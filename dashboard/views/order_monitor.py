"""Dashboard — Order Monitor (docs/60 Page 7): order-id traceability for
both strategies, read-only.

Repository-integrity correction to docs/60's original Page 7 spec: it
claimed momentum_atr's `trades` table has both entry_order_id and
exit_order_id. Verified false against the live schema
(db/momentum_atr_schema.sql) — entry_order_id lives only on the OPEN
Position row and is dropped when the position closes into a Trade record,
which persists exit_order_id only. So momentum_atr's order-id
traceability is: open positions (entry-side) + closed trades (exit-side
only) — never both for the same closed trade.

MAIN has no order-id columns at all, on either positions or trades
(confirmed via db/schema.sql / db/models.py) — weaker than momentum_atr on
both sides, not just the "existing but weaker" characterization the doc
implied.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import streamlit as st

from db.repository import load_trades as load_main_trades, load_positions as load_main_positions
from db.momentum_atr_repo import load_trades as load_atr_trades, load_positions as load_atr_positions


def render():
    st.title("Order Monitor")
    st.caption(
        "Order-id traceability only — not a live order-status feed (no broker call here). "
        "See caveats per strategy below; gaps are shown as such, not filled in."
    )

    col_main, col_atr = st.columns(2)

    with col_main:
        st.subheader("Main Strategy")
        st.warning("No order-id columns exist on either positions or trades for this strategy — no traceability available.")
        positions = load_main_positions()
        trades = load_main_trades()
        st.metric("Open Positions", len(positions))
        st.metric("Closed Trades", len(trades))

    with col_atr:
        st.subheader("Momentum × ATR")
        st.caption(
            "entry_order_id: open positions only (lost on close). "
            "exit_order_id: closed trades only. Never both for the same trade."
        )

        open_positions = load_atr_positions("OPEN")
        st.markdown("**Open Positions — Entry Order IDs**")
        if not open_positions:
            st.info("No open positions.")
        else:
            rows = [{
                "Symbol": p.symbol,
                "Entry Date": str(p.entry_date),
                "Entry Order ID": p.entry_order_id or "—",
            } for p in open_positions]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.markdown("**Closed Trades — Exit Order IDs**")
        trades = load_atr_trades()
        if not trades:
            st.info("No closed trades.")
        else:
            rows = [{
                "Symbol": t.symbol,
                "Exit Date": str(t.exit_date) if t.exit_date else "—",
                "Exit Order ID": t.exit_order_id or "—",
                "Entry Order ID": "N/A (dropped on close)",
            } for t in trades]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
