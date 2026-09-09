---
layout: default
parent: Reference
nav_order: 11
---

# Hermes PR review gate

The Hermes PR review gate produces local-only, exact-SHA review evidence before a pull-request workflow continues. It does not create, update, approve, label, merge, push, deploy, or otherwise change GitHub state. A clean report is not approval or merge authority.

## Install the public procedure

Keep this repository public-safe. Store live policy, manifests, reports, credentials, repository inventories, and host-specific paths outside the reviewed checkout and outside this repository. Without a policy, the built-in configuration deliberately produces a blocked diagnostic manifest under `~/.config/steward-os/runtime/`; it cannot establish project-specific quality evidence.

1. Make the runner available from a trusted checkout of this repository.
2. Create one owner-private directory outside every reviewed checkout. Copy [`setup/hermes-review-policy.example.json`](../../setup/hermes-review-policy.example.json) to `policy.json` there, replacing its path placeholders only in that private copy. The root must be owner-owned and mode `0700` (or stricter).
3. Set `STEWARD_POLICY_ROOT` to that private directory and run the collector against any clean supported-GitHub checkout. The runner derives `repository.id` from the checkout's `origin`, discovers the local default base ref, uses the policy's lane globs and exact dual-reviewer contract, and runs no reviewed-code commands.
4. Optionally add `overrides/owner__repository.json` under the same private root using [`setup/hermes-review-config.example.json`](../../setup/hermes-review-config.example.json). An override may contain only `base_ref` and lane path lists; it cannot replace repository identity, state roots, reviewers, execution flags, or commands. Complete per-repository configuration is forbidden.
5. Load [`skills/hermes-pr-review/SKILL.md`](../../skills/hermes-pr-review/SKILL.md) in Hermes for the review procedure.

Reviewer provider/model identifiers are private operator policy, not credentials: primary is ChatGPT OAuth `openai-codex`/`gpt-6-astra`. For deep and visual lanes, use the fail-closed ordered secondary candidates: Anthropic OAuth `anthropic`/`claude-opus-4-6`, then xAI OAuth `xai-oauth`/`grok-4.6`, then only free Zen `opencode-zen`/`muse-spark-1.3-contributor-free`. Paid models must never be routed through Zen. No credential value belongs in JSON.

The private `policy.json` has this complete shape (the public path strings are placeholders, not usable host paths):

```json
{
  "paths": {
    "report_root": "<absolute-private-report-root>",
    "manifest_root": "<absolute-private-manifest-root>"
  },
  "review": {
    "sensitive_paths": ["auth/**"],
    "visual_paths": ["web/**"],
    "deep_paths": ["src/**"],
    "reviewers": {
      "primary": {"provider": "openai-codex", "model": "gpt-6-astra"},
      "adversarial_candidates": [
        {"provider": "anthropic", "model": "claude-opus-4-6"},
        {"provider": "xai-oauth", "model": "grok-4.6"},
        {"provider": "opencode-zen", "model": "muse-spark-1.3-contributor-free"}
      ]
    },
    "execute_contributor_code": false,
    "sandbox_available": false,
    "command_timeout_seconds": 300,
    "safe_commands_execute_reviewed_code": false,
    "commands": []
  }
}
```

The global policy must set `commands` to `[]` and all reviewed-code execution flags to `false`; it has no integrated sandbox runtime. All state roots must be absolute, distinct, owner-owned, and outside the reviewed checkout. Every state-tree component created beneath a private root is `0700`; existing components must already be owner-private and are rejected without mode changes. The runner accepts no environment interpolation or secret values. The `config_revision` field is the SHA-256 of the canonical resolved configuration.

## Run the evidence collector

From the public runner checkout, invoke the runner with a clean target repository and owner-private policy:

```sh
STEWARD_POLICY_ROOT=/private/steward-os/policy \
  python3 scripts/steward_review.py --repo-dir /path/to/repository
```

With `STEWARD_POLICY_ROOT` set, no per-repository configuration is required: the runner loads `<policy-root>/policy.json`, derives the identity from origin, and optionally reads only `<policy-root>/overrides/owner__repository.json`. It refuses a nonprivate policy root, policy contents containing a repository ID, an invalid override, state roots inside the checkout, or any command/reviewed-code execution policy. `--config` and `--config-dir` are rejected before any manifest output or write. Without a policy it retains the blocked built-in diagnostic baseline. It writes a manifest only after valid Git/policy state is resolved.

The manifest is local-only JSON at:

```text
<manifest_root>/<owner>__<repo>/branch-<sanitized-branch>/<head_sha>.json
```

It records the exact repository, branch, base ref, base SHA, merge-base SHA, head SHA, `config_revision`, selected lane, changed paths, command results, skipped checks, and status. Command output is bounded and records whether it was truncated. For a clean detached-HEAD checkout, the runner records `branch` as exactly `""`; a ready manifest binds reviewer prompts and private artifact paths to its validated `head_sha` instead. Nonempty branch values remain the branch binding, and whitespace-only branches remain invalid.

## Lane behavior and Hermes review

- **fast:** no changed path matches configured deep, sensitive, or visual patterns. Hermes still performs the primary review.
- **deep:** a changed path matches a deep or sensitive pattern. Hermes performs the primary review plus a separate adversarial review.
- **visual:** a changed path matches a visual pattern. Hermes performs the primary review plus a separate adversarial review, including visual evidence where available.

