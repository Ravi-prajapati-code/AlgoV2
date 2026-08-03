# Doc 50 — SECTOR_RS_WEIGHT Gate REJECT (2026-07-31)

**Status**: REJECTED. Written retroactively to close a Repository Integrity gap — this result
existed only in memory (`sector_rs_weight_reject_20260731`), never got its own `docs/NN.md`, and
was missing from doc/42's own same-day chronological index (doc/46 Finding 1). Per doc/47 §7.1,
a `docs/NN.md` is required for any commit that changes live-relevant behavior; a memory note alone
does not satisfy that rule.

## 1. Hypothesis

Add raw sector price-momentum-vs-Nifty (today's average `rs_rank` of a sector's own members,
ranked across sectors) as an entry-score nudge — distinct from the already-live
`sector_durability` (`docs/13`-era, `sector_durability_deployed_20260713`), which reads the
strategy's own past realized trade P&L per sector, not raw market price trend.

- **Why alpha should exist**: sectors go in and out of favor together (capital rotation); tilting
  toward the currently-hottest sector should concentrate capital in names most likely to keep
  outperforming.
- **What could reject it**: if broad rallies lift most sectors together, sector-tilting
  concentrates into fewer names and forfeits breadth — winning names outside the "hot" sector get
  missed.

## 2. Implementation

Wired into both `backtest/engine.py` and `runner/daily_runner.py` (live), default weight `0.0`
(off) — flag defaulted OFF per doc/47 §2.3. Committed `f2238b8`. Same commit also fixed
`sector_durability` being wired into backtest but missing from the live runner — a bundling that
violates doc/47 §2.2 in hindsight (unrelated bug fix + new experimental lever in one commit); noted
here rather than re-litigated, since the commit predates the constitution that names the rule.

## 3. Gate result (`robustness_gate.py --env SECTOR_RS_WEIGHT=1.0`)

```
TRAIN  base -1.10%/Sharpe 0.08   cand +10.47%/Sharpe 0.54   (candidate better)
TEST   base +41.64%/Sharpe 1.58  cand -11.95%/Sharpe -0.75  (candidate much worse, N=74 vs 133)
FULL   base -4.64%/Sharpe -0.13  cand +0.51%/Sharpe 0.14    (candidate better)
Stress crash_v_recovery/chop: candidate better in both scenarios
```

**REJECT**: TEST-window Sharpe delta -2.33, PF -1.43 vs. tolerance. Per doc/47 §3.4, this is
reported precisely as "wins TRAIN/FULL and both stress scenarios, guts TEST" — not rounded up to
a partial pass.

## 4. Mechanism

Same failure signature as `docs/38` Addendum 3 (strict-AND trend gate vs. `PURE_RS`): helps TRAIN
and trend-confirmation stress scenarios, guts the TEST window specifically. TEST (2025-2026) was a
broad-based rally where plain RS already caught winners across many sectors; sector-tilting
concentrated into 44% fewer trades (74 vs. 133) and missed movers outside the "hot" sector at any
given time. This reads as a real, consistent trade-off (helps in choppier/crash-recovery
conditions, hurts in a uniform broad rally) — not noise.

## 5. What was not done

Other weight values were not swept to find one that passes TEST. Doing so would repeat the exact
"TEST-window-driven parameter search" antipattern `docs/38`'s final verdict identified as the core
problem with this project's history — tuning against the evaluation window is not evidence of
alpha, it's overfitting to it. The lever stays at its default (`0.0`/off) as a clean negative
result: this rules out raw sector-RS tilting at this specific implementation, not "needs more
tuning."

## 6. Verdict

**REJECTED.** If sector-preference-on-multi-signal-days comes up again, this specific approach
(today's raw sector momentum, as an entry-score nudge) is tested and closed. `sector_durability`
(trade-outcome based, already live) remains the accepted alternative — see
`sector_durability_deployed_20260713` and `sector_durability_gate_pass_20260713`.
