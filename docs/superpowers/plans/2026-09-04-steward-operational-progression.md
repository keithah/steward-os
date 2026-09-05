# Steward Operational Progression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the local Hermes scheduler health and add private, read-only Steward operational groundwork while preserving the finite, bounded label canary and deferring issue auto-close.

**Architecture:** Local Hermes wrappers own operational side effects and delivery; the StewardOS repository provides deterministic runtime collectors and strict fixture tests. The private runtime root is the only mutable artifact location. New collection modules consume GitHub GET data through the existing client, while the heartbeat reads persisted scheduler and artifact state without restarting or repairing anything.

**Tech Stack:** Python 3.11 standard library, `gh api`, Hermes Cron CLI, JSON/JSONL private artifacts, unittest, macOS launchd.

**Spec:** `docs/superpowers/specs/2026-09-04-steward-operational-progression-design.md`

## Global Constraints

- Preserve the active `b8238d29c1ff` label canary’s hourly cadence, one-label limit, taxonomy, and explicit 23-run expiry; do not promote it automatically.
- No public write is added or expanded: no issue close/file, comment/reply, release announcement, merge, setting change, or label-taxonomy change.
- New runtime collection must use GitHub GET only; no raw community text, credentials, or personal data enters a commit or public artifact.
- Every external state change must be read back before reporting success.
- All private runtime writes are atomic and strict; malformed state fails closed.
- A heartbeat verifies and reports; it never restarts, repairs, pauses, or changes any job.
- Scheduled jobs use absolute interpreters and wrapper paths, `deliver=local`, `failure-deliver=local`, and are verified by persisted definition, direct smoke test, one completed scheduled execution, and destination read-back if they notify.
- New behavior follows RED → GREEN → REFACTOR; all targeted tests must be observed failing before implementation.

---

## File Structure

| File | Responsibility |
|---|---|
| `/Users/hermes/.hermes/scripts/cron_health_watchdog.py` | Existing local scheduler health detector; add testable delivery-neutral behavior only if root-cause investigation requires it. |
| `/Users/hermes/.hermes/scripts/nightly-system-update.sh` | Existing local maintenance wrapper; reconcile a successful replacement gateway without masking actual failed update/restart states. |
| `/Users/hermes/.hermes/scripts/tests/test_nightly_system_update.py` | Shell-harness regression tests for replacement gateway takeover and real restart failure. |
| `/Users/hermes/.hermes/scripts/steward_label_canary_audit.py` | One-time, read-only final audit executed only after finite canary expiry. |
| `steward_runtime/community.py` | Deterministic GitHub issue/PR intake collection, dedupe, strict normalization, bounded digest rendering. |
| `steward_runtime/trust.py` | Strict outcome-ledger parsing, last-write-wins collapse, decayed/shrunk trust map computation. |
| `steward_runtime/heartbeat.py` | Private artifact/job/gateway health evaluation and stable state-transition determination. |
| `steward_runtime/state.py` | Extend private path model for community queue, trust ledger/map, and heartbeat snapshot. |
| `scripts/steward-community-queue.py` | CLI wrapper for community queue refresh and bounded private digest. |
| `scripts/steward-contributor-trust.py` | CLI wrapper for read-only trust-map generation. |
| `scripts/steward-heartbeat.py` | CLI wrapper for evaluating/persisting heartbeat health; no delivery or repair. |
| `tests/test_community.py` | Fixture-based collection/dedupe/fail-closed tests. |
| `tests/test_trust.py` | Fixture-based trust parsing, decay, shrinkage, and correction tests. |
| `tests/test_heartbeat.py` | Fixture-based scheduler/artifact freshness and alert-transition tests. |
| `/Users/hermes/.hermes/scripts/steward_heartbeat.py` | Local wrapper that invokes the repository heartbeat and sends only state transitions to Matrix. |
| `docs/reference/private-runtime-levels.md` | Public operational documentation reflecting accepted read-only runtime capabilities and explicit exclusions. |

## Task 1: Stabilize and verify gateway supervision

**Files:**
- Modify through supported CLI only: local launchd gateway definition
- Verify: `/Users/hermes/.hermes/logs/gateway.log`, Cron persisted state

