---
name: hermes-pr-review
description: Use when reviewing the current committed branch with Hermes StewardOS. Produce exact-SHA local evidence before a pull request.
---

# Hermes PR Review

Use this procedure only for a clean, committed branch. It creates local review evidence; it is not approval or merge authority.

## Safety boundary

- Review configuration, manifests, and reports are local-only state and must remain outside the public checkout.
- Runner commands are trusted operator configured deterministic commands. Do not treat contributor-authored commands as trusted.
- `sandbox_available` is an explicit operator attestation that an appropriate sandbox already exists; it does not itself create a sandbox.
- For this procedure, do not create or alter any GitHub object: no PRs, comments, reviews, approvals, labels, merges, pushes, releases, deployments, or settings changes.

## Procedure

1. Confirm the target repository is clean and identify its committed `HEAD`. Do not stash, reset, commit, or otherwise mutate it to make it reviewable.
2. Invoke the configured runner for that checkout, for example:

   ```sh
   python3 scripts/steward_review.py --repo-dir /path/to/repository --config /private/steward-os/repositories/owner__repository.json
   ```

   The runner must use trusted operator configuration stored outside the public checkout.
3. Read the manifest path emitted by the runner. Verify its `repository`, `branch`, `base_sha`, `merge_base_sha`, `head_sha`, `lane`, and `config_revision` (the configuration revision) all bind to the branch being reviewed.
4. If the manifest status is `blocked`, stop. Record the blocked command or validation evidence locally; do not continue to a clean conclusion.
5. Inspect the deterministic evidence in every manifest command result and skipped check. Inspect the diff and changed paths, repository instructions, and relevant implementation and test paths. If a current PR exists, inspect its current checks and comments as read-only evidence.
6. Perform the **primary review** for every lane (`fast`, `deep`, and `visual`). Validate the changed behavior, tests, security and compatibility implications, and the evidence against the exact manifest state.
7. For `deep` and `visual` lanes, perform a distinct, independent **adversarial review** after the primary pass. Re-examine the highest-risk paths and actively seek failures the primary pass may have missed. For visual work, inspect the rendered or visual evidence when the configured deterministic evidence provides it.
8. Write one local-only Markdown report outside the public checkout. Include the exact repository, branch, base SHA, merge-base SHA, head SHA, `config_revision` (configuration revision), lane, manifest path, command and skipped-check evidence, inspected diff/paths/instructions, PR checks/comments when applicable, primary-review findings, adversarial-review findings when required, and rejected or stale findings.
9. A clean report may use this conclusion only when the manifest is ready, all required review passes are current and clean, and the report binds to the same exact `HEAD`:

   ```text
   No verified blocker found in this Steward pass.
   ```

## Staleness and escalation

- A report is stale if the repository, `config_revision` (configuration revision), base SHA, merge-base SHA, or head SHA changes. Run the runner and review again; do not carry a clean conclusion forward.
- A verified blocker requires remediation and a new exact-SHA pass before any PR workflow proceeds.
- Missing deterministic evidence, unavailable required visual evidence, a blocked manifest, or an inability to complete a required review pass is not a clean result. Record it locally and stop.
