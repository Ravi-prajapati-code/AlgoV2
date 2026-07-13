#!/usr/bin/env python3
"""Signal Mechanism Analysis.

Not "which ranking formula orders candidates best" (tested and rejected in
scripts/ranking_metric_comparison.py — rs_rank/adx/freshness/at_pivot/turnover
all ~0 correlation with forward return). This asks a different question:
among ALL qualified signals (bought or not), what actually separates the
ones that turn into winners from the ones that turn into losers? Tests
candidate market MECHANISMS, not just another numeric reranking:

  - breakout freshness   (breakout_dist_pct: how far past/short of the 20d
                           high the stock is right now)
  - volume/institutional  (vol_ratio: today's volume vs its own 20d average
    acceleration proxy      — a surge proxy, not a ranking score)
  - sector leadership     (sector_rel_rs: stock's rs_rank minus its own
                           sector's average rs_rank that day — is THIS stock
                           the leader within its sector, independent of
                           whether the sector itself is strong)
  - sector tailwind       (sector_rs_avg: the sector's own average rs_rank
                           that day — is the whole sector strong, regardless
                           of which stock within it you pick)

Reports both: (a) tercile winner-vs-loser group comparison with a
Mann-Whitney U significance test, and (b) full-sample Spearman correlation
with forward return, at the REAL median holding horizon (11 trading days,
per docs/23_Assumption_Audit.md §XVII), not just 20d.
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
        fwd = close.shift(-horizon) / close - 1
        out[sym] = fwd
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--horizon", type=int, default=11, help="forward-return horizon in trading days (default 11 = real median hold)")
    args = ap.parse_args()

    df = pd.read_csv(CSV_PATH, parse_dates=["date"])
    df["date"] = df["date"].dt.normalize()
    qualified = df[df["signal"] == "YES"].copy()
    qualified = qualified[qualified["symbol"] != "__REPLACE_DEBUG__"]

    symbols = qualified["symbol"].unique().tolist()
    fwd_map = load_forward_returns(symbols, args.horizon)

    def get_fwd(row):
        s = fwd_map.get(row["symbol"])
        if s is None or row["date"] not in s.index:
            return np.nan
        return s.loc[row["date"]]

    qualified["fwd_ret"] = qualified.apply(get_fwd, axis=1)
    qualified["sector_rs_avg"] = qualified["rs_rank"] - pd.to_numeric(qualified["sector_rel_rs"], errors="coerce")

    q = qualified.dropna(subset=["fwd_ret"]).copy()
    q = q[pd.to_numeric(q["sector_rel_rs"], errors="coerce").notna()]
    print(f"Qualified signals with valid fwd_ret + sector data: n={len(q)}  (horizon={args.horizon}d)")
    print(f"fwd_ret: mean={q['fwd_ret'].mean():+.4f}  median={q['fwd_ret'].median():+.4f}  std={q['fwd_ret'].std():.4f}\n")

    features = {
        "vol_ratio (institutional/volume surge)": "vol_ratio",
        "breakout_dist_pct (freshness)": "breakout_dist_pct",
        "sector_rel_rs (leadership within sector)": "sector_rel_rs",
        "sector_rs_avg (sector-wide tailwind)": "sector_rs_avg",
        "rs_rank (baseline, for comparison)": "rs_rank",
        "adx (baseline, for comparison)": "adx",
    }

    print("=== Full-sample Spearman correlation vs forward return ===")
    for label, col in features.items():
        x = pd.to_numeric(q[col], errors="coerce")
        mask = x.notna() & q["fwd_ret"].notna()
        rho, p = scistats.spearmanr(x[mask], q.loc[mask, "fwd_ret"])
        print(f"  {label:<45} rho={rho:+.4f}  p={p:.4f}  n={mask.sum()}")

    print("\n=== Tercile winner-vs-loser comparison (top 1/3 fwd_ret vs bottom 1/3) ===")
    lo_cut, hi_cut = q["fwd_ret"].quantile([1/3, 2/3])
    losers = q[q["fwd_ret"] <= lo_cut]
    winners = q[q["fwd_ret"] >= hi_cut]
    print(f"  losers n={len(losers)} (fwd_ret<={lo_cut:+.3f})   winners n={len(winners)} (fwd_ret>={hi_cut:+.3f})\n")

    for label, col in features.items():
        wl = pd.to_numeric(winners[col], errors="coerce").dropna()
        ll = pd.to_numeric(losers[col], errors="coerce").dropna()
        if len(wl) < 5 or len(ll) < 5:
            continue
        u, p = scistats.mannwhitneyu(wl, ll, alternative="two-sided")
        print(f"  {label:<45} winners_mean={wl.mean():>8.2f}  losers_mean={ll.mean():>8.2f}  delta={wl.mean()-ll.mean():>+8.2f}  MW p={p:.4f}")


if __name__ == "__main__":
    main()
