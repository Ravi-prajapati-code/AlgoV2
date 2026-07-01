"""
Position Reconciler — compares DB open positions vs actual Upstox holdings.
Alerts via Telegram if DB and broker are out of sync.

Cron (server — all times IST):
  # 09:20 IST Mon-Fri — after token refresh (08:30 IST), checks yesterday's positions
  20 9 * * 1-5  cd /home/ubuntu/AlgoV2 && .venv/bin/python scripts/reconcile_positions.py >> logs/reconcile.log 2>&1
"""

import os
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
logger = logging.getLogger("reconciler")

import requests
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, IGNORE_SYMBOLS


def _send(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(msg)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.error("Telegram send failed: %s", e)


def get_broker_symbols() -> set | None:
    """Fetch live holdings from Upstox (CNC delivery positions).

    Returns set of symbols on success, or None if ALL API calls failed
    (auth error / network issue) — caller must skip mismatch check in that case.
    """
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN missing from .env")

    base_url = "https://api.upstox.com/v2"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    symbols = set()
    any_success = False

    # Long-term holdings (CNC delivery)
    try:
        resp = requests.get(f"{base_url}/portfolio/long-term-holdings", headers=headers, timeout=10)
        resp.raise_for_status()
        any_success = True
        for pos in resp.json().get("data", []):
            qty = int(pos.get("quantity", 0))
            if qty > 0:
                sym = pos.get("tradingsymbol", "") + ".NS"
                if sym not in IGNORE_SYMBOLS:
                    symbols.add(sym)
    except Exception as e:
        logger.warning("Holdings fetch failed: %s", e)

    # Short-term positions (T1 / unsettled)
    try:
        resp = requests.get(f"{base_url}/portfolio/short-term-positions", headers=headers, timeout=10)
        resp.raise_for_status()
        any_success = True
        for pos in resp.json().get("data", []):
            qty = int(pos.get("quantity", 0))
            if qty > 0 and pos.get("product") == "CNC":
                sym = pos.get("tradingsymbol", "") + ".NS"
                if sym not in IGNORE_SYMBOLS:
                    symbols.add(sym)
    except Exception as e:
        logger.warning("Short-term positions fetch failed: %s", e)

    if not any_success:
        logger.warning("All Upstox API calls failed — token may be expired. Skipping mismatch check.")
        return None

    return symbols


def get_db_symbols() -> set:
    """Fetch open positions from local DB."""
    from db.repository import load_positions
    return {p.symbol for p in load_positions("OPEN")}


def run_reconcile():
    now = datetime.now().strftime("%d %b %Y %H:%M")
    logger.info("Starting reconciliation — %s", now)

    try:
        broker_syms = get_broker_symbols()
    except RuntimeError as e:
        _send(f"⚠️ <b>Reconciler Error</b>\n{e}\n<i>{now}</i>")
        sys.exit(1)

    if broker_syms is None:
        # All API calls failed — likely stale token. Don't raise false mismatch.
        logger.warning("Reconciliation skipped — Upstox API unavailable (check token).")
        print(f"[{now}] SKIP — API unavailable, token may be expired.")
        return

    db_syms = get_db_symbols()

    # Stocks DB thinks are open but broker doesn't hold
    ghost = db_syms - broker_syms
    # Stocks broker holds that DB doesn't know about (excluding manual holdings)
    unknown = broker_syms - db_syms

    logger.info("DB open: %s", db_syms)
    logger.info("Broker holds: %s", broker_syms)
    logger.info("Ghost (DB-only): %s", ghost)
    logger.info("Unknown (broker-only): %s", unknown)

    if not ghost and not unknown:
        logger.info("Reconciliation OK — DB and broker match.")
        print(f"[{now}] OK — {len(db_syms)} positions match.")
        return

    lines = [f"⚠️ <b>Position Mismatch — {now}</b>"]

    if ghost:
        lines.append(
            "\n🔴 <b>DB OPEN but broker has NO position:</b>\n"
            + "\n".join(f"  • {s}" for s in sorted(ghost))
            + "\n<i>Sell order may have failed — check manually.</i>"
        )

    if unknown:
        lines.append(
            "\n🟡 <b>Broker holds but DB has no record:</b>\n"
            + "\n".join(f"  • {s}" for s in sorted(unknown))
            + "\n<i>May be manual trade or sync lag — verify.</i>"
        )

    _send("\n".join(lines))
    logger.warning("Mismatch detected — alert sent.")
    sys.exit(2)


if __name__ == "__main__":
    run_reconcile()
