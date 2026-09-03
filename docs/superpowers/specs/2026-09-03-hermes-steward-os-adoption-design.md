# Hermes StewardOS Adoption Design

## Purpose

Adopt the MIT-licensed `nesquena/steward-os` operating model as the public `keithah/steward-os` fork while preserving Keith's existing local-only, exact-SHA PR review gate. The result is a Hermes-native PR review and quality-gate system that runs before every PR creation and on demand through either chat (`run steward`) or the `steward` command.

This first implementation phase covers only pull-request review and quality gates. Issue intake, release automation, community monitoring, contributor recognition, and any autonomous public write remain disabled until separately designed and configured.

## Repository and state boundaries

- `keithah/steward-os` is a public GitHub fork of `nesquena/steward-os`; `upstream` remains the original repository.
- The former private `keithah/steward` repository is archived, not deleted. It retains historic reports and the original shadow-gate implementation.
- A permission-restricted local backup of the old ignored `.state/` and `reports/` data is retained outside public repositories.
- The active local checkout becomes `/Users/hermes/steward-os`.
- Runtime state, reports, credentials, repository inventories, local hostnames, and any project-specific configuration stay outside the public fork under a permission-restricted local root. They are never committed, printed in public PRs, or copied into test fixtures.
- The public repository contains generic documentation, schemas, runner code, test fixtures without sensitive values, and integration instructions only.

## Roles and authority

Adopt StewardOS's Watcher, Reviewer, Builder, and Steward roles, with these initial constraints:

- The **Reviewer** reads diffs, GitHub metadata, existing comments, current CI, repository instructions, and safe local checks; it produces evidence only.
- The **Builder** may prepare a branch or fix verified defects only in a user-authorized development workflow. It never self-approves or self-merges.
- The **Steward** records and ranks local review work but does not make public GitHub writes in phase one.
- The **Watcher** is limited to the existing read-only scheduled PR candidate collector until a later lifecycle phase.
- No PR comment, approval, change request, label, close, merge, release, deployment, or repository-setting mutation is automatic. Keith authorizes every public write.

## Local per-repository configuration

Each managed repository has a local configuration file outside the public fork. Configuration has an explicit, validated schema and must include:

- canonical repository owner/name and local checkout path;
- base branch and policy for resolving the review base;
- scope anchors, philosophy vetoes, and sensitive path patterns;
- exact full-suite, focused-test, lint, formatting, type/build, and visual-check commands; each command is classified as safe local execution, sandbox-required, or disabled;
- declared automated and adversarial reviewer passes, including the required evidence artifact for each;
- whether contributor-authored code may run and the sandbox requirement;
- report root and local state root, both outside the public repository;
- autonomy settings fixed to local evidence only for phase one.

The configuration rejects unknown repositories, missing required commands, unsafe report/state paths, and execution of contributor code without an available sandbox. Secrets are referenced only through the existing Hermes secret mechanism and never stored in configuration.

## Invocation contract

The same `review current committed branch` operation is available through two entry points:

1. **Chat:** Keith says `run steward`. Hermes loads the public-fork review skill and reviews the active workspace's current committed branch.
2. **Terminal:** Running `steward` from a Git working tree invokes Hermes with the same skill and local configuration.

Both entry points require a clean working tree. They determine repository identity, branch, exact HEAD, base ref, and merge base before reviewing. They must not stash, commit, push, alter GitHub, deploy, change settings, or access production credentials. A request on a dirty tree fails with instructions to commit the intended target or explicitly request a separately designed working-tree review.

A branch report is written only under the configured local report root at:

`reports/<owner>__<repo>/branch-<sanitized-branch>/<head_sha>.md`

The report contains repository identity, branch, base and merge-base SHAs, reviewed HEAD SHA, configuration revision, evidence read, commands and full bounded outputs, reviewer pass identifiers, verified blockers, non-blocking risks, rejected or stale prior findings, skipped checks, and an explicit statement that it is not approval or merge authority.

## Mandatory pre-PR gate

