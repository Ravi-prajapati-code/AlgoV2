"""
Batch-1 zero-risk measurements (docs/19 E1-step-1, E3, E5). Two backtests, no behavior changes.

1. Stranded-cash attribution (E1 step 1): for each buy, the portfolio drawdown at entry ->
   which DD-throttle tier applied; daily idle-cash decomposition on full-slot vs open-slot days.
2. Churn / re-entry cycle audit (E3): same-symbol re-entries within K days of exit; cycle P&L
   and friction share by holding-period bucket.
3. GOLDBEES->cash ablation (E5): identical run with GOLDBEES.NS removed from the data dict
   (get_defensive_entries skips missing prices), so BEAR-regime capital sits in cash.
"""
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.watchlist_nse import ALL_SYMBOLS
from config.settings import (
    INITIAL_CAPITAL, MARKET_INDEX_SYMBOL, SAFE_HAVEN_SYMBOL,
    DRAWDOWN_REDUCE_SIZE_PCT, DRAWDOWN_REDUCE_TIER2_MULT,
)
from data.fetcher import fetch_all, fetch_index
from backtest.engine import BacktestEngine
from scripts.benchmark_attribution import sharpe_ratio, max_drawdown_pct, cagr_from_returns

START = "2022-01-01"
END = str(date.today())
REENTRY_GAP_DAYS = 10


def run(data, start, end):
    engine = BacktestEngine(data, start, end, INITIAL_CAPITAL,
                            slippage_model="fixed_pct", fund_injections={})
    return engine.run()


def metrics(result):
    eq = pd.Series(result.equity_curve).sort_index()
    eq.index = pd.to_datetime(eq.index).normalize()
    ret = eq.pct_change().dropna()
    return {
        "cagr": cagr_from_returns(ret), "mdd": max_drawdown_pct(ret),
        "sharpe": sharpe_ratio(ret), "trades": len(result.trades), "equity": eq,
    }


