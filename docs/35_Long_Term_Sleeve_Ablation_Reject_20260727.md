# 35. Long-Term Sleeve Ablation — REJECT (2026-07-27)

## Origin

New buy-and-hold sleeve built alongside the swing engine: a 20% capital
carve-out (`LONG_TERM_CAPITAL_RESERVE_PCT`) trading its own pool, max 5
positions, isolated selection/exit state
(`strategy/long_term_selection.py`, `portfolio/long_term_reserve.py`).
Entry: RS-rank ≥ 85 sustained 2 consecutive monthly qualifications
(`LONG_TERM_MIN_RS_RANK` / `LONG_TERM_SUSTAIN_MONTHS`). Exit: close below
EMA200 confirmed for 2 consecutive monthly calls
(`LONG_TERM_TREND_EXIT_EMA` / `LONG_TERM_TREND_EXIT_CONFIRM_MONTHS`). No
RSI/momentum-decay churn exit — deliberately the sleeve's only sell
trigger. Shipped off by default (`LONG_TERM_REBALANCE_ENABLED=false`).
Validated with a standalone historical validator
(`scripts/backtest_long_term_sleeve.py`) that reuses the live
fetch/RS/indicator pipeline and `backtest.metrics.calculate_metrics`
without touching `backtest/engine.py`.

## Gate result

`scripts/backtest_long_term_sleeve.py`, ₹20,000 reserve (₹1,00,000 × 20%):

| | TRAIN (2022-01–2024-12) | TEST/OOS (2025-01–2026-06) | FULL (2022-01–2026-06) |
|---|---|---|---|
| CAGR | +54.09% | **-2.85%** | +31.56% |
| Max Drawdown | 16.31% | 26.70% | 29.84% |
| Sharpe | 7.11 | **-0.13** | 5.19 |
| Win rate | 40.0% | 40.0% | 50.0% |
| Profit factor | 10.50 | 1.97 | 70.49 |
| Trades | 5 | 5 | 10 |

**VERDICT: REJECT** — sign flip TRAIN→TEST (CAGR +54%→-2.85%, Sharpe
7.11→-0.13). Same signature as `ENTRY_MODE=SURVIVAL_RANK` (docs/24):
spectacular TRAIN number that doesn't survive OOS. The FULL-window numbers
(Sharpe 5.19, PF 70.49) are themselves a red flag independent of the
split — with only 5 trades per window, both ratios are dominated by one
or two picks and carry no statistical weight either direction.

## What this means

RS-rank magnitude was already shown to carry no directional predictive
value on this universe (`REVERSE_RS` beat `FULL` in the original 8-symbol
test, `entry_attribution_suite_20260709`; reconfirmed at scale in
docs/34). A bare `RS-rank ≥ 85` threshold with no other qualifying signal
inherits that same lack of edge — it's selecting on a metric that doesn't
predict returns, just dressed up in a slower cadence (monthly, 2-month
sustain) that happens to produce a large but noisy TRAIN result from a
handful of trades. Slower cadence does not fix a signal that has no edge
at any cadence.

## Deployment

No change. `LONG_TERM_REBALANCE_ENABLED` stays `false`. Code kept
(flagged off) rather than reverted, per this repo's convention for
documented-REJECT levers (docs/24) — the modules are isolated (own state
files, netted-out reserve cash, no coupling into swing sizing/backtest)
so leaving them in place costs nothing and preserves the validator for
re-use if a genuinely RS-independent long-term selection signal ever
gets proposed.
