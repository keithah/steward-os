#!/usr/bin/env python3
"""Read-only verification of the Steward PR label ledger."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from steward_runtime.github import GitHubClient
from steward_runtime.labels import _load_label_state
from steward_runtime.state import RuntimePaths
from steward_runtime.watchdog import run_watchdog


class FixtureReadOnlyClient:
    """Offline fixture client with no GitHub write methods."""

    def __init__(self, responses: Mapping[str, object]) -> None:
        self._responses = responses

    def get_json(self, path: str, params: Mapping[str, str] | None = None) -> object:
        del params
        try:
            return self._responses[path]
        except KeyError as error:
            raise ValueError(f"fixture has no response for {path}") from error


def load_fixture(path: Path) -> tuple[list[object], set[str], FixtureReadOnlyClient]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("watchdog fixture must be a JSON object")
    entries = payload.get("ledger")
    onboarded = payload.get("onboarded_repositories")
    responses = payload.get("responses")
    if not isinstance(entries, list):
        raise ValueError("watchdog fixture ledger must be a list")
    if not isinstance(onboarded, list) or not all(isinstance(repository, str) for repository in onboarded):
        raise ValueError("watchdog fixture onboarded_repositories must be a list of names")
    if not isinstance(responses, Mapping):
        raise ValueError("watchdog fixture responses must be an object")
    return entries, set(onboarded), FixtureReadOnlyClient(responses)


def print_findings(findings: list[dict[str, str]]) -> None:
    for finding in findings:
        print(f"RED Steward label watchdog: {finding['reference']}: {finding['reason']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, help="offline watchdog fixture; never contacts GitHub")
    arguments = parser.parse_args(argv)
    try:
        if arguments.fixture:
            entries, onboarded, client = load_fixture(arguments.fixture)
            with tempfile.TemporaryDirectory() as directory:
                ledger_path = Path(directory) / "label-ledger.jsonl"
                ledger_path.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
                findings = run_watchdog(ledger_path, client, onboarded)
        else:
            paths = RuntimePaths.from_environment()
            state = _load_label_state(paths.label_state_json)
            onboarded = set(cast(list[str], state["onboarded_repositories"]))
            findings = run_watchdog(paths.label_ledger_jsonl, GitHubClient(), onboarded)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"RED Steward label watchdog: failed closed: {error}")
        return 1
    print_findings(findings)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
