# 58 — Standalone Robustness Gate for the Momentum×ATR Experiment

Status: **research only, standalone**, same status as docs/57. Continues
that experiment; not wired to production.

## Why this exists, and why it isn't `scripts/robustness_gate.py`

The user asked to run "our script" (`scripts/robustness_gate.py`) against
every strategy variant from docs/57. That script drives AlgoV2's
production `backtest/engine.py` through `--env KEY=VALUE` overrides that
must correspond to real `os.getenv("KEY", default)` call sites in
`config/settings.py`. It uses the live universe (`data.universe.
get_all_symbols()`) and the live RS-based composite score.

None of `portfolio_size`, `exit_mode`, `exit_threshold`, `freeze_days`
exist as env vars anywhere in production config, and the momentum×ATR
experiment doesn't call `backtest/engine.py` at all — it has its own
112-symbol universe, its own SMA50-momentum × ATR% score, and its own
9:16-minute-fill engine (`scripts/momentum_atr_experiment/engine.py`).
There is nothing for `robustness_gate.py` to compare. Running it as
literally requested is not possible; forcing a fit would have produced a
fake pass/fail. Built `scripts/momentum_atr_experiment/gate.py` instead —
same purpose, same class of pass/fail rules, run against this
experiment's own engine and data.

## Method

**Baseline**: portfolio_size=3, exit_mode=immediate, freeze_days=None —
the current/default config used throughout docs/57.

**Candidates**: every variant actually run in docs/57 (A_TOP1, A_TOP2,
A_TOP5, A_TOP10, B_RANK_GT6, B_CONSEC3, C_FREEZE30). TOP3/immediate is
identical to baseline and wasn't re-tested.

**Rule 1 — TRAIN/TEST** (chronological split, no shuffle: first 70% of
trading days = TRAIN, last 30% = TEST, each window run from a fresh
₹100,000 start): candidate TEST Sharpe ≥ 0.90× baseline TEST Sharpe,
candidate TEST profit factor ≥ 0.90× baseline TEST PF (10% relative
tolerance, same magnitude as `robustness_gate.py`'s `OOS_TEST_TOLERANCE`),
and TEST CAGR may not flip negative if baseline TEST CAGR is positive.

**Rule 2 — stress**: 4 synthetic scenarios reused verbatim from
`scripts/stress_test_scenarios.py` (same macro daily-return paths, same
per-symbol noise model): `crash_v_recovery`, `extended_bear_grind`,
`prolonged_sideways_chop`, `gap_down_bleed`. A synthetic OHLCV tail is
appended after each symbol's real last close (single seed=7); SMA50/ATR/
score are recomputed over the combined real+synthetic series so the
50-day warmup is genuine. Approximation, stated explicitly: the synthetic
tail has no minute data, so its 9:16 fill is proxied by the synthetic
daily open (real TRAIN/TEST windows use the true 9:16 print). This proxy
applies identically to baseline and every candidate, so it doesn't bias
the comparison. Candidate profit factor must not drop more than 0.10
(absolute) below baseline PF in any scenario — `STRESS_PF_DROP_MAX`, same
value `robustness_gate.py` uses.

Verdict PASS only if every rule passes; REJECT otherwise, with the
specific failing checks listed.

Code: `scripts/momentum_atr_experiment/gate.py`. Raw output:
`scripts/momentum_atr_experiment/gate_results.json`.

## Results

Baseline: TRAIN CAGR 52.99% / Sharpe 1.42 / PF 1.85. TEST CAGR 78.89% /
Sharpe 2.53 / PF 1.578.

| Candidate | TRAIN CAGR | TEST CAGR | TEST Sharpe | TEST PF | Verdict |
|---|---|---|---|---|---|
| A_TOP1 | 82.5% | 110.5% | 2.55 | 3.02 | **REJECT** |
| A_TOP2 | 79.8% | 86.3% | 2.42 | 2.53 | **REJECT** |
| A_TOP5 | 39.7% | 61.1% | 2.11 | 1.43 | **REJECT** |
| A_TOP10 | 38.1% | 32.2% | 1.43 | 1.06 | **REJECT** |
| B_RANK_GT6 | 32.9% | 61.0% | 2.10 | 1.65 | **REJECT** |
| B_CONSEC3 | 40.9% | 53.7% | 1.92 | 1.42 | **REJECT** |
| C_FREEZE30 | 27.2% | 26.7% | 1.25 | 1.39 | **REJECT** |

All 7 candidates reject — but not for the same reason, and not with the
same strength of evidence.

**A_TOP1 / A_TOP2 — reject on a single thin margin, otherwise dominate.**
Both beat baseline on every TRAIN/TEST metric (TOP1 TEST CAGR +31.6pp,
Sharpe +0.02, PF +1.44 vs baseline). The only failure for either is one
stress scenario, `prolonged_sideways_chop`: TOP1 PF 1.224 vs a required
1.302 (baseline 1.402, drop-max 0.10) — a real miss, but from a single
seeded synthetic draw on n=124 trades. This is the weakest evidence in
the whole gate; a different seed could plausibly flip it. Not treated as
a pass, since the rule is what it is, but flagged as materially different
in kind from the rejects below.

**A_TOP5 / A_TOP10, B_RANK_GT6 / B_CONSEC3, C_FREEZE30 — reject broadly.**
Each fails TEST Sharpe by 10%+ and fails 2-6 of the 4 stress scenarios,
consistent with docs/57's full-history verdicts (diluting past N=3 and
loosening/slowing rotation both cost real, not cosmetic, performance).
C_FREEZE30 is the worst on every axis, matching docs/57 Experiment C's
"rotation is the edge" conclusion. A_TOP10 fails all 4 stress scenarios
outright — the highest-turnover, most-diluted config is also the least
stress-robust.

## What this does and doesn't establish

Confirms docs/57's Experiment A/B/C conclusions hold up under an
out-of-sample split and synthetic stress, not just on the single
full-history run they were originally reported on. Does not establish
that baseline (or TOP1/TOP2) is production-ready: no brokerage/slippage
anywhere in this engine — TOP1's 273 real-history trades and baseline's
1211 are both cost-exposed in ways this gate doesn't price in, and the
ranking between them could compress or reverse once costs are added. The
stress scenarios use one seed each; the TRAIN/TEST split is a single
70/30 cut of one historical window (2022-11 → 2026-05, a strong bull
period) — this is a materially thinner robustness check than
`robustness_gate.py` runs for production candidates, which is expected
given it's testing a standalone research engine, not a production change.
Per the charter, none of this is grounds to change how AlgoV2 actually
trades.
