import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

from steward_runtime.scoreboard import _review_status, build_scoreboard, material_digest, rank_items, render_scoreboard


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "steward-scoreboard.py"
NOW = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)


def load_scoreboard_module():
    spec = importlib.util.spec_from_file_location("steward_scoreboard", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("scoreboard script must be importable for unit tests")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGitHubClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get_json(self, path, params=None):
        self.calls.append((path, params))
        page = (params or {}).get("page")
        return self.responses.get((path, page), self.responses[path])


class ScoreboardRankingTests(unittest.TestCase):
    def test_critical_item_ranks_before_failing_ci_item(self):
        items = rank_items(
            [
                {"key": "keithah/a#1", "kind": "pr", "ci": "failure", "severity": "normal"},
                {"key": "keithah/b#2", "kind": "issue", "ci": "unknown", "severity": "critical"},
            ]
        )

        self.assertEqual([item["key"] for item in items], ["keithah/b#2", "keithah/a#1"])

    def test_unchanged_top_queue_has_no_digest(self):
        snapshot = {"top_keys": ["keithah/a#1"], "items": []}

        self.assertEqual(material_digest(snapshot, snapshot), "")

    def test_latest_review_from_each_reviewer_supersedes_stale_change_request(self):
        reviews = [
            {
                "state": "CHANGES_REQUESTED",
                "submitted_at": "2026-09-01T12:00:00Z",
                "user": {"login": "reviewer"},
            },
            {
                "state": "APPROVED",
                "submitted_at": "2026-09-02T12:00:00Z",
                "user": {"login": "reviewer"},
            },
        ]

        self.assertEqual(_review_status(reviews), "approved")

    def test_dismissed_newer_review_preserves_reviewer_prior_active_state(self):
        reviews = [
            {
                "state": "CHANGES_REQUESTED",
                "submitted_at": "2026-09-01T12:00:00Z",
                "user": {"login": "reviewer"},
            },
            {
                "state": "APPROVED",
                "submitted_at": "2026-09-02T12:00:00Z",
                "user": {"login": "reviewer"},
            },
            {
                "state": "DISMISSED",
                "submitted_at": "2026-09-03T12:00:00Z",
                "user": {"login": "reviewer"},
            },
        ]

        self.assertEqual(_review_status(reviews), "changes_requested")


class ScoreboardCollectionTests(unittest.TestCase):
    def test_active_repositories_discovers_an_old_open_pr_repository_on_page_two(self):
        first_page = [
            {
                "full_name": f"keithah/repository-{number}",
                "pushed_at": "2020-01-01T00:00:00Z",
            }
            for number in range(100)
        ]
        old_open_pr_repository = {
            "full_name": "keithah/old-open-pr",
            "pushed_at": "2020-01-01T00:00:00Z",
        }
        client = FakeGitHubClient(
            {
                "user/repos": first_page,
                ("user/repos", "2"): [old_open_pr_repository],
                **{f"repos/{repository['full_name']}/pulls": [] for repository in first_page},
                "repos/keithah/old-open-pr/pulls": [{"number": 1}],
            }
        )

        repositories = load_scoreboard_module().active_repositories(client, NOW)

        self.assertEqual(repositories, ["keithah/old-open-pr"])
        self.assertIn(
            ("user/repos", {"affiliation": "owner", "per_page": "100", "sort": "updated", "page": "2"}),
            client.calls,
        )

    def test_collects_only_open_pr_and_issue_signals_and_merges_judgment_by_immutable_key(self):
        client = FakeGitHubClient(
            {
                "repos/keithah/live/pulls": [
                    {
                        "number": 4,
                        "draft": False,
                        "head": {"sha": "head-sha"},
                        "created_at": "2026-09-01T00:00:00Z",
                        "updated_at": "2026-09-03T10:00:00Z",
                    }
                ],
                "repos/keithah/live/pulls/4": {
                    "number": 4,
                    "draft": False,
                    "mergeable_state": "dirty",
                    "additions": 12,
                    "deletions": 3,
                    "labels": [{"name": "bug"}],
                    "user": {"login": "author"},
                    "created_at": "2026-09-01T00:00:00Z",
                    "updated_at": "2026-09-03T10:00:00Z",
                },
                "repos/keithah/live/pulls/4/reviews": [{"state": "APPROVED"}],
                "repos/keithah/live/commits/head-sha/check-runs": {
                    "check_runs": [{"status": "completed", "conclusion": "failure"}]
                },
                "repos/keithah/live/issues": [
                    {
                        "number": 5,
                        "created_at": "2026-09-02T00:00:00Z",
                        "updated_at": "2026-09-03T09:00:00Z",
                        "labels": [{"name": "incident"}],
                    },
                    {"number": 4, "pull_request": {}, "labels": []},
                ],
            }
        )

        snapshot = build_scoreboard(
            ["keithah/live"],
            client,
            {"keithah/live#5": {"severity": "critical", "note": "human judgment"}},
            NOW,
        )

        self.assertEqual([item["key"] for item in snapshot["items"]], ["keithah/live#5", "keithah/live#4"])
        pr = snapshot["items"][1]
        self.assertEqual(pr["ci"], "failure")
        self.assertEqual(pr["mergeable"], "conflict")
        self.assertEqual(pr["review"], "approved")
        self.assertEqual(pr["labels"], ["bug"])
        self.assertEqual(pr["author"], "author")
        self.assertEqual(pr["additions"], 12)
        self.assertEqual(pr["deletions"], 3)
        self.assertEqual(snapshot["top_keys"], ["keithah/live#5", "keithah/live#4"])
        self.assertEqual(snapshot["repositories"], ["keithah/live"])
        self.assertEqual(snapshot["items"][0]["judgment"], {"severity": "critical", "note": "human judgment"})
        self.assertEqual(client.calls[0], ("repos/keithah/live/pulls", {"state": "open", "per_page": "100"}))

    def test_uses_later_review_and_check_pages_for_status_and_priority(self):
        repository = "keithah/paginated-signals"
        first_reviews = [{"state": "COMMENTED"} for _ in range(100)]
        first_checks = [{"status": "completed", "conclusion": "success"} for _ in range(100)]
        client = FakeGitHubClient(
            {
                f"repos/{repository}/pulls": [
                    {"number": 1, "head": {"sha": "first"}},
                    {"number": 2, "head": {"sha": "second"}},
                ],
                f"repos/{repository}/pulls/1": {"number": 1, "mergeable_state": "clean", "labels": []},
                f"repos/{repository}/pulls/2": {"number": 2, "mergeable_state": "clean", "labels": []},
                f"repos/{repository}/pulls/1/reviews": [],
                f"repos/{repository}/pulls/2/reviews": first_reviews,
                (f"repos/{repository}/pulls/2/reviews", "2"): [{"state": "CHANGES_REQUESTED"}],
                f"repos/{repository}/commits/first/check-runs": {"check_runs": [{"status": "completed", "conclusion": "success"}]},
                f"repos/{repository}/commits/second/check-runs": {"check_runs": first_checks},
                (f"repos/{repository}/commits/second/check-runs", "2"): {"check_runs": [{"status": "completed", "conclusion": "failure"}]},
                f"repos/{repository}/issues": [],
            }
        )

        snapshot = build_scoreboard([repository], client, {}, NOW)

        self.assertEqual([item["key"] for item in snapshot["items"]], [f"{repository}#2", f"{repository}#1"])
        self.assertEqual(snapshot["items"][0]["ci"], "failure")
        self.assertEqual(snapshot["items"][0]["review"], "changes_requested")
        self.assertIn((f"repos/{repository}/pulls/2/reviews", {"per_page": "100", "page": "2"}), client.calls)
        self.assertIn((f"repos/{repository}/commits/second/check-runs", {"per_page": "100", "page": "2"}), client.calls)

    def test_collects_and_ranks_open_issues_from_page_two(self):
        issue_path = "repos/keithah/paginated/issues"
        first_page = [
            {
                "number": number,
                "created_at": "2026-09-01T00:00:00Z",
                "updated_at": "2026-09-03T09:00:00Z",
                "labels": [],
            }
            for number in range(1, 101)
        ]
        client = FakeGitHubClient(
            {
                "repos/keithah/paginated/pulls": [],
                issue_path: first_page,
                (issue_path, "2"): [
                    {
                        "number": 101,
                        "created_at": "2026-09-01T00:00:00Z",
                        "updated_at": "2026-09-03T10:00:00Z",
                        "labels": [],
                    }
                ],
            }
        )

        snapshot = build_scoreboard(
            ["keithah/paginated"],
            client,
            {"keithah/paginated#101": {"severity": "critical"}},
            NOW,
        )

        self.assertEqual(len(snapshot["items"]), 101)
        self.assertEqual(snapshot["top_keys"][0], "keithah/paginated#101")
        self.assertIn(
            (issue_path, {"state": "open", "per_page": "100", "page": "2"}),
            client.calls,
        )

    def test_render_has_review_first_table_and_evidence_presence_legend(self):
        rendered = render_scoreboard(
            {
                "generated_at": "2026-09-03T12:00:00Z",
                "top_keys": ["keithah/live#4"],
                "items": [
                    {
                        "key": "keithah/live#4",
                        "kind": "pr",
                        "severity": "normal",
                        "ci": "failure",
                        "mergeable": "conflict",
                        "review": "approved",
                        "evidence": {"checks": True, "mergeable": True, "reviews": True},
                    }
                ],
            }
        )

        self.assertIn("## Review first", rendered)
        self.assertIn("| Key | Kind | Severity | CI | Mergeable | Review | Evidence |", rendered)
        self.assertIn("keithah/live#4", rendered)
        self.assertIn("Evidence presence is not a merge verdict", rendered)

    def test_ci_or_conflict_change_emits_digest_even_when_top_queue_is_unchanged(self):
        previous = {"top_keys": ["keithah/live#4"], "items": [{"key": "keithah/live#4", "ci": "success", "mergeable": "clean"}]}
        current = {"top_keys": ["keithah/live#4"], "items": [{"key": "keithah/live#4", "ci": "failure", "mergeable": "clean"}]}

        self.assertIn("CI", material_digest(previous, current))


class ScoreboardScriptTests(unittest.TestCase):
    def test_parse_time_normalizes_a_naive_now_to_utc(self):
        parsed = load_scoreboard_module().parse_time("2026-09-03T12:00:00")

        self.assertEqual(parsed, datetime(2026, 9, 3, 12, tzinfo=timezone.utc))

    def test_fixture_run_writes_private_artifacts_then_is_silent_when_unchanged(self):
        fixture = {
            "repositories": ["keithah/live"],
            "judgments": {"keithah/live#5": {"severity": "critical"}},
            "responses": {
                "repos/keithah/live/pulls": [],
                "repos/keithah/live/issues": [
                    {"number": 5, "created_at": "2026-09-02T00:00:00Z", "updated_at": "2026-09-03T09:00:00Z", "labels": []}
                ],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            fixture_path = pathlib.Path(directory) / "scoreboard.json"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            environment = {**os.environ, "HOME": directory}
            command = [sys.executable, str(SCRIPT), "--fixture", str(fixture_path), "--now", "2026-09-03T12:00:00Z"]

            first = subprocess.run(command, capture_output=True, text=True, check=True, env=environment)
            second = subprocess.run(command, capture_output=True, text=True, check=True, env=environment)

            root = pathlib.Path(directory) / ".hermes" / "steward-os"
            self.assertIn("Steward queue changed", first.stdout)
            self.assertEqual(second.stdout, "")
            self.assertTrue((root / "scoreboard.json").is_file())
            self.assertTrue((root / "scoreboard.md").is_file())

    def test_daily_fixture_run_prints_bounded_top_queue_digest_and_keeps_full_private_board(self):
        fixture = ROOT / "tests" / "fixtures" / "scoreboard.json"
        with tempfile.TemporaryDirectory() as directory:
            environment = {**os.environ, "HOME": directory}
            command = [
                sys.executable,
                str(SCRIPT),
                "--fixture",
                str(fixture),
                "--now",
                "2026-09-03T12:00:00Z",
                "--daily",
            ]

            result = subprocess.run(command, capture_output=True, text=True, check=True, env=environment)

            private_board = pathlib.Path(directory) / ".hermes" / "steward-os" / "scoreboard.md"
            self.assertIn("Steward daily digest", result.stdout)
            self.assertIn("Top queue: keithah/fixture#7", result.stdout)
            self.assertNotIn("| Key | Kind | Severity |", result.stdout)
            self.assertLess(len(result.stdout), 600)
            self.assertIn("| Key | Kind | Severity |", private_board.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