**Interfaces:**
- Consumes: `hermes gateway start`, `hermes gateway status`, `hermes cron status`
- Produces: one launchd-supervised default-profile gateway and a scheduler heartbeat.

- [ ] **Step 1: Capture the pre-recovery failure evidence**

Run:
```bash
hermes gateway status || true
hermes cron status || true
tail -100 /Users/hermes/.hermes/logs/gateway.log
```

Expected: no active gateway or a missing/obsolete service definition, and log evidence identifying the stop/restart boundary.

- [ ] **Step 2: Start the registered gateway service through Hermes**

Run:
```bash
hermes gateway start
hermes gateway status
hermes cron status
```

Expected: launchd reports one supervised gateway and Cron reports a current ticker heartbeat.

- [ ] **Step 3: Verify no competing live gateway owners**

Run:
```bash
ps -axo pid,ppid,state,etime,command | grep -E '[h]ermes gateway|[h]ermes.*run'
hermes gateway status
```

Expected: one launchd-owned gateway service, no competing foreground gateway.

## Task 2: Repair Cron health-watchdog delivery

**Files:**
- Modify through supported CLI only: Cron job `783c7f82838e`
- Verify: Cron persisted job record and Matrix room read-back

**Interfaces:**
- Consumes: local `cron_health_watchdog.py` stdout; explicit Matrix destination.
- Produces: silent healthy runs and delivered alerts for emitted findings.

- [ ] **Step 1: Verify the current configuration creates the known failure**

Run:
```bash
hermes cron list | grep -A14 -B1 '783c7f82838e'
hermes cron runs 783c7f82838e --limit 5
```

Expected: `deliver=origin` and a completed execution with `unknown platform 'webui'` delivery failure.

- [ ] **Step 2: Change delivery through the supported Cron CLI**

Run:
```bash
hermes cron edit 783c7f82838e \
  --deliver 'matrix:!jfK0a0HnJvfON53LlAbE_WSHCPdM7mP2TSlwoNmBNk8' \
  --failure-deliver local
```

Expected: command succeeds without changing script, cadence, or no-agent mode.

- [ ] **Step 3: Read back the persisted definition**

Run:
```bash
hermes cron list | grep -A14 -B1 '783c7f82838e'
```

Expected: explicit Matrix destination and local failure delivery.

- [ ] **Step 4: Create a one-time deterministic fixture alert acceptance job**

Create `~/.hermes/scripts/cron_health_watchdog_fixture.py` that prints exactly `Cron health alert:\n- fixture finding` and exits `0`; mode `700`. Create a `2m --repeat 1 --no-agent` job with explicit Matrix delivery and local failure delivery.

Run:
```bash
hermes cron create '2m' --repeat 1 --name 'Cron health delivery acceptance' \
  --no-agent --script cron_health_watchdog_fixture.py \
  --deliver 'matrix:!jfK0a0HnJvfON53LlAbE_WSHCPdM7mP2TSlwoNmBNk8' \
  --failure-deliver local
```

Expected: a one-shot ID and a future run time.

- [ ] **Step 5: Verify scheduled delivery, then remove the fixture job and file**

After its due time, run:
```bash
hermes cron runs <fixture-job-id> --limit 1
hermes cron remove <fixture-job-id>
rm -f /Users/hermes/.hermes/scripts/cron_health_watchdog_fixture.py
```

Expected: durable `completed` execution and Matrix read-back of precisely one fixture alert; clean normal watchdog output remains silent.

## Task 3: Reproduce and repair nightly update gateway-takeover handling

**Files:**
- Create: `/Users/hermes/.hermes/scripts/tests/test_nightly_system_update.py`
- Modify: `/Users/hermes/.hermes/scripts/nightly-system-update.sh`

**Interfaces:**
- Consumes: `hermes update --yes --backup` output, `hermes gateway status`, `hermes cron status`.
- Produces: `reconcile_gateway_after_update(update_output, gateway_status, cron_status) -> exit status` behavior expressed in a shell helper; no second restart when a healthy replacement gateway is already supervising the profile.

- [ ] **Step 1: Write the failing subprocess regression test for healthy replacement takeover**

