"""Read-only collection, ranking, rendering, and digesting for Steward queues."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from steward_runtime.github import GitHubClient

_TOP_LIMIT = 10
_SEVERITY_RANK = {"critical": 0, "high": 1, "normal": 2, "low": 3}
_CI_RANK = {"failure": 0, "pending": 1, "unknown": 2, "success": 3}


def _objects(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _all_open_items(client: GitHubClient, path: str) -> list[Mapping[str, object]]:
    """Collect every open item from a paginated GitHub list endpoint."""
    items: list[Mapping[str, object]] = []
    page = 1
    while True:
        params = {"state": "open", "per_page": "100"}
        if page > 1:
            params["page"] = str(page)
        payload = client.get_json(path, params)
        if not isinstance(payload, list):
            return items
        items.extend(_objects(payload))
        if len(payload) < 100:
            return items
        page += 1


def _labels(value: object) -> list[str]:
    return sorted(
        str(label["name"])
        for label in _objects(value)
        if isinstance(label.get("name"), str)
    )


def _review_status(reviews: object) -> str:
    states = {str(review.get("state", "")).upper() for review in _objects(reviews)}
    if "CHANGES_REQUESTED" in states:
        return "changes_requested"
    if "APPROVED" in states:
        return "approved"
    if states:
        return "pending"
    return "unknown"


def _check_status(payload: object) -> str:
    checks = _objects(payload.get("check_runs")) if isinstance(payload, Mapping) else []
    if not checks:
        return "unknown"
    conclusions = {str(check.get("conclusion", "")).lower() for check in checks}
    states = {str(check.get("status", "")).lower() for check in checks}
    if conclusions & {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}:
        return "failure"
    if states - {"completed"}:
        return "pending"
    return "success"


def _mergeable_status(value: object) -> str:
    if value in {"dirty", "behind"}:
        return "conflict"
    if value == "clean":
        return "clean"
    return "unknown"


def _item_severity(judgment: object) -> str:
    if isinstance(judgment, Mapping) and str(judgment.get("severity", "")) in _SEVERITY_RANK:
        return str(judgment["severity"])
    return "normal"


def _now_string(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def rank_items(items: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Return a deterministic priority ordering of scoreboard items."""
    return sorted(
        (dict(item) for item in items),
        key=lambda item: (
            _SEVERITY_RANK.get(str(item.get("severity", "normal")), _SEVERITY_RANK["normal"]),
            _CI_RANK.get(str(item.get("ci", "unknown")), _CI_RANK["unknown"]),
            str(item.get("key", "")),
        ),
    )


def build_scoreboard(
    repositories: Sequence[str], client: GitHubClient, judgments: Mapping[str, object], now: datetime
) -> dict[str, object]:
    """Collect documented GitHub signals and merge immutable human judgments."""
    items: list[dict[str, object]] = []
    for repository in sorted(repositories):
        pulls = _all_open_items(client, f"repos/{repository}/pulls")
        for pull in _objects(pulls):
            number = pull.get("number")
            if not isinstance(number, int):
                continue
            detail = client.get_json(f"repos/{repository}/pulls/{number}")
            if not isinstance(detail, Mapping):
                continue
            head = pull.get("head")
            sha = head.get("sha") if isinstance(head, Mapping) else None
            reviews = client.get_json(f"repos/{repository}/pulls/{number}/reviews")
            checks: object = None
            if isinstance(sha, str) and sha:
                checks = client.get_json(f"repos/{repository}/commits/{sha}/check-runs")
            key = f"{repository}#{number}"
            judgment = judgments.get(key)
            item: dict[str, object] = {
                "key": key,
                "kind": "pr",
                "severity": _item_severity(judgment),
                "draft": bool(detail.get("draft", pull.get("draft", False))),
                "ci": _check_status(checks),
                "mergeable": _mergeable_status(detail.get("mergeable_state")),
                "review": _review_status(reviews),
                "additions": detail.get("additions", 0),
                "deletions": detail.get("deletions", 0),
                "labels": _labels(detail.get("labels")),
                "author": (detail.get("user") or {}).get("login", "") if isinstance(detail.get("user"), Mapping) else "",
                "created_at": detail.get("created_at", pull.get("created_at", "")),
                "updated_at": detail.get("updated_at", pull.get("updated_at", "")),
                "evidence": {"checks": checks is not None, "mergeable": detail.get("mergeable_state") is not None, "reviews": reviews is not None},
            }
            if isinstance(judgment, Mapping):
                item["judgment"] = dict(judgment)
            items.append(item)

        issues = _all_open_items(client, f"repos/{repository}/issues")
        for issue in _objects(issues):
            number = issue.get("number")
            if not isinstance(number, int) or "pull_request" in issue:
                continue
            key = f"{repository}#{number}"
            judgment = judgments.get(key)
            item = {
                "key": key,
                "kind": "issue",
                "severity": _item_severity(judgment),
                "ci": "unknown",
                "mergeable": "unknown",
                "review": "unknown",
                "labels": _labels(issue.get("labels")),
                "created_at": issue.get("created_at", ""),
                "updated_at": issue.get("updated_at", ""),
                "evidence": {"checks": False, "mergeable": False, "reviews": False},
            }
            if isinstance(judgment, Mapping):
                item["judgment"] = dict(judgment)
            items.append(item)

    ranked = rank_items(items)
    return {
        "generated_at": _now_string(now),
        "repositories": sorted(set(repositories)),
        "top_keys": [item["key"] for item in ranked[:_TOP_LIMIT]],
        "items": ranked,
    }


