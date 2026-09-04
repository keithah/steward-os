import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from unittest.mock import patch

from steward_runtime.labels import bootstrap_repository_labels, desired_label_for_pr, size_label, sync_labels

ROOT = pathlib.Path(__file__).resolve().parents[1]
SYNC_SCRIPT = ROOT / "scripts" / "steward-label-sync.py"
FIXTURE = ROOT / "tests" / "fixtures" / "labels.json"
BOOTSTRAP_SCRIPT = ROOT / "scripts" / "steward-bootstrap-labels.py"


def load_bootstrap_module():
    spec = spec_from_file_location("steward_bootstrap_labels", BOOTSTRAP_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("bootstrap script must be importable for unit tests")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SizeLabelTests(unittest.TestCase):
    def test_size_label_boundaries(self):
        self.assertEqual(size_label(50), "steward:size:S")
        self.assertEqual(size_label(51), "steward:size:M")
        self.assertEqual(size_label(500), "steward:size:L")
        self.assertEqual(size_label(501), "steward:size:XL")

    def test_existing_steward_size_label_is_not_replaced(self):
        pr = {"labels": [{"name": "steward:size:S"}], "additions": 600, "deletions": 0}

        self.assertIsNone(desired_label_for_pr(pr))

    def test_malformed_structured_totals_are_rejected(self):
        for additions, deletions in [(-1, 0), (0, -1), (True, 0), (0, False)]:
            with self.subTest(additions=additions, deletions=deletions):
                self.assertIsNone(desired_label_for_pr({"labels": [], "additions": additions, "deletions": deletions}))


class BootstrapTests(unittest.TestCase):
    def test_frozen_scoreboard_repositories_accepts_a_persisted_selection(self):
        bootstrap = load_bootstrap_module()

        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "scoreboard.json"
            path.write_text(json.dumps({"repositories": ["keithah/b", "keithah/a", "keithah/a"]}), encoding="utf-8")

            self.assertEqual(bootstrap.frozen_scoreboard_repositories(path), ["keithah/a", "keithah/b"])

    def test_frozen_scoreboard_repositories_fails_closed_for_legacy_or_malformed_snapshots(self):
        bootstrap = load_bootstrap_module()

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for name, payload in {
                "legacy.json": {"items": []},
                "malformed.json": {"repositories": ["keithah/a", 7]},
            }.items():
                path = root / name
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "frozen repository selection"):
                    bootstrap.frozen_scoreboard_repositories(path)

    def test_bootstrap_only_adds_absent_allowlisted_labels_when_apply_is_true(self):
        class Client:
            def __init__(self):
                self.added = []

            def get_json(self, path, params=None):
                self.assert_path = (path, params)
                return [{"name": "steward:size:S"}, {"name": "bug"}]

            def create_label(self, repository, label):
                self.added.append((repository, label))

        client = Client()

        proposed = bootstrap_repository_labels("keithah/example", client, apply=False)
        applied = bootstrap_repository_labels("keithah/example", client, apply=True)

        expected = ["steward:size:M", "steward:size:L", "steward:size:XL"]
        self.assertEqual(proposed, expected)
        self.assertEqual(applied, expected)
        self.assertEqual(
            client.added,
            [("keithah/example", label) for label in expected],
        )

    def test_bootstrap_checks_later_label_pages_before_creating(self):
        class Client:
            def __init__(self):
                self.added = []

            def get_json(self, path, params=None):
                if params.get("page") == "2":
                    return [{"name": "steward:size:S"}]
                return [{"name": f"other-{number}"} for number in range(100)]

            def create_label(self, repository, label):
                self.added.append((repository, label))

        client = Client()

        missing = bootstrap_repository_labels("keithah/example", client, apply=True)

        self.assertEqual(missing, ["steward:size:M", "steward:size:L", "steward:size:XL"])
        self.assertNotIn(("keithah/example", "steward:size:S"), client.added)

