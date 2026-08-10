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
)
from db import reporting_repo as rrepo


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
    for p in positions:
        if p.product != "CNC":
            continue
        qty_by_symbol[p.symbol] = qty_by_symbol.get(p.symbol, 0) + p.quantity
        ltp_by_symbol[p.symbol] = p.ltp
    invested = sum(qty_by_symbol[s] * ltp_by_symbol[s] for s in qty_by_symbol)
    return {
        "cash": cash,
        "qty_by_symbol": qty_by_symbol,
        "ltp_by_symbol": ltp_by_symbol,
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
    """Writes the 3-way per-symbol qty view + collision flag. Returns
    (symbol, main_qty, atr_qty, broker_qty) rows for the reconciliation
    checks below, so they don't have to re-query."""
    main_qty = {}
    for p in main_positions:
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
        rrepo.save_strategy_position_snapshot(ts, sym, broker_qty=b, main_qty=m, momentum_atr_qty=a)
        rows.append((sym, m, a, b))
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


def run_reconciliation(ts: str, position_rows: list) -> None:
    """Alert-only -- nothing here writes to either strategy DB. main_vs_broker
    mirrors scripts/reconcile_positions.py's read-side ghost check;
    momentum_atr_vs_broker has no prior implementation anywhere (docs/60
    §1.7 / Page 14 -- a real production blind spot this closes for the
    dashboard, still read-only/alert-only per the observability-only
    constraint)."""
    collisions = [(sym, m, a, b) for sym, m, a, b in position_rows if (m + a) != b]
    if collisions:
        detail = "; ".join(f"{s}: main={m} atr={a} broker={b}" for s, m, a, b in collisions)
        rrepo.record_reconciliation(ts, "position_collision_sum", "FAIL", detail=detail)
        rrepo.record_alert(
            ts, "CRITICAL", "position_collision",
            f"{len(collisions)} symbol(s) where main_qty+momentum_atr_qty != broker_qty: {detail}",
            "observability_snapshot.py",
        )
    else:
        rrepo.record_reconciliation(ts, "position_collision_sum", "PASS")

    main_ghost = [s for s, m, a, b in position_rows if m > 0 and b == 0 and a == 0]
    if main_ghost:
        rrepo.record_reconciliation(
            ts, "main_vs_broker", "WARNING", strategy_id="main",
            detail=f"DB open, broker has none: {main_ghost}",
        )
    else:
        rrepo.record_reconciliation(ts, "main_vs_broker", "PASS", strategy_id="main")

    atr_ghost = [s for s, m, a, b in position_rows if a > 0 and b == 0 and m == 0]
    if atr_ghost:
        rrepo.record_reconciliation(
            ts, "momentum_atr_vs_broker", "WARNING", strategy_id="momentum_atr",
            detail=f"DB open, broker has none: {atr_ghost}",
        )
        rrepo.record_alert(
            ts, "WARNING", "momentum_atr_ghost_position",
            f"momentum_atr DB shows open position(s) the broker doesn't have: {atr_ghost}",
            "observability_snapshot.py", strategy_id="momentum_atr",
        )
    else:
        rrepo.record_reconciliation(ts, "momentum_atr_vs_broker", "PASS", strategy_id="momentum_atr")


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

    rrepo.save_broker_snapshot(ts, broker_snap["cash"], broker_snap["total_equity"], broker_snap["qty_by_symbol"])
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
