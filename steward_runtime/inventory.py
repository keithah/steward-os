"""Selection rules for active, owned Steward repositories."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

ACTIVE_DAYS = 30


def parse_github_time(value: str) -> datetime:
    """Parse a GitHub ISO-8601 timestamp into an aware datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_active_repository(repository: Mapping[str, object], now: datetime) -> bool:
    """Return whether an eligible repository was pushed in the active window."""
    if repository.get("fork") or repository.get("archived") or repository.get("disabled"):
        return False
    pushed_at = repository.get("pushed_at")
    return isinstance(pushed_at, str) and parse_github_time(pushed_at) >= now - timedelta(days=ACTIVE_DAYS)


def select_active_repositories(
    repositories: Sequence[Mapping[str, object]],
    open_pr_repositories: set[str],
    now: datetime,
) -> list[str]:
    """Return sorted owned repositories active by push recency or open PR presence."""
    selected = []
    for repository in repositories:
        full_name = repository.get("full_name")
        if not isinstance(full_name, str):
            continue
        if repository.get("fork") or repository.get("archived") or repository.get("disabled"):
            continue
        if is_active_repository(repository, now) or full_name in open_pr_repositories:
            selected.append(full_name)
    return sorted(selected)
