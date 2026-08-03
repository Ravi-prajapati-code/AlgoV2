# Final Verdict: The "Blind 2022" Test

**Date**: 2026-07-30
**Question asked**: If this strategy had been built and frozen in 2022, with zero knowledge of what markets would do afterward, would it have worked? And does the answer to that tell us anything about whether it works *now*?

This is not a new experiment. It is a single clean run of the **current, already-live config** (post Nifty-500 expansion 07-21, post regime-tiered sizing 07-27) across TRAIN / TEST / FULL, all three numbers from the same script, same config, same run — so nothing here mixes strategies the way earlier reports (docs/23, docs/36) accidentally did.

```
python3 scripts/out_of_sample_validator.py

TRAIN: 2022-01-01->2024-12-31  CAGR -1.10%  Sharpe 0.08  MDD 30.13%  WR 37.4%  PF 1.26  N=203  FAIL
TEST:  2025-01-01->2026-06-04  CAGR +41.64%  Sharpe 1.58  MDD 18.77%  WR 41.4%  PF 2.11  N=133  PASS
FULL:  2022-01-01->2026-06-04  CAGR -4.64%  Sharpe -0.13  MDD 40.40%  WR 34.1%  PF 1.11  N=276  FAIL

VERDICT (tool's own output): UNSTABLE — PF, Sharpe diverge materially between train and test.
Any full-window or single-window 'pass' for this config is not reliable evidence of a real edge.
```

## Answering the question directly

**If you had turned this on in January 2022 and never touched it again**: you would have spent **three years** — 2022 through 2024 — in a strategy that was flat-to-losing (CAGR -1.10%, Sharpe 0.08, essentially a coin flip after costs) and that at one point drew down **30%**. Nobody — no real person managing real capital — holds a system through a 3-year losing stretch with a 30% drawdown on faith that year 4 will be different. You would have stopped, or been stopped out by risk limits, long before reaching 2025.

The only reason the *current* headline numbers look good is that the good part (2025 → mid-2026) comes *after* the bad part, and you're looking at it with the benefit of already knowing it happened. A truly blind 2022 investor does not get to skip to the good chapter.

