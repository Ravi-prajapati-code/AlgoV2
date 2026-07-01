"""Dashboard — Strategy Health Score (C) + Regime Monitor."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import math
import os
from datetime import date, timedelta
from typing import List

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from db.repository import load_trades, load_snapshots
from config.settings import INITIAL_CAPITAL


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sharpe(returns: List[float]) -> float:
    if len(returns) < 5:
        return 0.0
    mean = sum(returns) / len(returns)
    var  = sum((r - mean) ** 2 for r in returns) / len(returns)
    std  = math.sqrt(var)
    return (mean / std) * math.sqrt(252) if std > 0 else 0.0


def _profit_factor(trades) -> float:
    wins   = sum(t.net_pnl for t in trades if t.net_pnl and t.net_pnl > 0)
    losses = sum(abs(t.net_pnl) for t in trades if t.net_pnl and t.net_pnl < 0)
    return round(wins / losses, 2) if losses > 0 else float("inf")


def _max_dd(values: List[float]) -> float:
    peak = 0.0
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        dd   = (peak - v) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return max_dd


def _score_color(score: float) -> str:
    if score >= 70:
        return "#00c853"
    if score >= 45:
        return "#ffd600"
    return "#ff1744"


def _health_score(win_rate: float, profit_factor: float,
                  sharpe: float, max_dd_pct: float) -> float:
    """
    Composite 0-100 score.
      Win rate  30%  (50%=0pts, 70%=full)
      PF        30%  (1.0=0pts, 2.0=full)
      Sharpe    25%  (0=0pts, 2.0=full)
      MDD       15%  (30%=0pts, 5%=full)
    """
    wr_pts  = max(0, min(1, (win_rate - 0.50) / 0.20)) * 30
    pf_pts  = max(0, min(1, (profit_factor - 1.0) / 1.0)) * 30
    sh_pts  = max(0, min(1, sharpe / 2.0)) * 25
    dd_pts  = max(0, min(1, (0.30 - max_dd_pct / 100) / 0.25)) * 15
    return round(wr_pts + pf_pts + sh_pts + dd_pts, 1)


def _regime_analysis(snapshots):
    """Detect regime switch events and false BEARs."""
    if not snapshots:
        return [], 0, 0

    events = []
    prev_regime = None
    streak = 0
    false_bears = 0   # BEAR that lasted < 10 trading days before flipping BULL
    bear_start  = None
    bear_streaks = []

    for snap in sorted(snapshots, key=lambda s: s.date):
        r = snap.regime or "BULL"
        if r != prev_regime:
            if prev_regime == "BEAR" and bear_start:
                dur = streak
                bear_streaks.append(dur)
                if dur < 10:
                    false_bears += 1
            events.append({
                "date":     str(snap.date),
                "from":     prev_regime or "—",
                "to":       r,
                "streak":   streak,
            })
            streak    = 1
            bear_start = snap.date if r == "BEAR" else None
        else:
            streak += 1
        prev_regime = r

    # Handle open BEAR streak
    if prev_regime == "BEAR" and bear_start:
        bear_streaks.append(streak)

    avg_bear = round(sum(bear_streaks) / len(bear_streaks), 1) if bear_streaks else 0
    return events[-20:], false_bears, avg_bear


# ── Main Render ───────────────────────────────────────────────────────────────

def render():
    st.title("Strategy Health Score")

    trades    = load_trades()
    snapshots = load_snapshots()

    if not trades and not snapshots:
        st.info("No data yet — run the strategy first.")
        return

    today = date.today()
    cutoff_30d  = today - timedelta(days=30)
    cutoff_6m   = today - timedelta(days=182)

    trades_all  = [t for t in trades if t.entry_price and t.entry_price > 0]
    trades_30d  = [t for t in trades_all if t.exit_date and t.exit_date >= cutoff_30d]
    trades_6m   = [t for t in trades_all if t.exit_date and t.exit_date >= cutoff_6m]

    snaps_sorted = sorted(snapshots, key=lambda s: s.date) if snapshots else []
    daily_returns = []
    for i in range(1, len(snaps_sorted)):
        prev = snaps_sorted[i-1].total_value
        curr = snaps_sorted[i].total_value
        daily_returns.append((curr - prev) / prev if prev > 0 else 0.0)
    returns_30d = daily_returns[-30:]

    total_values = [s.total_value for s in snaps_sorted]
    max_dd_all   = _max_dd(total_values) * 100
    max_dd_6m    = _max_dd([s.total_value for s in snaps_sorted
                            if s.date >= cutoff_6m]) * 100

    # ── Overall Score ─────────────────────────────────────────────────────────
    wr_all = (sum(1 for t in trades_all if t.net_pnl > 0) / len(trades_all)
              if trades_all else 0.5)
    pf_all = _profit_factor(trades_all)
    sh_all = _sharpe(daily_returns)
    score  = _health_score(wr_all, pf_all, sh_all, max_dd_all)

    col_score, col_trend = st.columns([1, 3])
    color = _score_color(score)
    col_score.markdown(
        f"<div style='text-align:center; padding:20px; border-radius:12px; "
        f"background:{color}20; border: 2px solid {color};'>"
        f"<span style='font-size:52px; font-weight:700; color:{color};'>{score}</span>"
        f"<br><span style='color:#aaa; font-size:14px;'>/ 100</span>"
        f"<br><b style='color:{color};'>{'HEALTHY' if score>=70 else 'CAUTION' if score>=45 else 'WEAK'}</b>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # Score components breakdown
    with col_trend:
        st.markdown("**Score Components**")
        components = {
            "Win Rate":      (wr_all * 100, 50, 70, "%"),
            "Profit Factor": (pf_all if pf_all != float("inf") else 9.99, 1.0, 2.0, "x"),
            "Sharpe (30d)":  (_sharpe(returns_30d), 0, 2.0, ""),
            "Max Drawdown":  (max_dd_all, 30, 5, "%"),  # lower is better
        }
        for label, (val, bad, good, unit) in components.items():
            if label == "Max Drawdown":
                ok = val <= good
                pct_bar = max(0, min(1, (bad - val) / (bad - good)))
            else:
                ok = val >= good
                pct_bar = max(0, min(1, (val - bad) / (good - bad))) if good != bad else 0
            bar_color = "#00c853" if ok else "#ffd600" if pct_bar > 0.4 else "#ff1744"
            val_str = f"{val:.1f}{unit}" if unit else f"{val:.2f}"
            st.markdown(
                f"**{label}**: `{val_str}` "
                f"<span style='color:{bar_color};'>{'▲' if ok else '▼'}</span>",
                unsafe_allow_html=True,
            )

    st.divider()

    # ── KPI Grid ──────────────────────────────────────────────────────────────
    st.subheader("Key Metrics")
    wr_30d = (sum(1 for t in trades_30d if t.net_pnl > 0) / len(trades_30d) * 100
              if trades_30d else 0)
    pf_6m  = _profit_factor(trades_6m)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Win Rate (all)",    f"{wr_all*100:.1f}%",
              f"{wr_30d - wr_all*100:+.1f}% 30d")
    c2.metric("Profit Factor",     f"{pf_all:.2f}x" if pf_all != float("inf") else "∞",
              f"{pf_6m:.2f}x 6m")
    c3.metric("Sharpe (30d)",      f"{_sharpe(returns_30d):.2f}",
              f"all: {sh_all:.2f}")
    c4.metric("Max Drawdown",      f"{max_dd_all:.1f}%",
              f"6m: {max_dd_6m:.1f}%")
    c5.metric("Total Trades",      len(trades_all),
              f"{len(trades_30d)} last 30d")
    c6.metric("Net P&L",
              f"₹{sum(t.net_pnl for t in trades_all if t.net_pnl):+,.0f}",
              f"₹{sum(t.net_pnl for t in trades_30d if t.net_pnl):+,.0f} 30d")

    st.divider()

    # ── Rolling Sharpe & Win-Rate Chart ───────────────────────────────────────
    if len(snaps_sorted) >= 10:
        st.subheader("Rolling 30-Day Sharpe")
        roll_sharpes = []
        roll_dates   = []
        for i in range(30, len(daily_returns)):
            roll_sharpes.append(_sharpe(daily_returns[i-30:i]))
            roll_dates.append(snaps_sorted[i+1].date if i+1 < len(snaps_sorted)
                              else snaps_sorted[-1].date)

        if roll_sharpes:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=roll_dates, y=roll_sharpes,
                fill="tozeroy",
                line=dict(color="#00b0ff", width=2),
                name="Sharpe",
            ))
            fig.add_hline(y=1.0, line_dash="dash", line_color="#00c853",
                          annotation_text="Target 1.0")
            fig.add_hline(y=0.0, line_dash="dot", line_color="#ff1744")
            fig.update_layout(height=240, margin=dict(l=0, r=0, t=20, b=0),
                              paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ── Regime Monitor ────────────────────────────────────────────────────────
    st.subheader("Regime Monitor")
    events, false_bears, avg_bear_dur = _regime_analysis(snapshots)

    rc1, rc2, rc3 = st.columns(3)
    current_regime = snaps_sorted[-1].regime if snaps_sorted else "UNKNOWN"
    rc1.metric("Current Regime", current_regime)
    rc2.metric("False BEARs",    false_bears,
               help="BEAR regimes that flipped back to BULL within 10 trading days")
    rc3.metric("Avg BEAR Duration", f"{avg_bear_dur:.0f} days")

    if false_bears > 0:
        st.warning(
            f"⚠️  {false_bears} short BEAR regime(s) detected — "
            "consider whether EMA100 is triggering prematurely on shallow corrections.",
        )

    if events:
        df_events = pd.DataFrame(events)
        df_events.columns = ["Date", "From", "To", "Days in Prior Regime"]
        st.dataframe(df_events, use_container_width=True, hide_index=True, height=300)

    st.divider()

    # ── Exit Reason Breakdown ─────────────────────────────────────────────────
    st.subheader("Exit Reason Breakdown")
    if trades_all:
        reasons = {}
        for t in trades_all:
            r = (t.exit_reason or "unknown").split("|")[-1].strip()
            reasons[r] = reasons.get(r, [])
            reasons[r].append(t.net_pnl or 0)

        rows = []
        for reason, pnls in sorted(reasons.items()):
            wins = sum(1 for p in pnls if p > 0)
            rows.append({
                "Exit Reason": reason,
                "Count":       len(pnls),
                "Win Rate":    f"{wins/len(pnls)*100:.0f}%",
                "Avg P&L":     f"₹{sum(pnls)/len(pnls):+,.0f}",
                "Total P&L":   f"₹{sum(pnls):+,.0f}",
            })
        rows.sort(key=lambda x: int(x["Count"]), reverse=True)
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.divider()

    # ── Drift Monitor (B) ─────────────────────────────────────────────────────
    st.subheader("Execution Drift Monitor")
    st.caption("Expected signal price vs actual broker fill — detects slippage creep.")
    try:
        from monitoring.drift_monitor import compute_drift
        drift = compute_drift()
        if drift.get("pairs", 0) == 0:
            st.info("No matched signal/trade pairs yet — drift will appear after first live trade.")
        else:
            dc1, dc2, dc3, dc4 = st.columns(4)
            dc1.metric("Entry Drift",   f"{drift['entry_drift_pct']:+.3f}%",
                       help="+ve = buying above signal price")
            dc2.metric("Exit Drift",    f"{drift['exit_drift_pct']:+.3f}%",
                       help="-ve = selling below signal price")
            dc3.metric("Avg Drift",     f"{drift['avg_drift_pct']:+.3f}%")
            dc4.metric("Max Single",    f"{drift['max_drift_pct']:.3f}%")

            if abs(drift["avg_drift_pct"]) > 0.15:
                st.warning(
                    f"⚠️ Avg drift {drift['avg_drift_pct']:+.3f}% exceeds 0.15% threshold — "
                    "live execution diverging from backtest assumption.",
                )

            if drift["rows"]:
                df_drift = pd.DataFrame(drift["rows"][:20])
                df_drift.columns = ["Date", "Symbol", "Side",
                                    "Signal ₹", "Fill ₹", "Drift %"]
                st.dataframe(df_drift, use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"Drift monitor error: {e}")

    st.divider()

    # ── Walk-Forward Results (E) ──────────────────────────────────────────────
    st.subheader("Walk-Forward Validation")
    st.caption("Recent 6-month performance vs full historical baseline.")
    import json as _json
    wf_path = "outputs/walk_forward/latest.json"
    if os.path.exists(wf_path):
        with open(wf_path) as f:
            wf = _json.load(f)
        rec  = wf.get("recent", {})
        hist = wf.get("historical", {})
        wf_verdict = wf.get("verdict", "UNKNOWN")
        verdict_color = {"HEALTHY": "#00c853", "WEAK": "#ffd600", "DECAYING": "#ff1744"}.get(
            wf_verdict, "#aaa"
        )

        st.markdown(
            f"**Verdict:** <span style='color:{verdict_color}; font-weight:700'>"
            f"{wf_verdict}</span>  —  as of {wf.get('as_of', '?')}",
            unsafe_allow_html=True,
        )

        wf_cols = st.columns(3)
        for col, (label, r_key) in zip(wf_cols, [
            ("CAGR %", "cagr_pct"), ("Sharpe", "sharpe"), ("Win Rate %", "win_rate_pct")
        ]):
            r_val = rec.get(r_key, 0)
            h_val = hist.get(r_key, 0)
            delta = r_val - h_val
            col.metric(f"Recent 6M {label}", f"{r_val:.2f}",
                       f"{delta:+.2f} vs hist {h_val:.2f}")

        decay_flags = wf.get("decay_flags", [])
        if decay_flags:
            for flag in decay_flags:
                st.error(
                    f"❌ {flag['metric']} decay: {flag['baseline']:.1f} → "
                    f"{flag['recent']:.1f} (↓{flag['drop_pct']:.0f}%)"
                )
    else:
        st.info(
            "No walk-forward data yet. Run: `python3 scripts/walk_forward.py --force`\n\n"
            "Scheduled monthly (last Friday)."
        )
