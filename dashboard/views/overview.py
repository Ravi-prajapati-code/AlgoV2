"""Dashboard — Overview page: portfolio value, equity curve, daily P&L."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json
import os
import streamlit as st
import pandas as pd

from dashboard.charts import equity_curve_chart, pnl_bar_chart
from config.settings import INITIAL_CAPITAL, OUTPUTS_DIR


def render():
    st.title("Portfolio Overview")

    # Load portfolio state
    state_path = os.path.join(OUTPUTS_DIR, "portfolio_state.json")
    if not os.path.exists(state_path):
        st.warning("No portfolio data yet. Run `python main.py run` first.")
        return

    with open(state_path) as f:
        state = json.load(f)

    # ── KPI Cards ──────────────────────────────────────────────────────
    total = state.get("total_value", INITIAL_CAPITAL)
    cash  = state.get("cash", 0)
    day   = state.get("daily_pnl", 0)
    n_pos = state.get("open_positions", 0)

    # Use pre-computed fields from JSON when available (written by new signal_output.py).
    # Fall back to live DB query for old JSON files that predate this field.
    if "total_deployed" in state and "return_pct" in state:
        baseline       = state["baseline"]
        total_injected = state["total_injected"]
        deployed       = state["total_deployed"]
        cum            = state["cumulative_pnl"]
        pct            = state["return_pct"]
    else:
        from db.repository import load_baseline_capital, total_capital_injected_ever
        baseline       = load_baseline_capital() or INITIAL_CAPITAL
        total_injected = total_capital_injected_ever()
        deployed       = baseline + total_injected
        cum            = total - deployed
        pct            = (cum / deployed * 100) if deployed > 0 else 0.0

    # Compute market value from positions list so Cash + Market Val = Total Value
    positions_data = state.get("positions", [])
    market_val  = sum(p.get("current_price", 0) * p.get("shares", 0) for p in positions_data)
    unrealized  = sum(p.get("unrealized_pnl", 0) for p in positions_data)
    unreal_pct  = (unrealized / (market_val - unrealized) * 100) if (market_val - unrealized) > 0 else 0.0

    col1, col2, col3, col4, col5 = st.columns([2, 1.5, 1.5, 1.5, 1])
    col1.metric("Total Value",    f"₹{total:,.0f}", f"₹{cum:+,.0f} ({pct:+.1f}% on deployed)")
    col2.metric("Cash",           f"₹{cash:,.0f}")
    col3.metric("Invested",       f"₹{market_val:,.0f}")
    col4.metric("Unrealized P&L", f"₹{unrealized:+,.0f}", f"{unreal_pct:+.1f}%")
    col5.metric("Positions",      n_pos)

    if total_injected > 0:
        st.caption(
            f"Last updated: {state.get('date', '—')} | "
            f"Starting: ₹{baseline:,.0f} | Injected: ₹{total_injected:,.0f} | "
            f"Total deployed: ₹{deployed:,.0f}"
        )
    else:
        st.caption(f"Last updated: {state.get('date', '—')} | Starting capital: ₹{baseline:,.0f}")

    st.divider()

    # ── Equity Curve ───────────────────────────────────────────────────
    try:
        from db.repository import load_snapshots
        snaps = load_snapshots()
        if snaps:
            dates     = [s.date for s in snaps]
            values    = [s.total_value for s in snaps]
            daily_pnl = [s.daily_pnl for s in snaps]
            # Collect injection dates/amounts for vertical annotations
            injections = [
                (s.date, s.capital_injected)
                for s in snaps if (s.capital_injected or 0) > 500
            ]
            st.plotly_chart(
                equity_curve_chart(dates, values, baseline, deployed, injections),
                use_container_width=True,
            )
            st.plotly_chart(pnl_bar_chart(dates, daily_pnl), use_container_width=True)
        else:
            st.info("Equity curve will appear after the first trading day.")
    except Exception as e:
        st.error(f"Could not load equity curve: {e}")
