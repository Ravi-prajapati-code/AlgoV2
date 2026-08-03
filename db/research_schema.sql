-- Research Intelligence Platform — Phase 1 schema (docs/48 + docs/49).
-- Physically separate database from the live-trading db/schema.sql — never
-- attach or query this file from live trading code (docs/48 §2).
--
-- Phase 1 tables only: experiments, parameter_deltas, performance_metrics,
-- evidence_ledger, research_decisions, plus docs/49's strategy_family and
-- param_taxonomy (both needed for experiments.strategy_family_id and
-- parameter_deltas.attribution_dimension to resolve to real lookups).
-- Phase 2/3 tables (research_hypotheses, market_context, trade_attribution,
-- strategy_config_snapshot, research_questions, daily_strategy_state,
-- research_debt/technical_debt/open_questions) intentionally not created
-- yet — their real capture sources don't exist (docs/48 §9).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS strategy_family (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,
    description     TEXT,
    is_control_arm  INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS param_taxonomy (
    param_key              TEXT PRIMARY KEY,
    attribution_dimension  TEXT NOT NULL CHECK (attribution_dimension IN (
        'stock_selection','universe_construction','ranking','portfolio_construction',
        'risk_management','entry_timing','exit_timing','regime','sector',
        'execution_assumptions')),
    alpha_source            TEXT CHECK (alpha_source IN (
        'trend','momentum','quality','value','volatility','liquidity',
        'portfolio','risk','execution','universe','fundamental','sector','macro')),
    notes                   TEXT
);

CREATE TABLE IF NOT EXISTS experiments (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                    TEXT    NOT NULL UNIQUE,
    title                   TEXT    NOT NULL,
    author_role             TEXT    NOT NULL,
    commit_hash             TEXT,
    branch                  TEXT,
    docs_nn_path            TEXT,
    baseline_experiment_id  INTEGER REFERENCES experiments(id),
    hypothesis_id           INTEGER,  -- REFERENCES research_hypotheses(id), Phase 2 table not yet built
    strategy_version_id     INTEGER,  -- REFERENCES strategy_config_snapshot(id), Phase 2 table not yet built
    strategy_family_id      INTEGER REFERENCES strategy_family(id),
    status                  TEXT    NOT NULL DEFAULT 'PROPOSED',
    runtime_ms              INTEGER,
    peak_mem_mb             REAL,
    created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    decided_at              TEXT
);
CREATE INDEX IF NOT EXISTS idx_experiments_baseline ON experiments(baseline_experiment_id);
CREATE INDEX IF NOT EXISTS idx_experiments_commit   ON experiments(commit_hash);
CREATE INDEX IF NOT EXISTS idx_experiments_status   ON experiments(status);

CREATE TABLE IF NOT EXISTS parameter_deltas (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id           INTEGER NOT NULL REFERENCES experiments(id),
    param_key               TEXT    NOT NULL,
    baseline_value          TEXT,
    candidate_value         TEXT,
    attribution_dimension   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_param_deltas_experiment ON parameter_deltas(experiment_id);
CREATE INDEX IF NOT EXISTS idx_param_deltas_key        ON parameter_deltas(param_key);

CREATE TABLE IF NOT EXISTS performance_metrics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id       INTEGER NOT NULL REFERENCES experiments(id),
    source              TEXT    NOT NULL,
    cagr                REAL,
    sharpe              REAL,
    max_drawdown_pct    REAL,
    total_trades        INTEGER,
    win_rate            REAL,
    effective_n         INTEGER,  -- distinct symbols/episodes, docs/47 §3.2 — NOT row count. NULL until the gate computes it (not computed yet — see robustness_gate.py comment).
    p_value             REAL,     -- NULL until a permutation/significance test is wired in (not computed yet)
    window_start        TEXT,
    window_end          TEXT,
    recorded_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_metrics_experiment ON performance_metrics(experiment_id);
CREATE INDEX IF NOT EXISTS idx_metrics_source     ON performance_metrics(source);

CREATE TABLE IF NOT EXISTS evidence_ledger (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id                   INTEGER NOT NULL REFERENCES experiments(id),
    has_economic_reasoning          INTEGER NOT NULL DEFAULT 0,
    effective_n_checked             INTEGER NOT NULL DEFAULT 0,
    train_and_test_reported         INTEGER NOT NULL DEFAULT 0,
    stress_tested                   INTEGER NOT NULL DEFAULT 0,
    config_parity_confirmed         INTEGER NOT NULL DEFAULT 0,
    backtest_live_parity_confirmed  INTEGER NOT NULL DEFAULT 0,
    independently_rederived         INTEGER NOT NULL DEFAULT 0,
    notes                           TEXT
);
CREATE INDEX IF NOT EXISTS idx_evidence_experiment ON evidence_ledger(experiment_id);

CREATE TABLE IF NOT EXISTS research_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   INTEGER NOT NULL REFERENCES experiments(id),
    proposing_role  TEXT NOT NULL,
    reviewing_role  TEXT NOT NULL,
    verdict         TEXT NOT NULL,
    agreements      TEXT,
    disagreements   TEXT,
    open_concerns   TEXT,
    reasoning       TEXT NOT NULL,
    decided_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_decisions_experiment ON research_decisions(experiment_id);

-- Seed param_taxonomy for levers with a real, already-documented gate/ablation
-- result (docs/35, 36, 38 Addendum 3, 50) so the docs/35-47 backfill
-- (scripts/backfill_historical_experiments.py) doesn't hit "unmapped param_key"
-- on ingest. Not exhaustive -- extend as new params get their first real run.
INSERT OR IGNORE INTO param_taxonomy (param_key, attribution_dimension, alpha_source, notes) VALUES
    ('LONG_TERM_REBALANCE_ENABLED', 'stock_selection', 'momentum',
     'RS-rank>=85 buy-hold sleeve, docs/35. REJECTED; module fully removed 2026-08-03 (1cd103c) -- entry stays for historical attribution even though the param no longer exists in settings.py.'),
    ('UNIVERSE_CAP_SIZE', 'universe_construction', 'liquidity',
     'Static top-N-by-turnover universe cap, docs/36. REJECTED (opportunity starvation).'),
    ('ENTRY_MODE', 'entry_timing', 'trend',
     'Entry gate mode (PURE_RS default vs FULL strict-AND trend/ADX/breakout gate), docs/38 Addendum 3.'),
    ('SECTOR_RS_WEIGHT', 'sector', 'sector',
     'Raw sector price-momentum entry-score nudge, docs/50. REJECTED (guts TEST window).');
