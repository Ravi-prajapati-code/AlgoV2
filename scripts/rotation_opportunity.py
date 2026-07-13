#!/usr/bin/env python3
"""Follow-up to opportunity_attribution.py, answering 4 sharper questions:

  Q1. Is the ranking informative at the actual holding horizon (not just 20d)?
  Q2. Within NO_SLOT_AVAILABLE, how many skipped signals would have beaten
      the weakest held position (over the same forward window)?
  Q4. capacity-capture estimate: of the missed opportunity, how much would
      extra slots actually have captured (naive, non-simulated estimate;
      see rerun_capacity.py for the real backtest-level answer).

(Q3 — alternate ordering metrics — is handled separately in
scripts/ranking_metric_comparison.py because it needs new fields in the
decision log, which requires a fresh backtest run.)
"""
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats as scistats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import repository as repo

DECISIONS_CSV = "outputs/backtest_decisions.csv"
TRADES_CSV = "outputs/backtest_trades.csv"


def daily_rank_corr(qualified: pd.DataFrame) -> tuple:
    corrs = []
    for d, g in qualified.groupby("date"):
        if len(g) >= 3 and g["rank_score"].nunique() > 1:
            rho, _ = scistats.spearmanr(g["rank_score"], g["fwd_return"])
            if not np.isnan(rho):
                corrs.append(rho)
    corrs = np.array(corrs)
    return corrs


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
    qualified_base = df[df["signal"] == "YES"].copy()
    symbols = sorted(qualified_base["symbol"].unique())

    trades = pd.read_csv(TRADES_CSV, parse_dates=["entry_date", "exit_date"])
    trades["entry_date"] = trades["entry_date"].dt.normalize()
    trades["exit_date"] = trades["exit_date"].dt.normalize()

    # ---------------------------------------------------------------
    # Q1: ranking informativeness across horizons, incl. the real
    #     holding-period distribution (median 11d, mean 16.4d, p75 21d)
    # ---------------------------------------------------------------
    print("=" * 70)
    print("Q1: Is ranking informative at the ACTUAL holding horizon?")
    print(f"    (real hold_days: median 11, mean 16.4, p75 21, from {len(trades)} trades)")
    print("=" * 70)
    fwd_cache = {}
    for horizon in [5, 10, 11, 16, 20, 30, 40, 60, 90]:
        fwd_map = load_forward_returns(symbols, horizon)
        fwd_cache[horizon] = fwd_map
        q = qualified_base.copy()
        q["fwd_return"] = q.apply(
            lambda r: fwd_map.get(r["symbol"], pd.Series(dtype=float)).get(r["date"], np.nan), axis=1
        )
        q = q.dropna(subset=["fwd_return"])
        corrs = daily_rank_corr(q)
        print(f"    horizon={horizon:>3}d  n_days={len(corrs):>4}  mean_corr={corrs.mean():+.3f}  "
              f"%pos={100*(corrs > 0).mean():.1f}%  %neg={100*(corrs < 0).mean():.1f}%")

    # ---------------------------------------------------------------
    # Q2: within NO_SLOT_AVAILABLE, how many skipped signals beat the
    #     weakest CURRENTLY HELD position, over the same forward window?
    # ---------------------------------------------------------------
    print()
    print("=" * 70)
    print("Q2: NO_SLOT_AVAILABLE skips vs weakest held position (20d fwd window)")
    print("=" * 70)
    HORIZON = 20
    fwd_map = fwd_cache[HORIZON]

    def get_fwd(sym, date):
        s = fwd_map.get(sym)
        if s is None:
            return np.nan
        return s.get(date, np.nan)

    qualified = qualified_base.copy()
    qualified["fwd_return"] = qualified.apply(lambda r: get_fwd(r["symbol"], r["date"]), axis=1)
    qualified = qualified.dropna(subset=["fwd_return"])

    bought_by_date = qualified[qualified["selected"] == "YES"].groupby("date")
    any_bought = set(bought_by_date.groups.keys())
    max_bought_rank = bought_by_date["rank_score"].max()

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
    no_slot = qualified[qualified["bottleneck"] == "NO_SLOT_AVAILABLE"].copy()

    # weakest held position on each date = held symbol whose OWN forward
    # return (over the same 20d window, from that same date) is lowest.
    # "held" = trades where entry_date <= date <= exit_date.
    all_dates = sorted(no_slot["date"].unique())
    weakest_held_fwd = {}
    for d in all_dates:
        held = trades[(trades["entry_date"] <= d) & (trades["exit_date"] >= d)]
        if held.empty:
            continue
        fwds = [get_fwd(sym, d) for sym in held["symbol"]]
        fwds = [f for f in fwds if not np.isnan(f)]
        if fwds:
            weakest_held_fwd[d] = min(fwds)

    no_slot["weakest_held_fwd"] = no_slot["date"].map(weakest_held_fwd)
    comparable = no_slot.dropna(subset=["weakest_held_fwd"])
    beats = comparable[comparable["fwd_return"] > comparable["weakest_held_fwd"]]

    print(f"    NO_SLOT_AVAILABLE rows total          : {len(no_slot)}")
    print(f"    ...with a comparable held position     : {len(comparable)}")
    print(f"    ...that would have beaten weakest held : {len(beats)} "
          f"({100*len(beats)/max(len(comparable),1):.1f}%)")
    print(f"    Mean fwd_return of skipped signal      : {comparable['fwd_return'].mean():+.2%}")
    print(f"    Mean fwd_return of weakest held (same window): {comparable['weakest_held_fwd'].mean():+.2%}")
    print(f"    Mean edge when it does beat weakest    : {(beats['fwd_return'] - beats['weakest_held_fwd']).mean():+.2%}")

    # ---------------------------------------------------------------
    # Q4 (proxy, non-simulated): naive capacity-capture estimate.
    # If 1 extra slot were free every day, what's the best NO_SLOT_AVAILABLE
    # candidate's fwd_return that day, averaged? (Ignores capital/overlap
    # constraints - see rerun_capacity.py for the real simulated answer.)
    # ---------------------------------------------------------------
    print()
    print("=" * 70)
    print("Q4 (naive proxy only - see rerun_capacity.py for the real simulated answer)")
    print("=" * 70)
    best_missed_by_day = no_slot.loc[no_slot.groupby("date")["fwd_return"].idxmax()]
    print(f"    Days with >=1 NO_SLOT_AVAILABLE candidate: {no_slot['date'].nunique()}")
    print(f"    Mean fwd_return of best missed candidate/day: {best_missed_by_day['fwd_return'].mean():+.2%}")
    print(f"    (naive upper bound if 1 extra always-available slot existed every such day —")
    print(f"     ignores capital ties, overlapping holds, and diminishing returns of more slots)")


if __name__ == "__main__":
    main()
