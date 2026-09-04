import json
import os
import pathlib
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from steward_runtime.github import GitHubClient
from steward_runtime.inventory import is_active_repository, select_active_repositories
from steward_runtime.state import RuntimePaths, _fsync_parent_directory, atomic_write_json, label_state_lock_path


class RuntimeStateTests(unittest.TestCase):
    def test_runtime_paths_default_to_private_steward_directory(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"HOME": home}, clear=False):
                paths = RuntimePaths.from_environment()

        expected_root = pathlib.Path(home) / ".hermes" / "steward-os"
        self.assertEqual(paths.root, expected_root)
        self.assertEqual(paths.scoreboard_json, expected_root / "scoreboard.json")
        self.assertEqual(paths.scoreboard_markdown, expected_root / "scoreboard.md")
        self.assertEqual(paths.judgment_json, expected_root / "judgment.json")
        self.assertEqual(paths.label_ledger_jsonl, expected_root / "label-ledger.jsonl")
        self.assertEqual(paths.label_state_json, expected_root / "label-state.json")
        self.assertEqual(paths.watchdog_state_json, expected_root / "watchdog-state.json")
        self.assertEqual(paths.locks, expected_root / "locks")

    def test_label_state_lock_path_serializes_all_label_state_writers(self):
        root = pathlib.Path("/private/steward-runtime")

        self.assertEqual(
            label_state_lock_path(root / "label-state.json"),
            root / "locks" / "label-state.lock",
        )

    def test_atomic_write_json_syncs_parent_directory_after_replace(self):
        parent = pathlib.Path("/private/steward-runtime")
        with patch("steward_runtime.state.os.open", return_value=91) as open_directory, patch(
            "steward_runtime.state.os.close"
        ) as close_directory, patch("steward_runtime.state.os.fsync") as fsync:
            _fsync_parent_directory(parent)

        fsync.assert_called_once_with(91)
        open_directory.assert_called_once()
        self.assertEqual(open_directory.call_args.args[0], parent)
        close_directory.assert_called_once_with(91)

    def test_atomic_write_json_creates_parent_and_replaces_json(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "nested" / "state.json"
            atomic_write_json(target, {"repos": ["keithah/live"], "version": 1})

            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"repos": ["keithah/live"], "version": 1},
            )
            self.assertEqual(list(target.parent.glob("*.tmp")), [])


class GitHubClientTests(unittest.TestCase):
    def test_get_json_uses_gh_api_get_and_decodes_response(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return type("Result", (), {"stdout": '{"id": 7}'})()

        client = GitHubClient(runner=runner)

        self.assertEqual(
            client.get_json("repos/keithah/live", {"page": "2", "per_page": "100"}),
            {"id": 7},
        )
        self.assertEqual(
            calls,
            [
                (
                    [
                        "gh",
                        "api",
                        "--method",
                        "GET",
                        "repos/keithah/live",
                        "-f",
                        "page=2",
                        "-f",
                        "per_page=100",
                    ],
                    {"check": True, "capture_output": True, "text": True},
                )
            ],
        )

    def test_label_writes_are_limited_to_the_exact_steward_size_allowlist(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return type("Result", (), {"stdout": "{}"})()

        client = GitHubClient(runner=runner)

        client.create_label("keithah/live", "steward:size:S")
        client.add_label("keithah/live", 7, "steward:size:XL")
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            client.add_label("keithah/live", 7, "bug")

        self.assertEqual(
            calls,
            [
                (["gh", "api", "--method", "POST", "repos/keithah/live/labels", "-f", "name=steward:size:S"], {"check": True, "capture_output": True, "text": True}),
                (["gh", "api", "--method", "POST", "repos/keithah/live/issues/7/labels", "-f", "labels[]=steward:size:XL"], {"check": True, "capture_output": True, "text": True}),
            ],
        )


class SelectActiveRepositoriesTests(unittest.TestCase):
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)

    def test_selects_recent_owned_nonfork_repository(self):
        repositories = [{"full_name": "keithah/live", "fork": False, "archived": False,
                         "disabled": False, "pushed_at": "2026-08-20T00:00:00Z"}]

        self.assertEqual(select_active_repositories(repositories, set(), self.now), ["keithah/live"])

    def test_selects_old_repository_only_when_it_has_an_open_pr(self):
        repositories = [{"full_name": "keithah/old", "fork": False, "archived": False,
                         "disabled": False, "pushed_at": "2026-01-01T00:00:00Z"}]

        self.assertEqual(
            select_active_repositories(repositories, {"keithah/old"}, self.now),
            ["keithah/old"],
        )

    def test_excludes_forks_archived_and_disabled_repositories_even_with_open_prs(self):
        repositories = [
            {"full_name": "keithah/fork", "fork": True, "archived": False, "disabled": False, "pushed_at": "2026-09-03T00:00:00Z"},
            {"full_name": "keithah/archive", "fork": False, "archived": True, "disabled": False, "pushed_at": "2026-09-03T00:00:00Z"},
            {"full_name": "keithah/disabled", "fork": False, "archived": False, "disabled": True, "pushed_at": "2026-09-03T00:00:00Z"},
        ]

        self.assertEqual(
            select_active_repositories(repositories, {repo["full_name"] for repo in repositories}, self.now),
            [],
        )

    def test_treats_exactly_thirty_days_old_push_as_active(self):
        repository = {"full_name": "keithah/boundary", "fork": False, "archived": False,
                      "disabled": False, "pushed_at": "2026-08-05T00:00:00Z"}

        self.assertTrue(is_active_repository(repository, self.now))

    def test_sorts_selected_repository_names(self):
        repositories = [
            {"full_name": "keithah/z", "fork": False, "archived": False, "disabled": False, "pushed_at": "2026-09-03T00:00:00Z"},
            {"full_name": "keithah/a", "fork": False, "archived": False, "disabled": False, "pushed_at": "2026-09-03T00:00:00Z"},
        ]

        self.assertEqual(select_active_repositories(repositories, set(), self.now), ["keithah/a", "keithah/z"])


if __name__ == "__main__":
    unittest.main()
