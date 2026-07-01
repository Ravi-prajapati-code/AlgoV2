"""
Headless Upstox token refresh using TOTP.

Flow:
  1. POST mobile number → Upstox sends OTP (we use TOTP instead)
  2. POST TOTP code → get auth code
  3. Exchange auth code → access token
  4. Update .env with new token
  5. Notify via Telegram

Run:
  python broker/token_refresh.py

Cron (8:45 AM IST = 03:15 UTC):
  15 3 * * 1-5 cd ~/AlgoV2 && .venv/bin/python broker/token_refresh.py
"""

import os
import sys
import json
import time
import logging
import re
import urllib.parse
from pathlib import Path

import requests
import pyotp
from dotenv import load_dotenv, set_key

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv(override=True)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

ENV_PATH = Path(__file__).parent.parent / ".env"

API_KEY        = os.getenv("UPSTOX_API_KEY")
API_SECRET     = os.getenv("UPSTOX_API_SECRET")
REDIRECT_URI   = os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:8080")
MOBILE         = os.getenv("UPSTOX_MOBILE")
PIN            = os.getenv("UPSTOX_PIN")
TOTP_SECRET    = os.getenv("UPSTOX_TOTP_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID")

BASE = "https://api.upstox.com/v2"

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
}


def _send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg},
            timeout=10,
        )
    except Exception:
        pass


def _get_auth_code() -> str:
    session = requests.Session()

    # Step 1 — initiate login, get state/session cookies
    auth_url = (
        f"{BASE}/login/authorization/dialog"
        f"?client_id={API_KEY}&redirect_uri={urllib.parse.quote(REDIRECT_URI)}&response_type=code"
    )
    r = session.get(auth_url, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True, timeout=15)
    logger.info("Auth dialog status: %s", r.status_code)

    # Step 2 — submit mobile number
    r2 = session.post(
        f"{BASE}/login/authorization/step-one",
        data={"mobile_num": MOBILE},
        headers=HEADERS,
        timeout=15,
    )
    logger.info("Step 1 (mobile): %s — %s", r2.status_code, r2.text[:120])
    if r2.status_code != 200:
        raise RuntimeError(f"Step 1 failed: {r2.text}")

    # Step 3 — submit PIN
    r3 = session.post(
        f"{BASE}/login/authorization/step-two",
        data={"mobile_num": MOBILE, "client_id": API_KEY, "pin": PIN},
        headers=HEADERS,
        timeout=15,
    )
    logger.info("Step 2 (PIN): %s — %s", r3.status_code, r3.text[:120])
    if r3.status_code != 200:
        raise RuntimeError(f"Step 2 failed: {r3.text}")

    # Step 4 — generate TOTP and submit
    totp = pyotp.TOTP(TOTP_SECRET)
    otp_code = totp.now()
    logger.info("Generated TOTP: %s", otp_code)

    r4 = session.post(
        f"{BASE}/login/authorization/step-three",
        data={
            "mobile_num": MOBILE,
            "client_id":  API_KEY,
            "pin":        PIN,
            "otp":        otp_code,
        },
        headers=HEADERS,
        allow_redirects=False,
        timeout=15,
    )
    logger.info("Step 3 (TOTP): %s — %s", r4.status_code, r4.text[:200])

    # Auth code is in redirect Location or response body
    code = None

    # Check Location header redirect
    location = r4.headers.get("Location", "")
    if "code=" in location:
        parsed = urllib.parse.urlparse(location)
        code = urllib.parse.parse_qs(parsed.query).get("code", [None])[0]

    # Check response body
    if not code:
        try:
            body = r4.json()
            code = body.get("data", {}).get("code") or body.get("code")
        except Exception:
            pass

    # Fallback: regex scan body
    if not code:
        match = re.search(r'"code"\s*:\s*"([^"]+)"', r4.text)
        if match:
            code = match.group(1)

    if not code:
        raise RuntimeError(f"Could not extract auth code. Response: {r4.text[:300]}")

    logger.info("Auth code obtained: %s...", code[:10])
    return code


def _exchange_code(code: str) -> str:
    r = requests.post(
        f"{BASE}/login/authorization/token",
        data={
            "code":          code,
            "client_id":     API_KEY,
            "client_secret": API_SECRET,
            "redirect_uri":  REDIRECT_URI,
            "grant_type":    "authorization_code",
        },
        headers=HEADERS,
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Token exchange failed: {r.text}")
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {r.text}")
    return token


def _update_env(token: str):
    set_key(str(ENV_PATH), "UPSTOX_ACCESS_TOKEN", token)
    logger.info("Updated .env with new token")


def refresh() -> bool:
    logger.info("=== Upstox Token Refresh ===")

    if not all([API_KEY, API_SECRET, MOBILE, PIN, TOTP_SECRET]):
        msg = "[TokenRefresh] Missing env vars. Check UPSTOX_MOBILE, UPSTOX_PIN, UPSTOX_TOTP_SECRET."
        logger.error(msg)
        _send_telegram(msg)
        return False

    try:
        code  = _get_auth_code()
        token = _exchange_code(code)
        _update_env(token)

        # Reload env so supervisor-restarted processes pick up new token
        os.environ["UPSTOX_ACCESS_TOKEN"] = token

        _send_telegram("✅ Upstox token refreshed successfully. Live trading ready.")
        logger.info("Token refresh complete.")
        return True

    except Exception as e:
        msg = f"❌ Upstox token refresh FAILED: {e}"
        logger.error(msg)
        _send_telegram(msg)
        return False


if __name__ == "__main__":
    success = refresh()
    sys.exit(0 if success else 1)