class SyncTests(unittest.TestCase):
    def test_sync_rejects_malformed_totals_without_external_or_private_mutation(self):
        class Client:
            def __init__(self, additions, deletions):
                self.additions = additions
                self.deletions = deletions
                self.added = []

            def get_json(self, path, params=None):
                if path == "repos/keithah/onboarded/pulls":
                    return [{"number": 8, "draft": False}]
                if path == "repos/keithah/onboarded/pulls/8":
                    return {
                        "number": 8,
                        "draft": False,
                        "additions": self.additions,
                        "deletions": self.deletions,
                        "labels": [],
                    }
                raise AssertionError(f"unexpected read {path}")

            def add_label(self, repository, number, label):
                self.added.append((repository, number, label))

            def create_label(self, repository, label):
                raise AssertionError("sync must not create repository labels")

        for additions, deletions in [(-1, 0), (0, -1), (True, 0), (0, False)]:
            with self.subTest(additions=additions, deletions=deletions):
                with tempfile.TemporaryDirectory() as directory:
                    root = pathlib.Path(directory)
                    state_path = root / "label-state.json"
                    original = {"onboarded_repositories": ["keithah/onboarded"], "processed": {}}
                    state_path.write_text(json.dumps(original), encoding="utf-8")
                    ledger_path = root / "label-ledger.jsonl"
                    client = Client(additions, deletions)

                    self.assertEqual(sync_labels(["keithah/onboarded"], client, ledger_path), [])
                    self.assertEqual(client.added, [])
                    self.assertFalse(ledger_path.exists())
                    self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), original)

    def test_sync_rejects_malformed_totals_before_recovering_a_visible_pending_label(self):
        class Client:
            def __init__(self, additions, deletions):
                self.additions = additions
                self.deletions = deletions
                self.added = []

            def get_json(self, path, params=None):
                if path == "repos/keithah/onboarded/pulls":
                    return [{"number": 8, "draft": False}]
                if path == "repos/keithah/onboarded/pulls/8":
                    return {
                        "number": 8,
                        "draft": False,
                        "additions": self.additions,
                        "deletions": self.deletions,
                        "labels": [{"name": "steward:size:S"}],
                    }
                raise AssertionError(f"unexpected read {path}")

            def add_label(self, repository, number, label):
                self.added.append((repository, number, label))

            def create_label(self, repository, label):
                raise AssertionError("sync must not create repository labels")

        pending_entry = {
            "action": "add_label",
            "repository": "keithah/onboarded",
            "number": 8,
            "label": "steward:size:S",
            "changed_lines": 1,
            "url": "https://github.com/keithah/onboarded/pull/8",
            "timestamp": "2026-09-03T00:00:00Z",
        }
        for additions, deletions in [(-1, 0), (0, -1), (True, 0), (0, False)]:
            with self.subTest(additions=additions, deletions=deletions):
                with tempfile.TemporaryDirectory() as directory:
                    root = pathlib.Path(directory)
                    state_path = root / "label-state.json"
                    original = {
                        "onboarded_repositories": ["keithah/onboarded"],
                        "processed": {"keithah/onboarded#7": "2026-09-02T00:00:00Z"},
                        "pending": {"keithah/onboarded#8": pending_entry},
                    }
                    state_path.write_text(json.dumps(original), encoding="utf-8")
                    ledger_path = root / "label-ledger.jsonl"
                    client = Client(additions, deletions)

                    with patch("steward_runtime.labels._append_ledger_entry") as append_ledger:
                        self.assertEqual(sync_labels(["keithah/onboarded"], client, ledger_path), [])

                    self.assertEqual(client.added, [])
                    append_ledger.assert_not_called()
                    self.assertFalse(ledger_path.exists())
                    self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), original)

    def test_sync_adds_only_missing_label_in_onboarded_repository_and_ledgers_it(self):
        class Client:
            def __init__(self):
                self.added = []

            def get_json(self, path, params=None):
                if path == "repos/keithah/onboarded/pulls":
                    return [{"number": 7, "draft": False}]
                if path == "repos/keithah/onboarded/pulls/7":
                    return {"number": 7, "draft": False, "additions": 184, "deletions": 0, "labels": []}
                if path == "repos/keithah/not-onboarded/pulls":
                    raise AssertionError("must not read a non-onboarded repository")
                raise AssertionError(f"unexpected read {path}")

            def add_label(self, repository, number, label):
                self.added.append((repository, number, label))

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "label-state.json").write_text(
                json.dumps({"onboarded_repositories": ["keithah/onboarded"], "processed": {}}),
                encoding="utf-8",
            )
            ledger_path = root / "label-ledger.jsonl"
            client = Client()

            actions = sync_labels(["keithah/not-onboarded", "keithah/onboarded"], client, ledger_path)

            self.assertEqual(client.added, [("keithah/onboarded", 7, "steward:size:M")])
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0]["changed_lines"], 184)
            self.assertEqual(actions[0]["url"], "https://github.com/keithah/onboarded/pull/7")
            self.assertEqual(
                json.loads(ledger_path.read_text(encoding="utf-8")),
                actions[0],
            )
            state = json.loads((root / "label-state.json").read_text(encoding="utf-8"))
            self.assertIn("keithah/onboarded#7", state["processed"])

    def test_sync_uses_persisted_processed_cursor_to_skip_a_completed_label(self):
        class Client:
            def __init__(self):
                self.added = []

            def get_json(self, path, params=None):
                if path == "repos/keithah/onboarded/pulls":
                    return [{"number": 8, "draft": False}]
                if path == "repos/keithah/onboarded/pulls/8":
                    return {"number": 8, "draft": False, "additions": 1, "deletions": 0, "labels": []}
                raise AssertionError(f"unexpected read {path}")

            def add_label(self, repository, number, label):
                self.added.append((repository, number, label))

            def create_label(self, repository, label):
                raise AssertionError("sync must not create repository labels")

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "label-state.json").write_text(
                json.dumps({"onboarded_repositories": ["keithah/onboarded"], "processed": {}}),
                encoding="utf-8",
            )
            ledger_path = root / "label-ledger.jsonl"
            client = Client()

            sync_labels(["keithah/onboarded"], client, ledger_path)
            sync_labels(["keithah/onboarded"], client, ledger_path)

            self.assertEqual(client.added, [("keithah/onboarded", 8, "steward:size:S")])

    def test_sync_keeps_a_recoverable_pending_record_when_label_write_raises(self):
        class Client:
            def get_json(self, path, params=None):
                if path == "repos/keithah/onboarded/pulls":
                    return [{"number": 8, "draft": False}]
                if path == "repos/keithah/onboarded/pulls/8":
                    return {"number": 8, "draft": False, "additions": 1, "deletions": 0, "labels": []}
                raise AssertionError(f"unexpected read {path}")

            def add_label(self, repository, number, label):
                raise OSError("write failed")

            def create_label(self, repository, label):
                raise AssertionError("sync must not create repository labels")

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            state_path = root / "label-state.json"
            original = {"onboarded_repositories": ["keithah/onboarded"], "processed": {}}
            state_path.write_text(json.dumps(original), encoding="utf-8")
            ledger_path = root / "label-ledger.jsonl"

            with self.assertRaisesRegex(OSError, "write failed"):
                sync_labels(["keithah/onboarded"], Client(), ledger_path)

            self.assertFalse(ledger_path.exists())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["onboarded_repositories"], original["onboarded_repositories"])
            self.assertEqual(state["processed"], {})
            self.assertIn("keithah/onboarded#8", state["pending"])

    def test_sync_fails_closed_when_an_ambiguous_pending_write_has_a_changed_size(self):
        class Client:
            def __init__(self):
                self.added = []
                self.fail_write = True
                self.changed_lines = 1

            def get_json(self, path, params=None):
                if path == "repos/keithah/onboarded/pulls":
                    return [{"number": 8, "draft": False}]
                if path == "repos/keithah/onboarded/pulls/8":
                    return {
                        "number": 8,
                        "draft": False,
                        "additions": self.changed_lines,
                        "deletions": 0,
                        "labels": [],
                    }
                raise AssertionError(f"unexpected read {path}")

            def add_label(self, repository, number, label):
                self.added.append((repository, number, label))
                if self.fail_write:
                    raise OSError("write outcome unknown")

            def create_label(self, repository, label):
                raise AssertionError("sync must not create repository labels")

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            state_path = root / "label-state.json"
            state_path.write_text(
                json.dumps({"onboarded_repositories": ["keithah/onboarded"], "processed": {}}),
                encoding="utf-8",
            )
            ledger_path = root / "label-ledger.jsonl"
            client = Client()

            with self.assertRaisesRegex(OSError, "write outcome unknown"):
                sync_labels(["keithah/onboarded"], client, ledger_path)

            pending_state = json.loads(state_path.read_text(encoding="utf-8"))
            client.fail_write = False
            client.changed_lines = 501

            with self.assertRaisesRegex(ValueError, "ambiguous pending label write"):
                sync_labels(["keithah/onboarded"], client, ledger_path)

            self.assertEqual(client.added, [("keithah/onboarded", 8, "steward:size:S")])
            self.assertFalse(ledger_path.exists())
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), pending_state)

    def test_sync_recovers_a_successful_label_when_ledger_append_fails(self):
        class Client:
            def __init__(self):
                self.labels = []
                self.added = []

            def get_json(self, path, params=None):
                if path == "repos/keithah/onboarded/pulls":
                    return [{"number": 10, "draft": False}]
                if path == "repos/keithah/onboarded/pulls/10":
                    return {"number": 10, "draft": False, "additions": 1, "deletions": 0, "labels": self.labels}
                raise AssertionError(f"unexpected read {path}")

            def add_label(self, repository, number, label):
                self.added.append((repository, number, label))
                self.labels.append({"name": label})

            def create_label(self, repository, label):
                raise AssertionError("sync must not create repository labels")

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            state_path = root / "label-state.json"
            state_path.write_text(
                json.dumps({"onboarded_repositories": ["keithah/onboarded"], "processed": {}}),
                encoding="utf-8",
            )
            ledger_path = root / "label-ledger.jsonl"
            client = Client()

            with patch("steward_runtime.labels._append_ledger_entry", side_effect=OSError("ledger unavailable")):
                with self.assertRaisesRegex(OSError, "ledger unavailable"):
                    sync_labels(["keithah/onboarded"], client, ledger_path)

            pending = json.loads(state_path.read_text(encoding="utf-8"))["pending"]
            self.assertIn("keithah/onboarded#10", pending)
            self.assertEqual(client.added, [("keithah/onboarded", 10, "steward:size:S")])

            recovered = sync_labels(["keithah/onboarded"], client, ledger_path)

            self.assertEqual(client.added, [("keithah/onboarded", 10, "steward:size:S")])
            self.assertEqual(len(recovered), 1)
            self.assertEqual(json.loads(ledger_path.read_text(encoding="utf-8")), recovered[0])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("keithah/onboarded#10", state["processed"])
            self.assertEqual(state["pending"], {})

    def test_dry_run_proposes_actions_without_a_github_write_or_private_mutation(self):
        class Client:
            def __init__(self):
                self.added = []

            def get_json(self, path, params=None):
                if path == "repos/keithah/onboarded/pulls":
                    return [{"number": 9, "draft": False}]
                if path == "repos/keithah/onboarded/pulls/9":
                    return {"number": 9, "draft": False, "additions": 501, "deletions": 0, "labels": []}
                raise AssertionError(f"unexpected read {path}")

            def add_label(self, repository, number, label):
                self.added.append((repository, number, label))

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            state_path = root / "label-state.json"
            original = {"onboarded_repositories": ["keithah/onboarded"], "processed": {}}
            state_path.write_text(json.dumps(original), encoding="utf-8")
            ledger_path = root / "label-ledger.jsonl"
            client = Client()

            actions = sync_labels(["keithah/onboarded"], client, ledger_path, apply=False)

            self.assertEqual(client.added, [])
            self.assertEqual([action["label"] for action in actions], ["steward:size:XL"])
            self.assertFalse(ledger_path.exists())
            self.assertFalse((root / "locks").exists())
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), original)


class LabelSyncScriptTests(unittest.TestCase):
    def test_fixture_dry_run_outputs_no_more_than_limit_and_never_uses_a_write_client(self):
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                [sys.executable, str(SYNC_SCRIPT), "--fixture", str(FIXTURE), "--dry-run", "--limit", "6"],
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": home},
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        actions = [json.loads(line) for line in result.stdout.splitlines() if line]
        self.assertLessEqual(len(actions), 6)
        self.assertEqual([action["action"] for action in actions], ["add_label"] * len(actions))


if __name__ == "__main__":
    unittest.main()
