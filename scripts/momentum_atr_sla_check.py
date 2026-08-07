#!/usr/bin/env python3
"""
EOD SLA rollup for momentum_atr (docs/57, docs/58; user-requested watchdog,
2026-08-07: "I would build it before adding any more features"). Runs once
per weekday after both pipeline crons have had their window to fire:

  0 16 * * 1-5 cd /home/ubuntu/AlgoV2 && .venv/bin/python scripts/momentum_atr_sla_check.py \
    >> logs/momentum_atr_sla_check.log 2>&1

Reads db/momentum_atr.db's sla_checkpoints table (written by
precompute_momentum_atr_ranking.py and run_momentum_atr_live.py at the end
of every attempt -- success, abort, or crash) and reports RED if either
stage never reported in at all today, or reported in but not OK. This is
deliberately stronger than scripts/health_check.py's "log file exists and
is non-empty" check: a log can exist and be non-empty even when the
process died before doing anything real (e.g. a flock deadlock that never
even reaches the first logger.info call writes nothing, so log-exists is
blind to it too) -- a step simply absent from sla_checkpoints means that
stage never got far enough to report in, which is the actual failure mode
behind this repo's own documented week-long silent outage.

Only two checkpoints exist today: PRECOMPUTE (08:50 IST) and EXECUTION
(9:17 IST, live runs only -- --dry-run intentionally does not write a
checkpoint here, since dry-runs are manual testing, not the production
pipeline this watchdog is meant to attest to). There is no EOD PnL job or
separate order-fill/DB-update step for momentum_atr to check independently
-- those all happen atomically inside one run_daily() call today, so a 6-
step SLA would be checkpoints that can never actually diverge from
EXECUTION's own status. If a real EOD step (PnL snapshot, etc.) gets built
later, add a third checkpoint here rather than splitting EXECUTION
artificially.
"""
import os
import sys
from datetime import date
from typing import Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REQUIRED_STEPS = ["PRECOMPUTE", "EXECUTION"]


def evaluate_sla(checkpoints: Dict[str, dict]) -> dict:
    """Pure decision function -- no I/O, so it's directly unit-testable.
    Returns {"health": "GREEN"|"RED", "lines": [...]} describing each
    required step's status. A step key absent from `checkpoints` is
    reported as MISSING (never ran), distinct from a present but
    non-"OK" status (ran and failed) -- both are RED, but the message
    differs because they point at different bugs."""
    lines = []
    health = "GREEN"
    for step in REQUIRED_STEPS:
        cp = checkpoints.get(step)
        if cp is None:
            lines.append(f"- {step}: MISSING (never reported in)")
            health = "RED"
        elif cp["status"] != "OK":
            lines.append(f"- {step}: {cp['status']} -- {cp.get('detail', '')}")
            health = "RED"
        else:
            lines.append(f"- {step}: OK -- {cp.get('detail', '')}")
    return {"health": health, "lines": lines}


def main() -> int:
    today = date.today()

    from runner.daily_runner import _is_market_holiday
    if _is_market_holiday(today):
        print(f"=== momentum_atr SLA check: market holiday on {today} -- no check. ===")
        return 0

    from db import momentum_atr_repo as repo
    checkpoints = repo.load_sla_checkpoints(today)
    result = evaluate_sla(checkpoints)

    icon = "🟢" if result["health"] == "GREEN" else "🔴"
    msg = (
        f"{icon} <b>momentum_atr Strategy Health -- {today}</b>\n"
        + "\n".join(result["lines"])
    )
    print(msg)

    if result["health"] == "RED":
        try:
            from notifications.telegram import send_message
            send_message(msg)
        except Exception as e:
            print(f"[momentum_atr SLA] Failed to send Telegram alert: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
