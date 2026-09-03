# Hermes PR Review Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first Hermes-native StewardOS slice: a local-only, exact-SHA PR-review runner that is mandatory before Hermes opens a PR and is available through both `run steward` and `steward`.

**Architecture:** The public fork provides a dependency-free Python runner, a JSON local-configuration schema, an exact Git-state manifest, tests, and a Hermes procedure. Each managed repository stores its configuration, manifests, and reports outside the public checkout. Hermes consumes the manifest to perform the primary and adversarial passes and writes the final local-only report; the terminal launcher simply invokes that same Hermes procedure.

**Tech Stack:** Python 3 standard library (`argparse`, `json`, `pathlib`, `subprocess`, `unittest`), Bash launcher, Markdown skills and documentation, existing Git and Hermes CLI.

**Spec:** `docs/superpowers/specs/2026-09-03-hermes-steward-os-adoption-design.md`

## Global Constraints

- Never create GitHub comments, reviews, approvals, labels, merges, releases, deployments, pushes, or setting changes in the runner or review procedure.
- The working tree must be clean. Do not stash, commit, reset, or otherwise mutate the target repository to make it reviewable.
- Every conclusion must bind to exact repository, base SHA, merge-base SHA, head SHA, and configuration revision.
- Configuration, manifests, reports, state, credentials, repository inventories, and local hostnames stay outside the public repository.
- The runner supports only trusted operator-supplied deterministic commands. Contributor-authored code is skipped unless configuration says `execute_contributor_code: true`, `sandbox_available: true`, and the command has `execution: "sandbox"`.
- Tests must be written and observed failing before each new production behavior.
- A current clean report can permit a PR workflow to continue but is never approval or merge authority.
- This first slice implements local evidence only. Public lifecycle actions and all non-PR StewardOS workflows remain deferred.

---

## File Structure

- `scripts/steward_review.py` — dependency-free CLI: load/validate local JSON config, resolve exact Git state, select lane, execute eligible deterministic commands, and atomically write a manifest outside the repo.
- `tests/test_steward_review.py` — `unittest` integration tests using temporary Git repositories and safe shell commands.
- `setup/hermes-review-config.example.json` — public sanitized example; this is never read as live configuration.
- `docs/reference/hermes-pr-review-gate.md` — installation, configuration, invocation, evidence, and safety documentation.
- `skills/hermes-pr-review/SKILL.md` — public Hermes procedure that reads the manifest and writes the exact-SHA local report after primary and adversarial passes.
- `README.md`, `skills/README.md`, `_data/skills.yml` — discoverability for the new public runner/skill.
- `/Users/hermes/.config/steward-os/repositories/keithah__steward-os.json` — local-only configuration for the first managed repository; not tracked.
- `/Users/hermes/.config/steward-os/reports/` and `/Users/hermes/.config/steward-os/manifests/` — local-only runtime evidence roots; not tracked.
- `/Users/hermes/.local/bin/steward` — existing stable terminal entrypoint, changed only after the public runner passes its end-to-end dry run.
- `/Users/hermes/.hermes/skills/github/steward-pr-gate/SKILL.md` — local Hermes enforcement procedure, changed only after the public runner passes its end-to-end dry run.

## Configuration Interface

A live configuration is JSON at `<config-dir>/<owner>__<repo>.json`:

```json
{
  "repository": {"id": "owner/repo", "base_ref": "main"},
  "paths": {"report_root": "/absolute/private/reports", "manifest_root": "/absolute/private/manifests"},
  "review": {
    "sensitive_paths": ["auth/**"],
    "visual_paths": ["web/**"],
    "deep_paths": ["src/**"],
    "execute_contributor_code": false,
    "sandbox_available": false,
    "command_timeout_seconds": 300,
    "safe_commands_execute_reviewed_code": false,
    "commands": [
      {"id": "test", "command": "python3 -m unittest", "execution": "safe"}
    ]
  }
}
```

`execution` is exactly one of `safe`, `sandbox`, or `disabled`. Config paths must be absolute, must not resolve inside the reviewed checkout, and must be distinct. The config revision is the SHA-256 of canonical JSON (`sort_keys=True`, compact separators). The runner does not support environment interpolation or secret values.

