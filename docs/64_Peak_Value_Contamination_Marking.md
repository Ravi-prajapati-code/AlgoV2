# 64 — Peak-Value Contamination Marking (`value_change_reason`, 2026-09-09)

## Summary

Third, distinct contamination source in the same `peak_value` computation
docs/63 fixed twice already. Both prior fixes filtered `portfolio_snapshots`
by *date* (`MAIN_STRATEGY_PAPER_SINCE`) and by *baseline* (removing the
`initial_capital` floor). Neither can catch a legitimate correction that
lands *inside* the already-trusted date range: nothing on a snapshot row
distinguished "real trading P&L" from "bookkeeping fix," so the
peak-seeking `max()` over `strategy_value` treated both identically.

## Root cause (real incident)

2026-09-07: momentum_atr genuinely held 175 sh of GOLDBEES.NS; `trading.db`
also carried an OPEN row for the identical 175 sh (see
[[goldbees_orphan_position_fold_20260907]] in memory —
`db.repository.close_position_and_save_trade`, `exit_price == entry_price`,
`net_pnl = 0`, a pure dedupe). That single write dropped that date's
`portfolio_snapshots.strategy_value` from 54,950.15 to 32,811.16, because
`date` is `UNIQUE` and the save path is `INSERT OR REPLACE` — the correction
overwrote the day's row rather than appending a parallel record. The date
itself was well inside `MAIN_STRATEGY_PAPER_SINCE`, so docs/63's filter
didn't and couldn't exclude it. `peak_value` (`max()` over all eligible
snapshots) never came back down from 54,950.15, so `can_open_new_trades()`
started reading a permanent ~40.6% false drawdown against an 18% circuit
breaker.

## Fix

New `portfolio_snapshots.value_change_reason` column (`TEXT DEFAULT
'REALIZED_TRADING_PNL'`), written once at snapshot-creation/correction time,
never inferred retroactively from a delta:

- `db/schema.sql` / `db/repository.py::init_db()` — additive column +
  migration, same pattern as the existing `strategy_value` migration.
  SQLite backfills existing rows to the column default
  (`'REALIZED_TRADING_PNL'`) on `ALTER TABLE ADD COLUMN`, which is exactly
  correct for all pre-migration history — none of it was a correction.
- `db/models.py::PortfolioSnapshot.value_change_reason` — new field.
- `db/repository.py::save_snapshot()` / `load_snapshots()` — persist and
  parse the column, with the same fallback-to-default pattern used for
  `strategy_value`.
- New `db/repository.py::record_ownership_correction(symbol, prior_qty,
  corrected_qty, reason, source, snapshot_date=None)` — explicit, auditable
  replacement for an ad hoc dedupe: stamps the target date's row
  `'OWNERSHIP_CORRECTION'`, rejects empty `reason`/`source`, and writes a
  `strategy_reconciliation_log` row via `db/reporting_repo.py` using its
  existing repair-evidence columns (`repair_what`/`repair_why`/
  `repair_previous_value`/`repair_new_value`/`repair_source`) so the fix is
  queryable from `reporting.db` without re-deriving it from a git diff.

Every peak-seeking `max()` over `strategy_value` now filters out
`value_change_reason == 'OWNERSHIP_CORRECTION'` rows, on top of (not instead
of) docs/63's existing `MAIN_STRATEGY_PAPER_SINCE` date filter:

- `portfolio/manager.py::_load_state()` — the real live BUY gate.
- `main.py::cmd_risk_report()` — the manual CLI diagnostic (uses
  `total_value` not `strategy_value` for its own peak metric, a
  pre-existing, separate, out-of-scope discrepancy from `manager.py`'s
  metric — left as-is).
- `dashboard/views/risk_monitor.py::_main_risk_state()` — the dashboard
  reproduction.

`current`/`cash` values at each of these sites still read the *unfiltered*
latest snapshot — a correction is still the real current state, it's only
excluded from the *peak* search.

`momentum_atr/risk.py::check_kill_switch()` was deliberately left with no
logic change — its `peak_equity` is a persisted running max, not derived
from `portfolio_snapshots`, so this exact mechanism doesn't apply there
today. A one-line comment flags the latent structural gap (a manual
correction to momentum_atr's own ledger has no equivalent marking) for a
future incident to pick up; no known incident there yet, so no speculative
fix was built.

## Regression tests

`tests/test_portfolio.py` — extends
`TestPeakValueExcludesPreLiveSharedCashHistory` with:

- Real-incident-number regression: seed peak 54,950.15, an
  `OWNERSHIP_CORRECTION` row at 32,811.16 inside the trusted date range —
  `peak_value` stays 54,950.15. (A lower-valued correction is trivially
  excluded by `max()` regardless of any filter, so this test pins the real
  incident's numbers but isn't the load-bearing case — that's next.)
- A correction row with a *higher* value (99,999.0) is still excluded from
  `peak_value` (stays 40,000.0) — the filter is reason-based, not
  direction-based, and this is the case a naive `max()` would get wrong.
- `record_ownership_correction()` rejects empty `reason`/`source`.
- Default/backfilled rows classify as `REALIZED_TRADING_PNL`.

## Relationship to the reconciliation plan

This is milestone M2 of the Position/Order/Ownership/Reconciliation
Integrity subsystem (plan `optimized-humming-crayon`), whose M0
(`reconciliation/classifier.py`) gave GOLDBEES's original double-count a
name (`DUPLICATE_OWNERSHIP`) — M2 makes sure *fixing* that kind of finding
can never again silently reappear as a fake drawdown. docs/63 itself is
unmodified; this is the third, independent fix in that lineage, not a
correction to the first two.
