#!/usr/bin/env python3
"""
READ-ONLY pre-market check (08:35 IST weekdays, after the 08:30 token
refresh and before the 09:17 momentum_atr run): compares real Upstox cash
and holdings against the momentum_atr ledger (db/momentum_atr.db).

    35 8 * * 1-5 cd /home/ubuntu/AlgoV2 && .venv/bin/python \
        scripts/check_momentum_atr_broker.py >> logs/momentum_atr_broker_check.log 2>&1

Places no orders and never writes the DB. A cash gap can be a deposit, a
withdrawal, or unsettled sale proceeds -- the script cannot tell which, so
it only alerts; a human applies any ledger change (cash + peak_equity by
the same amount, so a deposit isn't read as profit by the DD scale/kill
switch). Added 2026-10-21 after a friend-account deposit sat unused.
"""
import html
import logging
import os
import sys
from datetime import date
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s -- %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("MomentumATRBrokerCheck")

# Ignore paise-level drift between broker and ledger cash.
CASH_TOLERANCE = 10.0


def compare(broker_cash: float, broker_qty: Dict[str, int],
            ledger_cash: float, ledger_qty: Dict[str, int]) -> Tuple[List[str], List[str]]:
    """Returns (problems, notes). Problems block correct trading and need a
    human; notes are informational (e.g. idle cash from a deposit)."""
    problems, notes = [], []
    for sym in sorted(ledger_qty):
        b, l = broker_qty.get(sym, 0), ledger_qty[sym]
        if b < l:
            problems.append(f"{sym}: ledger {l}, Upstox {b} -- sold outside the bot? its next SELL will be skipped")
        elif b > l:
            notes.append(f"{sym}: Upstox {b} > ledger {l} -- extra shares are not managed by the bot")
    gap = broker_cash - ledger_cash
    if gap < -CASH_TOLERANCE:
        problems.append(f"Upstox cash Rs.{broker_cash:,.2f} < ledger Rs.{ledger_cash:,.2f} "
                        f"(Rs.{-gap:,.2f} short) -- withdrawal? BUYs will be capped at broker cash")
    elif gap > CASH_TOLERANCE:
        notes.append(f"Upstox cash Rs.{broker_cash:,.2f} > ledger Rs.{ledger_cash:,.2f} "
                     f"(Rs.{gap:,.2f} extra) -- deposit? the bot will NOT spend it until the ledger is raised")
    return problems, notes


def main() -> int:
    today = date.today()
    from runner.daily_runner import _is_market_holiday
    if _is_market_holiday(today):
        logger.info("market holiday %s -- no check", today)
        return 0

    from config.settings import IS_PRODUCTION
    from db import momentum_atr_repo as repo
    from notifications.telegram import send_message
    prefix = "" if IS_PRODUCTION else "[LOCAL TEST] "

    def _alert(lines: List[str]) -> None:
        try:
            send_message(f"{prefix}⚠️ <b>momentum_atr broker check -- {today}</b>\n"
                         + "\n".join(f"- {html.escape(x)}" for x in lines))
        except Exception as e:
            logger.warning("Telegram alert failed: %s", e)

    repo.init_db()
    from broker.upstox import UpstoxBroker
    broker = UpstoxBroker()
    cash = broker.get_available_cash_or_none()
    holdings = broker.get_net_holdings_or_none()
    if cash is None or holdings is None:
        msg = f"broker read FAILED (cash={cash}, holdings={'ok' if holdings is not None else None}) -- token/API problem?"
        logger.error(msg)
        _alert([msg])
        return 1

    broker_qty = {h.symbol: h.quantity for h in holdings if h.quantity}
    ledger_qty = {p.symbol: p.shares for p in repo.load_positions("OPEN")}
    ledger_cash = repo.get_state().cash
    logger.info("Upstox cash Rs.%.2f | ledger cash Rs.%.2f", cash, ledger_cash)
    for h in holdings:
        if h.quantity:
            tag = "bot" if h.symbol in ledger_qty else "not bot"
            logger.info("Upstox holding %s x%d avg=%.2f ltp=%.2f (%s)", h.symbol, h.quantity, h.avg_price, h.ltp, tag)

    problems, notes = compare(cash, broker_qty, ledger_cash, ledger_qty)
    for n in notes:
        logger.info("NOTE %s", n)
    for p in problems:
        logger.warning("MISMATCH %s", p)
    if problems or any("deposit?" in n for n in notes):
        _alert(problems + [n for n in notes if "deposit?" in n])
    if problems:
        return 1
    logger.info("OK: %d bot positions present at Upstox", len(ledger_qty))
    return 0


if __name__ == "__main__":
    sys.exit(main())
