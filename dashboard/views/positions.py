"""Dashboard — Open Positions page."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json
import os
from dataclasses import dataclass
from datetime import datetime

import streamlit as st
import pandas as pd

from dashboard.charts import sector_allocation_pie
from config.settings import OUTPUTS_DIR


@dataclass
class _Holding:
    """Shim so downstream code (written for broker/base.py's LivePosition)
    doesn't care whether a holding came from a live call or a DB snapshot."""
    symbol: str
    quantity: int
    avg_price: float
    ltp: float


def _fetch_broker_holdings():
    """Reads the latest broker_snapshot row from the reporting DB (docs/60
    Phase 1) instead of calling Upstox live on every render -- that live
    call was firing on every page load with no caching, hammering the
    broker API for no reason. Snapshots land here every ~15min via
    scripts/observability_snapshot.py; this page shows their age so the
    data is never presented as real-time when it isn't.
    Returns (holdings_list, snapshot_ts_or_None)."""
    try:
        from db import reporting_repo
        snap = reporting_repo.load_latest_broker_snapshot()
    except Exception as e:
        st.caption(f"Broker snapshot read failed: {e}")
        return [], None
    if snap is None:
        return [], None
    holdings = [
        _Holding(symbol=sym, quantity=int(h["qty"]), avg_price=float(h["avg_price"]), ltp=float(h["ltp"]))
        for sym, h in snap["holdings"].items()
    ]
    return holdings, snap["ts"]


def render():
    st.title("Open Positions")

    state_path = os.path.join(OUTPUTS_DIR, "portfolio_state.json")
    if not os.path.exists(state_path):
        st.warning("No portfolio data yet.")
        return

    with open(state_path) as f:
        state = json.load(f)

    # ── Latest broker snapshot (periodic, not live-per-render) ─────────
    all_holdings, snapshot_ts = _fetch_broker_holdings()
    holdings_map  = {h.symbol: h for h in all_holdings}
    if snapshot_ts:
        try:
            age_min = (datetime.now() - datetime.fromisoformat(snapshot_ts)).total_seconds() / 60
            staleness = f" ({age_min:.0f} min old)" if age_min >= 1 else " (<1 min old)"
        except ValueError:
            staleness = ""
        st.caption(f"Broker snapshot as of **{snapshot_ts}**{staleness} — refreshed periodically, not on page load.")
    elif not all_holdings:
        st.caption("No broker snapshot available yet — showing last-run figures only.")

    strategy_positions = state.get("positions", [])
    strategy_symbols   = {p["symbol"] for p in strategy_positions}

    # ── Section 1: Strategy-managed positions ─────────────────────────
    st.subheader("Strategy Positions")
    price_source = f"Broker snapshot ({snapshot_ts})" if all_holdings else f"Last run ({state.get('date', '—')})"
    st.caption(f"Price source: **{price_source}**")

    if not strategy_positions:
        st.info("No strategy positions currently.")
    else:
        for pos in strategy_positions:
            sym = pos["symbol"]
            if sym in holdings_map:
                h = holdings_map[sym]
                # Upstox avg_price = 0 for T+1 holdings — fall back to ltp to avoid bad display
                broker_avg = h.avg_price if h.avg_price > 0 else h.ltp
                pos["current_price"] = h.ltp
                # Only override entry_price if broker has a valid avg price
                if broker_avg > 0:
                    pos["entry_price"] = broker_avg
                shares = pos.get("shares", 1)
                entry = pos.get("entry_price") or broker_avg
                pnl = (h.ltp - entry) * shares if entry > 0 else 0.0
                pos["unrealized_pnl"] = round(pnl, 2)
                pos["unrealized_pct"] = round(
                    (h.ltp - entry) / entry * 100, 2
                ) if entry > 0 else 0.0

        df = pd.DataFrame(strategy_positions)
        for col in ("unrealized_pnl", "unrealized_pct"):
            if col not in df.columns:
                df[col] = 0.0

        df["Unrealized P&L"] = df["unrealized_pnl"].apply(lambda x: f"₹{x:+,.2f}")
        df["Change %"]       = df["unrealized_pct"].apply(lambda x: f"{x:+.2f}%")

        # trailing_stop is the live protective level (matches the broker GTT) once a
        # position has ratcheted past its entry stop_loss — show both, since stop_loss
        # alone understates protection and can look stale/misleading (2026-07-01 audit).
        all_cols     = ["symbol", "sector", "entry_date", "entry_price", "current_price",
                        "shares", "stop_loss", "trailing_stop", "take_profit",
                        "Unrealized P&L", "Change %"]
        display_cols = [c for c in all_cols if c in df.columns]

        def highlight(row):
            color = "#1a3d2b" if "₹-" not in str(row.get("Unrealized P&L", "")) else "#3d1a1a"
            return [f"background-color: {color}; color: #ffffff"] * len(row)

        st.dataframe(
            df[display_cols].style.apply(highlight, axis=1),
            use_container_width=True, height=300,
        )
        st.plotly_chart(sector_allocation_pie(strategy_positions), use_container_width=True)

    # ── Section 2: Pre-existing holdings (read-only, not managed) ─────
    pre_holdings = [h for h in all_holdings if h.symbol not in strategy_symbols]
    if pre_holdings:
        st.divider()
        st.subheader("Pre-existing Holdings (Read-only)")
        st.caption("Not managed by strategy — no stop loss or exit rules applied.")

        rows = []
        for h in pre_holdings:
            avg = h.avg_price if h.avg_price > 0 else h.ltp
            pnl     = (h.ltp - avg) * h.quantity if avg > 0 else 0.0
            pnl_pct = (h.ltp - avg) / avg * 100   if avg > 0 else 0.0
            rows.append({
                "Symbol":     h.symbol,
                "Avg Buy":    f"₹{avg:.2f}" if avg > 0 else "—",
                "LTP":        f"₹{h.ltp:.2f}",
                "Qty":        h.quantity,
                "P&L":        pnl,
                "P&L %":      f"{pnl_pct:+.2f}%",
                "Value":      f"₹{h.ltp * h.quantity:,.0f}",
            })

        df2 = pd.DataFrame(rows)

        def color_pnl(val):
            if isinstance(val, (int, float)):
                return f"color: {'#00c853' if val > 0 else '#ff1744'}; font-weight: bold"
            return ""

        st.dataframe(
            df2.style.map(color_pnl, subset=["P&L"]),
            use_container_width=True, hide_index=True,
        )
