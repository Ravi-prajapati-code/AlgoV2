"""Dashboard — Open Positions page."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json
import os
import streamlit as st
import pandas as pd

from dashboard.charts import sector_allocation_pie
from config.settings import OUTPUTS_DIR


def _fetch_broker_holdings() -> list:
    """Returns all holdings from Upstox broker."""
    try:
        live_trading = os.getenv("LIVE_TRADING", "false").lower() in ("true", "1", "yes")
        if not live_trading:
            return []
        from broker.upstox import UpstoxBroker
        broker = UpstoxBroker()
        return broker.get_holdings()
    except Exception as e:
        st.caption(f"Live price fetch failed: {e}")
        return []


def render():
    st.title("Open Positions")

    state_path = os.path.join(OUTPUTS_DIR, "portfolio_state.json")
    if not os.path.exists(state_path):
        st.warning("No portfolio data yet.")
        return

    with open(state_path) as f:
        state = json.load(f)

    # ── Fetch all broker holdings ──────────────────────────────────────
    all_holdings = _fetch_broker_holdings()
    holdings_map  = {h.symbol: h for h in all_holdings}

    strategy_positions = state.get("positions", [])
    strategy_symbols   = {p["symbol"] for p in strategy_positions}

    # ── Section 1: Strategy-managed positions ─────────────────────────
    st.subheader("Strategy Positions")
    price_source = "Live (Upstox)" if all_holdings else f"Last run ({state.get('date', '—')})"
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
