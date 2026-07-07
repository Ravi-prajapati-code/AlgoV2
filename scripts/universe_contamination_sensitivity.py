"""
Quantifies the scope of the confirmed universe survivorship bias (docs/13_Independent_Institutional_Review.md
§2/§4.3/§10, docs/14_Universe_Verification_Report.md).

config/watchlist_nse.py's comments document 33 symbols removed from the universe using retrospective
trade-level P&L/win-rate statistics (the "Governance risks & confirmed losers" cleanup + the
2026-06-17 "Quality revision"). Every historical backtest to date has applied the POST-removal
(100-symbol) list to every date, including dates before the removal decisions were ever made — this
mechanically excludes known losers from the entire evaluation history, inflating every reported
metric by an unknown amount.

This script restores those 33 symbols (all have cached OHLCV data back to 2021-12-02, confirmed via
db.repository.earliest_cached_date) and re-runs the same FULL/TRAIN/TEST windows
out_of_sample_validator.py uses, once with the current 100-symbol universe and once with the
133-symbol restored universe, to directly measure the effect of the removal on reported performance.

This does not resolve the look-ahead problem (see docs/14) — it measures one specific, documented
instance of it as a lower-bound estimate of the bias's magnitude.
"""
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.watchlist_nse import ALL_SYMBOLS
from config.settings import INITIAL_CAPITAL, MARKET_INDEX_SYMBOL, MAX_OPEN_POSITIONS
from data.fetcher import fetch_all, fetch_index
from backtest.engine import BacktestEngine
from backtest.metrics import calculate_metrics
from db.repository import init_db

REMOVED_GOVERNANCE_LOSERS = ["IIFL", "RECLTD", "BIOCON", "DEEPAKNTR", "NAVINFLUOR"]
REMOVED_QUALITY_REVISION = [
    "INDUSTOWER", "BHARTIARTL", "BEL", "KEI", "SOLARINDS", "SCHAEFFLER", "ETERNAL",
    "PERSISTENT", "LUPIN", "HEROMOTOCO", "TIINDIA", "ZYDUSLIFE", "ESCORTS", "SUPREMEIND",
    "FORTIS", "JBCHEPHARM", "LAURUSLABS", "THERMAX", "COFORGE", "DIXON",
]
REMOVED_CONSUMER_SERVICES = ["INDHOTEL", "IRCTC", "DEVYANI", "JUBLFOOD", "NAUKRI"]
REMOVED_REALTY = ["GODREJPROP", "PHOENIXLTD", "PRESTIGE"]

RESTORED_SYMBOLS = [
    s + ".NS" for s in
    REMOVED_GOVERNANCE_LOSERS + REMOVED_QUALITY_REVISION + REMOVED_CONSUMER_SERVICES + REMOVED_REALTY
]

WINDOWS = {
    "TRAIN": ("2022-01-01", "2024-12-31"),
    "TEST":  ("2025-01-01", str(date.today())),
    "FULL":  ("2022-01-01", str(date.today())),
}


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
    restored_universe = ALL_SYMBOLS + RESTORED_SYMBOLS
    print(f"Baseline universe: {len(ALL_SYMBOLS)} symbols (current config/watchlist_nse.py)")
    print(f"Restored universe: {len(restored_universe)} symbols "
          f"(+{len(RESTORED_SYMBOLS)} removed-using-hindsight names added back)\n")

    rows = []
    for label, (start_s, end_s) in WINDOWS.items():
        print(f"--- {label}: {start_s} -> {end_s} ---")
        baseline_m = run(ALL_SYMBOLS, start_s, end_s)
        print(f"  baseline (100): {fmt(baseline_m)}")
        restored_m = run(restored_universe, start_s, end_s)
        print(f"  restored (133): {fmt(restored_m)}")
        rows.append((label, baseline_m, restored_m))
        print()

    print("=== Summary: CAGR delta (restored - baseline) ===")
    for label, b, r in rows:
        delta = r["cagr_pct"] - b["cagr_pct"]
        print(f"  {label:<6} baseline={b['cagr_pct']:+.2f}%  restored={r['cagr_pct']:+.2f}%  delta={delta:+.2f}pp")

    return 0


if __name__ == "__main__":
    sys.exit(main())
