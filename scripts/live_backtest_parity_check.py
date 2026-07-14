"""
Live vs backtest indicator parity check (docs/29 Rule 1 follow-up).

Decision logic (check_entry/check_exit_conditions/generate_signals) is already
shared between live and backtest via direct imports -- verified by reading both
call sites. The actual duplication risk is narrower: indicators/composite.py
(live) and backtest/engine.py's _precompute_all (backtest) are two independent
implementations of the same formulas over the same cached OHLCV data. Every
live/backtest fidelity bug found this arc (ema_50 mislabel, fill timing,
replacement parity, cash buffer) traces back to this kind of silent duplication
drift -- see docs/28_Software_Truth_Audit.md.

This script feeds identical cached daily OHLCV through both implementations for
a fixed recent window and diffs every indicator field they both expose. Exits
non-zero on any mismatch beyond its documented tolerance, so it can be wired
into CI/the gate the same way check_config_drift() is.

Usage: python scripts/live_backtest_parity_check.py [--end YYYY-MM-DD] [--symbols N] [--dates N]
"""
import argparse
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from config.settings import MARKET_INDEX_SYMBOL, INITIAL_CAPITAL, EXECUTION_TIMES
from data.fetcher import fetch_all, fetch_index, filter_symbols_with_insufficient_history
from data.universe import get_all_symbols
from indicators.composite import compute_indicators
from backtest.engine import BacktestEngine
from scripts.out_of_sample_validator import DEFAULT_GATE_END as DEFAULT_PARITY_END

# key -> (abs_tolerance, note). Tolerances absorb *intentional* rounding
# differences between the two implementations (e.g. live rounds rsi to 2dp,
# backtest doesn't round it at all) -- not real divergence. A field with no
# entry here is compared at 1e-6 absolute tolerance (should be byte-identical).
FIELD_TOLERANCES = {
    "ema_20":     (0.01, "live's ema_20 comes from indicators/trend.py's compute_trend() "
                          "which rounds ema_fast to 2dp; backtest's is unrounded"),
    "vol_ratio":  (0.02, "both round to 2dp, +/-1 ulp"),
    "macd_hist":  (0.02, "live rounds 2dp, backtest rounds 4dp"),
    "perf_10d":   (0.02, "live rounds to 2dp, backtest doesn't"),
    "adx":        (0.15, "live rounds 2dp, backtest rounds 1dp"),
    # atr/rsi formula divergence (docs/33) fixed 2026-07-14 -- both now use
    # Wilder's EMA on both sides. Small tolerance for residual float noise only.
    "atr":        (1e-3, "post-fix float noise"),
    "atr_pct":    (1e-4, "post-fix float noise"),
    "rsi":        (1e-2, "post-fix float noise"),
}

COMPARE_KEYS = [
    "close", "ema_20", "ema_50", "ema_100", "ema_150",
    "ema_entry_med", "ema_entry_long", "ema_exit_trend",
    "atr", "atr_pct", "rsi", "turnover", "vol_ratio",
    "macd_hist", "high_20d", "perf_10d", "adx", "st_direction",
]


def _slice_daily(df: pd.DataFrame, as_of: date) -> pd.DataFrame:
    return df[df.index.date <= as_of]


def run_parity_check(end_str: str, n_symbols: int, n_dates: int) -> int:
    end = datetime.strptime(end_str, "%Y-%m-%d").date()
    # 500-day warmup so EMA(150)/(200) are converged at the test dates, same
    # rationale as main.py's cmd_backtest warmup.
    warmup_start = end - timedelta(days=500)
    lookback = (end - warmup_start).days + 10

    symbols = sorted(get_all_symbols())[:n_symbols]
    symbols = filter_symbols_with_insufficient_history(symbols, warmup_start)
    print(f"Fetching {len(symbols)} symbols + index, {warmup_start} -> {end} ...")

    data = fetch_all(symbols, lookback_days=lookback, start=warmup_start, end=end)
    index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=lookback, start=warmup_start, end=end)
    if not index_df.empty:
        data[MARKET_INDEX_SYMBOL] = index_df

    symbols = [s for s in symbols if s in data]
    if not symbols:
        print("No symbols with sufficient cached data -- aborting.", file=sys.stderr)
        return 2

    print("Running backtest engine's real indicator pipeline (_precompute_all) ...")
    engine = BacktestEngine(data, warmup_start, end, INITIAL_CAPITAL)
    all_dates = engine._get_trading_dates()
    all_indicators, _, _ = engine._precompute_all(all_dates)

    t = datetime.strptime(EXECUTION_TIMES[0], "%H:%M").time()
    test_dates = all_dates[-n_dates:]

    mismatches = []
    checked = 0
    for symbol in symbols:
        df = data[symbol]
        for d in test_dates:
            ts_key = pd.Timestamp(datetime.combine(d, t))
            bt_ind = all_indicators.get(ts_key, {}).get(symbol)
            if bt_ind is None:
                continue  # not enough warmup yet at this date for this symbol

            live_slice = _slice_daily(df, d)
            if len(live_slice) < 20:
                continue
            live_ind = compute_indicators(live_slice, symbol=symbol)
            if live_ind is None:
                continue

            checked += 1
            for key in COMPARE_KEYS:
                if key not in live_ind or key not in bt_ind:
                    continue
                lv, bv = live_ind[key], bt_ind[key]
                if lv is None or bv is None or (isinstance(lv, float) and pd.isna(lv)) or (isinstance(bv, float) and pd.isna(bv)):
                    continue
                tol, note = FIELD_TOLERANCES.get(key, (1e-6, None))
                if tol is None:
                    # Known, already-documented divergence -- still record so
                    # its magnitude shows in the summary, but tagged as known.
                    mismatches.append((symbol, d, key, lv, bv, note, True))
                    continue
                if abs(float(lv) - float(bv)) > tol:
                    mismatches.append((symbol, d, key, lv, bv, note, False))

    known = [m for m in mismatches if m[6]]
    new = [m for m in mismatches if not m[6]]

    print(f"\nChecked {checked} symbol-day snapshots across {len(symbols)} symbols x {len(test_dates)} dates.\n")

    if known:
        print(f"--- {len(known)} known-issue mismatches (not gating, see docs/33) ---")
        by_key = {}
        for symbol, d, key, lv, bv, note, _ in known:
            by_key.setdefault(key, []).append(bv - lv if isinstance(lv, (int, float)) else None)
        for key, diffs in by_key.items():
            diffs = [x for x in diffs if x is not None]
            if diffs:
                avg = sum(diffs) / len(diffs)
                print(f"  {key}: {len(diffs)} snapshots, avg (backtest-live) diff = {avg:.4f}")

    if new:
        print(f"\n--- {len(new)} UNEXPECTED mismatches ---")
        for symbol, d, key, lv, bv, note, _ in new[:30]:
            print(f"  {symbol} {d} {key}: live={lv} backtest={bv} (tol exceeded)")
        if len(new) > 30:
            print(f"  ... and {len(new) - 30} more")
        print("\nFAIL: unexpected live/backtest indicator divergence found.")
        return 1

    print("\nPASS: no unexpected divergence.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", default=DEFAULT_PARITY_END)
    parser.add_argument("--symbols", type=int, default=15)
    parser.add_argument("--dates", type=int, default=15)
    args = parser.parse_args()
    sys.exit(run_parity_check(args.end, args.symbols, args.dates))
