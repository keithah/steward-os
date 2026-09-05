# Steward operational progression design

**Status:** approved for design; implementation requires review of this document.

## Goal

Progress the active private StewardOS deployment safely after the current Level 2 operation and finite Level 3 size-label canary. Work proceeds in four independently verifiable phases:

1. Repair scheduler-health defects.
2. Let the existing label canary complete, then audit it before a separate promotion decision.
3. Add read-only operational groundwork: a private community intake queue, contributor-trust aggregation, and a fleet/artifact heartbeat.
4. Separately design and canary strict shipped-issue auto-close.

No phase authorizes merges, issue filing, public replies, release announcements, or any public write beyond the already-approved finite add-only label canary.

## Phase 1 — Scheduler health repair

### Current evidence

- Cron health watchdog job `783c7f82838e` executes successfully but has `delivery_failed: unknown platform 'webui'` because it uses `deliver=origin` and the originating `webui` target is unsupported by the gateway.
- Nightly update job `0a991d89578c` updates Hermes successfully, then attempts `hermes gateway restart`. During that restart a replacement gateway is already running, so the older updater process exits nonzero. Its failure is reported as a failed nightly update despite completing the actual package update.

### Design

- Change the health watchdog to an explicit supported Matrix delivery destination. It stays silent when healthy and sends only detected scheduler failures.
- Modify the nightly-update wrapper only after a red regression test or deterministic command-level reproduction. The wrapper must treat successful replacement-gateway takeover as successful recovery when the new gateway is live and healthy. It must continue to fail for an actual failed update, failed restart with no live replacement, dirty repository, merge conflict, failed tests, failed push, or failed WebUI health check.
- Add bounded post-update checks: one live default-profile gateway, current update revision, and scheduler heartbeat. No update job starts a second gateway if a healthy replacement already owns the profile.
- Preserve the updater lock and the no-secret logging policy.

### Acceptance

- `hermes cron doctor` reports no scheduler-health defects attributable to either job.
- A health-watchdog scheduled run has durable completion and delivery is read back at Matrix when it has a fixture-injected finding; a clean run is silent.
- The nightly wrapper’s focused regression suite proves the replacement-gateway race is handled while true restart failure remains nonzero.
- A controlled run completes with one live gateway and a healthy scheduler.

## Phase 2 — Label canary completion and audit

### Boundary

The existing job `b8238d29c1ff` remains the only scheduled Steward public-write controller. It has 23 total hourly attempts, each limited to one add-only `steward:size:*` label. The independent watchdog remains read-only and has a pause latch for this exact job.

### Design

- Do not change its cadence, limit, taxonomy, or scope during the window.
- At expiry, remove or leave inactive the finite canary job; it must not silently become indefinite.
- Produce a private audit from the label ledger and live GitHub reads. For every ledger action: confirm the allowed label exists, live additions plus deletions still equal the recorded size, repository was onboarded, label was add-only, and no duplicate ledger action exists.
- Any finding remains a stop condition. It is not repaired automatically.

### Acceptance

- All executions have a durable terminal state; unknown states are not counted as successes.
- Every successful action passes the independent final audit.
- The canary has expired and is no longer runnable without an explicit promotion decision.
- Promotion to an indefinite one-label/hour schedule is a separate approval, not an automatic consequence.

## Phase 3 — Read-only operational groundwork

All components in this phase are deterministic, private, read-only against external systems, and silent when clear. Their persisted artifacts live outside the checkout under the private runtime root.

### Community intake queue

- Initial source: owned GitHub issue and PR activity only; no chat, social, or web scraping in this phase.
- Collect stable source references and structured metadata into a private candidate queue. The collector does not infer from untrusted prose, post reactions, reply, file issues, label items, or change GitHub state.
- Deduplicate by immutable repository-and-item key; retain the latest structured timestamps and state.
- Render a bounded private digest for human review.

### Contributor-trust aggregation

- Define an append-only local outcome ledger format matching the documented `pr`, `author`, `outcome`, and UTC `at` fields.
- Add a read-only aggregator with configurable prior, shrinkage, and half-life. Last appended valid event wins per PR. Unknown or malformed events are surfaced, never scored.
- Publish a private trust map with `score`, `events_scored`, `effective_n`, and `last_event_at`.
- Do not populate past outcomes automatically and do not use trust scores to bypass quality gates. An empty ledger yields no contributor claims and no effect on the public scoreboard.

### Fleet and artifact heartbeat

- Check every enabled Steward job’s terminal execution freshness against its configured cadence, not merely process liveness.
- Check freshness and parseability of private scoreboard, community queue, trust map, and action-ledger artifacts when each respective feature is enabled.
- Check the scheduler heartbeat and a single owning gateway.
- Persist a compact private health snapshot. Send Matrix only on unhealthy state transitions; do not page repeatedly while the same fault remains active. Recovery may be reported once.
- The heartbeat does not restart processes, modify repositories, repair state, or change cron jobs.

### Acceptance

- Fixture tests cover dedupe, malformed state, no-op behavior, trust decay/shrinkage, last-write-wins corrections, stale artifacts, and stable alert transitions.
- Direct smoke tests and a gateway-owned scheduled acceptance run complete successfully.
- A deliberate fixture fault produces one Matrix alert and a clean follow-up suppresses duplicate noise.
- Read-back verifies the private artifacts; no GitHub mutation occurs.

## Phase 4 — Strict shipped-issue auto-close (separate design and approval)

This is intentionally not implemented under this document. It is an irreversible public write and requires its own design review, plan, tests, ledger, independent watchdog, manual lower-band evidence, and explicit canary authorization.

The future design must restrict candidates to open issues with exactly one linked merged PR whose merge commit is contained in a released tag; no unresolved task list, no multi-surface marker, no post-merge reporter activity, and no reopen. It must close with a fixed template only, ledger the action before cursor advancement, and leave every ambiguous case open for human review.

## Test and delivery discipline

- New behavior follows RED → GREEN → REFACTOR with focused tests first, then the full relevant suite.
- Scheduler changes use supported Hermes CLI operations, followed by persisted-definition read-back, direct smoke test, one scheduled acceptance run, and notification read-back when applicable.
- Private runtime paths use atomic writes, strict parsing, and fail-closed behavior. No credentials, raw community content, or personal data is committed.
- Public GitHub state is read back before it is reported as changed.

## Out of scope

- Unattended issue filing, auto-close, release announcement, comment/reply, merge, repository-setting changes, and label-taxonomy expansion.
- Chat/social/web monitoring and public capture reactions.
- Automatic trust-ledger classification or using trust as a gate bypass.
- Auto-repair, auto-restart, or auto-pause behavior beyond the already-approved finite label-canary pause latch.
