-- Research Intelligence Platform — Phase 1 schema (docs/48 + docs/49).
-- Physically separate database from the live-trading db/schema.sql — never
-- attach or query this file from live trading code (docs/48 §2).
--
-- Phase 1 tables: experiments, parameter_deltas, performance_metrics,
-- evidence_ledger, research_decisions, plus docs/49's strategy_family and
-- param_taxonomy (both needed for experiments.strategy_family_id and
-- parameter_deltas.attribution_dimension to resolve to real lookups).
--
-- Phase 2 tables (added 2026-08-03, docs/48 §4.6-4.10 + docs/49 §3-6):
-- research_questions, research_hypotheses (+confidence_history),
-- market_context, trade_attribution (live-side only — backtest side stays
-- Phase 3, blocked on a backtest/engine.py trade-persistence change per
-- docs/48 §3), strategy_config_snapshot, feature_registry (view),
-- daily_strategy_state.
--
-- research_debt/technical_debt/open_questions intentionally still not
-- created — their real capture sources don't exist (docs/48 §9, Phase 3+).

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
    hypothesis_id           INTEGER REFERENCES research_hypotheses(id),  -- table now exists (Phase 2); no writer yet -- ingest scripts don't set this
    strategy_version_id     INTEGER REFERENCES strategy_config_snapshot(id),  -- table now exists (Phase 2); no writer yet
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

-- ============================================================================
-- Phase 2 (docs/48 §4.6-4.10, docs/49 §3-6)
-- ============================================================================

-- docs/49 §3 -- broader/longer-lived than a single research_hypotheses row;
-- one question can be asked multiple different ways over time (docs/49's own
-- example: "does sector rotation help?" asked 3 different ways in this repo's
-- history — SECTOR_RS_WEIGHT REJECT, sector_durability PASS, regime-gated
-- untested). status is a bounded auto-suggestion only (see ingest note below),
-- never silently auto-closed.
CREATE TABLE IF NOT EXISTS research_questions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    question_text       TEXT    NOT NULL,
    status              TEXT    NOT NULL DEFAULT 'OPEN' CHECK (status IN (
        'OPEN', 'PARTIALLY_ANSWERED', 'ANSWERED', 'ABANDONED')),
    resolution_summary  TEXT,
    opened_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    closed_at           TEXT
);

-- docs/48 §4.6. economic_reasoning/expected_alpha_mechanism/expected_failure_mechanism
-- are split into three NOT NULL columns (not one free-text blob) because the
-- charter's "every feature must answer" requires each as a distinct,
-- separately-checkable question — a hypothesis missing an explicit failure
-- mechanism fails a schema constraint, not just reads as a weaker paragraph.
CREATE TABLE IF NOT EXISTS research_hypotheses (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id                 INTEGER REFERENCES research_questions(id),  -- nullable: one-off hypotheses don't need a forced parent
    statement                   TEXT    NOT NULL,
    economic_reasoning          TEXT    NOT NULL,
    expected_alpha_mechanism    TEXT    NOT NULL,
    expected_failure_mechanism  TEXT    NOT NULL,
    status                      TEXT    NOT NULL DEFAULT 'UNRESOLVED' CHECK (status IN (
        'ACCEPTED', 'REJECTED', 'UNRESOLVED', 'NEEDS_MORE_EVIDENCE')),
    confidence_score             REAL    NOT NULL DEFAULT 0.5,
    superseded_by                INTEGER REFERENCES research_hypotheses(id),
    created_at                   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at                   TEXT
);
CREATE INDEX IF NOT EXISTS idx_hypotheses_question ON research_hypotheses(question_id);

