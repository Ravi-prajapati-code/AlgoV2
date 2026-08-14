"""Dashboard — Collision/Overlap View (docs/60 Page 4): per-symbol
SUM(main_qty + momentum_atr_qty) == broker_qty check, from the latest
strategy_position_snapshot cycle (docs/60 Phase 1's observability_snapshot.py
already computes collision_flag at write time -- this page only displays it,
never recomputes or repairs).

Two failure classes shown separately per docs/60 §4:
- Collision: both/either strategy's DB qty sums don't match broker qty for
  a symbol either strategy claims to hold.
- Unattributed: broker holds a symbol neither strategy's DB claims at all
  (Level 2 / strategy_id=NULL in docs/60's terms) -- this is exactly the
  origin="manual" misclassification risk flagged in docs/60 §1.7, made
  visible rather than silently absorbed into one strategy's view.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from datetime import datetime

import pandas as pd
import streamlit as st

from db import reporting_repo


def render():
    st.title("Collision / Overlap View")
    st.caption(
        "Per-symbol reconciliation between both strategies' DB-recorded quantities "
        "and the broker's actual holdings, from the latest snapshot cycle. "
        "Read-only — nothing here auto-repairs a mismatch."
    )

    ts = reporting_repo.load_latest_position_snapshot_ts()
    if ts is None:
        st.info("No position snapshot recorded yet — Phase 1 observability cron not running.")
        return

    try:
        age_min = (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 60
        staleness = f" ({age_min:.0f} min old)" if age_min >= 1 else " (<1 min old)"
    except ValueError:
        staleness = ""
    st.caption(f"Snapshot as of **{ts}**{staleness}.")

    rows = reporting_repo.load_position_snapshots_for_ts(ts)
    if not rows:
        st.info("Snapshot cycle recorded no symbols.")
        return

    collisions = [r for r in rows if r["collision_flag"]]
    unattributed = [
        r for r in rows
        if r["main_qty"] == 0 and r["momentum_atr_qty"] == 0 and r["broker_qty"] > 0
    ]

    col1, col2, col3 = st.columns(3)
    col1.metric("Symbols in Snapshot", len(rows))
    col2.metric("Collisions", len(collisions))
    col3.metric("Unattributed Holdings", len(unattributed))

    st.divider()
    st.subheader("Collisions — main_qty + momentum_atr_qty ≠ broker_qty")
    if not collisions:
        st.success("🟢 None — every symbol's strategy-recorded quantities sum to the broker's qty.")
    else:
        st.error(f"🔴 {len(collisions)} symbol(s) with a quantity mismatch — investigate before trusting either strategy's position count for these.")
        df = pd.DataFrame([{
            "Symbol": r["symbol"],
            "Main Qty": r["main_qty"],
            "Momentum × ATR Qty": r["momentum_atr_qty"],
            "Sum": r["main_qty"] + r["momentum_atr_qty"],
            "Broker Qty": r["broker_qty"],
        } for r in collisions])
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Unattributed Broker Holdings")
    st.caption("Broker holds these, but neither strategy's DB has an open position for them.")
    if not unattributed:
        st.success("🟢 None.")
    else:
        st.warning(f"🟡 {len(unattributed)} symbol(s) held by the broker but not tracked by either strategy.")
        df2 = pd.DataFrame([{"Symbol": r["symbol"], "Broker Qty": r["broker_qty"]} for r in unattributed])
        st.dataframe(df2, use_container_width=True, hide_index=True)
