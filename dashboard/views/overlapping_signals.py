"""Dashboard — Overlapping Signals (docs/60 Page 15): same-symbol,
same-date crossings between MAIN's signals and momentum_atr's actual
entries/exits. Informational only — never auto-resolved or acted on.

Asymmetric by necessity: MAIN persists a `signals` row for every BUY/SELL
it generates, whether or not a slot was free to act on it. momentum_atr
has no equivalent "signal fired" log — only its daily rank order (not a
fired signal, since ranking alone doesn't mean an entry happened) and its
actual executed entries/exits (positions.entry_date, trades.exit_date).
Comparing MAIN's raw signals against momentum_atr's *executed* actions
(rather than guessing intent from rank position) avoids inventing a
momentum_atr "signal" that was never actually recorded as one.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import pandas as pd

from db.repository import load_signals
from db.momentum_atr_repo import load_positions as load_atr_positions, load_trades as load_atr_trades


def render():
    st.title("Overlapping Signals")
    st.caption(
        "MAIN signals (BUY/SELL, as generated) vs momentum_atr executed actions "
        "(entry/exit, as actually taken) — same symbol, same date. Informational "
        "only, not a conflict-resolution or auto-fix mechanism."
    )

    main_signals = [s for s in load_signals() if s.action in ("BUY", "SELL")]
    if not main_signals:
        st.info("No MAIN BUY/SELL signals recorded yet.")
        return

    atr_entries = {
        (p.symbol, p.entry_date): "ENTRY"
        for p in load_atr_positions("OPEN") + load_atr_positions("CLOSED")
    }
    atr_exits = {
        (t.symbol, t.exit_date): "EXIT"
        for t in load_atr_trades() if t.exit_date
    }
    atr_events = {**atr_entries, **atr_exits}

    if not atr_events:
        st.info("No momentum_atr entries/exits recorded yet — nothing to compare against.")
        return

    rows = []
    for s in main_signals:
        key = (s.symbol, s.date)
        atr_action = atr_events.get(key)
        if atr_action is None:
            continue
        same_direction = (
            (s.action == "BUY" and atr_action == "ENTRY") or
            (s.action == "SELL" and atr_action == "EXIT")
        )
        rows.append({
            "Symbol": s.symbol,
            "Date": str(s.date),
            "MAIN Action": s.action,
            "ATR Action": atr_action,
            "Type": "Aligned" if same_direction else "⚠️ Opposing",
        })

    if not rows:
        st.info("No same-symbol, same-date crossings found between the two strategies' recorded history.")
        return

    df = pd.DataFrame(rows).sort_values("Date", ascending=False)
    opposing = (df["Type"] == "⚠️ Opposing").sum()
    col1, col2 = st.columns(2)
    col1.metric("Total Crossings", len(df))
    col2.metric("Opposing", int(opposing))

    st.dataframe(df, use_container_width=True, hide_index=True)
