# Agentic Project System

**A complete, installable playbook for running a software project with an AI agent as a genuine co-maintainer.**

This repository is not an application. It is a *system* — a set of playbooks, skills, and
processes that let you drop an agent (or a team of agents) into any software project and have it
help triage issues, review and merge pull requests, run quality gates, talk to your community, and
keep the whole machine healthy — at a level of rigor that holds up in a real open-source project.

It is **project-agnostic by design.** Nothing here is specific to any one codebase. You bring the
project; the system brings the operating model, the recipes, and the questions it needs to ask you
to wire itself up.

> If you run an open-source project and you've ever wished you had a tireless, careful co-maintainer
> who reads every diff, never drops a contributor's question, and gets *more* trustworthy over time
> instead of less — that's what this system is for.

---

## What you get

- **An operating model** — the four-role architecture (Watcher · Reviewer · Builder · Steward),
  a human-in-the-loop spectrum (Bands A/B/C), and a security spine that makes autonomous action
  safe instead of reckless.
- **End-to-end lifecycle playbooks** — how work travels from "someone mentioned a bug on chat"
  all the way to "shipped in a release, issue closed with credit," with the exact decision states
  at every step.
- **A quality-gate pipeline** — layered, independent checks (automated review, adversarial review,
  the test suite as a growing body of regressions, visual verification) so that "the suite is the
  primary line of defense and the other gates are the secondary line."
- **Skills** — focused, loadable procedures an agent runs (set up the system, triage a PR, triage
  an issue, run the release pipeline).
- **A setup interview** — a documented question list the agent works through with you to learn your
  repos, your chat platform, your CI, your review tools — and configure the system to your project.
- **A documentation site** — everything organized by lifecycle area, published with GitHub Pages.

## Who it's for

- Maintainers of open-source projects who want an agent co-maintainer.
- Teams standing up an autonomous or semi-autonomous maintenance system.
- Anyone who has built a one-off agent workflow and wants a principled, reusable foundation instead.

## The core idea in one paragraph

Reading is cheap and safe; **writing to a public surface in the project's voice is the irreversible
act that needs a human decision.** Everything upstream of that line — discover, classify, dedupe,
review, draft, rank, gate — an agent can do autonomously with the right guardrails. The single
irreversible, reputational acts (merging code, closing on taste, posting publicly as the project)
stay human-gated, or autonomous-with-an-independent-watchdog. That membrane is not a limitation;
it's what makes running the whole thing unattended *safe*.

---

## Start here

1. **Read the [architecture overview](docs/architecture/README.md)** — the four roles, the bands,
   the security spine. ~15 minutes; everything else builds on it.
2. **Run the [setup interview](setup/setup-interview.md)** — the agent asks you the questions it
   needs (your repos, chat platform, CI, review tools) and writes your project's config.
3. **Pick your lifecycle area** — [issues](docs/lifecycle/issue-lifecycle.md),
   [pull requests](docs/lifecycle/pr-lifecycle.md), [quality gates](docs/lifecycle/quality-gates.md),
   [community](docs/lifecycle/community.md), [contributor recognition](docs/lifecycle/contributor-recognition.md).
4. **Load the [skills](skills/)** as you enter each phase.

Full map: **[docs/README.md](docs/README.md)** · Published site: _(GitHub Pages — see repo settings)_

## Repository layout

```
docs/
  architecture/    the operating model — roles, bands, security spine, principles
  lifecycle/       end-to-end playbooks per area (issues, PRs, gates, community, contributors)
  playbooks/       cross-cutting recipes (autonomy ladder, watchdog pattern, cron design)
  reference/       glossary, FAQ, adoption levels, anti-patterns
setup/
  setup-interview.md   the documented question list the agent runs with you
  config.template.*    the config the interview produces
skills/            loadable agent procedures, linked from the playbooks
```

## Status

Early. The architecture and lifecycle playbooks are the stable core. Skills and the setup interview
are evolving. Contributions and adaptations to your own project are the whole point — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) — use it, adapt it, run your project with it.
