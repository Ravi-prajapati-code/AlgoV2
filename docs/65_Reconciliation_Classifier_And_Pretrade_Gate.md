# 65 — Reconciliation Classifier + Pre-Trade Integrity Gate (M0/M1/M3, 2026-09-09)

## Summary

Covers milestones M0, M1, and M3 of the Position/Order/Ownership/
Reconciliation Integrity subsystem (plan `optimized-humming-crayon`). M2
(peak-value contamination marking) has its own entry, docs/64. This doc was
written retroactively alongside M3 to close a documentation gap — M0/M1 had
no standalone entry before now, only inline comments and the plan file
itself.

## Origin: three real incidents

1. **GOLDBEES** — momentum_atr genuinely held 175 sh; MAIN's DB also carried
   an OPEN row for the same 175 sh (`main_qty=175, atr_qty=175,
   broker_qty=175`). Fixed in docs/64.
2. **ASIANENE** — OPEN in both `trading.db` (manual origin) and
   `momentum_atr.db` (bot order); broker holds zero. Doubly ghosted.
3. **CYIENT** — broker holds 3, momentum_atr's DB says OPEN 6. MAIN manually
   liquidated 3 sh pre-paper-cutover; Upstox holds CYIENT as one fungible
   balance, not segregated by strategy, so the manual sell likely ate into
   momentum_atr's real holding without momentum_atr's ledger being told.

Both the existing `scripts/observability_snapshot.py` (boolean
`collision_flag` only) and `scripts/reconcile_positions.py` (symbol-set diff
only, never quantities) could detect *that* something was wrong but not
classify *what*, and neither fed any decision — a reconciliation finding sat
in a log with no consequence for the next BUY.

## M0 — `reconciliation/classifier.py`

Pure function, no DB/broker I/O: `classify(symbol, main_qty, atr_qty,
broker_qty, evidence) -> (Classification, evidence_dict)`. Twelve-value enum
(`MATCH`, `PENDING_EXECUTION`, `DUPLICATE_OWNERSHIP`, `GHOST_DB_POSITION`,
`STRATEGY_ONLY_POSITION`, `BROKER_ONLY_POSITION`, `QUANTITY_MISMATCH`,
`OWNERSHIP_CONFLICT`, `MANUAL_BROKER_POSITION`, `UNKNOWN_POSITION`,
`BROKER_DATA_UNAVAILABLE`, `RECONCILIATION_ERROR`). `broker_qty` is
`Optional[int]` — `None` (broker read failed) is never conflated with `0`
(broker confirmed no position); `UNKNOWN != ZERO` throughout, per the user's
original safety rules. Any internal exception is caught and reclassified as
`RECONCILIATION_ERROR` rather than propagating or silently dropping —
fail-closed even inside the classifier itself.

`BLOCKING_CLASSIFICATIONS` (`DUPLICATE_OWNERSHIP`, `OWNERSHIP_CONFLICT`,
`QUANTITY_MISMATCH`, `UNKNOWN_POSITION`, `RECONCILIATION_ERROR`) is the tier
consumed by M3's gate — these are never auto-repaired anywhere in the
codebase; only `BROKER_ONLY_POSITION` (via `reconcile_positions.py`'s
pre-existing SAFE boundary, gated on `MAIN_STRATEGY_LIVE_TRADING_ENABLED`)
gets any automated write.

`db/reporting_schema.sql` / `db/reporting_repo.py::init_db()` gained
`strategy_position_snapshot.classification` / `.evidence_json` (idempotent
`ALTER TABLE`, same pattern as every other migration in this repo).
`save_strategy_position_snapshot()` accepts both as optional params,
default `None` — fully backward compatible with existing callers.

Regression fixtures in `tests/test_reconciliation_classifier.py` reproduce
the GOLDBEES/CYIENT/ASIANENE numbers exactly, plus evidence-gating (CYIENT
classifies as `QUANTITY_MISMATCH` without `manual_evidence`, or
`MANUAL_BROKER_POSITION` with it — the classifier never guesses).

## M1 — Wiring (partial: code complete, cron deploy not done)

`db/reporting_repo.py::load_latest_position_classification(symbol)` — new
read-only accessor so M3's gate doesn't need to know `reporting.db`'s row
shape directly.

**Not yet done, out of scope for this doc**: adding
`scripts/observability_snapshot.py` to the live server's crontab. That is a
live-server deployment action, deferred pending explicit user
authorization/request, same as this plan's Verification section requires.

## M3 — Pre-trade integrity gate

**New `reconciliation/gate.py::pre_trade_check(symbol, strategy_id) ->
(allowed, reason)`.** Read-only against `reporting.db`; never touches
`trading.db`/`momentum_atr.db`; never raises (any internal failure returns
`(False, ...)` rather than crashing the caller's BUY loop — fail-closed at
the boundary, not just in the happy path).

