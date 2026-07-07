"""
Universe management DB layer.
All reads/writes for universe_candidates, universe_active, universe_history,
universe_ipo, universe_metrics tables.
"""
import sqlite3
import os
from datetime import date, datetime
from typing import List, Optional, Dict, Any

from config.settings import DB_PATH


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_universe_db():
    schema_path = os.path.join(os.path.dirname(__file__), "schema_universe.sql")
    with open(schema_path) as f:
        sql = f.read()
    conn = _conn()
    conn.executescript(sql)
    conn.commit()
    conn.close()


# ── CANDIDATES ────────────────────────────────────────────────────────────

def upsert_candidate(symbol: str, **kwargs):
    """Insert or update a candidate. kwargs map to column names."""
    conn = _conn()
    cols = ["symbol"] + list(kwargs.keys())
    placeholders = ", ".join("?" * len(cols))
    updates = ", ".join(f"{k}=excluded.{k}" for k in kwargs)
    vals = [symbol] + list(kwargs.values())
    conn.execute(
        f"INSERT INTO universe_candidates ({', '.join(cols)}) VALUES ({placeholders})"
        f" ON CONFLICT(symbol) DO UPDATE SET {updates}",
        vals,
    )
    conn.commit()
    conn.close()


def get_candidate(symbol: str) -> Optional[Dict]:
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM universe_candidates WHERE symbol = ?", (symbol,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_candidates_by_status(status: str) -> List[Dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM universe_candidates WHERE status = ? ORDER BY score_percentile DESC",
        (status,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_candidates() -> List[Dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM universe_candidates ORDER BY status, score_percentile DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_candidate_status(symbol: str, new_status: str, reason: str = "",
                             operator: str = "system"):
    conn = _conn()
    old = conn.execute(
        "SELECT status, composite_score FROM universe_candidates WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    if not old:
        conn.close()
        return
    conn.execute(
        "UPDATE universe_candidates SET status=?, last_reviewed=? WHERE symbol=?",
        (new_status, date.today().isoformat(), symbol),
    )
    conn.execute(
        "INSERT INTO universe_history (symbol, event, from_status, to_status, "
        "score_at_event, reason, operator) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (symbol, _event_name(old["status"], new_status), old["status"], new_status,
         old["composite_score"], reason, operator),
    )
    conn.commit()
    conn.close()


def _event_name(from_s: str, to_s: str) -> str:
    mapping = {
        ("watchlist", "core"):        "promoted_core",
        ("core",      "watchlist"):   "demoted_watchlist",
        ("watchlist", "removed"):     "removed",
        ("core",      "removed"):     "removed",
        ("removed",   "lockout"):     "lockout_applied",
        ("lockout",   "watchlist"):   "lockout_expired",
        ("ipo_watch", "watchlist"):   "ipo_qualified",
        ("ipo_watch", "rejected"):    "ipo_rejected",
    }
    return mapping.get((from_s, to_s), f"{from_s}_to_{to_s}")


def update_candidate_score(symbol: str, composite_score: float,
                           score_percentile: float):
    conn = _conn()
    conn.execute(
        "UPDATE universe_candidates SET composite_score=?, score_percentile=?, "
        "last_reviewed=? WHERE symbol=?",
        (composite_score, score_percentile, date.today().isoformat(), symbol),
    )
    conn.commit()
    conn.close()


def bulk_update_scores(scores: List[Dict]):
    """scores: list of {symbol, composite_score, score_percentile}"""
    conn = _conn()
    today = date.today().isoformat()
    conn.executemany(
        "UPDATE universe_candidates SET composite_score=?, score_percentile=?, "
        "last_reviewed=? WHERE symbol=?",
        [(s["composite_score"], s["score_percentile"], today, s["symbol"])
         for s in scores],
    )
    conn.commit()
    conn.close()


def increment_churn_counter(symbol: str, above: bool):
    """Increment weeks_above or weeks_below, reset the other."""
    conn = _conn()
    if above:
        conn.execute(
            "UPDATE universe_candidates SET weeks_above_threshold=weeks_above_threshold+1,"
            " weeks_below_threshold=0 WHERE symbol=?", (symbol,)
        )
    else:
        conn.execute(
            "UPDATE universe_candidates SET weeks_below_threshold=weeks_below_threshold+1,"
            " weeks_above_threshold=0 WHERE symbol=?", (symbol,)
        )
    conn.commit()
    conn.close()


def reset_churn_counters(symbols: Optional[List[str]] = None):
    conn = _conn()
    if symbols:
        conn.executemany(
            "UPDATE universe_candidates SET weeks_above_threshold=0, "
            "weeks_below_threshold=0 WHERE symbol=?",
            [(s,) for s in symbols],
        )
    else:
        conn.execute(
            "UPDATE universe_candidates SET weeks_above_threshold=0, "
            "weeks_below_threshold=0"
        )
    conn.commit()
    conn.close()


def set_lockout(symbol: str, until_date: date):
    conn = _conn()
    conn.execute(
        "UPDATE universe_candidates SET lockout_until=? WHERE symbol=?",
        (until_date.isoformat(), symbol),
    )
    conn.commit()
    conn.close()


# ── ACTIVE (CORE) ─────────────────────────────────────────────────────────

def rebuild_active_universe():
    """Sync universe_active from universe_candidates WHERE status='core'."""
    conn = _conn()
    conn.execute("DELETE FROM universe_active")
    conn.execute(
        "INSERT INTO universe_active (symbol, name, sector, composite_score, "
        "score_percentile, promoted_date, last_updated) "
        "SELECT symbol, name, sector, composite_score, score_percentile, "
        "COALESCE(added_date, date('now')), date('now') "
        "FROM universe_candidates WHERE status='core'"
    )
    conn.commit()
    conn.close()


def get_active_symbols() -> List[str]:
    """Fast lookup — used by data/universe.py."""
    conn = _conn()
    rows = conn.execute("SELECT symbol FROM universe_active ORDER BY score_percentile DESC").fetchall()
    conn.close()
    return [r["symbol"] for r in rows]


def active_universe_count() -> int:
    conn = _conn()
    n = conn.execute("SELECT COUNT(*) FROM universe_active").fetchone()[0]
    conn.close()
    return n


# ── HISTORY ───────────────────────────────────────────────────────────────

def log_event(symbol: str, event: str, from_status: str = "", to_status: str = "",
              score: float = 0.0, reason: str = "", operator: str = "system"):
    conn = _conn()
    conn.execute(
        "INSERT INTO universe_history (symbol, event, from_status, to_status, "
        "score_at_event, reason, operator) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (symbol, event, from_status, to_status, score, reason, operator),
    )
    conn.commit()
    conn.close()


def get_history(symbol: Optional[str] = None, limit: int = 100) -> List[Dict]:
    conn = _conn()
    if symbol:
        rows = conn.execute(
            "SELECT * FROM universe_history WHERE symbol=? ORDER BY ts DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM universe_history ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── STATIC WATCHLIST POINT-IN-TIME TRACKING ────────────────────────────────
# config/watchlist_nse.py's ALL_SYMBOLS is a plain list with no dated record
# of when symbols were added/removed, and git holds only a single squashed
# commit for the file — so history can't be recovered after the fact. These
# functions log dated snapshots going forward (reusing universe_history, the
# same table the dynamic promotion/demotion state machine already logs to)
# so that get_static_symbols_as_of() can answer "what was tradeable on date
# X" for any date at or after tracking began, instead of a caller silently
# substituting today's list for every historical date.

STATIC_SYNC_OPERATOR = "static_watchlist_sync"


def get_static_universe_tracking_start() -> Optional[str]:
    """Earliest ts of any logged static-watchlist snapshot event, or None if never seeded."""
    conn = _conn()
    row = conn.execute(
        "SELECT MIN(ts) AS start FROM universe_history WHERE operator = ?",
        (STATIC_SYNC_OPERATOR,),
    ).fetchone()
    conn.close()
    return row["start"] if row and row["start"] else None


def get_static_symbols_as_of(as_of) -> Optional[List[str]]:
    """
    Reconstruct static-watchlist membership as of `as_of` (a date) from
    logged static_add/static_remove events. Returns None if `as_of` predates
    the earliest logged event — point-in-time membership before that date is
    not knowable from any existing record, and callers must not treat None
    as "empty universe."
    """
    start = get_static_universe_tracking_start()
    if not start or as_of.isoformat() < start[:10]:
        return None
    conn = _conn()
    rows = conn.execute(
        "SELECT symbol, event, ts FROM universe_history "
        "WHERE operator = ? AND ts <= ? ORDER BY ts ASC, id ASC",
        (STATIC_SYNC_OPERATOR, as_of.isoformat() + " 23:59:59"),
    ).fetchall()
    conn.close()
    membership: Dict[str, bool] = {}
    for r in rows:
        membership[r["symbol"]] = r["event"] == "static_add"
    return sorted(sym for sym, present in membership.items() if present)


def sync_static_universe_snapshot(current_symbols: List[str], reason: str = "sync") -> int:
    """
    Diff `current_symbols` (config/watchlist_nse.py's ALL_SYMBOLS) against the
    last logged static-watchlist snapshot and log any additions/removals with
    today's real date/time. Call this every time the static file changes —
    tests/test_static_universe_sync.py fails if the working file and the last
    logged snapshot ever diverge, so this can't silently go stale the way the
    2026-06-17 revision did. The first-ever call seeds a full baseline (every
    current symbol logged as static_add "today") — that baseline is the
    earliest date get_static_symbols_as_of() can answer for; membership
    before it is permanently unknowable. Returns the number of changes logged.

    Timestamps are written explicitly via Python's local `datetime.now()`
    rather than relying on the schema's CURRENT_TIMESTAMP default (which
    SQLite evaluates in UTC) — as_of comparisons elsewhere in this module use
    Python's local `date.today()`/caller-supplied calendar dates, and mixing
    UTC-stored timestamps with local-date comparisons risks an off-by-one-day
    boundary error for several hours around each local midnight.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    rows = conn.execute(
        "SELECT symbol, event, MAX(ts) AS ts FROM universe_history "
        "WHERE operator = ? GROUP BY symbol",
        (STATIC_SYNC_OPERATOR,),
    ).fetchall()
    last_known = {r["symbol"]: r["event"] for r in rows}
    current_set = set(current_symbols)
    changes = 0
    for sym in current_set:
        if last_known.get(sym) != "static_add":
            conn.execute(
                "INSERT INTO universe_history (symbol, event, to_status, reason, operator, ts) "
                "VALUES (?, 'static_add', 'in_static_universe', ?, ?, ?)",
                (sym, reason, STATIC_SYNC_OPERATOR, now_str),
            )
            changes += 1
    for sym, last_event in last_known.items():
        if last_event == "static_add" and sym not in current_set:
            conn.execute(
                "INSERT INTO universe_history (symbol, event, from_status, reason, operator, ts) "
                "VALUES (?, 'static_remove', 'in_static_universe', ?, ?, ?)",
                (sym, reason, STATIC_SYNC_OPERATOR, now_str),
            )
            changes += 1
    conn.commit()
    conn.close()
    return changes


# ── IPO ───────────────────────────────────────────────────────────────────

def upsert_ipo(symbol: str, **kwargs):
    conn = _conn()
    cols = ["symbol"] + list(kwargs.keys())
    placeholders = ", ".join("?" * len(cols))
    updates = ", ".join(f"{k}=excluded.{k}" for k in kwargs)
    vals = [symbol] + list(kwargs.values())
    conn.execute(
        f"INSERT INTO universe_ipo ({', '.join(cols)}) VALUES ({placeholders})"
        f" ON CONFLICT(symbol) DO UPDATE SET {updates}",
        vals,
    )
    conn.commit()
    conn.close()


def get_watching_ipos() -> List[Dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM universe_ipo WHERE status='watching' ORDER BY listing_date"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_ipo_status(symbol: str, status: str, notes: str = ""):
    conn = _conn()
    conn.execute(
        "UPDATE universe_ipo SET status=?, last_checked=?, notes=? WHERE symbol=?",
        (status, date.today().isoformat(), notes, symbol),
    )
    conn.commit()
    conn.close()


# ── METRICS (weekly snapshots) ────────────────────────────────────────────

def save_weekly_metrics(week_date: date, metrics: List[Dict]):
    """
    Flexible insert — maps factor names to available columns.
    Handles both old (momentum_1m/volatility_rank) and new (breakout_readiness/volatility_atr)
    factor schemas without breaking on schema evolution.
    """
    conn = _conn()
    week_str = week_date.isoformat()
    # Migrate schema if new columns don't exist yet
    for col_def in (
        "breakout_readiness REAL",
        "momentum_consistency REAL",
        "volatility_atr REAL",
        "rs_momentum_6m REAL",
        "rs_momentum_3m REAL",
    ):
        col = col_def.split()[0]
        try:
            conn.execute(f"ALTER TABLE universe_metrics ADD COLUMN {col_def}")
        except Exception:
            pass  # column already exists
    conn.executemany(
        "INSERT OR REPLACE INTO universe_metrics "
        "(week_date, symbol, rs_momentum_6m, rs_momentum_3m, "
        "breakout_readiness, momentum_consistency, "
        "volume_quality, volatility_atr, composite_score, score_percentile, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(week_str, m["symbol"],
          m.get("rs_momentum_6m", m.get("momentum_6m")),
          m.get("rs_momentum_3m", m.get("momentum_3m")),
          m.get("breakout_readiness", m.get("momentum_1m")),
          m.get("momentum_consistency"),
          m.get("volume_quality"),
          m.get("volatility_atr", m.get("volatility_rank")),
          m.get("composite_score"),
          m.get("score_percentile"),
          m.get("status"))
         for m in metrics],
    )
    conn.commit()
    conn.close()


def get_weekly_metrics(week_date: date) -> List[Dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM universe_metrics WHERE week_date=? ORDER BY score_percentile DESC",
        (week_date.isoformat(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── STATS ─────────────────────────────────────────────────────────────────

def get_universe_stats() -> Dict[str, Any]:
    conn = _conn()
    counts = {}
    for status in ("core", "watchlist", "removed", "lockout", "ipo_watch", "delisted"):
        n = conn.execute(
            "SELECT COUNT(*) FROM universe_candidates WHERE status=?", (status,)
        ).fetchone()[0]
        counts[status] = n
    recent = conn.execute(
        "SELECT event, COUNT(*) as n FROM universe_history "
        "WHERE ts >= date('now', '-7 days') GROUP BY event"
    ).fetchall()
    conn.close()
    counts["recent_events"] = {r["event"]: r["n"] for r in recent}
    return counts


# ── SECTOR ANALYSIS ───────────────────────────────────────────────────────

def get_sector_concentration(status: str = "core") -> Dict[str, int]:
    """Return {sector: count} for stocks in given status."""
    conn = _conn()
    rows = conn.execute(
        "SELECT COALESCE(sector, 'Unknown') as sector, COUNT(*) as n "
        "FROM universe_candidates WHERE status=? GROUP BY sector ORDER BY n DESC",
        (status,),
    ).fetchall()
    conn.close()
    return {r["sector"]: r["n"] for r in rows}


def get_score_history(symbol: str, weeks: int = 12) -> List[Dict]:
    """Weekly score history for a stock — used for trend detection."""
    conn = _conn()
    rows = conn.execute(
        "SELECT week_date, composite_score, score_percentile FROM universe_metrics "
        "WHERE symbol=? ORDER BY week_date DESC LIMIT ?",
        (symbol, weeks),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_core_score_history(weeks: int = 13) -> List[Dict]:
    """All core stocks' weekly scores for the last N weeks — used by audit."""
    conn = _conn()
    rows = conn.execute(
        "SELECT m.week_date, m.symbol, m.composite_score, m.score_percentile, "
        "m.rs_momentum_6m, m.volume_quality, c.sector "
        "FROM universe_metrics m "
        "JOIN universe_candidates c ON m.symbol = c.symbol "
        "WHERE c.status = 'core' "
        "AND m.week_date >= date('now', ? || ' days') "
        "ORDER BY m.week_date, m.score_percentile DESC",
        (f"-{weeks * 7}",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_events_in_period(from_date: date, to_date: date) -> List[Dict]:
    """History events in a date range — for quarterly audit."""
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM universe_history "
        "WHERE ts >= ? AND ts <= ? ORDER BY ts",
        (from_date.isoformat(), to_date.isoformat()),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_avg_metrics_for_period(from_date: date, to_date: date) -> Dict[str, float]:
    """Average composite_score and score_percentile for CORE in a date range."""
    conn = _conn()
    row = conn.execute(
        "SELECT AVG(m.composite_score) as avg_score, "
        "AVG(m.score_percentile) as avg_pct, "
        "AVG(m.volume_quality) as avg_volume, "
        "COUNT(DISTINCT m.symbol) as n_symbols "
        "FROM universe_metrics m "
        "JOIN universe_candidates c ON m.symbol = c.symbol "
        "WHERE c.status = 'core' "
        "AND m.week_date >= ? AND m.week_date <= ?",
        (from_date.isoformat(), to_date.isoformat()),
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


# ── STRATEGY FEEDBACK ─────────────────────────────────────────────────────
# These query the `trades` table written by the strategy engine.
# Same DB (trading.db) — universe and strategy share one file.

def get_strategy_stats(symbol: str) -> Dict[str, Any]:
    """Return trade count, wins, net P&L and win-rate for a symbol from strategy trades."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT net_pnl FROM trades WHERE symbol = ?", (symbol,)
        ).fetchall()
    except Exception:
        rows = []
    conn.close()
    total = len(rows)
    wins  = sum(1 for r in rows if (r["net_pnl"] or 0) > 0)
    pnl   = sum((r["net_pnl"] or 0) for r in rows)
    return {
        "trades":   total,
        "wins":     wins,
        "net_pnl":  pnl,
        "win_rate": (wins / total) if total else 0.0,
    }


def get_trade_count_recent(symbol: str, months: int = 6) -> int:
    """Count strategy trades for symbol in the last N months."""
    from datetime import timedelta
    conn = _conn()
    cutoff = (date.today() - timedelta(days=months * 30)).isoformat()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM trades WHERE symbol = ? AND exit_date >= ?",
            (symbol, cutoff),
        ).fetchone()
    except Exception:
        row = None
    conn.close()
    return int(row["cnt"]) if row else 0
