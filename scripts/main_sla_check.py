#!/usr/bin/env python3
"""
EOD SLA rollup for the main strategy's precompute/execute split (docs/59).
Runs once per weekday after both pipeline crons have had their window to
fire:

  0 16 * * 1-5 cd /home/ubuntu/AlgoV2 && .venv/bin/python scripts/main_sla_check.py \
    >> logs/main_sla_check.log 2>&1

Reads db/trading.db's sla_checkpoints table (written by
precompute_main_indicators.py at ~09:30 and main.py::cmd_run at 14:55 --
live runs only -- at the end of every attempt: success, abort, or crash)
and reports RED if either stage never reported in at all today, or
reported in but not OK. Same rationale and same evaluate_sla() shape as
scripts/momentum_atr_sla_check.py: a log-exists check can't tell "ran and
aborted" from "never ran" (e.g. the 2026-08-07 SIGTERM-kill produced a
non-empty log with no completion line), but an sla_checkpoints row's
absence unambiguously means that stage never got far enough to report in.

Only two checkpoints exist today: PRECOMPUTE (~09:30 IST) and EXECUTION
(14:55 IST, live runs only -- paper/dry runs intentionally do not write a
checkpoint here, matching momentum_atr's own convention of only attesting
to the production pipeline).
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
        print(f"=== main SLA check: market holiday on {today} -- no check. ===")
        return 0

    from db.repository import load_sla_checkpoints
    checkpoints = load_sla_checkpoints(today)
    result = evaluate_sla(checkpoints)

    icon = "🟢" if result["health"] == "GREEN" else "🔴"
    msg = (
        f"{icon} <b>Main Strategy Health -- {today}</b>\n"
        + "\n".join(result["lines"])
    )
    print(msg)

    if result["health"] == "RED":
        try:
            from notifications.telegram import send_message
            send_message(msg)
        except Exception as e:
            print(f"[main SLA] Failed to send Telegram alert: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