Create a unittest harness that copies the shell script to a temporary directory and places fake `hermes`, `git`, `npx`, `uv`, `launchctl`, and `curl` executables first in `PATH`. Configure fake `hermes update` to emit `Update complete!` plus `STALE (pre-update code)` and exit `1`; configure fake `hermes gateway status` to emit a supervised live gateway; assert the script exits `0` and fake `hermes gateway restart` is never called.

```python
def test_stale_update_with_healthy_replacement_gateway_succeeds_without_restart(self):
    result, calls = run_fixture(update_rc=1, gateway_status="supervised live", cron_status="ticker healthy")
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertNotIn(("gateway", "restart"), calls)
```

- [ ] **Step 2: Run the focused test to verify RED**

Run:
```bash
/opt/homebrew/bin/python3.11 -m unittest /Users/hermes/.hermes/scripts/tests/test_nightly_system_update.py
```

Expected: fail because the current script always invokes `gateway restart` after stale-update output.

- [ ] **Step 3: Implement the smallest bounded reconciliation helper in the shell script**

Add `gateway_is_healthy()` that runs `"$HERMES" gateway status` and requires the exact supervised/running indication; add `scheduler_is_healthy()` requiring `"$HERMES" cron status` and `Ticker heartbeat:`. In the existing stale-update branch, only call `gateway restart` when both checks are not healthy. If both are healthy, log `Replacement gateway already healthy; continuing without restart` and continue. Do not alter any later update/test/push/WebUI failure handling.

```bash
gateway_is_healthy() {
  "$HERMES" gateway status 2>&1 | grep -Eq 'Gateway is supervised|Gateway is running'
}

scheduler_is_healthy() {
  "$HERMES" cron status 2>&1 | grep -q 'Ticker heartbeat:'
}
```

- [ ] **Step 4: Run the focused test to verify GREEN**

Run:
```bash
/opt/homebrew/bin/python3.11 -m unittest /Users/hermes/.hermes/scripts/tests/test_nightly_system_update.py
```

Expected: healthy replacement case passes.

- [ ] **Step 5: Add and run the true-failure regression case**

Add:
```python
def test_stale_update_without_healthy_gateway_fails_if_restart_fails(self):
    result, calls = run_fixture(update_rc=1, gateway_status="not running", cron_status="not running", restart_rc=1)
    self.assertNotEqual(result.returncode, 0)
    self.assertIn(("gateway", "restart"), calls)
```

Run the focused suite. Expected: the new test fails before the implementation has correct nonzero behavior, then passes after minimal correction.

- [ ] **Step 6: Run shell syntax and focused regression suite**

Run:
```bash
bash -n /Users/hermes/.hermes/scripts/nightly-system-update.sh
/opt/homebrew/bin/python3.11 -m unittest /Users/hermes/.hermes/scripts/tests/test_nightly_system_update.py
```

Expected: both pass.

## Task 4: Implement private community queue collection

**Files:**
- Modify: `steward_runtime/state.py`
- Create: `steward_runtime/community.py`
- Create: `scripts/steward-community-queue.py`
- Create: `tests/test_community.py`

**Interfaces:**
- Consumes: `GitHubClient.get_json(path, params)` and `RuntimePaths`.
- Produces:
```python
def collect_community_candidates(repositories: Sequence[str], client: ReadOnlyGitHubClient) -> list[dict[str, object]]: ...
def render_community_digest(candidates: Sequence[Mapping[str, object]], limit: int = 10) -> str: ...
```
- Private paths: `community_queue_json`, `community_queue_md`.

- [ ] **Step 1: Write the failing dedupe/normalization test**

```python
def test_collect_community_candidates_deduplicates_by_repository_kind_and_number(self):
    candidates = collect_community_candidates(["keithah/a"], FixtureClient({...}))
    self.assertEqual([item["key"] for item in candidates], ["keithah/a:issue:4", "keithah/a:pr:7"])
    self.assertNotIn("body", candidates[0])
```

- [ ] **Step 2: Run the test to verify RED**

Run:
```bash
/opt/homebrew/bin/python3.11 -m unittest tests.test_community.CommunityCollectionTests.test_collect_community_candidates_deduplicates_by_repository_kind_and_number
```

