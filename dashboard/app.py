"""
Streamlit dashboard entry point.
Run: streamlit run dashboard/app.py

Free hosting: deploy to https://streamlit.io/cloud
  → Connect GitHub repo → Set main file: dashboard/app.py
"""

import sys
import os

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()
import streamlit as st

_live = os.getenv("LIVE_TRADING", "false").lower() in ("true", "1", "yes")
_mode_label = "🔴 LIVE Mode" if _live else "📄 Paper Mode"

st.set_page_config(
    page_title="Algo Swing Trader",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Sidebar navigation
st.sidebar.title("📈 Algo Swing Trader")
st.sidebar.caption(f"NSE Swing Trading — {_mode_label}")
st.sidebar.divider()

page = st.sidebar.radio(
    "Navigate",
    ["Overview", "Open Positions", "Today's Signals", "Trade History",
     "Strategy Health", "Backtest",
     "Global Overview (All Strategies)", "Risk Monitor", "System Health & Cron",
     "P&L / Equity Curve", "Signals (DB History)", "ATR Trade Journal",
     "Order Monitor", "Strategy Comparison", "Overlapping Signals"],
    index=0,
)

st.sidebar.divider()
_disclaimer = "⚠️ Live trading active — real money at risk" if _live else "⚠️ Paper trading only — not financial advice"
st.sidebar.caption(
    "Strategy: EMA20/50 + RSI + MACD + Volume\n\n"
    "Broker: Upstox (₹0 delivery brokerage)\n\n"
    "Target: 22%+ CAGR\n\n"
    f"{_disclaimer}"
)

# Route to page
if page == "Overview":
    from dashboard.views.overview import render
    render()

elif page == "Open Positions":
    from dashboard.views.positions import render
    render()

elif page == "Today's Signals":
    from dashboard.views.signals import render
    render()

elif page == "Trade History":
    from dashboard.views.history import render
    render()

elif page == "Strategy Health":
    from dashboard.views.health import render
    render()

elif page == "Backtest":
    from dashboard.views.backtest import render
    render()

elif page == "Global Overview (All Strategies)":
    from dashboard.views.global_overview import render
    render()

elif page == "Risk Monitor":
    from dashboard.views.risk_monitor import render
    render()

elif page == "System Health & Cron":
    from dashboard.views.system_health import render
    render()

elif page == "P&L / Equity Curve":
    from dashboard.views.pnl_equity import render
    render()

elif page == "Signals (DB History)":
    from dashboard.views.signals_db import render
    render()

elif page == "ATR Trade Journal":
    from dashboard.views.atr_trade_journal import render
    render()

elif page == "Order Monitor":
    from dashboard.views.order_monitor import render
    render()

elif page == "Strategy Comparison":
    from dashboard.views.strategy_comparison import render
    render()

elif page == "Overlapping Signals":
    from dashboard.views.overlapping_signals import render
    render()
