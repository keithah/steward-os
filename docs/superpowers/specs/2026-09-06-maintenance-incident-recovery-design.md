# Maintenance incident recovery design

**Status:** approved for design; implementation requires review of this document.

## Goal

Restore trustworthy unattended operations after the 2026-09-05 incident sequence without hiding failures:

1. make consolidated maintenance stage-aware and auditable;
2. recover the WebUI integration branch through a clean, test-gated path;
3. reconcile the Steward label ledger discrepancy for `keithah/gumroad-mcp#9` without rewriting audit history; and
4. close historical scheduler incidents only after fresh, relevant success evidence.

This is an operational-recovery design. It does not authorize public GitHub writes, WebUI deployment from a failing branch, or resumption of the paused Level 3 label canary.

## Current facts

- Hermes runtime is current at `245e4800`; its supervised gateway and the deployed WebUI health endpoint are live.
- The consolidated job successfully completed Hermes, skill, Printing Press, and MCP stages, but the WebUI test gate failed with 43 failures. The update job is therefore correctly blocked from push/restart.
- The deployed WebUI branch is `local/all-keith-prs`; it is intentionally local-only and tracks `origin/master`. It is 102 commits ahead of that base and contains uncommitted changes. It must be treated as evidence, not as a safe deployment candidate.
- The Steward watchdog observed a live-versus-ledger size mismatch for merged `keithah/gumroad-mcp#9`: live GitHub totals are `+5420/-2369`. The finite Level 3 label canary is paused.
- Package tracking and the Steward watchdog have since produced successful direct executions, but their prior cron incidents remain open. Existing incidents must not be interpreted as evidence of a current active fault.

## Architecture

### 1. Stage-aware maintenance controller

The local maintenance wrapper remains deterministic and fail-closed, but it becomes a controller for four explicit stages:

| Stage | Scope | Success evidence | Failure effect |
|---|---|---|---|
| `runtime` | Hermes update, gateway/scheduler recovery, skills | current revision; one supervised gateway; ticker heartbeat | stop dependent stages |
| `catalog` | last30days diagnostic, Printing Press update, MCP inventory | each command exit status and bounded report | stop dependent stages |
| `webui_prepare` | clean-tree/branch validation, base synchronization, upstream merge, test gate | exact commit SHAs and `./scripts/test.sh` exit 0 | do not push/restart WebUI |
| `webui_deploy` | push tested head, restart intended service, health check | remote ref read-back plus `/health` | report deployment failure |

Each stage writes a private structured report with start/finish time, exact revision(s), outcome, bounded sanitized detail, and a stable failure fingerprint. The overall job exits nonzero when any required stage fails, but the report distinguishes completed stages from the blocking stage. A later stage never runs after an unmet predecessor.

A successful runtime or catalog stage is not invalidated by an unrelated WebUI test failure. Conversely, a successful WebUI health endpoint from the already deployed version does not validate an untested candidate branch.

### 2. Incident lifecycle and notifications

Cron incidents become stateful operational records, not permanent alarms.

- A failure opens or refreshes an incident keyed by `(job_id, stage, fingerprint)`.
- The watchdog alerts only on an incident transition to open or on a changed fingerprint; it does not repeat an unchanged historical failure.
- A fresh verified success for the same job/stage closes the matching incident and records the success execution ID and timestamp.
- A job-wide success may close job-wide runtime incidents only; a failed `webui_prepare` incident remains open until the WebUI test gate succeeds.
- Legacy incidents without stage data are retained for audit and explicitly migrated as `historical`; they must not page when the job has subsequently succeeded.

The existing cron health watchdog remains a detector. It must consume the controller’s structured stage outcome rather than infer current health solely from a stale `last_status` field.

### 3. WebUI integration recovery

The current `local/all-keith-prs` worktree is preserved read-only as incident evidence. Recovery occurs in a new isolated worktree from a known-good base; no automated maintenance job modifies it.

1. Record the current local head, working-tree diff, configured tracking reference, and all 43 failing test names as the baseline artifact.
2. Create a recovery branch from the exact intended base (`origin/master`, after fetching). Do not import uncommitted changes automatically.
3. Build a candidate change inventory from the 102 commits on the preserved branch, grouped by subsystem and dependency. For each candidate, decide: retain, replace with upstream equivalent, or discard. Decisions are recorded in a private reconciliation manifest.
4. Replay selected changes in dependency order. After every group, run focused tests. Any behavior change is repaired through a failing regression first.
5. Run the full repository gate (`./scripts/test.sh`) only on a clean candidate. Resolve every confirmed failure; do not delete or weaken tests merely because they fail against the aggregate branch.
6. Before deployment, require: clean worktree, exact commit identity, all tests passing, a review of the candidate diff against the manifest, a successful push read-back, service restart, and `/health` read-back.