Expected: import failure because the module does not exist.

- [ ] **Step 3: Implement strict structured collection**

Collect open issue and PR structured fields only: repository, `kind`, number, immutable `key`, URL, title, author login, created/updated timestamps, labels, state, and draft flag for PRs. Reject malformed pages/items, sort by `updated_at` descending then key, dedupe by key retaining the newest valid record, and never include description/body/comments.

- [ ] **Step 4: Run the focused test to verify GREEN**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 5: Add failing bounded digest and malformed-page tests**

```python
def test_render_community_digest_limits_entries_and_reports_remainder(self):
    self.assertEqual(render_community_digest(items, limit=2), "Community queue: keithah/a#1, keithah/a#2 (+1 more)")

def test_collect_community_candidates_rejects_malformed_response(self):
    with self.assertRaisesRegex(ValueError, "must be an array"):
        collect_community_candidates(["keithah/a"], FixtureClient({"repos/keithah/a/issues": {}}))
```

- [ ] **Step 6: Run RED, implement render/write CLI, then run GREEN**

Implement a CLI that selects the existing private scoreboard repository list, atomically persists JSON and Markdown, prints only the bounded digest when changed, and prints nothing when unchanged. Run:
```bash
/opt/homebrew/bin/python3.11 -m unittest tests.test_community
```
Expected: tests first fail, then pass.

## Task 5: Implement contributor-trust aggregation

**Files:**
- Modify: `steward_runtime/state.py`
- Create: `steward_runtime/trust.py`
- Create: `scripts/steward-contributor-trust.py`
- Create: `tests/test_trust.py`

**Interfaces:**
- Consumes: JSONL outcome ledger where each entry has `pr: str`, `author: str`, `outcome: str`, `at: UTC ISO-8601 string`.
- Produces:
```python
def load_effective_outcomes(path: Path) -> dict[str, TrustEvent]: ...
def compute_trust_map(events: Iterable[TrustEvent], now: datetime, prior: float, shrinkage: float, half_life_days: float) -> dict[str, dict[str, object]]: ...
```
- Private paths: `trust_ledger_jsonl`, `trust_map_json`.

- [ ] **Step 1: Write the failing correction/last-write-wins test**

```python
def test_last_appended_terminal_outcome_wins_for_a_pr(self):
    events = load_effective_outcomes(write_ledger([
        {"pr": "keithah/a#1", "author": "a", "outcome": "clean-ship", "at": "2026-09-01T00:00:00Z"},
        {"pr": "keithah/a#1", "author": "a", "outcome": "regression", "at": "2026-09-02T00:00:00Z"},
    ]))
    self.assertEqual(events["keithah/a#1"].outcome, "regression")
```

- [ ] **Step 2: Run RED**

Run:
```bash
/opt/homebrew/bin/python3.11 -m unittest tests.test_trust.TrustLedgerTests.test_last_appended_terminal_outcome_wins_for_a_pr
```
Expected: import failure.

- [ ] **Step 3: Implement strict ledger parsing and effective-event collapse**

Permit only documented positive, negative, and unscored outcomes; validate nonempty PR/author and timezone-aware `at`; surface malformed/unknown input as `ValueError`; preserve append order for correction precedence.

- [ ] **Step 4: Run GREEN**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 5: Add failing shrinkage/decay/empty-ledger tests**

```python
def test_fresh_single_positive_is_shrunk_toward_prior(self):
    result = compute_trust_map([event("clean-ship")], NOW, prior=0.5, shrinkage=3.0, half_life_days=30.0)
    self.assertAlmostEqual(result["a"]["score"], 0.625)

def test_unscored_event_does_not_affect_effective_n(self): ...
def test_five_half_lives_reverts_toward_prior(self): ...
def test_empty_ledger_publishes_empty_map(self): ...
```

- [ ] **Step 6: Run RED, implement deterministic map/CLI, then run GREEN**

Compute `weight = 0.5 ** (age_days / half_life_days)` and publish only `score`, `events_scored`, `effective_n`, and `last_event_at`. The CLI must not create or backfill ledger events. Run:
```bash
/opt/homebrew/bin/python3.11 -m unittest tests.test_trust
```
Expected: tests first fail, then pass.