The manifest producer selects the required lane contract from the configured OAuth-first policy: a fast manifest binds exactly the configured Astra primary role and publishes exactly one primary exact-SHA artifact. Every deep or visual manifest binds that primary plus the ordered secondary candidate list and publishes two exact-SHA artifacts bound to the same repository, configuration revision, base SHA, merge-base SHA, and head SHA. The launcher tries candidates in order and accepts only the first valid adversarial artifact; its provider and model must exactly match the candidate actually selected. Missing, extra, malformed, reordered, or substituted contracts are rejected. The Zen candidate is exclusively the free Muse Spark 1.3 fallback; paid models never route through Zen.

Hermes reads the manifest before reviewing. A `blocked` manifest stops the procedure. Before generating a committed diff, before launching either reviewer, between reviewer passes, and immediately before publication, it revalidates the checkout's normalized supported-GitHub `origin` repository plus its `HEAD`, configured base ref, merge base, and branch/detached binding against the manifest; any mismatch fails closed without further reviewer launch or artifact write. Each reviewer keeps private-only prompt, transcript, and artifact state outside the public checkout and GitHub. Review agents have no GitHub writes or reviewed-code execution; any future sandbox is the only boundary for executing reviewed code. The isolated reviewer receives only a bounded host-generated committed diff, manifest bindings, and its role-specific private checklist. The host excludes committed repository instruction/rule files named `AGENTS.md`, `SOUL.md`, `.cursorrules`, `.hermes.md`, and `CLAUDE.md` at the repository root and at every nested depth before sending that diff. Deterministic runner evidence remains in the manifest for the host/operator; the reviewer does not independently read repository instructions, checkout paths, or live PR checks/comments. The artifact requires structured probe outcomes with stable IDs: every role-specific checklist ID appears exactly once, findings contain only a declared `probe_id` and that probe's outcome is `finding`, and limitations are normalized bounded strings. Reviewer calls have a fixed 300-second (five-minute) bounded timeout; a timeout fails closed without retaining model stdout or private prompt content. Exact-SHA artifacts stage under one private `0700` transaction directory with owner-private `0600` files, then become consumer-visible only through one same-parent atomic directory rename. A pre-existing exact-SHA artifact directory is rejected; failed staging or publication is removed without exposing a partial lane artifact set.

The reviewer runs from an external private working directory with `--safe-mode --toolsets context_engine --max-turns 1 --query-file <private-prompt-path>`. `context_engine` has no static tools, and safe mode disables any plugin-provided tools, preserving a zero-tool capability surface without the comma-selector warning. The retired `--toolsets ,` selector previously resolved to an empty toolset list but emitted an unrelated selector warning; it is not used by reviewers. The private query file contains the manifest-bound diff prompt, and the host verifies the structured artifact before acceptance. The host discards only the exact local `  ⚠ tirith security scanner enabled but not available — command scanning will use pattern matching only` diagnostic line when it is the initial stdout line; it then requires exactly one JSON object, rejecting every other prefix, suffix, malformed response, or multiple object response. This enforced boundary means `isolation remediation is pending` is not a current hold. Hermes writes its review artifact outside the public checkout, under the configured private report root, binding it to the same exact state as the manifest.

The private benchmark scorer receives only structured finding `probe_id` values. Its scorecard reports probe/category coverage rather than case recall: `matched_probe_ids` identifies mapped canonical probes and `candidate_case_ids_by_probe` lists corpus cases sharing each probe, while `matched_case_ids` and `missed_case_ids` remain empty because an unbound probe cannot establish a distinct historical case match. Its output must be an absolute path outside the checkout: every newly created output directory is owner-private (`0700`), existing output directories must already be owner-owned and private, and the scorer atomically writes the JSON scorecard as `0600`. It rejects non-private directories and symlink or other non-regular destination targets without changing their modes or replacing them. At scorer input only, its bounded explicit mapping expands documented role probes to canonical corpus probes: primary public-contract/compatibility, malformed/omitted/negative/timezone inputs, and error propagation; and adversarial policy/effectful sinks, token/output/redirect boundaries, cancellation/partial-success compensation, writers/shared locks, pagination snapshots/changed totals, ordering/deduplication, ambient credentials/caller authorization, and process cleanup/status propagation. The reviewer artifact schema and prompts do not expose corpus contents. Unmapped finding IDs remain unexpected findings; the scorer does not perform fuzzy matching.

A clean conclusion is permitted only for a ready, complete, current review:

```text
No verified blocker found in this Steward pass.
```

## `steward` and `run steward`

`steward` is the terminal entrypoint. It runs the local runner first, using the owner-private global policy when `STEWARD_POLICY_ROOT` is configured and the blocked built-in baseline otherwise; only a ready manifest may be passed to Hermes for the public `hermes-pr-review` procedure. The launcher also requires `STEWARD_POLICY_ROOT` and accepts only the deterministic manifest location derived from that active policy, checkout origin, branch, and exact HEAD. It must not use GitHub write operations.

`run steward` is the chat invocation of the same gate on the current committed branch. Hermes runs the local runner, reads the resulting manifest, and follows the public procedure. It is read-only with respect to GitHub objects and writes only its local report outside the public checkout.

## Exact-SHA freshness

A report covers only the repository, configuration revision, base SHA, merge-base SHA, and head SHA recorded in it. Any change to any of those values makes the report stale. Rerun the runner and complete the required review passes before relying on it. If a verified blocker is found, remediate it, commit the fix, and obtain a new report for the new exact head SHA.