Before Hermes creates any PR, including documentation and configuration-only PRs, it runs the same branch review against the exact proposed committed HEAD.

1. Resolve and record the proposed PR base/head SHA.
2. Run deterministic configured gates first.
3. Run the primary Reviewer pass and an independent adversarial review pass for the required lane.
4. Write the local exact-SHA report.
5. If a verified blocker is found, do not create the PR. Repair it, commit the repair, then rerun the full review against the new SHA.
6. If no verified blocker is found, the PR workflow may proceed and must include the report path and reviewed SHA in the private handoff record.

Any change to HEAD, base, configuration revision, or required-gate result invalidates prior evidence and requires a fresh run. A clean report is permission to continue the workflow, never an approval or merge authorization.

## PR lanes and quality gates

A PR is first classified into a lane using the configured scope/benefit screen and sensitive paths:

- **Fast lane:** small, narrow, non-sensitive change. Requires deterministic checks, primary review, and local report.
- **Deep lane:** broad, risky, behavior-changing, or sensitive change. Requires deterministic checks, primary review, independent adversarial review, and local report.
- **Visual lane:** any user-visible change. Adds declared screenshot/interaction verification at configured desktop and mobile viewports.
- **Hold:** draft, uncertain fit, unresolved scope question, missing safe execution environment, or missing required configuration. No recommendation to create or merge a PR.

The full suite is the primary defense where it can be run safely. A focused check never substitutes for a configured full suite. Flakes are reported as defects with signatures, not silently retried away. Untrusted contributor code is never executed outside a locked-down no-network, no-credential sandbox; if no sandbox is available, execution-dependent gates are marked skipped and the review cannot claim that execution passed.

## Implementation units

1. Preserve the upstream public documentation and contract tests; add Hermes adaptation documentation without embedding local values.
2. Define a generic local configuration schema, validator, and sanitized fixtures.
3. Add a deterministic runner that resolves Git state, reads configuration, selects a lane, runs allowed commands in fixed order, and emits a machine-readable review manifest.
4. Add a Hermes review skill that consumes the manifest, performs the primary evidence review, and writes a local Markdown report.
5. Add a separately invoked adversarial review skill/pass, with its own prompt and evidence identifier, for deep and visual lanes.
6. Replace the existing `steward` launcher and scheduled candidate gate to target `/Users/hermes/steward-os` only after a dry run proves report/state continuity.
7. Add the mandatory pre-PR hook in Hermes project instructions and PR workflow tooling so the exact current head cannot bypass the gate.
8. Run end-to-end dry runs on generic fixtures and on `keithah/steward-os` before adding any other managed repository.

## Tests and acceptance evidence

The public fork must add tests proving:

- invalid or unsafe configuration fails closed;
- repository/base/head resolution is exact and a dirty worktree is rejected before running Hermes;
- commands run only in declared order and only when their execution classification permits it;
- fast, deep, visual, sensitive, and hold lanes select the required gates;
- both review passes produce distinct manifest identifiers where required;
- report paths are outside the repository and contain the exact head/base/config revision;
- no command path invokes GitHub writes;
- a changed HEAD or configuration revision invalidates a prior report;
- the pre-PR integration refuses to create a PR without a clean current-head report;
- local state migration preserves report and seen-head counts before the scheduled job is repointed.

Each implementation change follows test-first development. The full upstream test/lint contract and the adaptation-specific tests must pass in a Ruby version supported by upstream. On this host, the upstream baseline currently fails under macOS Ruby 2.6 because it calls `Array#filter_map`, which requires Ruby 2.7 or newer; the implementation environment must use a supported Ruby rather than changing upstream semantics to accommodate the system Ruby.

## Explicitly deferred

- Automated GitHub comments, approvals, labels, merges, releases, or deployments.
- Issue capture/triage, chat/community monitoring, contributor ledger, and release lifecycle automation.
- Running untrusted contributor code without a sandbox.
- Publishing reports, state, or project-specific local configuration.
- Replacing human final authority at any irreversible public boundary.
