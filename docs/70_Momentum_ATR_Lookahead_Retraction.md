# 70 — Retraction: docs/57, docs/58, docs/61 Numbers Predate Look-Ahead Fix

**Date:** 2026-09-22
**Status:** Retraction / correction notice, not new research
**Trigger:** user asked what to work on next with full permission; re-audit
of a 2026-08-14 confirmed-but-never-closed-out finding.

## What's being retracted

Every CAGR/Sharpe/PF figure in `docs/57_Momentum_ATR_Concentrated_Rotation_Experiment.md`,
`docs/58_Momentum_ATR_Standalone_Robustness_Gate.md`, and
`docs/61_Momentum_ATR_Universe_Eligibility_Research.md` (research
experiment ids 9-19, plus the docs/61 addendum's *baseline* comparison
column, dated 2026-09-02) was produced by
`scripts/momentum_atr_experiment/engine.py` **before** it was fixed.

## The bug, and the fix, confirmed still in place

`engine.py`'s `topN_names()` built every buy decision (initial buy,
RANK_EXIT_REALLOC_TOPN, FREEZE_REASSESS) from the *same* day's score and
open price — a score built from that day's own close/high/low, executed
at a price from earlier the same day. Fixed 2026-08-14, commit `6e1c275`,
logged as `db/research.db` experiment id 24 (ACCEPTED). Verified today
(2026-09-22) still committed and unreverted:

```
$ git log --oneline -- scripts/momentum_atr_experiment/engine.py
6e1c275 Fix two simulation-fidelity bugs in momentum_atr engine.run_sim
5c0f429 Add standalone momentum x ATR live strategy (docs/57, docs/58)
$ git status --short scripts/momentum_atr_experiment/engine.py
(clean)
$ grep -n pday scripts/momentum_atr_experiment/engine.py
186:    def topN_names(day):
219:    # pday's score, never day's own...
222:    pday = trading_days[i - 1] if i > 0 else None
227,252,262,289: names = topN_names(pday)
```

Impact measured at fix time (baseline FULL-strategy): TRAIN CAGR
96.04% -> 51.14% (Sharpe 1.58 -> 1.02), TEST CAGR 138.75% -> 91.2%
(Sharpe 2.10 -> 1.61). Every number in docs/57/58 and the pre-addendum
part of docs/61 was generated before this fix and is inflated by an
unknown, non-uniform amount per variant (the distortion isn't a constant
offset — it depends how sensitive each variant's turnover/exit logic is
to same-day vs. next-day fill price).

**Not retracted**: the docs/61 addendum itself (2026-09-02, volume-floor
research, 91.2%->97.4% / 1.61->1.68 / -36.7%->-33.9%) — those figures were
generated after the fix, against the corrected engine, and are internally
consistent with each other (same engine version on both sides of that
comparison). Only the pre-addendum baseline number they're diffed against
inherits the same caveat as docs/57/58.

## Why this wasn't caught earlier

The 2026-08-14 finding explicitly flagged this reconciliation as
not-yet-started ("docs/57, docs/58, docs/61 and experiment 23 ... are now
confirmed-stale and not yet re-run/retracted"). No later doc closed the
loop. `docs/62` through `docs/69` never touch momentum_atr experiment
numbers, so nothing else in the docs series depends on the stale figures
— this is a self-contained retraction, not a chain of contaminated
downstream conclusions.

## Live trading: unaffected, checked independently

The live momentum_atr path (`momentum_atr/scoring.py` +
`momentum_atr/execution.py`, wired to production since 2026-08-07) is a
**separate implementation** from `scripts/momentum_atr_experiment/engine.py`
and was never subject to the same bug, structurally rather than by luck:

- `scripts/precompute_momentum_atr_ranking.py` runs 08:50 IST
  (pre-market, cron-verified) and calls `compute_live_scores()` once.
  Market hasn't opened yet, so the only daily bar the data source can
  possibly return as "latest complete" is the prior day's close — there
  is no same-day bar to leak from at that hour.
- `scripts/run_momentum_atr_live.py` (09:17 IST, after market open) does
  **no scoring at all** — it only reads the ranking already written to
  `db/momentum_atr.db` by the 08:50 run and places orders at today's
  open. If the precompute row is missing, it aborts and pages rather
  than falling back to a live compute that could reintroduce the bug
  (see that script's own docstring).

So the decide-on-yesterday's-close / execute-at-today's-open separation
that `engine.py` had to be fixed to enforce is, for the live path, a
structural consequence of *when* the two crons run, not something that
could regress via a code change to the scoring formula alone. This is
worth remembering if the cron schedule is ever changed (e.g. moving
precompute later, or adding a same-morning rescore) — that would be the
one way to reintroduce this class of bug on the live side.

## Action taken

No code changed. This doc marks docs/57, docs/58, and docs/61's
pre-addendum baseline as **stale evidence, not to be cited** without this
caveat. Re-running those experiments under the corrected engine to get
honest replacement numbers is optional future work, not required — none
of the currently-live configuration was chosen based on those numbers
(confirmed: live momentum_atr config traces to `docs/61`'s addendum
volume-floor work and later docs, not to the pre-fix docs/57/58
experiments).
