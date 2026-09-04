import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from steward_runtime.watchdog import run_watchdog, verify_ledger_entry


ROOT = pathlib.Path(__file__).resolve().parents[1]
WATCHDOG_SCRIPT = ROOT / "scripts" / "steward-label-watchdog.py"
BAD_FIXTURE = ROOT / "tests" / "fixtures" / "bad-ledger.jsonl"


class FakeReadOnlyClient:
    """A fixture client deliberately lacking all GitHub write methods."""

    def __init__(self, details):
        self._details = details
        self.reads = []

    def get_json(self, path, params=None):
        self.reads.append((path, params))
        return self._details[path]


def write_ledger(root, entry):
    path = root / "label-ledger.jsonl"
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    return path


def clean_entry(**overrides):
    entry = {
        "action": "add_label",
        "repository": "keithah/a",
        "number": 1,
        "label": "steward:size:M",
        "changed_lines": 100,
        "url": "https://github.com/keithah/a/pull/1",
        "timestamp": "2026-09-03T00:00:00Z",
    }
    entry.update(overrides)
    return entry


def clean_detail(**overrides):
    detail = {"labels": [{"name": "steward:size:M"}], "additions": 60, "deletions": 40}
    detail.update(overrides)
    return detail


class WatchdogTests(unittest.TestCase):
    def test_clean_ledger_entry_is_silent_and_uses_only_live_read(self):
        client = FakeReadOnlyClient({"repos/keithah/a/pulls/1": clean_detail()})

        with tempfile.TemporaryDirectory() as directory:
            ledger = write_ledger(pathlib.Path(directory), clean_entry())
            findings = run_watchdog(ledger, client, {"keithah/a"})

        self.assertEqual(findings, [])
        self.assertEqual(client.reads, [("repos/keithah/a/pulls/1", None)])

    def test_wrong_recorded_label_is_a_red_source_mismatch(self):
        entry = clean_entry(label="steward:size:S", changed_lines=600)
        client = FakeReadOnlyClient({"repos/keithah/a/pulls/1": clean_detail()})

        with tempfile.TemporaryDirectory() as directory:
            findings = run_watchdog(write_ledger(pathlib.Path(directory), entry), client, {"keithah/a"})

        self.assertEqual(findings[0]["severity"], "red")
        self.assertIn("source mismatch", findings[0]["reason"])

    def test_rechecks_live_pr_for_unsupported_action_when_target_is_usable(self):
        client = FakeReadOnlyClient({"repos/keithah/a/pulls/1": clean_detail(labels=[], additions=90, deletions=20)})
        findings = verify_ledger_entry(clean_entry(action="remove_label", label="bug"), client, set())

        reasons = [finding["reason"] for finding in findings]
        self.assertTrue(any("unsupported action shape" in reason for reason in reasons))
        self.assertTrue(any("off-allowlist" in reason for reason in reasons))
        self.assertTrue(any("not onboarded" in reason for reason in reasons))
        self.assertTrue(any("missing label" in reason for reason in reasons))
        self.assertTrue(any("live additions plus deletions" in reason for reason in reasons))
        self.assertEqual(client.reads, [("repos/keithah/a/pulls/1", None)])

    def test_rechecks_live_pr_even_when_label_is_off_allowlist_or_repo_is_not_onboarded(self):
        client = FakeReadOnlyClient({"repos/keithah/a/pulls/1": clean_detail()})
        findings = verify_ledger_entry(clean_entry(label="bug"), client, set())

        reasons = [finding["reason"] for finding in findings]
        self.assertTrue(any("off-allowlist" in reason for reason in reasons))
        self.assertTrue(any("not onboarded" in reason for reason in reasons))
        self.assertEqual(client.reads, [("repos/keithah/a/pulls/1", None)])

    def test_negative_live_total_is_a_red_finding_even_when_sum_matches_ledger(self):
        client = FakeReadOnlyClient({"repos/keithah/a/pulls/1": clean_detail(additions=-1, deletions=101)})

        findings = verify_ledger_entry(clean_entry(changed_lines=100), client, {"keithah/a"})

        self.assertTrue(any("live totals must be non-negative" in finding["reason"] for finding in findings))

    def test_detects_label_missing_and_live_total_mismatch(self):
        client = FakeReadOnlyClient(
            {"repos/keithah/a/pulls/1": clean_detail(labels=[], additions=90, deletions=20)}
        )

        findings = verify_ledger_entry(clean_entry(), client, {"keithah/a"})

        reasons = [finding["reason"] for finding in findings]
        self.assertTrue(any("missing label" in reason for reason in reasons))
        self.assertTrue(any("source mismatch" in reason for reason in reasons))

    def test_malformed_ledger_json_is_a_red_finding_and_other_entries_are_checked(self):
        client = FakeReadOnlyClient({"repos/keithah/a/pulls/1": clean_detail()})
        with tempfile.TemporaryDirectory() as directory:
            ledger = pathlib.Path(directory) / "label-ledger.jsonl"
            ledger.write_text("{not json}\n" + json.dumps(clean_entry()) + "\n", encoding="utf-8")
            findings = run_watchdog(ledger, client, {"keithah/a"})

        self.assertEqual(findings, [{"severity": "red", "reference": "ledger line 1", "reason": "unsupported action shape: malformed JSON"}])
        self.assertEqual(client.reads, [("repos/keithah/a/pulls/1", None)])

    def test_corrupt_ledger_fields_are_red_not_silent(self):
        corrupt_entries = [
            clean_entry(repository=""),
            clean_entry(number=-1),
            clean_entry(url="https://github.com/keithah/a/pull/2"),
            clean_entry(timestamp="not-a-timestamp"),
        ]

        for entry in corrupt_entries:
            with self.subTest(entry=entry):
                findings = verify_ledger_entry(entry, FakeReadOnlyClient({}), {"keithah/a"})
                self.assertTrue(any("unsupported action shape" in finding["reason"] for finding in findings))

    def test_transport_failure_is_red_and_retains_prior_violations(self):
        class FailingReadOnlyClient:
            def get_json(self, path, params=None):
                raise subprocess.CalledProcessError(1, ["gh", "api"])

        findings = verify_ledger_entry(clean_entry(action="remove_label", label="bug"), FailingReadOnlyClient(), set())

        reasons = [finding["reason"] for finding in findings]
        self.assertTrue(any("unsupported action shape" in reason for reason in reasons))
        self.assertTrue(any("off-allowlist" in reason for reason in reasons))
        self.assertTrue(any("not onboarded" in reason for reason in reasons))
        self.assertTrue(any("live read failed" in reason for reason in reasons))

    def test_invalid_live_response_is_red_and_retains_prior_violations(self):
        client = FakeReadOnlyClient({"repos/keithah/a/pulls/1": []})
        findings = verify_ledger_entry(clean_entry(action="remove_label", label="bug"), client, set())

        reasons = [finding["reason"] for finding in findings]
        self.assertTrue(any("unsupported action shape" in reason for reason in reasons))
        self.assertTrue(any("off-allowlist" in reason for reason in reasons))
        self.assertTrue(any("not onboarded" in reason for reason in reasons))
        self.assertTrue(any("live PR response is not an object" in reason for reason in reasons))


class WatchdogScriptTests(unittest.TestCase):
    def test_bad_fixture_prints_red_and_exits_nonzero_without_live_github(self):
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                [sys.executable, str(WATCHDOG_SCRIPT), "--fixture", str(BAD_FIXTURE)],
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": home},
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RED Steward label watchdog", result.stdout)

    def test_malformed_fixture_is_a_red_nonzero_internal_error(self):
        with tempfile.TemporaryDirectory() as home:
            fixture = pathlib.Path(home) / "corrupt-fixture.json"
            fixture.write_text("{not json}", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(WATCHDOG_SCRIPT), "--fixture", str(fixture)],
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": home},
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("RED Steward label watchdog: failed closed:", result.stdout)
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
