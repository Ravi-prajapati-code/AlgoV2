"""Dashboard — Momentum × ATR Trade Journal (docs/60 Page 8, momentum_atr
side). MAIN's equivalent is the existing "Trade History" page.

exit_reason is a direct column. entry_reason has no persisted source for
this strategy today (a still-unapproved trades.entry_reason schema change,
same class of gap as Page 6's momentum_atr score) — shown as N/A, not
invented.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import streamlit as st

from db.momentum_atr_repo import load_trades


def render():
    st.title("Momentum × ATR — Trade Journal")
    st.caption(
        "Entry Reason is N/A for every row — this strategy doesn't persist why "
        "a symbol was entered, only its rank at entry time (see Signals page). "
        "Not fabricated here."
    )

    trades = load_trades()
    if not trades:
        st.info("No completed trades yet.")
        return

    rows = []
    for t in trades:
        exit_px = f"₹{t.exit_price:,.2f}" if t.exit_price else "—"
        rows.append({
            "Symbol":       t.symbol,
            "Entry Date":   str(t.entry_date),
            "Entry Reason": "N/A",
            "Entry ₹":      f"₹{t.entry_price:,.2f}",
            "Exit Date":    str(t.exit_date) if t.exit_date else "—",
            "Exit ₹":       exit_px,
            "Shares":       t.shares,
            "Gross P&L":    t.gross_pnl if t.gross_pnl is not None else "—",
            "Charges":      t.charges if t.charges is not None else "—",
            "Net P&L":      t.net_pnl if t.net_pnl is not None else "—",
            "Exit Reason":  t.exit_reason or "—",
            "Exit Order ID": t.exit_order_id or "—",
        })

    df = pd.DataFrame(rows)

    valid = df[df["Net P&L"] != "—"]
    col1, col2, col3 = st.columns(3)
    winners = valid[valid["Net P&L"] > 0] if not valid.empty else valid
    win_rate = len(winners) / len(valid) * 100 if len(valid) > 0 else 0
    col1.metric("Total Trades", len(df))
    col2.metric("Win Rate", f"{win_rate:.1f}%")
    col3.metric("Total Net P&L", f"₹{valid['Net P&L'].sum():+,.0f}" if not valid.empty else "—")

    st.divider()
    st.dataframe(df, use_container_width=True, height=450, hide_index=True)

    st.download_button(
        label="⬇ Download Trade Journal (CSV)",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="momentum_atr_trade_journal.csv",
        mime="text/csv",
    )
