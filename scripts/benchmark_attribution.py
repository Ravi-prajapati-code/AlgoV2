"""
Investment Committee Gate 1 — "Is there any alpha?" (docs/16_Benchmark_Attribution.md).

A CAGR number in isolation doesn't answer the question. This computes CAPM alpha, beta,
information ratio, tracking error, Jensen's alpha, up/down capture, and excess return against
three benchmarks:
  - Nifty 50 (MARKET_INDEX_SYMBOL — already used internally for regime detection, full 2022+ history)
  - Nifty Midcap 150 proxy (MIDCAPETF.NS, a tradeable ETF — used instead of the raw index because
    the Gate 1 question is "what would a PASSIVE INVESTOR have earned," which an ETF answers more
    directly than a raw index level, tracking error and expense drag included; full 2022+ history)
  - Nifty 500 proxy (MONIFTY500.NS, a tradeable ETF) — only has price history from 2023-10-06
    onward, so this comparison is restricted to that sub-window and reported separately
  - Equal-weight universe (synthetic: average daily return of every symbol in ALL_SYMBOLS that has
    data that day) — NOTE this benchmark inherits the exact same universe-selection caveat as the
    strategy itself (docs/14_Universe_Verification_Report.md); it is not an independent, bias-free
    benchmark the way the three external ones are

Caveat inherited from docs/14: strategy returns here come from the current 100-symbol universe
applied to all historical dates, which is confirmed-unvalidated pre-2026-07-06 evidence. Every
number this script produces should be read as "based on the existing (unvalidated) evidence base,"
not as a final word on whether real alpha exists.

Risk-free rate: no risk-free series exists anywhere in this project. Uses a flat 6.5%/year
assumption (rough India 91-day T-bill/repo-rate proxy) — this is a stated assumption, not fetched
data, and materially affects Jensen's alpha; treat the alpha number as sensitive to this choice.
"""
import os
import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.watchlist_nse import ALL_SYMBOLS
from config.settings import INITIAL_CAPITAL, MARKET_INDEX_SYMBOL, MAX_OPEN_POSITIONS
from data.fetcher import fetch_all, fetch_index, fetch_symbol
from backtest.engine import BacktestEngine

RISK_FREE_RATE_ANNUAL = 0.065
TRADING_DAYS = 252

START = "2022-01-01"
END = str(date.today())


def run_backtest_equity_curve() -> pd.Series:
    start = datetime.strptime(START, "%Y-%m-%d").date()
    end = datetime.strptime(END, "%Y-%m-%d").date()
    warmup_start = start - timedelta(days=500)
    lookback = (end - start).days + 60

    data = fetch_all(ALL_SYMBOLS, lookback_days=lookback, start=warmup_start, end=end)
    index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=lookback, start=warmup_start, end=end)
    if not index_df.empty:
        data[MARKET_INDEX_SYMBOL] = index_df

    engine = BacktestEngine(
        data, start, end, INITIAL_CAPITAL,
        slippage_model="fixed_pct", max_selected=MAX_OPEN_POSITIONS,
        fund_injections={},
    )
    result = engine.run()
    s = pd.Series(result.equity_curve).sort_index()
    s.index = pd.to_datetime(s.index).normalize()
    return s, data


def to_daily_returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def build_equal_weight_benchmark(data: dict) -> pd.Series:
    closes = {}
    for sym in ALL_SYMBOLS:
        df = data.get(sym)
        if df is not None and not df.empty and "close" in df.columns:
            closes[sym] = df["close"]
    panel = pd.DataFrame(closes)
    panel.index = pd.to_datetime(panel.index).normalize()
    panel = panel.sort_index()
    daily_ret = panel.pct_change()
    ew_daily_ret = daily_ret.mean(axis=1, skipna=True).dropna()
    return ew_daily_ret


