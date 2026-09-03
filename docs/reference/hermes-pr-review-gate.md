# Hermes PR Review Gate

The Hermes PR review gate produces local-only, exact-SHA review evidence before a pull-request workflow continues. It does not create, update, approve, label, merge, push, deploy, or otherwise change GitHub state. A clean report is not approval or merge authority.

## Install the public procedure

Keep this repository public-safe. Store live configuration, manifests, reports, credentials, repository inventories, and host-specific paths outside the reviewed checkout and outside this repository.

1. Make the runner available from a trusted checkout of this repository.
2. Copy [`setup/hermes-review-config.example.json`](../../setup/hermes-review-config.example.json) to a private configuration directory.
3. Replace only the sanitized placeholders with your repository ID and private absolute state roots. Do not put secrets, tokens, hostnames, or live local paths in public files.
4. Load [`skills/hermes-pr-review/SKILL.md`](../../skills/hermes-pr-review/SKILL.md) in Hermes for the review procedure.

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
    "execute_contributor_code": false,
    "sandbox_available": false,
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

The runner accepts only `safe`, `sandbox`, and `disabled` command execution modes. Commands are trusted operator-configured deterministic commands; a contributor does not gain command execution by changing the repository. Contributor-authored code is eligible only when `execute_contributor_code` and `sandbox_available` are both explicitly true and the command uses `sandbox`. `sandbox_available` is an operator attestation that a sandbox already exists; it does not provision or create a sandbox.

All state roots must be absolute, distinct, and outside the reviewed checkout. The runner accepts no environment interpolation or secret values. The configuration revision is the SHA-256 of the canonical JSON configuration.

## Run the evidence collector

From the public runner checkout, invoke the runner with a clean target repository and private configuration:

```sh
python3 scripts/steward_review.py \
  --repo-dir /path/to/repository \
  --config /private/steward-os/repositories/owner__repository.json
```

The runner refuses a dirty checkout, mismatched origin/config identity, invalid configuration, state roots inside the checkout, or failed eligible command. It writes a manifest only after valid Git/configuration state is resolved. A failed eligible command produces a `blocked` manifest and a nonzero exit status.

The manifest is local-only JSON at:

```text
<manifest_root>/<owner>__<repo>/branch-<sanitized-branch>/<head_sha>.json
```

It records the exact repository, branch, base ref, base SHA, merge-base SHA, head SHA, configuration revision, selected lane, changed paths, command results, skipped checks, and status. Command output is bounded and records whether it was truncated.

## Lane behavior and Hermes review

- **fast:** no changed path matches configured deep, sensitive, or visual patterns. Hermes still performs the primary review.
- **deep:** a changed path matches a deep or sensitive pattern. Hermes performs the primary review plus a separate adversarial review.
- **visual:** a changed path matches a visual pattern. Hermes performs the primary review plus a separate adversarial review, including visual evidence where available.

Hermes reads the manifest before reviewing. A `blocked` manifest stops the procedure. For a ready manifest, Hermes inspects the diff, changed paths, repository instructions, deterministic command evidence, relevant paths, and current PR checks/comments when a PR exists. Hermes writes its Markdown report outside the public checkout, under the configured private report root, binding it to the same exact state as the manifest.

A clean conclusion is permitted only for a ready, complete, current review:

```text
No verified blocker found in this Steward pass.
```

## `steward` and `run steward`

`steward` is the terminal entrypoint. It runs the configured local runner first; only a ready manifest may be passed to Hermes for the public `hermes-pr-review` procedure. It must not use GitHub write operations.

`run steward` is the chat invocation of the same gate on the current committed branch. Hermes runs the local runner, reads the resulting manifest, and follows the public procedure. It is read-only with respect to GitHub objects and writes only its local report outside the public checkout.

## Exact-SHA freshness

A report covers only the repository, configuration revision, base SHA, merge-base SHA, and head SHA recorded in it. Any change to any of those values makes the report stale. Rerun the runner and complete the required review passes before relying on it. If a verified blocker is found, remediate it, commit the fix, and obtain a new report for the new exact head SHA.
