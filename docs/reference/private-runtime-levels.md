# Private runtime: Level 2 scoreboard and Level 3 labels

This reference describes an optional private runtime for Steward adopters. It is not enabled by this repository, does not include credentials, and does not install scheduled jobs.

## Boundaries

The runtime stores all mutable artifacts outside the checkout, by default under `~/.hermes/steward-os/`. It uses `gh api` with `GET` for discovery and verification. Public writes are limited to one opt-in action: adding exact `steward:size:S`, `steward:size:M`, or `steward:size:L`, or `steward:size:XL` labels to explicitly onboarded pull requests.

It never removes, replaces, or renames a label; infers labels from free text; posts comments; closes issues; merges pull requests; changes repository settings; or repairs watchdog findings.

## Level 2: private scoreboard

`scripts/steward-scoreboard.py` selects eligible owned repositories that are not forks, archived, or disabled and are either recently pushed or have an open pull request. It paginates repository, issue, and pull-request collection deterministically.

The job combines structured evidence such as checks, mergeability, review activity, age, dependency/security signals, and persisted human priority or risk judgment into a private scoreboard. It atomically writes `scoreboard.json` and `scoreboard.md`; an unchanged normal run is silent. `--daily` emits a bounded top-queue digest while retaining the full private board.

```sh
/opt/homebrew/bin/python3.11 scripts/steward-scoreboard.py
/opt/homebrew/bin/python3.11 scripts/steward-scoreboard.py --daily
```

The presence of CI, mergeability, or review evidence is not a merge verdict.

## Level 3: add-only pull-request size labels

Before any public write, freeze the initial repository set from a current private scoreboard or pass explicit repository names. Bootstrap requires `--apply` and creates only missing labels from this exact taxonomy:

- `steward:size:S` for 0–50 changed lines;
- `steward:size:M` for 51–200;
- `steward:size:L` for 201–500;
- `steward:size:XL` for 501 or more.

A sync applies at most one label to an open non-draft pull request in an explicitly onboarded repository. Existing `steward:size:*` labels are terminal. Negative, boolean, and otherwise malformed diff totals fail closed before any external or private completion-state mutation. Every successful action is written to a private append-only ledger.

```sh
/opt/homebrew/bin/python3.11 scripts/steward-bootstrap-labels.py --from-current-scoreboard --apply
/opt/homebrew/bin/python3.11 scripts/steward-label-sync.py --limit 6
```

`--limit 6` is a maximum for one incremental run. It does not replay prior actions; a later invocation may process the next eligible pull requests. Do not use another live sync as an idempotence probe.

If a label write times out or otherwise has an ambiguous outcome, the runtime retains its private pending record and fails closed. It does not recalculate a different size or issue a second public label write. A visible matching label is reconciled into the ledger exactly once.

## Independent watchdog

`scripts/steward-label-watchdog.py` is read-only. It treats the ledger only as a list of actions to recheck, then reads current pull-request labels and totals. It emits RED findings for malformed records, off-allowlist actions, non-onboarded repositories, missing labels, source-total mismatches, and unavailable or malformed live responses. It never changes GitHub or repairs drift.

```sh
/opt/homebrew/bin/python3.11 scripts/steward-label-watchdog.py
/opt/homebrew/bin/python3.11 scripts/steward-label-watchdog.py --fixture tests/fixtures/bad-ledger.jsonl
```

The fixture command is intentionally nonzero and offline.

## Scheduling

Scheduling is a separate acceptance step, after controlled live onboarding, read-back verification, and a clean watchdog result. A future scheduler should use absolute paths, bounded incremental label sync, and a durable delivery path. No schedule is created by these scripts.