def render_scoreboard(snapshot: Mapping[str, object]) -> str:
    """Render a Markdown queue with an explicit evidence-presence disclaimer."""
    rows = ["# Steward scoreboard", "", "## Review first", "", "| Key | Kind | Severity | CI | Mergeable | Review | Evidence |", "| --- | --- | --- | --- | --- | --- | --- |"]
    items = snapshot.get("items", [])
    for item in _objects(items):
        evidence = item.get("evidence")
        present = ", ".join(sorted(key for key, value in evidence.items() if value)) if isinstance(evidence, Mapping) else "none"
        rows.append("| {key} | {kind} | {severity} | {ci} | {mergeable} | {review} | {evidence} |".format(
            key=item.get("key", ""), kind=item.get("kind", ""), severity=item.get("severity", ""), ci=item.get("ci", ""), mergeable=item.get("mergeable", ""), review=item.get("review", ""), evidence=present
        ))
    if len(rows) == 7:
        rows.append("| _No open items_ |  |  |  |  |  |  |")
    rows.extend(["", "## Evidence legend", "", "Evidence presence is not a merge verdict. It only records whether checks, mergeability, or reviews were available when this read-only snapshot was collected.", ""])
    return "\n".join(rows)


def _material_states(snapshot: Mapping[str, object]) -> dict[str, tuple[object, object]]:
    result: dict[str, tuple[object, object]] = {}
    for item in _objects(snapshot.get("items", [])):
        key = item.get("key")
        if isinstance(key, str):
            result[key] = (item.get("ci"), item.get("mergeable"))
    return result


def daily_digest(snapshot: Mapping[str, object]) -> str:
    """Render a bounded Matrix-ready summary of the highest-priority queue items."""
    top_keys = snapshot.get("top_keys", [])
    if not isinstance(top_keys, Sequence) or isinstance(top_keys, (str, bytes)):
        top_keys = []
    summary = ", ".join(str(key) for key in top_keys[:5]) or "none"
    remaining = max(0, len(top_keys) - 5)
    suffix = f" (+{remaining} more)" if remaining else ""
    return f"Steward daily digest\nTop queue: {summary}{suffix}"


def material_digest(previous: Mapping[str, object] | None, current: Mapping[str, object]) -> str:
    """Describe material queue, CI, or conflict changes; return empty when unchanged."""
    current_top = current.get("top_keys", [])
    if not isinstance(current_top, Sequence) or isinstance(current_top, (str, bytes)):
        current_top = []
    if previous is None or previous.get("top_keys") != current_top:
        return "Steward queue changed: {}".format(", ".join(str(key) for key in current_top))
    prior_states = _material_states(previous)
    current_states = _material_states(current)
    changed = sorted(key for key in current_top if prior_states.get(str(key)) != current_states.get(str(key)))
    if changed:
        return "Steward CI/conflict changed: {}".format(", ".join(str(key) for key in changed))
    return ""
