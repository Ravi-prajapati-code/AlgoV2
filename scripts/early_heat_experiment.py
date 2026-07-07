#!/usr/bin/env python3
"""Early-Heat Counterfactual (Phase 1.5 follow-up).

Trade Attribution Engine finding: entry-time indicators (RS rank, ATR%, vol
ratio, ADX, EMA distance) cannot distinguish eventual LONG_WINNER trades from
QUICK_LOSER trades — but day 1/3/5/10 forward returns can (winners average
+3.5% by day 5, losers average -3.5%). This script asks a narrow, testable
question purely as a historical replay: if we had cut STRENGTH_CONFIRMED_BUY
trades early on a bad day-5 return instead of waiting for the normal exit,
would that have improved total PnL?

This is a pure counterfactual over already-closed historical trades. It does
NOT modify backtest/engine.py, does not change any live strategy code, and
does not simulate a new/different sequence of portfolio decisions (position
slots freed up early aren't reinvested elsewhere) — it only bounds whether the
early-heat signal is worth building into the real engine as an actual rule.
A positive result here is a green light to prototype properly (in the real
engine, through walk_forward.py / stress tests) before it's a green light to
change live behavior.

Usage:
    python3 scripts/early_heat_experiment.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]
"""
import argparse
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from charges.calculator import net_pnl
from scripts.trade_attribution import run, _index_pos


def price_and_date_at_offset(df, ref_date, n_sessions):
    pos = _index_pos(df, pd.Timestamp(ref_date))
    if pos is None:
        return None, None
    target = pos + n_sessions
    if target < 0 or target >= len(df.index):
        return None, None
    row = df.iloc[target]
    return row["close"], df.index[target]


def counterfactual_cut(trade_row, data, threshold_pct):
    """If this trade was still open at day 5 and day-5 return <= threshold,
    replace its outcome with an exit at day 5's close. Otherwise unchanged.
    Returns (net_pnl, was_modified)."""
    if trade_row["entry_trigger"] != "STRENGTH_CONFIRMED_BUY":
        return trade_row["net_pnl"], False
    if trade_row["hold_days"] <= 5:
        return trade_row["net_pnl"], False  # already exited before day 5, nothing to change
    day5 = trade_row["day5_ret_pct"]
    if day5 is None or pd.isna(day5) or day5 > threshold_pct:
        return trade_row["net_pnl"], False  # rule doesn't trigger, keep actual outcome

    df = data.get(trade_row["symbol"])
    if df is None:
        return trade_row["net_pnl"], False
    exit_price, _ = price_and_date_at_offset(df, trade_row["entry_date"], 5)
    if exit_price is None:
        return trade_row["net_pnl"], False

    pnl = net_pnl(trade_row["entry_price"], exit_price, trade_row["shares"])
    return pnl["net_pnl"], True


def evaluate_threshold(df_out: pd.DataFrame, data: dict, threshold_pct: float):
    results = df_out.apply(
        lambda r: counterfactual_cut(r, data, threshold_pct), axis=1, result_type="expand"
    )
    results.columns = ["counterfactual_pnl", "modified"]
    n_modified = int(results["modified"].sum())
    actual_total = df_out["net_pnl"].sum()
    counterfactual_total = results["counterfactual_pnl"].sum()
    return {
        "threshold_pct": threshold_pct,
        "trades_modified": n_modified,
        "actual_total_pnl": round(actual_total, 2),
        "counterfactual_total_pnl": round(counterfactual_total, 2),
        "delta": round(counterfactual_total - actual_total, 2),
    }


def main(start_str, end_str):
    df_out, data, _result = run(start_str, end_str)
    print("\n=== Early-Heat Counterfactual: cut STRENGTH_CONFIRMED_BUY trades early "
          "if day-5 return <= threshold ===")
    print(f"{'threshold':>10}  {'trades_cut':>10}  {'actual_pnl':>12}  {'counterfactual_pnl':>19}  {'delta':>10}")
    for threshold in [-1, -2, -3, -5, -8]:
        r = evaluate_threshold(df_out, data, threshold)
        print(f"{r['threshold_pct']:>9}%  {r['trades_modified']:>10}  "
              f"{r['actual_total_pnl']:>12,.0f}  {r['counterfactual_total_pnl']:>19,.0f}  "
              f"{r['delta']:>+10,.0f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Early-heat cut counterfactual")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    main(args.start, args.end)
