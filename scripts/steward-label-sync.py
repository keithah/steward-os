#!/usr/bin/env python3
"""Synchronize deterministic add-only Steward PR size labels."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from steward_runtime.github import GitHubClient
from steward_runtime.labels import _load_label_state, sync_labels
from steward_runtime.state import RuntimePaths


class FixtureGitHubClient:
    """Fixture-only GitHub client that fails if a write is attempted."""

    def __init__(self, responses: Mapping[str, object]) -> None:
        self._responses = responses

    def get_json(self, path: str, params: Mapping[str, str] | None = None) -> object:
        del params
        try:
            return self._responses[path]
        except KeyError as error:
            raise ValueError(f"fixture has no response for {path}") from error

    def create_label(self, repository: str, label: str) -> None:
        raise AssertionError(f"fixture must not create {repository} label {label}")

    def add_label(self, repository: str, number: int, label: str) -> None:
        raise AssertionError(f"fixture must not add {repository}#{number} label {label}")


def load_fixture(path: Path) -> tuple[list[str], list[str], FixtureGitHubClient]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("fixture must be a JSON object")
    repositories = payload.get("repositories")
    onboarded = payload.get("onboarded_repositories")
    responses = payload.get("responses")
    if not isinstance(repositories, list) or not all(isinstance(repo, str) for repo in repositories):
        raise ValueError("fixture repositories must be a list of names")
    if not isinstance(onboarded, list) or not all(isinstance(repo, str) for repo in onboarded):
        raise ValueError("fixture onboarded_repositories must be a list of names")
    if not isinstance(responses, Mapping):
        raise ValueError("fixture responses must be an object")
    return repositories, onboarded, FixtureGitHubClient(responses)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, help="use local responses only; requires --dry-run")
    parser.add_argument("--dry-run", action="store_true", help="propose actions without any GitHub or private-state write")
    parser.add_argument("--limit", type=int, help="maximum proposed or applied labels")
    arguments = parser.parse_args(argv)
    if arguments.fixture and not arguments.dry_run:
        parser.error("--fixture requires --dry-run to guarantee no GitHub write")
    try:
        if arguments.fixture:
            repositories, onboarded, client = load_fixture(arguments.fixture)
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "label-state.json").write_text(
                    json.dumps({"onboarded_repositories": onboarded, "processed": {}}), encoding="utf-8"
                )
                actions = sync_labels(repositories, client, root / "label-ledger.jsonl", arguments.limit, apply=False)
        else:
            paths = RuntimePaths.from_environment()
            state = _load_label_state(paths.label_state_json)
            repositories = state["onboarded_repositories"]
            actions = sync_labels(
                repositories,
                GitHubClient(),
                paths.label_ledger_jsonl,
                arguments.limit,
                apply=not arguments.dry_run,
            )
        for action in actions:
            print(json.dumps(action, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"steward label sync failed closed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
