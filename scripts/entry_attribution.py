#!/usr/bin/env python3
"""Entry Attribution Suite (docs/23_Assumption_Audit.md §XIV).

Answers the question raised in this session: is the entry/stock-selection
signal (RS rank + ADX/breakout trend gate) itself creating alpha, or is the
system's performance coming from everything else (regime filter, exits,
sizing, universe)? All prior bracket-testing only proved individual
parameters sit at local optima -- it never tested whether the SIGNAL has
positive edge at all.

Runs the same full-window backtest under 7 ENTRY_MODE values (see
strategy/entry.py and strategy/signals.py for the mode implementations):

  FULL               current live signal (RS + ADX/breakout/SuperTrend gate, ranked by RS)
  RANDOM_ALL         any symbol in the universe, no gates, random order       -- floor
  RANDOM_ELIGIBLE    liquidity/extension-safe symbols only, random order      -- floor + safety only
  REVERSE_RS         same gates as FULL, ranked WORST-RS-first
  SHUFFLE_RS         same gates/ranking as FULL, but RS values permuted across symbols
  PURE_RS            RS gate + ranking only, trend/ADX/breakout gate skipped
  PURE_ADX_BREAKOUT  trend/ADX/breakout gate only, RS gate skipped, ranked by ADX
  SURVIVAL_RANK      same gates as FULL, ranked by trade_attribution.py's 2026-07-21
                     survival composite (EMA20/50 extension + dist-from-20d-high
                     percentile avg) instead of RS -- see strategy/signals.py

RANDOM_ALL, RANDOM_ELIGIBLE and SHUFFLE_RS involve a seeded RNG -- each is run
across several seeds and averaged to separate real effect from noise.

Everything else (regime filter, exits, sizing, universe, slippage) is held
fixed at live config across every arm -- this isolates the entry signal only.

Usage:
    python3 scripts/entry_attribution.py
    python3 scripts/entry_attribution.py --start 2022-01-01 --end 2026-07-08
"""
import argparse
import os
import statistics
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.out_of_sample_validator import DEFAULT_HIST_START, run_window

MODES = ["FULL", "RANDOM_ALL", "RANDOM_ELIGIBLE", "REVERSE_RS", "SHUFFLE_RS", "PURE_RS", "PURE_ADX_BREAKOUT", "SURVIVAL_RANK"]
RANDOM_MODES = {"RANDOM_ALL", "RANDOM_ELIGIBLE", "SHUFFLE_RS"}
SEEDS = [42, 7, 123]

METRIC_KEYS = ("cagr", "sharpe", "mdd", "wr", "pf", "n")


def run_mode(mode: str, seed: int, start: str, end: str) -> dict:
    os.environ["ENTRY_MODE"] = mode
    os.environ["ENTRY_MODE_SEED"] = str(seed)
    try:
        return run_window(start, end)
    finally:
        os.environ.pop("ENTRY_MODE", None)
        os.environ.pop("ENTRY_MODE_SEED", None)


def avg_row(runs: list) -> dict:
    return {k: statistics.mean(r[k] for r in runs) for k in METRIC_KEYS}


def fmt_row(label: str, r: dict, n_seeds: int = 1) -> str:
    tag = f" (avg of {n_seeds})" if n_seeds > 1 else ""
    return (f"  {label:<20}{tag:<14} CAGR {r['cagr']:+7.2f}%  Sharpe {r['sharpe']:5.2f}  "
            f"MDD {r['mdd']:6.2f}%  WR {r['wr']:5.1f}%  PF {r['pf']:5.2f}  N={r['n']:.0f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default=DEFAULT_HIST_START)
    ap.add_argument("--end", default=str(date.today()))
    args = ap.parse_args()

    print(f"[entry_attribution] window {args.start} -> {args.end}, seeds={SEEDS}\n")

    results = {}
    for mode in MODES:
        if mode in RANDOM_MODES:
            runs = [run_mode(mode, s, args.start, args.end) for s in SEEDS]
            avg = avg_row(runs)
            results[mode] = avg
            print(fmt_row(mode, avg, n_seeds=len(SEEDS)))
            for s, r in zip(SEEDS, runs):
                print(f"      seed={s:<4} CAGR {r['cagr']:+7.2f}%  Sharpe {r['sharpe']:5.2f}  PF {r['pf']:5.2f}  N={r['n']}")
        else:
            r = run_mode(mode, SEEDS[0], args.start, args.end)
            results[mode] = r
            print(fmt_row(mode, r))

    print("\n=== Summary (sorted by CAGR) ===")
    for mode, r in sorted(results.items(), key=lambda kv: kv[1]["cagr"], reverse=True):
        print(fmt_row(mode, r))

    full, rand_all, rand_elig = results["FULL"], results["RANDOM_ALL"], results["RANDOM_ELIGIBLE"]
    pure_rs, pure_adx = results["PURE_RS"], results["PURE_ADX_BREAKOUT"]
    reverse_rs, shuffle_rs = results["REVERSE_RS"], results["SHUFFLE_RS"]

    print("\n=== Attribution reading ===")
    print(f"  Signal has edge over pure luck        : FULL {full['cagr']:+.2f}% vs RANDOM_ALL {rand_all['cagr']:+.2f}% "
          f"(delta {full['cagr']-rand_all['cagr']:+.2f}pp)")
    print(f"  Signal has edge over safety-only floor : FULL {full['cagr']:+.2f}% vs RANDOM_ELIGIBLE {rand_elig['cagr']:+.2f}% "
          f"(delta {full['cagr']-rand_elig['cagr']:+.2f}pp)")
    print(f"  RS component alone                    : PURE_RS {pure_rs['cagr']:+.2f}%  Sharpe {pure_rs['sharpe']:.2f}")
    print(f"  ADX/breakout component alone          : PURE_ADX_BREAKOUT {pure_adx['cagr']:+.2f}%  Sharpe {pure_adx['sharpe']:.2f}")
    print(f"  Ranking direction matters             : FULL {full['cagr']:+.2f}% vs REVERSE_RS {reverse_rs['cagr']:+.2f}% "
          f"(delta {full['cagr']-reverse_rs['cagr']:+.2f}pp)")
    print(f"  RS value (not just gate) matters      : FULL {full['cagr']:+.2f}% vs SHUFFLE_RS {shuffle_rs['cagr']:+.2f}% "
          f"(delta {full['cagr']-shuffle_rs['cagr']:+.2f}pp)")
    better_component = "RS" if pure_rs["cagr"] >= pure_adx["cagr"] else "ADX/breakout"
    additive_gap = full["cagr"] - max(pure_rs["cagr"], pure_adx["cagr"])
    print(f"  Stronger single component             : {better_component}")
    print(f"  FULL vs best single component (additive if >0, redundant if <=0): {additive_gap:+.2f}pp")


if __name__ == "__main__":
    main()
