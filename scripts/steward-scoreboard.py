#!/usr/bin/env python3
"""Collect a private, read-only Steward scoreboard."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from steward_runtime.github import GitHubClient
from steward_runtime.inventory import select_active_repositories
from steward_runtime.scoreboard import build_scoreboard, daily_digest, material_digest, render_scoreboard
from steward_runtime.state import RuntimePaths, atomic_write_json


class FixtureGitHubClient(GitHubClient):
    """Read fixture responses without allowing any network access."""

    def __init__(self, responses: Mapping[str, object]) -> None:
        self._responses = responses

    def get_json(self, path: str, params: Mapping[str, str] | None = None) -> object:
        del params
        try:
            return self._responses[path]
        except KeyError as error:
            raise ValueError(f"fixture has no response for {path}") from error


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    ) as temporary:
        temporary.write(value)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def load_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def active_repositories(client: GitHubClient, now: datetime) -> list[str]:
    repositories: list[Mapping[str, object]] = []
    page = 1
    while True:
        params = {"affiliation": "owner", "per_page": "100", "sort": "updated"}
        if page > 1:
            params["page"] = str(page)
        response = client.get_json("user/repos", params)
        if not isinstance(response, list):
            raise ValueError("GitHub user/repos response must be an array")
        repositories.extend(cast(list[Mapping[str, object]], response))
        if len(response) < 100:
            break
        page += 1
    open_pr_repositories: set[str] = set()
    for repository in repositories:
        full_name = repository.get("full_name")
        if not isinstance(full_name, str):
            continue
        pulls = client.get_json(f"repos/{full_name}/pulls", {"state": "open", "per_page": "1"})
        if isinstance(pulls, list) and pulls:
            open_pr_repositories.add(full_name)
    return select_active_repositories(repositories, open_pr_repositories, now)


def load_fixture(path: Path) -> tuple[list[str], Mapping[str, object], FixtureGitHubClient]:
    payload = load_json(path, None)
    if not isinstance(payload, Mapping):
        raise ValueError("fixture must be a JSON object")
    repositories = payload.get("repositories")
    responses = payload.get("responses")
    judgments = payload.get("judgments", {})
    if not isinstance(repositories, list) or not all(isinstance(repo, str) for repo in repositories):
        raise ValueError("fixture repositories must be an array of names")
    if not isinstance(responses, Mapping) or not isinstance(judgments, Mapping):
        raise ValueError("fixture responses and judgments must be objects")
    return repositories, judgments, FixtureGitHubClient(responses)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, help="read only this local JSON fixture; never calls GitHub")
    parser.add_argument("--now", help="UTC ISO-8601 timestamp for deterministic fixture runs")
    parser.add_argument("--daily", action="store_true", help="print a concise daily top-queue digest")
    arguments = parser.parse_args(argv)

    try:
        now = parse_time(arguments.now) if arguments.now else datetime.now(timezone.utc)
        paths = RuntimePaths.from_environment()
        previous = load_json(paths.scoreboard_json, None)
        if previous is not None and not isinstance(previous, Mapping):
            raise ValueError("existing scoreboard must be a JSON object")
        if arguments.fixture:
            repositories, judgments, client = load_fixture(arguments.fixture)
        else:
            client = GitHubClient()
            repositories = active_repositories(client, now)
            judgments = load_json(paths.judgment_json, {})
            if not isinstance(judgments, Mapping):
                raise ValueError("judgment.json must be a JSON object keyed by repo#number")
        snapshot = build_scoreboard(repositories, client, judgments, now)
        markdown = render_scoreboard(snapshot)
        digest = material_digest(previous, snapshot)
        atomic_write_json(paths.scoreboard_json, snapshot)
        atomic_write_text(paths.scoreboard_markdown, markdown)
        if arguments.daily:
            print(daily_digest(snapshot))
        elif digest:
            print(digest)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"steward scoreboard failed closed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
