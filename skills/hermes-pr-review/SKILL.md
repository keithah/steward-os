---
name: hermes-pr-review
description: Use when reviewing the current committed branch with Hermes StewardOS. Produce exact-SHA local evidence before a pull request.
---

# Hermes PR Review

Use this procedure only for a clean, committed branch. It creates local review evidence; it is not approval or merge authority.

## Safety boundary

- No `STEWARD_POLICY_ROOT` is required. The ready built-in baseline derives repository identity and base ref from the reviewed checkout, binds the fixed reviewer contract, uses no commands or reviewed-code execution, and writes state beneath its private runtime root.
- `STEWARD_POLICY_ROOT`, when explicitly supplied, is an optional absolute, owner-owned, mode-`0700` (or stricter) owner-private override outside the reviewed checkout. Its `policy.json` may set private report/manifest roots, base ref, and lane globs; it must not set repository identity, arbitrary reviewers, execution flags, or commands. Per-repository files at `overrides/<owner>__<repository>.json` remain narrower: only `base_ref` and lane path globs (`sensitive_paths`, `visual_paths`, `deep_paths`). An invalid supplied policy is a hard failure, not a fallback.
- The reviewer identities are fixed in both modes: primary ChatGPT OAuth `openai-codex` / `gpt-6-astra`; for `deep` and `visual`, the fail-closed ordered secondary candidates are Anthropic OAuth `anthropic` / `claude-opus-4-6`, then xAI OAuth `xai-oauth` / `grok-4.6`, then only free Zen `opencode-zen` / `muse-spark-1.3-contributor-free`. Zen must never route paid models. The manifest binds the primary plus this exact candidate order; the adversarial artifact binds to the candidate actually selected. Fast remains primary-only.
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
2. Invoke the runner without configuration flags. Set `STEWARD_POLICY_ROOT` only when intentionally using an owner-private override:

   ```sh
   python3 scripts/steward_review.py --repo-dir /path/to/repository
   ```

   Do not replace this built-in-or-optional-policy flow with a full repository configuration.
3. Read the manifest path emitted by the runner. Verify its `repository`, `branch`, `base_sha`, `merge_base_sha`, `head_sha`, `lane`, and `config_revision` (the configuration revision) all bind to the branch being reviewed.
4. If the manifest status is `blocked`, stop. Record the blocked command or validation evidence locally; do not continue to a clean conclusion.
5. Invoke the exact-SHA launcher with the emitted manifest; it must succeed before any clean conclusion:

   ```sh
   python3 scripts/steward_llm_review.py --repo-dir /path/to/repository \
     --manifest <emitted manifest>
   ```

   Validate the launcher output paths and artifacts against the same manifest bindings. Every lane requires a current, valid exact-SHA primary artifact. `deep` and `visual` additionally require a current, valid exact-SHA adversarial artifact; `fast` permits only the primary artifact. Do not substitute manual review notes, a Markdown report, or runner evidence for launcher artifacts.
6. Inspect the deterministic evidence in every manifest command result and skipped check. Inspect the diff and changed paths, repository instructions, and relevant implementation and test paths. If a current PR exists, inspect its current checks and comments as read-only evidence.
7. Perform the **primary review** for the selected lane. Validate the changed behavior, tests, security and compatibility implications, and the evidence against the exact manifest state.
8. When the selected lane is `deep` or `visual`, perform a distinct, independent **adversarial review** after the primary pass. Re-examine the highest-risk paths and actively seek failures the primary pass may have missed. For visual work, inspect the rendered or visual evidence when the configured deterministic evidence provides it.
9. Write one local-only Markdown report outside the public checkout. Include the exact repository, branch, base SHA, merge-base SHA, head SHA, `config_revision` (configuration revision), lane, manifest path, command and skipped-check evidence, launcher artifact paths, inspected diff/paths/instructions, PR checks/comments when applicable, primary-review findings, adversarial-review findings when required, and rejected or stale findings. The manual Markdown is supplemental to the launcher artifacts.
10. A clean report may use this conclusion only when the manifest is ready, the required launcher artifacts for its lane are current and clean, all required review passes are current and clean, and the report binds to the same exact `HEAD`:

   ```text
   No verified blocker found in this Steward pass.
   ```

## Staleness and escalation

- A report is stale if the repository, `config_revision` (configuration revision), base SHA, merge-base SHA, or head SHA changes. Run the runner and review again; do not carry a clean conclusion forward.
- A verified blocker requires remediation and a new exact-SHA pass before any PR workflow proceeds.
- Missing deterministic evidence, unavailable required visual evidence, a blocked manifest, or an inability to complete a required review pass is not a clean result. Record it locally and stop.
