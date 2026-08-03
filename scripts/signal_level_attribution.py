#!/usr/bin/env python3
"""Signal-Level Entry Attribution: does FULL's trend/breakout gate reject
future winners or filter out future losers, relative to PURE_RS?

Unlike scripts/entry_attribution.py (which runs full backtests per ENTRY_MODE
and compares realized trade blotters), this operates at the symbol-day signal
level, BEFORE portfolio construction/MAX_OPEN_POSITIONS slot allocation. That
matters because executed trades under PURE_RS vs FULL are NOT a clean nested
subset of each other once slots are scarce — PURE_RS can spend a slot on a
name FULL would have rejected, at the cost of a name FULL would have bought,
confounding "signal quality" with "portfolio dynamics". This script instead
asks: of every symbol-day that clears the PURE_RS gate (RS rank + safety
checks), how does forward return differ between the ones that also clear
FULL's additional trend/breakout gate and the ones that don't? No backtest
run, no slot allocation — pure signal instrumentation via
BacktestEngine._precompute_all().

Known fidelity gap (confirmed by reading backtest/engine.py's indicator
precompute block): it never computes vcp_detected/vcp_pivot or ema_200, so
in-backtest the breakout gate always takes the "standard: within BREAKOUT_PCT
of 20d high" branch (VCP is live-only, untested here) and TREND_GATE_200 is
structurally a no-op regardless of its live setting. This script reports
breakout_pass as that standard-branch check only — do not read it as "VCP
entries are bad", VCP entries do not exist in this dataset at all.

Usage:
    python3 scripts/signal_level_attribution.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]

Output:
    outputs/signal_level_attribution.csv (one row per PURE_RS-eligible symbol-day)
    summary tables printed to stdout, split by TRAIN/TEST/FULL window
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import (
    INITIAL_CAPITAL, MARKET_INDEX_SYMBOL, MAX_OPEN_POSITIONS, OUTPUTS_DIR,
    RS_THRESHOLD, EXTENSION_CAP_PCT, MIN_DAILY_TURNOVER, ADX_TREND_THRESHOLD,
    MACD_CONFIRM_ENABLED, BREAKOUT_PCT, TREND_GATE_200_ENABLED,
)
from data.fetcher import fetch_all, fetch_index
from data.universe import get_all_symbols
from backtest.engine import BacktestEngine
from db.repository import init_db

TRAIN_END = pd.Timestamp("2024-12-31")
TEST_START = pd.Timestamp("2025-01-01")
FORWARD_HORIZONS = (5, 10, 20, 60)


def window_for(ts: pd.Timestamp) -> str:
    return "TRAIN" if ts <= TRAIN_END else ("TEST" if ts >= TEST_START else "GAP")


def _index_pos(df, ts):
    pos = df.index.searchsorted(ts)
    return pos if pos < len(df.index) else None


def fwd_return(df, ref_date, n_sessions, col="close"):
    pos = _index_pos(df, pd.Timestamp(ref_date))
    if pos is None:
        return None
    target = pos + n_sessions
    if target < 0 or target >= len(df.index):
        return None
    base = df[col].iloc[pos]
    if base <= 0:
        return None
    return round((df[col].iloc[target] - base) / base * 100, 2)


def evaluate_gates(ind: dict) -> dict:
    """Mirrors strategy/entry.py's check_entry gate logic, but records every
    sub-gate as a pass/fail flag instead of short-circuiting on first fail."""
    close = float(ind.get("close", 0))
    rs_rank = float(ind.get("rs_rank", 0) or 0)
    ema_50 = float(ind.get("ema_50", 0))
    ema_100 = float(ind.get("ema_100", 0))
    ema_entry_med = float(ind.get("ema_entry_med", ema_50) or ema_50)
    ema_entry_long = float(ind.get("ema_entry_long", ema_100) or ema_100)
    high_20d = float(ind.get("high_20d", 0))
    adx = float(ind.get("adx", 0))
    st_dir = ind.get("st_direction", -1)
    turnover = float(ind.get("turnover", 0))

    rs_pass = rs_rank >= RS_THRESHOLD

    extension = (close - ema_50) / ema_50 if ema_50 > 0 else 0.0
    ext_pass = not (ema_50 > 0 and extension > EXTENSION_CAP_PCT)

    turnover_pass = not (0 < turnover < MIN_DAILY_TURNOVER)

    vcp_detected = bool(ind.get("vcp_detected", False))
    vcp_pivot = float(ind.get("vcp_pivot", 0))
    if vcp_detected and vcp_pivot > 0:
        breakout_pass = close >= vcp_pivot * 0.98
    else:
        breakout_pass = close >= high_20d * (1 - BREAKOUT_PCT)

    ema_200 = float(ind.get("ema_200", 0))
    trend200_pass = not (TREND_GATE_200_ENABLED and ema_200 > 0 and close < ema_200)

    ema_align_pass = close > ema_entry_med > ema_entry_long
    supertrend_pass = st_dir in (1, "up")
    adx_pass = adx >= ADX_TREND_THRESHOLD
    macd_pass = (not MACD_CONFIRM_ENABLED) or bool(ind.get("macd_bullish", False))
    trend_strength_pass = ema_align_pass and supertrend_pass and adx_pass and macd_pass

    full_trend_pass = breakout_pass and trend200_pass and trend_strength_pass

    return {
        "rs_pass": rs_pass, "ext_pass": ext_pass, "turnover_pass": turnover_pass,
        "pure_rs_eligible": rs_pass and ext_pass and turnover_pass,
        "breakout_pass": breakout_pass, "trend200_pass": trend200_pass,
        "ema_align_pass": ema_align_pass, "supertrend_pass": supertrend_pass,
        "adx_pass": adx_pass, "macd_pass": macd_pass,
        "trend_strength_pass": trend_strength_pass,
        "full_trend_pass": full_trend_pass,
    }


def run(start_str: str, end_str: str):
    init_db()
    start = datetime.strptime(start_str, "%Y-%m-%d").date()
    end = datetime.strptime(end_str, "%Y-%m-%d").date()

    symbols = get_all_symbols()
    lookback = (end - start).days + 60
    warmup_start = start - timedelta(days=500)

    print(f"[SignalAttr] Fetching data ({start} -> {end})...")
    data = fetch_all(symbols, lookback_days=lookback, start=warmup_start, end=end)
    index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=lookback, start=warmup_start, end=end)
    if not index_df.empty:
        data[MARKET_INDEX_SYMBOL] = index_df

    print("[SignalAttr] Precomputing indicators (no backtest run — signal level only)...")
    engine = BacktestEngine(
        data, start, end, INITIAL_CAPITAL,
        slippage_model="fixed_pct", max_selected=MAX_OPEN_POSITIONS,
    )
    all_dates = engine._get_trading_dates()
    all_indicators, _, _ = engine._precompute_all(all_dates)

    rows = []
    for ts, symbol_indicators in all_indicators.items():
        win = window_for(ts)
        if win == "GAP":
            continue
        for symbol, ind in symbol_indicators.items():
            gates = evaluate_gates(ind)
            if not gates["pure_rs_eligible"]:
                continue  # only score the PURE_RS-admitted pool
            df = data.get(symbol)
            if df is None:
                continue
            row = {
                "date": ts.date(), "symbol": symbol, "window": win,
                "rs_rank": round(float(ind.get("rs_rank", 0) or 0), 1),
                "adx": ind.get("adx"),
                **gates,
            }
            for n in FORWARD_HORIZONS:
                row[f"fwd_{n}d_pct"] = fwd_return(df, ts, n)
            rows.append(row)

    df_out = pd.DataFrame(rows)
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUTS_DIR, "signal_level_attribution.csv")
    df_out.to_csv(out_path, index=False)
    print(f"[SignalAttr] Wrote {len(df_out)} PURE_RS-eligible symbol-day rows to {out_path}")
    return df_out


def print_split(df: pd.DataFrame, flag_col: str, label: str):
    print(f"\n=== {label}: full_trend_pass split by window ===")
    for win in ("TRAIN", "TEST"):
        sub = df[df["window"] == win]
        print(f"-- {win} (n={len(sub)}) --")
        for val, tag in ((True, "PASS (FULL would admit)"), (False, "FAIL (FULL rejects, PURE_RS admits)")):
            grp = sub[sub[flag_col] == val]
            if grp.empty:
                print(f"  {tag:<38} n=0")
                continue
            line = f"  {tag:<38} n={len(grp):<6}"
            for n in FORWARD_HORIZONS:
                col = f"fwd_{n}d_pct"
                vals = grp[col].dropna()
                if len(vals) == 0:
                    continue
                wr = (vals > 0).mean() * 100
                line += f" | {n}d: mean={vals.mean():+6.2f}% wr={wr:5.1f}% n={len(vals)}"
            print(line)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Signal-Level Entry Attribution")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2026-06-04")
    args = parser.parse_args()

    result_df = run(args.start, args.end)
    print_split(result_df, "full_trend_pass", "FULL trend gate (breakout+ema/st/adx/macd)")
    print_split(result_df, "breakout_pass", "Breakout-only sub-gate")
    print_split(result_df, "trend_strength_pass", "Trend-strength-only sub-gate (ema align+ST+ADX+MACD)")
