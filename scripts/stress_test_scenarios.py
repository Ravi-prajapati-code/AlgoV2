#!/usr/bin/env python3
"""
Strategy stress-test: feeds synthetic "future" market scenarios (crash,
bear grind, sideways chop, gap-down) through the real backtest engine to see
how the CURRENT live strategy config holds up outside the normal historical
window. Complements scripts/scenario_runner.py, which instead sweeps CONFIG
against fixed historical DATA — this sweeps DATA against fixed CONFIG.

How it works:
  1. For every universe symbol (+ the Nifty 50 index, so regime-switch logic
     actually reacts), the real cached OHLCV history is carried over
     UNCHANGED into a scratch DB, so EMA/regime warmup stays realistic.
  2. A synthetic continuation tail is appended after the last real close,
     built from a designed macro daily-return path (same shape for every
     symbol, so it's a genuine market-wide scenario) plus small per-symbol
     idiosyncratic noise (seeded, reproducible) so ranking/rotation still has
     something real to do.
  3. main.py backtest is run as a subprocess against ONLY the scratch DB
     (via DB_PATH_OVERRIDE — see config/settings.py) with UPSTOX_ACCESS_TOKEN
     stripped, so there is no way it can read or write db/trading.db or hit
     the live API.

Usage (from AlgoV2 root):
    python3 scripts/stress_test_scenarios.py
    python3 scripts/stress_test_scenarios.py --tail-days 150 --seed 7
"""
import argparse
import os
import re
import subprocess
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import MARKET_INDEX_SYMBOL
from data.universe import get_all_symbols
from strategy.defensive_portfolio import ALL_DEFENSIVE_SYMBOLS
from db import repository as repo

SCRATCH_DIR = "outputs/stress_test_scratch"

# Empirically calibrated from GOLDBEES.NS vs Nifty 50 real cached history
# (2017-2026, db/trading.db): daily-return correlation ~0.02 (effectively
# uncorrelated); on Nifty's worst 5% days gold averaged +0.08%/day vs
# Nifty's -2.46%/day. Before this fix, defensive symbols were fed the exact
# same crash macro as every equity, so synthetic "gold" crashed in lockstep
# with the market and tripped its own MAX_LOSS floor for reasons that never
# happen with real gold data — silently preventing every stress scenario
# from ever exercising a real hedge re-entry-during-drawdown scenario.
DEFENSIVE_DAILY_MEAN = 0.0008
DEFENSIVE_DAILY_STD = 0.0103

METRIC_RE = {
    "cagr":   re.compile(r"CAGR\s*:\s*([+-]?[\d.]+)%"),
    "sharpe": re.compile(r"Sharpe Ratio\s*:\s*([+-]?[\d.]+)"),
    "mdd":    re.compile(r"Max Drawdown\s*:\s*([\d.]+)%"),
    "wr":     re.compile(r"Win Rate\s*:\s*([\d.]+)%"),
    "pf":     re.compile(r"Profit Factor\s*:\s*([\d.]+)"),
}

# ── Scenario macro paths: list of (n_days, total_pct_change_over_segment) ──
SCENARIOS = {
    "crash_v_recovery": {
        "desc": "COVID-2020-style shock: sharp crash, brief bottom, snapback recovery",
        "segments": [(20, -0.32), (10, 0.0), (40, 0.47), (30, 0.05)],
        "noise_std": 0.020,
    },
    "extended_bear_grind": {
        "desc": "Slow grinding decline, no single trigger day (tests sustained-pressure regime handling)",
        "segments": [(150, -0.25)],
        "noise_std": 0.018,
    },
    "prolonged_sideways_chop": {
        "desc": "Range-bound whipsaw, near-flat net drift, elevated daily noise",
        "segments": [(150, 0.02)],
        "noise_std": 0.028,
    },
    "gap_down_bleed": {
        "desc": "Single overnight gap shock followed by continued bleed, then stabilization",
        "segments": [(1, -0.12), (60, -0.15), (30, 0.03)],
        "noise_std": 0.020,
    },
}


def macro_daily_returns(segments) -> np.ndarray:
    out = []
    for n_days, total_pct in segments:
        daily = (1.0 + total_pct) ** (1.0 / n_days) - 1.0
        out.extend([daily] * n_days)
    return np.array(out)


def synth_tail(last_close: float, last_vol: float, macro: np.ndarray, noise_std: float,
               rng: np.random.Generator, start_date: date) -> pd.DataFrame:
    """Build a synthetic OHLCV tail continuing from (last_close, last_vol)."""
    idio = rng.normal(0, noise_std, size=len(macro))
    daily_ret = macro + idio

    dates, opens, highs, lows, closes, vols = [], [], [], [], [], []
    d = start_date
    prev_close = last_close
    for r in daily_ret:
        while d.weekday() >= 5:  # skip weekends
            d += timedelta(days=1)
        overnight = r * rng.uniform(0.2, 0.5)
        o = prev_close * (1 + overnight)
        c = prev_close * (1 + r)
        intraday_range = abs(r) * 0.4 + 0.006
        h = max(o, c) * (1 + abs(rng.normal(0, intraday_range)))
        l = min(o, c) * (1 - abs(rng.normal(0, intraday_range)))
        vol_mult = 1.0 + min(abs(r) * 8, 3.0) * rng.uniform(0.7, 1.3)  # panic days trade heavier
        v = max(int(last_vol * vol_mult), 1000)

        dates.append(d); opens.append(o); highs.append(h); lows.append(l)
        closes.append(c); vols.append(v)
        prev_close = c
        d += timedelta(days=1)

    return pd.DataFrame({
        "date": dates, "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": vols,
    })