## Task 6: Implement read-only fleet/artifact heartbeat

**Files:**
- Modify: `steward_runtime/state.py`
- Create: `steward_runtime/heartbeat.py`
- Create: `scripts/steward-heartbeat.py`
- Create: `tests/test_heartbeat.py`
- Create: `/Users/hermes/.hermes/scripts/steward_heartbeat.py`

**Interfaces:**
- Consumes: parsed cron job records, execution-state snapshot, private artifact paths, prior heartbeat state.
- Produces:
```python
def evaluate_health(now: datetime, jobs: Sequence[Mapping[str, object]], artifacts: Mapping[str, Path]) -> list[dict[str, str]]: ...
def transition_messages(previous: Mapping[str, object], findings: Sequence[Mapping[str, str]]) -> tuple[dict[str, object], list[str]]: ...
```
- Private paths: `heartbeat_state_json`, `heartbeat_snapshot_json`.

- [ ] **Step 1: Write failing stale-job and stale-artifact tests**

```python
def test_evaluate_health_flags_enabled_steward_job_without_fresh_terminal_run(self): ...
def test_evaluate_health_flags_unparseable_or_stale_scoreboard(self): ...
```

- [ ] **Step 2: Run RED**

Run:
```bash
/opt/homebrew/bin/python3.11 -m unittest tests.test_heartbeat.HeartbeatTests.test_evaluate_health_flags_enabled_steward_job_without_fresh_terminal_run
```
Expected: import failure.

- [ ] **Step 3: Implement pure health evaluation**

A job is unhealthy if enabled and its latest terminal status is missing, failed/unknown, or older than its interval/cron-derived freshness allowance. Artifact checks require valid JSON for JSON files and bounded mtime freshness. Gateway/scheduler conditions are injected as explicit facts from the CLI wrapper, never inferred from PID text inside the runtime module.

- [ ] **Step 4: Run GREEN**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 5: Add failing stable-transition tests**

```python
def test_first_fault_emits_one_alert_and_same_fault_is_suppressed(self): ...
def test_clean_after_fault_emits_one_recovery(self): ...
```

- [ ] **Step 6: Run RED, implement transition state and CLI, then run GREEN**

The repository CLI prints only messages from transitions and atomically writes snapshot/state. The local wrapper invokes it, sends each emitted transition through Hermes Send to Matrix, and never restarts, modifies, or pauses jobs. Run:
```bash
/opt/homebrew/bin/python3.11 -m unittest tests.test_heartbeat
```
Expected: tests first fail, then pass.

## Task 7: Validate and schedule read-only operational jobs

**Files:**
- Create/modify local wrappers only: `/Users/hermes/.hermes/scripts/steward_heartbeat.py`
- Modify through supported CLI only: new local Cron records

**Interfaces:**
- Consumes: repository CLIs and explicit local delivery wrappers.
- Produces: hourly community refresh, hourly trust-map refresh, hourly heartbeat with Matrix state-transition delivery only.

- [ ] **Step 1: Compile and direct-smoke all new CLIs**

Run:
```bash
/opt/homebrew/bin/python3.11 -m py_compile steward_runtime/community.py steward_runtime/trust.py steward_runtime/heartbeat.py scripts/steward-community-queue.py scripts/steward-contributor-trust.py scripts/steward-heartbeat.py
/opt/homebrew/bin/python3.11 scripts/steward-community-queue.py
/opt/homebrew/bin/python3.11 scripts/steward-contributor-trust.py
/opt/homebrew/bin/python3.11 scripts/steward-heartbeat.py
```

Expected: private artifacts exist; clean runs are silent; no GitHub writes.

- [ ] **Step 2: Create local-delivery no-agent jobs**

Create jobs using `hermes cron create '60m' --no-agent --script <filename> --workdir /Users/hermes/steward-os --deliver local --failure-deliver local` for community refresh, trust aggregation, and heartbeat. Do not attach write-capable tools or use `origin` delivery.

- [ ] **Step 3: Read persisted job definitions**

