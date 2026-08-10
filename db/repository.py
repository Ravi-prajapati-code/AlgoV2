"""
SQLite repository — handles all DB reads and writes.
Standardized robust date parsing to handle diverse timestamp formats.
"""

import sqlite3
import json
import os
from datetime import date, datetime, timedelta
from typing import List, Optional
import pandas as pd

from db.models import Position, Trade, PortfolioSnapshot, Signal
from config.settings import DB_PATH

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Run schema.sql + schema_universe.sql to initialize all tables."""
    conn = get_connection()
    for fname in ("schema.sql", "schema_universe.sql"):
        schema_path = os.path.join(os.path.dirname(__file__), fname)
        if os.path.exists(schema_path):
            with open(schema_path, "r") as f:
                conn.executescript(f.read())
    # Migrate existing DBs — SQLite has no ADD COLUMN IF NOT EXISTS
    for migration in [
        "ALTER TABLE positions ADD COLUMN days_below_ema50 INTEGER DEFAULT 0",
        "ALTER TABLE portfolio_snapshots ADD COLUMN capital_injected REAL DEFAULT 0",
        "ALTER TABLE positions ADD COLUMN origin TEXT DEFAULT 'strategy'",
        "ALTER TABLE portfolio_snapshots ADD COLUMN strategy_value REAL DEFAULT 0",
    ]:
        try:
            conn.execute(migration)
            conn.commit()
        except Exception:
            pass  # column already exists
    # Backfill: every snapshot recorded before this migration was 100% strategy-origin
    # (the origin column didn't exist yet, so nothing could have been "manual") — total_value
    # and strategy_value are exactly equal for that entire history, not an approximation.
    conn.execute(
        "UPDATE portfolio_snapshots SET strategy_value = total_value WHERE strategy_value IS NULL OR strategy_value = 0"
    )
    conn.commit()
    conn.close()
    print(f"[DB] Initialized: {DB_PATH}")

# ── OHLCV CACHE ───────────────────────────────────────────────────────────

def save_ohlcv(symbol: str, df: pd.DataFrame):
    if df.empty: return
    conn = get_connection()
    # Expects columns: date, open, high, low, close, volume
    data = []
    for _, row in df.iterrows():
        # Ensure date is string YYYY-MM-DD
        d_str = row['date'].strftime("%Y-%m-%d") if hasattr(row['date'], 'strftime') else str(row['date'])[:10]
        data.append((symbol, d_str, row['open'], row['high'], row['low'], row['close'], int(row['volume'])))
    
    conn.executemany(
        "INSERT OR REPLACE INTO ohlcv_cache (symbol, date, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
        data
    )
    conn.commit()
    conn.close()

def load_ohlcv(symbol: str, start: Optional[date] = None, end: Optional[date] = None) -> pd.DataFrame:
    conn = get_connection()
    query = "SELECT date, open, high, low, close, volume FROM ohlcv_cache WHERE symbol = ?"
    params = [symbol]
    
    if start:
        query += " AND date >= ?"
        params.append(start.strftime("%Y-%m-%d"))
    if end:
        query += " AND date <= ?"
        params.append(end.strftime("%Y-%m-%d"))
        
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    
    if not df.empty:
        # Aggressive parsing: convert to string first, then infer format
        df['date'] = pd.to_datetime(df['date'].astype(str), errors='coerce', utc=True).dt.tz_localize(None)
        # Drop any failed parses
        df = df.dropna(subset=['date'])
        df = df.set_index('date').sort_index()
    return df

def latest_cached_date(symbol: str) -> Optional[date]:
    conn = get_connection()
    row = conn.execute("SELECT MAX(date) as d FROM ohlcv_cache WHERE symbol = ?", (symbol,)).fetchone()
    conn.close()
    if row and row["d"]:
        try:
            # Use pandas for robust parsing of various formats
            return pd.to_datetime(row["d"]).date()
        except:
            return None
    return None

def earliest_cached_date(symbol: str) -> Optional[date]:
    conn = get_connection()
    row = conn.execute("SELECT MIN(date) as d FROM ohlcv_cache WHERE symbol = ?", (symbol,)).fetchone()
    conn.close()
    if row and row["d"]:
        try:
            return pd.to_datetime(row["d"]).date()
        except:
            return None
    return None

# ── POSITIONS ─────────────────────────────────────────────────────────────

def save_position(pos: Position):
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO positions
           (symbol, sector, entry_date, entry_price, shares, stop_loss, take_profit, trailing_stop, peak_price, days_below_ema50, status, atr_at_entry, origin)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (pos.symbol, pos.sector, pos.entry_date.strftime("%Y-%m-%d"), pos.entry_price, pos.shares,
         pos.stop_loss, pos.take_profit, pos.trailing_stop, pos.peak_price, pos.days_below_ema50, pos.status,
         pos.atr_at_entry, pos.origin)
    )
    conn.commit()
    conn.close()

def load_positions(status: str = "OPEN") -> List[Position]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM positions WHERE status = ?", (status,)).fetchall()
    conn.close()
    
    positions = []
    for r in rows:
        positions.append(Position(
            symbol=r["symbol"], sector=r["sector"],
            entry_date=pd.to_datetime(r["entry_date"]).date(),
            entry_price=r["entry_price"], shares=r["shares"],
            stop_loss=r["stop_loss"], take_profit=r["take_profit"],
            trailing_stop=r["trailing_stop"], peak_price=r["peak_price"],
            days_below_ema50=r["days_below_ema50"] if "days_below_ema50" in r.keys() else 0,
            atr_at_entry=r["atr_at_entry"] if "atr_at_entry" in r.keys() and r["atr_at_entry"] is not None else 0.0,
            origin=r["origin"] if "origin" in r.keys() and r["origin"] else "strategy",
            status=r["status"], id=r["id"]
        ))
    return positions

def close_position(symbol: str):
    conn = get_connection()
    conn.execute("UPDATE positions SET status = 'CLOSED' WHERE symbol = ? AND status = 'OPEN'", (symbol,))
    conn.commit()
    conn.close()

def get_last_position(symbol: str) -> Optional[Position]:
    """Return the most recent position record for a symbol (any status) — used to recover entry_date on re-sync."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM positions WHERE symbol = ? ORDER BY id DESC LIMIT 1", (symbol,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return Position(
        symbol=row["symbol"], sector=row["sector"],
        entry_date=pd.to_datetime(row["entry_date"]).date(),
        entry_price=row["entry_price"], shares=row["shares"],
        stop_loss=row["stop_loss"], take_profit=row["take_profit"],
        trailing_stop=row["trailing_stop"], peak_price=row["peak_price"],
        days_below_ema50=row["days_below_ema50"] if "days_below_ema50" in row.keys() else 0,
        origin=row["origin"] if "origin" in row.keys() and row["origin"] else "strategy",
        status=row["status"], id=row["id"]
    )

# ── TRADES ────────────────────────────────────────────────────────────────

def save_trade(t: Trade):
    conn = get_connection()
    conn.execute(
        """INSERT INTO trades
           (symbol, sector, entry_date, exit_date, entry_price, exit_price, shares, gross_pnl, charges, net_pnl, exit_reason, hold_days, slippage_pct)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (t.symbol, t.sector, t.entry_date.strftime("%Y-%m-%d"), t.exit_date.strftime("%Y-%m-%d"),
         t.entry_price, t.exit_price, t.shares, t.gross_pnl, t.charges, t.net_pnl, t.exit_reason, t.hold_days, t.slippage_pct)
    )
    conn.commit()
    conn.close()


def close_position_and_save_trade(symbol: str, t: Trade):
    """Atomically close an OPEN position and record its trade in a single transaction.
    Either both commit or neither — prevents a closed position with no trade record
    (or a recorded trade with the position still OPEN) if the process dies mid-write."""
    conn = get_connection()
    try:
        with conn:  # sqlite3 context manager: commit on success, rollback on exception
            conn.execute(
                "UPDATE positions SET status = 'CLOSED' WHERE symbol = ? AND status = 'OPEN'",
                (symbol,)
            )
            conn.execute(
                """INSERT INTO trades
                   (symbol, sector, entry_date, exit_date, entry_price, exit_price, shares, gross_pnl, charges, net_pnl, exit_reason, hold_days, slippage_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (t.symbol, t.sector, t.entry_date.strftime("%Y-%m-%d"), t.exit_date.strftime("%Y-%m-%d"),
                 t.entry_price, t.exit_price, t.shares, t.gross_pnl, t.charges, t.net_pnl, t.exit_reason, t.hold_days, t.slippage_pct)
            )
    finally:
        conn.close()


def reduce_position_and_save_trade(symbol: str, remaining_shares: int, t: Trade):
    """Atomically reduce an OPEN position's share count (partial sell — e.g. a
    LIQUIDBEES trim to fund another entry) and record the sold portion as its
    own trade, in a single transaction. Mirrors close_position_and_save_trade's
    all-or-nothing guarantee for the partial case; the position row stays OPEN
    with entry_price/entry_date unchanged since only share count changed."""
    conn = get_connection()
    try:
        with conn:
            cur = conn.execute(
                "UPDATE positions SET shares = ? WHERE symbol = ? AND status = 'OPEN'",
                (remaining_shares, symbol)
            )
            if cur.rowcount != 1:
                # No matching OPEN row (already closed/reduced concurrently) — abort the
                # whole transaction so the trade insert below doesn't commit without it.
                raise RuntimeError(f"reduce_position_and_save_trade: expected 1 OPEN row for {symbol}, matched {cur.rowcount}")
            conn.execute(
                """INSERT INTO trades
                   (symbol, sector, entry_date, exit_date, entry_price, exit_price, shares, gross_pnl, charges, net_pnl, exit_reason, hold_days, slippage_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (t.symbol, t.sector, t.entry_date.strftime("%Y-%m-%d"), t.exit_date.strftime("%Y-%m-%d"),
                 t.entry_price, t.exit_price, t.shares, t.gross_pnl, t.charges, t.net_pnl, t.exit_reason, t.hold_days, t.slippage_pct)
            )
    finally:
        conn.close()


def get_last_ohlcv_close(symbol: str) -> float:
    """Return the most recent close price from OHLCV cache. Returns 0.0 if not found."""
    conn = get_connection()
    row = conn.execute(
        "SELECT close FROM ohlcv_cache WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (symbol,)
    ).fetchone()
    conn.close()
    return float(row["close"]) if row and row["close"] else 0.0


def was_sold_today(symbol: str, today) -> bool:
    """Return True if symbol was sold today OR yesterday (blocks T+1 ghost re-sync from settlement residue)."""
    conn = get_connection()
    cutoff = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT id FROM trades WHERE symbol = ? AND exit_date >= ? LIMIT 1",
        (symbol, cutoff)
    ).fetchone()
    conn.close()
    return row is not None


def bear_swing_sold_within(symbol: str, today, days: int) -> bool:
    """True if symbol has a bear_swing exit trade within the last `days` calendar days."""
    conn = get_connection()
    cutoff = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT id FROM trades WHERE symbol = ? AND exit_date >= ? AND exit_reason LIKE 'bear_swing|%' LIMIT 1",
        (symbol, cutoff)
    ).fetchone()
    conn.close()
    return row is not None


def load_trades() -> List[Trade]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM trades ORDER BY exit_date DESC").fetchall()
    conn.close()
    
    trades = []
    for r in rows:
        trades.append(Trade(
            symbol=r["symbol"], sector=r["sector"],
            entry_date=pd.to_datetime(r["entry_date"]).date(),
            exit_date=pd.to_datetime(r["exit_date"]).date(),
            entry_price=r["entry_price"], exit_price=r["exit_price"],
            shares=r["shares"], gross_pnl=r["gross_pnl"],
            charges=r["charges"], net_pnl=r["net_pnl"],
            exit_reason=r["exit_reason"], hold_days=r["hold_days"],
            slippage_pct=r["slippage_pct"] if "slippage_pct" in r.keys() else None,
            id=r["id"]
        ))
    return trades

# ── SIGNALS ───────────────────────────────────────────────────────────────

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        import numpy as np
        # Use np.floating and np.integer for broad compatibility
        if isinstance(obj, (np.integer, int)):
            return int(obj)
        elif isinstance(obj, (np.floating, float)):
            return float(obj)
        elif isinstance(obj, (np.ndarray, list)):
            return obj.tolist()
        elif isinstance(obj, (np.bool_, bool)):
            return bool(obj)
        return json.JSONEncoder.default(self, obj)

def save_signal(s: Signal):
    conn = get_connection()
    # Ensure stop_loss and take_profit are in indicators for persistence
    # if we are not adding columns to the DB table yet.
    inds = s.indicators.copy()
    inds["_stop_loss"] = s.stop_loss
    inds["_take_profit"] = s.take_profit
    
    conn.execute(
        """INSERT INTO signals (date, symbol, action, score, price, reason, indicators_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (s.date.strftime("%Y-%m-%d"), s.symbol, s.action, s.score, s.price, s.reason, json.dumps(inds, cls=NumpyEncoder))
    )
    conn.commit()
    conn.close()

def load_signals(symbol: str = None, action: str = None, start: date = None) -> List[Signal]:
    conn = get_connection()
    query = "SELECT * FROM signals WHERE 1=1"
    params = []
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)
    if action:
        query += " AND action = ?"
        params.append(action)
    if start:
        query += " AND date >= ?"
        params.append(start.strftime("%Y-%m-%d"))
    
    query += " ORDER BY date DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    signals = []
    for r in rows:
        inds = json.loads(r["indicators_json"] if r["indicators_json"] else "{}")
        signals.append(Signal(
            date=pd.to_datetime(r["date"]).date(),
            symbol=r["symbol"], action=r["action"],
            score=r["score"], price=r["price"],
            stop_loss=inds.get("_stop_loss", 0.0),
            take_profit=inds.get("_take_profit", 0.0),
            reason=r["reason"],
            indicators=inds,
            id=r["id"]
        ))
    return signals

# ── SNAPSHOTS ─────────────────────────────────────────────────────────────

def save_snapshot(s: PortfolioSnapshot):
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO portfolio_snapshots
           (date, cash, invested, total_value, open_positions, daily_pnl, cumulative_pnl, regime, capital_injected, strategy_value)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (s.date.strftime("%Y-%m-%d"), s.cash, s.invested, s.total_value, s.open_positions,
         s.daily_pnl, s.cumulative_pnl, s.regime, s.capital_injected, s.strategy_value)
    )
    conn.commit()
    conn.close()

def load_snapshots(limit: int = 500) -> List[PortfolioSnapshot]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM portfolio_snapshots ORDER BY date DESC LIMIT ?", (limit,)).fetchall()
    conn.close()

    snaps = []
    for r in reversed(rows):
        snaps.append(PortfolioSnapshot(
            date=pd.to_datetime(r["date"]).date(),
            cash=r["cash"], invested=r["invested"], total_value=r["total_value"],
            open_positions=r["open_positions"], daily_pnl=r["daily_pnl"], cumulative_pnl=r["cumulative_pnl"],
            regime=r["regime"] if "regime" in r.keys() else None,
            capital_injected=r["capital_injected"] if "capital_injected" in r.keys() else 0.0,
            strategy_value=r["strategy_value"] if "strategy_value" in r.keys() and r["strategy_value"] else r["total_value"],
            id=r["id"]
        ))
    return snaps


def total_capital_injected_ever() -> float:
    """Sum all capital_injected across ALL snapshots — no limit."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(SUM(capital_injected), 0) FROM portfolio_snapshots"
    ).fetchone()
    conn.close()
    return float(row[0])


def snapshot_exists_for_date(d: date) -> bool:
    """Return True if a snapshot was already saved for this date."""
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM portfolio_snapshots WHERE date = ?", (d.strftime("%Y-%m-%d"),)
    ).fetchone()
    conn.close()
    return row is not None


def _json_safe(obj):
    """json.dumps default= hook. indicators/composite.py builds per-symbol
    dicts from pandas .iloc[-1] reads -- numpy.int64/numpy.bool_ don't
    subclass Python int/bool and raise TypeError under plain json.dumps."""
    if hasattr(obj, "item"):
        return obj.item()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def save_precompute_indicators(
    d: date, indicators: dict, regime: str, market_bullish: bool,
    strong_bull: bool, index_candles: int, stalls: int = 0,
) -> None:
    """Persist the main strategy's precompute output (fetch_all + RS +
    compute_all), keyed UNIQUE(date), for the 14:55 execute step to read
    instead of recomputing. regime/market_bullish/strong_bull are stored
    for diagnostics only -- execute always recomputes these fresh."""
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO precompute_indicators
           (date, indicators_json, regime, market_bullish, strong_bull,
            index_candles, scored_count, stalls, computed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            d.strftime("%Y-%m-%d"),
            json.dumps(indicators, default=_json_safe),
            regime,
            int(market_bullish),
            int(strong_bull),
            index_candles,
            len(indicators),
            stalls,
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def load_precompute_indicators(d: date) -> Optional[dict]:
    """Return today's precomputed indicators row, or None if precompute
    hasn't run/completed yet -- absence must be treated as "not available",
    never silently substituted with a fresh live fetch by the caller."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM precompute_indicators WHERE date = ?", (d.strftime("%Y-%m-%d"),)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "indicators": json.loads(row["indicators_json"]),
        "regime": row["regime"],
        "market_bullish": bool(row["market_bullish"]),
        "strong_bull": bool(row["strong_bull"]),
        "index_candles": row["index_candles"],
        "scored_count": row["scored_count"],
        "stalls": row["stalls"],
        "computed_at": row["computed_at"],
    }