def load_real_history(symbols: list) -> dict:
    """Read all real cached history while DB_PATH still points at the real DB."""
    assert repo.DB_PATH == "db/trading.db", f"refusing to read history from {repo.DB_PATH}"
    history = {}
    for symbol in symbols:
        df = repo.load_ohlcv(symbol)
        if not df.empty:
            history[symbol] = df
    return history


def populate_scratch_db(scratch_path: str, history: dict, scenario: dict, rng: np.random.Generator):
    if os.path.exists(scratch_path):
        os.remove(scratch_path)
    repo.DB_PATH = scratch_path
    repo.init_db()

    macro = macro_daily_returns(scenario["segments"])
    tail_start = None
    tail_end = None

    for symbol, real in history.items():
        repo.save_ohlcv(symbol, real.reset_index())

        last_row = real.iloc[-1]
        last_date = real.index[-1].date() if hasattr(real.index[-1], "date") else real.index[-1]
        start = last_date + timedelta(days=1)

        sym_rng = np.random.default_rng(rng.integers(0, 2**32 - 1))
        if symbol in ALL_DEFENSIVE_SYMBOLS:
            # Safe-haven assets don't ride the equity crash macro — see
            # DEFENSIVE_DAILY_* constants above.
            sym_macro = np.full(len(macro), DEFENSIVE_DAILY_MEAN)
            sym_noise_std = DEFENSIVE_DAILY_STD
        else:
            sym_macro = macro
            sym_noise_std = scenario["noise_std"] * (0.3 if symbol == MARKET_INDEX_SYMBOL else 1.0)
        tail = synth_tail(float(last_row["close"]), float(last_row["volume"]),
                           sym_macro, sym_noise_std, sym_rng, start)
        repo.save_ohlcv(symbol, tail)

        if tail_start is None:
            tail_start, tail_end = tail["date"].iloc[0], tail["date"].iloc[-1]

    return tail_start, tail_end


def run_backtest(scratch_path: str, start: str, end: str) -> dict:
    env = os.environ.copy()
    env["DB_PATH_OVERRIDE"] = scratch_path
    env.pop("UPSTOX_ACCESS_TOKEN", None)  # guarantee no live-API fallback

    proc = subprocess.run(
        [sys.executable, "main.py", "backtest", "--start", start, "--end", end],
        env=env, capture_output=True, text=True, timeout=900,
    )
    out = proc.stdout + "\n" + proc.stderr
    row = {"returncode": proc.returncode}
    for key, pat in METRIC_RE.items():
        m = pat.search(out)
        row[key] = float(m.group(1)) if m else float("nan")
    if proc.returncode != 0 or all(pd.isna(row[k]) for k in METRIC_RE):
        row["error"] = out[-800:]
    return row


def print_table(rows: list):
    print(f"\n{'Scenario':<28} {'CAGR':>8} {'Sharpe':>7} {'MDD':>7} {'WR':>7} {'PF':>6}")
    print("-" * 62)
    for row in rows:
        if "error" in row:
            print(f"{row['name']:<28} FAILED — {row['error'][-120:]}")
            continue
        print(f"{row['name']:<28} {row['cagr']:>7.2f}% {row['sharpe']:>7.2f} "
              f"{row['mdd']:>6.2f}% {row['wr']:>6.2f}% {row['pf']:>6.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--only", nargs="*", choices=list(SCENARIOS), default=None)
    args = ap.parse_args()

    os.makedirs(SCRATCH_DIR, exist_ok=True)
    symbols = get_all_symbols() + [MARKET_INDEX_SYMBOL]
    names = args.only or list(SCENARIOS)

    print(f"Loading real cached history for {len(symbols)} symbols from {repo.DB_PATH} ...")
    history = load_real_history(symbols)
    print(f"  {len(history)}/{len(symbols)} symbols have cached history.")

    rows = []
    for name in names:
        scenario = SCENARIOS[name]
        print(f"\n[{name}] {scenario['desc']}")
        rng = np.random.default_rng(args.seed)
        scratch_path = os.path.join(SCRATCH_DIR, f"{name}.db")

        tail_start, tail_end = populate_scratch_db(scratch_path, history, scenario, rng)
        print(f"  Synthetic tail: {tail_start} -> {tail_end} ({len(macro_daily_returns(scenario['segments']))} sessions)")

        # Start the backtest ~90 real days before the shock hits, so the
        # strategy has already built a live portfolio (real entries, real
        # trailing stops) before the synthetic scenario begins — testing
        # "does an existing portfolio survive this", not "cold-start into it".
        warmup_start = tail_start - timedelta(days=90)
        row = run_backtest(scratch_path, str(warmup_start), str(tail_end))
        row["name"] = name
        rows.append(row)
        if "error" in row:
            print(f"  FAILED: {row['error']}")
        else:
            print(f"  CAGR {row['cagr']:.2f}% | Sharpe {row['sharpe']:.2f} | "
                  f"MDD {row['mdd']:.2f}% | WR {row['wr']:.2f}% | PF {row['pf']:.2f}")

    print_table(rows)


if __name__ == "__main__":
    main()
