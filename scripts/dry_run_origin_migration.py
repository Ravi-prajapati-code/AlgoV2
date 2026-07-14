"""
Read-only dry-run for docs/30 Steps 2+5 — validates the origin-classification
logic (5ff13d5, not yet deployed) against REAL current broker holdings before
the schema/exit-logic change ships to the live server.

Does NOT write to the DB, place orders, or touch GTTs. Pure diagnostic:
for every symbol the broker currently holds, shows what origin the new code
would assign (get_last_position(symbol) is None -> "manual", else "strategy"),
and flags anything that looks surprising (e.g. a currently-IGNORE_SYMBOLS
holding classified "strategy", or a symbol with DB history classified
"manual").
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

import requests
from datetime import date

import config.settings as settings
IGNORE_SYMBOLS = getattr(settings, "IGNORE_SYMBOLS", [])
BLOCKED_SYMBOLS = getattr(settings, "BLOCKED_SYMBOLS", [])
from db.repository import get_last_position, load_positions, load_trades

RECENT_GAP_DAYS = 3  # must match runner/daily_runner.py's RECENT_GAP_DAYS


def get_broker_positions() -> list[dict]:
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN missing from .env")

    base_url = "https://api.upstox.com/v2"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    positions = []

    resp = requests.get(f"{base_url}/portfolio/long-term-holdings", headers=headers, timeout=10)
    resp.raise_for_status()
    for pos in resp.json().get("data", []):
        qty = int(pos.get("quantity", 0))
        if qty > 0:
            positions.append({
                "symbol": pos.get("tradingsymbol", "") + ".NS",
                "qty": qty,
                "avg_price": pos.get("average_price", 0),
                "source": "long-term-holdings",
            })

    resp = requests.get(f"{base_url}/portfolio/short-term-positions", headers=headers, timeout=10)
    resp.raise_for_status()
    for pos in resp.json().get("data", []):
        qty = int(pos.get("quantity", 0))
        if qty > 0 and pos.get("product") == "CNC":
            positions.append({
                "symbol": pos.get("tradingsymbol", "") + ".NS",
                "qty": qty,
                "avg_price": pos.get("average_price", 0),
                "source": "short-term-positions(CNC)",
            })

    return positions


def main():
    broker_positions = get_broker_positions()
    db_open = {p.symbol: p for p in load_positions("OPEN")}

    print(f"Broker holds {len(broker_positions)} position(s). DB currently tracks {len(db_open)} OPEN.\n")
    print(f"{'SYMBOL':<16}{'QTY':>6}  {'IGNORE?':<8}{'BLOCKED?':<9}{'DB-OPEN-NOW?':<14}{'NEW ORIGIN':<11}{'FLAG'}")
    print("-" * 90)

    today = date.today()
    all_trades = load_trades()

    surprises = []
    for pos in sorted(broker_positions, key=lambda p: p["symbol"]):
        sym = pos["symbol"]
        is_ignored = sym in IGNORE_SYMBOLS
        is_blocked = sym in BLOCKED_SYMBOLS
        in_db_open = sym in db_open
        prev = get_last_position(sym)
        last_trade = next((t for t in all_trades if t.symbol == sym), None)
        is_recent_gap = bool(prev and last_trade and (today - last_trade.exit_date).days <= RECENT_GAP_DAYS)
        effective_prev = prev if is_recent_gap else None
        new_origin = "strategy" if effective_prev else "manual"

        detail = ""
        if prev and not is_recent_gap:
            gap = (today - last_trade.exit_date).days if last_trade else "?"
            detail = f"stale prev (last trade closed {gap}d ago) -> ignored"

        flag = ""
        if is_ignored and new_origin == "strategy":
            flag = "!! IGNORE_SYMBOLS but classifies strategy -> would become strategy-managed"
        elif is_ignored and new_origin == "manual":
            flag = f"IGNORE_SYMBOLS -> correctly manual. {detail}"
        elif not is_ignored and new_origin == "manual" and not in_db_open:
            flag = f"manual, invisible to DB too (consistent). {detail}"

        if flag.startswith("!!"):
            surprises.append((sym, flag))

        print(f"{sym:<16}{pos['qty']:>6}  {str(is_ignored):<8}{str(is_blocked):<9}{str(in_db_open):<14}{new_origin:<11}{flag}")

    print()
    if surprises:
        print(f"SURPRISES ({len(surprises)}) — review before deploying:")
        for sym, flag in surprises:
            print(f"  {sym}: {flag}")
    else:
        print("No surprises: every IGNORE_SYMBOLS holding has no DB history -> classifies 'manual', matching intent.")

    print(f"\nIGNORE_SYMBOLS (config): {IGNORE_SYMBOLS}")
    print(f"BLOCKED_SYMBOLS (config): {BLOCKED_SYMBOLS}")


if __name__ == "__main__":
    main()
