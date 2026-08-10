"""Dashboard — Strategy Comparison (docs/60 Page 2): CAGR/Sharpe/MDD/win
rate side by side for both strategies, computed from each strategy's own
equity history — never blended (see P&L / Equity Curve page's same rule).

Research/Live status is shown straight from strategy_registry.status_label
-- a manually-set field (docs/60 §1.1), not auto-inferred from trade
activity. Auto-inferring it would risk calling a strategy "LIVE" purely
because it has trades, which says nothing about whether it's authorized
for live capital.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import pandas as pd

from db.repository import load_snapshots, load_trades as load_main_trades
from db.momentum_atr_repo import load_equity_snapshots, load_trades as load_atr_trades
from db import reporting_repo
from dashboard.metrics import sharpe, max_dd, cagr, profit_factor


def _main_row():
    snaps = load_snapshots(limit=2000)
    trades = load_main_trades()
    if not snaps:
        return None
    values = [s.strategy_value for s in snaps if s.strategy_value]
    daily_returns = [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values)) if values[i - 1] > 0
    ]
    days = (snaps[-1].date - snaps[0].date).days
    valid_trades = [t for t in trades if t.net_pnl is not None]
    winners = [t for t in valid_trades if t.net_pnl > 0]
    return {
        "CAGR": cagr(values[0], values[-1], days) if values else 0.0,
        "Sharpe": sharpe(daily_returns),
        "Max Drawdown": max_dd(values) if values else 0.0,
        "Win Rate": len(winners) / len(valid_trades) if valid_trades else 0.0,
        "Trade Count": len(trades),
        "Profit Factor": profit_factor(trades),
        "Latest Value": values[-1] if values else 0.0,
    }


def _atr_row():
    snaps = load_equity_snapshots(limit=2000)
    trades = load_atr_trades()
    if not snaps:
        return None
    values = [s.total_equity for s in snaps]
    daily_returns = [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values)) if values[i - 1] > 0
    ]
    days = (snaps[-1].date - snaps[0].date).days
    valid_trades = [t for t in trades if t.net_pnl is not None]
    winners = [t for t in valid_trades if t.net_pnl > 0]
    return {
        "CAGR": cagr(values[0], values[-1], days) if values else 0.0,
        "Sharpe": sharpe(daily_returns),
        "Max Drawdown": max_dd(values) if values else 0.0,
        "Win Rate": len(winners) / len(valid_trades) if valid_trades else 0.0,
        "Trade Count": len(trades),
        "Profit Factor": profit_factor(trades),
        "Latest Value": values[-1] if values else 0.0,
    }


def render():
    st.title("Strategy Comparison")
    st.caption(
        "Each strategy's metrics are computed from its own equity history only — "
        "never blended into a combined figure."
    )

    registry = {r["strategy_id"]: r for r in reporting_repo.load_strategy_registry()}
    main = _main_row()
    atr = _atr_row()

    if main is None and atr is None:
        st.info("No equity history for either strategy yet.")
        return

    def _fmt(row):
        if row is None:
            return {k: "—" for k in ["CAGR", "Sharpe", "Max Drawdown", "Win Rate",
                                       "Trade Count", "Profit Factor", "Latest Value"]}
        return {
            "CAGR": f"{row['CAGR']:.2%}",
            "Sharpe": f"{row['Sharpe']:.2f}",
            "Max Drawdown": f"{row['Max Drawdown']:.2%}",
            "Win Rate": f"{row['Win Rate']:.1%}",
            "Trade Count": row["Trade Count"],
            "Profit Factor": f"{row['Profit Factor']:.2f}" if row["Profit Factor"] != float("inf") else "∞",
            "Latest Value": f"₹{row['Latest Value']:,.0f}",
        }

    main_fmt = _fmt(main)
    atr_fmt = _fmt(atr)

    df = pd.DataFrame({
        "Metric": list(main_fmt.keys()),
        "Main Strategy": list(main_fmt.values()),
        "Momentum × ATR": list(atr_fmt.values()),
    })
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Status (manual classification, not auto-inferred)")
    col1, col2 = st.columns(2)
    col1.metric("Main Strategy", registry.get("main", {}).get("status_label", "—"))
    col2.metric("Momentum × ATR", registry.get("momentum_atr", {}).get("status_label", "—"))