The 43 failures are initially classified before code changes into:

- missing behavior expected by retained change sets;
- stale tests whose prerequisite change was intentionally excluded or superseded upstream;
- source/test contract drift due to the upgraded Hermes runtime; or
- test isolation/environment defects.

A classification is a hypothesis, not a deletion authorization. A test can be changed only when a retained product contract proves its expectation obsolete and the replacement observable behavior is covered.

### 4. Steward ledger reconciliation

The label ledger is append-only evidence. The original record for `keithah/gumroad-mcp#9` is never edited or deleted.

A read-only reconciliation command compares the ledger event with live GitHub PR metadata and available local/GitHub history. It writes a new private reconciliation event containing:

- immutable ledger-event identity and original recorded totals;
- current live head/base SHA, merged state, additions, deletions, labels, and fetch timestamp;
- a deterministic mismatch classification; and
- an operator disposition (`confirmed-source-drift`, `recording-defect`, or `unresolved`) supplied only after review.

The watchdog treats an unresolved reconciliation as a real RED finding. It may treat a reviewed, append-only `confirmed-source-drift` event as reconciled only when the live PR identity still matches the recorded immutable identity. It must reopen the finding if the live identity changes or if the reconciliation record is malformed.

The canary remains paused through reconciliation and one clean watchdog run. It is not resumed automatically. Resumption requires a separate explicit user decision after the audit report is reviewed.

### 5. Boundaries and safety

- No automatic GitHub labels, comments, issue closes, merges, pushes, or configuration changes are introduced by incident reconciliation.
- No secret, raw email, raw GitHub response body, or user content is placed in reports or committed files.
- All private reports are atomically written outside public checkouts with restrictive permissions.
- The maintenance script may update managed software only in its scheduled or explicitly requested durable run; short probes validate syntax, command contracts, locks, and report parsing without invoking full updates or restarts.
- The currently paused update job stays paused until the stage-report/controller migration has focused verification. It then receives one durable acceptance run; no competing manual runs are started.

## Testing and verification

### Maintenance controller

- Unit/shell-harness tests cover: runtime success with stale/manual-gateway nonzero updater output; genuine update failure; catalog failure; WebUI branch tracking validation; WebUI test failure; failed push; failed health check; lock contention; and report redaction/bounding.
- A controller test proves a `webui_prepare` failure preserves `runtime` and `catalog` as successful in the report while returning an overall nonzero result.
- Cron integration verifies persisted job definition, one completed scheduled execution, and structured report read-back. Notification behavior is tested with a non-production fixture job and explicit destination read-back.

### WebUI recovery

- Preserve an exact failure-baseline artifact before creating the recovery worktree.
- Run focused tests after each retained candidate group and the complete `./scripts/test.sh` gate on the final clean candidate.
- Verify remote branch SHA after push, supervisor state after restart, and `/health` after deployment.

### Steward reconciliation

- Fixture tests cover no mismatch, reviewed source drift, malformed reconciliation records, changed live identity after reconciliation, and unresolved mismatch.
- A direct live watchdog execution remains nonzero before reconciliation and returns zero only after the reviewed record satisfies exact identity checks.
- Confirm the canary job remains disabled after watchdog success; a green watchdog is not a canary-resume action.

### Incident closure

- Fixture tests prove unchanged historical failures are silent, a changed failure fingerprint alerts once, matching stage success closes only that incident, and unrelated stage success cannot close a WebUI-test incident.
- Read back each closed incident record and its linked execution ID before reporting recovery.

## Delivery order

1. Add controller report schema, pure incident-lifecycle logic, and their tests.
2. Migrate and verify the paused maintenance job without running a full update.
3. Capture the WebUI evidence baseline and construct its recovery manifest/worktree.
4. Add Steward reconciliation model/fixtures and produce the reviewed private reconciliation report.
5. Repair the WebUI candidate in isolated, test-gated increments.
6. Run one durable maintenance acceptance after all WebUI checks are green.
7. Close only the verified incidents, retain the canary disabled, and publish a private final recovery report.

## Out of scope

- Repairing or deploying the WebUI branch before it passes its complete test gate.
- Automatically choosing the Steward reconciliation disposition or resuming the label canary.
- Clearing cron incident history without corresponding success evidence.
- Refactoring unrelated Hermes/WebUI/Steward subsystems discovered during recovery.
