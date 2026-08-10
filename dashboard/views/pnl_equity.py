"""Dashboard — P&L / Equity Curve (docs/60 Page 9): both strategies'
equity histories plotted as two separate series on one chart. Never
summed into a single blended line -- the strategies are capital-isolated
(see CLAUDE.md's strategy-independence constraint), so a combined total
would misrepresent a shared pool that doesn't exist.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import plotly.graph_objects as go
import streamlit as st

from db.repository import load_snapshots
from db.momentum_atr_repo import load_equity_snapshots


def render():
    st.title("P&L / Equity Curve")
    st.caption(
        "Two independent series, shown separately — never summed. Each strategy "
        "trades its own capital; a blended total would imply a shared pool that "
        "doesn't exist."
    )

    main_snaps = load_snapshots(limit=2000)
    atr_snaps = load_equity_snapshots(limit=2000)

    if not main_snaps and not atr_snaps:
        st.info("No equity history for either strategy yet.")
        return

    fig = go.Figure()
    if main_snaps:
        fig.add_trace(go.Scatter(
            x=[s.date for s in main_snaps],
            y=[s.strategy_value for s in main_snaps],
            name="Main Strategy",
            line=dict(color="#4da6ff", width=2),
        ))
    if atr_snaps:
        fig.add_trace(go.Scatter(
            x=[s.date for s in atr_snaps],
            y=[s.total_equity for s in atr_snaps],
            name="Momentum × ATR",
            line=dict(color="#00cc88", width=2),
        ))
    fig.update_layout(
        height=450,
        margin=dict(l=20, r=20, t=40, b=20),
        yaxis_title="Strategy Value (₹)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    col_main, col_atr = st.columns(2)

    with col_main:
        st.subheader("Main Strategy")
        if main_snaps:
            latest = main_snaps[-1]
            st.metric("Latest Strategy Value", f"₹{latest.strategy_value:,.0f}")
            st.metric("Cumulative P&L", f"₹{latest.cumulative_pnl:+,.0f}")
            st.caption(f"As of {latest.date}")
        else:
            st.info("No snapshots yet.")

    with col_atr:
        st.subheader("Momentum × ATR")
        if atr_snaps:
            latest = atr_snaps[-1]
            st.metric("Latest Total Equity", f"₹{latest.total_equity:,.0f}")
            st.metric("Drawdown from Peak", f"{latest.drawdown_pct:.2%}")
            st.caption(f"As of {latest.date}")
        else:
            st.info("No snapshots yet.")
