"""Read-only GitHub API access through the GitHub CLI."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from typing import Any


class GitHubClient:
    """GitHub API client with GET reads and two allowlisted label writes."""

    _SIZE_LABELS = frozenset({"steward:size:S", "steward:size:M", "steward:size:L", "steward:size:XL"})

    def __init__(self, runner: Callable[..., Any] = subprocess.run) -> None:
        self._runner = runner

    def get_json(self, path: str, params: Mapping[str, str] | None = None) -> object:
        command = ["gh", "api", "--method", "GET", path]
        for key, value in (params or {}).items():
            command.extend(["-f", f"{key}={value}"])
        result = self._runner(command, check=True, capture_output=True, text=True)
        return json.loads(result.stdout)

    def _require_size_label(self, label: str) -> None:
        if label not in self._SIZE_LABELS:
            raise ValueError("only exact allowlisted steward size labels may be written")

    def create_label(self, repository: str, label: str) -> None:
        """Create one exact allowed repository label."""
        self._require_size_label(label)
        self._runner(
            ["gh", "api", "--method", "POST", f"repos/{repository}/labels", "-f", f"name={label}"],
            check=True,
            capture_output=True,
            text=True,
        )

    def add_label(self, repository: str, number: int, label: str) -> None:
        """Add one exact allowed label to a pull request without replacement."""
        self._require_size_label(label)
        self._runner(
            ["gh", "api", "--method", "POST", f"repos/{repository}/issues/{number}/labels", "-f", f"labels[]={label}"],
            check=True,
            capture_output=True,
            text=True,
        )
