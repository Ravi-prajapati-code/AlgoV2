"""Dashboard — Live Positions (docs/60 Page 3): both strategies' open
positions side by side, direct DB reads. Neither strategy persists a
current-price column on its position row, so LTP is overlaid from the
latest broker_snapshot (docs/60 Phase 1) rather than a live broker call
per render -- same caching discipline as the existing Open Positions page
(dashboard/views/positions.py).

Distinct from that page: this one covers momentum_atr too (which has no
positions view of its own anywhere in the dashboard), and does not
attempt the broker-collision detection that belongs to Page 4
(Collision/Overlap) -- this page is direct-read only, Low risk per docs/60.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from datetime import datetime

import pandas as pd
import streamlit as st

from db.repository import load_positions as load_main_positions
from db.momentum_atr_repo import load_positions as load_atr_positions
from db import reporting_repo


def _broker_ltp_map():
    try:
        snap = reporting_repo.load_latest_broker_snapshot()
    except Exception as e:
        st.caption(f"Broker snapshot read failed: {e}")
        return {}, None
    if snap is None:
        return {}, None
    return {sym: h["ltp"] for sym, h in snap["holdings"].items()}, snap["ts"]


def _staleness_caption(snap_ts):
    if not snap_ts:
        st.caption("No broker snapshot yet — prices unavailable, showing entry price only.")
        return
    try:
        age_min = (datetime.now() - datetime.fromisoformat(snap_ts)).total_seconds() / 60
        staleness = f" ({age_min:.0f} min old)" if age_min >= 1 else " (<1 min old)"
    except ValueError:
        staleness = ""
    st.caption(f"Prices from broker snapshot as of **{snap_ts}**{staleness} — not live per-render.")


def render():
    st.title("Live Positions")

    ltp_map, snap_ts = _broker_ltp_map()
    _staleness_caption(snap_ts)

    col_main, col_atr = st.columns(2)

    with col_main:
        st.subheader("Main Strategy")
        positions = load_main_positions(status="OPEN")
        if not positions:
            st.info("No open positions.")
        else:
            rows = []
            for p in positions:
                ltp = ltp_map.get(p.symbol)
                current = ltp if ltp is not None else p.entry_price
                rows.append({
                    "Symbol": p.symbol,
                    "Sector": p.sector,
                    "Entry Date": str(p.entry_date),
                    "Entry ₹": f"₹{p.entry_price:,.2f}",
                    "Current ₹": f"₹{current:,.2f}" if ltp is not None else "— (no snapshot)",
                    "Shares": p.shares,
                    "Unrealized P&L": f"₹{p.unrealized_pnl(current):+,.2f}" if ltp is not None else "—",
                    "Unrealized %": f"{p.unrealized_pct(current):+.2f}%" if ltp is not None else "—",
                    "Origin": p.origin,
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with col_atr:
        st.subheader("Momentum × ATR")
        positions = load_atr_positions("OPEN")
        if not positions:
            st.info("No open positions.")
        else:
            rows = []
            for p in positions:
                ltp = ltp_map.get(p.symbol)
                current = ltp if ltp is not None else p.entry_price
                pnl = (current - p.entry_price) * p.shares
                pnl_pct = (current - p.entry_price) / p.entry_price * 100 if p.entry_price else 0.0
                rows.append({
                    "Symbol": p.symbol,
                    "Entry Date": str(p.entry_date),
                    "Entry ₹": f"₹{p.entry_price:,.2f}",
                    "Current ₹": f"₹{current:,.2f}" if ltp is not None else "— (no snapshot)",
                    "Shares": p.shares,
                    "Unrealized P&L": f"₹{pnl:+,.2f}" if ltp is not None else "—",
                    "Unrealized %": f"{pnl_pct:+.2f}%" if ltp is not None else "—",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