The manifest is JSON under `<manifest_root>/<owner>__<repo>/branch-<sanitized-branch>/<head_sha>.json` and contains `status`, `repository`, `branch`, `head_sha`, `base_ref`, `base_sha`, `merge_base_sha`, `config_revision`, `lane`, `changed_paths`, `commands`, and `skipped_checks`. Command output is bounded to 16,384 UTF-8 bytes per stream, preserving a `truncated` boolean. `status` is `ready` only if configuration/Git validation succeeds and every eligible configured command exits 0; otherwise it is `blocked`.

### Task 1: Exact Git State and Local Configuration Validation

**Files:**
- Create: `scripts/steward_review.py`
- Create: `tests/test_steward_review.py`
- Create: `setup/hermes-review-config.example.json`

**Interfaces:**
- Produces `load_config(path: Path, repo_dir: Path) -> dict`.
- Produces `git_state(repo_dir: Path, base_ref: str) -> dict` with `repository`, `branch`, `head_sha`, `base_sha`, `merge_base_sha`, and `changed_paths`.
- Produces CLI `python3 scripts/steward_review.py --repo-dir PATH --config PATH`.

- [ ] **Step 1: Write the failing configuration/Git-state tests**

Add a test helper that initializes a temporary Git repository with `main`, an `origin` URL of `https://github.com/acme/widget.git`, one base commit, and one feature commit. Add these tests:

```python
def test_writes_exact_state_for_clean_repository(self):
    result = self.run_runner()
    self.assertEqual(result.returncode, 0, result.stderr)
    manifest = self.read_manifest()
    self.assertEqual(manifest["repository"], "acme/widget")
    self.assertEqual(manifest["head_sha"], self.feature_head)
    self.assertEqual(manifest["base_sha"], self.base_head)
    self.assertEqual(manifest["merge_base_sha"], self.base_head)
    self.assertEqual(manifest["status"], "ready")

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
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_steward_review.StewardReviewTests.test_writes_exact_state_for_clean_repository tests.test_steward_review.StewardReviewTests.test_rejects_dirty_repository_before_writing_manifest tests.test_steward_review.StewardReviewTests.test_rejects_state_path_inside_reviewed_checkout -v`

Expected: FAIL because `scripts/steward_review.py` does not exist.

- [ ] **Step 3: Implement the minimal fail-closed runner**

Implement only the interfaces required by the tests. Use `subprocess.run([...], cwd=repo_dir, check=True, text=True, capture_output=True)` for Git calls. Accept only origin URLs matching `https://github.com/<owner>/<repo>.git`, `git@github.com:<owner>/<repo>.git`, or `ssh://git@github.com/<owner>/<repo>.git`; fail closed otherwise. Require `git status --porcelain` to be empty. Reject blank/unknown config keys, non-absolute roots, roots equal to each other, and any root whose resolved path is inside `repo_dir.resolve()`. Do not execute configured commands yet. Atomically write the manifest using a sibling temporary file followed by `Path.replace`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2.

Expected: all 3 tests PASS.

- [ ] **Step 5: Add the sanitized configuration example**

Create `setup/hermes-review-config.example.json` using repository `owner/repository`, absolute placeholder paths `/private/steward-os/reports` and `/private/steward-os/manifests`, and one disabled `test` command. Include no secrets, hostnames, or live repository names.

- [ ] **Step 6: Run the full runner test file and commit**

Run: `python3 -m unittest tests.test_steward_review -v`

Expected: PASS.

Commit:

```bash
git add scripts/steward_review.py tests/test_steward_review.py setup/hermes-review-config.example.json
git commit -m "feat: add exact-state Steward review runner"
```

### Task 2: Lane Selection, Command Safety, and Manifest Evidence

**Files:**
- Modify: `scripts/steward_review.py`
- Modify: `tests/test_steward_review.py`

**Interfaces:**
- Produces `select_lane(changed_paths: list[str], review: dict) -> str` returning exactly `fast`, `deep`, or `visual`.
- Produces a `commands` array in the manifest with `id`, `execution`, `status`, `exit_code`, `stdout`, `stderr`, and `truncated`.
- Produces a `skipped_checks` array of objects with `id` and `reason`.

- [ ] **Step 1: Write one failing command/lane test**

Add one test that changes `web/page.html`, configures `visual_paths: ["web/**"]`, and declares these commands in this order:

```python
[
    {"id": "format", "command": "printf format-ok", "execution": "safe"},
    {"id": "sandboxed", "command": "printf must-not-run", "execution": "sandbox"},
    {"id": "disabled", "command": "printf disabled", "execution": "disabled"},
]
```

