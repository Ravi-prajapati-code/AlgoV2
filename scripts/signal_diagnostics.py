#!/usr/bin/env python3
"""Day-by-day signal diagnostics from outputs/backtest_decisions.csv.

Answers: how many days produce zero/few signals, why (gate-by-gate rejection
breakdown), and whether the ranking (RS-descending) actually matters on days
where it has more than one candidate to choose from.

Run a FULL-mode backtest first (python3 main.py backtest) to regenerate the
decision log, then run this script.
"""
import re
import sys

import pandas as pd

CSV_PATH = "outputs/backtest_decisions.csv"


def categorize(reason: str) -> str:
    return re.sub(r"\s*\(.*$", "", str(reason)).strip()


def main():
    df = pd.read_csv(CSV_PATH, parse_dates=["date"])
    total_days = df["date"].nunique()

    per_day_qualified = df[df["signal"] == "YES"].groupby("date").size()
    days_with_signal = set(per_day_qualified.index)
    all_days = set(df["date"].unique())
    zero_signal_days = all_days - days_with_signal

    print(f"=== Signal frequency ({total_days} trading days scanned) ===")
    print(f"  Days with >=1 qualified candidate : {len(days_with_signal):>5}  ({100*len(days_with_signal)/total_days:.1f}%)")
    print(f"  Days with ZERO qualified candidates: {len(zero_signal_days):>5}  ({100*len(zero_signal_days)/total_days:.1f}%)")

    # Zero-signal days broken down by regime (check_entry itself is regime-blind;
    # this shows whether "no signal" correlates with BEAR markets or happens in BULL too)
    zero_df = df[df["date"].isin(zero_signal_days)].drop_duplicates("date")
    if "regime" in zero_df.columns:
        print("\n  Zero-signal days by regime:")
        for regime, cnt in zero_df["regime"].value_counts().items():
            print(f"    {regime:<8} {cnt:>5}  ({100*cnt/len(zero_signal_days):.1f}% of zero-signal days)")

    # Distribution of candidate count on non-zero days
    print("\n=== Candidate-count distribution (days with >=1 candidate) ===")
    buckets = pd.cut(per_day_qualified, bins=[0, 1, 2, 3, 5, 100], labels=["1", "2", "3", "4-5", "6+"], right=True, include_lowest=True)
    for label, cnt in buckets.value_counts().sort_index().items():
        print(f"    {label:<6} candidates/day: {cnt:>5} days")
    print(f"    mean when >0: {per_day_qualified.mean():.2f}, median: {per_day_qualified.median():.0f}")

    # Rejection reason histogram (why NOT generated)
    no_df = df[df["signal"] == "NO"].copy()
    no_df["category"] = no_df["reason"].apply(categorize)
    print(f"\n=== Rejection reasons ({len(no_df)} symbol-day NO evaluations) ===")
    hist = no_df["category"].value_counts()
    for cat, cnt in hist.items():
        print(f"    {cat:<45} {cnt:>7}  ({100*cnt/len(no_df):.1f}%)")

    # Does ranking matter? On days with >1 qualified candidate, how often does
    # the RS-first pick differ from what an ADX-first pick would have been?
    multi = df[df["signal"] == "YES"].groupby("date").filter(lambda g: len(g) > 1)
    if not multi.empty and "rank_score" in multi.columns:
        n_multi_days = multi["date"].nunique()
        print(f"\n=== Ranking relevance ===")
        print(f"    Days with >1 qualified candidate (ranking has an actual choice to make): {n_multi_days} "
              f"({100*n_multi_days/len(days_with_signal):.1f}% of signal days)")
        print(f"    On the other {100*(1-n_multi_days/len(days_with_signal)):.1f}% of signal days, only 1 candidate qualifies —")
        print(f"    ranking is a no-op regardless of RS/ADX/random, the gate alone decides the trade.")

    # Actually-bought vs merely-qualified (portfolio slot / market_bullish gate)
    if "selected" in df.columns:
        qualified = (df["signal"] == "YES").sum()
        selected = (df["selected"] == "YES").sum()
        print(f"\n=== Qualified vs executed ===")
        print(f"    Symbol-day rows qualified (signal=YES): {qualified}")
        print(f"    Symbol-day rows actually bought        : {selected}  ({100*selected/qualified:.1f}% of qualified)")
        print(f"    Gap is slots-full / already-held / market_bullish=False (BEAR regime blocks new momentum buys).")


if __name__ == "__main__":
    main()
