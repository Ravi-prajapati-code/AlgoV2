"""
Reporting/observability DB repository (docs/60). Physically separate DB
(db/reporting.db) from trading.db and momentum_atr.db -- this module never
imports db.repository or db.momentum_atr_repo for writes, only for the
snapshot script's read side. Nothing here is a source of truth for trading
decisions.
"""

import json
import os
import sqlite3
from typing import List, Optional

from config.settings import REPORTING_DB_PATH


def get_connection():
    conn = sqlite3.connect(REPORTING_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Run reporting_schema.sql to create all reporting tables."""
    conn = get_connection()
    schema_path = os.path.join(os.path.dirname(__file__), "reporting_schema.sql")
    with open(schema_path, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


# ── strategy_registry ────────────────────────────────────────────────────

def register_strategy(strategy_id: str, display_name: str, db_path: str,
                       status_label: str = "LIVE") -> None:
    """Idempotent seed -- INSERT OR IGNORE so a re-run never clobbers a
    manually-set status_label."""
    conn = get_connection()
    conn.execute(
        """INSERT OR IGNORE INTO strategy_registry
           (strategy_id, display_name, db_path, status_label)
           VALUES (?, ?, ?, ?)""",
        (strategy_id, display_name, db_path, status_label),
    )
    conn.commit()
    conn.close()


def set_strategy_status(strategy_id: str, status_label: str, set_by: str, ts: str) -> None:
    """Manual-only field -- never called from an automated snapshot cycle.
    status_label must never be auto-inferred/auto-promoted (docs/60 Page 17)."""
    conn = get_connection()
    conn.execute(
        """UPDATE strategy_registry
           SET status_label = ?, status_set_by = ?, status_set_at = ?
           WHERE strategy_id = ?""",
        (status_label, set_by, ts, strategy_id),
    )
    conn.commit()
    conn.close()


def load_strategy_registry() -> List[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM strategy_registry").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── broker_snapshot ──────────────────────────────────────────────────────

def save_broker_snapshot(ts: str, broker_cash: float, total_equity: float,
                          holdings: dict) -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO broker_snapshot (ts, broker_cash, total_equity, holdings_json)
           VALUES (?, ?, ?, ?)""",
        (ts, broker_cash, total_equity, json.dumps(holdings)),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def load_latest_broker_snapshot() -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM broker_snapshot ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    d["holdings"] = json.loads(d.pop("holdings_json"))
    return d


# ── strategy_capital_snapshot ────────────────────────────────────────────

def save_strategy_capital_snapshot(
    strategy_id: str, ts: str, strategy_invested_value: float,
    strategy_equity: float, source_note: str,
    strategy_allocated_cash: Optional[float] = None,
    strategy_available_cash: Optional[float] = None,
) -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO strategy_capital_snapshot
           (strategy_id, ts, strategy_allocated_cash, strategy_available_cash,
            strategy_invested_value, strategy_equity, source_note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (strategy_id, ts, strategy_allocated_cash, strategy_available_cash,
         strategy_invested_value, strategy_equity, source_note),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def load_latest_strategy_capital_snapshot(strategy_id: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        """SELECT * FROM strategy_capital_snapshot
           WHERE strategy_id = ? ORDER BY id DESC LIMIT 1""",
        (strategy_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── strategy_position_snapshot ───────────────────────────────────────────

def save_strategy_position_snapshot(ts: str, symbol: str, broker_qty: int,
                                     main_qty: int = 0, momentum_atr_qty: int = 0) -> int:
    collision = 1 if (main_qty + momentum_atr_qty) != broker_qty else 0
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO strategy_position_snapshot
           (ts, symbol, main_qty, momentum_atr_qty, broker_qty, collision_flag)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ts, symbol, main_qty, momentum_atr_qty, broker_qty, collision),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def load_position_snapshots_for_ts(ts: str) -> List[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM strategy_position_snapshot WHERE ts = ? ORDER BY symbol", (ts,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_latest_position_snapshot_ts() -> Optional[str]:
    conn = get_connection()
    row = conn.execute(
        "SELECT ts FROM strategy_position_snapshot ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row["ts"] if row else None


# ── strategy_reconciliation_log ──────────────────────────────────────────

def record_reconciliation(
    ts: str, check_name: str, result: str, strategy_id: Optional[str] = None,
    detail: str = "", auto_repaired: bool = False,
    repair_what: Optional[str] = None, repair_why: Optional[str] = None,
    repair_previous_value: Optional[str] = None, repair_new_value: Optional[str] = None,
    repair_source: Optional[str] = None,
) -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO strategy_reconciliation_log
           (ts, strategy_id, check_name, result, detail, auto_repaired,
            repair_what, repair_why, repair_previous_value, repair_new_value, repair_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ts, strategy_id, check_name, result, detail, int(auto_repaired),
         repair_what, repair_why, repair_previous_value, repair_new_value, repair_source),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def load_recent_reconciliation(limit: int = 50) -> List[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM strategy_reconciliation_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── strategy_alert ────────────────────────────────────────────────────────

def record_alert(ts: str, severity: str, category: str, message: str,
                  source_script: str, strategy_id: Optional[str] = None) -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO strategy_alert
           (ts, strategy_id, severity, category, message, source_script)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ts, strategy_id, severity, category, message, source_script),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def load_recent_alerts(limit: int = 50) -> List[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM strategy_alert ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def acknowledge_alert(alert_id: int) -> None:
    conn = get_connection()
    conn.execute("UPDATE strategy_alert SET acknowledged = 1 WHERE id = ?", (alert_id,))
    conn.commit()
    conn.close()


# ── strategy_run_log ──────────────────────────────────────────────────────

def record_run_log(ts: str, job_name: str, status: str,
                    strategy_id: Optional[str] = None, detail: str = "") -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO strategy_run_log (ts, strategy_id, job_name, status, detail)
           VALUES (?, ?, ?, ?, ?)""",
        (ts, strategy_id, job_name, status, detail),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def load_recent_run_log(limit: int = 50) -> List[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM strategy_run_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
