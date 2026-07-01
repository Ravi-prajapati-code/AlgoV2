"""
Upstox Auto Token Generator — No Browser Required
Pure HTTP + TOTP. Reverse-engineered from login.upstox.com SPA.

Flow:
  1. GET auth dialog → extract user_id + session cookies
  2. POST generate OTP (API user flow — no CAPTCHA, uses X-Device-Details fingerprint)
  3. POST verify TOTP  (pyotp generates code from secret key)
  4. POST 2FA Secret PIN → identity token
  5. POST OAuth authorize → internal redirect code
  6. GET api-v2.upstox.com redirect → real auth code at our redirect_uri
  7. Exchange auth code for access_token

Prerequisites (one-time setup):
    pip install pyotp curl-cffi
    Add to .env:
        UPSTOX_MOBILE=9876543210
        UPSTOX_TOTP_SECRET=ABCD1234EFGH5678   # base32 key from Upstox 2FA setup
        UPSTOX_PIN=123456                      # your 6-digit Upstox Secret PIN

    *** TOTP must be enabled in your Upstox account first ***
    Upstox App → Profile → My Profile → Security → Authenticator App
    Setup the authenticator. The plain-text key shown = UPSTOX_TOTP_SECRET.

Schedule (crontab -e):
    30 8 * * 1-5  cd /path/to/AlgoV2 && python3 scripts/auto_token.py >> logs/token_refresh.log 2>&1
"""

import os
import re
import sys
import uuid
import pyotp
import urllib.parse
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

try:
    from curl_cffi import requests       # Chrome TLS fingerprint — required for Cloudflare bypass
except ImportError:
    import requests                      # fallback — may not work

ROOT = Path(__file__).parent.parent
ENV_PATH = ROOT / ".env"
load_dotenv(ENV_PATH)

API_KEY      = os.getenv("UPSTOX_API_KEY", "")
API_SECRET   = os.getenv("UPSTOX_API_SECRET", "")
REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:8080")
MOBILE       = os.getenv("UPSTOX_MOBILE", "")
TOTP_SECRET  = os.getenv("UPSTOX_TOTP_SECRET", "")
PIN          = os.getenv("UPSTOX_PIN", "")
BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID      = os.getenv("TELEGRAM_CHAT_ID", "")

SERVICE_BASE = "https://service.upstox.com/login/open"
OAUTH_BASE   = "https://service.upstox.com/login/v2/oauth"

# Persistent device UUID — stored in script; server uses this as a device fingerprint.
# Change only if you get device-mismatch errors.
_DEVICE_UUID = os.getenv("UPSTOX_DEVICE_UUID", str(uuid.uuid5(uuid.NAMESPACE_DNS, API_KEY or "default")))


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _device_details() -> str:
    ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    return (
        f"platform=WEB|osName=Linux|osVersion=5.15|appVersion=4.0.0"
        f"|modelName=Chrome|manufacturer=Google"
        f"|uuid={_DEVICE_UUID}"
        f"|userAgent=Upstox 3.0 {ua}"
    )


def _svc_headers(extra: dict | None = None) -> dict:
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://login.upstox.com",
        "Referer": "https://login.upstox.com/",
        "sec-fetch-site": "cross-site",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "Accept-Language": "en-IN,en;q=0.9",
        "X-Device-Details": _device_details(),
        "X-Request-ID": f"WPRO-{uuid.uuid4().hex[:10]}",
    }
    if extra:
        h.update(extra)
    return h