Blocks on:
- Latest classification for the symbol in `BLOCKING_CLASSIFICATIONS`.
- Staleness: no snapshot for the symbol within
  `RECONCILIATION_STALENESS_TRADING_SESSIONS` (default 2) weekday-counted
  trading sessions. This single mechanism also covers
  `BROKER_DATA_UNAVAILABLE` — confirmed by code-path reading that
  `observability_snapshot.py::main()` exits *before* calling
  `snapshot_positions()`/`classify()` when `get_broker_snapshot()` returns
  `None`, so a failed broker read never writes a new
  `strategy_position_snapshot` row at all. It shows up here purely as an
  aging existing row, not as a distinct classification value — no separate
  special-case was needed.

Allows on:
- `MATCH`, `GHOST_DB_POSITION` (alert-only by existing design),
  `PENDING_EXECUTION`.
- No row at all for the symbol (genuinely new, not stale) — distinct from
  staleness, which requires a row that's too old.
- **Bootstrap exception**: `strategy_registry` has zero rows (reporting.db
  never initialized on this box) → allow with a loud `WARNING` log. The one
  deliberate exception to fail-closed, scoped to first-deploy only — once
  any row exists in `strategy_registry`, this exception no longer applies
  even for a symbol with no snapshot history of its own.
- `PRE_TRADE_INTEGRITY_GATE_ENABLED=False` (kill-switch config flag, default
  `True`, same pattern as `DD_THROTTLE_DISABLED_ENABLED`).

**Wiring** — both strategies, per-symbol `continue` (not `break`), so one
poisoned symbol never blocks the rest of the cycle's buys:
- `portfolio/manager.py`'s BUY loop, immediately after the existing
  portfolio-wide `can_open_new_trades()` check (which correctly does
  `break` — a drawdown breach *should* stop the whole cycle; a reconciliation
  problem on one symbol should not).
- `momentum_atr/execution.py::_execute_buys()` — the single shared chokepoint
  for all three of that strategy's BUY call sites (initial split,
  swap-winner, rank-exit reallocation), so the gate call and its wiring
  exist exactly once.

`config/settings.py` — `PRE_TRADE_INTEGRITY_GATE_ENABLED` and
`RECONCILIATION_STALENESS_TRADING_SESSIONS` (both env-overridable).

## Regression tests

`tests/test_reconciliation_gate.py` (new): match→allowed; duplicate-
ownership→blocked, reason names the classification; new symbol with no
history→allowed, reason distinguishes it from staleness; stale row→blocked,
distinct reason; empty registry→allowed + `WARNING` logged (asserted via
`caplog`); gate disabled→always allowed even over a blocking classification;
integration test against `portfolio/manager.py`'s real BUY loop with two
signals in one cycle, one gate-blocked and one not, proving the `continue`
wiring — the blocked symbol is skipped, the other still buys in the same
cycle.

Full suite: 249 passed (7 new), same 4 pre-existing unrelated failures
(`test_momentum_atr_execution.py` scoring-formula drift,
`test_universe_research.py` universe-size drift) — zero regressions.

## What's still open

- **Live deployment**: `scripts/observability_snapshot.py` is not in the
  live server's crontab. Until it is, `reporting.db`'s `strategy_registry`
  stays empty in production, so M3's gate runs permanently in its bootstrap
  allow-with-warning state — real protection, but not yet the fail-closed
  state the gate is designed for. Deploying the cron is the single action
  that activates the rest of this subsystem's protection.
- **M4/M5/M6 remain explicitly deferred** per the plan, with stated trigger
  conditions — order/fill ledger, allocation-transition state machine,
  corporate-action handling. Not built speculatively.

**Resolved since this doc was written**:
- **Phase 26 live scan**: run manually against the live server post-deploy
  (2026-09-09). GOLDBEES was a classifier false-positive, not a real
  incident — fixed same day (a44bf12, see below). ASIANENE/ATHERENERG/
  CYIENT/WELCORP were real broker-vs-ledger gaps, corrected — see docs/66.
- **GOLDBEES classifier false-positive (a44bf12, 2026-09-09)**: the live
  scan surfaced GOLDBEES as `DUPLICATE_OWNERSHIP` even after docs/64's fix —
  MAIN's post-cutover *paper* position (102 sh, opened 2026-09-07, after
  `MAIN_STRATEGY_PAPER_SINCE`) was still being counted toward `main_qty` at
  both `classify()` call sites (`observability_snapshot.py` and
  `reconcile_positions.py`), even though MAIN is paper-only and that
  position was never real. Fixed by filtering MAIN positions on
  `entry_date >= MAIN_STRATEGY_PAPER_SINCE` per-position (not a blanket
  "ignore MAIN if paper mode is on today" check, which would have wrongly
  blinded pre-cutover real positions like ASIANENE). Regression tests added
  at both call sites; live-verified post-deploy: GOLDBEES now `MATCH`.
- **ASIANENE / CYIENT disposition**: resolved via docs/66's broker-truth
  correction, alongside two more symbols the live scan found in the same
  state (ATHERENERG, WELCORP).
