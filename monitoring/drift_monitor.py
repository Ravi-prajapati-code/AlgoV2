"""
Drift Monitor (B) — track signal expected price vs actual fill.

Answers: "Is the live system executing at the prices the strategy expects?"

A growing drift means:
  - Slippage is larger than the backtest assumption
  - Market impact is worsening (liquidity / size problems)
  - Execution timing is off

Usage
-----
    from monitoring.drift_monitor import DriftMonitor
    report = DriftMonitor().compute()
"""

import logging
import sqlite3
from datetime import date, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_db_path() -> str:
    from config.settings import DB_PATH
    return DB_PATH


def compute_drift() -> Dict:
    """
    Match signals (expected price) to trades (actual fill).
    Returns dict with slippage stats per action type and overall.
    """
    try:
        db = _get_db_path()
    except Exception:
        db = "db/trading.db"

    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row

        # Entry drift: signal BUY price → trade entry_price (last 60 trading days only)
        entry_rows = conn.execute("""
            SELECT s.date, s.symbol, s.price AS signal_price,
                   t.entry_price AS fill_price, 'ENTRY' AS side
            FROM signals s
            JOIN trades t ON t.symbol = s.symbol
                          AND t.entry_date = s.date
                          AND s.action = 'BUY'
            WHERE t.entry_price > 0 AND s.price > 0
              AND t.entry_date >= date('now', '-60 days')
        """).fetchall()

        # Exit drift: signal SELL price → trade exit_price (last 60 trading days only)
        exit_rows = conn.execute("""
            SELECT s.date, s.symbol, s.price AS signal_price,
                   t.exit_price AS fill_price, 'EXIT' AS side
            FROM signals s
            JOIN trades t ON t.symbol = s.symbol
                          AND t.exit_date = s.date
                          AND s.action = 'SELL'
            WHERE t.exit_price > 0 AND s.price > 0
              AND t.exit_date >= date('now', '-60 days')
        """).fetchall()

        conn.close()
    except Exception as e:
        logger.warning("[DriftMonitor] DB query failed: %s", e)
        return {"error": str(e), "entry_drift_pct": 0.0, "exit_drift_pct": 0.0, "pairs": 0}

    all_rows = list(entry_rows) + list(exit_rows)
    if not all_rows:
        return {
            "entry_drift_pct": 0.0,
            "exit_drift_pct":  0.0,
            "avg_drift_pct":   0.0,
            "pairs":           0,
            "rows":            [],
        }

    detail = []
    entry_drifts = []
    exit_drifts  = []

    for row in all_rows:
        sig_px  = float(row["signal_price"])
        fill_px = float(row["fill_price"])
        drift   = (fill_px - sig_px) / sig_px * 100  # +ve = bought/sold higher than expected

        detail.append({
            "date":         str(row["date"]),
            "symbol":       row["symbol"],
            "side":         row["side"],
            "signal_price": round(sig_px,  2),
            "fill_price":   round(fill_px, 2),
            "drift_pct":    round(drift,   3),
        })

        if row["side"] == "ENTRY":
            entry_drifts.append(drift)
        else:
            exit_drifts.append(drift)

    def _avg(lst):
        return round(sum(lst) / len(lst), 3) if lst else 0.0

    def _max_abs(lst):
        return round(max(abs(x) for x in lst), 3) if lst else 0.0

    return {
        "entry_drift_pct":     _avg(entry_drifts),    # +ve = buying above signal price
        "exit_drift_pct":      _avg(exit_drifts),     # -ve = selling below signal price
        "avg_drift_pct":       _avg([d["drift_pct"] for d in detail]),
        "max_drift_pct":       _max_abs([d["drift_pct"] for d in detail]),
        "entry_pairs":         len(entry_drifts),
        "exit_pairs":          len(exit_drifts),
        "pairs":               len(detail),
        "rows":                sorted(detail, key=lambda x: abs(x["drift_pct"]), reverse=True),
    }
