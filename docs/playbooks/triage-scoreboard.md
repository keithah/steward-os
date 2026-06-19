---
title: The triage scoreboard
layout: default
parent: Playbooks
nav_order: 4
---

# The triage scoreboard

A pile of open pull requests (or issues) is not a plan. The scoreboard turns it into a **ranked,
evidence-tagged review queue** so the maintainer always knows what to look at next and what state
each item is in. It's the Steward's central instrument.

---

## The idea
Compute, for every open item, a small set of **dimensions**, combine them into a single **composite
rank**, and render a living document sorted by that rank. Re-generate it on a schedule so it's always
current without anyone asking.

## Two kinds of dimension
- **Mechanical** (recomputed fresh every run, no judgment): CI status, mergeable/conflicting,
  how far behind the trunk, diff size, age, contributor trust score.
- **Judgment** (set by a reviewer/agent, persisted across runs): scope fit, criticality, risk,
  severity. These survive regeneration so a human's assessment isn't lost when the mechanical data
  refreshes.

Keep the two separate in storage: mechanical data is disposable and re-derived; judgment data is
precious and persisted.

## The composite
A weighted sum of the dimensions, tuned so the ranking matches "what a maintainer would actually
pick up next." Weights are a knob you adjust as you learn what your project values (e.g. how much to
weight contributor trust vs. raw criticality). Promote genuinely-breaking items to the top regardless
of their composite (a severity spotlight).

## Evidence-readiness tags
Beyond rank, show **which review streams have landed** for each item — turning the board from a
*ranking* into a *readiness view*. For PRs that might mean: automated code review present? visual
preview present? deep-review dossier cached? A compact per-row marker (✓ present / · not yet) lets
the maintainer see at a glance which items are fully evidenced and which are still settling.
Presence ≠ clean — it means the evidence *exists to review*; the authoritative gate still runs.

## Make it self-refreshing
Run the generation on a schedule ([scheduled-jobs](scheduled-jobs.md) rules apply): pull live
metadata, recompute mechanical dims, merge in persisted judgment dims, render, commit. The maintainer
never has to ask for a fresh scan — the board is current when they open it. A companion "review these
first" curated short-list can ride on top for the highest-priority handful.

## Why it works
- It **separates ranking from doing** — the agent ranks continuously and cheaply; the human (or a
  Band-B session) does the expensive review on the top of the list.
- It makes **state legible** — at a glance you see what's ready, what's blocked, what's waiting on
  whom.
- It's **honest about evidence** — the readiness tags stop "green CI" from masquerading as "ready to
  merge."

---

## Skill for this
- `triage-scoreboard` — compute dimensions, rank, render the living board + the curated short-list.

_Related: [PR lifecycle](../lifecycle/pr-lifecycle.md) ·
[contributor recognition](../lifecycle/contributor-recognition.md) · [scheduled jobs](scheduled-jobs.md)._
