# Doc 40 — Signal-Level Entry Attribution: FULL vs PURE_RS

**Date**: 2026-07-31
**Script**: `scripts/signal_level_attribution.py`
**Data**: `outputs/signal_level_attribution.csv` (136,428 rows)

## Question

Prior work (`entry_attribution_suite_20260709`, docs/23 §XIV) showed FULL mode
(RS + trend/ADX/breakout/SuperTrend gate) underperforms both PURE_RS and
PURE_ADX_BREAKOUT single-factor arms in realized CAGR/Sharpe, and that
REVERSE_RS (worst-RS-first, same gates as FULL) was the single best arm. That
result never explained *why* — and it's confounded, because realized trade
blotters under different ENTRY_MODEs are not a clean nested comparison once
`MAX_OPEN_POSITIONS` makes slots scarce: a slot PURE_RS spends on a
trend-failing name is a slot FULL spent on something else. Aggregate
CAGR/Sharpe tables can't separate "signal quality" from "portfolio dynamics."

This experiment answers it at the signal level instead: for every symbol-day
that clears the PURE_RS gate (RS rank ≥ threshold + safety checks, no
portfolio/slot allocation involved), does the subset that *also* clears
FULL's additional trend/breakout gate go on to have **better** forward
returns (FULL correctly filters out future losers) or **worse** forward
returns (FULL is filtering out future winners, admitting later-stage /
already-run names instead)?

## Method

`BacktestEngine._precompute_all()` run standalone (no `.run()`, no slot
allocation) across 2022-01-01→2026-06-04. For every (date, symbol) with
`rs_rank >= RS_THRESHOLD` and passing safety checks (extension cap, min
turnover) — i.e. every PURE_RS-eligible symbol-day — gate logic mirroring
`strategy/entry.py::check_entry` is evaluated as flags instead of
short-circuiting:

- `breakout_pass` — within `BREAKOUT_PCT` of 20d high (VCP branch: see
  caveat below)
- `trend_strength_pass` — EMA alignment + SuperTrend up + ADX ≥ threshold +
  MACD (if enabled)
- `full_trend_pass` — both of the above AND the (structurally inert, see
  caveat) 200-EMA gate — this is exactly what FULL adds on top of PURE_RS

Forward returns computed close-to-close at +5/+10/+20/+60 trading sessions
from the signal date, split TRAIN (2022-01-01→2024-12-31) / TEST
(2025-01-01→2026-06-04) per the project's standard window.

**Fidelity caveat found while building this**: `backtest/engine.py`'s
indicator precompute never sets `vcp_detected`/`vcp_pivot` or `ema_200` —
those are live-only (`indicators/composite.py`). So in every backtest ever
run (including this one, the original entry_attribution_suite, and every
robustness_gate run), `breakout_pass` has always taken the "standard 20d-high"
branch, VCP entries have never been simulated, and `TREND_GATE_200_ENABLED`
is a structural no-op regardless of its live setting. Not a bug (sane
defaults, no crash) but a real live/backtest gap — VCP is untested. Out of
scope for this doc; flagging for a future session.

## Result

| Window | Gate | Group | n | 5d mean / WR | 10d mean / WR | 20d mean / WR | 60d mean / WR |
|---|---|---|---|---|---|---|---|
| TRAIN | full_trend_pass | **PASS** (FULL admits) | 29,883 | +0.68% / 53.2% | +1.46% / 55.0% | +2.96% / 57.6% | +8.98% / 63.5% |
| TRAIN | full_trend_pass | **FAIL** (FULL rejects) | 62,662 | +1.03% / 54.9% | +1.93% / 56.1% | +3.71% / 58.0% | +10.40% / 61.7% |
| TEST | full_trend_pass | **PASS** (FULL admits) | 14,116 | -0.12% / 48.2% | -0.12% / 48.2% | +0.05% / 49.1% | +0.76% / 49.7% |
| TEST | full_trend_pass | **FAIL** (FULL rejects) | 29,767 | +0.20% / 50.6% | +0.44% / 51.0% | +0.93% / 51.8% | +2.74% / 54.0% |

