"""
Momentum x ATR standalone live strategy DB access (docs/57, docs/58) --
physically separate SQLite file from the live-trading DB
(config.settings.DB_PATH). Mirrors db/repository.py's
get_connection()/init_db() convention and db/research_repo.py's
physical-isolation precedent.
"""

import json
import os
import sqlite3
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd

from config.settings import MOMENTUM_ATR_DB_PATH, MOMENTUM_ATR_INITIAL_CAPITAL
from momentum_atr.models import Position, Trade, EquitySnapshot, StrategyState


def get_connection():
    conn = sqlite3.connect(MOMENTUM_ATR_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Run momentum_atr_schema.sql (idempotent) then seed the singleton
    state row if this is a fresh DB."""
    conn = get_connection()
    schema_path = os.path.join(os.path.dirname(__file__), "momentum_atr_schema.sql")
    with open(schema_path, "r") as f:
        conn.executescript(f.read())
    conn.execute(
        """INSERT OR IGNORE INTO state (id, cash, peak_equity, kill_switch_tripped)
           VALUES (1, ?, ?, 0)""",
        (MOMENTUM_ATR_INITIAL_CAPITAL, MOMENTUM_ATR_INITIAL_CAPITAL)
    )
    conn.commit()
    conn.close()
    print(f"[DB] momentum_atr initialized: {MOMENTUM_ATR_DB_PATH}")


# ── STATE (singleton) ────────────────────────────────────────────────────

def get_state() -> StrategyState:
    conn = get_connection()
    row = conn.execute("SELECT * FROM state WHERE id = 1").fetchone()
    conn.close()
    if not row:
        raise RuntimeError("momentum_atr state row missing -- call init_db() first")
    return StrategyState(
        cash=row["cash"], peak_equity=row["peak_equity"],
        kill_switch_tripped=bool(row["kill_switch_tripped"]),
        kill_switch_tripped_date=(
            pd.to_datetime(row["kill_switch_tripped_date"]).date()
            if row["kill_switch_tripped_date"] else None
        ),
    )


def update_state(cash: Optional[float] = None, peak_equity: Optional[float] = None,
                  kill_switch_tripped: Optional[bool] = None,
                  kill_switch_tripped_date: Optional[date] = None):
    """Partial update -- only columns explicitly passed (not None) change."""
    current = get_state()
    new_cash = current.cash if cash is None else cash
    new_peak = current.peak_equity if peak_equity is None else peak_equity
    new_tripped = current.kill_switch_tripped if kill_switch_tripped is None else kill_switch_tripped
    new_tripped_date = current.kill_switch_tripped_date if kill_switch_tripped_date is None else kill_switch_tripped_date
    conn = get_connection()
    conn.execute(
        """UPDATE state SET cash = ?, peak_equity = ?, kill_switch_tripped = ?,
           kill_switch_tripped_date = ? WHERE id = 1""",
        (new_cash, new_peak, int(new_tripped),
         new_tripped_date.strftime("%Y-%m-%d") if new_tripped_date else None)
    )
    conn.commit()
    conn.close()


# ── POSITIONS ─────────────────────────────────────────────────────────────

def _row_to_position(r) -> Position:
    return Position(
        symbol=r["symbol"], entry_date=pd.to_datetime(r["entry_date"]).date(),
        entry_price=r["entry_price"], shares=r["shares"], status=r["status"],
        entry_order_id=r["entry_order_id"] or "", id=r["id"]
    )


def save_position(pos: Position):
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO positions
           (symbol, entry_date, entry_price, shares, status, entry_order_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (pos.symbol, pos.entry_date.strftime("%Y-%m-%d"), pos.entry_price,
         pos.shares, pos.status, pos.entry_order_id)
    )
    conn.commit()
    conn.close()


def load_positions(status: str = "OPEN") -> List[Position]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM positions WHERE status = ?", (status,)).fetchall()
    conn.close()
    return [_row_to_position(r) for r in rows]


def get_position(symbol: str) -> Optional[Position]:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM positions WHERE symbol = ? AND status = 'OPEN'", (symbol,)
    ).fetchone()
    conn.close()
    return _row_to_position(row) if row else None


def close_position_and_save_trade(symbol: str, t: Trade):
    """Atomically close an OPEN position and record its trade -- mirrors
    db/repository.py's close_position_and_save_trade: either both commit
    or neither, so a process death mid-write can't leave a closed
    position with no trade record (or vice versa)."""
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE positions SET status = 'CLOSED' WHERE symbol = ? AND status = 'OPEN'",
                (symbol,)
            )
            conn.execute(
                """INSERT INTO trades
                   (symbol, entry_date, exit_date, entry_price, exit_price, shares,
                    gross_pnl, charges, net_pnl, exit_reason, exit_order_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (t.symbol, t.entry_date.strftime("%Y-%m-%d"),
                 t.exit_date.strftime("%Y-%m-%d") if t.exit_date else None,
                 t.entry_price, t.exit_price, t.shares, t.gross_pnl, t.charges,
                 t.net_pnl, t.exit_reason, t.exit_order_id)
            )
    finally:
        conn.close()


def load_trades() -> List[Trade]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM trades ORDER BY id DESC").fetchall()
    conn.close()
    trades = []
    for r in rows:
        trades.append(Trade(
            symbol=r["symbol"], entry_date=pd.to_datetime(r["entry_date"]).date(),
            exit_date=pd.to_datetime(r["exit_date"]).date() if r["exit_date"] else None,
            entry_price=r["entry_price"], exit_price=r["exit_price"], shares=r["shares"],
            gross_pnl=r["gross_pnl"], charges=r["charges"], net_pnl=r["net_pnl"],
            exit_reason=r["exit_reason"], exit_order_id=r["exit_order_id"] or "", id=r["id"]
        ))
    return trades


# ── EQUITY SNAPSHOTS ──────────────────────────────────────────────────────

def snapshot_exists_for_date(d: date) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM equity_snapshots WHERE date = ?", (d.strftime("%Y-%m-%d"),)
    ).fetchone()
    conn.close()
    return row is not None


