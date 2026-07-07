"""
Selection-skill permutation test (docs/16.6 Issue 4/11).

Question: does the strategy's stock SELECTION add value beyond its market timing (regime gate),
trend filters, and exposure profile?

Design: the selection signal is rs_rank (cross-sectional RS percentile from compute_rs_for_all),
which drives the entry threshold (RS_THRESHOLD), buy ranking, bear-swing candidate ranking, and
rank-replacement — all read from the precomputed all_indicators[ts][symbol]["rs_rank"] values.

Each random path applies one FIXED symbol->symbol permutation to the rs_rank series: symbol A
carries symbol B's entire rs_rank history. This preserves (a) the exact cross-sectional rank
distribution every day, (b) temporal persistence of ranks (no artificial churn from iid daily
shuffling), and (c) the daily count of stocks clearing RS_THRESHOLD — while destroying only the
informational link between a stock's rank and its own price series. Everything else (regime gate,
trend/liquidity/ATR entry filters on the stock's own prices, sizing, exits, safe-haven logic) is
untouched. GOLDBEES.NS (safe haven) and the market index are excluded from the permutation so the
defensive leg is identical across all arms.

Interpretation: if the real strategy's CAGR/Sharpe sit in the upper tail (e.g. >90th percentile) of
the permuted distribution, the ranking contains genuine selection information. If it sits in the
middle, returns are attributable to timing/exposure/filters, not selection — corroborating the
equal-weight-universe result in docs/16.
"""
import argparse
import os
import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.watchlist_nse import ALL_SYMBOLS
from config.settings import INITIAL_CAPITAL, MARKET_INDEX_SYMBOL, SAFE_HAVEN_SYMBOL
from data.fetcher import fetch_all, fetch_index
from backtest.engine import BacktestEngine
from scripts.benchmark_attribution import (
    sharpe_ratio, max_drawdown_pct, cagr_from_returns,
)

START = "2022-01-01"
END = str(date.today())
DEFAULT_PATHS = 40


class PermutedRSEngine(BacktestEngine):
    """BacktestEngine with rs_rank series permuted across symbols (fixed map per run)."""

    perm_seed = None  # class attr set before construction; None = no permutation (baseline)

    def _precompute_all(self, all_dates):
        all_indicators, idx_conf, pullback_ok = super()._precompute_all(all_dates)
        if self.perm_seed is None:
            return all_indicators, idx_conf, pullback_ok

        rng = np.random.default_rng(self.perm_seed)
        excluded = {SAFE_HAVEN_SYMBOL, MARKET_INDEX_SYMBOL}
        symbols = sorted(
            {s for per_sym in all_indicators.values() for s in per_sym} - excluded
        )
        shuffled = list(symbols)
        rng.shuffle(shuffled)
        perm = dict(zip(symbols, shuffled))

        for per_sym in all_indicators.values():
            orig = {s: d["rs_rank"] for s, d in per_sym.items()}
            for s, d in per_sym.items():
                src = perm.get(s)
                if src is not None and src in orig:
                    d["rs_rank"] = orig[src]
        return all_indicators, idx_conf, pullback_ok


def run_path(seed, data, start, end):
    PermutedRSEngine.perm_seed = seed
    try:
        engine = PermutedRSEngine(
            data, start, end, INITIAL_CAPITAL,
            slippage_model="fixed_pct", fund_injections={},
        )
        result = engine.run()
    finally:
        PermutedRSEngine.perm_seed = None
    equity = pd.Series(result.equity_curve).sort_index()
    equity.index = pd.to_datetime(equity.index).normalize()
    ret = equity.pct_change().dropna()
    return {
        "seed": seed,
        "trades": len(result.trades),
        "cagr": cagr_from_returns(ret),
        "mdd": max_drawdown_pct(ret),
        "sharpe": sharpe_ratio(ret),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="RS-rank selection-skill permutation test")
    parser.add_argument(
        "--paths", type=int, default=DEFAULT_PATHS,
        help=f"Number of permuted paths (default: {DEFAULT_PATHS})",
    )
    parser.add_argument(
        "--output", default=None,
        help="Optional CSV path for per-path results (default: outputs/selection_skill_monte_carlo_<paths>.csv)",
    )
    parser.add_argument("--start", default=START, help="Backtest start date YYYY-MM-DD")
    parser.add_argument("--end", default=END, help="Backtest end date YYYY-MM-DD")
    return parser.parse_args()


