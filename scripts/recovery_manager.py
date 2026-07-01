#!/usr/bin/env python3
"""
Recovery Manager (D) — operational watchdog.

Checks:
  1. UPSTOX_ACCESS_TOKEN present and not expired
  2. Today's daily runner log exists and completed successfully
  3. DB is accessible and not corrupted
  4. Dashboard process is running

Alerts via Telegram on failure. Can auto-restart the dashboard.

Crontab (server — crontab must have TZ=UTC at top):
  # 03:35 UTC = 09:05 IST — check token before market open
  35 3 * * 1-5  cd /home/ubuntu/AlgoV2 && .venv/bin/python scripts/recovery_manager.py --check token >> logs/recovery.log 2>&1

  # 10:15 UTC = 15:45 IST — check runner completed after market close
  15 10 * * 1-5  cd /home/ubuntu/AlgoV2 && .venv/bin/python scripts/recovery_manager.py --check all >> logs/recovery.log 2>&1
"""

import argparse
import logging
import os
import sys
import subprocess
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("recovery_manager")

RUNNER_LOG_PATTERN = str(BASE_DIR / "logs" / "daily_run_{date}.log")
RUNNER_COMPLETE_MARKER = "=== Daily Runner complete:"
DASHBOARD_PORT = 8501


# ── Individual Checks ──────────────────────────────────────────────────────────

def check_token() -> tuple[bool, str]:
    """Verify UPSTOX_ACCESS_TOKEN is present."""
    from dotenv import load_dotenv
    load_dotenv(override=True)
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not token:
        return False, "UPSTOX_ACCESS_TOKEN missing from .env"
    if len(token) < 20:
        return False, f"UPSTOX_ACCESS_TOKEN looks invalid (len={len(token)})"
    return True, f"Token present (len={len(token)})"


def check_runner(today: date = None) -> tuple[bool, str]:
    """Verify today's daily runner completed successfully."""
    today = today or date.today()
    log_path = RUNNER_LOG_PATTERN.format(date=today.strftime("%Y%m%d"))

    if not os.path.exists(log_path):
        return False, f"Runner log missing: {log_path}"

    try:
        with open(log_path) as f:
            content = f.read()
        if RUNNER_COMPLETE_MARKER in content:
            return True, f"Runner completed OK: {log_path}"
        # Check for crash markers
        if "Traceback" in content or "CRITICAL" in content:
            lines = [l for l in content.splitlines() if "Traceback" in l or "CRITICAL" in l]
            return False, f"Runner crashed: {lines[-1] if lines else 'see log'}"
        return False, f"Runner log exists but completion marker missing: {log_path}"
    except Exception as e:
        return False, f"Could not read runner log: {e}"


def check_db() -> tuple[bool, str]:
    """Verify DB is accessible and has recent snapshots."""
    try:
        from db.repository import init_db, load_snapshots
        init_db()
        snaps = load_snapshots()
        if not snaps:
            return True, "DB OK (no snapshots yet — live trading not started)"
        last = max(snaps, key=lambda s: s.date)
        days_ago = (date.today() - last.date).days
        if days_ago > 5:
            return False, f"Last snapshot is {days_ago} days old ({last.date})"
        return True, f"DB OK — last snapshot: {last.date} (₹{last.total_value:,.0f})"
    except Exception as e:
        return False, f"DB check failed: {e}"


def check_dashboard() -> tuple[bool, str]:
    """Verify dashboard Streamlit process is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "streamlit run"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            pids = result.stdout.strip().split()
            return True, f"Dashboard running (PID {', '.join(pids)})"
        return False, "Dashboard process not found"
    except Exception as e:
        return False, f"Dashboard check failed: {e}"


def restart_dashboard() -> bool:
    """Restart the Streamlit dashboard."""
    try:
        subprocess.run(["pkill", "-f", "streamlit run"], capture_output=True)
        import time
        time.sleep(2)
        venv_python = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".venv/bin/python3"
        )
        app_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "dashboard/app.py"
        )
        log_out = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "logs/dashboard.out.log"
        )
        log_err = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "logs/dashboard.err.log"
        )
        with open(log_out, "a") as out, open(log_err, "a") as err:
            subprocess.Popen(
                [venv_python, "-m", "streamlit", "run", app_path,
                 "--server.port", str(DASHBOARD_PORT),
                 "--server.address", "0.0.0.0",
                 "--server.headless", "true"],
                stdout=out, stderr=err,
                start_new_session=True,
            )
        logger.info("[Recovery] Dashboard restarted.")
        return True
    except Exception as e:
        logger.error("[Recovery] Dashboard restart failed: %s", e)
        return False


def _alert(message: str):
    """Send Telegram alert."""
    try:
        from notifications.telegram import send_message
        import html as _html
        send_message(f"🚨 <b>AlgoV2 Recovery Alert</b>\n\n{_html.escape(message)}")
    except Exception as e:
        logger.warning("[Recovery] Telegram alert failed: %s", e)


# ── Main ──────────────────────────────────────────────────────────────────────

def run_checks(mode: str = "all"):
    today = date.today()
    results = {}

    if mode in ("all", "token"):
        ok, msg = check_token()
        results["token"] = (ok, msg)
        logger.info("[Token] %s — %s", "OK" if ok else "FAIL", msg)

    if mode in ("all", "runner"):
        ok, msg = check_runner(today)
        results["runner"] = (ok, msg)
        logger.info("[Runner] %s — %s", "OK" if ok else "FAIL", msg)

    if mode in ("all", "db"):
        ok, msg = check_db()
        results["db"] = (ok, msg)
        logger.info("[DB] %s — %s", "OK" if ok else "FAIL", msg)

    if mode in ("all", "dashboard"):
        ok, msg = check_dashboard()
        results["dashboard"] = (ok, msg)
        logger.info("[Dashboard] %s — %s", "OK" if ok else "FAIL", msg)

        if not ok:
            logger.info("[Recovery] Attempting dashboard restart...")
            restart_dashboard()
            ok2, msg2 = check_dashboard()
            results["dashboard"] = (ok2, f"{msg} → restart → {msg2}")

    # Build alert if any failures
    failures = [(k, msg) for k, (ok, msg) in results.items() if not ok]
    if failures:
        alert_lines = ["Failures detected:"]
        for check, msg in failures:
            alert_lines.append(f"  ❌ {check.upper()}: {msg}")
        alert_lines.append(f"\nServer: {datetime.now().isoformat()[:19]}")
        _alert("\n".join(alert_lines))
        logger.error("[Recovery] %d failure(s): %s", len(failures), failures)
        sys.exit(1)
    else:
        logger.info("[Recovery] All checks passed.")


def main():
    parser = argparse.ArgumentParser(description="AlgoV2 Recovery Manager")
    parser.add_argument("--check", default="all",
                        choices=["all", "token", "runner", "db", "dashboard"],
                        help="Which checks to run")
    args = parser.parse_args()
    run_checks(args.check)


if __name__ == "__main__":
    main()
