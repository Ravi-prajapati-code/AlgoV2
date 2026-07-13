#!/usr/bin/env python3
"""Per user request: for every day the portfolio was full and a better
candidate was skipped (NO_SLOT_AVAILABLE), log:

  Weak Holding | its fwd return | New Candidate (best available) | its fwd
  return | Replace? (always "No" - no rotation mechanism exists in the live
  code, see strategy/exit.py) | Future Difference

One row per day (the single weakest holding vs the single best skipped
candidate that day), not one row per candidate - answers "was THIS specific
swap worth it," not just "did something beat something."
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import repository as repo

TRADES_CSV = "outputs/backtest_trades.csv"
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
    trades = pd.read_csv(TRADES_CSV, parse_dates=["entry_date", "exit_date"])
    trades["entry_date"] = trades["entry_date"].dt.normalize()
    trades["exit_date"] = trades["exit_date"].dt.normalize()

    df = pd.read_csv(DECISIONS_CSV, parse_dates=["date"])
    df["date"] = df["date"].dt.normalize()
    qualified = df[df["signal"] == "YES"].copy()
    symbols = sorted(set(qualified["symbol"]) | set(trades["symbol"]))
    fwd_map = load_forward_returns(symbols, HORIZON)

    def get_fwd(sym, date):
        s = fwd_map.get(sym)
        return np.nan if s is None else s.get(date, np.nan)

    qualified["fwd_return"] = qualified.apply(lambda r: get_fwd(r["symbol"], r["date"]), axis=1)
    qualified = qualified.dropna(subset=["fwd_return"])

    bought_by_date = qualified[qualified["selected"] == "YES"].groupby("date")
    any_bought = set(bought_by_date.groups.keys())

    def bottleneck(row):
        if row["selected"] == "YES":
            return "BOUGHT"
        if not row.get("market_bullish", True):
            return "BEAR_BLOCKED"
        if row["date"] not in any_bought:
            return "NO_SLOT_AVAILABLE"
        return "OTHER"

    qualified["bottleneck"] = qualified.apply(bottleneck, axis=1)
    no_slot = qualified[qualified["bottleneck"] == "NO_SLOT_AVAILABLE"].copy()

    rows = []
    for d, g in no_slot.groupby("date"):
        held = trades[(trades["entry_date"] <= d) & (trades["exit_date"] >= d)]
        if held.empty:
            continue
        held_fwds = [(sym, get_fwd(sym, d)) for sym in held["symbol"]]
        held_fwds = [(s, f) for s, f in held_fwds if not np.isnan(f)]
        if not held_fwds:
            continue
        weak_sym, weak_fwd = min(held_fwds, key=lambda x: x[1])

        best_row = g.loc[g["fwd_return"].idxmax()]
        cand_sym, cand_fwd = best_row["symbol"], best_row["fwd_return"]

        rows.append({
            "date": d.date(),
            "weak_holding": weak_sym,
            "weak_fwd_return": weak_fwd,
            "new_candidate": cand_sym,
            "candidate_fwd_return": cand_fwd,
            "replaced": "No",  # no rotation mechanism exists - see strategy/exit.py
            "future_difference": cand_fwd - weak_fwd,
        })

    out = pd.DataFrame(rows)
    out.to_csv("outputs/replacement_diagnostic.csv", index=False)

    print(f"Days with a full portfolio + a skipped candidate + a comparable held position: {len(out)}")
    print(f"\n{'date':<12} {'weak_holding':<14} {'weak_fwd':>9} {'new_candidate':<14} {'cand_fwd':>9} {'replace?':<9} {'fut_diff':>9}")
    for _, r in out.head(15).iterrows():
        print(f"{str(r['date']):<12} {r['weak_holding']:<14} {r['weak_fwd_return']:>+8.2%} "
              f"{r['new_candidate']:<14} {r['candidate_fwd_return']:>+8.2%} {r['replaced']:<9} {r['future_difference']:>+8.2%}")
    print(f"... ({len(out)} total rows, full table in outputs/replacement_diagnostic.csv)")

    helped = out[out["future_difference"] > 0]
    hurt = out[out["future_difference"] < 0]
    print(f"\n=== Aggregate ===")
    print(f"Replacement would have helped (fut_diff > 0): {len(helped)} / {len(out)} ({100*len(helped)/len(out):.1f}%)")
    print(f"Replacement would have hurt   (fut_diff < 0): {len(hurt)} / {len(out)} ({100*len(hurt)/len(out):.1f}%)")
    print(f"Mean future difference (all days)  : {out['future_difference'].mean():+.2%}")
    print(f"Mean future difference (when help)  : {helped['future_difference'].mean():+.2%}")
    print(f"Mean future difference (when hurt)  : {hurt['future_difference'].mean():+.2%}")
    print(f"Median future difference            : {out['future_difference'].median():+.2%}")

    # How many DISTINCT rotation events would this be, vs how many days flagged?
    # (a rotation on day 1 changes what's "held" for days 2..N, so this count
    # overstates independent opportunities - it's an upper bound / diagnostic,
    # not a trade count for a simulated policy.)
    print(f"\nNote: {len(out)} is a day-count upper bound, not independent rotation events -")
    print(f"a real replace-weakest-holding policy needs its own simulated backtest (state")
    print(f"changes after each swap) before this becomes a CAGR estimate. This table answers")
    print(f"only 'was there value sitting there,' not 'how much would a policy capture.'")


if __name__ == "__main__":
    main()