Run:
```bash
hermes cron list | grep -A15 -B1 -E 'Steward community queue|Steward contributor trust|Steward fleet heartbeat'
```

Expected: all jobs are active, no-agent, local delivery, and have the correct workdir.

- [ ] **Step 4: Run one scheduled acceptance job per wrapper**

For each wrapper, create a temporary `2m --repeat 1` no-agent acceptance job, wait for one durable `completed` execution, verify artifact freshness, then remove the temporary job. Do not trigger a second run while one is active.

- [ ] **Step 5: Verify heartbeat alert transition and destination read-back**

Use a fixture/private temporary state that supplies one stale artifact. Run the local heartbeat wrapper once; confirm Matrix contains one finding. Run it again without changing state; confirm no duplicate finding. Restore clean fixture state; run once; confirm a single recovery. Remove all fixture state afterward.

## Task 8: Finalize finite label-canary audit only at expiry

**Files:**
- Create: `/Users/hermes/.hermes/scripts/steward_label_canary_audit.py`
- Verify: private label ledger/state and live GitHub labels

**Interfaces:**
- Consumes: `label-ledger.jsonl`, `label-state.json`, `scripts/steward-label-watchdog.py`, live GitHub GET reads, canary Cron runs.
- Produces: private JSON audit report and nonzero exit on any finding; no GitHub mutation.

- [ ] **Step 1: Do not modify the active canary while it has remaining attempts**

Run:
```bash
hermes cron list | grep -A15 -B1 'b8238d29c1ff'
```

Expected: retain the original 23-attempt finite bound and exact controller script.

- [ ] **Step 2: At `23/23`, verify execution terminal states**

Run:
```bash
hermes cron runs b8238d29c1ff --limit 30
```

Expected: every counted attempt is `completed`; any `unknown`, `running`, or failed execution aborts promotion/audit success.

- [ ] **Step 3: Write and run the read-only final audit**

The script validates unique ledger keys, exact allowlist labels, onboarded membership, live diff-size equality, and invokes the independent label watchdog. It atomically writes a private JSON report and exits nonzero on any discrepancy.

Run:
```bash
/opt/homebrew/bin/python3.11 /Users/hermes/.hermes/scripts/steward_label_canary_audit.py
```

Expected: report artifact and zero exit only if every action re-verifies.

- [ ] **Step 4: Disable the exhausted canary without promotion**

Run:
```bash
hermes cron pause b8238d29c1ff
```

Expected: persisted job reads inactive or exhausted; no indefinite label controller exists.

- [ ] **Step 5: Final no-write inventory**

Run:
```bash
hermes cron list | grep -i -A15 -B1 'steward.*label'
```

Expected: watchdog remains read-only; no active `steward-label-sync` or indefinite label-canary job exists.

## Task 9: Documentation, full verification, and commit

**Files:**
- Modify: `docs/reference/private-runtime-levels.md`
- Test: `tests/test_community.py`, `tests/test_trust.py`, `tests/test_heartbeat.py`, existing runtime suite

- [ ] **Step 1: Update public documentation**

Document the private read-only community queue, trust-map limitations/low-confidence nature, and heartbeat. State explicitly that none performs public writes; issue auto-close remains unimplemented and separately approved.

- [ ] **Step 2: Run all Python tests**

Run:
```bash
/opt/homebrew/bin/python3.11 -m unittest discover -s tests -v
/opt/homebrew/bin/python3.11 -m py_compile steward_runtime/*.py scripts/steward-*.py
```

Expected: all tests pass and all runtime scripts compile.

- [ ] **Step 3: Run static checks and inspect the exact diff**

Run:
```bash
git diff --check
git status --short
git diff -- docs/reference/private-runtime-levels.md steward_runtime scripts tests
```

Expected: no whitespace errors and no credentials/private artifacts in the diff.

- [ ] **Step 4: Commit repository changes**

```bash
git add steward_runtime scripts tests docs/reference/private-runtime-levels.md
git commit -m "feat: add private Steward operational groundwork"
```

Expected: one focused commit containing only runtime, tests, and public documentation. Do not create a pull request without loading and passing `steward-pr-gate`.
