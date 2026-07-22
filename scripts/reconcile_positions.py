"""
Position Reconciler — compares DB open positions vs actual Upstox holdings.

Broker-only mismatches (broker holds a symbol DB doesn't know about) are
auto-fixed: inserted via the same origin-recovery logic daily_runner.py's
sync uses, reusing add_or_update_broker_positions() so there's one vetted
implementation instead of two that can drift apart. This was previously
alert-only, which is how the CEMPRO.NS live-buy-not-yet-persisted incident
(2026-07-21) sat undetected until caught manually the same day — see
[[cempro_orphan_position_bug_20260722]] in memory.

DB-only mismatches (DB thinks a position is open, broker doesn't have it)
stay alert-only — auto-closing on a possibly-stale/erroring broker read
risks masking a real failed-sell that needs a human look, so that side is
intentionally NOT auto-fixed.

Cron (server — all times IST):
  # 09:20 IST Mon-Fri — after token refresh (08:30 IST), checks yesterday's positions
  20 9 * * 1-5  cd /home/ubuntu/AlgoV2 && .venv/bin/python scripts/reconcile_positions.py >> logs/reconcile.log 2>&1
"""

import sys
import logging
from datetime import date
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
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


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


def get_broker_positions():
    """Fetch live CNC delivery positions from Upstox via the shared broker abstraction.

    Returns list[LivePosition] on success, or None if the broker returned
    nothing at all (auth error / network issue) — caller must skip the
    mismatch check in that case rather than treat it as "broker holds 0".
    """
    from broker.upstox import UpstoxBroker

    broker = UpstoxBroker()
    positions = broker.get_positions()
    if not positions:
        logger.warning("Broker returned 0 positions — token may be expired or API down. Skipping check.")
        return None
    # Keep parity with the old conservative filter: only CNC delivery counts here.
    return [p for p in positions if p.product == "CNC"]


def get_db_symbols() -> set:
    """Fetch open positions from local DB."""
    from db.repository import load_positions
    return {p.symbol for p in load_positions("OPEN")}


def run_reconcile():
    now_str = date.today().strftime("%d %b %Y")
    logger.info("Starting reconciliation — %s", now_str)

    try:
        broker_positions = get_broker_positions()
    except Exception as e:
        _send(f"⚠️ <b>Reconciler Error</b>\n{e}\n<i>{now_str}</i>")
        sys.exit(1)

    if broker_positions is None:
        print(f"[{now_str}] SKIP — API unavailable, token may be expired.")
        return

    from db.repository import load_positions

    broker_syms = {p.symbol for p in broker_positions}
    db_syms = get_db_symbols()

    ghost = db_syms - broker_syms      # DB open, broker doesn't have it — alert only
    unknown = broker_syms - db_syms    # Broker holds, DB doesn't know — auto-fixed below

    logger.info("DB open: %s", db_syms)
    logger.info("Broker holds: %s", broker_syms)
    logger.info("Ghost (DB-only): %s", ghost)
    logger.info("Unknown (broker-only): %s", unknown)

    if not ghost and not unknown:
        logger.info("Reconciliation OK — DB and broker match.")
        print(f"[{now_str}] OK — {len(db_syms)} positions match.")
        return

    fixed = []
    fix_failed = []
    if unknown:
        from runner.daily_runner import add_or_update_broker_positions

        db_positions = {p.symbol: p for p in load_positions(status="OPEN")}
        unknown_positions = [p for p in broker_positions if p.symbol in unknown]
        try:
            add_or_update_broker_positions(date.today(), unknown_positions, db_positions)
            # Verify it actually landed before calling it fixed.
            still_missing = unknown - {p.symbol for p in load_positions(status="OPEN")}
            fixed = sorted(unknown - still_missing)
            fix_failed = sorted(still_missing)
        except Exception as e:
            logger.error("Auto-fix failed: %s", e)
            fix_failed = sorted(unknown)

    lines = [f"⚠️ <b>Position Mismatch — {now_str}</b>"]

    if ghost:
        lines.append(
            "\n🔴 <b>DB OPEN but broker has NO position:</b>\n"
            + "\n".join(f"  • {s}" for s in sorted(ghost))
            + "\n<i>Sell order may have failed — check manually.</i>"
        )

    if fixed:
        lines.append(
            "\n🟢 <b>Broker-only positions auto-recorded to DB:</b>\n"
            + "\n".join(f"  • {s}" for s in fixed)
            + "\n<i>Origin classified strategy/manual per prior-record heuristic — verify.</i>"
        )

    if fix_failed:
        lines.append(
            "\n🟡 <b>Broker holds but auto-fix failed, DB still has no record:</b>\n"
            + "\n".join(f"  • {s}" for s in fix_failed)
            + "\n<i>Needs manual insert — check logs/reconcile.log.</i>"
        )

    _send("\n".join(lines))
    logger.warning("Mismatch detected — %d auto-fixed, %d ghost, %d fix-failed.",
                    len(fixed), len(ghost), len(fix_failed))
    if ghost or fix_failed:
        sys.exit(2)


if __name__ == "__main__":
    run_reconcile()
