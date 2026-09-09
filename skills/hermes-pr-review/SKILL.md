---
name: hermes-pr-review
description: Use when reviewing the current committed branch with Hermes StewardOS. Produce exact-SHA local evidence before a pull request.
---

# Hermes PR Review

Use this procedure only for a clean, committed branch. It creates local review evidence; it is not approval or merge authority.

## Safety boundary

- A ready review requires `STEWARD_POLICY_ROOT`: an absolute, owner-owned, mode-`0700` (or stricter) directory outside the reviewed checkout. It contains the private `policy.json`; without it, the runner's built-in result is diagnostic and `blocked`.
- The runner derives repository identity from the reviewed checkout's supported GitHub `origin`. The private policy must not set a repository ID. Per-repository files are optional narrow overrides at `overrides/<owner>__<repository>.json` and may set only `base_ref` and lane path globs (`sensitive_paths`, `visual_paths`, `deep_paths`). They may not set paths, reviewers, execution flags, commands, or repository identity.
- The ready-policy reviewer identities are fixed: primary `anthropic` / `claude-opus-4-6`; adversarial `openai-codex` / `gpt-5.6-terra`. The policy has distinct owner-private report and manifest roots and no commands or reviewed-code execution.
- **Never use, accept, suggest, or pass `--config` or `--config-dir`.** A complete per-repository configuration is forbidden and the runner rejects those flags before manifest output or write.
- For this procedure, do not create or alter any GitHub object: no PRs, comments, reviews, approvals, labels, merges, pushes, releases, deployments, or settings changes.
- This public skill is read-only: do not execute, import, build, test, or otherwise run reviewed-checkout code. Do not write to the reviewed checkout.

## Review contracts

### Primary review checklist

Record evidence for each applicable item:

- full changed-file/caller/test inspection;
- public contract and compatibility paths;
- malformed/omitted/negative/timezone inputs; and
- error propagation.

### Adversarial review checklist

For `deep` and `visual` lanes, independently test these hypotheses against the inspected evidence:

- policy at effectful sinks;
- token/output/redirect boundaries;
- cancellation and partial-success compensation;
- all writers and shared locks;
- pagination snapshots and changed totals;
- ordering/deduplication;
- ambient credentials and caller-widenable authorization; and
- process cleanup and status propagation.

## Structured findings

Use stable, normalized `probe_id` values for findings. Each applicable probe records its identifier, status, exact local evidence reference, and concise impact. An empty findings list is allowed only after every applicable probe is recorded as passed/not-applicable with evidence in the local report.

## Procedure

1. Confirm the target repository is clean and identify its committed `HEAD`. Do not stash, reset, commit, or otherwise mutate it to make it reviewable.
2. Confirm `STEWARD_POLICY_ROOT` names the owner-private policy root, then invoke the runner without configuration flags:

   ```sh
   STEWARD_POLICY_ROOT=/private/steward-os/policy \
     python3 scripts/steward_review.py --repo-dir /path/to/repository
   ```

   Do not replace this global-policy flow with a full repository configuration.
3. Read the manifest path emitted by the runner. Verify its `repository`, `branch`, `base_sha`, `merge_base_sha`, `head_sha`, `lane`, and `config_revision` (the configuration revision) all bind to the branch being reviewed.
4. If the manifest status is `blocked`, stop. Record the blocked command or validation evidence locally; do not continue to a clean conclusion.
5. Inspect the deterministic evidence in every manifest command result and skipped check. Inspect the diff and changed paths, repository instructions, and relevant implementation and test paths. If a current PR exists, inspect its current checks and comments as read-only evidence.
6. Perform the **primary review** for the selected lane. Validate the changed behavior, tests, security and compatibility implications, and the evidence against the exact manifest state.
7. When the selected lane is `deep` or `visual`, perform a distinct, independent **adversarial review** after the primary pass. Re-examine the highest-risk paths and actively seek failures the primary pass may have missed. For visual work, inspect the rendered or visual evidence when the configured deterministic evidence provides it.
8. Write one local-only Markdown report outside the public checkout. Include the exact repository, branch, base SHA, merge-base SHA, head SHA, `config_revision` (configuration revision), lane, manifest path, command and skipped-check evidence, inspected diff/paths/instructions, PR checks/comments when applicable, primary-review findings, adversarial-review findings when required, and rejected or stale findings.
9. A clean report may use this conclusion only when the manifest is ready, all required review passes are current and clean, and the report binds to the same exact `HEAD`:

   ```text
   No verified blocker found in this Steward pass.
   ```

## Staleness and escalation

- A report is stale if the repository, `config_revision` (configuration revision), base SHA, merge-base SHA, or head SHA changes. Run the runner and review again; do not carry a clean conclusion forward.
- A verified blocker requires remediation and a new exact-SHA pass before any PR workflow proceeds.
- Missing deterministic evidence, unavailable required visual evidence, a blocked manifest, or an inability to complete a required review pass is not a clean result. Record it locally and stop.
