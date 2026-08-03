"""
Pre-flight import smoke test — catches deploy-time breakage (e.g. a removed
config var still referenced by an importer) BEFORE the 15:20 IST live cron
hits it. Run manually right after any deploy; also cron'd at a tight
interval through the trading day so an ad-hoc deploy at any time of day
gets caught with hours of buffer instead of losing that day's run.

Each module is imported in its own subprocess (`python -c "import X"`) so
one broken module can't abort the check for the rest, and so we get a
per-module pass/fail instead of one opaque traceback.
"""
import os
import subprocess
import sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from scripts.health_check import send  # reuse existing Telegram plumbing

# Every module a cron entry point imports at module-load time (see crontab).
# Import-only — no module here should have side effects at import time
# (all real work is gated behind `if __name__ == "__main__":`).
MODULES = [
    "runner.daily_runner",
    "strategy.signals",
    "scripts.reconcile_positions",
    "scripts.recovery_manager",
    "scripts.universe_scheduler",
    "scripts.daily_pnl_summary",
    "scripts.backup_db",
    "scripts.walk_forward",
    "scripts.auto_token_playwright",
    "scripts.send_token_reminder",
    "scripts.cron_integrity_check",
    "monitoring.gtt_coverage",
    "monitoring.gtt_price_audit",
]


def check_module(name: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", f"import {name}"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 0:
        return ""
    last_line = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown error"
    return last_line


if __name__ == "__main__":
    today_label = date.today().strftime("%a %d %b %Y")
    failures = {}
    for mod in MODULES:
        err = check_module(mod)
        if err:
            failures[mod] = err

    if failures:
        lines = "\n".join(f"`{mod}`: {err}" for mod, err in failures.items())
        send(
            f"*Smoke Test FAILED — {today_label}* 🔴\n"
            f"{len(failures)}/{len(MODULES)} module(s) broken on import — "
            f"live cron will crash:\n{lines}"
        )
        print(f"SMOKE TEST FAILED: {len(failures)}/{len(MODULES)} modules broken")
        for mod, err in failures.items():
            print(f"  {mod}: {err}")
        sys.exit(1)

    print(f"[{today_label}] Smoke test OK — all {len(MODULES)} modules import clean.")
