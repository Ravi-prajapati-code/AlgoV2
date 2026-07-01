"""Dashboard — Today's Signals page."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json
import os
import streamlit as st
import pandas as pd

from config.settings import OUTPUTS_DIR


def render():
    st.title("Today's Signals")

    sig_path = os.path.join(OUTPUTS_DIR, "signals.json")
    if not os.path.exists(sig_path):
        st.warning("No signals yet. Run `python main.py run` first.")
        return

    with open(sig_path) as f:
        data = json.load(f)

    st.caption(f"Generated: {data.get('generated_at', 'unknown')}")

    signals = data.get("signals", [])
    if not signals:
        st.info("No signals generated today.")
        return

    buys  = [s for s in signals if s["action"] == "BUY"]
    sells = [s for s in signals if s["action"] == "SELL"]
    holds = [s for s in signals if s["action"] == "HOLD"]

    col1, col2, col3 = st.columns(3)
    col1.metric("BUY",  len(buys))
    col2.metric("SELL", len(sells))
    col3.metric("HOLD", len(holds))

    def _fmt(val, decimals=2):
        if val == "-" or val is None:
            return "-"
        try:
            return f"{float(val):.{decimals}f}"
        except (TypeError, ValueError):
            return str(val)

    if buys:
        st.subheader("🟢 BUY Signals")
        buy_rows = []
        for s in buys:
            ind = s.get("indicators", {})
            buy_rows.append({
                "Symbol":    s["symbol"],
                "Price":     f"₹{s['price']:.2f}",
                "Score":     s["score"],
                "RSI":       _fmt(ind.get("rsi"), 1),
                "MACD H":    _fmt(ind.get("macd_hist"), 3),
                "Vol Ratio": _fmt(ind.get("vol_ratio"), 2),
                "Reason":    s["reason"],
            })
        st.dataframe(pd.DataFrame(buy_rows), use_container_width=True)

    if sells:
        st.subheader("🔴 SELL Signals")
        sell_rows = [
            {"Symbol": s["symbol"], "Price": f"₹{s['price']:.2f}", "Reason": s["reason"]}
            for s in sells
        ]
        st.dataframe(pd.DataFrame(sell_rows), use_container_width=True)

    if holds:
        st.subheader("⏸ Holding")
        hold_rows = [
            {"Symbol": s["symbol"], "Price": f"₹{s['price']:.2f}"}
            for s in holds
        ]
        st.dataframe(pd.DataFrame(hold_rows), use_container_width=True)
