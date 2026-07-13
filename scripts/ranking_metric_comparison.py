#!/usr/bin/env python3
"""Q3: can a different ordering metric improve selection among already-
qualified candidates, WITHOUT changing the qualification rules (check_entry
gates stay identical - this only reorders who wins a slot when multiple
candidates qualify same day)?

Tests each candidate ranking metric's daily Spearman correlation with
forward return, same method as opportunity_attribution.py's rs_rank test,
so results are directly comparable:
  - rs_rank            (current live ranking metric)
  - adx                (trend strength - higher = stronger trend)
  - extension_pct       (distance above EMA50 - lower = less overextended,
                          so tested as -extension_pct, i.e. "freshness")
  - breakout_dist_pct   (distance below/above 20d high - closer to 0 = at
                          the pivot; tested as -abs(breakout_dist_pct))
  - turnover           (liquidity - higher = more institutional participation)
"""
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats as scistats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import repository as repo

DECISIONS_CSV = "outputs/backtest_decisions.csv"
HORIZON = 20


def load_forward_returns(symbols, horizon):
    out = {}
    for sym in symbols:
        df = repo.load_ohlcv(sym)
        if df.empty:
            continue
        close = df["close"]
        out[sym] = close.shift(-horizon) / close - 1
    return out


def main():
    df = pd.read_csv(DECISIONS_CSV, parse_dates=["date"])
    df["date"] = df["date"].dt.normalize()
    qualified = df[df["signal"] == "YES"].copy()
    symbols = sorted(qualified["symbol"].unique())

    print(f"[ranking_metric_comparison] {len(qualified)} qualified rows, horizon={HORIZON}d")
    fwd_map = load_forward_returns(symbols, HORIZON)

    def get_fwd(row):
        s = fwd_map.get(row["symbol"])
        return np.nan if s is None else s.get(row["date"], np.nan)

    qualified["fwd_return"] = qualified.apply(get_fwd, axis=1)
    qualified = qualified.dropna(subset=["fwd_return"])

    # derived metrics: "better" direction normalized so higher = hypothesized-better
    qualified["freshness"] = -qualified["extension_pct"]          # less overextended = better
    qualified["at_pivot"] = -qualified["breakout_dist_pct"].abs()  # closer to 20d high = better

    metrics = {
        "rs_rank (current live metric)": "rank_score",
        "adx (trend strength)": "adx",
        "freshness (-extension from EMA50)": "freshness",
        "at_pivot (-|dist from 20d high|)": "at_pivot",
        "turnover (liquidity)": "turnover",
    }

    print(f"\n{'metric':<38} {'n_days':>7} {'mean_corr':>10} {'%pos':>6} {'%neg':>6}")
    print("-" * 72)
    results = {}
    for label, col in metrics.items():
        corrs = []
        for d, g in qualified.groupby("date"):
            if len(g) >= 3 and g[col].nunique() > 1:
                rho, _ = scistats.spearmanr(g[col], g["fwd_return"])
                if not np.isnan(rho):
                    corrs.append(rho)
        corrs = np.array(corrs)
        results[label] = corrs
        print(f"{label:<38} {len(corrs):>7} {corrs.mean():>+10.3f} {100*(corrs>0).mean():>5.1f}% {100*(corrs<0).mean():>5.1f}%")

    best = max(results, key=lambda k: results[k].mean())
    print(f"\nBest single metric: {best} (mean corr {results[best].mean():+.3f})")
    print("For reference: correlation this weak (|r|<0.05) means the metric is not")
    print("usefully predictive regardless of which one wins this comparison.")


if __name__ == "__main__":
    main()
