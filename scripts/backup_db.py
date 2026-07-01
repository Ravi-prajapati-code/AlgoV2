"""
Nightly DB backup — copies trading.db to db/backups/trading_YYYYMMDD.db.
Keeps last 30 days. Alerts via Telegram on failure.

Cron (add to server):
  # 16:30 IST Mon-Fri — after daily runner + health check complete
  0 11 * * 1-5  cd /home/ubuntu/AlgoV2 && .venv/bin/python scripts/backup_db.py >> logs/backup.log 2>&1
"""

import os
import sys
import shutil
import logging
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("db_backup")

import requests
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

DB_SOURCE   = ROOT / "db" / "trading.db"
BACKUP_DIR  = ROOT / "db" / "backups"
KEEP_DAYS   = 30


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


def run_backup():
    today_str = date.today().strftime("%Y%m%d")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    dest = BACKUP_DIR / f"trading_{today_str}.db"

    if not DB_SOURCE.exists():
        msg = f"⚠️ <b>DB Backup FAILED</b>\nSource not found: {DB_SOURCE}"
        logger.error(msg)
        _send(msg)
        sys.exit(1)

    # SQLite safe copy via sqlite3 backup API
    try:
        import sqlite3
        src_conn = sqlite3.connect(str(DB_SOURCE))
        dst_conn = sqlite3.connect(str(dest))
        src_conn.backup(dst_conn)
        src_conn.close()
        dst_conn.close()
    except Exception as e:
        msg = f"⚠️ <b>DB Backup FAILED</b>\n{e}"
        logger.error(msg)
        _send(msg)
        sys.exit(1)

    size_kb = dest.stat().st_size / 1024
    logger.info("Backup OK: %s (%.1f KB)", dest.name, size_kb)

    # Prune backups older than KEEP_DAYS
    cutoff = date.today() - timedelta(days=KEEP_DAYS)
    pruned = []
    for f in BACKUP_DIR.glob("trading_*.db"):
        try:
            file_date_str = f.stem.split("_")[1]
            from datetime import datetime
            file_date = datetime.strptime(file_date_str, "%Y%m%d").date()
            if file_date < cutoff:
                f.unlink()
                pruned.append(f.name)
        except Exception:
            pass

    if pruned:
        logger.info("Pruned %d old backups: %s", len(pruned), pruned)

    remaining = len(list(BACKUP_DIR.glob("trading_*.db")))
    print(f"[{date.today()}] Backup OK: {dest.name} ({size_kb:.1f} KB). Kept {remaining} backups.")


if __name__ == "__main__":
    run_backup()