def fetch_benchmark_returns(symbol: str) -> pd.Series:
    start = datetime.strptime(START, "%Y-%m-%d").date()
    end = datetime.strptime(END, "%Y-%m-%d").date()
    df = fetch_symbol(symbol, start=start - timedelta(days=30), end=end)
    if df.empty:
        return pd.Series(dtype=float)
    df.index = pd.to_datetime(df.index).normalize()
    return df["close"].sort_index().pct_change().dropna()


def cagr_from_returns(daily_ret: pd.Series) -> float:
    n_years = len(daily_ret) / TRADING_DAYS
    if n_years <= 0:
        return 0.0
    total_growth = (1 + daily_ret).prod()
    return (total_growth ** (1 / n_years) - 1) * 100


def max_drawdown_pct(daily_ret: pd.Series) -> float:
    equity = (1 + daily_ret).cumprod()
    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    return drawdown.min() * 100


def sharpe_ratio(daily_ret: pd.Series) -> float:
    rf_daily = RISK_FREE_RATE_ANNUAL / TRADING_DAYS
    excess = daily_ret - rf_daily
    return (excess.mean() / excess.std()) * np.sqrt(TRADING_DAYS) if excess.std() != 0 else float("nan")


def sortino_ratio(daily_ret: pd.Series) -> float:
    rf_daily = RISK_FREE_RATE_ANNUAL / TRADING_DAYS
    excess = daily_ret - rf_daily
    downside = excess[excess < 0]
    downside_std = downside.std()
    return (excess.mean() / downside_std) * np.sqrt(TRADING_DAYS) if downside_std not in (0, None) and not pd.isna(downside_std) else float("nan")


def calmar_ratio(cagr_pct: float, mdd_pct: float) -> float:
    return cagr_pct / abs(mdd_pct) if mdd_pct != 0 else float("nan")


def compute_attribution(strat_ret: pd.Series, bench_ret: pd.Series, label: str) -> dict:
    aligned = pd.concat([strat_ret, bench_ret], axis=1, join="inner").dropna()
    aligned.columns = ["strat", "bench"]
    if len(aligned) < 30:
        return {"label": label, "n_days": len(aligned), "insufficient_data": True}

    rf_daily = RISK_FREE_RATE_ANNUAL / TRADING_DAYS

    strat_excess = aligned["strat"] - rf_daily
    bench_excess = aligned["bench"] - rf_daily

    cov = np.cov(strat_excess, bench_excess)
    beta = cov[0, 1] / cov[1, 1] if cov[1, 1] != 0 else float("nan")
    alpha_daily = strat_excess.mean() - beta * bench_excess.mean()
    jensen_alpha_annual = alpha_daily * TRADING_DAYS * 100

    active_ret = aligned["strat"] - aligned["bench"]
    tracking_error_annual = active_ret.std() * np.sqrt(TRADING_DAYS) * 100
    information_ratio = (active_ret.mean() * TRADING_DAYS) / (active_ret.std() * np.sqrt(TRADING_DAYS)) \
        if active_ret.std() != 0 else float("nan")

    up_days = aligned["bench"] > 0
    down_days = aligned["bench"] < 0
    up_capture = (aligned.loc[up_days, "strat"].mean() / aligned.loc[up_days, "bench"].mean() * 100) \
        if up_days.sum() > 0 and aligned.loc[up_days, "bench"].mean() != 0 else float("nan")
    down_capture = (aligned.loc[down_days, "strat"].mean() / aligned.loc[down_days, "bench"].mean() * 100) \
        if down_days.sum() > 0 and aligned.loc[down_days, "bench"].mean() != 0 else float("nan")

    strat_cagr = cagr_from_returns(aligned["strat"])
    bench_cagr = cagr_from_returns(aligned["bench"])
    strat_mdd = max_drawdown_pct(aligned["strat"])
    bench_mdd = max_drawdown_pct(aligned["bench"])

    return {
        "label": label,
        "n_days": len(aligned),
        "date_range": f"{aligned.index.min().date()} -> {aligned.index.max().date()}",
        "strat_cagr_pct": strat_cagr,
        "bench_cagr_pct": bench_cagr,
        "excess_return_pct": strat_cagr - bench_cagr,
        "beta": beta,
        "jensen_alpha_annual_pct": jensen_alpha_annual,
        "tracking_error_annual_pct": tracking_error_annual,
        "information_ratio": information_ratio,
        "up_capture_pct": up_capture,
        "down_capture_pct": down_capture,
        "strat_mdd_pct": strat_mdd,
        "bench_mdd_pct": bench_mdd,
        "strat_sharpe": sharpe_ratio(aligned["strat"]),
        "bench_sharpe": sharpe_ratio(aligned["bench"]),
        "strat_sortino": sortino_ratio(aligned["strat"]),
        "bench_sortino": sortino_ratio(aligned["bench"]),
        "strat_calmar": calmar_ratio(strat_cagr, strat_mdd),
        "bench_calmar": calmar_ratio(bench_cagr, bench_mdd),
    }


