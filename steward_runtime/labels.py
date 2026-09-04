"""Deterministic, add-only Steward pull-request size labels."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, TypeGuard, cast

from steward_runtime.state import atomic_write_json

SIZE_LABELS = (
    "steward:size:S",
    "steward:size:M",
    "steward:size:L",
    "steward:size:XL",
)


def size_label(changed_lines: int) -> str:
    """Return the allowlisted label for a structured diff-size total."""
    if changed_lines <= 50:
        return "steward:size:S"
    if changed_lines <= 200:
        return "steward:size:M"
    if changed_lines <= 500:
        return "steward:size:L"
    return "steward:size:XL"


def _is_nonnegative_integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def desired_label_for_pr(pr: Mapping[str, object]) -> str | None:
    """Return an add-only desired label, or None when a size label exists."""
    labels = pr.get("labels")
    if isinstance(labels, list):
        for label in labels:
            if isinstance(label, Mapping) and label.get("name") in SIZE_LABELS:
                return None
    additions = pr.get("additions", 0)
    deletions = pr.get("deletions", 0)
    if not _is_nonnegative_integer(additions) or not _is_nonnegative_integer(deletions):
        return None
    return size_label(additions + deletions)


def _repository_label_names(repository: str, client: "LabelClient") -> set[str]:
    names: set[str] = set()
    page = 1
    while True:
        params = {"per_page": "100"}
        if page > 1:
            params["page"] = str(page)
        response = client.get_json(f"repos/{repository}/labels", params)
        if not isinstance(response, list):
            raise ValueError(f"label response for {repository} must be an array")
        names.update(_label_names(response))
        if len(response) < 100:
            return names
        page += 1


def bootstrap_repository_labels(repository: str, client: "LabelClient", apply: bool) -> list[str]:
    """Create only absent size labels when the caller has explicitly applied."""
    existing = _repository_label_names(repository, client)
    missing = [label for label in SIZE_LABELS if label not in existing]
    if apply:
        for label in missing:
            client.create_label(repository, label)
    return missing


class LabelClient(Protocol):
    def get_json(self, path: str, params: Mapping[str, str] | None = None) -> object: ...

    def create_label(self, repository: str, label: str) -> None: ...

    def add_label(self, repository: str, number: int, label: str) -> None: ...


def _label_names(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        name
        for label in value
        if isinstance(label, Mapping) and isinstance((name := label.get("name")), str)
    }


def _load_label_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"onboarded_repositories": [], "processed": {}, "pending": {}}
    with path.open(encoding="utf-8") as handle:
        state = json.load(handle)
    if not isinstance(state, dict):
        raise ValueError("label state must be a JSON object")
    onboarded = state.get("onboarded_repositories", [])
    processed = state.get("processed", {})
    pending = state.get("pending", {})
    if not isinstance(onboarded, list) or not all(isinstance(repo, str) for repo in onboarded):
        raise ValueError("label state onboarded_repositories must be a list of names")
    if not isinstance(processed, dict):
        raise ValueError("label state processed must be an object")
    if not isinstance(pending, dict) or not all(isinstance(key, str) and isinstance(entry, Mapping) for key, entry in pending.items()):
        raise ValueError("label state pending must be an object of action records")
    return {
        "onboarded_repositories": sorted(set(onboarded)),
        "processed": dict(processed),
        "pending": {key: dict(entry) for key, entry in pending.items()},
    }


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _all_open_pulls(repository: str, client: LabelClient) -> list[Mapping[str, object]]:
    pulls: list[Mapping[str, object]] = []
    page = 1
    while True:
        params = {"state": "open", "per_page": "100"}
        if page > 1:
            params["page"] = str(page)
        response = client.get_json(f"repos/{repository}/pulls", params)
        if not isinstance(response, list):
            raise ValueError(f"open pull response for {repository} must be an array")
        page_items = [pull for pull in response if isinstance(pull, Mapping)]
        pulls.extend(page_items)
        if len(response) < 100:
            return pulls
        page += 1


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append_ledger_entry(path: Path, entry: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _ledger_contains_entry(path: Path, entry: Mapping[str, object]) -> bool:
    if not path.exists():
        return False
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                recorded = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError("label ledger contains malformed JSON") from error
            if recorded == entry:
                return True
    return False


def _complete_pending_action(
    key: str,
    entry: Mapping[str, object],
    state_path: Path,
    state: dict[str, object],
    ledger_path: Path,
    pending: dict[str, object],
    processed: dict[str, object],
) -> dict[str, object]:
    """Durably ledger a confirmed external write before completing its cursor."""
    if not _ledger_contains_entry(ledger_path, entry):
        _append_ledger_entry(ledger_path, entry)
    processed[key] = entry["timestamp"]
    pending.pop(key)
    state["processed"] = processed
    state["pending"] = pending
    atomic_write_json(state_path, state)
    return dict(entry)


def _is_pending_action_for_target(entry: Mapping[str, object], repository: str, number: int) -> bool:
    label = entry.get("label")
    return (
        entry.get("action") == "add_label"
        and entry.get("repository") == repository
        and entry.get("number") == number
        and isinstance(label, str)
        and label in SIZE_LABELS
    )


def sync_labels(
    repositories: Sequence[str],
    client: LabelClient,
    ledger_path: Path,
    limit: int | None = None,
    apply: bool = True,
) -> list[dict[str, object]]:
    """Add at most one missing size label per eligible, onboarded open PR."""
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")
    state_path = ledger_path.with_name("label-state.json")
    lock_path = ledger_path.parent / "locks" / "label-sync.lock"
    actions: list[dict[str, object]] = []
    lock = _exclusive_lock(lock_path) if apply else nullcontext()
    with lock:
        state = _load_label_state(state_path)
        onboarded = set(cast(list[str], state["onboarded_repositories"]))
        processed = dict(cast(Mapping[str, object], state["processed"]))
        pending = dict(cast(Mapping[str, object], state["pending"]))
        for repository in sorted(set(repositories) & onboarded):
            for pull in sorted(_all_open_pulls(repository, client), key=lambda item: int(item.get("number", -1))):
                if limit is not None and len(actions) >= limit:
                    return actions
                number = pull.get("number")
                if not isinstance(number, int) or bool(pull.get("draft", False)):
                    continue
                key = f"{repository}#{number}"
                if key in processed:
                    continue
                detail = client.get_json(f"repos/{repository}/pulls/{number}")
                if not isinstance(detail, Mapping) or bool(detail.get("draft", pull.get("draft", False))):
                    continue
                additions = detail.get("additions")
                deletions = detail.get("deletions")
                if not _is_nonnegative_integer(additions) or not _is_nonnegative_integer(deletions):
                    continue
                pending_entry = pending.get(key)
                if isinstance(pending_entry, Mapping):
                    label = pending_entry.get("label")
                    labels = _label_names(detail.get("labels"))
                    if isinstance(label, str) and label in labels:
                        actions.append(
                            _complete_pending_action(
                                key,
                                dict(pending_entry),
                                state_path,
                                state,
                                ledger_path,
                                pending,
                                processed,
                            )
                        )
                        continue
                    if _is_pending_action_for_target(pending_entry, repository, number):
                        raise ValueError(
                            f"ambiguous pending label write for {key}: recorded label {label!r} is not visible; manual reconciliation required"
                        )
                desired = desired_label_for_pr(detail)
                if desired is None:
                    continue
                changed_lines = additions + deletions
                entry: dict[str, object] = {
                    "action": "add_label",
                    "repository": repository,
                    "number": number,
                    "label": desired,
                    "changed_lines": changed_lines,
                    "url": f"https://github.com/{repository}/pull/{number}",
                    "timestamp": _timestamp(),
                }
                if not apply:
                    actions.append(entry)
                    continue
                pending[key] = entry
                state["pending"] = pending
                atomic_write_json(state_path, state)
                client.add_label(repository, number, desired)
                actions.append(
                    _complete_pending_action(key, entry, state_path, state, ledger_path, pending, processed)
                )
    return actions
