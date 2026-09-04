import copy
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from scripts import steward_review


class StewardReviewTests(unittest.TestCase):
    def setUp(self):
        """Exercise the Steward review gate behavior."""
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
                "command_timeout_seconds": 30,
                "safe_commands_execute_reviewed_code": False,
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
        """Exercise the Steward review gate behavior."""
        self.temp_dir.cleanup()

    def run_git(self, *args):
        """Exercise the Steward review gate behavior."""
        subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            text=True,
            capture_output=True,
        )

    def git_output(self, *args):
        """Exercise the Steward review gate behavior."""
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def write_config(self):
        """Exercise the Steward review gate behavior."""
        self.config_path.write_text(json.dumps(self.config))

    def run_runner(self):
        """Exercise the Steward review gate behavior."""
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
        """Exercise the Steward review gate behavior."""
        manifests = list(self.manifest_root.rglob("*.json"))
        self.assertEqual(len(manifests), 1)
        return json.loads(manifests[0].read_text())

    def test_resolves_configuration_from_config_directory(self):
        """Exercise the Steward review gate behavior."""
        config_dir = self.root / "repositories"
        config_dir.mkdir()
        config_path = config_dir / "acme__widget.json"
        config_path.write_text(json.dumps(self.config))
        result = subprocess.run(
            [
                sys.executable,
                str(self.runner),
                "--repo-dir",
                str(self.repo),
                "--config-dir",
                str(config_dir),
            ],
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(next(self.manifest_root.rglob("*.json")).read_text())["repository"], "acme/widget")

    def test_accepts_github_origin_without_dot_git_suffix(self):
        """Bind a standard suffix-free GitHub remote to its private config."""
        self.run_git("remote", "set-url", "origin", "https://github.com/acme/widget")

        result = self.run_runner()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_manifest()["repository"], "acme/widget")

    def test_rejects_configuration_inside_the_reviewed_checkout(self):
        """Reject repo-controlled configuration before any command can run."""
        checkout_config = self.repo / "steward-review.json"
        self.config["review"]["commands"] = [
            {
                "id": "contributor-code",
                "command": "python3 contributor.py",
                "execution": "safe",
            }
        ]
        checkout_config.write_text(json.dumps(self.config))
        (self.repo / "contributor.py").write_text("raise SystemExit('must not run')\n")
        result = subprocess.run(
            [
                sys.executable,
                str(self.runner),
                "--repo-dir",
                str(self.repo),
                "--config",
                str(checkout_config),
            ],
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("configuration path must be outside the reviewed checkout", result.stderr)
        self.assertFalse(self.manifest_root.exists())

    def test_rejects_mutually_exclusive_configuration_flags(self):
        """An explicit config path and config directory cannot be combined."""
        config_dir = self.root / "repositories"
        config_dir.mkdir()
        conflicting = subprocess.run(
            [
                sys.executable,
                str(self.runner),
                "--repo-dir", str(self.repo),
                "--config", str(self.config_path),
                "--config-dir", str(config_dir),
            ],
            text=True,
            capture_output=True,
        )

        self.assertEqual(conflicting.returncode, 2)
        self.assertIn("not allowed with argument", conflicting.stderr)

    def test_uses_builtin_default_when_no_private_config_exists(self):
        """A clean GitHub checkout can receive local-only evidence without setup."""
        state_root = self.root / "default-state"
        result = subprocess.run(
            [
                sys.executable,
                str(self.runner),
                "--repo-dir", str(self.repo),
            ],
            env={**os.environ, "STEWARD_STATE_ROOT": str(state_root)},
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        manifest_path = Path(result.stdout.strip())
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["repository"], "acme/widget")
        self.assertEqual(manifest["base_ref"], "main")
        self.assertEqual(manifest["lane"], "deep")
        self.assertEqual(manifest["commands"], [])
        self.assertEqual(manifest["config_source"], "builtin-default")
        self.assertEqual(state_root.stat().st_mode & 0o777, 0o700)

    def test_builtin_default_uses_a_private_runtime_subdirectory(self):
        """The fallback must not reuse the shared configuration directory itself."""
        home = self.root / "home"
        env = {**os.environ, "HOME": str(home)}
        env.pop("STEWARD_STATE_ROOT", None)
        result = subprocess.run(
            [
                sys.executable,
                str(self.runner),
                "--repo-dir", str(self.repo),
            ],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
        )

        state_root = home / ".config" / "steward-os" / "runtime"
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state_root.stat().st_mode & 0o777, 0o700)
        self.assertTrue(list((state_root / "manifests").rglob("*.json")))

    def test_uses_builtin_default_when_config_directory_has_no_match(self):
        """An absent optional per-repository override falls back safely."""
        config_dir = self.root / "repositories"
        config_dir.mkdir()
        state_root = self.root / "default-state"
        result = subprocess.run(
            [
                sys.executable,
                str(self.runner),
                "--repo-dir", str(self.repo),
                "--config-dir", str(config_dir),
            ],
            env={**os.environ, "STEWARD_STATE_ROOT": str(state_root)},
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(Path(result.stdout.strip()).read_text())
        self.assertEqual(manifest["config_source"], "builtin-default")

    def test_rejects_builtin_state_root_inside_reviewed_checkout(self):
        """The environment override cannot make zero-config state repo-controlled."""
        state_root = self.repo / "steward-state"
        result = subprocess.run(
            [
                sys.executable,
                str(self.runner),
                "--repo-dir", str(self.repo),
            ],
            env={**os.environ, "STEWARD_STATE_ROOT": str(state_root)},
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("built-in state root must be outside reviewed checkout", result.stderr)
        self.assertFalse(state_root.exists())

    def test_rejects_relative_builtin_state_root(self):
        """A relative environment override cannot depend on the runner cwd."""
        working_directory = self.root / "non-target-cwd"
        working_directory.mkdir()
        result = subprocess.run(
            [
                sys.executable,
                str(self.runner),
                "--repo-dir", str(self.repo),
            ],
            cwd=working_directory,
            env={**os.environ, "STEWARD_STATE_ROOT": "relative-state"},
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("STEWARD_STATE_ROOT must be absolute", result.stderr)
        self.assertFalse((working_directory / "relative-state").exists())

    def test_rejects_existing_file_as_builtin_state_root(self):
        """Zero-config setup fails cleanly instead of surfacing FileExistsError."""
        state_root = self.root / "state-file"
        state_root.write_text("do not modify\n")
        result = subprocess.run(
            [
                sys.executable,
                str(self.runner),
                "--repo-dir", str(self.repo),
            ],
            env={**os.environ, "STEWARD_STATE_ROOT": str(state_root)},
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("built-in state root must be a directory", result.stderr)
        self.assertEqual(state_root.read_text(), "do not modify\n")

    def test_rejects_existing_non_private_builtin_state_root_without_chmod(self):
        """Existing directories are never permission-mutated by the runner."""
        state_root = self.root / "existing-state"
        state_root.mkdir(mode=0o755)
        state_root.chmod(0o755)
        result = subprocess.run(
            [
                sys.executable,
                str(self.runner),
                "--repo-dir", str(self.repo),
            ],
            env={**os.environ, "STEWARD_STATE_ROOT": str(state_root)},
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("existing built-in state root must be private", result.stderr)
        self.assertEqual(state_root.stat().st_mode & 0o777, 0o755)

    def test_writes_exact_state_for_clean_repository(self):
        """Exercise the Steward review gate behavior."""
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
        self.assertEqual(manifest["lane"], "fast")
        self.assertEqual(manifest["commands"], [])
        self.assertEqual(
            manifest["skipped_checks"],
            [{"id": "test", "reason": "disabled by configuration"}],
        )
        self.assertEqual(manifest["status"], "ready")
        self.assertEqual(
            self.manifest_root
            / "acme__widget"
            / "branch-feature-exact-state"
            / f"{self.feature_head}.json",
            next(self.manifest_root.rglob("*.json")),
        )

    def test_rejects_dirty_repository_before_writing_manifest(self):
        """Exercise the Steward review gate behavior."""
        (self.repo / "dirty.txt").write_text("uncommitted\n")
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("working tree is dirty", result.stderr)
        self.assertFalse(list(self.manifest_root.rglob("*.json")))

    def test_rejects_state_path_inside_reviewed_checkout(self):
        """Exercise the Steward review gate behavior."""
        self.config["paths"]["manifest_root"] = str(self.repo / "state")
        self.write_config()
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be outside reviewed checkout", result.stderr)

    def test_selects_visual_lane_and_skips_unsafe_commands(self):
        """Exercise the Steward review gate behavior."""
        (self.repo / "web").mkdir()
        (self.repo / "web" / "page.html").write_text("<p>page</p>\n")
        self.run_git("add", "web/page.html")
        self.run_git("commit", "-m", "add web page")
        self.config["review"]["commands"] = [
            {"id": "format", "command": "printf format-ok", "execution": "safe"},
            {"id": "sandboxed", "command": "printf must-not-run", "execution": "sandbox"},
            {"id": "disabled", "command": "printf disabled", "execution": "disabled"},
        ]
        self.config["review"]["execute_contributor_code"] = False
        self.config["review"]["sandbox_available"] = False
        self.write_config()

        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = self.read_manifest()
        self.assertEqual(manifest["lane"], "visual")
        self.assertEqual([item["id"] for item in manifest["commands"]], ["format"])
        self.assertEqual(manifest["commands"][0]["stdout"], "format-ok")
        self.assertEqual(manifest["skipped_checks"], [
            {"id": "sandboxed", "reason": "sandbox execution unavailable"},
            {"id": "disabled", "reason": "disabled by configuration"},
        ])

    def test_rejects_sandbox_commands_until_a_runtime_is_integrated(self):
        """Exercise the Steward review gate behavior."""
        self.config["review"].update(
            execute_contributor_code=True,
            sandbox_available=True,
            commands=[
                {"id": "sandboxed", "command": "printf must-not-run", "execution": "sandbox"}
            ],
        )
        self.write_config()

        result = self.run_runner()

        self.assertEqual(result.returncode, 1)
        self.assertIn("sandbox runtime is not integrated", result.stderr)
        self.assertFalse(list(self.manifest_root.rglob("*.json")))

    def test_rejects_safe_commands_that_execute_reviewed_code_without_a_sandbox(self):
        """Exercise the Steward review gate behavior."""
        self.config["review"].update(
            safe_commands_execute_reviewed_code=True,
            commands=[
                {"id": "tests", "command": "python3 -m unittest", "execution": "safe"}
            ],
        )
        self.write_config()

        result = self.run_runner()

        self.assertEqual(result.returncode, 1)
        self.assertIn("safe commands that execute reviewed code require a sandbox runtime", result.stderr)
        self.assertFalse(list(self.manifest_root.rglob("*.json")))

    def test_records_timeout_as_a_failed_blocking_command(self):
        """Exercise the Steward review gate behavior."""
        self.config["review"].update(
            command_timeout_seconds=1,
            commands=[
                {
                    "id": "hangs",
                    "command": f"{shlex.quote(sys.executable)} -c \"import time; time.sleep(5)\"",
                    "execution": "safe",
                }
            ],
        )
        self.write_config()

        result = self.run_runner()

        self.assertEqual(result.returncode, 1)
        manifest = self.read_manifest()
        self.assertEqual(manifest["status"], "blocked")
        self.assertEqual(manifest["commands"][0]["status"], "failed")
        self.assertEqual(manifest["commands"][0]["exit_code"], None)
        self.assertIn("timed out after 1 seconds", manifest["commands"][0]["stderr"])

    def test_timeout_message_preserves_stderr_capture_bound(self):
        """Keep timeout diagnostics within the documented evidence cap."""
        self.config["review"].update(
            command_timeout_seconds=1,
            commands=[
                {
                    "id": "noisy-hang",
                    "command": (
                        f"{shlex.quote(sys.executable)} -c \"import sys, time; "
                        "sys.stderr.write('x' * 20000); sys.stderr.flush(); time.sleep(5)\""
                    ),
                    "execution": "safe",
                }
            ],
        )
        self.write_config()

        result = self.run_runner()
        manifest = self.read_manifest()
        evidence = manifest["commands"][0]

        self.assertEqual(result.returncode, 1)
        self.assertTrue(evidence["truncated"])
        self.assertLessEqual(len(evidence["stderr"].encode("utf-8")), 16_384)
        self.assertIn("timed out after 1 seconds", evidence["stderr"])

    def test_caps_command_output_in_evidence(self):
        """Exercise the Steward review gate behavior."""
        self.config["review"]["commands"] = [
            {
                "id": "large-output",
                "command": f"{shlex.quote(sys.executable)} -c \"print('x' * 20000)\"",
                "execution": "safe",
            }
        ]
        self.write_config()

        result = self.run_runner()
        manifest = self.read_manifest()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(manifest["commands"][0]["truncated"])
        self.assertEqual(len(manifest["commands"][0]["stdout"]), 16_384)

    def test_termination_tolerates_a_process_that_exits_before_signal_delivery(self):
        """Ignore the race where a process exits before its group is signaled."""
        process = mock.Mock(pid=42)

        with mock.patch.object(
            steward_review.os, "killpg", side_effect=ProcessLookupError
        ):
            steward_review._terminate_process_group(process, steward_review.signal.SIGTERM)

    def test_blocks_manifest_when_a_safe_command_changes_checkout_state(self):
        """Exercise the Steward review gate behavior."""
        self.config["review"]["commands"] = [
            {
                "id": "mutates-tree",
                "command": f"{shlex.quote(sys.executable)} -c \"from pathlib import Path; Path('changed-by-check').write_text('x')\"",
                "execution": "safe",
            }
        ]
        self.write_config()

        result = self.run_runner()

        self.assertEqual(result.returncode, 1)
        manifest = self.read_manifest()
        self.assertEqual(manifest["status"], "blocked")
        self.assertEqual(manifest["post_command_state"]["status"], "failed")
        self.assertIn("working tree changed", manifest["post_command_state"]["reason"])

    def test_blocks_manifest_when_a_safe_command_changes_head(self):
        """Exercise the Steward review gate behavior."""
        self.config["review"]["commands"] = [
            {
                "id": "commits",
                "command": "printf changed > changed-by-check && git add changed-by-check && git commit -m changed-by-check",
                "execution": "safe",
            }
        ]
        self.write_config()

        result = self.run_runner()

        self.assertEqual(result.returncode, 1)
        manifest = self.read_manifest()
        self.assertEqual(manifest["status"], "blocked")
        self.assertEqual(manifest["post_command_state"]["status"], "failed")
        self.assertIn("HEAD changed", manifest["post_command_state"]["reason"])

    def test_blocks_manifest_when_a_safe_command_moves_base_ref(self):
        """Exercise the Steward review gate behavior."""
        self.config["review"]["commands"] = [
            {
                "id": "moves-base",
                "command": "git branch -f main HEAD",
                "execution": "safe",
            }
        ]
        self.write_config()

        result = self.run_runner()

        self.assertEqual(result.returncode, 1)
        manifest = self.read_manifest()
        self.assertEqual(manifest["status"], "blocked")
        self.assertIn("base ref changed", manifest["post_command_state"]["reason"])

    def test_writes_detached_head_manifest_under_head_sha_segment(self):
        """Store detached-HEAD evidence without requiring a branch name."""
        manifest = {
            "repository": "acme/widget",
            "branch": "",
            "head_sha": self.feature_head,
        }

        destination = steward_review.write_manifest(self.manifest_root, manifest)

        self.assertEqual(
            destination,
            self.manifest_root
            / "acme__widget"
            / f"branch-{self.feature_head}"
            / f"{self.feature_head}.json",
        )
        self.assertEqual(json.loads(destination.read_text()), manifest)

    def test_records_failed_command_and_returns_blocked_manifest(self):
        """Exercise the Steward review gate behavior."""
        self.config["review"]["commands"] = [
            {"id": "failing", "command": "sh -c 'exit 7'", "execution": "safe"}
        ]
        self.write_config()

        result = self.run_runner()

        self.assertEqual(result.returncode, 1)
        manifest = self.read_manifest()
        self.assertEqual(manifest["status"], "blocked")
        self.assertEqual(manifest["commands"][0]["exit_code"], 7)

    def test_public_skill_has_required_local_only_contract(self):
        """Exercise the Steward review gate behavior."""
        skill_path = Path(__file__).resolve().parents[1] / "skills" / "hermes-pr-review" / "SKILL.md"
        content = skill_path.read_text()
        required = [
            "python3 scripts/steward_review.py",
            "No verified blocker found in this Steward pass.",
            "do not create or alter any GitHub object",
            "**primary review** for the selected lane",
            "When the selected lane is `deep` or `visual`",
            "config_revision",
        ]
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, content)

    def test_rejects_invalid_nested_configuration(self):
        """Exercise the Steward review gate behavior."""
        cases = [
            ("unknown top-level key", lambda config: config.update(extra=True)),
            ("missing review key", lambda config: config.pop("review")),
            ("unknown repository key", lambda config: config["repository"].update(extra=True)),
            ("non-string repository id", lambda config: config["repository"].update(id=3)),
            ("mismatched repository identity", lambda config: config["repository"].update(id="acme/other")),
            ("non-list sensitive paths", lambda config: config["review"].update(sensitive_paths="auth/**")),
            ("non-string visual path", lambda config: config["review"].update(visual_paths=[3])),
            ("non-boolean sandbox flag", lambda config: config["review"].update(sandbox_available="false")),
            ("zero command timeout", lambda config: config["review"].update(command_timeout_seconds=0)),
            ("boolean command timeout", lambda config: config["review"].update(command_timeout_seconds=True)),
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