def main():
    args = parse_args()
    n_paths = args.paths
    if n_paths < 1:
        print("ERROR: --paths must be >= 1", file=sys.stderr)
        return 1

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    print(f"Window: {start} -> {end}", flush=True)
    warmup_start = start - timedelta(days=500)
    lookback = (end - start).days + 60
    progress_every = 50 if n_paths > 100 else 1

    print("Fetching universe data once, reused across all paths...", flush=True)
    data = fetch_all(ALL_SYMBOLS, lookback_days=lookback, start=warmup_start, end=end)
    index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=lookback, start=warmup_start, end=end)
    if not index_df.empty:
        data[MARKET_INDEX_SYMBOL] = index_df

    print("Baseline (real rs_rank, no permutation):", flush=True)
    base = run_path(None, data, start, end)
    print(f"  ACTUAL   trades={base['trades']:>4}  CAGR={base['cagr']:+7.2f}%  "
          f"MDD={base['mdd']:7.2f}%  Sharpe={base['sharpe']:5.2f}", flush=True)

    results = []
    print(f"\nRunning {n_paths} permuted paths (progress every {progress_every}):", flush=True)
    for seed in range(n_paths):
        r = run_path(seed, data, start, end)
        results.append(r)
        if seed % progress_every == 0 or seed == n_paths - 1:
            print(f"  seed={seed:>4}  trades={r['trades']:>4}  CAGR={r['cagr']:+7.2f}%  "
                  f"MDD={r['mdd']:7.2f}%  Sharpe={r['sharpe']:5.2f}", flush=True)

    cagrs = np.array([r["cagr"] for r in results])
    sharpes = np.array([r["sharpe"] for r in results])

    out_path = args.output or os.path.join(
        "outputs", f"selection_skill_monte_carlo_{n_paths}.csv"
    )
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    pd.DataFrame([{**base, "arm": "actual"}] + [{**r, "arm": "permuted"} for r in results]).to_csv(
        out_path, index=False
    )
    print(f"\nWrote per-path results to {out_path}", flush=True)

    print("\n=== SUMMARY ===", flush=True)
    print(f"Permuted CAGR  : mean={cagrs.mean():+.2f}%  sd={cagrs.std(ddof=1):.2f}  "
          f"min={cagrs.min():+.2f}%  max={cagrs.max():+.2f}%")
    print(f"Permuted Sharpe: mean={sharpes.mean():.2f}  sd={sharpes.std(ddof=1):.2f}  "
          f"min={sharpes.min():.2f}  max={sharpes.max():.2f}")
    pct_cagr = (cagrs < base["cagr"]).mean() * 100
    pct_sharpe = (sharpes < base["sharpe"]).mean() * 100
    print(f"ACTUAL CAGR   {base['cagr']:+.2f}%  -> percentile {pct_cagr:.1f} of permuted distribution")
    print(f"ACTUAL Sharpe {base['sharpe']:.2f}   -> percentile {pct_sharpe:.1f} of permuted distribution")
    n_ge_cagr = int((cagrs >= base["cagr"]).sum())
    n_ge_sharpe = int((sharpes >= base["sharpe"]).sum())
    print(f"One-sided permutation p-value (CAGR)  : {(n_ge_cagr + 1) / (len(cagrs) + 1):.4f}")
    print(f"One-sided permutation p-value (Sharpe): {(n_ge_sharpe + 1) / (len(sharpes) + 1):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