def record_sla_checkpoint(d: date, step: str, status: str, detail: str = "") -> None:
    """Written at the end of every PRECOMPUTE/EXECUTION attempt (OK/
    ABORTED/CRASHED) so scripts/main_sla_check.py can tell "ran and
    failed" apart from "never ran at all", which a log-exists check
    can't. UNIQUE(date, step) -- a same-day rerun overwrites, not
    duplicates."""
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO sla_checkpoints (date, step, status, detail, ts)
           VALUES (?, ?, ?, ?, ?)""",
        (d.strftime("%Y-%m-%d"), step, status, detail, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def load_sla_checkpoints(d: date) -> dict:
    """Return {step: {status, detail, ts}} for the given date. Empty dict
    if nothing was recorded -- that absence is itself the "never ran"
    signal callers need."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT step, status, detail, ts FROM sla_checkpoints WHERE date = ?",
        (d.strftime("%Y-%m-%d"),),
    ).fetchall()
    conn.close()
    return {r["step"]: {"status": r["status"], "detail": r["detail"], "ts": r["ts"]} for r in rows}


def load_baseline_capital() -> Optional[float]:
    """Organic starting capital: first snapshot total_value minus any injection detected on day 1."""
    conn = get_connection()
    row = conn.execute(
        "SELECT total_value, capital_injected FROM portfolio_snapshots ORDER BY date ASC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return None
    injected = float(row["capital_injected"]) if row["capital_injected"] else 0.0
    return float(row["total_value"]) - injected