Same direction holds for both sub-gates individually (`breakout_pass`,
`trend_strength_pass`) in both windows, every horizon — see CSV / script
output for full breakdown. No cut flips sign anywhere.

**In every window, every horizon, every sub-gate: the pool FULL rejects
outperforms the pool FULL admits — on mean forward return AND win rate.**
n is large (13k-63k per cell), TEST 60d win rate is 49.7% (FULL-admitted) vs
54.0% (FULL-rejected), a 4.3pp gap in the out-of-sample window.

**Caveat — row count overstates independence.** The 136k rows are
symbol-days, not independent observations: a stock eligible 40 consecutive
days contributes 40 rows with heavily overlapping 60d forward windows, and
the "2 windows × 4 horizons × 3 gate cuts all agree" framing counts nested,
correlated cells as if they were separate replications (~1 finding shown 24
ways). Deduped to one observation per distinct symbol (mean fwd_20d return
per symbol, paired PASS-vs-FAIL):

| Window | distinct symbols | mean(FAIL−PASS) | median | paired t | p |
|---|---|---|---|---|---|
| TRAIN | 453 | −0.31% | +0.32% | −1.12 | 0.26 (not sig.) |
| TEST | 413 | +1.95% | +2.08% | 6.76 | <0.0001 |

TRAIN's row-level signal **does not survive** deduping — at symbol level it's
noise (p=0.26, near coin-flip on which side wins per symbol). TEST survives
with ~400 independent symbols, which is the more relevant window anyway
(current gate thresholds were not tuned on TEST). Effect size (4.3pp win
rate, ~2%/20d mean gap) is real but modest, not dramatic.

## Interpretation

Two competing readings, not one settled mechanism:

1. **Momentum-maturity (original reading)**: FULL's gate requires
   already-confirmed trend (new high + ADX/SuperTrend/EMA aligned), selecting
   later-stage names with less room left to run.
2. **Short-horizon mean reversion (alternative, not ruled out)**: FULL-pass
   names sit at a 20d high *by construction*; FULL-fail names are mid-pullback
   below their high. Raw forward returns from those two starting points can
   just be measuring generic short-horizon mean reversion — a name near its
   high reverting slightly, a name below its high drifting back up — which
   produces the same signature regardless of trade quality, and has nothing
   to do with FULL vs PURE_RS specifically.

This experiment does not distinguish between the two. Both are consistent
with the data as measured.

**Scope limit**: neither PURE_RS nor FULL actually trades this raw
signal-level pool — both rank by RS and take the top `MAX_OPEN_POSITIONS`,
both exit on MOMENTUM_DECAY. Raw 60d forward return of the eligible pool is
not the same as what either live strategy captures. The 2026-07-09
realized-trade-blotter result (FULL underperforms PURE_RS/REVERSE_RS in
actual CAGR/Sharpe) remains the decision-relevant test; this doc adds
possible color/mechanism, not a superior or independent confirmation of it.

## Verdict

**Directionally consistent with, not independent confirmation of, the live
PURE_RS default being reasonable** — TEST-window gap survives symbol-level
dedupe (p<0.0001, ~400 independent symbols), TRAIN-window gap does not
(p=0.26, likely row-autocorrelation artifact). No config change from this
doc — PURE_RS already is what's live. Treat "FULL rejects future winners"
as a hypothesis with mean-reversion as a live alternative explanation, not a
closed question. Do not re-add the trend/breakout gate as a hard entry
filter without addressing this finding, but also don't cite this doc alone
as proof PURE_RS is structurally correct — cite the 2026-07-09 realized
result for that, this doc for mechanism color only.

**Follow-up flagged, not done here**: the VCP/ema_200 backtest fidelity gap
above is real and separate — worth a dedicated session if VCP entries are
ever going to be evaluated pre-live.
