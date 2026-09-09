"""
Observability snapshot (docs/60 Phase 1).

Read-only against db/trading.db and db/momentum_atr.db, one broker read
per cycle, writes only land in db/reporting.db. Never touches positions/
trades/signals/state in either strategy DB, never places an order, never
auto-repairs a mismatch -- every reconciliation check here is alert-only,
same discipline as scripts/reconcile_positions.py's DB-only-mismatch side.

Cron (cadence decided with user 2026-08-10, NOT yet added to any
crontab -- this is Phase 1, DB+script only, deploy is a separate step):
  */15 9-15 * * 1-5 cd /home/ubuntu/AlgoV2 && .venv/bin/python scripts/observability_snapshot.py >> logs/observability_snapshot.log 2>&1
"""

import sys
import logging
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("observability_snapshot")

from config.settings import (
    DB_PATH, MOMENTUM_ATR_DB_PATH, MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT,
    MAIN_STRATEGY_PAPER_SINCE,
)
from db import reporting_repo as rrepo
from reconciliation.classifier import Classification, classify


def _now_iso() -> str:
    return datetime.now().isoformat()


def get_broker_snapshot():
    """One broker read for this whole cycle: positions (short-term +
    long-term holdings, combined by UpstoxBroker.get_positions()) plus
    available cash. Returns None on failure/empty so callers skip the
    cycle rather than treat a failed read as 'broker has nothing'."""
    from broker.upstox import UpstoxBroker

    try:
        broker = UpstoxBroker()
        positions = broker.get_positions()
        cash = broker.get_available_cash()
    except Exception as e:
        logger.error("Broker read failed: %s", e)
        return None
    if not positions and cash == 0.0:
        logger.warning("Broker returned nothing (0 positions, 0 cash) -- token may be expired.")
        return None

    qty_by_symbol = {}
    ltp_by_symbol = {}
    cost_by_symbol = {}  # qty-weighted, for a correct avg_price if a symbol spans lots
    for p in positions:
        if p.product != "CNC":
            continue
        qty_by_symbol[p.symbol] = qty_by_symbol.get(p.symbol, 0) + p.quantity
        cost_by_symbol[p.symbol] = cost_by_symbol.get(p.symbol, 0.0) + p.quantity * p.avg_price
        ltp_by_symbol[p.symbol] = p.ltp
    invested = sum(qty_by_symbol[s] * ltp_by_symbol[s] for s in qty_by_symbol)
    holdings = {
        s: {
            "qty": qty_by_symbol[s],
            "avg_price": cost_by_symbol[s] / qty_by_symbol[s] if qty_by_symbol[s] else 0.0,
            "ltp": ltp_by_symbol[s],
        }
        for s in qty_by_symbol
    }
    return {
        "cash": cash,
        "qty_by_symbol": qty_by_symbol,
        "ltp_by_symbol": ltp_by_symbol,
        "holdings": holdings,
        "total_equity": cash + invested,
    }


def get_main_state():
    from db import repository as repo
    positions = repo.load_positions(status="OPEN")
    snapshots = repo.load_snapshots(limit=1)
    latest = snapshots[-1] if snapshots else None
    return positions, latest


def get_momentum_atr_state():
    from db import momentum_atr_repo as m_repo
    positions = m_repo.load_positions(status="OPEN")
    state = m_repo.get_state()
    return positions, state


def snapshot_positions(ts: str, main_positions, atr_positions, broker_snap: dict) -> list:
    """Writes the 3-way per-symbol qty view + classification. Returns
    (symbol, main_qty, atr_qty, broker_qty, classification) rows for the
    reconciliation checks below, so they don't have to re-query.

    No evidence lookup here (read-only/alert-only path, no trades-table
    join) -- manual_evidence/prior_record_exists stay unset, so ambiguous
    shapes classify conservatively (QUANTITY_MISMATCH not
    MANUAL_BROKER_POSITION, UNKNOWN_POSITION not BROKER_ONLY_POSITION).
    scripts/reconcile_positions.py is the one path with an origin-recovery
    heuristic and passes real evidence.

    MAIN positions opened on/after MAIN_STRATEGY_PAPER_SINCE are simulated
    -- they never reach the broker, so they must not count toward
    ownership/conflict math (GOLDBEES.NS: a 2026-09-07 paper entry was
    classifying as DUPLICATE_OWNERSHIP against momentum_atr's real 175 sh,
    even though momentum_atr's holding alone already matched the broker
    exactly). A position dated before the cutover is real regardless of
    MAIN's *current* live-trading flag (ASIANENE.NS: manual entry
    2026-08-27, predates the 2026-09-03 cutover, must stay counted) -- so
    this is a per-position date filter, not a flag check. Paper qty is
    still passed through as evidence for audit visibility, just excluded
    from the classifier's quantity math."""
    main_qty = {}
    main_paper_qty = {}
    for p in main_positions:
        if p.entry_date >= MAIN_STRATEGY_PAPER_SINCE:
            main_paper_qty[p.symbol] = main_paper_qty.get(p.symbol, 0) + p.shares
        else:
            main_qty[p.symbol] = main_qty.get(p.symbol, 0) + p.shares
    atr_qty = {}
    for p in atr_positions:
        atr_qty[p.symbol] = atr_qty.get(p.symbol, 0) + p.shares

    all_symbols = set(main_qty) | set(atr_qty) | set(broker_snap["qty_by_symbol"])
    rows = []
    for sym in sorted(all_symbols):
        m = main_qty.get(sym, 0)
        a = atr_qty.get(sym, 0)
        b = broker_snap["qty_by_symbol"].get(sym, 0)
        evidence = {"main_paper_qty": main_paper_qty[sym]} if sym in main_paper_qty else None
        cls, ev = classify(sym, m, a, b, evidence)
        rrepo.save_strategy_position_snapshot(
            ts, sym, broker_qty=b, main_qty=m, momentum_atr_qty=a,
            classification=cls.value, evidence=ev,
        )
        rows.append((sym, m, a, b, cls))
    return rows


