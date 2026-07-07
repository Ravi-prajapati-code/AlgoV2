# 04 — Backtest System

## Engine (`backtest/engine.py`, 895 lines)

`BacktestEngine.run()` is a self-contained simulation loop — it does **not** call
`portfolio/manager.py`; it reimplements order execution, sizing, and drawdown-tiering inline. Key
pieces:
- `_precompute_all()` — precomputes indicators for the whole run up front (same Cutler's-RSI /
  simple-rolling-ATR convention as production, see `03_Strategy.md`).
- `_entry_fill_price()` — models fill price on the signal day.
- A hybrid regime state machine mirroring the live runner's BULL/BEAR branching, using the same
  `strategy/regime.py::detect_regime()` (post the 2026-07-02 fix that made backtest and live use
  the identical regime signal — previously backtest used a raw EMA100 crossover instead).
- A 3-tier drawdown-reduction sizing model (100%/50%/25% at `DRAWDOWN_REDUCE_SIZE_PCT` and
  `× DRAWDOWN_REDUCE_TIER2_MULT`) — this is duplicated, not shared, with `portfolio/manager.py`'s
  live version of the same idea; see `09_Open_Questions.md` for the divergence from the dead
  `risk/manager.py`'s different 2-tier version of the same constants.
- `BacktestResult` — the output container consumed by `backtest/metrics.py` and
  `backtest/reporter.py`.

## Metrics (`backtest/metrics.py`, 203 lines)

`calculate_metrics()` computes CAGR, Sharpe, Sortino, MDD, win rate, profit factor, etc. Uses
**population variance (÷N)** for Sharpe/Sortino. There is a key-parity risk: an empty trades list
and a full trades list don't necessarily return dicts with identical key sets, which can surface as
a `KeyError` downstream if a caller assumes a fixed schema regardless of trade count.

**Methodology inconsistency** (see `06_Validation.md`): `scripts/walk_forward.py`'s
`_run_backtest_window()` instead uses Python's `statistics.stdev()` (sample variance, ÷N-1) for its
own Sharpe calculation. The "official" `main.py backtest` Sharpe and the automated monthly
decay-monitor's Sharpe are therefore not exactly reproducible against each other for the same
equity curve — a small but real methodology drift between two places that both claim to report
"Sharpe."

## Reporting (`backtest/reporter.py`, 459 lines)

Formats `BacktestResult` into human-readable output. `stop_loss_2pct_est` is a **documented-fake
field** (an estimate, not a measured value — labeled as such in the code, not a bug, but a trap for
anyone who assumes every reported field is empirically measured). `save_daily_scan_log` uses a
stale schema relative to current usage.

## Slippage (`backtest/slippage.py`, 133 lines) — confirmed dead

`apply_slippage()` has zero callers anywhere in the codebase. `simulate_partial_fill()` is imported
somewhere but never actually invoked. The backtest engine does not currently model slippage or
partial fills at all, despite this module existing to do exactly that.

## Charges (`charges/calculator.py`, 128 lines)

Models the full Upstox NSE delivery-charge schedule (brokerage, STT, exchange charges, GST, SEBI
charges, stamp duty) used to compute realistic net P&L in both backtest and live reporting.
Confirmed correct on the GST-excludes-STT/stamp-duty point (a common charge-modeling mistake this
codebase avoids).

## Research scripts (`scripts/`)

A dozen backtest-adjacent research tools, all confirmed present and independently exercised by the
Partition A research pass: `correlation_analysis.py`, `feature_importance.py`,
`trade_attribution.py`, `early_heat_experiment.py`, `out_of_sample_validator.py`,
`robustness_gate.py`, `stress_test_scenarios.py`, `walk_forward.py`, `ensemble_eval.py` (has a
stale hardcoded `END_DATE` — needs manual bumping), `scenario_runner.py` (its `THRESHOLDS` dict is
duplicated from elsewhere, a drift risk), `verify_indicators.py` (the source of the Cutler's-RSI
divergence finding above), `universe_audit.py` (depends on a stale CSV with no freshness check).
Full research history and how these tools were used is in `05_Research.md`.

## Test coverage (`tests/`)

Six test files: `test_backtest.py`, `test_indicators.py`, `test_signals.py`, `test_portfolio.py`,
`test_charges.py`, `test_manager_execution.py`. The last is notable for being meticulously tied to
four dated, `git log`-verified live-manager incident-fix commits (`bcf4441`, `162726b`, `c5460f6`,
`96df9bd`) — i.e. it's regression coverage written specifically to pin down real production bugs
after they were fixed, not general-purpose coverage. This is a good pattern; new incident fixes to
`portfolio/manager.py` should follow the same convention of adding a dedicated regression test.