**Is the 2025-2026 window even trustworthy as "proof it works now"?** No, and this is the part that matters most: that window was not a held-out test that got checked once. It was used, by name, as the pass/fail criterion in at least 15+ separate experiments over the last month (docs/23 §XIII/§XVIII, docs/36, and others — every "REJECT — TEST window fails" verdict in this project's history). A window that has been used repeatedly to decide which changes to keep is no longer a clean measurement of forward edge — it's partially a number you selected *toward*. The validator script itself, unprompted, calls this config's TRAIN/TEST relationship "UNSTABLE" and says a pass on either window alone "is not reliable evidence of a real edge."

## So: does it work?

**Not proven. Leaning no, as currently built.**

What *is* real, independently of the TEST-window contamination problem: the entry-admission threshold has permutation-tested signal (shuffling RS labels collapses performance to breakeven — that's a clean, non-contaminated test, done once, not re-run 15 times). So there is a real, non-random mechanism in here. What's not proven is that the *whole system* — sizing, rotation, universe, regime gating, all stacked together — has edge that survives an honest, chronological, never-peeked-at forward period. The one time this exact config was asked to prove that (the run above), it failed the first three years and only "worked" in the window that had already been used to pick it.

## What now

Not more backtesting. Backtesting this history further just repeats the same contamination — there is no unexplored data left in 2022-2026 that hasn't already been used to make a keep/reject call at least once.

The only test left that isn't contaminated: **freeze the config today, stop making any further TEST-window-driven changes, and track everything forward from 2026-07-30 as genuinely blind data.** That's the actual 2022 experiment, just run starting now instead of retroactively. It needs real time to mean anything — a few weeks tells you nothing, a few quarters starts to. Paper mode is already running; the only change is discipline: no parameter changes justified by "TEST window improved," full stop, until there's a real forward track record independent of this dataset.

If in six months to a year the frozen config is still profitable on data none of these decisions ever touched, that's the first genuinely trustworthy "yes." Until then, the honest answer to "does this work" is: **it has a real mechanism, an unproven system, and a lucky-or-real recent window we can't yet tell apart.**

## Addendum 2026-07-30: the live default's own gate-PASS is stale

The numbers above (TRAIN/TEST/FULL) were run against `ENTRY_MODE=PURE_RS`, the actual live default (`config/settings.py:142`). Tracing why that's the default: on 2026-07-21, an 8-arm entry-gate ablation (docs/34) found the old strict-AND gate (RS + ADX + trend-confirm + breakout + SuperTrend, all required) was the **worst of 8 arms tested — worse than random buying** on the 504-symbol universe. `PURE_RS` (RS-threshold + safety checks only, all trend/momentum confirmation gates dropped) won that comparison by +24pp CAGR full-window and cleared `robustness_gate.py` cleanly, so it was deployed.

That gate-PASS was measured **one day before** the brokerage/STT charges-model bug fix (b70335f, 07-22), which dropped full-window CAGR project-wide by ~27pp. `PURE_RS` was never re-gated after that fix. The TRAIN/FULL numbers in this doc — both negative — are that overdue re-check, and they say the currently-deployed entry logic does not clear its own bar under honest costs. It has not been reverted or re-validated since.

**Practical effect on "what now"**: the frozen-config forward track described above is still the right move, and it should be understood as tracking a lever whose own justification is currently unverified, not a lever that's already proven itself twice.

## Addendum 3 (same day): re-validation run — `PURE_RS` vs `FULL`, current honest costs

`robustness_gate.py --env ENTRY_MODE=FULL` (baseline=live `PURE_RS`, candidate=old strict-AND `FULL`):

```
TRAIN: baseline -1.10%/Sharpe 0.08/FAIL   candidate -6.08%/Sharpe -0.30/FAIL
TEST:  baseline +41.64%/Sharpe 1.58/PASS  candidate -12.03%/Sharpe -0.56/FAIL
FULL:  baseline -4.64%/Sharpe -0.13/FAIL  candidate -9.67%/Sharpe -0.54/FAIL
Stress: crash_v_recovery      base 9.29%/PF1.33  cand 10.62%/PF1.44  (candidate better)
        extended_bear_grind   identical both arms
        prolonged_sideways_chop base 6.88%/PF1.18 cand 22.02%/PF1.64 (candidate better)
        gap_down_bleed        identical both arms
VERDICT: REJECT candidate (FULL) — TEST-window Sharpe/PF both far worse than baseline.
```

**Two things confirmed, one thing newly revealed.**

1. Confirmed: reverting to `FULL` would be strictly worse. `PURE_RS` remains the better of the two known options — docs/34's directional call survives the honest-cost re-check.
2. Confirmed: `PURE_RS` still does not clear its own absolute bar. TRAIN and FULL are still negative — this is not "best-of-two = proven," it's "best-of-two, and both are unproven."
3. **New**: the two arms aren't uniformly ranked — they trade off by regime. `FULL` (the trend/ADX/SuperTrend-gated version) is *better* than `PURE_RS` in both stress scenarios that involve a real trend regime (crash-recovery, sideways chop), and only loses badly in the actual TRAIN/TEST calendar windows, which were dominated by a strong directional run. That's consistent with trend-confirmation doing its job in choppy/reversal conditions but vetoing real, profitable RS-qualified entries during a strong trend — a hard AND gate can't tell those apart, it applies the same veto everywhere. A partial-consensus/weighted gate (this session's other open thread) is a plausible way to keep the chop-protection without killing trend-period trades, and this result is direct evidence for trying it, not just a guess.

## Addendum 4 (2026-07-30, later same day): bootstrap significance check on the TEST-window trades

Part of the production-readiness plan (`docs/39`) called for a cheap, honest fragility probe on the TEST-window result that's driving most of the optimism: is +41.64% CAGR / Sharpe 1.58 actually distinguishable from noise given only N=133 trades, or is it well within what chance alone produces at this sample size? This test can only downgrade confidence or leave it unchanged — it reuses the same already-used TEST-window trades, so it cannot manufacture new proof.

Regenerated the TEST-window trade log (`outputs/backtest_trades.csv`, current live config, cached data, same 133 trades as every other TEST-window number in this project) and bootstrapped the per-trade net P&L (20,000 resamples):

```
N trades: 133
Mean net_pnl per trade: ₹314.81
Bootstrap 95% CI on mean net_pnl/trade: [₹-142.53, ₹929.28]
Fraction of resamples with mean <= 0: 11.24%
Bootstrap p-value (H0: true mean = 0): 0.246
```

**This is a real downgrade, not a null result.** The 95% confidence interval on the average trade's profitability spans zero. Roughly 1 in 9 resamples of these same 133 trades comes out net-losing. A p-value of 0.246 is nowhere close to any conventional significance threshold (0.05) — conventionally, this result would be reported as "not statistically distinguishable from a coin flip with a slight positive lean," not as evidence of a real edge.

**What this changes**: it doesn't overturn anything — the verdict was already "leaning no," not "yes." But it removes a possible objection to that verdict (*"the TEST window's numbers look too good to be luck"*) — they don't. A 133-trade sample producing a p=0.246 mean is exactly the kind of result you'd expect from a strategy with little-to-no real edge that got a favorably-timed few years. Combined with the earlier finding that TRAIN and FULL are both negative under honest costs, there is now no window — TRAIN, TEST, or FULL — that survives rigorous scrutiny on its own terms. TEST merely survives the loosest scrutiny (a directional CAGR/Sharpe readout); it does not survive a significance test at the trade level.
