"""
Forward shadow ledger (docs/21 item 10, docs/22 "missing evidence" item 5).

Purpose: accumulate the ONLY evidence this project can no longer manufacture retroactively —
a point-in-time, append-only record of what the research configuration would do each day,
written BEFORE outcomes are known. Every historical dataset here is statistically spent
(docs/16.6 Issue 2); this ledger is the clean test.

Run daily after market close (add to cron alongside the existing jobs):
    python3 scripts/shadow_ledger.py

Appends one JSON line per day to outputs/shadow_ledger.jsonl containing: date, regime, every
qualified signal with its rs_rank/score, the top-N selection, and closing prices for later
scoring. Never overwrites; refuses to append twice for the same date. Scoring of past entries
(forward returns) is done read-only at analysis time, not here — this file records decisions,
not outcomes.
"""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LEDGER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "outputs", "shadow_ledger.jsonl")


def already_logged(today: str) -> bool:
    if not os.path.exists(LEDGER):
        return False
    with open(LEDGER) as f:
        return any(json.loads(line).get("date") == today for line in f if line.strip())


def main():
    today = str(date.today())
    if already_logged(today):
        print(f"shadow_ledger: {today} already recorded, skipping (append-only, no rewrites)")
        return 0

    # Log the PROVEN signal directly: the daily rs_rank cross-section (validated by the
    # permutation tests, docs/16.6) plus regime and closes. Deliberately does NOT replicate the
    # full live entry pipeline — the ranking is the asset whose forward performance needs clean
    # evidence; entry-filter mechanics are already observable in the live system's own ledger.
    from config.watchlist_nse import ALL_SYMBOLS
    from config.settings import MARKET_INDEX_SYMBOL
    from data.fetcher import fetch_all, fetch_index
    from strategy.regime import detect_regime
    from strategy.relative_strength import compute_rs_for_all

    data = fetch_all(ALL_SYMBOLS, lookback_days=400)
    index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=400)
    regime = detect_regime(index_df)
    rs = compute_rs_for_all(data, index_df)

    record = {
        "date": today,
        "regime": regime,
        "rs_ranks": {
            sym: {k: (round(float(v), 2) if isinstance(v, (int, float)) else v)
                  for k, v in m.items()}
            for sym, m in rs.items()
        },
        "closes": {
            sym: round(float(df["close"].iloc[-1]), 2)
            for sym, df in data.items() if len(df)
        },
    }

    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps(record) + "\n")
    print(f"shadow_ledger: recorded {today} — regime={regime}, "
          f"{len(record['rs_ranks'])} ranked symbols, {len(record['closes'])} closes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
