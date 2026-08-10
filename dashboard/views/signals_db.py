"""Dashboard — Signals (docs/60 Page 6): DB-backed signal history for both
strategies, not just today's outputs/signals.json snapshot (see the
existing "Today's Signals" page for that).

MAIN: full detail (score, reason, action) straight from the `signals`
table. momentum_atr: rank + rank-change only, from daily_ranking's
ranked_json -- there is no persisted per-symbol numeric score for this
strategy (would require the still-unapproved daily_ranking.scores_json
schema change), so score/score-change are shown as N/A rather than
invented.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import streamlit as st

from db.repository import load_signals
from db.momentum_atr_repo import load_recent_daily_rankings


def render():
    st.title("Signals")

    col_main, col_atr = st.columns(2)

    # ── MAIN: full detail ────────────────────────────────────────────────
    with col_main:
        st.subheader("Main Strategy")
        signals = load_signals()
        if not signals:
            st.info("No signals recorded yet.")
        else:
            latest_date = signals[0].date
            latest = [s for s in signals if s.date == latest_date]
            st.caption(f"Latest recorded date: {latest_date}")
            rows = [{
                "Symbol": s.symbol,
                "Action": s.action,
                "Score": s.score,
                "Price": f"₹{s.price:,.2f}" if s.price else "—",
                "Reason": s.reason,
            } for s in latest]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ── momentum_atr: rank + rank-change only, score N/A ────────────────
    with col_atr:
        st.subheader("Momentum × ATR")
        try:
            recent = load_recent_daily_rankings(limit=2)
        except Exception as e:
            st.error(f"Could not load momentum_atr ranking data: {e}")
            recent = None
        if not recent:
            st.info("No ranking recorded yet.")
        else:
            latest_date, latest_ranked = recent[0]
            prev_ranked = recent[1][1] if len(recent) > 1 else None
            st.caption(f"Latest recorded date: {latest_date}")

            prev_rank_by_symbol = (
                {sym: i + 1 for i, sym in enumerate(prev_ranked)} if prev_ranked else {}
            )
            rows = []
            for i, sym in enumerate(latest_ranked):
                rank = i + 1
                prev_rank = prev_rank_by_symbol.get(sym)
                if prev_rank is None:
                    change = "new"
                else:
                    delta = prev_rank - rank
                    change = f"+{delta}" if delta > 0 else str(delta) if delta < 0 else "—"
                rows.append({"Rank": rank, "Symbol": sym, "Rank Δ": change, "Score": "N/A"})
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(
                "Score/score-change not shown: this strategy doesn't persist a "
                "per-symbol numeric score today, only rank order (rank/rank-change "
                "above are real; score is N/A, not fabricated)."
            )
