"""Independent, read-only verification of Steward label ledger entries."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Protocol

from steward_runtime.labels import SIZE_LABELS, size_label


class ReadOnlyGitHubClient(Protocol):
    """The watchdog intentionally depends only on GitHub GET reads."""

    def get_json(self, path: str, params: Mapping[str, str] | None = None) -> object: ...


_REQUIRED_KEYS = frozenset({"action", "repository", "number", "label", "changed_lines", "url", "timestamp"})


def _finding(entry: Mapping[str, object], reason: str) -> dict[str, str]:
    repository = entry.get("repository")
    number = entry.get("number")
    reference = f"{repository}#{number}" if isinstance(repository, str) and isinstance(number, int) else "invalid ledger entry"
    return {"severity": "red", "reference": reference, "reason": reason}


def _valid_repository(repository: object) -> bool:
    return (
        isinstance(repository, str)
        and repository.count("/") == 1
        and all(component and component.strip() == component and " " not in component for component in repository.split("/"))
    )


def _valid_timestamp(timestamp: object) -> bool:
    if not isinstance(timestamp, str) or not timestamp:
        return False
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _has_usable_target(entry: Mapping[str, object]) -> bool:
    number = entry.get("number")
    return _valid_repository(entry.get("repository")) and isinstance(number, int) and not isinstance(number, bool) and int(number) > 0


def _valid_shape(entry: Mapping[str, object]) -> bool:
    repository = entry.get("repository")
    number = entry.get("number")
    changed_lines = entry.get("changed_lines")
    return (
        set(entry) == _REQUIRED_KEYS
        and entry.get("action") == "add_label"
        and _valid_repository(repository)
        and isinstance(number, int)
        and not isinstance(number, bool)
        and int(number) > 0
        and isinstance(entry.get("label"), str)
        and entry["label"] in SIZE_LABELS
        and isinstance(changed_lines, int)
        and not isinstance(changed_lines, bool)
        and changed_lines >= 0
        and entry.get("url") == f"https://github.com/{repository}/pull/{number}"
        and _valid_timestamp(entry.get("timestamp"))
    )


def _live_label_names(value: object) -> set[str] | None:
    if not isinstance(value, list):
        return None
    names: set[str] = set()
    for label in value:
        if not isinstance(label, Mapping) or not isinstance(label.get("name"), str):
            return None
        names.add(label["name"])
    return names


def verify_ledger_entry(
    entry: Mapping[str, object], client: ReadOnlyGitHubClient, onboarded: set[str]
) -> list[dict[str, str]]:
    """Re-fetch one ledgered PR and return only red discrepancies.

    The ledger selects a target; it is never trusted as a source of current
    GitHub state.  This function calls only ``get_json`` and makes no repair.
    """
    findings: list[dict[str, str]] = []
    if not _valid_shape(entry):
        findings.append(_finding(entry, "unsupported action shape"))

    repository = entry.get("repository")
    number = entry.get("number")
    label = entry.get("label")
    changed_lines = entry.get("changed_lines")
    if isinstance(label, str) and label not in SIZE_LABELS:
        findings.append(_finding(entry, f"off-allowlist label {label!r}"))
    if isinstance(repository, str) and repository not in onboarded:
        findings.append(_finding(entry, f"repository {repository!r} is not onboarded"))
    if isinstance(label, str) and label in SIZE_LABELS and isinstance(changed_lines, int) and not isinstance(changed_lines, bool) and size_label(changed_lines) != label:
        findings.append(_finding(entry, "source mismatch: recorded changed_lines does not map to recorded label"))

    if not _has_usable_target(entry):
        return findings
    assert isinstance(repository, str)
    assert isinstance(number, int)
    try:
        detail = client.get_json(f"repos/{repository}/pulls/{number}")
    except Exception as error:
        findings.append(_finding(entry, f"live read failed: {type(error).__name__}"))
        return findings
    if not isinstance(detail, Mapping):
        findings.append(_finding(entry, "source mismatch: live PR response is not an object"))
        return findings
    labels = _live_label_names(detail.get("labels"))
    additions = detail.get("additions")
    deletions = detail.get("deletions")
    if labels is None or not isinstance(additions, int) or isinstance(additions, bool) or not isinstance(deletions, int) or isinstance(deletions, bool):
        findings.append(_finding(entry, "source mismatch: live PR lacks structured labels or totals"))
        return findings
    if isinstance(label, str) and label not in labels:
        findings.append(_finding(entry, f"missing label {label!r} on live PR"))
    if isinstance(changed_lines, int) and not isinstance(changed_lines, bool) and additions + deletions != changed_lines:
        findings.append(_finding(entry, "source mismatch: live additions plus deletions differs from ledger"))
    return findings


def run_watchdog(ledger_path: Path, client: ReadOnlyGitHubClient, onboarded: set[str]) -> list[dict[str, str]]:
    """Verify every JSON-lines ledger record without mutating GitHub or state."""
    if not ledger_path.exists():
        return []
    findings: list[dict[str, str]] = []
    with ledger_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                findings.append({"severity": "red", "reference": f"ledger line {line_number}", "reason": "unsupported action shape: malformed JSON"})
                continue
            if not isinstance(entry, Mapping):
                findings.append({"severity": "red", "reference": f"ledger line {line_number}", "reason": "unsupported action shape"})
                continue
            findings.extend(verify_ledger_entry(entry, client, onboarded))
    return findings
