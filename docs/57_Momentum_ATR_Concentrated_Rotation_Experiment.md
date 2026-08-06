# 57 — Momentum×ATR Concentrated-Rotation Experiment

Status: **research only, standalone, not wired to production**. Separate
score formula from AlgoV2's live composite_rank (RS-based); separate
execution engine; not called by `runner/daily_runner.py` or
`backtest/engine.py`. No live/backtest code was modified for any of this.

Code: `scripts/momentum_atr_experiment/engine.py` (generalized simulator),
`scripts/momentum_atr_experiment/run_experiments.py` (driver),
`scripts/momentum_atr_experiment/legacy/` (original one-off scratch
scripts, unmodified, kept as restore point).

## Rule set (user-specified, not derived from AlgoV2's research process)

```
momentum  = (close - SMA50) / SMA50 * 100
ATR_PCT   = (ATR14 / close) * 100          (Wilder EMA, alpha=1/14)
score     = momentum * ATR_PCT, forced to 0 where momentum <= 0
```
Top-N by score held, equal-split + max-cash-deploy cascade, 9:16-minute
open fills, no brokerage/slippage. -3%/+3% same-day swap rule. Exit rule
and portfolio size varied per experiment below. Universe: 112 symbols with
full 783-day 9:16 minute coverage (2022-11-01 → 2026-05-07).

## Baseline (docs-internal reference, from the prior session's attribution run)

FULL (N=3, immediate rank>3 exit, daily rotation): **59.5% CAGR, -25.2% DD,
1211 trades, Sharpe 1.63**. Nifty 50 buy&hold same window: 8.7% CAGR.
Decomposition: 8.7% (market) → +4.4pp (this 112-symbol universe) → +10.4pp
(mechanical shape: concentration + momentum-positive filter + rotation,
random stock pick) → +36.0pp (real ranking vs random, z≈10).

## Experiment A — does concentration or ranking create the edge?

Portfolio size swept 1/2/3/5/10, real score vs. random-rank control
(15 seeds) at each size, everything else held fixed.

| N | Real CAGR | Real DD | Real trades | Random mean CAGR (σ) |
|---|---|---|---|---|
| 1 | 90.7% | -34.8% | 273 | 69.4% (σ=17.3)* |
| 2 | 82.8% | -26.7% | 627 | 21.6% (σ=6.3) |
| 3 | 59.5% | -25.2% | 1211 | 23.0% (σ=3.6) |
| 5 | 48.6% | -22.4% | 2293 | 24.9% (σ=3.8) |
| 10 | 36.5% | -22.7% | 3947 | 25.6% (σ=3.6) |

\* N=1 random is a known artifact, not a real signal: with only 1 slot, the
"immediate rank>N exit" rule requires the held name to literally be the
random #1 pick again every single day to survive — near-impossible with a
large eligible pool — so N=1-random forces near-daily forced rotation
through the momentum-positive filter, an unstable, high-variance regime
different in kind from N=1-real (which is stable because real momentum
scores are autocorrelated day to day). The 17.3pp std vs. 3.6–6.3pp
elsewhere confirms this cell is noise-dominated, not evidence either way.

**Verdict: ranking creates the edge, not concentration.** Random-rank CAGR
is flat (~22–26%) from N=2 through N=10 regardless of how concentrated the
random portfolio is — pure concentration into randomly-chosen
momentum-positive names buys nothing. Real-rank CAGR falls steadily as N
grows (90.7%→36.5%) precisely because diluting into more names means
including progressively lower-quality (lower-score) picks. The real-vs-
random gap is largest where ranking quality has the most room to matter
(N=2: +61pp) and shrinks toward the diluted end (N=10: +11pp). Concentration
amplifies the ranking edge; it doesn't create it. Cost: N=1 has the worst
drawdown (-34.8%) of any real config — concentration is a pure risk
amplifier, symmetric in both directions.

## Experiment B — is constant (daily) rotation necessary?

N=3 fixed. Exit rule varied: current (immediate, rank>3), loose-rank
(exit only if rank>6), time-buffered (exit after 3 consecutive days
outside top-3).

| Exit rule | CAGR | DD | Trades | Sharpe |
|---|---|---|---|---|
| Immediate rank>3 (current) | 59.5% | -25.2% | 1211 | 1.63 |
| 3 consecutive days outside top-3 | 50.2% | -24.5% | 677 | 1.43 |
| Rank>6 | 42.6% | -25.0% | 746 | 1.34 |

**Verdict: rotation frequency is doing real work, not just adding
turnover.** Both looser rules reduce CAGR and Sharpe versus daily/immediate
exit, while drawdown stays roughly flat across all three (~-25%, if
anything marginally better with looser rules) — i.e., the extra rotation
in the current rule buys real incremental return without a matching risk
cost in this sample. Secondary finding: a short time-buffer (3 consecutive
days) beats an equivalent-turnover rank-based slack (rank>6, actually
*more* trades for *less* return) — transient day-to-day score jitter is
better tolerated with a brief grace period than with a looser absolute
rank cutoff, which lets genuinely decaying names linger too long.

## Experiment C — is daily rotation itself the edge, or would monthly suffice?

N=3, reassess every 30 trading days (freeze in between) vs. daily.

| | CAGR | DD | Trades | Sharpe |
|---|---|---|---|---|
| Daily rotation (current) | 59.5% | -25.2% | 1211 | 1.63 |
| Freeze 30 trading days | 35.7% | -35.5% | 170 | 1.13 |

**Verdict: rotation is the edge, not just cosmetic churn.** Performance
does not "barely change" — it collapses on both axes simultaneously: CAGR
-23.8pp *and* drawdown 10.3pp worse, for 86% fewer trades. Letting a
selection ride for a month lets decaying names decay further before being
cut, hurting return and risk at the same time. This is the sharpest of the
three experiments and corroborates B's direction at an extreme setting.

## What this does NOT establish

Single historical realization (2022-11 → 2026-05, an unusually strong bull
window; Nifty +8.7%/yr compounded over it). No brokerage/slippage modeled
— Experiment A's high-turnover configs (N=5/10: 2293–3947 trades) and B's
immediate-exit config (1211 trades) would be disproportionately hurt by
real costs relative to the looser variants; the ranking between "current"
and "looser" could compress or flip once costs are added. No TRAIN/TEST
split, no bear/flat-regime test, no out-of-sample check. This is causal
attribution within one run, not a production readiness verdict — per the
charter, none of this is grounds to change how AlgoV2 actually trades
without a full propose→adversarial-review cycle and out-of-sample
validation, which has not been done here and was explicitly not the
purpose (user's instruction: "Don't optimize. Understand.").
