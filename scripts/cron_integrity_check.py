"""
Cron integrity check — runs before market open.

Guards against the exact failure mode found 2026-07-13: a second,
unmarked "main.py run" entry sitting in crontab alongside the real
AlgoTrading-managed one, both firing at market close and risking a
live double-run. setup_cron.sh only dedupes lines carrying its own
"# AlgoTrading" marker, so a manually-added or half-edited line is
invisible to it. This check reads the raw crontab, independent of the
marker, so it catches drift setup_cron.sh itself cannot see.

Alerts via Telegram (same pattern as health_check.py) if:
  - more than one line invokes "main.py run" (paper or live, marked or not)
  - any such line invokes "main.py run --live" without going through flock
  - any two live-trading processes are running concurrently right now
"""

import os
import re
import subprocess
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

RUN_LINE_RE = re.compile(r"main\.py\s+run\b")
LIVE_FLAG_RE = re.compile(r"--live\b")
FLOCK_RE = re.compile(r"\bflock\b")


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


def get_crontab_lines() -> list[str]:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return [l for l in result.stdout.splitlines() if l.strip() and not l.strip().startswith("#")]


def get_live_pids() -> list[str]:
    result = subprocess.run(["pgrep", "-f", "main.py run --live"], capture_output=True, text=True)
    return [p for p in result.stdout.splitlines() if p.strip()]


if __name__ == "__main__":
    today_label = date.today().strftime("%a %d %b %Y")
    problems = []

    run_lines = [l for l in get_crontab_lines() if RUN_LINE_RE.search(l)]
    if len(run_lines) == 0:
        problems.append("No `main.py run` entry found in crontab at all — strategy will not run today.")
    elif len(run_lines) > 1:
        listing = "\n".join(f"  `{l.strip()}`" for l in run_lines)
        problems.append(f"{len(run_lines)} separate `main.py run` cron entries found (expected 1):\n{listing}")

    for l in run_lines:
        if LIVE_FLAG_RE.search(l) and not FLOCK_RE.search(l):
            problems.append(f"Live-trading cron entry has no `flock` guard — a duplicate/retry could double-run:\n  `{l.strip()}`")

    live_pids = get_live_pids()
    if len(live_pids) > 1:
        problems.append(f"{len(live_pids)} live-trading processes (`main.py run --live`) running concurrently right now: PIDs {', '.join(live_pids)}")

    if problems:
        body = "\n\n".join(problems)
        send(f"*Cron Integrity Check — {today_label}* 🔴\n{body}")
        sys.exit(1)
    else:
        print(f"[{today_label}] Cron integrity OK — {len(run_lines)} daily-run entry, no duplicate live processes.")
