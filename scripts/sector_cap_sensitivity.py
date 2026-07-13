"""
Tests whether capping the largest sector's share of the universe changes
reported performance (docs/24_Rejected_Forever.md "Universe" section follow-up).

config/watchlist_nse.py's WATCHLIST is 24% Financial Services (24/100 symbols) --
the largest single sector by a wide margin (next is Automobile at 14%). This
script caps Financial Services at 15% (15 symbols) by removing 9, and re-runs
the same FULL/TRAIN/TEST windows out_of_sample_validator.py uses, once with
the current 100-symbol universe and once with the 91-symbol capped universe.

Which 9 to remove is itself a design choice that can bias the result -- the
watchlist's own history includes removals based on retrospective P&L
(config/watchlist_nse.py's comments), which this script deliberately avoids
repeating. Selection here is a seeded random sample (seed=42, matching this
codebase's ENTRY_MODE_SEED convention) of which 15 of 24 to KEEP, not a
performance-informed choice.
"""
import os
import sys
import random
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.watchlist_nse import ALL_SYMBOLS, WATCHLIST
from config.settings import INITIAL_CAPITAL, MARKET_INDEX_SYMBOL, MAX_OPEN_POSITIONS
from data.fetcher import fetch_all, fetch_index
from backtest.engine import BacktestEngine
from backtest.metrics import calculate_metrics
from db.repository import init_db

SECTOR_CAP_PCT = 0.15  # no sector may exceed 15% of the universe

WINDOWS = {
    "TRAIN": ("2022-01-01", "2024-12-31"),
    "TEST":  ("2025-01-01", str(date.today())),
    "FULL":  ("2022-01-01", str(date.today())),
}


def build_capped_universe(seed: int = 42) -> list:
    cap_n = int(len(ALL_SYMBOLS) * SECTOR_CAP_PCT)
    by_sector = {}
    for sym, sector, _ in WATCHLIST:
        by_sector.setdefault(sector, []).append(sym)

    rng = random.Random(seed)
    capped = []
    for sector, syms in by_sector.items():
        if len(syms) > cap_n:
            capped.extend(sorted(rng.sample(syms, cap_n)))
        else:
            capped.extend(syms)
    return sorted(capped)


def run(symbols, start_s: str, end_s: str) -> dict:
    start = datetime.strptime(start_s, "%Y-%m-%d").date()
    end = datetime.strptime(end_s, "%Y-%m-%d").date()
    lookback = (end - start).days + 60
    warmup_start = start - timedelta(days=500)

    data = fetch_all(symbols, lookback_days=lookback, start=warmup_start, end=end)
    index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=lookback, start=warmup_start, end=end)
    if not index_df.empty:
        data[MARKET_INDEX_SYMBOL] = index_df

    engine = BacktestEngine(
        data, start, end, INITIAL_CAPITAL,
        slippage_model="fixed_pct", max_selected=MAX_OPEN_POSITIONS,
        fund_injections={},
    )
    result = engine.run()
    return calculate_metrics(result, INITIAL_CAPITAL)


def fmt(m: dict) -> str:
    return (f"CAGR {m['cagr_pct']:+.2f}%  Sharpe {m['sharpe_ratio']:.2f}  "
            f"MDD {m['max_drawdown_pct']:.2f}%  WR {m['win_rate_pct']:.1f}%  "
            f"PF {m['profit_factor']:.2f}  N={m.get('total_trades', m.get('trades', '?'))}")


def main():
    init_db()
    capped_universe = build_capped_universe()
    removed = sorted(set(ALL_SYMBOLS) - set(capped_universe))
    print(f"Baseline universe: {len(ALL_SYMBOLS)} symbols (current config/watchlist_nse.py)")
    print(f"Capped universe:   {len(capped_universe)} symbols (Financial Services capped at "
          f"{int(SECTOR_CAP_PCT*100)}%, {len(removed)} removed)\n")

    rows = []
    for label, (start_s, end_s) in WINDOWS.items():
        print(f"--- {label}: {start_s} -> {end_s} ---")
        baseline_m = run(ALL_SYMBOLS, start_s, end_s)
        print(f"  baseline ({len(ALL_SYMBOLS)}): {fmt(baseline_m)}")
        capped_m = run(capped_universe, start_s, end_s)
        print(f"  capped   ({len(capped_universe)}): {fmt(capped_m)}")
        rows.append((label, baseline_m, capped_m))
        print()

    print("=== Summary: CAGR delta (capped - baseline) ===")
    for label, b, c in rows:
        delta = c["cagr_pct"] - b["cagr_pct"]
        print(f"  {label:<6} baseline={b['cagr_pct']:+.2f}%  capped={c['cagr_pct']:+.2f}%  delta={delta:+.2f}pp")

    return 0


if __name__ == "__main__":
    sys.exit(main())
