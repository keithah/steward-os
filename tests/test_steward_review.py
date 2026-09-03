import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class StewardReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repo = self.root / "widget"
        self.repo.mkdir()
        self.run_git("init")
        self.run_git("checkout", "-b", "main")
        self.run_git("config", "user.email", "tests@example.invalid")
        self.run_git("config", "user.name", "Steward Tests")
        self.run_git("remote", "add", "origin", "https://github.com/acme/widget.git")

        (self.repo / "README.md").write_text("base\n")
        self.run_git("add", "README.md")
        self.run_git("commit", "-m", "base")
        self.base_head = self.git_output("rev-parse", "HEAD")

        self.run_git("checkout", "-b", "feature/exact-state")
        (self.repo / "feature.txt").write_text("feature\n")
        self.run_git("add", "feature.txt")
        self.run_git("commit", "-m", "feature")
        self.feature_head = self.git_output("rev-parse", "HEAD")

        self.report_root = self.root / "reports"
        self.manifest_root = self.root / "manifests"
        self.config_path = self.root / "config.json"
        self.config = {
            "repository": {"id": "acme/widget", "base_ref": "main"},
            "paths": {
                "report_root": str(self.report_root),
                "manifest_root": str(self.manifest_root),
            },
            "review": {
                "sensitive_paths": ["auth/**"],
                "visual_paths": ["web/**"],
                "deep_paths": ["src/**"],
                "execute_contributor_code": False,
                "sandbox_available": False,
                "commands": [
                    {
                        "id": "test",
                        "command": "python3 -m unittest",
                        "execution": "disabled",
                    }
                ],
            },
        }
        self.write_config()
        self.runner = Path(__file__).resolve().parents[1] / "scripts" / "steward_review.py"

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_git(self, *args):
        subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            text=True,
            capture_output=True,
        )

    def git_output(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config))

    def run_runner(self):
        return subprocess.run(
            [
                sys.executable,
                str(self.runner),
                "--repo-dir",
                str(self.repo),
                "--config",
                str(self.config_path),
            ],
            text=True,
            capture_output=True,
        )

    def read_manifest(self):
        manifests = list(self.manifest_root.rglob("*.json"))
        self.assertEqual(len(manifests), 1)
        return json.loads(manifests[0].read_text())

    def test_writes_exact_state_for_clean_repository(self):
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = self.read_manifest()
        self.assertEqual(manifest["repository"], "acme/widget")
        self.assertEqual(manifest["base_ref"], "main")
        self.assertEqual(manifest["branch"], "feature/exact-state")
        self.assertEqual(manifest["head_sha"], self.feature_head)
        self.assertEqual(manifest["base_sha"], self.base_head)
        self.assertEqual(manifest["merge_base_sha"], self.base_head)
        self.assertEqual(manifest["changed_paths"], ["feature.txt"])
        self.assertEqual(
            manifest["config_revision"],
            hashlib.sha256(
                json.dumps(
                    self.config, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(manifest["commands"], [])
        self.assertEqual(manifest["skipped_checks"], [])
        self.assertEqual(manifest["status"], "ready")
        self.assertEqual(
            self.manifest_root
            / "acme__widget"
            / "branch-feature-exact-state"
            / f"{self.feature_head}.json",
            next(self.manifest_root.rglob("*.json")),
        )

    def test_rejects_dirty_repository_before_writing_manifest(self):
        (self.repo / "dirty.txt").write_text("uncommitted\n")
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("working tree is dirty", result.stderr)
        self.assertFalse(list(self.manifest_root.rglob("*.json")))

    def test_rejects_state_path_inside_reviewed_checkout(self):
        self.config["paths"]["manifest_root"] = str(self.repo / "state")
        self.write_config()
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be outside reviewed checkout", result.stderr)
    def test_rejects_invalid_nested_configuration(self):
        cases = [
            ("unknown top-level key", lambda config: config.update(extra=True)),
            ("missing review key", lambda config: config.pop("review")),
            ("unknown repository key", lambda config: config["repository"].update(extra=True)),
            ("non-string repository id", lambda config: config["repository"].update(id=3)),
            ("mismatched repository identity", lambda config: config["repository"].update(id="acme/other")),
            ("non-list sensitive paths", lambda config: config["review"].update(sensitive_paths="auth/**")),
            ("non-string visual path", lambda config: config["review"].update(visual_paths=[3])),
            ("non-boolean sandbox flag", lambda config: config["review"].update(sandbox_available="false")),
            ("non-list commands", lambda config: config["review"].update(commands={})),
            ("unknown command key", lambda config: config["review"]["commands"][0].update(extra=True)),
            ("blank command id", lambda config: config["review"]["commands"][0].update(id=" ")),
            ("duplicate command id", lambda config: config["review"].update(commands=[
                {"id": "same", "command": "one", "execution": "disabled"},
                {"id": "same", "command": "two", "execution": "disabled"},
            ])),
            ("invalid command execution", lambda config: config["review"]["commands"][0].update(execution="unsafe")),
            ("relative root", lambda config: config["paths"].update(report_root="relative")),
            ("equal roots", lambda config: config["paths"].update(report_root=str(self.manifest_root))),
        ]
        original_config = copy.deepcopy(self.config)
        for label, mutate in cases:
            with self.subTest(label=label):
                self.config = copy.deepcopy(original_config)
                mutate(self.config)
                self.write_config()
                result = self.run_runner()
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertFalse(list(self.manifest_root.rglob("*.json")))


if __name__ == "__main__":
    unittest.main()
