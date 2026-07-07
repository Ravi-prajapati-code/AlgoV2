#!/usr/bin/env python3
"""Correlation Analysis (Phase 3 of the research roadmap).

Distinct from the already-REJECTED correlation-based position sizing (see
phase2_improvements.md memory) — that was about using correlation to
THROTTLE size, which collapsed exposure under MAX_POSITIONS=3 exactly like
every other size-lever tried this research line. This is purely diagnostic:
understand the actual co-movement structure of what this strategy holds
concurrently, without touching sizing or the engine at all.

Three questions:
  1. Do the existing sector labels (used for the 50%/25% sector/stock
     allocation caps in portfolio/allocator.py) actually track real
     co-movement? Compare mean pairwise return correlation for same-sector
     vs different-sector symbol pairs across the full universe.
  2. At the moment of each historical entry, does higher average return
     correlation with whatever else is concurrently open predict a worse
     outcome for that trade? (Spearman vs pnl_pct, same method as
     feature_importance.py — chosen for the same small-N-honesty reason.)
  3. On days when 2-3 slots are filled, does the realized portfolio daily
     return's magnitude scale with the average pairwise correlation among
     that day's actual holdings — i.e. is the strategy actually getting a
     diversification benefit day to day, or is exposure usually one
     correlated cluster regardless of sector labels?

Read-only: reuses trade_attribution.run() for trades/data/result (full
BacktestResult, including equity_curve). Zero engine.py changes, zero live
DB writes.

Usage:
    python3 scripts/correlation_analysis.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from scipy.stats import spearmanr, ttest_ind

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.universe import get_sector
from scripts.trade_attribution import run

TRAILING_WINDOW_DAYS = 90  # for correlation-at-entry-time lookback


def build_return_matrix(data: dict) -> pd.DataFrame:
    """Symbol -> daily log return series, aligned on a common date index."""
    closes = {}
    for sym, df in data.items():
        if sym == "Nifty 50" or df.empty:
            continue
        closes[sym] = df["close"]
    price_df = pd.DataFrame(closes).sort_index()
    return np.log(price_df / price_df.shift(1))


def sector_label_check(returns: pd.DataFrame):
    corr = returns.corr(min_periods=100)
    symbols = corr.columns.tolist()
    same_sector, diff_sector = [], []
    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            a, b = symbols[i], symbols[j]
            rho = corr.loc[a, b]
            if pd.isna(rho):
                continue
            if get_sector(a) == get_sector(b):
                same_sector.append(rho)
            else:
                diff_sector.append(rho)
    same_sector, diff_sector = np.array(same_sector), np.array(diff_sector)
    t, p = ttest_ind(same_sector, diff_sector, equal_var=False)
    print("\n=== 1. Does the sector label track real co-movement? ===")
    print(f"  same-sector pairs   n={len(same_sector):<6} mean_corr={same_sector.mean():.3f}")
    print(f"  different-sector n={len(diff_sector):<6} mean_corr={diff_sector.mean():.3f}")
    print(f"  Welch t={t:.2f}  p={p:.4f}" + ("  <-- significant" if p < 0.05 else "  (not significant)"))


def concurrent_corr_at_entry(df_out: pd.DataFrame, returns: pd.DataFrame) -> pd.DataFrame:
    df_out = df_out.copy()
    df_out["entry_date"] = pd.to_datetime(df_out["entry_date"])
    df_out["exit_date"] = pd.to_datetime(df_out["exit_date"])

    avg_corrs = []
    for _, trade in df_out.iterrows():
        entry_dt = trade["entry_date"]
        concurrent = df_out[
            (df_out["symbol"] != trade["symbol"]) &
            (df_out["entry_date"] <= entry_dt) &
            (df_out["exit_date"] >= entry_dt)
        ]
        if concurrent.empty:
            avg_corrs.append(None)
            continue
        window_start = entry_dt - pd.Timedelta(days=TRAILING_WINDOW_DAYS * 1.5)
        window = returns.loc[(returns.index >= window_start) & (returns.index < entry_dt)]
        if trade["symbol"] not in window.columns:
            avg_corrs.append(None)
            continue
        rhos = []
        for other_sym in concurrent["symbol"].unique():
            if other_sym not in window.columns:
                continue
            pair = window[[trade["symbol"], other_sym]].dropna()
            if len(pair) < 30:
                continue
            rhos.append(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
        avg_corrs.append(np.mean(rhos) if rhos else None)

    df_out["avg_concurrent_corr_at_entry"] = avg_corrs
    return df_out


def print_corr_vs_outcome(df_out: pd.DataFrame):
    sub = df_out[["avg_concurrent_corr_at_entry", "pnl_pct"]].dropna()
    print(f"\n=== 2. Does correlation-at-entry predict trade outcome? (n={len(sub)}) ===")
    if len(sub) < 20:
        print("  Not enough concurrent-entry trades to test.")
        return
    rho, pval = spearmanr(sub["avg_concurrent_corr_at_entry"], sub["pnl_pct"])
    print(f"  Spearman rho={rho:.3f}  p={pval:.4f}" + ("  <-- p<0.05" if pval < 0.05 else "  (not significant)"))
    try:
        sub = sub.assign(bucket=pd.qcut(sub["avg_concurrent_corr_at_entry"], 3, duplicates="drop"))
        agg = sub.groupby("bucket", observed=True)["pnl_pct"].agg(["count", "mean"])
        print("  Terciles (low -> high concurrent correlation):")
        for bucket, row in agg.iterrows():
            print(f"    {str(bucket):<28} n={int(row['count']):<4} avg_pnl%={row['mean']:>7.2f}")
    except ValueError:
        pass


def print_portfolio_diversification_check(df_out: pd.DataFrame, returns: pd.DataFrame, result):
    df_out = df_out.copy()
    df_out["entry_date"] = pd.to_datetime(df_out["entry_date"])
    df_out["exit_date"] = pd.to_datetime(df_out["exit_date"])

    equity_dates = sorted(result.equity_curve.keys())
    daily_ret = {}
    prev_val = None
    for d in equity_dates:
        val = result.equity_curve[d]
        if prev_val:
            daily_ret[d] = (val - prev_val) / prev_val
        prev_val = val

    full_corr = returns.corr(min_periods=100)

    rows = []
    for d in equity_dates:
        ts = pd.Timestamp(d)
        holdings = df_out[(df_out["entry_date"] <= ts) & (df_out["exit_date"] >= ts)]["symbol"].unique()
        if len(holdings) < 2:
            continue
        pair_corrs = []
        for i in range(len(holdings)):
            for j in range(i + 1, len(holdings)):
                a, b = holdings[i], holdings[j]
                if a in full_corr.columns and b in full_corr.columns:
                    v = full_corr.loc[a, b]
                    if not pd.isna(v):
                        pair_corrs.append(v)
        if not pair_corrs or d not in daily_ret:
            continue
        rows.append({
            "date": d, "n_holdings": len(holdings),
            "avg_pair_corr": np.mean(pair_corrs), "daily_ret": daily_ret[d],
        })

    day_df = pd.DataFrame(rows)
    print(f"\n=== 3. Does realized daily volatility scale with holdings correlation? (n_days={len(day_df)}) ===")
    if len(day_df) < 30:
        print("  Not enough multi-position days to test.")
        return
    day_df = day_df.assign(abs_ret=day_df["daily_ret"].abs())
    rho, pval = spearmanr(day_df["avg_pair_corr"], day_df["abs_ret"])
    print(f"  Spearman(avg_pair_corr, |daily_return|) rho={rho:.3f}  p={pval:.4f}"
          + ("  <-- p<0.05" if pval < 0.05 else "  (not significant)"))
    try:
        day_df = day_df.assign(bucket=pd.qcut(day_df["avg_pair_corr"], 3, duplicates="drop"))
        agg = day_df.groupby("bucket", observed=True)["abs_ret"].agg(["count", "mean"])
        print("  Terciles (low -> high avg holdings correlation) vs avg |daily return|:")
        for bucket, row in agg.iterrows():
            print(f"    {str(bucket):<28} n_days={int(row['count']):<5} avg_abs_ret%={row['mean']*100:>6.2f}")
    except ValueError:
        pass
    print(f"\n  Overall avg pairwise correlation among concurrent holdings: {day_df['avg_pair_corr'].mean():.3f}")


def main(start_str, end_str):
    df_out, data, result = run(start_str, end_str)
    returns = build_return_matrix(data)

    sector_label_check(returns)
    df_out = concurrent_corr_at_entry(df_out, returns)
    print_corr_vs_outcome(df_out)
    print_portfolio_diversification_check(df_out, returns, result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cross-symbol correlation structure analysis")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    main(args.start, args.end)
