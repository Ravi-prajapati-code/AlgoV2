# 63 — Main Strategy Peak-Value Contamination Bug (found + fixed 2026-09-07)

## Summary

`portfolio/manager.py::PortfolioManager._load_state()` computed the main
strategy's `peak_value` — the number that feeds `portfolio/risk.py::
can_open_new_trades()`'s drawdown circuit breaker, the graduated size-cut
logic, and the rank-replacement risk gate — as `max(strategy_value)` over
**all** snapshot history, unfiltered. On 2026-09-07 this was found to have
`can_open_new_trades()` returning `False` on every real run: a permanent
false 43% "drawdown" against a stale 2026-07-21 peak (₹96,520.32) that
predates a real data-contamination window.

## Root cause

Main and momentum_atr shared one real Upstox broker account through
2026-09-02 (main switched to paper-only 2026-09-03). While main ran live,
`portfolio/manager.py:136` set `self.cash = self.broker.get_available_cash()`
— the **full shared account balance**, not scoped to main's own capital.
Every momentum_atr buy/sell therefore swung main's reported cash, and hence
its `strategy_value` (`cash + strategy-origin positions`), with nothing to
do with main's own P&L. Evidence: single-day `strategy_value` swings like
₹12,295 → ₹58,132 → ₹12,295 through August, with zero matching real trading
activity of that magnitude in main's own trade log.

docs/59's 2026-08-10 scoped split fixed *position*-level origin filtering
(`open_positions` filtered to `origin == "strategy"`) but never scoped
*cash* — this bug predates that split and is a separate, deeper issue.

Since 2026-09-03 (paper-only), `self.cash` comes from `_load_cash_from_db()`
— main's own isolated ledger — so `strategy_value` from that date forward
is finally a clean, valid series.

## Where it was first noticed

The dashboard's Risk Monitor page (`dashboard/views/risk_monitor.py`)
showed "KILL-SWITCH ACTIVE — drawdown ≥ 18%. New BUYs blocked." Initial
read was that this was a display-only artifact (dashboard reconstructs
peak read-only, per its own docstring). Deeper check found the *real* live
gate (`portfolio/manager.py` → `portfolio/risk.py::can_open_new_trades`)
uses the exact same unfiltered `max(strategy_value)` computation — so the
dashboard was accurately reporting a real, live functional bug, not
inventing one.

## Fix

Added `MAIN_STRATEGY_PAPER_SINCE = date(2026, 9, 3)` to `config/settings.py`
— the first date main's cash is genuinely isolated. Filtered every site that
reconstructs peak/drawdown from `portfolio_snapshots` to `s.date >=
MAIN_STRATEGY_PAPER_SINCE`:

- `portfolio/manager.py::_load_state()` — the real live gate (fixes actual
  trading behavior, not just display)
- `dashboard/views/risk_monitor.py::_main_risk_state()` — the dashboard
  reproduction
- `main.py::cmd_risk_report()` — the manual CLI diagnostic

Verified on production: post-fix, `peak == current == 54,950.15`, 0%
drawdown, `can_open_new_trades()` returns `True`. Pre-fix, same server data
gave `can_open_new_trades() -> False, "Portfolio drawdown circuit breaker:
43.1% (limit 18%)"`.

## Regression test

`tests/test_portfolio.py::TestPeakValueExcludesPreLiveSharedCashHistory` —
seeds a contaminated pre-cutoff spike alongside a clean post-cutoff series
in a temp DB, asserts `PortfolioManager.peak_value` reflects only the
post-cutoff data, and asserts `can_open_new_trades()` allows trading given
that peak. Confirmed to fail against the pre-fix code (`git stash` check):
`peak_value == 96520.32` instead of `54950.15`.

## Why this matters going forward

Main strategy is paper-only, so no real capital was at risk from the block
itself — but this is exactly the kind of bug the Repository Integrity role
exists to catch: a metric computed correctly in isolation (per-snapshot
`strategy_value`) became silently invalid the moment its *input* (shared
broker cash) stopped meaning what the code assumed it meant, and nothing
enforced that assumption. `MAIN_STRATEGY_PAPER_SINCE` is now the single
source of truth for "when did main's own accounting become trustworthy" —
any future code touching `portfolio_snapshots` history for peak/drawdown
purposes must filter through it, not re-derive its own cutoff.
