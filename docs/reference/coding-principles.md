---
title: Coding principles
layout: default
parent: Reference
nav_order: 4
---

# Coding principles

The craft-level discipline for *writing and editing* code — the companion to the
[quality gates](../lifecycle/quality-gates.md) (which govern reviewing and shipping it). The gates
catch a bad change after it exists; these principles stop you from writing one in the first place.

Load these before any non-trivial code change. The [`coding-principles`](https://github.com/nesquena/steward-os/tree/main/skills/coding-principles)
skill is the runnable checklist form.

> One sentence governs all ten: **a wrong change that passes the tests is worse than no change.**
> Speed that creates a regression is not speed.

---

## The ten

### 1. Surface ambiguity before writing code
If the requirement is unclear or the approach has meaningful trade-offs, ask before implementing.
The cost of a clarifying question is a minute; the cost of confidently building the wrong thing is
the change, the rework, and the wasted review. When a task has two plausible readings, name both and
pick one out loud rather than silently guessing.

### 2. Surgical edits only
Change exactly what the task requires. Don't clean up nearby code, rename things "for clarity," or
add "while we're here" improvements unless asked. Every extraneous change is an independent
regression risk and makes the diff harder to review. If you spot real cleanup worth doing, file it
separately — don't smuggle it into an unrelated change.

### 3. No speculative features
If the spec says X, build X. Don't add Y because it logically follows or seems obviously useful.
Unrequested capability is untested surface, maintenance you signed others up for, and scope the
reviewer didn't agree to. The feature you're sure they'll want is a proposal, not a fait accompli.

### 4. Define "done" up front, then verify the real thing
Before writing code, name the observable fact that has to be true for this to be done — a specific
scenario, a failing test that should pass, a state that should appear. After the change: check
syntax, run the tests, and confirm *that exact scenario* actually works. Then keep going until it
does — don't stop at the first green checkmark.

- "It looks right" is not done.
- "The tests pass" is not done if the tests never exercised the real scenario.
- "The diff looks correct" is not done if the running code was never exercised.

### 5. Test isolation, and tests that assert intent
New tests must be independent: no dependence on run order, no shared mutable state, no reliance on a
previous test's side effects. The suite must pass in any order.

And a test should encode *why* the behavior matters. Pinning a literal log string or an exact markup
attribute is fragile when the real intent is "the user sees a clear error" — assert the contract (an
error is surfaced; the message names the failing field), not the surface text. A test that still
passes when you revert the implementation is testing presence, not intent — it's worse than no test,
because it gives false confidence.

### 6. Find which layer owns the lost state before fixing it
When something "disappears" or "resets" or "comes back wrong," locate the layer that owns the lost
state *before* touching code. The classic trap is blaming the presentation layer (the renderer, the
view) for what is actually a data-layer loss (a save/clear race, a dropped write).

A cheap discriminator: does reloading from the source of truth recover the data?
- **Recovers on reload** → the persisted data is fine; the bug is in the presentation/refresh layer.
- **Still gone after reload** → the data was lost at the source; the fix belongs in the write/persist
  path, not the view.

Data-layer loss is invisible to view-only debugging. Defaulting to "must be the renderer" burns
hours chasing the wrong layer.

### 7. Surface conflicts — don't blend patterns
When two patterns in the codebase contradict — an old helper vs. a new one, two competing data
shapes, parallel implementations of the same flow — pick one (usually the newer or better-tested),
commit to it, and flag the other for separate cleanup. **Don't make both coexist.**

Blending them creates the *runtime-coexistence* bug class: both code paths fire and produce
conflicting results, or one half of a rename ships while the other half still expects the old form.
These bugs pass unit tests (each path works alone) and only surface when both run together. See the
[bug-shape catalog](bug-shapes.md#1-runtime-coexistence).

### 8. Read the consumer, not just the producer
Before adding code to a file, read its exports, the immediate callers of the function you're
touching, and the shared utilities it depends on. The moment a change is "swap A for B," "migrate
from old to new," or "change the source of truth," assume there is a consumer *somewhere* still
expecting A.

Run a quick probe on both paths: does the old shape still get produced anywhere? Does every consumer
handle the new shape? Most "swap the source" regressions come from updating the producer and never
checking who reads it. See [bug-shape catalog](bug-shapes.md#2-producer-changed-consumer-didnt).

### 9. Fail loud — distinguish verified from assumed
"Completed" is wrong if anything was skipped silently. "Tests pass" is wrong if any were skipped,
marked expected-fail, or excluded without explanation. "Shipped" is wrong if a piece of the work
quietly didn't land. Always report the *actual* state, including partial failures, skipped checks,
and assumptions you didn't verify.

When you summarize a multi-step task, separate what you **verified** from what you **assumed**. If a
step couldn't be done, say so and why — don't paper over it. The cost of a silent skip is paid
later, by someone else, with interest.

### 10. Match the codebase's conventions over personal taste
If existing code uses a particular style, helper, naming pattern, or error-handling approach, follow
it — even when you'd write it differently. Conformance beats personal taste: mismatches make review
harder and seed new patterns that future contributors copy. If you genuinely think a convention is
wrong, raise it as a separate refactor proposal; never introduce a competing style inside a feature
change.

---

## One state, one owner

A corollary that prevents a whole family of subtle bugs: **any single piece of state should have
exactly one owner.** When two layers both try to control the same thing — two layers deciding
whether an element is visible, two caches holding the same value, two flags tracking the same
condition — they drift, and the result is a silent, intermittent wrongness that's painful to debug.

When you find yourself writing the second writer of some state, stop and make one of them the owner.
The other should *read*, not *write*.

---

## Public-repo discipline

If the project is public (or has any external contributors), a few rules are non-negotiable
regardless of language:

- **Branch + change-request for everything.** No direct pushes to the trunk, not even one-liners.
- **A changelog entry per release**, and any in-product version marker kept in sync with the tag.
- **A security pass before merge** on anything that handles input, auth, file paths, or
  credentials — see the [security spine](security-spine.md).
- **Never trust a contributor's stated numbers** (test counts, "all green"); re-derive them
  yourself. See [anti-patterns](anti-patterns.md).

---

_Related: [quality gates](../lifecycle/quality-gates.md) · [the bug-shape catalog](bug-shapes.md) ·
[anti-patterns](anti-patterns.md). The architecture page's
[guiding principles](../architecture/index.md#the-guiding-principles) are the project-level values;
these are the keystroke-level discipline that serves them._