Set `execute_contributor_code` and `sandbox_available` to `False`, then assert:

```python
self.assertEqual(manifest["lane"], "visual")
self.assertEqual([item["id"] for item in manifest["commands"]], ["format"])
self.assertEqual(manifest["commands"][0]["stdout"], "format-ok")
self.assertEqual(manifest["skipped_checks"], [
    {"id": "sandboxed", "reason": "sandbox execution unavailable"},
    {"id": "disabled", "reason": "disabled by configuration"},
])
```

- [ ] **Step 2: Run the new test and verify RED**

Run: `python3 -m unittest tests.test_steward_review.StewardReviewTests.test_selects_visual_lane_and_skips_unsafe_commands -v`

Expected: FAIL because lane selection and command evidence are absent.

- [ ] **Step 3: Implement minimal lane/command behavior**

Use `fnmatch.fnmatchcase` for declared path patterns. Select `visual` if any changed path matches `visual_paths`; otherwise select `deep` if any changed path matches `deep_paths` or `sensitive_paths`; otherwise select `fast`. Validate unique non-empty command IDs and the exact allowed execution values. Execute `safe` commands with `shell=True`, `cwd=repo_dir`, `text=True`, and captured output only because the local config is a trusted operator surface. Execute `sandbox` commands only when both execution flags are true; otherwise record the exact skip reason. Never execute `disabled`. If an executed command exits non-zero, record it and write a `blocked` manifest before exiting non-zero. Bound each captured stream to 16,384 characters and set `truncated` when truncating.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 5: Write and run a failing command-failure test**

Add:

```python
def test_records_failed_command_and_returns_blocked_manifest(self):
    self.config["review"]["commands"] = [
        {"id": "failing", "command": "sh -c 'exit 7'", "execution": "safe"}
    ]
    self.write_config()
    result = self.run_runner()
    self.assertEqual(result.returncode, 1)
    manifest = self.read_manifest()
    self.assertEqual(manifest["status"], "blocked")
    self.assertEqual(manifest["commands"][0]["exit_code"], 7)
```

Run: `python3 -m unittest tests.test_steward_review.StewardReviewTests.test_records_failed_command_and_returns_blocked_manifest -v`

Expected: FAIL before the failure-recording implementation, then PASS after it.

- [ ] **Step 6: Run the full runner test file and commit**

Run: `python3 -m unittest tests.test_steward_review -v`

Expected: PASS.

Commit:

```bash
git add scripts/steward_review.py tests/test_steward_review.py
git commit -m "feat: add Steward quality-gate manifest"
```

### Task 3: Public Hermes Procedure and Documentation

**Files:**
- Create: `skills/hermes-pr-review/SKILL.md`
- Create: `docs/reference/hermes-pr-review-gate.md`
- Modify: `README.md`
- Modify: `skills/README.md`
- Modify: `_data/skills.yml`
- Test: `tests/test_steward_review.py`

**Interfaces:**
- The skill consumes a runner manifest and requires a primary review pass for all lanes plus an independent adversarial pass for `deep` and `visual` lanes.
- The report conclusion string is exactly `No verified blocker found in this Steward pass.` only when all required evidence is current and clean.

- [ ] **Step 1: Write the failing documentation/skill contract test**

Add a test that reads `skills/hermes-pr-review/SKILL.md` and asserts it includes each exact string:

```python
required = [
    "python3 scripts/steward_review.py",
    "No verified blocker found in this Steward pass.",
    "do not create or alter any GitHub object",
    "adversarial review",
    "configuration revision",
]
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python3 -m unittest tests.test_steward_review.StewardReviewTests.test_public_skill_has_required_local_only_contract -v`

Expected: FAIL because the skill is missing.

- [ ] **Step 3: Write the public procedure and docs**

Create the public skill with YAML frontmatter whose description begins `Use when reviewing the current committed branch with Hermes StewardOS.` It must instruct Hermes to invoke the runner; read its manifest; stop on a blocked manifest; inspect diff, changed paths, repository instructions, current PR comments/checks if a PR exists, and every deterministic command result; then perform the primary review. It requires a second independent adversarial review for deep/visual lanes, requires a local-only Markdown report outside the public repo, and forbids GitHub writes exactly with the phrase in the failing test.

