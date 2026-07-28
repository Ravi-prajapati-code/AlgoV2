# Universe Cap (Top-50 by Turnover) — REJECTED

## Hypothesis

Restrict the tradeable universe to the top 50 symbols by pre-start average
turnover (a liquidity/size proxy — no market-cap field exists in the
historical dataset), applied statically across the whole backtest window
(no time-variation, so it's testable on the full 2022-2026 history without
touching point-in-time universe-membership logic, which only has ~3 weeks
of valid tracking since 2026-07-06). Motivation: fewer, larger/more liquid
names might mean a safer, more defensible portfolio, especially in bear
markets.

## Implementation

- `config/settings.py`: `UNIVERSE_CAP_SIZE` (default `0` = disabled).
- `main.py` (`cmd_backtest`): when set, ranks the resolved symbol list once
  by mean(close × volume) over the pre-`start` warmup window only (never
  touches TRAIN/TEST-window data for ranking), keeps top N. `backtest/engine.py`
  untouched — universe stays frozen at backtest-start exactly as before,
  just a shorter list.

## Gate Result — `UNIVERSE_CAP_SIZE=50`, isolation

TEST window (2025-01→2026-06-04): baseline CAGR +41.64%/Sharpe 1.58/PF
2.11 (N=133) → candidate CAGR **-15.91%**/Sharpe **-1.18**/PF **0.72**
(N=98).

All 4 stress scenarios FAIL, 2 with CAGR sign-flips:

| Scenario | PF base→cand | CAGR base→cand |
|---|---|---|
| crash_v_recovery | 1.33→0.43 | +9.29%→**-8.59%** |
| extended_bear_grind | 0.99→0.12 | -7.66%→-42.65% |
| prolonged_sideways_chop | 1.18→0.53 | +6.88%→**-14.33%** |
| gap_down_bleed | 1.02→0.07 | -11.01%→-49.10% |

**VERDICT: REJECT — 8 gate failures**, none marginal.

## Why

N drops 133→98 in TEST — cutting to top-50-by-turnover starves the
strategy of trade opportunities rather than de-risking it. This is the
same structural mechanism already found in
[[liquidity_threshold_experiment_20260710]] (tightening liquidity
thresholds at p25/p10 REJECTED, "structural not calibration") — a
turnover/size-based universe restriction removes exactly the
smaller/newer names this momentum strategy depends on for its edge, and
does so on every stress signature at once, not selectively in bear
conditions as hoped.

Given the size of the failure margin, cap=75/cap=30 sweeps were not run —
the opportunity-starvation signature would very likely reproduce at any
cap size in this range. Closed without further sweeping.

## How to apply

Don't propose universe-size-restriction levers ranked by
turnover/liquidity/size proxies for this strategy — 2/2 dead now, same
mechanism both times. `UNIVERSE_CAP_SIZE` flag stays in the codebase
(disabled, default `0`) as a permanent record, per the
[[rejected_forever_doc_20260710]] convention — don't remove the code, don't
revisit the idea without a genuinely different selection signal (not
turnover/liquidity magnitude).
