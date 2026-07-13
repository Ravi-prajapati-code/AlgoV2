#!/usr/bin/env python3
"""Sector-neutrality diagnostic (docs/23_Assumption_Audit.md §XXIII).

Before excluding sectors outright, check whether the sector effect found in
§XXII is really "avoid bad sectors" or actually "the RS-ranking process
already concentrates in strong sectors, and top-overall picks just happen to
skew that way." Compares two day-by-day selection rules on the SAME
qualified-signal pool:

  top_overall   = single best rank_score across ALL sectors that day
  top_per_sector = best rank_score WITHIN EACH sector that day (one pick
                   per sector present, so weak sectors are still represented)

If top_overall clearly beats top_per_sector, the edge is concentration in
strong sectors (diversifying across sectors, including weak ones, dilutes
it) — supports a blacklist approach. If they're similar, the sector effect
found in §XXII isn't really about AVOIDING bad sectors specifically.
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
        out[sym] = close.shift(-horizon) / close - 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=11)
    args = ap.parse_args()

    df = pd.read_csv(CSV_PATH, parse_dates=["date"])
    df["date"] = df["date"].dt.normalize()
    q = df[(df["signal"] == "YES") & (df["symbol"] != "__REPLACE_DEBUG__")].copy()

    symbols = q["symbol"].unique().tolist()
    fwd_map = load_forward_returns(symbols, args.horizon)

    def get_fwd(row):
        s = fwd_map.get(row["symbol"])
        if s is None or row["date"] not in s.index:
            return np.nan
        return s.loc[row["date"]]

    q["fwd_ret"] = q.apply(get_fwd, axis=1)
    q = q.dropna(subset=["fwd_ret"])

    top_overall_rows = []
    top_per_sector_rows = []
    for date, grp in q.groupby("date"):
        if grp.empty:
            continue
        top_overall_rows.append(grp.loc[grp["rank_score"].idxmax()])
        for sector, sgrp in grp.groupby("sector"):
            top_per_sector_rows.append(sgrp.loc[sgrp["rank_score"].idxmax()])

    top_overall = pd.DataFrame(top_overall_rows)
    top_per_sector = pd.DataFrame(top_per_sector_rows)

    print(f"horizon={args.horizon}d")
    print(f"top_overall:    n={len(top_overall):>5}  mean={top_overall['fwd_ret'].mean():+.4f}  median={top_overall['fwd_ret'].median():+.4f}  win_rate={(top_overall['fwd_ret']>0).mean():.3f}")
    print(f"top_per_sector: n={len(top_per_sector):>5}  mean={top_per_sector['fwd_ret'].mean():+.4f}  median={top_per_sector['fwd_ret'].median():+.4f}  win_rate={(top_per_sector['fwd_ret']>0).mean():.3f}")

    u, p = scistats.mannwhitneyu(top_overall["fwd_ret"], top_per_sector["fwd_ret"], alternative="two-sided")
    print(f"Mann-Whitney top_overall vs top_per_sector: p={p:.4f}")

    print("\nsector representation in top_overall (does 'top pick' already concentrate in a few sectors?):")
    print(top_overall["sector"].value_counts(normalize=True).head(8).round(3))


if __name__ == "__main__":
    main()
