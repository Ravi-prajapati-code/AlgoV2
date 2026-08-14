"""Dashboard — Reconciliation (docs/60 Page 14): history of both
strategies' broker-reconciliation checks, read-only.

Both checks this page displays (main_vs_broker, momentum_atr_vs_broker,
plus position_collision_sum) are written by
scripts/observability_snapshot.py::run_reconciliation() -- alert-only,
same discipline as scripts/reconcile_positions.py's DB-only-mismatch side.
momentum_atr_vs_broker closes a real production blind spot flagged in
docs/60 §1.7: no prior code anywhere reconciled momentum_atr's own
positions against the broker. Nothing on this page writes anything --
auto_repaired is always False for every row today; a future auto-fix
would be a trading-code change requiring separate approval, not a
dashboard feature.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import streamlit as st

from db import reporting_repo


def render():
    st.title("Reconciliation")
    st.caption(
        "History of broker-reconciliation checks for both strategies, most recent first. "
        "Alert-only — nothing here has ever auto-repaired a mismatch."
    )

    rows = reporting_repo.load_recent_reconciliation(limit=200)
    if not rows:
        st.info("No reconciliation checks recorded yet — Phase 1 observability cron not running.")
        return

    latest_ts = rows[0]["ts"]
    latest_cycle = [r for r in rows if r["ts"] == latest_ts]
    fails = [r for r in latest_cycle if r["result"] == "FAIL"]
    warnings = [r for r in latest_cycle if r["result"] == "WARNING"]

    st.subheader("Latest Cycle")
    st.caption(f"As of {latest_ts}")
    col1, col2, col3 = st.columns(3)
    col1.metric("Checks Run", len(latest_cycle))
    col2.metric("Failures", len(fails))
    col3.metric("Warnings", len(warnings))

    if fails:
        st.error(f"🔴 {len(fails)} check(s) FAILED in the latest cycle.")
    elif warnings:
        st.warning(f"🟡 {len(warnings)} check(s) raised a WARNING in the latest cycle.")
    else:
        st.success("🟢 All checks passed in the latest cycle.")

    st.divider()
    st.subheader("History")

    def _fmt_row(r):
        return {
            "Timestamp": r["ts"],
            "Strategy": r["strategy_id"] or "both",
            "Check": r["check_name"],
            "Result": r["result"],
            "Detail": r["detail"] or "—",
            "Auto-Repaired": "Yes" if r["auto_repaired"] else "No",
        }

    df = pd.DataFrame([_fmt_row(r) for r in rows])

    def highlight(row):
        color = {"FAIL": "#3d1a1a", "WARNING": "#3d3a1a", "PASS": "#1a3d2b"}.get(row["Result"], "")
        return [f"background-color: {color}; color: #ffffff" if color else ""] * len(row)

    st.dataframe(df.style.apply(highlight, axis=1), use_container_width=True, height=450, hide_index=True)
