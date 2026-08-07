"""
Post-run health check — runs at 15:55 IST after the 15:45 daily runner.
Alerts via Telegram if today's log is missing/empty, or if the run never
reached completion at all (checked via db.repository.snapshot_exists_for_date
-- the same idempotency marker daily_runner.run() itself uses -- not just
by grepping the log for "ERROR"/"Traceback"). 2026-08-07 incident: the
900s `timeout` cron wrapper SIGTERM-killed the run mid-fetch after a burst
of stalls; the log was neither missing nor empty (500+ lines of normal
per-symbol fetch progress plus some ERROR lines from the stalls), so the
old log-content check would have filed it as a low-severity "errors
detected" WARNING even though ZERO orders were placed and the run never
got anywhere near scoring/execution. A log existing (even with some
errors) is not proof the run finished -- only the snapshot row is.
"""

import os
import sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
LOG_DIR   = ROOT / "logs"


def send(msg: str):
    print(msg)
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"[Telegram] send failed (network?): {e}")


if __name__ == "__main__":
    today_str = date.today().strftime("%Y%m%d")
    today_label = date.today().strftime("%a %d %b %Y")
    log_file = LOG_DIR / f"daily_run_{today_str}.log"

    if not log_file.exists():
        send(
            f"*Algo Health Check — {today_label}* 🔴\n"
            f"Daily runner log not found! The 15:45 run may have failed.\n"
            f"Check: `logs/daily_run_{today_str}.log`"
        )
        print(f"ALERT: log file missing — {log_file}")
        sys.exit(1)

    content = log_file.read_text()
    if not content.strip():
        send(
            f"*Algo Health Check — {today_label}* 🔴\n"
            f"Daily runner log is empty. Run produced no output.\n"
            f"Check: `logs/daily_run_{today_str}.log`"
        )
        print(f"ALERT: log file empty — {log_file}")
        sys.exit(1)

    from db.repository import snapshot_exists_for_date
    if not snapshot_exists_for_date(date.today()):
        send(
            f"*Algo Health Check — {today_label}* 🔴\n"
            f"Daily runner did NOT reach completion -- no snapshot row for "
            f"today (log has output, so it started, but never finished; "
            f"likely killed by the 900s cron timeout mid-run). ZERO orders "
            f"placed today if this happened during the fetch/scoring phase. "
            f"Open positions got no exit/trailing-stop check.\n"
            f"Check: `logs/daily_run_{today_str}.log`"
        )
        print(f"ALERT: run started but never completed (no snapshot row) — {log_file}")
        sys.exit(1)

    if "ERROR" in content or "Traceback" in content:
        # Extract last error line for context
        lines = content.strip().splitlines()
        error_lines = [l for l in lines if "ERROR" in l or "Traceback" in l]
        snippet = "\n".join(error_lines[-3:]) if error_lines else ""
        send(
            f"*Algo Health Check — {today_label}* ⚠️\n"
            f"Errors detected in daily run log:\n`{snippet}`\n"
            f"Full log: `logs/daily_run_{today_str}.log`"
        )
        print(f"WARNING: errors found in log — {log_file}")
    else:
        print(f"[{today_label}] Health check OK — log looks clean.")
