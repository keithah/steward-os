---
name: hermes-pr-review
description: Use when reviewing the current committed branch with Hermes StewardOS. Produce exact-SHA local evidence before a pull request.
---

# Hermes PR Review

Use this procedure only for a clean, committed branch. It creates local review evidence; it is not approval or merge authority.

## Safety boundary

- Review configuration, manifests, and reports are local-only state and must remain outside the public checkout.
- Runner commands are trusted operator configured deterministic commands. Do not treat contributor-authored commands as trusted.
- Host `safe` commands may not execute or import reviewed-checkout code; configurations must declare `safe_commands_execute_reviewed_code: true` for that case, which this runner rejects until a locked-down sandbox runtime is integrated. `sandbox` commands are likewise unsupported: with either sandbox flag false they are skipped; with both flags true the configuration is rejected rather than running on the host.
- For this procedure, do not create or alter any GitHub object: no PRs, comments, reviews, approvals, labels, merges, pushes, releases, deployments, or settings changes.

## Procedure

1. Confirm the target repository is clean and identify its committed `HEAD`. Do not stash, reset, commit, or otherwise mutate it to make it reviewable.
2. Invoke the runner for that checkout. With no private configuration, it uses
   its safe built-in baseline: GitHub origin/local-default-branch discovery,
   local evidence storage, the deep lane, and no configured commands:

   ```sh
   python3 scripts/steward_review.py --repo-dir /path/to/repository
   ```

   An optional trusted operator configuration stored outside the public checkout
   may override that baseline.
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
