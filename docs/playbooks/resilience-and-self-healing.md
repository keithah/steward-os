---
title: Resilience & self-healing
layout: default
parent: Playbooks
nav_order: 5
---

# Resilience & self-healing

The maintenance system itself is software that runs unattended — so it needs the same care you'd
give any production service: it should survive a reboot, restart when it crashes, notice when it's
silently wrong, and close the gaps in its own bookkeeping. This playbook is the set of patterns that
keep the *machine that maintains the project* healthy.

> Principle: **automate the recovery, not just the action.** A system that does work autonomously
> but can't recover from its own failures isn't autonomous — it's a liability with a delay.

---

## Layer your supervision (OS-level under app-level)

If your system runs a long-lived service (a web app, a gateway, a daemon), supervise it at **two**
layers — they catch different failures and neither alone is enough:

1. **OS-level supervision** (e.g. systemd, launchd, a process manager): handles *process died* and
   *machine rebooted*. It boot-starts the service, restarts it within seconds of a crash, tracks the
   real PID, and logs to the system journal. This is the floor — without it, a reboot or a crash
   takes you down until a human notices.
2. **Application-level health watchdog** (a scheduled job): handles what the OS *can't see* — a
   process that's alive but wedged (HTTP-hung, 500ing, deadlocked), or serving a stale version after
   a deploy that didn't restart. Crucially, it **alerts a human** (the OS restarts silently — you'd
   never know it flapped). It restarts *through* the OS supervisor (never fighting it).

The division: **OS = the machine, watchdog = the app + the alerting.** A common mistake is having
only one. Only the OS layer → you never learn about app-level wedges or version drift. Only the
watchdog → a reboot leaves you down until the next poll, and there's no instant crash-restart.

### The pre-restart guard (don't restart into a broken state)
An auto-restarting watchdog must refuse to restart when a restart would make things *worse*. If the
deployment is mid-change (a partial update, an in-progress operation), a blind restart can serve
broken or half-applied code. The guard: **if the deploy is in a known-unclean state, alert instead
of restarting** — a human is probably in the middle of something. Restart only from a clean state.

---

## The reconcile pattern (close the gaps in hand-maintained bookkeeping)

Any ledger a human (or agent) maintains *by hand* — a trust ledger, a release log, an outcome
tally — drifts when an event happens outside the normal flow (something merged while no one was
looking, a step got interrupted). The reconcile pattern is a scheduled job that **diffs the ledger
against ground truth and closes the gap**:

- Find the events that reached a terminal state but have no ledger entry.
- **Auto-record only the unambiguous, low-distortion cases** (ones where a wrong guess is harmless —
  e.g. an *unscored* outcome, or a case with one obvious classification).
- **Surface the judgment cases** with a suggested entry + the exact command to record it — never
  auto-guess a value that moves a score or makes a claim.
- Never mutate the source of truth (the live system); only the local ledger.

This keeps a hand-maintained record honest without pretending the agent can make every call. The
same shape works for "issues that should be closed," "PRs that went stale," "contributors whose
outcomes weren't logged" — detect the gap, auto-close the safe part, surface the rest.

---

## The flake ledger (make "never tolerate a flake" systematic)

A flaky test — one that fails then passes on re-run — is a **defect**, usually a real race wearing a
costume. The failure mode is *social*, not technical: a flake gets re-run away in the moment and
forgotten, so it never gets fixed and erodes trust in the whole suite. The fix is a **ledger that
won't let it be forgotten**:

- **Log every flake occurrence** (test id + date + signature) whenever one is observed in a gate/CI run.
- **A test that flakes ≥2× (unresolved) is auto-flagged "root-fix now"** — recurrence is the trigger
  to stop re-running and start root-causing.
- **A flake stays open until explicitly resolved** (root-caused + fixed, with the fixing change
  recorded). It **re-opens automatically if it flakes again** (a regressed fix).

The ledger is the structured tally + the nag; the prose diagnosis of each flake lives wherever your
team keeps its debugging notes. Together they turn "ugh, flaky again, re-run" into a closed loop that
drives flakes to zero.

---

## Self-test for any unattended subsystem
- If the process dies, does it come back **without a human**? (OS supervision)
- If it's alive but *wrong*, does someone **find out**? (health watchdog + alerting)
- If it auto-acts on a public surface, is each action **independently verified**? ([watchdog pattern](watchdog-pattern.md))
- Does its own bookkeeping **self-heal** when an event slips through? (reconcile)
- Do recurring failures **get fixed, not re-run-away**? (flake ledger)

If you can't answer all five, the subsystem isn't truly autonomous yet — it's automated-until-it-isn't.

---

_Related: [the watchdog pattern](watchdog-pattern.md) · [scheduled jobs](scheduled-jobs.md) ·
[the autonomy ladder](autonomy-ladder.md) · [quality gates](../lifecycle/quality-gates.md#flakes-never-tolerate-one)._