def snapshot_main_capital(ts: str, main_snapshot) -> None:
    """MAIN has no allocation cap (confirmed: no ALLOCATED_CAPITAL/
    BASELINE_CAPITAL-style constant anywhere in config/settings.py) -- its
    cash figure is whole-account cash, never fabricated as a segregated
    pool."""
    if main_snapshot is None:
        logger.warning("No portfolio_snapshots row yet for MAIN -- skipping capital snapshot.")
        return
    cash = main_snapshot.cash
    strategy_equity = main_snapshot.strategy_value
    invested = strategy_equity - cash
    rrepo.save_strategy_capital_snapshot(
        "main", ts,
        strategy_invested_value=invested,
        strategy_equity=strategy_equity,
        strategy_allocated_cash=None,
        strategy_available_cash=cash,
        source_note="MAIN has no allocation cap; this is whole-account cash MAIN currently sees, not a segregated pool.",
    )


def snapshot_atr_capital(ts: str, atr_positions, atr_state, broker_snap: dict) -> None:
    ltp = broker_snap["ltp_by_symbol"]
    invested = sum(p.shares * ltp.get(p.symbol, p.entry_price) for p in atr_positions)
    equity = atr_state.cash + invested
    allocated = broker_snap["total_equity"] * MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT
    rrepo.save_strategy_capital_snapshot(
        "momentum_atr", ts,
        strategy_invested_value=invested,
        strategy_equity=equity,
        strategy_allocated_cash=allocated,
        strategy_available_cash=atr_state.cash,
        source_note=(
            f"momentum_atr capped at {MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT:.0%} of real combined "
            "account equity, recomputed live each run; cash is momentum_atr's own internal "
            "ledger, not a segregated broker pool."
        ),
    )


_ALERT_SEVERITY = {
    Classification.DUPLICATE_OWNERSHIP: "CRITICAL",
    Classification.OWNERSHIP_CONFLICT: "CRITICAL",
    Classification.QUANTITY_MISMATCH: "CRITICAL",
    Classification.RECONCILIATION_ERROR: "CRITICAL",
    Classification.UNKNOWN_POSITION: "WARNING",
    Classification.MANUAL_BROKER_POSITION: "WARNING",
    Classification.GHOST_DB_POSITION: "WARNING",
    Classification.BROKER_DATA_UNAVAILABLE: "WARNING",
}


def run_reconciliation(ts: str, position_rows: list) -> None:
    """Alert-only -- nothing here writes to either strategy DB. Classification
    replaces the three ad hoc collision/main_ghost/atr_ghost checks this used
    to run so both this script and scripts/reconcile_positions.py share one
    vetted classifier (reconciliation/classifier.py) instead of drifting."""
    non_match = [(sym, m, a, b, cls) for sym, m, a, b, cls in position_rows
                 if cls != Classification.MATCH]

    if non_match:
        detail = "; ".join(
            f"{s}: main={m} atr={a} broker={b} -> {cls.value}" for s, m, a, b, cls in non_match
        )
        rrepo.record_reconciliation(ts, "position_classification", "FAIL", detail=detail)
    else:
        rrepo.record_reconciliation(ts, "position_classification", "PASS")

    for sym, m, a, b, cls in non_match:
        severity = _ALERT_SEVERITY.get(cls, "WARNING")
        rrepo.record_alert(
            ts, severity, cls.value,
            f"{sym}: main={m} atr={a} broker={b} classified {cls.value}",
            "observability_snapshot.py",
        )


def main():
    ts = _now_iso()
    rrepo.init_db()
    rrepo.register_strategy("main", "Main Strategy", DB_PATH)
    rrepo.register_strategy("momentum_atr", "Momentum x ATR", MOMENTUM_ATR_DB_PATH)

    broker_snap = get_broker_snapshot()
    if broker_snap is None:
        rrepo.record_run_log(ts, "observability_snapshot", "FAILED", detail="broker read failed/empty")
        logger.error("Aborting snapshot cycle -- no usable broker read.")
        sys.exit(1)

    main_positions, main_snapshot = get_main_state()
    atr_positions, atr_state = get_momentum_atr_state()

    rrepo.save_broker_snapshot(ts, broker_snap["cash"], broker_snap["total_equity"], broker_snap["holdings"])
    position_rows = snapshot_positions(ts, main_positions, atr_positions, broker_snap)
    snapshot_main_capital(ts, main_snapshot)
    snapshot_atr_capital(ts, atr_positions, atr_state, broker_snap)
    run_reconciliation(ts, position_rows)

    rrepo.record_run_log(ts, "observability_snapshot", "OK", detail=f"{len(position_rows)} symbols snapshotted")
    logger.info(
        "Snapshot cycle OK -- %d symbols, %d main, %d momentum_atr positions",
        len(position_rows), len(main_positions), len(atr_positions),
    )


if __name__ == "__main__":
    main()