Create the reference doc showing installation without secrets, the full JSON configuration shape, lane behavior, report/manifest locations, `run steward` and `steward` semantics, failure conditions, and the rule that reports are not approval/merge authority. Link it from README and skills README. Add the skill to `_data/skills.yml` using the repository’s existing format.

- [ ] **Step 4: Run the focused contract test and verify GREEN**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 5: Run static checks available without adding dependencies and commit**

Run:

```bash
python3 -m unittest tests.test_steward_review -v
git diff --check
```

Expected: PASS with no whitespace errors.

Commit:

```bash
git add skills/hermes-pr-review/SKILL.md docs/reference/hermes-pr-review-gate.md README.md skills/README.md _data/skills.yml tests/test_steward_review.py
git commit -m "docs: add Hermes PR review procedure"
```

### Task 4: Local Runtime Integration and End-to-End Dry Run

**Files:**
- Create: `/Users/hermes/.config/steward-os/repositories/keithah__steward-os.json`
- Modify: `/Users/hermes/.local/bin/steward`
- Modify: `/Users/hermes/.hermes/skills/github/steward-pr-gate/SKILL.md`
- Modify: `/Users/hermes/work/AGENTS.md`
- Test: `/Users/hermes/.hermes/scripts/tests/test_steward_launcher.py`

**Interfaces:**
- `steward` calls the public runner from `/Users/hermes/steward-os`, then invokes Hermes only if its manifest is `ready`.
- Chat `run steward` follows the same runner and public `hermes-pr-review` procedure.

- [ ] **Step 1: Write the local configuration with no secrets**

Create `/Users/hermes/.config/steward-os/repositories/keithah__steward-os.json` with repository `keithah/steward-os`, base ref `main`, private report and manifest roots under `/Users/hermes/.config/steward-os/`, empty sensitive/visual/deep patterns, and one safe command `python3 -m unittest tests.test_steward_review -v`. Create the private roots with mode `0700` and configuration file mode `0600`.

- [ ] **Step 2: Extend the launcher test first**

Add a test to `/Users/hermes/.hermes/scripts/tests/test_steward_launcher.py` that uses a fake `python3` runner and fake `hermes` executable. It must prove the launcher invokes `scripts/steward_review.py` before `hermes`, passes the current repository, and does not invoke Hermes when the fake runner returns non-zero.

Run: `python3 -m unittest discover -s /Users/hermes/.hermes/scripts/tests -p 'test_steward_launcher.py' -v`

Expected: FAIL because the old launcher calls Hermes directly.

- [ ] **Step 3: Repoint the terminal launcher minimally**

Update `/Users/hermes/.local/bin/steward` so it locates `/Users/hermes/steward-os/scripts/steward_review.py`, runs it with `--repo-dir "$repo_dir"` and `--config-dir /Users/hermes/.config/steward-os/repositories`, exits if the runner fails, locates the manifest deterministically from the runner output, then invokes Hermes with the public skill and manifest path. Preserve dirty-tree refusal as defense in depth. Do not add a GitHub CLI invocation.

- [ ] **Step 4: Run launcher tests and verify GREEN**

Run: `python3 -m unittest discover -s /Users/hermes/.hermes/scripts/tests -p 'test_steward_launcher.py' -v`

Expected: PASS.

- [ ] **Step 5: Replace local Hermes enforcement instructions**

Patch the global `steward-pr-gate` skill and active workspace `AGENTS.md` so `run steward` executes `python3 /Users/hermes/steward-os/scripts/steward_review.py` with the local config directory before reading the public `skills/hermes-pr-review/SKILL.md`. Retain the universal pre-PR requirement, exact-SHA staleness rule, local-only rule, and no-write prohibition.

- [ ] **Step 6: End-to-end dry run on `keithah/steward-os`**

From `/Users/hermes/steward-os`, run `steward` at a clean committed head. Verify a fresh manifest and report contain the same exact head SHA, base SHA, merge-base SHA, config revision, command evidence, lane, and local-only conclusion. Verify no GitHub write happened by checking that the command uses no `gh` subcommand and querying current repository activity only after the run.

- [ ] **Step 7: Commit public work, run the required pre-PR Steward review, and prepare handoff**

Commit only the public Task 4-independent changes if any are needed; local runtime files are not committed to the public fork. Then run `steward` against the public branch’s exact final HEAD. Do not create a PR until the report for that exact SHA contains `No verified blocker found in this Steward pass.` and all public tests are green.
