"""Dashboard — Cash / Capital Integrity (docs/60 Page 5): broker cash vs
each strategy's own view of cash, read-only.

The two strategies are NOT complementary slices of one pool -- summing
their reported cash figures would double-count. MAIN has no allocation
cap and its "cash" IS the broker's whole-account cash, copied into
portfolio_snapshots at each 14:55 run (see
scripts/observability_snapshot.py::snapshot_main_capital's source_note).
momentum_atr keeps its own internal ledger cash, capped at a % of
combined account equity, which is not a broker-segregated pool. So the
only structurally valid integrity check here is MAIN cash vs broker cash
(same pool, different measurement times) -- NOT main_cash + atr_cash vs
broker_cash, which would fabricate an invariant that doesn't hold.

The MAIN/broker check is only computed when both snapshots are from the
same calendar date -- MAIN's cash is captured once/day (14:55 run) while
the broker snapshot refreshes every ~15min intraday (docs/60 Phase 1), so
comparing across different days would flag normal staleness as a false
integrity failure.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from datetime import datetime

import streamlit as st

from db import reporting_repo

# Same-pool cash gap tolerance -- broker cash moves intraday (other CNC
# activity, T+1 settlement) between MAIN's 14:55 snapshot and the broker
# snapshot cron's read, so a small gap is expected, not a bug.
_INTEGRITY_TOLERANCE_PCT = 0.02


def render():
    st.title("Cash / Capital Integrity")
    st.caption(
        "Read-only reconciliation of cash across the broker account and both "
        "strategies' own bookkeeping. Never auto-repaired here."
    )

    broker_snap = reporting_repo.load_latest_broker_snapshot()
    main_snap = reporting_repo.load_latest_strategy_capital_snapshot("main")
    atr_snap = reporting_repo.load_latest_strategy_capital_snapshot("momentum_atr")

    if broker_snap is None and main_snap is None and atr_snap is None:
        st.info("No capital snapshots recorded yet — Phase 1 observability cron not running.")
        return

    st.subheader("Broker Account")
    if broker_snap is None:
        st.info("No broker snapshot yet.")
    else:
        col1, col2 = st.columns(2)
        col1.metric("Broker Cash", f"₹{broker_snap['broker_cash']:,.0f}")
        col2.metric("Broker Total Equity", f"₹{broker_snap['total_equity']:,.0f}")
        st.caption(f"As of {broker_snap['ts']}")

    st.divider()
    col_main, col_atr = st.columns(2)

    with col_main:
        st.subheader("Main Strategy")
        if main_snap is None:
            st.info("No capital snapshot yet.")
        else:
            st.metric("Cash (whole-account, no allocation cap)", f"₹{main_snap['strategy_available_cash']:,.0f}")
            st.metric("Invested Value", f"₹{main_snap['strategy_invested_value']:,.0f}")
            st.metric("Strategy Equity", f"₹{main_snap['strategy_equity']:,.0f}")
            st.caption(f"As of {main_snap['ts']}")
            st.caption(main_snap["source_note"])

    with col_atr:
        st.subheader("Momentum × ATR")
        if atr_snap is None:
            st.info("No capital snapshot yet.")
        else:
            st.metric("Allocated Cash Cap", f"₹{atr_snap['strategy_allocated_cash']:,.0f}")
            st.metric("Available Cash (internal ledger)", f"₹{atr_snap['strategy_available_cash']:,.0f}")
            st.metric("Invested Value", f"₹{atr_snap['strategy_invested_value']:,.0f}")
            st.metric("Strategy Equity", f"₹{atr_snap['strategy_equity']:,.0f}")
            st.caption(f"As of {atr_snap['ts']}")
            st.caption(atr_snap["source_note"])

    st.divider()
    st.subheader("Integrity Check — MAIN cash vs Broker cash")
    st.caption(
        "The only valid check here: MAIN sees the broker's whole-account cash "
        "directly, so the two figures should track closely. momentum_atr's cash "
        "is a separate internal ledger, not a segregated broker pool — it is "
        "deliberately NOT summed into this check (would double-count the same pool)."
    )
    if main_snap is None or broker_snap is None:
        st.info("Need both a MAIN capital snapshot and a broker snapshot to check.")
        return

    main_date = main_snap["ts"][:10]
    broker_date = broker_snap["ts"][:10]
    if main_date != broker_date:
        st.info(
            f"MAIN's snapshot ({main_snap['ts']}) and the broker's ({broker_snap['ts']}) "
            "are from different days — skipping the check to avoid flagging normal "
            "staleness as a failure."
        )
        return

    main_cash = main_snap["strategy_available_cash"]
    broker_cash = broker_snap["broker_cash"]
    delta = broker_cash - main_cash
    delta_pct = abs(delta) / broker_cash if broker_cash > 0 else 0.0

    col1, col2 = st.columns(2)
    col1.metric("Delta (Broker − MAIN)", f"₹{delta:+,.0f}")
    col2.metric("Delta %", f"{delta_pct:.2%}")

    if delta_pct > _INTEGRITY_TOLERANCE_PCT:
        st.error(f"🔴 INTEGRITY CHECK FAILED — gap exceeds {_INTEGRITY_TOLERANCE_PCT:.0%} tolerance.")
    else:
        st.success("🟢 Within tolerance.")
