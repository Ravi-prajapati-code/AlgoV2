"""Dashboard — Risk Monitor (docs/60 Page 10): each strategy's real live
drawdown/kill-switch state, reproduced read-only.

MAIN: portfolio_snapshots.drawdown_pct is a dead column -- the real live
gate is portfolio/risk.py::can_open_new_trades() plus inline graduated
size-cut logic in portfolio/manager.py, computed each run from
self.peak_value (in-memory, never persisted). That peak is reconstructible
read-only as max(portfolio_snapshots.strategy_value) ever recorded, so this
page reproduces the same numbers without touching live code.

momentum_atr: a single binary threshold (MOMENTUM_ATR_DD_KILL_PCT), state
directly persisted (state.kill_switch_tripped/peak_equity) and read via
momentum_atr_repo.get_state() -- no graduated ladder exists for this
strategy, and none is fabricated here.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st

from config.settings import (
    DRAWDOWN_KILL_SWITCH_PCT,
    DRAWDOWN_REDUCE_SIZE_PCT,
    DRAWDOWN_REDUCE_TIER2_MULT,
    MOMENTUM_ATR_DD_KILL_PCT,
)


def _main_risk_state():
    from db.repository import load_snapshots

    snaps = load_snapshots()
    if not snaps:
        return None
    peak = max(s.strategy_value for s in snaps)
    current = snaps[-1].strategy_value
    if peak <= 0:
        return None
    drawdown = (peak - current) / peak

    kill_switch = drawdown >= DRAWDOWN_KILL_SWITCH_PCT
    tier2_threshold = DRAWDOWN_REDUCE_SIZE_PCT * DRAWDOWN_REDUCE_TIER2_MULT
    if drawdown >= tier2_threshold:
        size_factor = 0.25
    elif drawdown >= DRAWDOWN_REDUCE_SIZE_PCT:
        size_factor = 0.50
    else:
        size_factor = 1.00

    return {
        "date": snaps[-1].date,
        "peak": peak,
        "current": current,
        "drawdown": drawdown,
        "kill_switch": kill_switch,
        "size_factor": size_factor,
        "tier2_threshold": tier2_threshold,
    }


def _atr_risk_state():
    from db.momentum_atr_repo import get_state, load_equity_snapshots

    state = get_state()
    snaps = load_equity_snapshots(limit=1)
    latest = snaps[-1] if snaps else None
    return state, latest


def render():
    st.title("Risk Monitor")
    st.caption(
        "Real live thresholds only — no fabricated ladders. MAIN's numbers "
        "are reconstructed read-only from portfolio_snapshots history; "
        "momentum_atr's are read directly from its persisted state."
    )

    col_main, col_atr = st.columns(2)

    with col_main:
        st.subheader("Main Strategy")
        m = _main_risk_state()
        if m is None:
            st.info("No portfolio_snapshots history yet.")
        else:
            st.caption(f"As of {m['date']}")
            st.metric("Drawdown from peak", f"{m['drawdown']:.2%}")
            st.metric("Peak Strategy Value", f"₹{m['peak']:,.0f}")
            st.metric("Current Strategy Value", f"₹{m['current']:,.0f}")

            if m["kill_switch"]:
                st.error(f"🔴 KILL-SWITCH ACTIVE — drawdown ≥ {DRAWDOWN_KILL_SWITCH_PCT:.0%}. New BUYs blocked.")
            elif m["size_factor"] < 1.0:
                st.warning(f"🟡 Slot size reduced to {m['size_factor']:.0%} — drawdown ≥ tiered threshold.")
            else:
                st.success("🟢 Normal — no drawdown-based sizing reduction active.")

            st.caption(
                f"Thresholds: kill-switch @ {DRAWDOWN_KILL_SWITCH_PCT:.0%} · "
                f"50% size cut @ {DRAWDOWN_REDUCE_SIZE_PCT:.0%} · "
                f"25% size cut @ {m['tier2_threshold']:.1%}"
            )

    with col_atr:
        st.subheader("Momentum × ATR")
        state, latest = _atr_risk_state()
        if state is None:
            st.info("No momentum_atr state yet.")
        else:
            if latest is not None:
                st.caption(f"As of {latest.date}")
                st.metric("Drawdown from peak", f"{latest.drawdown_pct:.2%}")
                st.metric("Peak Equity", f"₹{state.peak_equity:,.0f}")
                st.metric("Current Equity", f"₹{latest.total_equity:,.0f}")
            else:
                st.metric("Peak Equity", f"₹{state.peak_equity:,.0f}")
                st.caption("No equity_snapshots row yet — showing state table only.")

            if state.kill_switch_tripped:
                tripped_note = f" (tripped {state.kill_switch_tripped_date})" if state.kill_switch_tripped_date else ""
                st.error(f"🔴 KILL-SWITCH TRIPPED{tripped_note} — manual recovery only, new BUYs blocked.")
            else:
                st.success("🟢 Normal — kill-switch not tripped.")

            st.caption(
                f"Single binary threshold: kill-switch @ {MOMENTUM_ATR_DD_KILL_PCT:.0%} drawdown. "
                "No graduated sizing ladder for this strategy — none shown here. "
                "SELLs (incl. rank-exits) keep running when tripped; only new BUYs are blocked. "
                "No auto-clear on recovery."
            )
