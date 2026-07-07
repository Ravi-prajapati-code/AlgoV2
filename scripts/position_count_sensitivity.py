"""
Tests the concentration-risk hypothesis from docs/16_Benchmark_Attribution.md: a small positive
Jensen's alpha (beta/systematic-risk adjusted) coexists with worse Sharpe/Sortino/Calmar (total-
volatility adjusted) than the Midcap 150 ETF benchmark. One plausible cause: holding only
MAX_OPEN_POSITIONS=6 concentrates idiosyncratic risk. If that's the mechanism, raising the position
count (spreading the same capital across more names, same signal/ranking logic) should improve
Sharpe/Sortino/Calmar without destroying CAGR.

Important note on how this had to be built: BacktestEngine's constructor takes a `max_selected`
parameter, but it is dead code — assigned to self.max_selected in __init__ and never read again.
The actual position-slot limit is the module-level MAX_OPEN_POSITIONS name imported directly into
backtest/engine.py from config.settings at import time, referenced directly (not via self.) at the
open-position-slot and position-sizing (cash / available_slots) call sites. To vary position count
across runs in one process, this script monkeypatches backtest.engine.MAX_OPEN_POSITIONS before each
run, since that's the actual live binding the sizing code reads.

Universe/data/signals are held constant across all runs — fetched once, reused for every position
count — so any Sharpe/Sortino/Calmar change is attributable to position count alone.
"""
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest.engine as engine_module
from config.watchlist_nse import ALL_SYMBOLS
from config.settings import INITIAL_CAPITAL, MARKET_INDEX_SYMBOL
from data.fetcher import fetch_all, fetch_index, fetch_symbol
from backtest.engine import BacktestEngine
from scripts.benchmark_attribution import (
    sharpe_ratio, sortino_ratio, max_drawdown_pct, calmar_ratio, cagr_from_returns,
)

START = "2022-01-01"
END = str(date.today())
POSITION_COUNTS = [3]


def run_at(n: int, data: dict, start: date, end: date) -> pd.Series:
    original = engine_module.MAX_OPEN_POSITIONS
    engine_module.MAX_OPEN_POSITIONS = n
    try:
        engine = BacktestEngine(
            data, start, end, INITIAL_CAPITAL,
            slippage_model="fixed_pct", fund_injections={},
        )
        result = engine.run()
    finally:
        engine_module.MAX_OPEN_POSITIONS = original
    equity = pd.Series(result.equity_curve).sort_index()
    equity.index = pd.to_datetime(equity.index).normalize()
    return equity.pct_change().dropna(), len(result.trades)


def main():
    start = datetime.strptime(START, "%Y-%m-%d").date()
    end = datetime.strptime(END, "%Y-%m-%d").date()
    warmup_start = start - timedelta(days=500)
    lookback = (end - start).days + 60

    print(f"Confirming default MAX_OPEN_POSITIONS = {engine_module.MAX_OPEN_POSITIONS} before override\n")

    print("Fetching universe data once, reused across all position counts...")
    data = fetch_all(ALL_SYMBOLS, lookback_days=lookback, start=warmup_start, end=end)
    index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=lookback, start=warmup_start, end=end)
    if not index_df.empty:
        data[MARKET_INDEX_SYMBOL] = index_df

    print("Fetching Midcap 150 ETF proxy benchmark...")
    midcap_df = fetch_symbol("MIDCAPETF.NS", start=start - timedelta(days=30), end=end)
    midcap_df.index = pd.to_datetime(midcap_df.index).normalize()
    midcap_ret = midcap_df["close"].sort_index().pct_change().dropna()

    bench_cagr = cagr_from_returns(midcap_ret)
    bench_mdd = max_drawdown_pct(midcap_ret)
    bench_sharpe = sharpe_ratio(midcap_ret)
    bench_sortino = sortino_ratio(midcap_ret)
    bench_calmar = calmar_ratio(bench_cagr, bench_mdd)
    print(f"Benchmark (Midcap150 ETF): CAGR={bench_cagr:+.2f}%  MDD={bench_mdd:.2f}%  "
          f"Sharpe={bench_sharpe:.2f}  Sortino={bench_sortino:.2f}  Calmar={bench_calmar:.2f}\n")

    print(f"Running backtest at position counts: {POSITION_COUNTS}\n")
    for n in POSITION_COUNTS:
        strat_ret, n_trades = run_at(n, data, start, end)
        aligned = pd.concat([strat_ret, midcap_ret], axis=1, join="inner").dropna()
        aligned.columns = ["strat", "bench"]
        s = aligned["strat"]

        cagr = cagr_from_returns(s)
        mdd = max_drawdown_pct(s)
        sharpe = sharpe_ratio(s)
        sortino = sortino_ratio(s)
        calmar = calmar_ratio(cagr, mdd)

        print(f"  N={n:>2}  trades={n_trades:>4}  CAGR={cagr:+7.2f}%  MDD={mdd:7.2f}%  "
              f"Sharpe={sharpe:5.2f}  Sortino={sortino:5.2f}  Calmar={calmar:5.2f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