def _update_env(token: str):
    content = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    new_line = f"UPSTOX_ACCESS_TOKEN={token}"
    if re.search(r"^UPSTOX_ACCESS_TOKEN=.*$", content, re.MULTILINE):
        content = re.sub(r"^UPSTOX_ACCESS_TOKEN=.*$", new_line, content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + "\n" + new_line + "\n"
    ENV_PATH.write_text(content)


def _telegram(msg: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception:
        pass


def auto_login() -> str:
    if not all([API_KEY, API_SECRET, MOBILE, TOTP_SECRET, PIN]):
        raise ValueError(
            "Missing .env vars. Need: UPSTOX_API_KEY, UPSTOX_API_SECRET, "
            "UPSTOX_MOBILE, UPSTOX_TOTP_SECRET, UPSTOX_PIN"
        )

    try:
        session = requests.Session(impersonate="chrome110")
    except TypeError:
        session = requests.Session()

    # ── Step 1: Auth dialog → extract user_id ────────────────────────────
    print(f"[{_ts()}] Step 1: Auth dialog → user_id...")
    auth_params = urllib.parse.urlencode({
        "client_id": API_KEY,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
    })
    r1 = session.get(
        f"https://api.upstox.com/v2/login/authorization/dialog?{auth_params}",
        allow_redirects=True,
        timeout=15,
    )
    final_params = urllib.parse.parse_qs(urllib.parse.urlparse(r1.url).query)
    user_id = final_params.get("user_id", [None])[0]
    if not user_id:
        raise ValueError(f"Could not extract user_id. Final URL: {r1.url}")
    print(f"[{_ts()}] user_id: {user_id}")

    # ── Step 2: Generate OTP ──────────────────────────────────────────────
    print(f"[{_ts()}] Step 2: Generate OTP challenge...")
    r2 = session.post(
        f"{SERVICE_BASE}/v6/auth/1fa/otp/generate",
        json={"data": {"mobileNumber": MOBILE, "userId": user_id}},
        headers=_svc_headers(),
        timeout=15,
    )
    body2 = r2.json()
    if not body2.get("success"):
        raise ValueError(f"Generate OTP failed: {body2}")
    validate_token = body2["data"]["validateOTPToken"]
    is_totp = body2["data"].get("isTotpEnabled", False)
    print(f"[{_ts()}] OTP challenge OK. TOTP enabled: {is_totp}")

    if not is_totp:
        raise ValueError(
            "isTotpEnabled=false — Authenticator App not set up for this account.\n"
            "  Setup: Upstox App → Profile → My Profile → Security → Authenticator App\n"
            "  After setup, copy the plain-text key to UPSTOX_TOTP_SECRET in .env"
        )

    # ── Step 3: Verify TOTP ───────────────────────────────────────────────
    totp_code = pyotp.TOTP(TOTP_SECRET).now()
    print(f"[{_ts()}] Step 3: Verify TOTP {totp_code}...")
    r3 = session.post(
        f"{SERVICE_BASE}/v4/auth/1fa/otp-totp/verify",
        json={"data": {"otp": totp_code, "otpType": "TOTP", "token": validate_token}},
        headers=_svc_headers(),
        timeout=15,
    )
    body3 = r3.json()
    if not body3.get("success"):
        raise ValueError(f"TOTP verify failed: {body3}")
    # Extract userId from user profile (needed for 2FA header)
    account_user_id = body3.get("data", {}).get("userProfile", {}).get("userId", user_id)
    print(f"[{_ts()}] TOTP verified. account_user_id: {account_user_id}")

    # ── Step 4: 2FA Secret PIN ────────────────────────────────────────────
    print(f"[{_ts()}] Step 4: Secret PIN...")
    r4 = session.post(
        f"{SERVICE_BASE}/v3/auth/2fa",
        json={"data": {"pin": PIN}},
        headers=_svc_headers({"X-User-Id": str(account_user_id)}),
        params={"client_id": API_KEY, "redirect_uri": REDIRECT_URI},
        timeout=15,
    )
    body4 = r4.json()
    if not body4.get("success"):
        raise ValueError(f"2FA PIN failed: {body4}")
    print(f"[{_ts()}] PIN accepted.")

    # ── Step 5: OAuth Authorize ───────────────────────────────────────────
    print(f"[{_ts()}] Step 5: OAuth authorize...")
    request_id = f"PW3{uuid.uuid4().hex[:16].upper()}"
    r5 = session.post(
        f"{OAUTH_BASE}/authorize",
        json={"data": {"userOAuthApproval": True}},
        headers=_svc_headers(),
        params={
            "client_id": API_KEY,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "requestId": request_id,
        },
        timeout=15,
    )
    body5 = r5.json()
    if not body5.get("success"):
        raise ValueError(f"OAuth authorize failed: {body5}")

    redirect_url = body5["data"].get("redirectUri", "")
    print(f"[{_ts()}] OAuth redirectUri: {redirect_url[:80]}...")

    # ── Step 5.5: Follow internal redirect → get real auth code ──────────
    # redirectUri may point to api-v2.upstox.com which then redirects to our
    # REDIRECT_URI with the real auth code. Follow without landing on localhost.
    parsed = urllib.parse.urlparse(redirect_url)
    code = urllib.parse.parse_qs(parsed.query).get("code", [None])[0]

    if not code and redirect_url:
        # Follow the intermediate redirect (api-v2.upstox.com → our redirect_uri)
        r5b = session.get(redirect_url, allow_redirects=False, timeout=15)
        location = r5b.headers.get("Location", "")
        code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("code", [None])[0]
        if not code:
            # Try following one more hop
            if location:
                r5c = session.get(location, allow_redirects=False, timeout=15)
                location2 = r5c.headers.get("Location", "")
                code = urllib.parse.parse_qs(urllib.parse.urlparse(location2).query).get("code", [None])[0]

    if not code:
        raise ValueError(f"No auth code found. redirectUri: {redirect_url}")
    print(f"[{_ts()}] Auth code received.")

    # ── Step 6: Exchange code for access token ────────────────────────────
    print(f"[{_ts()}] Step 6: Exchange code for token...")
    r6 = requests.post(
        "https://api.upstox.com/v2/login/authorization/token",
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={
            "code": code,
            "client_id": API_KEY,
            "client_secret": API_SECRET,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    r6.raise_for_status()
    token = r6.json().get("access_token")
    if not token:
        raise ValueError(f"No access_token in response: {r6.text}")
    return token


def main():
    print(f"\n=== Upstox Auto Token [{_ts()}] ===")
    try:
        token = auto_login()
        _update_env(token)
        print(f"[{_ts()}] Token saved to .env (length={len(token)})")
        _telegram(
            f"*Upstox Token Refreshed* ✅\n"
            f"Auto-generated at {_ts()} IST. Strategy ready."
        )
        print(f"[{_ts()}] Done. Token valid until midnight IST.\n")
    except Exception as e:
        print(f"[{_ts()}] ERROR: {e}")
        _telegram(f"*Upstox Token FAILED* ❌\n`{e}`\nManual refresh needed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
