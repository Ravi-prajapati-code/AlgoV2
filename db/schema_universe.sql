-- Universe management schema
-- Apply via: python main.py initdb   (repository.py loads both schema files)

-- ── UNIVERSE CANDIDATES (WATCHLIST_200) ───────────────────────────────────
-- All potential stocks being tracked. status drives promotion/demotion.
CREATE TABLE IF NOT EXISTS universe_candidates (
    symbol          TEXT    NOT NULL PRIMARY KEY,
    name            TEXT,
    sector          TEXT,
    market_cap_cr   REAL,
    isin            TEXT,
    status          TEXT    NOT NULL DEFAULT 'watchlist',
    -- ^ 'watchlist' | 'core' | 'removed' | 'lockout' | 'ipo_watch'
    added_date      DATE    NOT NULL,
    last_reviewed   DATE,
    composite_score REAL    DEFAULT 0,
    score_percentile REAL   DEFAULT 0,
    -- Churn protection counters (weeks consecutive above/below threshold)
    weeks_above_threshold  INTEGER DEFAULT 0,
    weeks_below_threshold  INTEGER DEFAULT 0,
    lockout_until   DATE,
    notes           TEXT
);

-- ── UNIVERSE ACTIVE (CORE_100) ────────────────────────────────────────────
-- Denormalized fast-lookup table. strategy/signals.py reads this.
-- Rebuilt from universe_candidates on every weekly refresh.
CREATE TABLE IF NOT EXISTS universe_active (
    symbol          TEXT    NOT NULL PRIMARY KEY,
    name            TEXT,
    sector          TEXT,
    composite_score REAL    DEFAULT 0,
    score_percentile REAL   DEFAULT 0,
    promoted_date   DATE    NOT NULL,
    last_updated    DATE    NOT NULL
);

-- ── UNIVERSE HISTORY ──────────────────────────────────────────────────────
-- Full audit trail of every promotion, demotion, removal, or lockout.
CREATE TABLE IF NOT EXISTS universe_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    symbol          TEXT    NOT NULL,
    event           TEXT    NOT NULL,
    -- ^ 'added_watchlist' | 'promoted_core' | 'demoted_watchlist'
    --   'removed' | 'lockout_applied' | 'lockout_expired' | 'ipo_qualified'
    from_status     TEXT,
    to_status       TEXT,
    score_at_event  REAL,
    reason          TEXT,
    operator        TEXT    DEFAULT 'system'
    -- 'system' for automated, 'manual' for human override
);

-- ── IPO WATCH ─────────────────────────────────────────────────────────────
-- New listings being monitored before qualifying for watchlist.
CREATE TABLE IF NOT EXISTS universe_ipo (
    symbol          TEXT    NOT NULL PRIMARY KEY,
    name            TEXT,
    sector          TEXT,
    listing_date    DATE    NOT NULL,
    issue_price     REAL,
    qualify_after   DATE,
    -- date when min_listing_days threshold is crossed
    status          TEXT    NOT NULL DEFAULT 'watching',
    -- 'watching' | 'qualified' | 'rejected' | 'added'
    last_checked    DATE,
    notes           TEXT
);

-- ── UNIVERSE METRICS (weekly snapshot per symbol) ─────────────────────────
-- Rolling history of scores for trend analysis and debugging.
CREATE TABLE IF NOT EXISTS universe_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    week_date       DATE    NOT NULL,   -- Friday date of the week
    symbol          TEXT    NOT NULL,
    momentum_6m     REAL,
    momentum_3m     REAL,
    momentum_1m     REAL,
    volume_quality  REAL,
    volatility_rank REAL,
    composite_score REAL,
    score_percentile REAL,
    status          TEXT,
    UNIQUE (week_date, symbol)
);

-- ── INDEXES ───────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_ucand_status      ON universe_candidates (status);
CREATE INDEX IF NOT EXISTS idx_ucand_sector      ON universe_candidates (sector);
CREATE INDEX IF NOT EXISTS idx_uhist_symbol      ON universe_history (symbol);
CREATE INDEX IF NOT EXISTS idx_uhist_event       ON universe_history (event);
CREATE INDEX IF NOT EXISTS idx_uhist_ts          ON universe_history (ts);
CREATE INDEX IF NOT EXISTS idx_umetrics_week     ON universe_metrics (week_date);
CREATE INDEX IF NOT EXISTS idx_umetrics_symbol   ON universe_metrics (symbol);
CREATE INDEX IF NOT EXISTS idx_uipo_status       ON universe_ipo (status);
