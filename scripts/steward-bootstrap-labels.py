#!/usr/bin/env python3
"""Explicitly bootstrap the four Steward size labels for selected repositories."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from steward_runtime.github import GitHubClient
from steward_runtime.labels import SIZE_LABELS, _exclusive_lock, _load_label_state, _repository_label_names, bootstrap_repository_labels
from steward_runtime.state import RuntimePaths, atomic_write_json, label_state_lock_path


def frozen_scoreboard_repositories(path: Path) -> list[str]:
    with path.open(encoding="utf-8") as handle:
        snapshot = json.load(handle)
    if not isinstance(snapshot, Mapping):
        raise ValueError("stored scoreboard must be a JSON object")
    repositories = snapshot.get("repositories")
    if not isinstance(repositories, list) or not repositories or not all(isinstance(repo, str) for repo in repositories):
        raise ValueError("stored scoreboard has no frozen repository selection")
    return sorted(set(repositories))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repositories", nargs="*", help="explicit OWNER/REPOSITORY names")
    parser.add_argument("--from-current-scoreboard", action="store_true", help="use only the stored frozen scoreboard selection")
    parser.add_argument("--apply", action="store_true", help="allow the exact label-creation writes")
    arguments = parser.parse_args(argv)
    if bool(arguments.repositories) == arguments.from_current_scoreboard:
        parser.error("supply explicit repositories or --from-current-scoreboard, but not both")
    if not arguments.apply:
        parser.error("--apply is required; this command creates public labels")
    try:
        paths = RuntimePaths.from_environment()
        repositories = (
            frozen_scoreboard_repositories(paths.scoreboard_json)
            if arguments.from_current_scoreboard
            else sorted(set(arguments.repositories))
        )
        if not repositories:
            raise ValueError("repository selection must not be empty")
        client = GitHubClient()
        with _exclusive_lock(label_state_lock_path(paths.label_state_json)):
            state = _load_label_state(paths.label_state_json)
            onboarded = set(state["onboarded_repositories"])
            for repository in repositories:
                created = bootstrap_repository_labels(repository, client, apply=True)
                labels = _repository_label_names(repository, client)
                if not set(SIZE_LABELS).issubset(labels):
                    raise ValueError(f"bootstrap verification failed for {repository}")
                onboarded.add(repository)
                print(json.dumps({"repository": repository, "created": created}, sort_keys=True, separators=(",", ":")))
            state["onboarded_repositories"] = sorted(onboarded)
            atomic_write_json(paths.label_state_json, state)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"steward label bootstrap failed closed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
