"""Dashboard — Trade History page."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import pandas as pd

from dashboard.charts import trade_history_chart
from db.repository import load_trades


def render():
    st.title("Trade History")

    trades = load_trades()
    if not trades:
        st.info("No completed trades yet.")
        return

    bad_entry = [t for t in trades if t.entry_price == 0]
    if bad_entry:
        st.warning(
            f"⚠️ {len(bad_entry)} trade(s) have entry_price = 0 (T+1 sync issue — fixed going forward). "
            "P&L for those rows is unreliable.",
            icon="⚠️",
        )

    rows = []
    for t in trades:
        entry_px = f"₹{t.entry_price:,.2f}" if t.entry_price else "—"
        exit_px  = f"₹{t.exit_price:,.2f}"  if t.exit_price  else "—"
        rows.append({
            "Symbol":      t.symbol,
            "Sector":      t.sector,
            "Entry Date":  str(t.entry_date),
            "Exit Date":   str(t.exit_date),
            "Hold Days":   t.hold_days,
            "Entry ₹":     entry_px,
            "Exit ₹":      exit_px,
            "Shares":      t.shares,
            "Gross P&L":   t.gross_pnl,
            "Charges":     t.charges,
            "Net P&L":     t.net_pnl,
            "Exit Reason": t.exit_reason,
        })

    df = pd.DataFrame(rows)

    # Summary stats — exclude trades with entry_price=0 from win rate (P&L unreliable)
    df_valid = df[df["Entry ₹"] != "—"]
    col1, col2, col3, col4 = st.columns(4)
    winners = df_valid[df_valid["Net P&L"] > 0]
    win_rate = len(winners) / len(df_valid) * 100 if len(df_valid) > 0 else 0
    col1.metric("Total Trades",  len(df))
    col2.metric("Win Rate",      f"{win_rate:.1f}%")
    col3.metric("Total Net P&L", f"₹{df['Net P&L'].sum():+,.0f}")
    col4.metric("Total Charges", f"₹{df['Charges'].sum():,.0f}")

    st.divider()
    st.plotly_chart(trade_history_chart(rows), use_container_width=True)

    # Full trade log
    st.subheader("All Trades")

    def color_pnl(val):
        color = "#00c853" if val > 0 else "#ff1744"
        return f"color: {color}; font-weight: bold"

    st.dataframe(
        df.style.map(color_pnl, subset=["Net P&L", "Gross P&L"]),
        use_container_width=True, height=450
    )

    # ── Download ───────────────────────────────────────────────────────
    st.download_button(
        label="⬇ Download Trade Report (CSV)",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="pnl_report.csv",
        mime="text/csv",
    )

    # ── Monthly P&L Summary ────────────────────────────────────────────
    st.divider()
    st.subheader("Monthly P&L Summary")
    df["Exit Date"] = pd.to_datetime(df["Exit Date"])
    monthly = (
        df.groupby(df["Exit Date"].dt.to_period("M"))
        .agg(
            Trades=("Net P&L", "count"),
            Winners=("Net P&L", lambda x: (x > 0).sum()),
            Net_PnL=("Net P&L", "sum"),
            Charges=("Charges", "sum"),
        )
        .reset_index()
    )
    monthly["Exit Date"] = monthly["Exit Date"].astype(str)
    monthly["Win Rate"] = (monthly["Winners"] / monthly["Trades"] * 100).round(1).astype(str) + "%"
    monthly["Net P&L"] = monthly["Net_PnL"].map(lambda v: f"₹{v:+,.0f}")
    monthly["Charges"] = monthly["Charges"].map(lambda v: f"₹{v:,.0f}")
    monthly = monthly.rename(columns={"Exit Date": "Month"}).drop(columns=["Net_PnL", "Winners"])

    def color_monthly(val):
        if isinstance(val, str) and val.startswith("₹"):
            num = float(val.replace("₹", "").replace(",", "").replace("+", ""))
            return f"color: {'#00c853' if num > 0 else '#ff1744'}; font-weight: bold"
        return ""

    st.dataframe(
        monthly.style.map(color_monthly, subset=["Net P&L"]),
        use_container_width=True, hide_index=True,
    )
    st.download_button(
        label="⬇ Download Monthly Summary (CSV)",
        data=monthly.to_csv(index=False).encode("utf-8"),
        file_name="monthly_pnl_summary.csv",
        mime="text/csv",
        key="monthly_csv",
    )
