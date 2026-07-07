#!/usr/bin/env python3
"""Feature Importance (Phase 2 of the research roadmap).

Trade Attribution already showed the CURRENT 5-dimension entry filter
(rs_rank, atr_pct, vol_ratio, adx, ema50_dist) can't separate LONG_WINNER
from QUICK_LOSER trades. This script asks the broader question: across every
indicator the engine already computes at entry time (now 15 fields captured
in outputs/trade_attribution.csv, up from 5), is there ANY entry-time signal
that predicts trade profitability — even a weak one worth layering in later —
or is the null result total?

Method: Spearman rank correlation between each entry-time feature and
pnl_pct (continuous outcome), with p-values. Spearman (not Pearson) because
outcome distributions are fat-tailed and we care about monotonic, not
strictly linear, relationships. Chosen over a black-box model (e.g. random
forest importance) deliberately: with only ~155 trades and 15+ candidate
features, an RF would overfit and its importances would be unstable run to
run — a simple, interpretable per-feature correlation with a significance
test is more honest at this sample size, consistent with the Welch's
t-test/Cohen's d approach already used in trade_attribution.py's Winner DNA
analysis. Read-only analysis, no engine.py or live code touched.

Usage:
    python3 scripts/feature_importance.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]
"""
import argparse
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.stats import spearmanr, pointbiserialr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.trade_attribution import run

NUMERIC_FEATURES = [
    "rs_rank_at_entry", "atr_pct_at_entry", "vol_ratio_at_entry", "adx_at_entry",
    "ema50_dist_pct_at_entry", "ema20_dist_pct_at_entry", "ema100_dist_pct_at_entry",
    "ema150_dist_pct_at_entry", "dist_from_high20d_pct_at_entry", "rsi_at_entry",
    "macd_hist_at_entry", "perf_10d_at_entry", "turnover_at_entry",
]
BOOLEAN_FEATURES = ["macd_bullish_at_entry"]


def compute_importance(df: pd.DataFrame, target_col: str = "pnl_pct") -> pd.DataFrame:
    rows = []
    for feat in NUMERIC_FEATURES:
        sub = df[[feat, target_col]].dropna()
        if len(sub) < 20:
            continue
        rho, pval = spearmanr(sub[feat], sub[target_col])
        rows.append({"feature": feat, "n": len(sub), "spearman_rho": rho, "p_value": pval})
    for feat in BOOLEAN_FEATURES:
        sub = df[[feat, target_col]].dropna()
        if len(sub) < 20:
            continue
        rho, pval = pointbiserialr(sub[feat].astype(int), sub[target_col])
        rows.append({"feature": feat, "n": len(sub), "spearman_rho": rho, "p_value": pval})

    out = pd.DataFrame(rows).sort_values("spearman_rho", key=lambda s: s.abs(), ascending=False)
    return out.reset_index(drop=True)


def print_quartile_table(df: pd.DataFrame, feature: str, target_col: str = "pnl_pct"):
    sub = df[[feature, target_col]].dropna()
    if len(sub) < 20:
        return
    try:
        sub = sub.assign(bucket=pd.qcut(sub[feature], 4, duplicates="drop"))
    except ValueError:
        return
    agg = sub.groupby("bucket", observed=True)[target_col].agg(["count", "mean"])
    win_rate = sub.groupby("bucket", observed=True).apply(
        lambda g: (g[target_col] > 0).mean() * 100, include_groups=False
    )
    print(f"\n  {feature} quartiles vs {target_col}:")
    for bucket, row in agg.iterrows():
        wr = win_rate.get(bucket, float("nan"))
        print(f"    {str(bucket):<28} n={int(row['count']):<4} avg_pnl%={row['mean']:>7.2f}  win_rate%={wr:>6.1f}")


def main(start_str, end_str):
    df, _, _result = run(start_str, end_str)

    print(f"\n=== Feature Importance: entry-time indicators vs pnl_pct (n={len(df)}) ===")
    imp = compute_importance(df, "pnl_pct")
    print(f"{'feature':<32}{'n':>6}{'spearman_rho':>15}{'p_value':>12}")
    for _, r in imp.iterrows():
        flag = " <-- p<0.05" if r["p_value"] < 0.05 else ""
        print(f"{r['feature']:<32}{int(r['n']):>6}{r['spearman_rho']:>15.3f}{r['p_value']:>12.4f}{flag}")

    sig = imp[imp["p_value"] < 0.05]
    if sig.empty:
        print("\n  No entry-time feature reaches p<0.05 against pnl_pct.")
        print("  Confirms the Trade Attribution finding at a broader scale: outcome is not")
        print("  predictable from any single entry-time indicator this engine currently computes.")
    else:
        print(f"\n  {len(sig)} feature(s) reach p<0.05 — showing quartile breakdown:")
        for feat in sig["feature"]:
            print_quartile_table(df, feat, "pnl_pct")

    print(f"\n=== Same analysis vs binary win/loss (net_pnl > 0) ===")
    df = df.assign(is_win=(df["net_pnl"] > 0).astype(int))
    imp_bin = compute_importance(df, "is_win")
    print(f"{'feature':<32}{'n':>6}{'spearman_rho':>15}{'p_value':>12}")
    for _, r in imp_bin.iterrows():
        flag = " <-- p<0.05" if r["p_value"] < 0.05 else ""
        print(f"{r['feature']:<32}{int(r['n']):>6}{r['spearman_rho']:>15.3f}{r['p_value']:>12.4f}{flag}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Entry-time feature importance analysis")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    main(args.start, args.end)
