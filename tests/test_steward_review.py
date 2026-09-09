import json
import os
import stat
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
        self.run_git("checkout", "-b", "feature/exact-state")
        (self.repo / "feature.txt").write_text("feature\n")
        self.run_git("add", "feature.txt")
        self.run_git("commit", "-m", "feature")
        self.runner = Path(__file__).resolve().parents[1] / "scripts" / "steward_review.py"

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True, text=True, capture_output=True)

    def policy_root(self):
        root = self.root / "policy"
        root.mkdir(mode=0o700)
        root.chmod(0o700)
        return root

    def write_policy(self, root, *, mutate=None):
        policy = {
            "paths": {
                "report_root": str(self.root / "global-reports"),
                "manifest_root": str(self.root / "global-manifests"),
            },
            "review": {
                "sensitive_paths": ["auth/**"],
                "visual_paths": ["web/**"],
                "deep_paths": ["**"],
                "reviewers": {
                    "primary": {"provider": "anthropic", "model": "claude-opus-4-6"},
                    "adversarial": {"provider": "openai-codex", "model": "gpt-5.6-terra"},
                },
                "execute_contributor_code": False,
                "sandbox_available": False,
                "command_timeout_seconds": 300,
                "safe_commands_execute_reviewed_code": False,
                "commands": [],
            },
        }
        if mutate:
            mutate(policy)
        (root / "policy.json").write_text(json.dumps(policy))
        return policy

    def run_runner(self, *, env=None, extra_args=()):
        return subprocess.run(
            [sys.executable, str(self.runner), "--repo-dir", str(self.repo), *extra_args],
            text=True,
            capture_output=True,
            env=env,
        )

    def ready_env(self, policy_root):
        return {**os.environ, "STEWARD_POLICY_ROOT": str(policy_root)}

    def read_manifest(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest_path = Path(result.stdout.strip())
        self.assertTrue(manifest_path.is_file())
        return json.loads(manifest_path.read_text())

    def test_rejects_full_config_flags_before_ready_policy_manifest_output_or_write(self):
        policy_root = self.policy_root()
        self.write_policy(policy_root)
        config = self.root / "complete-config.json"
        config.write_text(json.dumps({"repository": {"id": "acme/widget"}}))
        config_dir = self.root / "repositories"
        config_dir.mkdir()
        (config_dir / "acme__widget.json").write_text(config.read_text())

        for flag, value in (("--config", config), ("--config-dir", config_dir)):
            with self.subTest(flag=flag):
                result = self.run_runner(env=self.ready_env(policy_root), extra_args=(flag, str(value)))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("unrecognized arguments", result.stderr)
                self.assertFalse((self.root / "global-manifests").exists())

    def test_private_global_policy_produces_ready_manifests_for_origin_identities(self):
        policy_root = self.policy_root()
        self.write_policy(policy_root)
        first = self.read_manifest(self.run_runner(env=self.ready_env(policy_root)))
        self.run_git("remote", "set-url", "origin", "https://github.com/acme/other.git")
        second = self.read_manifest(self.run_runner(env=self.ready_env(policy_root)))

        self.assertEqual(first["repository"], "acme/widget")
        self.assertEqual(second["repository"], "acme/other")
        self.assertEqual(first["config_source"], "private-global-policy")
        self.assertEqual(first["required_reviewers"], {
            "primary": {"provider": "anthropic", "model": "claude-opus-4-6"},
            "adversarial": {"provider": "openai-codex", "model": "gpt-5.6-terra"},
        })

    def test_no_policy_remains_blocked_diagnostic_baseline(self):
        state_root = self.root / "default-state"
        result = self.run_runner(env={**os.environ, "STEWARD_STATE_ROOT": str(state_root)})
        self.assertEqual(result.returncode, 1, result.stderr)
        manifest = json.loads(Path(result.stdout.strip()).read_text())
        self.assertEqual(manifest["config_source"], "builtin-default")
        self.assertEqual(manifest["status"], "blocked")
        self.assertIn("no repository-specific review configuration", manifest["evidence_gaps"])

    def test_rejects_nonprivate_global_policy_root_before_manifest_write(self):
        policy_root = self.policy_root()
        self.write_policy(policy_root)
        policy_root.chmod(0o755)
        result = self.run_runner(env=self.ready_env(policy_root))
        self.assertEqual(result.returncode, 1)
        self.assertIn("STEWARD_POLICY_ROOT must be owner-private", result.stderr)
        self.assertFalse((self.root / "global-manifests").exists())

    def test_rejects_policy_repository_identity(self):
        policy_root = self.policy_root()
        self.write_policy(policy_root, mutate=lambda policy: policy.update(repository={"id": "acme/other"}))
        result = self.run_runner(env=self.ready_env(policy_root))
        self.assertEqual(result.returncode, 1)
        self.assertIn("global policy contains unknown keys: repository", result.stderr)
        self.assertFalse((self.root / "global-manifests").exists())

    def test_rejects_policy_without_pinned_dual_reviewer_identities(self):
        policy_root = self.policy_root()
        self.write_policy(policy_root, mutate=lambda policy: policy["review"]["reviewers"]["adversarial"].update(model="grok-4.6"))
        result = self.run_runner(env=self.ready_env(policy_root))
        self.assertEqual(result.returncode, 1)
        self.assertIn("global policy reviewers must pin Opus primary and GPT Terra adversarial", result.stderr)

    def test_rejects_malformed_global_policy_review_fields_before_manifest_write(self):
        policy_root = self.policy_root()
        for mutate in (
            lambda policy: policy["review"].update(command_timeout_seconds="300"),
            lambda policy: policy["review"].update(deep_paths="**"),
            lambda policy: policy["review"].update(execute_contributor_code="false"),
        ):
            with self.subTest(mutate=mutate):
                self.write_policy(policy_root, mutate=mutate)

                result = self.run_runner(env=self.ready_env(policy_root))

                self.assertEqual(result.returncode, 1)
                self.assertFalse((self.root / "global-manifests").exists())

    def test_creates_every_nested_global_state_component_owner_private(self):
        policy_root = self.policy_root()
        policy = self.write_policy(policy_root)
        private_state = self.root / "private-state"
        policy["paths"] = {
            "report_root": str(private_state / "reports" / "artifacts"),
            "manifest_root": str(private_state / "manifests" / "ready"),
        }
        (policy_root / "policy.json").write_text(json.dumps(policy))

        manifest = self.read_manifest(self.run_runner(env=self.ready_env(policy_root)))

        self.assertEqual(manifest["status"], "ready")
        for path in (
            private_state,
            private_state / "reports",
            private_state / "reports" / "artifacts",
            private_state / "manifests",
            private_state / "manifests" / "ready",
            Path(manifest["report_root"]),
            Path(manifest["report_root"]).parent,
        ):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_rejects_nonprivate_existing_nested_state_component_without_chmodding(self):
        policy_root = self.policy_root()
        policy = self.write_policy(policy_root)
        private_state = self.root / "private-state"
        nonprivate = private_state / "manifests"
        nonprivate.mkdir(parents=True, mode=0o755)
        nonprivate.chmod(0o755)
        policy["paths"]["manifest_root"] = str(nonprivate / "ready")
        (policy_root / "policy.json").write_text(json.dumps(policy))

        result = self.run_runner(env=self.ready_env(policy_root))

        self.assertEqual(result.returncode, 1)
        self.assertIn("owner-private", result.stderr)
        self.assertEqual(stat.S_IMODE(nonprivate.stat().st_mode), 0o755)

    def test_rejects_executable_global_policy(self):
        policy_root = self.policy_root()
        self.write_policy(policy_root, mutate=lambda policy: policy["review"].update(commands=[{"id": "test", "command": "true", "execution": "safe"}]))
        result = self.run_runner(env=self.ready_env(policy_root))
        self.assertEqual(result.returncode, 1)
        self.assertIn("global policy must not configure commands", result.stderr)

    def test_rejects_override_of_repository_identity_reviewers_or_commands(self):
        policy_root = self.policy_root()
        self.write_policy(policy_root)
        overrides = policy_root / "overrides"
        overrides.mkdir(mode=0o700)
        overrides.chmod(0o700)
        override = overrides / "acme__widget.json"
        for key, value in (
            ("repository", {"id": "acme/other"}),
            ("review", {"reviewers": {}}),
            ("review", {"commands": []}),
        ):
            with self.subTest(key=key, value=value):
                override.write_text(json.dumps({key: value}))
                result = self.run_runner(env=self.ready_env(policy_root))
                self.assertEqual(result.returncode, 1)
                self.assertIn("unknown keys", result.stderr)
                self.assertFalse((self.root / "global-manifests").exists())

    def test_accepts_only_narrow_override_of_base_ref_and_lane_globs(self):
        policy_root = self.policy_root()
        self.write_policy(policy_root)
        overrides = policy_root / "overrides"
        overrides.mkdir(mode=0o700)
        overrides.chmod(0o700)
        (overrides / "acme__widget.json").write_text(json.dumps({
            "base_ref": "main",
            "review": {"deep_paths": ["feature.txt"], "visual_paths": [], "sensitive_paths": []},
        }))
        manifest = self.read_manifest(self.run_runner(env=self.ready_env(policy_root)))
        self.assertEqual(manifest["repository"], "acme/widget")
        self.assertEqual(manifest["lane"], "deep")

    def test_private_global_policy_uses_deterministic_detached_manifest_location(self):
        policy_root = self.policy_root()
        policy = self.write_policy(policy_root)
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True, text=True, capture_output=True
        ).stdout.strip()
        self.run_git("checkout", "--detach", head_sha)

        manifest = self.read_manifest(self.run_runner(env=self.ready_env(policy_root)))

        expected = (
            Path(policy["paths"]["manifest_root"])
            / "acme__widget"
            / f"branch-{head_sha}"
            / f"{head_sha}.json"
        )
        self.assertEqual(manifest["branch"], "")
        self.assertTrue(expected.is_file())


if __name__ == "__main__":
    unittest.main()
