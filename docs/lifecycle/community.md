---
title: Community
layout: default
parent: Lifecycle
nav_order: 4
---

# Community

Chat platforms, social mentions, announcements — the surfaces where the project meets its users.
The governing line for this whole area:

> **Reading, finding, and drafting are safe to automate. Writing publicly in the project's voice is
> the irreversible act that stays human (Band C) or autonomous-with-a-watchdog (Band A).**

A single misjudged public post can't be un-sent, and platforms ban bots that auto-reply. So the
system does all the *upstream* work — monitor, classify, dedupe, draft, queue — and keeps a human at
the public-voice membrane.

---

## Chat monitoring (Band A — read + capture only)
An agent watches your community's bug/feedback channels, classifies each message, dedupes against
the issue tracker, and captures actionable items to the staging queue (→ [issues](issue-lifecycle.md)).
- It is the most injection-exposed role — it runs under the strictest read-only guardrails and never
  obeys instruction-like text in a message.
- **It does not hold public conversations.** The only outward signal it leaves is a lightweight
  "captured" reaction so the reporter and maintainer can see it was logged. Replies in the project's
  voice stay human — that human touch is what makes contributors feel valued, and it's worth
  preserving deliberately, not automating away.
- Multi-product communities: route each channel to the *right* repo/queue. Don't capture reports for
  a product you don't own.

## Confidence-tiered capture → action (the safe way to auto-file)
Capturing is always safe; *filing an issue to the public tracker from a chat report* is a public
write, so gate it by **confidence tier** rather than filing everything or nothing:
- **HIGH** (a concrete, reproducible bug naming a specific surface, that passed dedup) → **auto-file**
  a privacy-safe issue. Two hard constraints: (1) the issue body is a **structured paraphrase, never
  the reporter's raw words**, with **no reporter identity** (handle, id, mention, message link,
  email) — run it through a **fail-closed privacy scrub** that blocks the file if any PII pattern is
  present ("no clean, no file"); (2) label it for triage + cap the auto-files per run so a busy day
  can't spray the tracker. Every auto-file is logged and [watchdog](../playbooks/watchdog-pattern.md)-verified.
- **MEDIUM** (actionable but vaguer, a feature request, or any doubt / scope call) → **draft it and
  surface to a human to approve** — don't file. The human's one-tap/one-word approval files it.
- **LOW / noise** → capture-to-queue only (or skip); never file, never ping.

The tiering is what lets you get the latency win (real bugs filed promptly) without the risk
(autonomously filing junk or leaking a reporter's identity). Feature requests and "does it do X?"
questions are scope/judgment calls — never auto-filed.

### The approval channel: a scheduled job notifies, a human reply *acts*
The MEDIUM/LOW approval has a wiring subtlety worth knowing before you build it. A **scheduled job
can SEND a message but cannot RECEIVE the answer** — it runs in its own context, detached from the
live chat connection, so it can't block on or read your reply. The clean pattern that respects this:

1. The scheduled monitor **notifies** — one message per pending item: *"Bug X queued. Reply `file
   <id>` or `skip <id>`."* It does not wait.
2. Your **reply is a fresh, live agent turn** on the chat platform (the platform's normal inbound
   path). *That* turn does the filing — it has the tools and the live connection the cron lacked.

This decoupling (notify-from-the-job, act-on-the-human-reply) is the no-extra-infrastructure way to
do human-in-the-loop confirmation over chat. Rich interactive widgets (inline buttons) may *render*
from a job, but a **tap only does something if the platform's connection owner has a handler for it**
— so unless that's already wired, a typed reply (`file`/`skip`) is the robust, build-nothing path.
Verify end-to-end with a real item before trusting it — confirm an actual record gets created, not
just that the message sent.

### Make the action atomic (concurrent writers will bite you)
The shared queue/ledger that tracks "what's pending / what's been filed" often has **several writers**
— the periodic monitor, the reply-handler turn, ad-hoc sessions. If each hand-edits the JSON, writes
get clobbered (a reply-handler's "filed!" bookkeeping vanishes under the monitor's next tick). Two
fixes, both cheap: (1) do the whole file-on-approval action through **one deterministic, file-locked
helper** rather than free-hand JSON edits; (2) record the durable audit trail in an **append-only log**
(one line per action) that no concurrent rewrite can truncate — that log, not the mutable state file,
is what the [watchdog](../playbooks/watchdog-pattern.md) re-verifies.



## Mentions sweep (Band A — find + report only)
A scheduled sweep of the open web (forums, social, blogs, search) for discussion of the project,
with a disambiguation step (confirm it's *your* project, not a namesake). Output is a curated digest
to the maintainer — **find and report, never auto-reply.**
- The highest-leverage *next* step (kept human): route confirmed-answerable mentions into a
  draft-reply queue — drafted by the agent, **sent by the human.** This converts passive monitoring
  into active support without any autonomous public posting.

## Announcements (Band A — narrow, templated, gated)
Release announcements are the one routine public write that's safe to automate, because the content
already exists (the changelog) and the action is templated and diff-gated:
- Compose from the changelog + release tags; post to a **fixed, hardcoded** channel via a
  secret-isolating helper (the bot token never enters the agent's context).
- Gate on a "last-announced" marker so it fires once per release, never re-posts.
- Anything beyond the templated announcement (general conversation, replies) stays human.

## The far horizon (deliberately staged)
The system *can* extend much further — drafting release posts for social, drafting replies to users
hitting known issues, aggregating sentiment. All of that is safe **as drafting** (Band B) → **human
sends** (Band C). The one place to resist full autonomy even with guards is **auto-posting public
replies in the project's voice** — keep it draft→approve. See
[anti-patterns](../reference/anti-patterns.md).

---

## Skills for this area
- `chat-monitor` — Watcher: read channels, classify, dedupe, capture, react.
- `mentions-sweep` — Watcher: web sweep, disambiguate, curate a digest.
- `release-announce` — Steward: templated announcement via a secret-isolating helper.

_Related: [issue lifecycle](issue-lifecycle.md) · [contributor recognition](contributor-recognition.md)
· [security spine](../reference/security-spine.md)._
