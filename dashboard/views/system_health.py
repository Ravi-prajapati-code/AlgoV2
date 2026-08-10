"""Dashboard — System Health & Cron (docs/60 Pages 11+12 combined):
today's SLA checkpoints for both strategies, MAIN's precompute stall
count, and the full static cron inventory (docs/60 §1.6, SSH-confirmed
against the live crontab) with known coverage gaps flagged explicitly
rather than silently omitted.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from datetime import date

import streamlit as st

# Static cron inventory (docs/60 §1.6) -- IST times, SSH-confirmed against
# the live crontab. Kept as a literal table here, not derived, because no
# single DB row proves "this line exists in root's crontab" -- that fact
# only lives in the doc + the server itself.
CRON_INVENTORY = [
    ("08:30", "token_refresh.py", "shared", ""),
    ("08:50", "precompute_momentum_atr_ranking.py", "momentum_atr", ""),
    ("09:00–15:00 /30min", "smoke_test.py", "shared", "Import-check only — does NOT cover either strategy's precompute/SLA scripts."),
    ("09:05", "token check", "shared", ""),
    ("09:17", "run_momentum_atr_live.py --live", "momentum_atr", ""),
    ("09:20", "reconcile_positions.py", "main", "momentum_atr symbols excluded from its 'unknown' set only."),
    ("09:25", "daily_pnl_summary.py", "main", ""),
    ("09:30", "precompute_main_indicators.py", "main", ""),
    ("14:55", "main.py run --live", "main", ""),
    ("15:35", "health_check.py + cron_integrity_check.py", "main", ""),
    ("15:40", "gtt_coverage.py", "main", "Includes GOLDBEES-fungibility check vs momentum_atr."),
    ("15:41", "gtt_price_audit.py", "main", ""),
    ("15:45", "recovery_manager.py --check all", "main", ""),
    ("16:00", "momentum_atr_sla_check.py", "momentum_atr", ""),
    ("16:00", "main_sla_check.py", "main", ""),
    ("16:30", "backup_db.py", "shared", "⚠️ trading.db only — momentum_atr.db is never backed up by any cron job."),
    ("18:05 / 18:30-Fri", "universe_scheduler.py", "shared", ""),
    ("18:45 last-Fri-monthly", "walk_forward.py", "main (research gate)", ""),
    ("00:30 Sun", "weekly log/backup cleanup", "shared", ""),
]

KNOWN_GAPS = [
    "momentum_atr.db is never backed up by any cron job (backup_db.py only covers trading.db).",
    "smoke_test.py is a lightweight import-check — it does not exercise either strategy's "
    "precompute or SLA scripts, so it can stay green while those are broken.",
    "momentum_atr has no structured stall-count column — its SLA checkpoint detail is free text only.",
]


def _sla_badge(status: str) -> str:
    return {"OK": "🟢", "ABORTED": "🟡", "CRASHED": "🔴", "FAILED": "🔴"}.get(status, "⚪")


def render():
    st.title("System Health & Cron")
    today = date.today()

    # ── SLA checkpoints, both strategies ────────────────────────────────
    st.subheader(f"Today's SLA Checkpoints — {today}")
    col_main, col_atr = st.columns(2)

    with col_main:
        st.markdown("**Main Strategy**")
        try:
            from db.repository import load_sla_checkpoints, load_precompute_indicators

            checkpoints = load_sla_checkpoints(today)
            if not checkpoints:
                st.info("No checkpoints recorded yet today.")
            else:
                for step, info in checkpoints.items():
                    st.write(f"{_sla_badge(info['status'])} **{step}** — {info['status']} ({info['ts']})")
                    if info["detail"]:
                        st.caption(info["detail"])

            precompute = load_precompute_indicators(today)
            if precompute is not None:
                st.metric("Fetch stall count (today's precompute)", precompute.get("stalls", 0))
            else:
                st.caption("No precompute_indicators row yet today.")
        except Exception as e:
            st.error(f"Could not load MAIN SLA data: {e}")

    with col_atr:
        st.markdown("**Momentum × ATR**")
        try:
            from db.momentum_atr_repo import load_sla_checkpoints as load_atr_sla

            checkpoints = load_atr_sla(today)
            if not checkpoints:
                st.info("No checkpoints recorded yet today.")
            else:
                for step, info in checkpoints.items():
                    st.write(f"{_sla_badge(info['status'])} **{step}** — {info['status']} ({info['ts']})")
                    if info["detail"]:
                        st.caption(info["detail"])
            st.caption(
                "No structured stall-count column for this strategy — check checkpoint "
                "detail text above for stall mentions, if any."
            )
        except Exception as e:
            st.error(f"Could not load momentum_atr SLA data: {e}")

    st.divider()

    # ── Observability snapshot job's own health (meta, but real) ────────
    st.subheader("Observability Snapshot Cron")
    try:
        from db import reporting_repo

        runs = reporting_repo.load_recent_run_log(limit=5)
        if not runs:
            st.info("No run_log entries yet — cron not deployed or hasn't fired.")
        else:
            for r in runs:
                st.write(f"{_sla_badge(r['status'])} {r['ts']} — {r['status']}" + (f" — {r['detail']}" if r["detail"] else ""))
    except Exception as e:
        st.caption(f"Could not load run log: {e}")

    st.divider()

    # ── Known coverage gaps ──────────────────────────────────────────────
    st.subheader("Known Coverage Gaps")
    for gap in KNOWN_GAPS:
        st.warning(gap)

    st.divider()

    # ── Static cron inventory ────────────────────────────────────────────
    st.subheader("Cron Inventory (docs/60 §1.6, SSH-confirmed)")
    import pandas as pd

    df = pd.DataFrame(CRON_INVENTORY, columns=["Time (IST)", "Job", "Strategy", "Notes"])
    st.dataframe(df, use_container_width=True, hide_index=True)
