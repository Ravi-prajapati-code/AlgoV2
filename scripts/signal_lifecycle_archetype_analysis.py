#!/usr/bin/env python3
"""Signal Lifecycle & Archetype Analysis.

Follow-up to scripts/signal_mechanism_analysis.py (docs/23_Assumption_Audit.md
§XX-XXI): none of RS/ADX/breakout/volume/sector-relative-strength/freshness/
turnover meaningfully predict which qualified signal outperforms as a
*continuous* factor. This script asks three different questions instead:

  (B) market environment: does regime (BULL/BEAR) at signal time predict
      outcome, and which SECTORS produce durable (consistently positive)
      signals vs noisy ones?
  (C) signal lifecycle: when a symbol qualifies for several consecutive
      trading days in a row, does "day 1 of the streak" (birth) outperform
      "day 4+" (decay)?
  (D) archetypes: a small set of PRE-SPECIFIED (not data-mined) rule-based
      buckets built from existing features — fresh-volume-breakout,
      extended-momentum, quiet-sector-drift, high-ADX-trend, low-ADX-drift
      — compared on forward return and win rate.

NOT attempted: "why did this signal occur (economic cause)". This dataset
is price/volume/derived-indicator only — no earnings, news, order-flow, or
fundamentals data exists in the pipeline (see docs/23_Assumption_Audit.md
§XXI). Any "economic cause" answer here would be fabricated. Technical
cause (breakout/drift/gap pattern) is answered instead, via archetypes.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats as scistats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import repository as repo

CSV_PATH = "outputs/backtest_decisions.csv"


def load_forward_returns(symbols, horizon: int) -> dict:
    out = {}
    for sym in symbols:
        df = repo.load_ohlcv(sym)
        if df.empty:
            continue
        close = df["close"]
        out[sym] = (close.shift(-horizon) / close - 1, close.index)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--horizon", type=int, default=11)
    ap.add_argument("--min-n", type=int, default=40, help="minimum n to report a group")
    args = ap.parse_args()

    df = pd.read_csv(CSV_PATH, parse_dates=["date"])
    df["date"] = df["date"].dt.normalize()
    q = df[(df["signal"] == "YES") & (df["symbol"] != "__REPLACE_DEBUG__")].copy()

    symbols = q["symbol"].unique().tolist()
    fwd_map = load_forward_returns(symbols, args.horizon)

    def get_fwd_and_tidx(row):
        entry = fwd_map.get(row["symbol"])
        if entry is None:
            return np.nan, -1
        s, idx = entry
        if row["date"] not in s.index:
            return np.nan, -1
        pos = idx.get_loc(row["date"])
        return s.loc[row["date"]], pos

    res = q.apply(get_fwd_and_tidx, axis=1, result_type="expand")
    q["fwd_ret"], q["_tidx"] = res[0], res[1]
    q = q.dropna(subset=["fwd_ret"]).copy()
    print(f"n={len(q)}  horizon={args.horizon}d  overall mean={q['fwd_ret'].mean():+.4f}  win_rate={ (q['fwd_ret']>0).mean():.3f}\n")

    # ── B1. Market environment (regime) ──────────────────────────────
    print("=== B1. By regime ===")
    for regime, grp in q.groupby("regime"):
        if len(grp) < args.min_n:
            continue
        print(f"  {regime:<8} n={len(grp):>5}  mean={grp['fwd_ret'].mean():+.4f}  median={grp['fwd_ret'].median():+.4f}  win_rate={(grp['fwd_ret']>0).mean():.3f}")
    if q["regime"].nunique() == 2:
        groups = [g["fwd_ret"].values for _, g in q.groupby("regime")]
        u, p = scistats.mannwhitneyu(*groups, alternative="two-sided")
        print(f"  Mann-Whitney BULL vs BEAR: p={p:.4f}")

    # ── B2. Sector durability ────────────────────────────────────────
    print("\n=== B2. By sector (min n={}) ===".format(args.min_n))
    sec_stats = q.groupby("sector")["fwd_ret"].agg(n="count", mean="mean", std="std", win_rate=lambda x: (x > 0).mean())
    sec_stats = sec_stats[sec_stats["n"] >= args.min_n].copy()
    sec_stats["t_stat"] = sec_stats["mean"] / (sec_stats["std"] / np.sqrt(sec_stats["n"]))
    sec_stats = sec_stats.sort_values("t_stat", ascending=False)
    for sector, row in sec_stats.iterrows():
        print(f"  {sector:<35} n={int(row['n']):>5}  mean={row['mean']:+.4f}  win_rate={row['win_rate']:.3f}  t={row['t_stat']:+.2f}")
    print("  (t-stat = mean/SE, a rough durability/consistency signal, not just raw mean)")

    # ── C. Lifecycle: streak position (birth vs decay) ───────────────
    print("\n=== C. Signal lifecycle: streak day-position ===")
    q = q.sort_values(["symbol", "_tidx"])
    q["_gap"] = q.groupby("symbol")["_tidx"].diff()
    q["_new_streak"] = (q["_gap"] != 1) | q["_gap"].isna()
    q["_streak_id"] = q.groupby("symbol")["_new_streak"].cumsum()
    q["day_in_streak"] = q.groupby(["symbol", "_streak_id"]).cumcount() + 1

    bins = [0, 1, 3, 7, np.inf]
    labels = ["day1 (birth)", "day2-3", "day4-7", "day8+ (decay)"]
    q["streak_bucket"] = pd.cut(q["day_in_streak"], bins=bins, labels=labels)
    for bucket in labels:
        grp = q[q["streak_bucket"] == bucket]
        if len(grp) < args.min_n:
            print(f"  {bucket:<16} n={len(grp):>5}  (below min-n, skipped)")
            continue
        print(f"  {bucket:<16} n={len(grp):>5}  mean={grp['fwd_ret'].mean():+.4f}  median={grp['fwd_ret'].median():+.4f}  win_rate={(grp['fwd_ret']>0).mean():.3f}")
    birth = q[q["streak_bucket"] == "day1 (birth)"]["fwd_ret"].dropna()
    decay = q[q["streak_bucket"].isin(["day4-7", "day8+ (decay)"])]["fwd_ret"].dropna()
    if len(birth) >= args.min_n and len(decay) >= args.min_n:
        u, p = scistats.mannwhitneyu(birth, decay, alternative="two-sided")
        print(f"  Mann-Whitney day1 vs day4+: p={p:.4f}")

    # ── D. Archetypes (pre-specified, not data-mined) ─────────────────
    print("\n=== D. Archetypes ===")
    ext = pd.to_numeric(q["breakout_dist_pct"], errors="coerce")
    vol = pd.to_numeric(q["vol_ratio"], errors="coerce")
    adx = pd.to_numeric(q["adx"], errors="coerce")

    archetypes = {
        "fresh_volume_breakout":  (ext.between(-0.02, 0.05)) & (vol >= 1.5),
        "extended_momentum":      (ext > 0.10),
        "quiet_sector_drift":     (vol < 1.0) & (ext.between(-0.05, 0.05)),
        "high_adx_trend":         (adx >= 30),
        "low_adx_weak_trend":     (adx < 20),
    }
    for name, mask in archetypes.items():
        grp = q[mask.fillna(False)]
        if len(grp) < args.min_n:
            print(f"  {name:<25} n={len(grp):>5}  (below min-n, skipped)")
            continue
        print(f"  {name:<25} n={len(grp):>5}  mean={grp['fwd_ret'].mean():+.4f}  median={grp['fwd_ret'].median():+.4f}  win_rate={(grp['fwd_ret']>0).mean():.3f}")

    baseline_mean = q["fwd_ret"].mean()
    print(f"\n  (baseline overall mean={baseline_mean:+.4f} for reference)")


if __name__ == "__main__":
    main()
