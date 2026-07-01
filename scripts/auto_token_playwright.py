"""
Upstox Auto Token — Playwright Browser Automation
Uses real Chromium browser to bypass API fingerprinting / TOTP verify issues.

Flow:
  1. Open Upstox auth dialog in headless Chromium
  2. Fill mobile → TOTP → PIN via real browser
  3. Intercept redirect to localhost:8080 to capture auth code
  4. Exchange code for access_token
  5. Save to .env

Setup:
  pip install playwright pyotp
  playwright install chromium
"""

import os
import re
import sys
import time
import pyotp
import urllib.parse
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

ROOT     = Path(__file__).parent.parent
ENV_PATH = ROOT / ".env"
load_dotenv(ENV_PATH)

API_KEY      = os.getenv("UPSTOX_API_KEY", "")
API_SECRET   = os.getenv("UPSTOX_API_SECRET", "")
REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "http://localhost:8080")
MOBILE       = os.getenv("UPSTOX_MOBILE", "")
TOTP_SECRET  = os.getenv("UPSTOX_TOTP_SECRET", "")
PIN          = os.getenv("UPSTOX_PIN", "")
BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID      = os.getenv("TELEGRAM_CHAT_ID", "")


def _ts():
    return datetime.now().strftime("%H:%M:%S")


def _log(msg):
    print(f"[{_ts()}] {msg}", flush=True)


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


def _exchange_code(code: str) -> str:
    _log("Exchanging auth code for access token...")
    r = requests.post(
        "https://api.upstox.com/v2/login/authorization/token",
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={
            "code":          code,
            "client_id":     API_KEY,
            "client_secret": API_SECRET,
            "redirect_uri":  REDIRECT_URI,
            "grant_type":    "authorization_code",
        },
        timeout=20,
    )
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise ValueError(f"No access_token in response: {r.text}")
    return token


