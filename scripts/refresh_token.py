"""
Upstox Daily Token Refresh — One-Click Flow

Steps (all automatic):
  1. Opens Upstox login URL in your browser
  2. Starts a local server on port 8080 to catch the redirect
  3. Exchanges the authorization code for an access token
  4. Writes UPSTOX_ACCESS_TOKEN into .env
  5. Sends a Telegram confirmation message

Run each morning before 09:15 IST:
    python3 scripts/refresh_token.py
"""

import os
import sys
import webbrowser
import threading
import urllib.parse
import re
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from dotenv import load_dotenv

# ── Resolve project root ───────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
ENV_PATH = ROOT / ".env"

load_dotenv(ENV_PATH)

API_KEY      = os.getenv("UPSTOX_API_KEY", "")
API_SECRET   = os.getenv("UPSTOX_API_SECRET", "")
REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "http://localhost:8080")
BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID      = os.getenv("TELEGRAM_CHAT_ID", "")

PORT = int(urllib.parse.urlparse(REDIRECT_URI).port or 8080)

# ── Shared state ───────────────────────────────────────────────────────────
_captured_code: str = ""
_server_done = threading.Event()


class _RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _captured_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [""])[0]

        if code:
            _captured_code = code
            body = b"<h2>Authorization successful! You can close this tab.</h2>"
            self.send_response(200)
        else:
            body = b"<h2>Error: no code in redirect. Try again.</h2>"
            self.send_response(400)

        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        _server_done.set()

    def log_message(self, *_):
        pass  # suppress request logs


def _start_server():
    server = HTTPServer(("", PORT), _RedirectHandler)
    server.handle_request()  # handles exactly one request then exits


def _exchange_code(code: str) -> str:
    resp = requests.post(
        "https://api.upstox.com/v2/login/authorization/token",
        headers={
            "accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "code": code,
            "client_id": API_KEY,
            "client_secret": API_SECRET,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token", "")
    if not token:
        raise ValueError(f"No access_token in response: {resp.text}")
    return token


def _update_env(token: str):
    """Write/replace UPSTOX_ACCESS_TOKEN in .env without touching other lines."""
    content = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    pattern = r"^UPSTOX_ACCESS_TOKEN=.*$"
    new_line = f"UPSTOX_ACCESS_TOKEN={token}"

    if re.search(pattern, content, flags=re.MULTILINE):
        content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + "\n" + new_line + "\n"

    ENV_PATH.write_text(content)


def _send_telegram(msg: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception:
        pass  # Telegram failure is non-fatal


def main():
    if not API_KEY or not API_SECRET:
        print("ERROR: UPSTOX_API_KEY or UPSTOX_API_SECRET missing from .env")
        sys.exit(1)

    # 1. Start local redirect server in background
    t = threading.Thread(target=_start_server, daemon=True)
    t.start()

    # 2. Build auth URL and open browser
    auth_url = (
        "https://api.upstox.com/v2/login/authorization/dialog?"
        + urllib.parse.urlencode({
            "client_id": API_KEY,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
        })
    )
    print(f"\nOpening browser for Upstox login...")
    print(f"If browser does not open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    # 3. Wait for redirect (timeout 120s)
    print("Waiting for authorization (complete login in your browser)...")
    if not _server_done.wait(timeout=120):
        print("ERROR: Timed out waiting for authorization. Try again.")
        sys.exit(1)

    if not _captured_code:
        print("ERROR: No authorization code received.")
        sys.exit(1)

    print("Authorization code received. Exchanging for token...")

    # 4. Exchange code for token
    try:
        token = _exchange_code(_captured_code)
    except Exception as e:
        print(f"ERROR: Token exchange failed: {e}")
        sys.exit(1)

    # 5. Update .env
    _update_env(token)
    print(f"\nToken saved to .env (length={len(token)})")

    # 6. Telegram confirmation
    _send_telegram(
        f"*Upstox Token Refreshed* ✅\n"
        f"Strategy is ready for today's trading session."
    )

    print("\nDone. Token is valid until midnight IST.")
    print("Run paper trading: python3 main.py run")
    print("Run live trading:  python3 main.py run --live")


if __name__ == "__main__":
    main()
