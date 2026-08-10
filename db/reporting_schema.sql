-- Observability/dashboard reporting schema (docs/60).
-- Physically separate DB (db/reporting.db) from trading.db and
-- momentum_atr.db -- read-only against both strategy DBs, this is the
-- only schema anything writes to. Never a source of truth for trading
-- decisions.

-- Registry of known strategies, plus their manually-set validation status
-- (RESEARCH/PAPER/LIVE/VALIDATED) -- never auto-set.
CREATE TABLE IF NOT EXISTS strategy_registry (
    strategy_id         TEXT PRIMARY KEY,   -- 'main' | 'momentum_atr'
    display_name        TEXT NOT NULL,
    db_path             TEXT NOT NULL,
    status_label         TEXT NOT NULL,     -- RESEARCH|PAPER|LIVE|VALIDATED, manual
    status_set_by         TEXT,
    status_set_at         TEXT
);

-- Broker-account-wide cache, written once per snapshot cycle, read by every
-- dashboard page needing broker state. This is what stops per-page-load
-- broker calls (see docs/60 §7).
CREATE TABLE IF NOT EXISTS broker_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    broker_cash     REAL NOT NULL,
    total_equity    REAL NOT NULL,
    holdings_json   TEXT NOT NULL   -- symbol -> {qty, avg_price, ltp}, as reported by broker
);

-- Per-strategy capital view, one row per snapshot cycle per strategy.
CREATE TABLE IF NOT EXISTS strategy_capital_snapshot (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id                 TEXT NOT NULL,
    ts                           TEXT NOT NULL,
    strategy_allocated_cash     REAL,          -- NULL where no allocation model exists (MAIN)
    strategy_available_cash     REAL,
    strategy_invested_value     REAL NOT NULL,
    strategy_equity             REAL NOT NULL,
    source_note                 TEXT NOT NULL  -- e.g. "MAIN has no allocation cap; this is whole-account cash MAIN currently sees"
);

-- Per-symbol 3-way position view, one row per symbol per snapshot cycle.
CREATE TABLE IF NOT EXISTS strategy_position_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    main_qty        INTEGER NOT NULL DEFAULT 0,
    momentum_atr_qty INTEGER NOT NULL DEFAULT 0,
    broker_qty      INTEGER NOT NULL,
    collision_flag  INTEGER NOT NULL DEFAULT 0  -- 1 if main_qty+momentum_atr_qty != broker_qty
);

-- Reconciliation run outcomes, PASS/WARNING/FAIL, one row per check per cycle.
CREATE TABLE IF NOT EXISTS strategy_reconciliation_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    strategy_id     TEXT,               -- NULL for cross-strategy checks (e.g. collision sum)
    check_name      TEXT NOT NULL,      -- e.g. 'position_collision_sum', 'capital_integrity'
    result          TEXT NOT NULL,      -- PASS|WARNING|FAIL
    detail          TEXT,
    -- populated only if an auto-repair actually occurred (never silent):
    auto_repaired   INTEGER NOT NULL DEFAULT 0,
    repair_what     TEXT,
    repair_why      TEXT,
    repair_previous_value TEXT,
    repair_new_value TEXT,
    repair_source   TEXT
);

-- Alert history, additive alongside existing Telegram sends.
CREATE TABLE IF NOT EXISTS strategy_alert (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    strategy_id     TEXT,               -- NULL for account-wide alerts
    severity        TEXT NOT NULL,      -- INFO|WARNING|CRITICAL
    category        TEXT NOT NULL,
    message         TEXT NOT NULL,
    source_script   TEXT NOT NULL,
    acknowledged    INTEGER NOT NULL DEFAULT 0
);

-- Per-cron-run completion record, superset of sla_checkpoints for jobs that
-- don't have one today (reconcile, gtt audits, backups, smoke test).
CREATE TABLE IF NOT EXISTS strategy_run_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    strategy_id     TEXT,               -- NULL for shared/cross-cutting jobs
    job_name        TEXT NOT NULL,
    status          TEXT NOT NULL,      -- OK|FAILED|SKIPPED
    detail          TEXT
);
