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

## Addendum (2026-09-07, same day): a second, deeper floor

Deploying the fix above and re-verifying live on production surfaced a
second, structurally identical bug in the same function. A duplicate real
position (GOLDBEES.NS, 175 shares, folded into `momentum_atr.db` in a prior
session but never removed from `trading.db`) was found and administratively
closed out of `trading.db` via `db.repository.close_position_and_save_trade`
(`exit_price == entry_price`, `net_pnl = 0` — pure bookkeeping correction,
no fabricated P&L). Re-running the live verification immediately after
showed `can_open_new_trades()` **still** blocked, at a worse 45%+
"drawdown" — the duplicate was not the cause.

Root cause: `portfolio/manager.py::_load_state()`'s no-broker (paper-mode)
branch unconditionally ran `self.peak_value = self.initial_capital` before
merging with snapshot history. `self.initial_capital` defaults to
`config.settings.INITIAL_CAPITAL` (100,000) — a figure inherited from when
main ran live against the *full* shared broker account, never re-baselined
after the 2026-09-03 paper cutover. Every real paper-mode cron run passes
`broker=None` (`runner/daily_runner.py`'s `live_mode` check), so this branch
is the one actually executing in production, always. Since main's real
isolated post-cutoff `strategy_value` has only ever been ~54,950 — nowhere
near 100,000 — the unconditional floor permanently won the `max()` against
real snapshot history, reproducing the exact same class of false-drawdown
bug the first half of this fix addressed, from a second source.

Fix: removed the unconditional `self.peak_value = self.initial_capital`
assignment from both the no-broker branch and the broker-exception branch.
`self.initial_capital` is now only used as a bootstrap fallback via the
pre-existing `elif not hasattr(self, 'peak_value')` guard later in the same
function — i.e. only on a genuine first-ever run with zero snapshot history,
matching how the broker-connected (live) branch already behaved. Verified
on production: `peak_value` now resolves to 54,950.15 (the real snapshot
max), not 100,000.

New regression test:
`tests/test_portfolio.py::TestPeakValueExcludesPreLiveSharedCashHistory::
test_peak_value_not_floored_by_stale_initial_capital` — seeds only clean
post-cutoff snapshots (no contaminated history needed this time) with
`initial_capital` left at its high default, asserts `peak_value` reflects
the snapshot max, not the default. Confirmed to fail against the pre-fix
code (`100000.0 != 54950.15`, via `git stash`) and pass with the fix.

Lesson: a single-cause theory ("the cutoff filter fixes the peak") was
under-verified until re-checked live end-to-end. Fixing the *history*
input (`MAIN_STRATEGY_PAPER_SINCE`) was necessary but not sufficient — the
*baseline* input (`initial_capital`) carried the same shared-account-era
contamination and needed its own independent fix. Any future "peak/drawdown
now correct" claim for main strategy must be verified against a live
`can_open_new_trades()` call, not just the snapshot-filtering logic in
isolation.

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