def save_equity_snapshot(s: EquitySnapshot):
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO equity_snapshots
           (date, cash, invested_value, total_equity, peak_equity, drawdown_pct, kill_switch_tripped)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (s.date.strftime("%Y-%m-%d"), s.cash, s.invested_value, s.total_equity,
         s.peak_equity, s.drawdown_pct, int(s.kill_switch_tripped))
    )
    conn.commit()
    conn.close()


def load_equity_snapshots(limit: int = 500) -> List[EquitySnapshot]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM equity_snapshots ORDER BY date DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    snaps = []
    for r in reversed(rows):
        snaps.append(EquitySnapshot(
            date=pd.to_datetime(r["date"]).date(), cash=r["cash"],
            invested_value=r["invested_value"], total_equity=r["total_equity"],
            peak_equity=r["peak_equity"], drawdown_pct=r["drawdown_pct"],
            kill_switch_tripped=bool(r["kill_switch_tripped"]), id=r["id"]
        ))
    return snaps


# ── DAILY RANKING (precompute/execution split) ──────────────────────────

def save_daily_ranking(d: date, ranked: List[str], closes: Dict[str, float]):
    """Written once per morning by the precompute cron -- the slow,
    universe-wide scoring pass -- so the 9:17 execution cron only reads
    (fast) instead of recomputing (slow, cache-bound, was blowing past
    9:17 by ~10min on cold cache)."""
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO daily_ranking
           (date, ranked_json, closes_json, scored_count, computed_at)
           VALUES (?, ?, ?, ?, ?)""",
        (d.strftime("%Y-%m-%d"), json.dumps(ranked), json.dumps(closes),
         len(closes), datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def load_daily_ranking(d: date) -> Optional[Tuple[List[str], Dict[str, float]]]:
    """Returns (ranked, closes) for `d`, or None if the precompute cron
    hasn't run yet for that date -- caller must abort, not fall back to a
    live compute (defeats the point of the split and risks the same
    cron-timeout the split exists to avoid)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT ranked_json, closes_json FROM daily_ranking WHERE date = ?",
        (d.strftime("%Y-%m-%d"),)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return json.loads(row["ranked_json"]), json.loads(row["closes_json"])


def load_recent_daily_rankings(limit: int = 2) -> List[Tuple[date, List[str]]]:
    """Returns up to `limit` most recent (date, ranked) pairs, newest first.
    Read-only helper for the dashboard's Signals page, which needs the
    latest ranking plus the prior one to compute rank-change deltas --
    load_daily_ranking() above requires an exact known date and can't do
    that alone."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT date, ranked_json FROM daily_ranking ORDER BY date DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [
        (datetime.strptime(r["date"], "%Y-%m-%d").date(), json.loads(r["ranked_json"]))
        for r in rows
    ]


# ── SLA CHECKPOINTS (pipeline-stage watchdog) ────────────────────────────

def record_sla_checkpoint(d: date, step: str, status: str, detail: str = "") -> None:
    """Written by precompute_momentum_atr_ranking.py (step='PRECOMPUTE') and
    run_momentum_atr_live.py (step='EXECUTION') at the end of every attempt
    -- success, abort, AND crash, not just the happy path -- so
    momentum_atr_sla_check.py can tell "ran and failed" apart from "never
    ran at all" (the flock-deadlock/silent-outage failure class this repo
    has already hit once on the main strategy)."""
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO sla_checkpoints (date, step, status, detail, ts)
           VALUES (?, ?, ?, ?, ?)""",
        (d.strftime("%Y-%m-%d"), step, status, detail, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def load_sla_checkpoints(d: date) -> Dict[str, dict]:
    """Returns {step: {status, detail, ts}} for every checkpoint recorded on
    `d`. A step key simply absent from the result means that stage never
    reported in at all today -- the case a plain log-exists check can miss."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT step, status, detail, ts FROM sla_checkpoints WHERE date = ?",
        (d.strftime("%Y-%m-%d"),)
    ).fetchall()
    conn.close()
    return {r["step"]: {"status": r["status"], "detail": r["detail"], "ts": r["ts"]} for r in rows}
