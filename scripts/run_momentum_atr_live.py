#!/usr/bin/env python3
"""
Cron entry point for the standalone momentum x ATR live strategy
(docs/57, docs/58; plan velvet-cooking-minsky). Runs once per weekday
pre-market (~9:17 IST):

  17 9 * * 1-5 cd /home/ubuntu/AlgoV2 && flock -n /tmp/algov2_momentum_atr.lock \
    -c "timeout 900 .venv/bin/python scripts/run_momentum_atr_live.py --live" \
    >> logs/momentum_atr_run.log 2>&1

Execution-only -- does no scoring itself. Reads the day's ranking from
db/momentum_atr.db (table daily_ranking), written earlier the same
morning by scripts/precompute_momentum_atr_ranking.py's own cron
(~08:00 IST, see that script's docstring). Split out after the
2026-08-07 first live run: scoring the ~509-symbol universe took ~10min
on a cold cache, so order placement didn't start right at 9:17. If
today's ranking is missing (precompute failed or hasn't run), this
aborts with a Telegram alert rather than falling back to a live compute
that could blow this cron's own 900s budget.

flock/timeout are applied at the cron-line level (matching
runner/daily_runner.py's own convention) -- this script does no locking
itself. Own lock file, never /tmp/algov2_runner.lock (docs/56's
flock-race incident is exactly the failure mode that would reproduce by
sharing one).

Always connects to the real, shared Upstox account -- this strategy
trades real capital per the user's explicit full-live-deployment decision,
there is no PaperBroker path here. --dry-run only gates whether
momentum_atr.execution.run_daily() places orders and mutates the ledger,
not whether the broker connection itself is real (dry-run still validates
the live auth path end-to-end, which is the point of running it once
before the first real cron fire).
"""
import argparse
import html
import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s -- %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("MomentumATRRunner")


def _env_prefix() -> str:
    """Local dev runs and the production cron share one real Telegram
    chat/bot -- a local crash during testing sends a real alert
    indistinguishable from a genuine production incident (happened once,
    2026-08-06: a local dry-run's pre-init_db() crash read as a live abort).
    Prefix anything not from the production server so it's unambiguous."""
    from config.settings import IS_PRODUCTION
    return "" if IS_PRODUCTION else "[LOCAL TEST] "


def _alert_abort(today: date, reason: str) -> None:
    """Best-effort Telegram alert on abort -- mirrors
    runner/daily_runner.py:_alert_run_abort. A silently-skipped run leaves
    any open momentum_atr positions unmanaged until the next session."""
    try:
        from notifications.telegram import send_message
        send_message(
            f"{_env_prefix()}\U0001F6D1 <b>momentum_atr run ABORTED -- {today}</b>\n"
            f"Reason: {html.escape(str(reason))}\n\n"
            f"No orders placed today. Any open momentum_atr positions are "
            f"UNMANAGED until the next run."
        )
    except Exception as e:
        logger.warning("[momentum_atr] Failed to send abort alert: %s", e)


def _send_summary_alert(today: date, dry_run: bool, summary: dict) -> None:
    """Best-effort Telegram summary of what run_daily() actually did (or,
    in dry-run, what it would have done)."""
    try:
        from notifications.telegram import send_message
        mode = "DRY-RUN" if dry_run else "LIVE"
        lines = [f"{_env_prefix()}<b>momentum_atr [{mode}] -- {today}</b>"]
        if dry_run:
            for o in summary.get("planned_orders", []):
                lines.append(f"- would {o['side']} {o['qty']} {o['symbol']}")
        else:
            lines.append(
                f"Equity: Rs.{summary.get('total_equity', 0):,.0f} | "
                f"Cash: Rs.{summary.get('cash', 0):,.0f} | "
                f"Open: {summary.get('open_positions', '?')}"
            )
        if summary.get("kill_switch_tripped"):
            lines.append("⚠️ Kill-switch: TRIPPED")
        for a in summary.get("actions", []):
            lines.append(f"- {a}")
        send_message("\n".join(lines))
    except Exception as e:
        logger.warning("[momentum_atr] Failed to send summary alert: %s", e)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true",
                       help="Place real orders and mutate the ledger")
    mode.add_argument("--dry-run", action="store_true",
                       help="Run the full pipeline, place no orders, mutate no ledger state")
    args = parser.parse_args()

    today = date.today()

    from runner.daily_runner import _is_market_holiday
    if _is_market_holiday(today):
        logger.info("=== momentum_atr: market holiday on %s -- no run. ===", today)
        return 0

    from db import momentum_atr_repo as repo
    if args.live and repo.snapshot_exists_for_date(today):
        logger.warning(
            "[momentum_atr] snapshot for %s already exists -- already ran today. "
            "Aborting to prevent double execution.", today,
        )
        return 0

    try:
        from broker.upstox import UpstoxBroker
        broker = UpstoxBroker()
        logger.info("[momentum_atr] Connected to Upstox")
    except Exception as e:
        logger.error("[momentum_atr] Upstox broker init failed: %s", e)
        _alert_abort(today, f"Upstox broker init failed: {e}")
        return 1

    try:
        from momentum_atr.execution import run_daily
        summary = run_daily(broker, today, dry_run=args.dry_run)
    except Exception as e:
        logger.exception("[momentum_atr] run_daily crashed")
        _alert_abort(today, f"Unhandled exception: {e}")
        return 1

    if summary.get("aborted"):
        logger.error("[momentum_atr] run aborted: %s", summary)
        return 1

    logger.info("[momentum_atr] run complete: %s", summary)
    _send_summary_alert(today, args.dry_run, summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
