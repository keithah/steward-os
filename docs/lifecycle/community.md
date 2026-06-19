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
