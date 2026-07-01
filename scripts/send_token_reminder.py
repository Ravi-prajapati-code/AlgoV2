"""
Morning Telegram reminder to refresh the Upstox token.
Run by cron at 08:45 IST Mon-Fri.
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


def check_token_valid() -> bool:
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not token or len(token) < 50:
        return False
    try:
        resp = requests.get(
            "https://api.upstox.com/v2/user/profile",
            headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
            timeout=8,
        )
        return resp.status_code == 200
    except Exception:
        return False


def send(msg: str):
    if not BOT_TOKEN or not CHAT_ID:
        print(msg)
        return
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
        timeout=10,
    )


if __name__ == "__main__":
    today = date.today().strftime("%a %d %b %Y")
    if check_token_valid():
        send(
            f"*Algo Trading — {today}* ✅\n"
            f"Upstox token is valid. Strategy will run at 15:45 IST."
        )
        print(f"[{today}] Token valid — reminder sent.")
    else:
        send(
            f"*Algo Trading — {today}* ⚠️\n"
            f"Upstox token expired or missing!\n\n"
            f"Run now to refresh:\n"
            f"`python3 scripts/refresh_token.py`\n\n"
            f"Must be done before 15:45 IST or today's run will fail."
        )
        print(f"[{today}] Token INVALID — alert sent.")
