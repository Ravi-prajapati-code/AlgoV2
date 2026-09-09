"""
One-off broker-truth reconciliation correction (2026-09-09).

Phase 26 live scan (see docs/65) found 4 symbols where a strategy ledger
claims shares the broker doesn't have, with NO exit/trade record on
either side explaining the gap -- unlike GOLDBEES.NS (a pure double-count
fiction, see docs/64), these are real shares that left the broker account
through an uncoordinated channel:

  - CYIENT.NS   (momentum_atr: 6 claimed, broker 3) -- root cause CONFIRMED:
                MAIN manually liquidated 3 sh pre-paper-cutover
                (trading.db trades.exit_reason='MANUAL_LIQUIDATION_PRE_PAPER_SWITCH').
  - WELCORP.NS  (momentum_atr: 8 claimed, broker 3) -- cause unconfirmed;
                same no-exit-record signature as CYIENT.
  - ATHERENERG.NS (momentum_atr: 1 claimed, broker 0) -- cause unconfirmed,
                small position.
  - ASIANENE.NS (MAIN: 8 claimed, broker 0; momentum_atr: 8 claimed, broker 0)
                -- doubly ghosted, cause unconfirmed.

User decision (2026-09-09): broker is ground truth; treat the missing
shares as a REAL realized loss (not a suppressed bookkeeping fiction like
GOLDBEES) -- record exit_price=0 (full write-off of cost basis, since no
sale proceeds were ever received by either strategy) and let
momentum_atr's peak_equity/kill-switch see the resulting drawdown
honestly. Cash is deliberately NOT credited for these "sales" -- no real
money was received.

Idempotent: skips any symbol whose recorded shares already match the
target (broker) quantity, so re-running after a partial failure or after
the correction has already landed is a safe no-op.

Usage:
  .venv/bin/python scripts/apply_broker_truth_corrections_20260909.py           # dry run
  .venv/bin/python scripts/apply_broker_truth_corrections_20260909.py --apply   # writes
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

TODAY = date.today()

# (strategy, symbol, broker_qty, exit_reason)
CORRECTIONS = [
    ("momentum_atr", "CYIENT.NS", 3, "RECONCILED_SHARES_LOST_MAIN_MANUAL_LIQUIDATION"),
    ("momentum_atr", "WELCORP.NS", 3, "RECONCILED_SHARES_LOST_UNEXPLAINED_BROKER_GAP"),
    ("momentum_atr", "ATHERENERG.NS", 0, "RECONCILED_SHARES_LOST_UNEXPLAINED_BROKER_GAP"),
    ("momentum_atr", "ASIANENE.NS", 0, "RECONCILED_SHARES_LOST_UNEXPLAINED_BROKER_GAP"),
    ("main", "ASIANENE.NS", 0, "RECONCILED_SHARES_LOST_UNEXPLAINED_BROKER_GAP"),
]


def apply_momentum_atr(symbol: str, broker_qty: int, reason: str, apply: bool) -> str:
    from db import momentum_atr_repo as m_repo
    from momentum_atr.models import Trade

    pos = m_repo.get_position(symbol)
    if pos is None:
        return f"SKIP {symbol}: no OPEN momentum_atr position (already corrected or never existed)"
    if pos.shares <= broker_qty:
        return f"SKIP {symbol}: recorded shares ({pos.shares}) already <= broker qty ({broker_qty})"

    lost = pos.shares - broker_qty
    trade = Trade(
        symbol=symbol, entry_date=pos.entry_date, exit_date=TODAY,
        entry_price=pos.entry_price, exit_price=0.0, shares=lost,
        gross_pnl=-pos.entry_price * lost, charges=0.0, net_pnl=-pos.entry_price * lost,
        exit_reason=reason, exit_order_id="",
    )
    action = f"{'CLOSE' if broker_qty == 0 else 'REDUCE'} momentum_atr {symbol}: {pos.shares} -> {broker_qty} (lost {lost} sh, write-off {-pos.entry_price * lost:,.2f})"
    if apply:
        if broker_qty == 0:
            m_repo.close_position_and_save_trade(symbol, trade)
        else:
            m_repo.reduce_position_and_save_trade(symbol, remaining_shares=broker_qty, t=trade)
    return action


def apply_main(symbol: str, broker_qty: int, reason: str, apply: bool) -> str:
    from db import repository as repo
    from db.models import Trade

    positions = [p for p in repo.load_positions("OPEN") if p.symbol == symbol]
    if not positions:
        return f"SKIP {symbol}: no OPEN main position (already corrected or never existed)"
    pos = positions[0]
    if pos.shares <= broker_qty:
        return f"SKIP {symbol}: recorded shares ({pos.shares}) already <= broker qty ({broker_qty})"
    if broker_qty != 0:
        raise NotImplementedError("MAIN partial-reduce correction not needed by this script")

    lost = pos.shares
    trade = Trade(
        symbol=symbol, sector=pos.sector, entry_date=pos.entry_date, exit_date=TODAY,
        entry_price=pos.entry_price, exit_price=0.0, shares=lost,
        gross_pnl=-pos.entry_price * lost, charges=0.0, net_pnl=-pos.entry_price * lost,
        exit_reason=reason, hold_days=(TODAY - pos.entry_date).days, slippage_pct=0.0,
    )
    action = f"CLOSE main {symbol}: {pos.shares} -> 0 (write-off {-pos.entry_price * lost:,.2f})"
    if apply:
        repo.close_position_and_save_trade(symbol, trade)
    return action


def main():
    apply = "--apply" in sys.argv
    print(f"[{'APPLY' if apply else 'DRY RUN'}] Broker-truth corrections -- {TODAY.isoformat()}")
    for strategy, symbol, broker_qty, reason in CORRECTIONS:
        fn = apply_momentum_atr if strategy == "momentum_atr" else apply_main
        result = fn(symbol, broker_qty, reason, apply)
        print(f"  [{strategy}] {result}")
    if not apply:
        print("\nDry run only -- re-run with --apply to write.")


if __name__ == "__main__":
    main()
