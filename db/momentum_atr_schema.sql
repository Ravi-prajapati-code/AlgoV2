-- Momentum x ATR standalone live strategy ledger (docs/57, docs/58, plan
-- velvet-cooking-minsky) -- physically separate SQLite file from the
-- live-trading DB (config.settings.DB_PATH). Never attach/query from the
-- main strategy's code path -- db/schema.sql's positions.symbol
-- TEXT NOT NULL UNIQUE means a second strategy writing the same symbol
-- into that table would silently clobber the other's entry price/shares.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL UNIQUE,
    entry_date      DATE    NOT NULL,
    entry_price     REAL    NOT NULL,
    shares          INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'OPEN',
    entry_order_id  TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    entry_date      DATE    NOT NULL,
    exit_date       DATE,
    entry_price     REAL    NOT NULL,
    exit_price      REAL,
    shares          INTEGER NOT NULL,
    gross_pnl       REAL,
    charges         REAL,
    net_pnl         REAL,
    exit_reason     TEXT,
    exit_order_id   TEXT
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    date                    DATE    NOT NULL UNIQUE,
    cash                    REAL    NOT NULL,
    invested_value          REAL    NOT NULL,
    total_equity            REAL    NOT NULL,
    peak_equity             REAL    NOT NULL,
    drawdown_pct            REAL    NOT NULL DEFAULT 0,
    kill_switch_tripped     INTEGER NOT NULL DEFAULT 0
);

-- Singleton row (id=1): writable live state carried between daily runs.
-- equity_snapshots above is the immutable one-row-per-day audit trail;
-- this is the mutable current cash/peak/kill-switch the next run reads.
CREATE TABLE IF NOT EXISTS state (
    id                          INTEGER PRIMARY KEY CHECK (id = 1),
    cash                        REAL    NOT NULL,
    peak_equity                 REAL    NOT NULL,
    kill_switch_tripped         INTEGER NOT NULL DEFAULT 0,
    kill_switch_tripped_date    DATE
);

-- Written once per morning by scripts/precompute_momentum_atr_ranking.py
-- (runs early, before market open -- the slow, universe-wide scoring
-- pass). Read once per morning by scripts/run_momentum_atr_live.py at
-- 9:17 IST, which does no scoring itself -- only order placement against
-- the ranking already sitting here. date UNIQUE doubles as same-day
-- idempotency, matching equity_snapshots' pattern.
CREATE TABLE IF NOT EXISTS daily_ranking (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            DATE    NOT NULL UNIQUE,
    ranked_json     TEXT    NOT NULL,
    closes_json     TEXT    NOT NULL,
    scored_count    INTEGER NOT NULL,
    computed_at     TEXT    NOT NULL
);

-- Per-pipeline-stage completion record, written by
-- precompute_momentum_atr_ranking.py (step='PRECOMPUTE') and
-- run_momentum_atr_live.py (step='EXECUTION') at the end of every attempt
-- (success, abort, or crash -- not just success). scripts/
-- momentum_atr_sla_check.py reads this once at EOD and reports RED if
-- either step is missing or not OK for a trading day -- catches "the cron
-- never fired at all" (flock deadlock, crash before any code runs), which
-- a plain log-exists check can miss if something else writes to the same
-- log file. UNIQUE(date, step) so a same-day rerun overwrites cleanly,
-- matching daily_ranking/equity_snapshots' idempotency pattern.
CREATE TABLE IF NOT EXISTS sla_checkpoints (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            DATE    NOT NULL,
    step            TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    detail          TEXT,
    ts              TEXT    NOT NULL,
    UNIQUE(date, step)
);
