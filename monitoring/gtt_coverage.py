"""
GTT stop-loss coverage audit.

Cross-checks live Upstox holdings against active GTT orders and alerts (Telegram)
on any position that has NO live stop-loss — a "naked" position. This is the
standing safety net behind the entry/ratchet GTT logic: it catches a stop that
silently dropped, expired, was cancelled, or never landed.

Run after the daily run completes (positions + GTTs settled). Cron (IST):
  40 15 * * 1-5  cd ~/AlgoV2 && .venv/bin/python monitoring/gtt_coverage.py

Exit codes: 0 = all covered or nothing to check, 2 = naked position(s) found,
1 = could not audit (API/token failure — no alert raised, just logged).
"""

import html
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("GTTCoverage")


def _excluded_symbols() -> set:
    """Symbols that intentionally carry no GTT stop: cash-park ETFs, legacy
    IGNORE_SYMBOLS entries, and any manual/imported-origin DB position — the
    strategy must never touch a stop the user placed themselves. docs/30."""
    from config.settings import IGNORE_SYMBOLS
    from strategy.defensive_portfolio import ALL_DEFENSIVE_SYMBOLS
    from db.repository import load_positions
    manual_symbols = {p.symbol for p in load_positions(status="OPEN") if p.origin != "strategy"}
    return set(IGNORE_SYMBOLS) | set(ALL_DEFENSIVE_SYMBOLS) | manual_symbols


def check() -> int:
    from broker.upstox import UpstoxBroker

    try:
        broker = UpstoxBroker()
    except Exception as e:
        logger.error("Could not init Upstox broker (token?): %s — skipping audit.", e)
        return 1

    holdings = broker.get_holdings()
    if not holdings:
        # Empty can mean genuinely flat OR an API error; either way nothing to alert on.
        logger.info("No holdings returned — nothing to audit.")
        return 0

    gtt_keys = broker.list_active_gtt_instrument_keys()
    if gtt_keys is None:
        # API failure — do NOT report everything as naked. Bail safely.
        logger.error("Could not fetch active GTT list — skipping audit to avoid false alerts.")
        return 1

    excluded = _excluded_symbols()
    naked = []
    for pos in holdings:
        if pos.quantity <= 0 or pos.symbol in excluded:
            continue
        key = broker._resolve_instrument(pos.symbol)
        if key not in gtt_keys:
            naked.append(pos)

    covered = len([p for p in holdings if p.quantity > 0 and p.symbol not in excluded])
    if not naked:
        logger.info("GTT coverage OK — %d position(s) all protected.", covered)
        return 0

    lines = "\n".join(
        f"• <b>{html.escape(p.symbol)}</b> — {p.quantity} sh @ ₹{p.avg_price:,.2f}"
        for p in naked
    )
    msg = (
        f"🚨 <b>NAKED POSITIONS — no live stop-loss</b>\n"
        f"{len(naked)} of {covered} position(s) have NO active GTT stop:\n"
        f"{lines}\n\n"
        f"Set a stop on Upstox manually NOW. The next daily run will also try to "
        f"re-place these stops."
    )
    try:
        from notifications.telegram import send_message
        send_message(msg)
    except Exception as e:
        logger.error("Failed to send naked-position alert: %s", e)
    logger.warning("Naked positions: %s", [p.symbol for p in naked])
    return 2


if __name__ == "__main__":
    sys.exit(check())
