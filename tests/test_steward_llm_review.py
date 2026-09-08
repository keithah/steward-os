import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


class StewardLlmReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repo = self.root / "widget"
        self.repo.mkdir()
        self.report_root = self.root / "reports"
        self.manifest_path = self.root / "manifest.json"
        self.runner = Path(__file__).resolve().parents[1] / "scripts" / "steward_llm_review.py"
        self.head_sha = "a" * 40
        self.base_sha = "b" * 40
        self.merge_base_sha = "c" * 40
        self.config_revision = "d" * 64
        self.manifest = {
            "repository": "acme/widget",
            "branch": "feature/exact-state",
            "head_sha": self.head_sha,
            "base_sha": self.base_sha,
            "merge_base_sha": self.merge_base_sha,
            "config_revision": self.config_revision,
            "status": "ready",
            "report_root": str(self.report_root),
            "required_reviewers": {
                "primary": {"provider": "primary-provider", "model": "primary-model"},
                "adversarial": {
                    "provider": "adversarial-provider",
                    "model": "adversarial-model",
                },
            },
        }
        self.write_manifest()
        self.hermes_log = self.root / "fake-hermes-log.jsonl"
        self.fake_hermes = self.root / "fake-hermes.py"
        self.fake_hermes.write_text(textwrap.dedent("""\
            #!__PYTHON__
            import json
            import os
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            prompt = Path(args[args.index("--query-file") + 1]).read_text()
            with Path(os.environ["FAKE_HERMES_LOG"]).open("a") as log:
                log.write(json.dumps({"args": args, "prompt": prompt}) + "\\n")
            role = "primary" if '"role": "primary"' in prompt else "adversarial"
            behavior = os.environ["FAKE_HERMES_BEHAVIOR"]
            if behavior == "reviewer-fails" and role == "primary":
                raise SystemExit(9)
            if behavior == "writes-valid-primary-only" and role == "adversarial":
                raise SystemExit(0)
            if behavior == "writes-malformed-json" and role == "primary":
                print("not json")
                raise SystemExit(0)
            artifact = {
                "repository": "acme/widget",
                "head_sha": "__HEAD_SHA__",
                "base_sha": "__BASE_SHA__",
                "merge_base_sha": "__MERGE_BASE_SHA__",
                "config_revision": "__CONFIG_REVISION__",
                "role": role,
                "provider": f"{role}-provider",
                "model": f"{role}-model",
                "status": "complete",
                "findings": [],
                "probes": [],
                "limitations": [],
            }
            if behavior == "writes-fallback-model" and role == "primary":
                artifact["model"] = "fallback-model"
            if behavior == "writes-mismatched-sha" and role == "primary":
                artifact["head_sha"] = "e" * 40
            if behavior == "writes-mismatched-role" and role == "primary":
                artifact["role"] = "adversarial"
            print(json.dumps(artifact))
        """).replace("__PYTHON__", sys.executable).replace(
            "__HEAD_SHA__", self.head_sha
        ).replace("__BASE_SHA__", self.base_sha).replace(
            "__MERGE_BASE_SHA__", self.merge_base_sha
        ).replace("__CONFIG_REVISION__", self.config_revision))
        self.fake_hermes.chmod(self.fake_hermes.stat().st_mode | stat.S_IXUSR)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest))

    def run_orchestrator(self, behavior="writes-valid"):
        return subprocess.run(
            [
                sys.executable,
                str(self.runner),
                "--repo-dir",
                str(self.repo),
                "--manifest",
                str(self.manifest_path),
                "--hermes-bin",
                str(self.fake_hermes),
            ],
            text=True,
            capture_output=True,
            env={
                **os.environ,
                "FAKE_HERMES_BEHAVIOR": behavior,
                "FAKE_HERMES_LOG": str(self.hermes_log),
            },
        )

    def test_requires_both_primary_and_adversarial_artifacts(self):
        result = self.run_orchestrator("writes-valid-primary-only")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("adversarial artifact missing", result.stderr)
        self.assertFalse(list(self.report_root.rglob("*.json")))

    def test_rejects_fallback_model_artifact(self):
        result = self.run_orchestrator("writes-fallback-model")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("model mismatch", result.stderr)
        self.assertFalse(list(self.report_root.rglob("*.json")))

    def test_rejects_malformed_json_artifact(self):
        result = self.run_orchestrator("writes-malformed-json")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must emit exactly one JSON object", result.stderr)
        self.assertFalse(list(self.report_root.rglob("*.json")))

    def test_rejects_artifact_bound_to_a_different_head(self):
        result = self.run_orchestrator("writes-mismatched-sha")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("head_sha mismatch", result.stderr)
        self.assertFalse(list(self.report_root.rglob("*.json")))

    def test_rejects_artifact_for_the_wrong_reviewer_role(self):
        result = self.run_orchestrator("writes-mismatched-role")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("role mismatch", result.stderr)
        self.assertFalse(list(self.report_root.rglob("*.json")))

    def test_rejects_nonzero_reviewer_exit(self):
        result = self.run_orchestrator("reviewer-fails")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("primary reviewer failed", result.stderr)
        self.assertFalse(list(self.report_root.rglob("*.json")))

    def test_rejects_missing_report_root(self):
        self.manifest.pop("report_root")
        self.write_manifest()

        result = self.run_orchestrator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("report_root missing", result.stderr)
        self.assertFalse(self.hermes_log.exists())

    def test_persists_valid_primary_and_adversarial_artifacts_atomically(self):
        result = self.run_orchestrator()

        self.assertEqual(result.returncode, 0, result.stderr)
        destination = (
            self.report_root
            / "acme__widget"
            / "branch-feature-exact-state"
            / self.head_sha
        )
        self.assertEqual(
            {path.name for path in destination.glob("*.json")},
            {"primary.json", "adversarial.json"},
        )
        for role in ("primary", "adversarial"):
            artifact = json.loads((destination / f"{role}.json").read_text())
            self.assertEqual(artifact["role"], role)
        self.assertFalse(list(destination.glob(".*.tmp")))

        invocations = [json.loads(line) for line in self.hermes_log.read_text().splitlines()]
        self.assertEqual(len(invocations), 2)
        for invocation, role in zip(invocations, ("primary", "adversarial")):
            args = invocation["args"]
            self.assertIn("--ignore-user-config", args)
            self.assertIn("--oneshot", args)
            self.assertEqual(args[args.index("--provider") + 1], f"{role}-provider")
            self.assertEqual(args[args.index("--model") + 1], f"{role}-model")
            self.assertIn("no GitHub writes", invocation["prompt"])
            self.assertIn("do not execute reviewed code", invocation["prompt"])


if __name__ == "__main__":
    unittest.main()