-- Audit trail for confidence_score changes (docs/48 §4.6: "a reviewable,
-- logged change... not a silent overwrite"). No auto-updater script writes
-- this table yet — nothing in this repo today links a new experiment to a
-- hypothesis_id and bumps confidence automatically (experiments.hypothesis_id
-- itself has no writer either, see the FK's Phase 1 comment above). Table
-- exists so a future updater has somewhere to log to that isn't a silent
-- overwrite; this is intentionally dormant, not wired to anything yet.
CREATE TABLE IF NOT EXISTS confidence_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_id   INTEGER NOT NULL REFERENCES research_hypotheses(id),
    old_score       REAL    NOT NULL,
    new_score       REAL    NOT NULL,
    experiment_id   INTEGER REFERENCES experiments(id),  -- the experiment whose verdict triggered this move, if any
    reason          TEXT    NOT NULL,
    recorded_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_confidence_history_hypothesis ON confidence_history(hypothesis_id);

-- docs/48 §4.7. regime_label is logged from strategy/regime.py's
-- detect_regime() output, never re-derived independently in this table —
-- avoids two disagreeing regime classifiers existing in the codebase.
--
-- DISCREPANCY (2026-08-03): docs/48 §4.7 assumes a CHOP regime_label exists
-- and defines pct_days_chop against it. Checked strategy/regime.py's actual
-- detect_regime() -- it returns only "BULL", "BEAR", or "UNKNOWN". There is
-- no CHOP state anywhere in this codebase (stress-test scenario names like
-- "prolonged_sideways_chop" are backtest fixtures, not a regime label).
-- pct_days_chop is kept as a real column (not dropped) because docs/49 §0's
-- own rebalancing instruction points toward this exact kind of research
-- question -- "should CHOP be a distinct third regime, separate from
-- UNKNOWN?" is a real, currently-open research question, not settled by
-- this schema change. Until that's answered, pct_days_chop has no source
-- and any ingest code populating this table must leave it NULL (equivalent
-- to pct_days_bull + pct_days_bear summing to <100%, i.e. the UNKNOWN
-- remainder), never silently computed as 100% - bull% - bear%, which would
-- fabricate a CHOP measurement the codebase doesn't actually make.
CREATE TABLE IF NOT EXISTS market_context (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   INTEGER NOT NULL REFERENCES experiments(id),
    window_start    TEXT,
    window_end      TEXT,
    regime_label    TEXT,
    pct_days_bull   REAL,
    pct_days_bear   REAL,
    pct_days_chop   REAL,  -- NULL until CHOP-vs-UNKNOWN is resolved as its own research question -- see DISCREPANCY note above
    breadth_avg     REAL
);
CREATE INDEX IF NOT EXISTS idx_market_context_experiment ON market_context(experiment_id);

-- docs/48 §4.8 (live-side only — backtest side is Phase 3, blocked on a
-- backtest/engine.py trade-persistence change) + docs/49 §5's
-- entry/exit_indicator_snapshot + expected_vs_actual_pct columns folded
-- directly into the CREATE (docs/49 issued them as ALTER TABLE against an
-- already-created table; since this table is being created fresh here,
-- the final column set is applied in one statement instead of create+alter).
--
-- SOURCING CHECK (2026-08-03, against db/schema.sql's live `trades` table +
-- runner/daily_runner.py + portfolio/manager.py): docs/48 framed this table
-- as "Phase 2, live-side" implying real sources exist today. That's only
-- true for symbol/sector/exit_reason/hold_days/regime_at_entry — all present
-- in `trades` already. entry_reason, mfe, mae, candidate_score,
-- replacement_score, expected_alpha, realized_alpha have NO current capture
-- source: portfolio/manager.py computes composite_rank transiently at
-- entry/replacement-decision time (lines 267, 415-418) but discards it —
-- never written to `trades` or anywhere persisted. Wiring that persistence
-- is its own engine change, not done in this commit — same "NULL until
-- sourced, not fabricated" discipline as daily_strategy_state below.
-- entry/exit_indicator_snapshot: same gap — compute_indicators() output
-- isn't persisted per-trade today either. expected_vs_actual_pct stays NULL
-- until a composite_rank -> expected-return mapping is derived as its own
-- piece of research (docs/49 §5).
CREATE TABLE IF NOT EXISTS trade_attribution (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id                INTEGER NOT NULL REFERENCES experiments(id),
    source                       TEXT    NOT NULL CHECK (source IN ('live', 'backtest')),
    operational_trade_id         INTEGER,  -- logical FK into the live-trading DB's trades.id (docs/48 §2 physical separation — not a real SQL FK, cross-DB)
    symbol                       TEXT,
    sector                       TEXT,
    entry_reason                 TEXT,
    exit_reason                  TEXT,
    hold_days                    INTEGER,
    is_winner                    INTEGER,
    mfe                          REAL,
    mae                          REAL,
    regime_at_entry              TEXT,
    candidate_score               REAL,
    replacement_score             REAL,
    expected_alpha                REAL,
    realized_alpha                 REAL,
    entry_indicator_snapshot       TEXT,  -- JSON dump of compute_indicators() at entry
    exit_indicator_snapshot        TEXT,  -- JSON dump of compute_indicators() at exit
    expected_vs_actual_pct          REAL  -- NULL until a composite_rank->expected-return mapping exists (docs/49 §5)
);
CREATE INDEX IF NOT EXISTS idx_trade_attribution_experiment ON trade_attribution(experiment_id);
CREATE INDEX IF NOT EXISTS idx_trade_attribution_symbol     ON trade_attribution(symbol);

-- docs/48 §4.9 — replaces an earlier 8-table config-split design with one
-- append-only key/value dump per commit.
CREATE TABLE IF NOT EXISTS strategy_config_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    commit_hash     TEXT    NOT NULL,
    category        TEXT    NOT NULL CHECK (category IN (
        'entry', 'exit', 'ranking', 'portfolio_construction', 'universe',
        'risk', 'sizing', 'sector', 'regime', 'fundamental')),
    config_key      TEXT    NOT NULL,
    config_value    TEXT    NOT NULL,
    UNIQUE(commit_hash, config_key)
);
CREATE INDEX IF NOT EXISTS idx_snapshot_commit   ON strategy_config_snapshot(commit_hash);
CREATE INDEX IF NOT EXISTS idx_snapshot_category ON strategy_config_snapshot(category);

-- docs/49 §4. Six of thirteen columns are sourced today from
-- backtest/engine.py's per-day loop (regime, cash_pct, invested_pct,
-- open_positions, universe_size) and backtest/reporter.py's equity_curve
-- (rolling_sharpe_63d, rolling_drawdown_pct, derivable at ingest time — that's
-- actually 7, see note below). The other six (breadth, top_sector,
-- avg_rs_rank, avg_atr_pct, candidate_count, replacement_opportunities) do
-- not exist anywhere in the engine today — nothing currently aggregates a
-- cross-sectional breadth/RS/ATR average or counts candidates seen vs. taken
-- per day. Left as real columns, not omitted, because docs/49 explicitly
-- reserves them — but they stay NULL until that engine instrumentation is
-- proposed and reviewed as its own change (docs/49 §4, "trading-system
-- engineering change requiring its own review"). No ingest code writes any
-- column of this table yet (docs/48 §9 Phase 2 scope was the schema; wiring
-- backtest/engine.py's per-day loop to populate the 7 sourced columns is
-- follow-up work, not done in this commit — see docs/53 adversarial review).
CREATE TABLE IF NOT EXISTS daily_strategy_state (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date                   TEXT    NOT NULL,
    experiment_id                 INTEGER NOT NULL REFERENCES experiments(id),
    regime                        TEXT,     -- sourced: strategy/regime.py detect_regime()
    cash_pct                      REAL,     -- sourced: engine per-day loop / portfolio state
    invested_pct                  REAL,     -- sourced: same
    open_positions                 INTEGER,  -- sourced: same
    universe_size                  INTEGER,  -- sourced: point-in-time universe snapshot
    breadth                        REAL,     -- NOT sourced -- needs new engine instrumentation
    top_sector                     TEXT,     -- NOT sourced -- needs new engine instrumentation
    avg_rs_rank                     REAL,     -- NOT sourced -- needs new engine instrumentation
    avg_atr_pct                     REAL,     -- NOT sourced -- needs new engine instrumentation
    candidate_count                 INTEGER,  -- NOT sourced -- needs new engine instrumentation
    replacement_opportunities        INTEGER,  -- NOT sourced -- needs new engine instrumentation
    rolling_sharpe_63d               REAL,     -- derivable from equity_curve at ingest, not per-day in engine
    rolling_drawdown_pct             REAL,     -- derivable from equity_curve at ingest
    UNIQUE(trade_date, experiment_id)
);
CREATE INDEX IF NOT EXISTS idx_daily_state_experiment ON daily_strategy_state(experiment_id);

-- docs/48 §4.10 — generated view, not a stored table. The concrete answer to
-- "which parameters consistently help/hurt": a SELECT against parameter_deltas
-- joined with performance_metrics/research_decisions, not a maintained table
-- that can silently drift out of sync with the rows it summarizes.
CREATE VIEW IF NOT EXISTS feature_registry AS
SELECT pd.param_key,
       COUNT(DISTINCT e.id)                                   AS times_tested,
       SUM(rd.verdict = 'APPROVE')                            AS times_accepted,
       SUM(rd.verdict = 'REJECT')                              AS times_rejected,
       AVG(pm_test.cagr)                                       AS avg_test_cagr,
       AVG(pm_test.sharpe)                                     AS avg_test_sharpe,
       AVG(pm_stress.max_drawdown_pct)                          AS avg_stress_mdd
FROM parameter_deltas pd
JOIN experiments e              ON e.id = pd.experiment_id
LEFT JOIN research_decisions rd ON rd.experiment_id = e.id
LEFT JOIN performance_metrics pm_test   ON pm_test.experiment_id = e.id AND pm_test.source = 'test'
LEFT JOIN performance_metrics pm_stress ON pm_stress.experiment_id = e.id AND pm_stress.source LIKE 'stress_%'
GROUP BY pd.param_key;
