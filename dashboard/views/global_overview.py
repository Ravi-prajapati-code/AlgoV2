"""Dashboard — Global Overview (docs/60 Page 1): both strategies side by
side plus broker-account (Level 2) totals. Sourced entirely from the
observability reporting DB (db/reporting.db), populated periodically by
scripts/observability_snapshot.py -- never a live broker call on render.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from datetime import datetime

import streamlit as st

from db import reporting_repo


def _age_caption(ts: str) -> str:
    try:
        age_min = (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 60
    except ValueError:
        return f"as of {ts}"
    if age_min < 1:
        return f"as of {ts} (<1 min old)"
    return f"as of {ts} ({age_min:.0f} min old)"


def render():
    st.title("Global Overview — All Strategies")

    broker_snap = reporting_repo.load_latest_broker_snapshot()
    registry = {r["strategy_id"]: r for r in reporting_repo.load_strategy_registry()}
    main_cap = reporting_repo.load_latest_strategy_capital_snapshot("main")
    atr_cap = reporting_repo.load_latest_strategy_capital_snapshot("momentum_atr")

    if broker_snap is None and main_cap is None and atr_cap is None:
        st.warning(
            "No observability snapshot yet. This page reads db/reporting.db, "
            "populated by scripts/observability_snapshot.py — that cron hasn't "
            "produced a snapshot yet (or isn't deployed)."
        )
        return

    # ── Level 2: broker account ─────────────────────────────────────────
    st.subheader("Broker Account (Level 2)")
    if broker_snap is None:
        st.info("No broker snapshot yet.")
    else:
        st.caption(_age_caption(broker_snap["ts"]))
        col1, col2, col3 = st.columns(3)
        col1.metric("Broker Cash (available)", f"₹{broker_snap['broker_cash']:,.0f}")
        col2.metric("Broker Total Equity", f"₹{broker_snap['total_equity']:,.0f}")
        col3.metric("Symbols Held", len(broker_snap["holdings"]))

    st.divider()

    # ── Level 1: per-strategy ────────────────────────────────────────────
    st.subheader("Per-Strategy (Level 1)")
    col_main, col_atr = st.columns(2)

    with col_main:
        st.markdown("**Main Strategy**")
        status = registry.get("main", {}).get("status_label", "—")
        st.caption(f"Status: {status}")
        if main_cap is None:
            st.info("No capital snapshot yet.")
        else:
            st.caption(_age_caption(main_cap["ts"]))
            st.metric("Strategy Equity", f"₹{main_cap['strategy_equity']:,.0f}")
            st.metric("Invested", f"₹{main_cap['strategy_invested_value']:,.0f}")
            st.metric("Cash MAIN Sees", f"₹{main_cap['strategy_available_cash']:,.0f}")
            st.caption(main_cap["source_note"])

    with col_atr:
        st.markdown("**Momentum × ATR**")
        status = registry.get("momentum_atr", {}).get("status_label", "—")
        st.caption(f"Status: {status}")
        if atr_cap is None:
            st.info("No capital snapshot yet.")
        else:
            st.caption(_age_caption(atr_cap["ts"]))
            st.metric("Strategy Equity", f"₹{atr_cap['strategy_equity']:,.0f}")
            st.metric("Invested", f"₹{atr_cap['strategy_invested_value']:,.0f}")
            st.metric("Allocated Cap", f"₹{atr_cap['strategy_allocated_cash']:,.0f}"
                       if atr_cap["strategy_allocated_cash"] is not None else "N/A")
            st.metric("Cash (internal ledger)", f"₹{atr_cap['strategy_available_cash']:,.0f}")
            st.caption(atr_cap["source_note"])

    st.divider()

    # ── Unallocated broker cash — only honest relative to momentum_atr's
    # real cap. MAIN has no allocation cap (confirmed: no such constant in
    # config/settings.py), so it can draw on any "unallocated" remainder
    # too — this is NOT a clean spare-cash figure, shown with an explicit
    # caveat rather than presented as a clean number (docs/60 Page 1 flags
    # this as a real risk if handled naively).
    if broker_snap is not None and atr_cap is not None and atr_cap["strategy_allocated_cash"] is not None:
        unallocated = broker_snap["total_equity"] - atr_cap["strategy_allocated_cash"]
        st.metric("Broker Equity Outside momentum_atr's Cap", f"₹{unallocated:,.0f}")
        st.caption(
            "This is broker total equity minus momentum_atr's allocated cap only. "
            "MAIN has no allocation cap of its own and can draw on this remainder too — "
            "it is not a clean 'spare/unused' figure, just the headroom above momentum_atr's cap."
        )
