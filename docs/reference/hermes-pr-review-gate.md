---
layout: default
parent: Reference
nav_order: 11
---

# Hermes PR review gate

The Hermes PR review gate produces local-only, exact-SHA review evidence before a pull-request workflow continues. It does not create, update, approve, label, merge, push, deploy, or otherwise change GitHub state. A clean report is not approval or merge authority.

## Install the public procedure

Keep this repository public-safe. Store live configuration, manifests, reports, credentials, repository inventories, and host-specific paths outside the reviewed checkout and outside this repository. Configuration is optional only for **local diagnostic evidence**: a built-in configuration deliberately produces a blocked manifest, because it cannot establish project-specific quality evidence. Its default evidence root is `~/.config/steward-os/runtime/`.

1. Make the runner available from a trusted checkout of this repository.
2. Run it against a clean GitHub checkout. With no private configuration it discovers the local default branch, records evidence under `~/.config/steward-os/runtime/`, uses the deep lane, and runs no commands from the checkout.
3. Optionally copy [`setup/hermes-review-config.example.json`](../../setup/hermes-review-config.example.json) to a private configuration directory when you need custom state roots, lane patterns, or trusted deterministic checks. Do not put secrets, tokens, hostnames, or live local paths in public files.
4. Load [`skills/hermes-pr-review/SKILL.md`](../../skills/hermes-pr-review/SKILL.md) in Hermes for the review procedure.

Reviewer provider/model identifiers are private operator policy, not credentials: the selected identities are primary `anthropic`/`claude-opus-4-6` and adversarial `xai-oauth`/`grok-4.6`. No OAuth value belongs in JSON.

A sanitized configuration has this complete shape:

```json
{
  "repository": {
    "id": "owner/repository",
    "base_ref": "main"
  },
  "paths": {
    "report_root": "/private/steward-os/reports",
    "manifest_root": "/private/steward-os/manifests"
  },
  "review": {
    "sensitive_paths": ["auth/**"],
    "visual_paths": ["web/**"],
    "deep_paths": ["src/**"],
    "reviewers": {
      "primary": {"provider": "anthropic", "model": "claude-opus-4-6"},
      "adversarial": {"provider": "xai-oauth", "model": "grok-4.6"}
    },
    "execute_contributor_code": false,
    "sandbox_available": false,
    "command_timeout_seconds": 300,
    "safe_commands_execute_reviewed_code": false,
    "commands": [
      {
        "id": "test",
        "command": "python3 -m unittest",
        "execution": "disabled"
      }
    ]
  }
}
```

The runner accepts only `safe`, `sandbox`, and `disabled` command execution modes. `safe` commands are trusted operator-configured deterministic commands and run on the host with `command_timeout_seconds`, an integer from 1 through 3600. Set `safe_commands_execute_reviewed_code` to `true` when a safe command would import, execute, or otherwise run files from the reviewed checkout; this runner rejects that configuration until a locked-down sandbox runtime is integrated. A contributor does not gain host execution by changing the repository. This first public runner has no integrated sandbox runtime: a `sandbox` command remains skipped while either sandbox flag is false, and configuration is rejected if both `execute_contributor_code` and `sandbox_available` are true for a sandbox command.

All state roots must be absolute, distinct, and outside the reviewed checkout. The runner accepts no environment interpolation or secret values. The `config_revision` field is the SHA-256 of the canonical JSON configuration.

## Run the evidence collector

From the public runner checkout, invoke the runner with a clean target repository:

```sh
python3 scripts/steward_review.py --repo-dir /path/to/repository
```

To override the baseline, add `--config /private/steward-os/repositories/owner__repository.json` or `--config-dir /private/steward-os/repositories`. The runner refuses a dirty checkout, invalid optional configuration, a mismatched origin/config identity, state roots inside the checkout, a post-command Git-state change, failed eligible commands, **or any skipped/missing project quality evidence**. Each eligible host command is bounded by `command_timeout_seconds`; a timeout is recorded as failed and produces a `blocked` manifest. It writes a manifest only after valid Git/configuration state is resolved.

The manifest is local-only JSON at:

```text
<manifest_root>/<owner>__<repo>/branch-<sanitized-branch>/<head_sha>.json
```

It records the exact repository, branch, base ref, base SHA, merge-base SHA, head SHA, `config_revision`, selected lane, changed paths, command results, skipped checks, and status. Command output is bounded and records whether it was truncated.

## Lane behavior and Hermes review

- **fast:** no changed path matches configured deep, sensitive, or visual patterns. Hermes still performs the primary review.
- **deep:** a changed path matches a deep or sensitive pattern. Hermes performs the primary review plus a separate adversarial review.
- **visual:** a changed path matches a visual pattern. Hermes performs the primary review plus a separate adversarial review, including visual evidence where available.

Every deep or visual run requires the configured primary and adversarial roles and two exact-SHA artifacts bound to the same repository, configuration revision, base SHA, merge-base SHA, and head SHA. The artifact provider and model must exactly match the configured identity; there is no fallback acceptance.

Hermes reads the manifest before reviewing. A `blocked` manifest stops the procedure. Each reviewer keeps private-only prompt, transcript, and artifact state outside the public checkout and GitHub. Review agents have no GitHub writes or reviewed-code execution; any future sandbox is the only boundary for executing reviewed code. The isolated reviewer receives only a bounded host-generated committed diff, manifest bindings, and its role-specific private checklist. Deterministic runner evidence remains in the manifest for the host/operator; the reviewer does not independently read repository instructions, checkout paths, or live PR checks/comments. The artifact requires structured probe outcomes with stable IDs, and a clean artifact records exactly one outcome for every role-specific checklist ID.

The reviewer runs from an external private working directory with `--safe-mode --toolsets context_engine --max-turns 1 --query-file <private-prompt-path>`. `context_engine` has no static tools, and safe mode disables any plugin-provided tools, preserving a zero-tool capability surface without the comma-selector warning. The retired `--toolsets ,` selector previously resolved to an empty toolset list but emitted an unrelated selector warning; it is not used by reviewers. The private query file contains the manifest-bound diff prompt, and the host verifies the structured artifact before acceptance. The host discards only the exact local `  ⚠ tirith security scanner enabled but not available — command scanning will use pattern matching only` diagnostic line when it is the initial stdout line; it then requires exactly one JSON object, rejecting every other prefix, suffix, malformed response, or multiple object response. This enforced boundary means `isolation remediation is pending` is not a current hold. Hermes writes its review artifact outside the public checkout, under the configured private report root, binding it to the same exact state as the manifest.

A clean conclusion is permitted only for a ready, complete, current review:

```text
No verified blocker found in this Steward pass.
```

## `steward` and `run steward`

`steward` is the terminal entrypoint. It runs the local runner first, using the safe built-in baseline when no per-repository override exists; only a ready manifest may be passed to Hermes for the public `hermes-pr-review` procedure. It must not use GitHub write operations.

`run steward` is the chat invocation of the same gate on the current committed branch. Hermes runs the local runner, reads the resulting manifest, and follows the public procedure. It is read-only with respect to GitHub objects and writes only its local report outside the public checkout.

## Exact-SHA freshness

A report covers only the repository, configuration revision, base SHA, merge-base SHA, and head SHA recorded in it. Any change to any of those values makes the report stale. Rerun the runner and complete the required review passes before relying on it. If a verified blocker is found, remediate it, commit the fix, and obtain a new report for the new exact head SHA.