def auto_login_playwright() -> str:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    if not all([API_KEY, API_SECRET, MOBILE, TOTP_SECRET, PIN]):
        raise ValueError("Missing .env vars: UPSTOX_API_KEY, API_SECRET, MOBILE, TOTP_SECRET, PIN")

    auth_url = (
        f"https://api.upstox.com/v2/login/authorization/dialog"
        f"?client_id={API_KEY}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}"
        f"&response_type=code"
    )

    captured_code = {}

    def _click_btn(page, texts, label="", timeout=8000):
        """Click first visible enabled button matching any of the text strings."""
        for text in texts:
            sel = f"button:not([disabled]):has-text('{text}')"
            try:
                page.wait_for_selector(sel, state="visible", timeout=timeout)
                page.click(sel)
                _log(f"Clicked [{label}] '{text}'")
                return True
            except Exception:
                continue
        _log(f"WARNING: could not click any of {texts} for [{label}]")
        return False

    # Detect available browser: prefer Google Chrome (non-snap), fall back to snap chromium
    import shutil
    _chrome_path = shutil.which("google-chrome") or shutil.which("google-chrome-stable") or "/usr/bin/chromium-browser"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=_chrome_path,
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()

        # Monitor ALL URL changes (SPA window.location changes + real navigations)
        def _on_navigated(frame):
            if frame != page.main_frame:
                return
            url = frame.url
            _log(f"URL → {url[:100]}")
            if url.startswith(REDIRECT_URI) or "localhost" in url or "127.0.0.1" in url:
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                code = parsed.get("code", [None])[0]
                if code:
                    captured_code["value"] = code
                    _log(f"Auth code captured!")

        page.on("framenavigated", _on_navigated)

        # Intercept ALL requests — catch the localhost redirect with code
        def _on_request(route, request):
            url = request.url
            if "localhost" in url or "127.0.0.1" in url:
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                code = parsed.get("code", [None])[0]
                if code and "value" not in captured_code:
                    captured_code["value"] = code
                    _log(f"Auth code from route intercept: ...{code[-8:]}")
                route.abort()
            else:
                route.continue_()

        # Also listen to ALL requests (catches the navigate-to-localhost event)
        def _on_any_request(request):
            url = request.url
            if "localhost" in url or "127.0.0.1" in url:
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                code = parsed.get("code", [None])[0]
                if code and "value" not in captured_code:
                    captured_code["value"] = code
                    _log(f"Auth code from request listener: ...{code[-8:]}")

        page.on("request", _on_any_request)

        # Route patterns must match the actual URL scheme
        redirect_pattern = REDIRECT_URI.rstrip("/") + "**"
        ctx.route(redirect_pattern, _on_request)
        ctx.route("http://localhost*", _on_request)
        ctx.route("http://127.0.0.1*", _on_request)

        _log("Opening Upstox auth dialog...")
        try:
            page.goto(auth_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            _log(f"Goto note: {e}")

        if "value" in captured_code:
            browser.close()
            return _exchange_code(captured_code["value"])

        # ── Step 1: Mobile number ──────────────────────────────────────────
        _log("Waiting for mobile input...")
        page.wait_for_selector("#mobileNum, input[type='tel'], input[type='text']", timeout=15000)
        page.screenshot(path="/tmp/upstox_step1.png")
        _log("Screenshot: /tmp/upstox_step1.png")

        for sel in ["input#mobileNum", "input[name='mobileNum']", "input[type='tel']", "input[type='text']"]:
            try:
                if page.is_visible(sel, timeout=1000):
                    page.fill(sel, MOBILE)
                    _log(f"Mobile filled: {sel}")
                    break
            except Exception:
                continue

        _click_btn(page, ["Get OTP", "Continue", "Next", "Submit"], label="mobile")
        time.sleep(3)  # wait for OTP page to load
        page.screenshot(path="/tmp/upstox_step2.png")
        _log("Screenshot: /tmp/upstox_step2.png")

        # ── Step 2: TOTP/OTP ───────────────────────────────────────────────
        _log("Waiting for OTP/TOTP input field...")
        page.wait_for_selector("input", timeout=10000)

        totp_obj = pyotp.TOTP(TOTP_SECRET)
        totp_code = totp_obj.now()
        # Wait for a fresh code if near end of 30s window (< 5s remaining)
        remaining = 30 - (int(time.time()) % 30)
        if remaining < 5:
            _log(f"TOTP expires in {remaining}s — waiting for fresh code...")
            time.sleep(remaining + 1)
            totp_code = totp_obj.now()
        _log(f"TOTP code: {totp_code} (valid for {30 - (int(time.time()) % 30)}s)")

        # React inputs require keyboard events, not fill(). Use JS + dispatchEvent.
        def _react_set(page, selector, value):
            return page.evaluate("""([sel, val]) => {
                const el = document.querySelector(sel) || document.querySelector('input');
                if (!el) return false;
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                setter.call(el, val);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }""", [selector, value])

        # Try JS setter first, fall back to real keystrokes (more reliable for React)
        ok = _react_set(page, "#otpNum", totp_code)
        if not ok:
            ok = _react_set(page, "input", totp_code)
        _log(f"TOTP set via JS: {totp_code} (ok={ok})")

        # If JS setter failed, type digit-by-digit to trigger React synthetic events
        if not ok:
            try:
                inp = page.query_selector("#otpNum") or page.query_selector("input")
                if inp:
                    inp.click()
                    inp.type(totp_code, delay=80)
                    _log(f"TOTP typed via keyboard fallback")
            except Exception as te:
                _log(f"TOTP keyboard fallback failed: {te}")

        time.sleep(1)
        _click_btn(page, ["Continue", "Verify", "Submit"], label="TOTP")

        # Wait for transition to PIN page (up to 15s)
        page.wait_for_timeout(4000)
        page.screenshot(path="/tmp/upstox_step3.png")
        _log("Screenshot: /tmp/upstox_step3.png")

        # ── Step 3: PIN ────────────────────────────────────────────────────
        _log("Waiting for PIN field...")
        try:
            page.wait_for_selector(
                "input[type='password'], input[placeholder*='pin' i], #pinCode",
                timeout=30000,
            )
        except Exception:
            page.screenshot(path="/tmp/upstox_pin_timeout.png")
            _log("PIN field timeout — screenshot: /tmp/upstox_pin_timeout.png")
            raise

        ok = _react_set(page, "#pinCode", PIN)
        if not ok:
            ok = _react_set(page, "input[type='password']", PIN)
        if not ok:
            ok = _react_set(page, "input", PIN)
        _log(f"PIN set via JS (ok={ok})")

        if not ok:
            try:
                inp = (page.query_selector("#pinCode")
                       or page.query_selector("input[type='password']")
                       or page.query_selector("input"))
                if inp:
                    inp.click()
                    inp.type(PIN, delay=80)
                    _log("PIN typed via keyboard fallback")
            except Exception as pe:
                _log(f"PIN keyboard fallback failed: {pe}")

        time.sleep(1)  # let React enable the Continue button
        _click_btn(page, ["Continue", "Login", "Submit"], label="PIN")

        # ── Wait for redirect ──────────────────────────────────────────────
        _log("Waiting for auth code redirect (up to 30s)...")
        deadline = time.time() + 30
        while time.time() < deadline:
            if "value" in captured_code:
                break
            # Also check current page URL directly
            try:
                cur_url = page.url
                if cur_url.startswith(REDIRECT_URI) or "localhost" in cur_url:
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(cur_url).query)
                    code = parsed.get("code", [None])[0]
                    if code:
                        captured_code["value"] = code
                        _log(f"Auth code from page.url poll!")
                        break
            except Exception:
                pass
            time.sleep(0.5)

        page.screenshot(path="/tmp/upstox_final.png")
        _log("Final screenshot: /tmp/upstox_final.png")
        browser.close()

    if "value" not in captured_code:
        raise ValueError(
            "Auth code not captured after 30s. "
            "Check /tmp/upstox_step*.png & /tmp/upstox_final.png"
        )

    return _exchange_code(captured_code["value"])


def main():
    print(f"\n=== Upstox Playwright Token [{_ts()}] ===")
    try:
        token = auto_login_playwright()
        _update_env(token)
        _log(f"Token saved to .env (length={len(token)})")
        _telegram(
            f"*Upstox Token Refreshed* ✅\n"
            f"Playwright auto-login at {_ts()} IST. Strategy ready."
        )
        print(f"[{_ts()}] Done. Token valid until midnight IST.\n")
    except Exception as e:
        import traceback
        _log(f"ERROR: {e}")
        traceback.print_exc()
        _telegram(f"*Upstox Token FAILED* ❌\n`{e}`\nManual refresh needed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
