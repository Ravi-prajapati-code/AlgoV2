"""
Tests docs/16_Benchmark_Attribution.md's open hypothesis: does the strategy's
excess return over its benchmark correlate with cross-sectional stock-return
dispersion, or is the TEST-window edge just calendar recency?

Reuses scripts/benchmark_attribution.py's backtest/benchmark plumbing (one
continuous 2022-01-01->today equity curve, same data this project's docs/16
numbers already come from) and the dispersion formula already validated in
scripts/signal_regime_diagnostics.py:104 (mean daily cross-sectional std of
stock returns). No new backtest path, no new dispersion formula.

Windows are non-overlapping calendar quarters, not signal_regime_diagnostics.py's
yearly-stepped-3-months windows -- overlapping windows pseudo-replicate and
would inflate any correlation found here.

Benchmark is Nifty Midcap 150 (MIDCAPETF.NS), per docs/16.5_Investment_Mandate.md's
conclusion that it -- not Nifty 50 -- is the correct comparison for this
mid-cap RS-momentum strategy.
"""
import sys
from datetime import date

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr

sys.path.insert(0, "/home/ravi.prajapati@brainvire.com/Workspace/AlgoV2")

from scripts.benchmark_attribution import (
    run_backtest_equity_curve,
    to_daily_returns,
    fetch_benchmark_returns,
    cagr_from_returns,
)
from config.watchlist_nse import ALL_SYMBOLS

MIDCAP_SYMBOL = "MIDCAPETF.NS"
MIN_WINDOW_DAYS = 40  # roughly a bare minimum for a CAGR/dispersion estimate to mean anything


def quarterly_windows(start: date, end: date):
    windows = []
    cur = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    while cur < end_ts:
        nxt = min(cur + pd.DateOffset(months=3), end_ts)
        windows.append((cur, nxt))
        cur = nxt
    return windows


def main():
    print("[dispersion_edge_test] running one continuous backtest for the equity curve...", file=sys.stderr)
    equity, data = run_backtest_equity_curve()
    strat_ret = to_daily_returns(equity)

    midcap_ret = fetch_benchmark_returns(MIDCAP_SYMBOL)

    close_wide = pd.DataFrame({
        symbol: data[symbol]["close"] for symbol in ALL_SYMBOLS
        if symbol in data and not data[symbol].empty
    }).sort_index()
    close_wide.index = pd.to_datetime(close_wide.index).normalize()
    wret = close_wide.pct_change()

    start = strat_ret.index.min().date()
    end = strat_ret.index.max().date()
    windows = quarterly_windows(start, end)

    rows = []
    for i, (ws, we) in enumerate(windows):
        s_win = strat_ret.loc[ws:we]
        m_win = midcap_ret.loc[ws:we]
        d_win = wret.loc[ws:we]
        if len(s_win) < MIN_WINDOW_DAYS or len(m_win) < MIN_WINDOW_DAYS:
            continue
        strat_cagr = cagr_from_returns(s_win)
        midcap_cagr = cagr_from_returns(m_win)
        excess = strat_cagr - midcap_cagr
        dispersion = d_win.std(axis=1, skipna=True).mean() * 100
        rows.append({
            "window_idx": i,
            "start": ws.date(),
            "end": we.date(),
            "strat_cagr_pct": strat_cagr,
            "midcap_cagr_pct": midcap_cagr,
            "excess_cagr_pct": excess,
            "dispersion_pct": dispersion,
        })

    result = pd.DataFrame(rows)
    if len(result) < 5:
        print(f"[dispersion_edge_test] only {len(result)} usable windows, too few to correlate -- aborting.", file=sys.stderr)
        sys.exit(1)

    print("\n=== Quarterly windows: excess CAGR vs dispersion ===")
    print(result[["start", "end", "strat_cagr_pct", "midcap_cagr_pct", "excess_cagr_pct", "dispersion_pct"]]
          .to_string(index=False, float_format=lambda x: f"{x:6.2f}"))

    disp_r, disp_p = pearsonr(result["dispersion_pct"], result["excess_cagr_pct"])
    disp_rho, disp_rho_p = spearmanr(result["dispersion_pct"], result["excess_cagr_pct"])
    time_r, time_p = pearsonr(result["window_idx"], result["excess_cagr_pct"])
    time_rho, time_rho_p = spearmanr(result["window_idx"], result["excess_cagr_pct"])

    print("\n=== Correlation: excess CAGR vs dispersion ===")
    print(f"  Pearson  r={disp_r:+.3f}  p={disp_p:.3f}")
    print(f"  Spearman rho={disp_rho:+.3f}  p={disp_rho_p:.3f}")

    print("\n=== Confound check: excess CAGR vs plain recency (window index) ===")
    print(f"  Pearson  r={time_r:+.3f}  p={time_p:.3f}")
    print(f"  Spearman rho={time_rho:+.3f}  p={time_rho_p:.3f}")

    print(f"\n[dispersion_edge_test] N={len(result)} windows, {result['start'].min()} -> {result['end'].max()}", file=sys.stderr)

    out_path = "outputs/dispersion_edge_test.csv"
    result.to_csv(out_path, index=False)
    print(f"[dispersion_edge_test] wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
