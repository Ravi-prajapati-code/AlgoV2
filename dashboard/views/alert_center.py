"""Dashboard — Alert Center (docs/60 Page 18): all strategy_alert rows,
both strategies, read-only.

Additive alongside the existing Telegram alert path (source_script column
identifies which script raised it) -- this table doesn't replace Telegram,
it's a second, queryable record of the same events. No acknowledge/dismiss
action here: the dashboard is read-only everywhere else in this project
(see CLAUDE.md's strategy-independence constraints) and introducing the
first write action would be a scope decision on its own, not bundled
into this page.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import streamlit as st

from db import reporting_repo


def render():
    st.title("Alert Center")
    st.caption(
        "All recorded alerts for both strategies, most recent first. Read-only — "
        "acknowledge/dismiss is not implemented here; Telegram remains the "
        "actionable channel for these same events."
    )

    rows = reporting_repo.load_recent_alerts(limit=200)
    if not rows:
        st.info("No alerts recorded yet.")
        return

    critical = [r for r in rows if r["severity"] == "CRITICAL"]
    warning = [r for r in rows if r["severity"] == "WARNING"]
    unacked = [r for r in rows if not r["acknowledged"]]

    col1, col2, col3 = st.columns(3)
    col1.metric("Critical", len(critical))
    col2.metric("Warning", len(warning))
    col3.metric("Unacknowledged", len(unacked))

    df = pd.DataFrame([{
        "Timestamp": r["ts"],
        "Strategy": r["strategy_id"] or "both",
        "Severity": r["severity"],
        "Category": r["category"],
        "Message": r["message"],
        "Source": r["source_script"],
        "Acknowledged": "Yes" if r["acknowledged"] else "No",
    } for r in rows])

    def highlight(row):
        color = {"CRITICAL": "#3d1a1a", "WARNING": "#3d3a1a"}.get(row["Severity"], "")
        return [f"background-color: {color}; color: #ffffff" if color else ""] * len(row)

    st.dataframe(df.style.apply(highlight, axis=1), use_container_width=True, height=450, hide_index=True)