def fmt(r: dict) -> str:
    if r.get("insufficient_data"):
        return f"{r['label']}: insufficient overlapping data ({r['n_days']} days)"
    return (
        f"{r['label']} [{r['date_range']}, n={r['n_days']}]\n"
        f"  Strategy CAGR   : {r['strat_cagr_pct']:+.2f}%   Benchmark CAGR  : {r['bench_cagr_pct']:+.2f}%   Excess: {r['excess_return_pct']:+.2f}pp\n"
        f"  Strategy MDD    : {r['strat_mdd_pct']:.2f}%   Benchmark MDD   : {r['bench_mdd_pct']:.2f}%\n"
        f"  Strategy Sharpe : {r['strat_sharpe']:.2f}   Benchmark Sharpe: {r['bench_sharpe']:.2f}\n"
        f"  Strategy Sortino: {r['strat_sortino']:.2f}   Benchmark Sortino: {r['bench_sortino']:.2f}\n"
        f"  Strategy Calmar : {r['strat_calmar']:.2f}   Benchmark Calmar: {r['bench_calmar']:.2f}\n"
        f"  Beta            : {r['beta']:.2f}\n"
        f"  Jensen's Alpha  : {r['jensen_alpha_annual_pct']:+.2f}%/yr\n"
        f"  Tracking Error  : {r['tracking_error_annual_pct']:.2f}%/yr\n"
        f"  Information Ratio: {r['information_ratio']:.2f}\n"
        f"  Up Capture      : {r['up_capture_pct']:.1f}%\n"
        f"  Down Capture    : {r['down_capture_pct']:.1f}%"
    )


def main():
    print(f"Running baseline backtest {START} -> {END} for equity curve...")
    equity, data = run_backtest_equity_curve()
    strat_ret = to_daily_returns(equity)
    print(f"Strategy daily returns: {len(strat_ret)} days, {equity.index.min().date()} -> {equity.index.max().date()}\n")

    print("Building equal-weight universe benchmark from strategy's own price data...")
    ew_ret = build_equal_weight_benchmark(data)

    print("Fetching Nifty 50...")
    nifty50_ret = fetch_benchmark_returns(MARKET_INDEX_SYMBOL)

    print("Fetching Nifty Midcap 150 proxy (MIDCAPETF.NS)...")
    midcap_ret = fetch_benchmark_returns("MIDCAPETF.NS")

    print("Fetching Nifty 500 proxy (MONIFTY500.NS)...\n")
    nifty500_ret = fetch_benchmark_returns("MONIFTY500.NS")

    results = [
        compute_attribution(strat_ret, nifty50_ret, "vs Nifty 50"),
        compute_attribution(strat_ret, midcap_ret, "vs Nifty Midcap 150 (MIDCAPETF proxy)"),
        compute_attribution(strat_ret, nifty500_ret, "vs Nifty 500 (MONIFTY500 proxy, restricted window)"),
        compute_attribution(strat_ret, ew_ret, "vs Equal-Weight Universe (same-universe caveat applies)"),
    ]

    for r in results:
        print(fmt(r))
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
