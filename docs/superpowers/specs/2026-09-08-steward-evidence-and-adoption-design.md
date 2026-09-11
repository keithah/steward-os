# Steward evidence and adoption design

## Decision

Evolve Steward in five ordered gates. The local review gate remains read-only, exact-SHA-bound, and private. Repository-specific configuration becomes an optional override; ordinary repositories use a single owner-private global policy derived from Git `origin`.

No public GitHub write, merge, deployment, or autonomous action is part of this work.

## Gate 1 — Private benchmark scorecards

`steward_review_benchmark.py` must treat its output as private evidence. Its output path remains absolute and outside the working tree. Before writing, it must create/validate an owner-private output directory (`0700`) and atomically create the JSON scorecard (`0600`). Existing non-private directories and non-regular destination paths fail closed without chmodding or replacing them. Tests must cover new root creation, non-private rejection, file modes, and failed write cleanup.

A fresh deep-lane Opus (`anthropic` / `claude-opus-4-6`) and GPT Terra (`openai-codex` / `gpt-5.6-terra`) review of the fix must complete at its exact committed SHA. A model finding is a blocker until independently reproduced and resolved.

## Gate 2 — Default global policy, optional repository overrides

The runner derives `owner/repository` from a normalized supported GitHub `origin`; it never requires a committed or per-repository config file for the default path.

A single external, owner-private global policy root supplies:

- distinct owner-private manifest and report roots;
- default lane globs and model contracts;
- a conservative default base-ref resolution;
- no executable checked-out commands by default.

Optional repository-specific overrides are allowed only for explicit exceptional requirements: alternate base refs, custom lane patterns, or trusted deterministic evidence. They extend or replace named policy fields only after exact schema validation. The repository ID remains origin-derived and cannot be supplied by configuration. Absent global policy falls back to the existing diagnostic baseline and remains blocked rather than implying quality evidence.

The global policy and all generated state stay outside the reviewed checkout, contain no secret values, and must be `0700`/`0600` as applicable.

## Gate 3 — Complete the supervised Level 1 loop

Run `pr-triage` on one current open `keithah/steward-os` pull request after Gate 1 passes. It must perform the vulnerability divert, scope/fit screen, marginal-benefit screen, and lane route. The result is a private record only; no GitHub comment, label, review, closure, or merge is made.

This proves the quickstart’s supervised PR loop. A local LLM artifact is supplemental evidence, not a substitute for triage.

## Gate 4 — Recall measurement

Run the redacted historical CodeRabbit corpus through the deterministic scorer for:

1. the current structured Opus+GPT artifact contract; and
2. the existing generic Hermes review baseline, using equivalent redacted structured findings.

All scorecards remain private. Report case count, matched and missed cases, unexpected IDs, recall, and precision proxy. Do not claim improvement unless both runs use the same validated corpus and the result is reproducible. The corpus remains redacted: no raw reviewer prose, prompts, diffs, tokens, or host paths.

## Gate 5 — Hermetic execution evidence

Add a locked-down Python 3.11 sandbox adapter for reviewed-code test commands. The adapter receives only the committed checkout at the manifest SHA, uses a deterministic environment, has no GitHub write capability, no host credential access, bounded CPU/time/disk/network policy, and returns bounded structured stdout/stderr and exit status.

The runner may record a sandbox command only when the adapter proves its isolation and exact SHA binding. Failure, timeout, missing runtime, dirty checkout, or incomplete output blocks the manifest. Host `safe` commands remain prohibited from executing reviewed code.

Start with the repository’s `python3.11 -m unittest discover -s tests -v` contract in the sandbox, then add a real sandbox acceptance run. The full test suite and isolation regressions must pass.

## Gate 6 — Level 2 read-only operations

After Gates 1–5 and a clean Level 1 triage record, run the existing deterministic scoreboard and watchdog as supervised one-shots with private state and local-only output. Verify:

- the scoreboard reads live GitHub state, preserves private judgment data, and emits a bounded private index;
- the watchdog re-derives recorded action correctness from live state, is silent when clear, and emits a bounded finding when a fixture is wrong;
- no command mutates GitHub.

Only after direct runs, persisted job-definition read-back, completed scheduler records, durable private artifact checks, and no duplicate-run risk may a read-only schedule be enabled. Public-write jobs remain disabled. A watchdog never repairs state.

## Acceptance sequence

1. Each gate follows RED → GREEN → REFACTOR with focused and full-suite evidence.
2. Each implementation commit is reviewed at its exact current head; a later commit invalidates prior review artifacts.
3. Gate 1 must be complete before a new live Opus+GPT acceptance. Gates 3–6 follow in order; a failure stops promotion.
4. Before publication, run Steward on the final committed head and independently review any remediations.
5. Push only the final reviewed branch, then create a PR. No merge occurs in this program.

## Non-goals

- Per-repository configuration as an adoption prerequisite.
- Storing credentials, prompts, corpus prose, or report artifacts in Git.
- Executing reviewed code on the host.
- Unattended GitHub writes, merging, release announcements, or issue closure.
- Replacing maintainer judgment with an LLM verdict.
