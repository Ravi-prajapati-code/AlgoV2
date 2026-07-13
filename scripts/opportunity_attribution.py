#!/usr/bin/env python3
"""Opportunity Attribution Engine.

Prior research in this session (docs/23_Assumption_Audit.md §XIV/§XV) asked
"why did we buy this stock?" This asks the more valuable question: "among
all valid signals today, did we buy the best one, and if not, exactly why?"

Scores EVERY qualified candidate (not just executed trades) by its forward
return, then quantifies the gap between the alpha the entry signal discovers
and the alpha the portfolio actually captures. Answers, per rebalance day:

  - How many signals generated?              -> candidate count
  - Why rejected before qualification?        -> see signal_diagnostics.py
  - Why were qualified signals not bought?     -> portfolio bottleneck reason
  - What was the best available signal?        -> its forward return
  - What did we actually buy?                  -> its forward return
  - How much alpha was missed?                 -> opportunity cost
  - Was the ranking correct?                   -> rank vs forward-return correlation
  - Was the portfolio forced to skip winners?   -> yes/no + count

Requires outputs/backtest_decisions.csv (FULL-mode backtest) and the
price cache in the SQLite DB (db/repository.load_ohlcv) already populated
by that same backtest run.
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
    """symbol -> pd.Series of forward `horizon`-trading-day returns, indexed by date."""
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
    ap.add_argument("--horizon", type=int, default=20, help="forward-return horizon in trading days (default 20 ~ 1 month)")
    args = ap.parse_args()

    df = pd.read_csv(CSV_PATH, parse_dates=["date"])
    df["date"] = df["date"].dt.normalize()
    qualified = df[df["signal"] == "YES"].copy()

    symbols = sorted(qualified["symbol"].unique())
    print(f"[opportunity_attribution] {len(qualified)} qualified symbol-day rows, {len(symbols)} symbols, "
          f"horizon={args.horizon}d. Loading price cache...")
    fwd_map = load_forward_returns(symbols, args.horizon)

    def get_fwd(row):
        s = fwd_map.get(row["symbol"])
        if s is None:
            return np.nan
        return s.get(row["date"], np.nan)

    qualified["fwd_return"] = qualified.apply(get_fwd, axis=1)
    n_before = len(qualified)
    qualified = qualified.dropna(subset=["fwd_return"])
    print(f"  {len(qualified)}/{n_before} rows have a valid forward return (rest fall within the last "
          f"{args.horizon} trading days of the window, dropped).\n")

    # ---- Portfolio bottleneck classification (why qualified but not bought) ----
    bought_by_date = qualified[qualified["selected"] == "YES"].groupby("date")
    max_bought_rank = bought_by_date["rank_score"].max()
    any_bought = set(bought_by_date.groups.keys())

    def bottleneck(row):
        if row["selected"] == "YES":
            return "BOUGHT"
        if not row.get("market_bullish", True):
            return "BEAR_BLOCKED"
        if row["date"] not in any_bought:
            return "NO_SLOT_AVAILABLE"
        if row["rank_score"] >= max_bought_rank.get(row["date"], np.inf):
            return "SKIPPED_DESPITE_QUALIFYING"
        return "OUTRANKED_SAME_DAY"

    qualified["bottleneck"] = qualified.apply(bottleneck, axis=1)

    print("=== Why were qualified signals not bought? ===")
    for reason, cnt in qualified["bottleneck"].value_counts().items():
        print(f"    {reason:<28} {cnt:>6}  ({100*cnt/len(qualified):.1f}%)")

    # ---- Best available vs actually bought, per day ----
    daily_best = qualified.loc[qualified.groupby("date")["fwd_return"].idxmax()]
    bought = qualified[qualified["selected"] == "YES"]
    daily_bought_avg = bought.groupby("date")["fwd_return"].mean()

    print(f"\n=== Signal quality (forward {args.horizon}-day return) ===")
    print(f"    All qualified candidates : mean {qualified['fwd_return'].mean():+.2%}  median {qualified['fwd_return'].median():+.2%}")
    print(f"    Best available each day  : mean {daily_best['fwd_return'].mean():+.2%}  median {daily_best['fwd_return'].median():+.2%}")
    print(f"    Actually bought          : mean {bought['fwd_return'].mean():+.2%}  median {bought['fwd_return'].median():+.2%}")

    # ---- Opportunity cost: best-available vs actually-bought, per day ----
    merged = daily_best.set_index("date")["fwd_return"].rename("best").to_frame()
    merged["bought_avg"] = daily_bought_avg
    merged["bought_avg"] = merged["bought_avg"].fillna(0.0)  # nothing bought that day = 0 captured
    merged["opportunity_cost"] = merged["best"] - merged["bought_avg"]

    print(f"\n=== Opportunity cost (best available - what we captured) ===")
    print(f"    Mean daily opportunity cost : {merged['opportunity_cost'].mean():+.2%}")
    print(f"    Days where cost > 5pp       : {(merged['opportunity_cost'] > 0.05).sum()} / {len(merged)} "
          f"({100*(merged['opportunity_cost'] > 0.05).mean():.1f}%)")
    print(f"    Days where we captured the best (cost <= 0) : {(merged['opportunity_cost'] <= 0).sum()} "
          f"({100*(merged['opportunity_cost'] <= 0).mean():.1f}%)")

    # ---- Was portfolio forced to skip winners? ----
    skipped = qualified[qualified["bottleneck"] == "SKIPPED_DESPITE_QUALIFYING"]
    winners_skipped = skipped[skipped["fwd_return"] > 0.10]
    print(f"\n=== Forced to skip winners? ===")
    print(f"    Qualified-but-unbought rows that beat what WAS bought that day: {len(skipped)}")
    print(f"    ...of which returned >10% over the horizon anyway: {len(winners_skipped)}")
    print(f"    Answer: {'YES' if len(winners_skipped) > 0 else 'NO'} — portfolio construction left "
          f"real winners on the table {len(winners_skipped)} times.")

    # ---- Was the ranking correct? Daily Spearman(rank_score, fwd_return) ----
    print(f"\n=== Was the ranking correct? (Spearman rank_score vs forward return, per day, days with >=3 candidates) ===")
    corrs = []
    for d, g in qualified.groupby("date"):
        if len(g) >= 3 and g["rank_score"].nunique() > 1:
            rho, _ = scistats.spearmanr(g["rank_score"], g["fwd_return"])
            if not np.isnan(rho):
                corrs.append(rho)
    corrs = np.array(corrs)
    print(f"    Days evaluated: {len(corrs)}")
    print(f"    Mean daily rank correlation  : {corrs.mean():+.3f}")
    print(f"    % of days correlation > 0 (higher RS -> better forward return): {100*(corrs > 0).mean():.1f}%")
    print(f"    % of days correlation < 0 (higher RS -> WORSE forward return) : {100*(corrs < 0).mean():.1f}%")
    if corrs.mean() < 0:
        print("    -> Ranking is directionally WRONG: higher RS rank predicts lower forward return, on average.")
    elif abs(corrs.mean()) < 0.05:
        print("    -> Ranking carries ~no information: RS rank does not predict forward return either way.")
    else:
        print("    -> Ranking is directionally correct: higher RS rank predicts higher forward return.")

    merged.to_csv("outputs/opportunity_attribution_daily.csv")
    qualified.to_csv("outputs/opportunity_attribution_candidates.csv", index=False)
    print(f"\nSaved: outputs/opportunity_attribution_daily.csv, outputs/opportunity_attribution_candidates.csv")


if __name__ == "__main__":
    main()