def main():
    start = datetime.strptime(START, "%Y-%m-%d").date()
    end = datetime.strptime(END, "%Y-%m-%d").date()
    warmup_start = start - timedelta(days=500)
    lookback = (end - start).days + 60

    print("Fetching data...", flush=True)
    data = fetch_all(ALL_SYMBOLS, lookback_days=lookback, start=warmup_start, end=end)
    index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=lookback, start=warmup_start, end=end)
    if not index_df.empty:
        data[MARKET_INDEX_SYMBOL] = index_df

    print("Run 1/2: baseline...", flush=True)
    base = run(data, start, end)
    m_base = metrics(base)

    # ---------- 1. STRANDED-CASH ATTRIBUTION ----------
    print("\n=== 1. STRANDED-CASH ATTRIBUTION ===")
    eq = m_base["equity"]
    running_max = eq.cummax()
    dd = (1 - eq / running_max)

    tiers = defaultdict(list)
    tier2_cut = DRAWDOWN_REDUCE_SIZE_PCT * DRAWDOWN_REDUCE_TIER2_MULT
    for t in base.trades:
        ed = pd.Timestamp(t.entry_date).normalize()
        d = float(dd.asof(ed)) if ed >= dd.index[0] else 0.0
        notional = (t.entry_price or 0) * getattr(t, "quantity", getattr(t, "shares", 0))
        if d >= tier2_cut:
            tiers["tier2 (x0.25)"].append(notional)
        elif d >= DRAWDOWN_REDUCE_SIZE_PCT:
            tiers["tier1 (x0.50)"].append(notional)
        else:
            tiers["full size"].append(notional)
    total_notional = sum(sum(v) for v in tiers.values())
    for k in ["full size", "tier1 (x0.50)", "tier2 (x0.25)"]:
        v = tiers.get(k, [])
        # cash withheld at entry: notional deployed n = slot_cash*mult -> withheld = n*(1/mult - 1)
        mult = 1.0 if k == "full size" else (0.5 if "tier1" in k else 0.25)
        withheld = sum(v) * (1 / mult - 1)
        print(f"  {k:<15} buys={len(v):>4}  deployed=₹{sum(v):>12,.0f} "
              f"({(sum(v)/total_notional*100 if total_notional else 0):5.1f}% of notional)  "
              f"cash withheld at entry=₹{withheld:>12,.0f}")

    scan = pd.DataFrame(base.daily_scan_log)
    scan["date"] = pd.to_datetime(scan["date"]).dt.normalize()
    scan = scan.drop_duplicates("date", keep="last").set_index("date")
    cash = pd.Series(base.cash_curve).sort_index()
    cash.index = pd.to_datetime(cash.index).normalize()
    idle = (cash / eq).reindex(scan.index)
    dd_s = dd.reindex(scan.index).fillna(0)
    dec = pd.DataFrame(base.decision_log)
    dec["date"] = pd.to_datetime(dec["date"]).dt.normalize()
    qual_by_day = dec[dec["signal"].astype(str).str.upper().isin(["BUY", "TRUE", "YES", "1"])] \
        .groupby("date").size().reindex(scan.index).fillna(0)

    full = scan["open_positions"] >= 3
    print(f"\nDaily idle-cash decomposition (mean cash fraction of equity):")
    print(f"  slots FULL (n={full.sum()}): idle={idle[full].mean()*100:5.1f}%  "
          f"[of which days in >10% DD: {(dd_s[full] >= 0.10).mean()*100:.0f}%]")
    nf = ~full
    nf_nocand = nf & (qual_by_day == 0)
    nf_cand = nf & (qual_by_day > 0)
    print(f"  slots OPEN, no qualified candidates (n={nf_nocand.sum()}): idle={idle[nf_nocand].mean()*100:5.1f}%")
    print(f"  slots OPEN, candidates existed      (n={nf_cand.sum()}): idle={idle[nf_cand].mean()*100:5.1f}%  "
          f"[gates: DD kill-switch/market filter/cash floor]")
    print(f"  DD>=10% days overall: {(dd_s >= 0.10).mean()*100:.1f}% of days; "
          f"DD>=15%: {(dd_s >= 0.15).mean()*100:.1f}%")

    # ---------- 2. CHURN / RE-ENTRY CYCLES ----------
    print("\n=== 2. CHURN / RE-ENTRY CYCLES ===")
    closed = [t for t in base.trades if t.exit_date is not None]
    by_sym = defaultdict(list)
    for t in closed:
        by_sym[t.symbol].append(t)
    cycles, cycle_pnl, cycle_charges = 0, 0.0, 0.0
    for sym, ts in by_sym.items():
        ts.sort(key=lambda x: pd.Timestamp(x.entry_date))
        for a, b in zip(ts, ts[1:]):
            gap = (pd.Timestamp(b.entry_date) - pd.Timestamp(a.exit_date)).days
            if 0 <= gap <= REENTRY_GAP_DAYS:
                cycles += 1
                cycle_pnl += (a.net_pnl or 0)
                cycle_charges += (a.charges or 0) + (b.charges or 0)
    tot_charges = sum((t.charges or 0) for t in closed)
    print(f"Re-entry cycles (same symbol re-bought within {REENTRY_GAP_DAYS}d of exit): {cycles}")
    print(f"  Net P&L of the exited leg of those cycles: ₹{cycle_pnl:,.0f}")
    print(f"  Charges attached to those cycles: ₹{cycle_charges:,.0f} of ₹{tot_charges:,.0f} total")
    hold_buckets = [(0, 10), (11, 30), (31, 60), (61, 9999)]
    print(f"\nP&L / friction by holding-period bucket:")
    for lo, hi in hold_buckets:
        grp = [t for t in closed if lo <= (t.hold_days or 0) <= hi]
        print(f"  {lo:>3}-{hi if hi < 9999 else '∞':>3}d  n={len(grp):>4}  "
              f"net=₹{sum((t.net_pnl or 0) for t in grp):>12,.0f}  "
              f"charges=₹{sum((t.charges or 0) for t in grp):>9,.0f}")

    # ---------- 3. GOLDBEES -> CASH ABLATION ----------
    print("\n=== 3. GOLDBEES -> CASH ABLATION (E5) ===", flush=True)
    data_nogold = {k: v for k, v in data.items() if k != SAFE_HAVEN_SYMBOL}
    print("Run 2/2: no-gold arm...", flush=True)
    nogold = run(data_nogold, start, end)
    m_ng = metrics(nogold)
    print(f"  BASELINE : CAGR={m_base['cagr']:+7.2f}%  MDD={m_base['mdd']:7.2f}%  "
          f"Sharpe={m_base['sharpe']:5.2f}  trades={m_base['trades']}")
    print(f"  NO-GOLD  : CAGR={m_ng['cagr']:+7.2f}%  MDD={m_ng['mdd']:7.2f}%  "
          f"Sharpe={m_ng['sharpe']:5.2f}  trades={m_ng['trades']}")
    print(f"  Gold-windfall share of CAGR: {m_base['cagr'] - m_ng['cagr']:+.2f}pp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
