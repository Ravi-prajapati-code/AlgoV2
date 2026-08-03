# Doc 48 — Research Intelligence Platform: Architecture Design (proposal, no implementation)

**Date**: 2026-08-03. Claude (Chief Quantitative Research Director) proposes; Codex role
(Repository Integrity + Principal Software Engineer) reviews adversarially in the same document,
per `CLAUDE.md`'s single-AI-session rule and `docs/47` §2.1/§5. **This document is architecture
only. No table has been created, no code has been written.** Per the user's explicit instruction:
"Build the complete architecture before writing any implementation code."

## 0. What this replaces and what it doesn't

Today the "source of truth" for research history is: the flat `docs/NN_Title.md` series, this
developer's local Claude Code memory store, terse commit messages, and `robustness_gate.py`'s
stdout (never persisted — confirmed by reading `scripts/robustness_gate.py`: it `print()`s
sections, there is no `json.dump`/file-write of a run's results anywhere in the script). Doc 46
named the failure mode this produces — "documentation-tier drift" — real results landing in
whichever of these four places was easiest at the time, with no single queryable record and no
guarantee any of them survive (memory is outside git; 11 of the `docs/NN.md` files themselves are
currently untracked in git as of this session — see the open-items list in §9).

The platform below is a normalized SQLite database (PostgreSQL-migration-clean) intended to
become the *structured* backbone for that record. It does not replace prose reasoning — hypothesis
narratives, adversarial debate, economic mechanism arguments stay as text, because that is exactly
the content the charter values most and a relational schema would flatten it. It replaces the
*bookkeeping*: which experiment changed which parameter, what its metrics were, what it was
compared against, what the verdict was, and how that connects across a decade of research.

## 1. Adversarial review of the spec itself (Codex role — applied before any schema, not after)

The user's directive is comprehensive and internally strong, but two of its own framings need to
be challenged before they get built into the schema, and the charter's own instruction — "the
reviewing role must actively try to reject the proposal," "must not simply implement the proposal
as given" — applies to a request from the user exactly as much as it applies to a request from the
other AI role.

**1.1 — "No manual tracking should be required" is not achievable, and promising it would be the
charter's forbidden "inventing certainty."** Splitting every field this platform wants to capture
by who can actually populate it:

| Auto-capturable today (git, gate script, operational DB) | Auto-capturable after one small script change | Inherently manual (an AI role or the user writes it) |
|---|---|---|
| Commit hash, author, files changed, diff stat | Gate TRAIN/TEST/stress/OOS metrics (gate currently prints, doesn't persist — §8.1 closes this) | Hypothesis statement, economic reasoning |
| Branch name, timestamp | Config parameter values actually in effect for a run | Expected failure mechanism, expected alpha mechanism |
| Operational `trades`/`positions` rows for *live* trades | Market-regime label for a date range (already computed by `detect_regime()`, just needs to be logged per-run) | Lessons learned, future work |
| — | — | Verdict (APPROVE/REJECT/REQUEST-MORE-EVIDENCE) and its reasoning |
| — | — | Confidence score adjustment on a hypothesis |

The middle column is real work — a code change to `robustness_gate.py` to emit structured output
(§8.1) — and is a prerequisite most of this schema's value depends on. The right column stays
manual forever; no schema design changes that. §4 tags every table's `update_rule` with one of
these three tiers instead of the spec's implicit "all automatic."

**1.2 — "Existing documentation should become generated views of the database wherever practical"
is right for registries, wrong for reasoning docs.** `docs/42` (Research State Registry), `docs/43`
§9 (the ranked queue), `docs/24` (rejected-forever table), and the Feature/Parameter registries the
user asks for are genuinely structured data being hand-maintained in markdown today — good
candidates to become a `SELECT` against this database, rendered to markdown by a script. But
`docs/40` (signal-level attribution — a 6-page argument with counter-examples), `docs/38` (a
verdict built on a specific blind-test design), and this document itself are *arguments*, not
records. Forcing them into rows would either truncate the reasoning or produce a `notes TEXT`
column doing all the work, which is not "structured" — it's just a database with worse editing
tools than markdown. **Decision**: registries and indexes generate from the DB; hypothesis/
decision/evidence *narratives* live as prose (in a `notes`/`reasoning` text column or, for anything
over a few paragraphs, a linked `docs/NN.md`), and the DB row for that hypothesis/decision holds a
foreign-key-shaped pointer plus the structured fields (status, confidence, dates, linked
experiments) that make it queryable. This is explicitly a rejection of the literal instruction —
stated here, not smoothed over, per the charter's "do not defend a weakened idea... do not praise
ideas... say so."

**1.3 — Building all ~25 tables before anything populates them repeats a pattern this project has
already flagged as a bug once.** `docs/47` codifies "simple over complex" and doc 45/46's own
lesson is that structure nobody actively re-verifies decays into silent incompleteness (cron jobs
undocumented in doc 41; results undocumented in doc 42) — the failure mode isn't "we had too few
tables," it's "the record-keeping wasn't kept current." A 25-table schema with 24 empty tables
because nothing feeds them yet is a more elaborate version of the same drift, and it directly
contradicts the charter's own "simple over complex, reusable over custom" engineering principle.
This is the stated tension the user's request creates against the existing charter, and it is
resolved in §2, not hidden: **the full target schema is designed and documented completely here
(the user's explicit ask), but §9's phased plan builds it in dependency order, starting from the
subset that a single script change makes immediately self-populating, and defers any table with no
identified data source to "designed, not built" status until one exists.**

**1.4 — Priority-queue conflict, named not silently overridden.** `docs/47` §1.3: "new-lever
research is explicitly lower priority than fidelity/validity work." This platform is tooling, not
a new lever, so §1.3 doesn't strictly gate it — but the P0 `data/universe.py` gitignore bug, the
two uncommitted files (`db/universe_repo.py`, `portfolio/manager.py`), the 11 untracked `docs/`
files, and the `0580cd5` incident doc are all still open from before this request. Building a
research database does not fix any of them, and a database that records "clean" history while the
underlying repo has known reproducibility holes would be actively misleading — a `research.db` row
saying an experiment's commit is reproducible is false while `data/universe.py` breaks a clean
clone. **This is flagged, not resolved, here**: §9's Phase 0 explicitly gates any Phase 1 code
work on the P0 items being closed first, because a Repository Integrity role that ignores its own
open P0 to go build a new database would be self-contradictory.

## 2. Design principles (how the tension in §1.3 gets resolved)

- **Design for the target size** (the user's explicit ask — "think like the CTO... design for 1000
  experiments, 100 strategy versions, 10 years"), but **build in dependency order from what can
  self-populate today**, not by table count. A table earns Phase-1 construction by having a named,
  automatic-or-cheap capture source (§3); everything else is fully specified here and built when
  its source exists.
- **Every experiment is a diff against a named baseline, not a freestanding record.** This is the
  mechanism the causal-attribution mandate (the user's closing paragraph) actually depends on —
  detailed in §5. Without it, "why did performance change" is unanswerable regardless of how many
  tables exist.
- **SQLite now, schema written Postgres-clean**: no SQLite-only pragmas relied on for correctness,
  `TEXT` for dates in ISO-8601 (sorts correctly in both), explicit `FOREIGN KEY` declarations with
  `PRAGMA foreign_keys = ON`, surrogate integer primary keys everywhere (not composite), JSON
  stored as `TEXT` with an application-layer schema (Postgres `JSONB` swap-in later is then a
  column-type change, not a redesign). Physically separate database file (`research.db`) and
  separate `RESEARCH_DB_PATH`/`get_research_connection()`, mirroring the existing
  `DB_PATH`/`get_connection()` convention in `db/repository.py` — never the same file as the
  live-trading `db/schema.sql` database, so a research-DB bug cannot touch live trading state.

## 3. Data source mapping (every table checked against a real source before being designed)

| Table (§4) | Real capture source | Phase |
|---|---|---|
| `experiments` | git commit + gate run + AI role (manual verdict fields) | 1 |
| `parameter_deltas` | diff of resolved config between candidate run and its `baseline_experiment_id`'s config | 1 |
| `performance_metrics` | `robustness_gate.py` output, once it emits structured JSON (§8.1) | 1 |
| `evidence_ledger` | derived from `performance_metrics` + `docs/47` §3 checklist, written at verdict time | 1 |
| `research_decisions` | AI-role verdict, written manually at decision time (this is `docs/47` §2.1's requirement, now with a queryable home) | 1 |
| `research_hypotheses` | manual entry at proposal time; confidence updated by trigger/script when a linked experiment resolves | 2 |
| `market_context` | `detect_regime()` output, logged per gate run once §8.1 lands | 2 |
| `strategy_versions` | git tag/commit range per named era (e.g. "PURE_RS", "FULL") — mostly backfilled once from `docs/42`, then appended manually at each named-era boundary | 2 |
| `feature_registry` | rollup query over `experiments`/`research_decisions` — generated, not entered | 2 (view) |
| `trade_attribution` (live) | join against operational `db/schema.sql` `trades`/`positions` — **has a real source today** | 2 |
| `trade_attribution` (backtest) | **no source exists** — confirmed by reading `backtest/engine.py`: trades accumulate in-memory (`result.trades`, a `List[Trade]`) and are never persisted; a backtest run today produces zero durable trade-level record. Needs a new `--dump-trades` path in the engine before this table can populate from backtests. | 3 (blocked on engine change) |
| `research_debt` | manual entry, reviewed periodically | 3 |
| `technical_debt` | manual entry; could partially seed from `docs/44`/`docs/45` open-items lists | 3 |
| `ai_reviews` | manual entry per `docs/47` §5.2 verdict (this is the same fact as `research_decisions`.reasoning — see §4 note, folded together to avoid a duplicate-source table) | 1 |
| `dependencies` | no real source identified; doc 45 found the dependency graph itself is incomplete (dynamic `importlib`, 30+ ad hoc `os.environ` reads) — **flagged aspirational, not built in any phase until doc 41 is extended per doc 45's own recommendation** | deferred |
| `open_questions` | manual entry; could seed from `docs/43` §9 queue once | 3 |

Tables the original spec named that collapse into others on inspection, to avoid the
"25 tables, several with no distinct source or query" trap §1.3 warns against:

- **Strategy Versions / Portfolio Rules / Universe Rules / Risk Management / Position Sizing /
  Sector Logic / Market Regime rules / Fundamental Filters** (8 named categories in the spec) are
  not independent tables — they are all *slices of the same thing*: "what was the resolved config
  at commit X." One `strategy_config_snapshot` table (config key/value pairs tied to a commit,
  tagged with a `category` enum matching this list) answers "what was the entry logic at commit X"
  and "what was the sizing logic at commit X" with the same table and a `WHERE category=`, instead
  of eight tables that would each need the same commit-linkage logic duplicated eight times. This
  is a direct instance of "reusable over custom" from the charter, applied to a place the literal
  spec would have produced eight near-identical tables.
- **AI Reviews** folds into `research_decisions.reasoning` (§4) rather than a separate table —
  the spec's "Claude review / Codex review / agreements / disagreements / final decision" is one
  row's worth of structured+prose content, not a many-to-one relationship to anything else.
- **Implementation History** is `experiments` joined to git — no separate table; `experiments.
  commit_hash` already is that link.
- **Paper Trading Results / Live Trading Results** — these are `performance_metrics` rows with
  `source = 'paper'` / `source = 'live'` rather than separate tables, since every column they'd
  need (P&L, drawdown, regime, dates) is identical to a backtest metrics row; only the `source`
  and `experiment_id` linkage differ.

This brings the ~25-table spec down to **13 real tables + 1 generated view**, each with an
identified source — smaller than the literal spec, not because the target is smaller, but because
several of the named 25 were the same table under different names.

## 4. Schema (target architecture — Phase tag per §3)

Convention for every table below: `Purpose`, `Schema`, `Relationships`, `Indexes`, `Update rule`
(auto / semi-auto / manual, per §1.1), `Version history` (all tables get a `created_at` and, where
mutable post-insert, an `updated_at`; nothing is hard-deleted — status/superseded_by columns
instead, matching the charter's "do not delete rejected-hypothesis docs").

### 4.1 `experiments` (Phase 1 — the spine)

Purpose: one row per research run (gate run, backtest run, or live-behavior commit) — the anchor
every other table links to.

```sql
CREATE TABLE experiments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                TEXT    NOT NULL UNIQUE,   -- e.g. 'sector_rs_weight_20260731'
    title               TEXT    NOT NULL,
    author_role         TEXT    NOT NULL,           -- 'claude' | 'codex' | 'joint'
    commit_hash         TEXT,                       -- NULL only for pre-commit design docs
    branch              TEXT,
    docs_nn_path        TEXT,                       -- e.g. 'docs/40_Signal_Level_...md', NULL if none yet (a gap, not hidden)
    baseline_experiment_id INTEGER REFERENCES experiments(id),  -- NULL only for the first-ever baseline
    hypothesis_id       INTEGER REFERENCES research_hypotheses(id),
    strategy_version_id INTEGER REFERENCES strategy_config_snapshot(id),
    status              TEXT    NOT NULL DEFAULT 'PROPOSED',  -- PROPOSED|RUNNING|DECIDED|STALE
    runtime_ms          INTEGER,                       -- wall-clock cost of the run this row records
    peak_mem_mb         REAL,                           -- peak RSS during the run, if captured
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    decided_at          TEXT
);
CREATE INDEX idx_experiments_baseline ON experiments(baseline_experiment_id);
CREATE INDEX idx_experiments_commit   ON experiments(commit_hash);
CREATE INDEX idx_experiments_status   ON experiments(status);
```

Relationships: parent to `parameter_deltas`, `performance_metrics`, `evidence_ledger`,
`research_decisions`, `trade_attribution`; self-referential via `baseline_experiment_id` (this is
the field §5 depends on).

Update rule: **semi-auto**. `slug`/`commit_hash`/`branch`/`created_at` auto-filled by the capture
script (§8.1) at gate-run time; `title`/`hypothesis_id`/`docs_nn_path` filled manually when the
researcher registers the run; `status`/`decided_at` set manually at verdict time. `runtime_ms`/
`peak_mem_mb` are auto-filled by the capture script wrapping the gate run in `time.perf_counter()`/
`resource.getrusage(RUSAGE_SELF).ru_maxrss` — satisfies the spec's explicit "runtime impact" /
"memory impact" requirement and the charter's "benchmarked... runtime cost, memory cost" line,
neither of which the earlier draft of this schema had a column for.

### 4.2 `parameter_deltas` (Phase 1)

Purpose: the config diff between an experiment and its baseline — the field the causal-attribution
mandate cannot function without (§5). Not a snapshot of all config (that's
`strategy_config_snapshot`, §4.9) — only what *changed*.

```sql
CREATE TABLE parameter_deltas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   INTEGER NOT NULL REFERENCES experiments(id),
    param_key       TEXT    NOT NULL,     -- e.g. 'SECTOR_RS_WEIGHT'
    baseline_value  TEXT,                 -- NULL = param newly introduced
    candidate_value TEXT,                 -- NULL = param removed
    attribution_dimension TEXT NOT NULL   -- see §5 enum
);
CREATE INDEX idx_param_deltas_experiment ON parameter_deltas(experiment_id);
CREATE INDEX idx_param_deltas_key        ON parameter_deltas(param_key);
```

Update rule: auto — computed by diffing resolved config (baseline vs candidate) at gate-run time.
`attribution_dimension` is the one semi-manual field: a lookup table (`param_key` →
`attribution_dimension`) seeded once and maintained as new parameters appear, not decided per-run.

### 4.3 `performance_metrics` (Phase 1)

Purpose: TRAIN/TEST/stress/OOS/paper/live numbers for an experiment — replaces gate stdout as the
durable record.

```sql
CREATE TABLE performance_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   INTEGER NOT NULL REFERENCES experiments(id),
    source          TEXT    NOT NULL,   -- 'train'|'test'|'full'|'stress_crash_v_recovery'|'stress_chop'|... |'paper'|'live'
    cagr            REAL, sharpe REAL, max_drawdown_pct REAL,
    total_trades    INTEGER, win_rate REAL,
    effective_n     INTEGER,             -- distinct symbols/episodes, per docs/47 §3.2 — NOT row count
    p_value         REAL,
    window_start    TEXT, window_end TEXT,
    recorded_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_metrics_experiment ON performance_metrics(experiment_id);
CREATE INDEX idx_metrics_source     ON performance_metrics(source);
```

`effective_n` is a required column, not optional — `docs/47` §3.2/§4.1 make this a mandatory check
for any claim; making it a column instead of a checklist item is this schema's actual enforcement
of that rule (a query can now find every metrics row with `effective_n IS NULL` and flag it, which
a markdown checklist cannot do).

Update rule: auto, from gate output once §8.1 lands.

### 4.4 `evidence_ledger` (Phase 1)

Purpose: whether an experiment satisfies each of `docs/47` §3's six evidence-standard checks —
makes "is this durably proven" a query, not a re-read of six paragraphs.

```sql
CREATE TABLE evidence_ledger (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id       INTEGER NOT NULL REFERENCES experiments(id),
    has_economic_reasoning     INTEGER NOT NULL DEFAULT 0,  -- 0/1
    effective_n_checked         INTEGER NOT NULL DEFAULT 0,
    train_and_test_reported     INTEGER NOT NULL DEFAULT 0,
    stress_tested                INTEGER NOT NULL DEFAULT 0,
    config_parity_confirmed      INTEGER NOT NULL DEFAULT 0,
    backtest_live_parity_confirmed INTEGER NOT NULL DEFAULT 0,
    independently_rederived      INTEGER NOT NULL DEFAULT 0,  -- docs/47 §4.2 "single-study-syndrome"
    notes               TEXT
);
```

Update rule: semi-auto — the boolean flags that a script can check (train+test both present,
stress rows present, config parity via the diff in `parameter_deltas` being non-empty) are set
automatically from `performance_metrics`/`parameter_deltas`; `has_economic_reasoning` and
`backtest_live_parity_confirmed` are manual (they require judgment, not just presence of a row).

### 4.5 `research_decisions` (Phase 1)

Purpose: the APPROVE/REJECT/REQUEST-MORE-EVIDENCE verdict `docs/47` §2.1/§5.2 requires to be
written down — folds in the "AI Reviews" content from the original spec (§3 note) since it's the
same fact.

```sql
CREATE TABLE research_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   INTEGER NOT NULL REFERENCES experiments(id),
    proposing_role  TEXT NOT NULL,   -- 'claude'|'codex'
    reviewing_role  TEXT NOT NULL,
    verdict         TEXT NOT NULL,   -- 'APPROVE'|'REJECT'|'REQUEST_MORE_EVIDENCE'
    agreements      TEXT,
    disagreements   TEXT,
    open_concerns   TEXT,
    reasoning       TEXT NOT NULL,   -- prose; this is where the argument lives, not flattened
    decided_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Update rule: **fully manual** — this is the field §1.1 is explicit cannot be automated, and
shouldn't be pretended otherwise.

### 4.6 `research_hypotheses` (Phase 2)

```sql
CREATE TABLE research_hypotheses (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    statement                 TEXT NOT NULL,
    economic_reasoning        TEXT NOT NULL,  -- why alpha should exist + why the market hasn't arbitraged it away
    expected_alpha_mechanism  TEXT NOT NULL,  -- the specific market behavior that creates the edge (charter Q1/Q2)
    expected_failure_mechanism TEXT NOT NULL, -- how this could fail + what evidence would reject it (charter Q5)
    status              TEXT NOT NULL DEFAULT 'UNRESOLVED',  -- ACCEPTED|REJECTED|UNRESOLVED|NEEDS_MORE_EVIDENCE
    confidence_score    REAL NOT NULL DEFAULT 0.5,           -- 0-1, adjusted per §4.6 note below
    superseded_by        INTEGER REFERENCES research_hypotheses(id),
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT
);
```

`economic_reasoning`/`expected_alpha_mechanism`/`expected_failure_mechanism` are split into three
`NOT NULL` columns rather than one free-text blob because the charter (and `CLAUDE.md` "Every
feature must answer") requires each of these as a distinct, separately-checkable question — a
hypothesis missing an explicit failure mechanism should fail a schema constraint, not just be a
weaker paragraph inside a wall of text. The earlier draft of this schema only had
`economic_reasoning` and silently dropped the other two, which is exactly the kind of spec gap
this document's own §1 adversarial review is supposed to catch.

Confidence-update rule (the user's "every new experiment automatically updates the confidence"):
**semi-auto, bounded**. A script bumps `confidence_score` by a fixed step (e.g. ±0.1) toward 0/1
whenever a new `experiments` row links to this `hypothesis_id` and its `research_decisions.verdict`
resolves — automatic *movement*, but the step size and any override stay a reviewable, logged
change (a `confidence_history` audit row per update, not a silent overwrite), because an
unreviewed auto-updated confidence score is exactly the kind of "inventing certainty" the charter
forbids if nobody can see why it moved.

### 4.7 `market_context` (Phase 2)

```sql
CREATE TABLE market_context (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   INTEGER NOT NULL REFERENCES experiments(id),
    window_start TEXT, window_end TEXT,
    regime_label    TEXT,   -- output of detect_regime(), logged not re-derived
    pct_days_bull REAL, pct_days_bear REAL, pct_days_chop REAL,
    breadth_avg     REAL
);
```

Update rule: auto, once the gate script logs `detect_regime()` output per window (§8.1).

### 4.8 `trade_attribution` (Phase 2 for live, Phase 3-blocked for backtest per §3)

```sql
CREATE TABLE trade_attribution (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id       INTEGER NOT NULL REFERENCES experiments(id),
    source              TEXT NOT NULL,   -- 'live' | 'backtest'
    operational_trade_id INTEGER,        -- FK into the OTHER db's trades.id, live-only (cross-DB, not enforced by FK — see note)
    symbol TEXT, sector TEXT,
    entry_reason TEXT, exit_reason TEXT,
    hold_days INTEGER, is_winner INTEGER,
    mfe REAL, mae REAL,
    regime_at_entry TEXT,
    candidate_score REAL, replacement_score REAL,
    expected_alpha REAL, realized_alpha REAL
);
```

Note: `operational_trade_id` cannot be a real SQL foreign key — it points into the separate
live-trading database file, not this one (§2's physical-separation rule). It's a logical reference,
validated by an application-layer join, not the database engine. This is called out explicitly
rather than left as a silently-broken-looking FK.

### 4.9 `strategy_config_snapshot` (Phase 2 — replaces the 8-table split, §3)

```sql
CREATE TABLE strategy_config_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    commit_hash     TEXT NOT NULL,
    category        TEXT NOT NULL,  -- entry|exit|ranking|portfolio_construction|universe|risk|sizing|sector|regime|fundamental
    config_key      TEXT NOT NULL,
    config_value    TEXT NOT NULL,
    UNIQUE(commit_hash, config_key)
);
CREATE INDEX idx_snapshot_commit   ON strategy_config_snapshot(commit_hash);
CREATE INDEX idx_snapshot_category ON strategy_config_snapshot(category);
```

Update rule: auto — dump resolved config at any commit a gate run touches; append-only.

### 4.10 `feature_registry` (Phase 2, **generated view**, not a stored table)

```sql
CREATE VIEW feature_registry AS
SELECT pd.param_key,
       COUNT(DISTINCT e.id)                                   AS times_tested,
       SUM(rd.verdict = 'APPROVE')                            AS times_accepted,
       SUM(rd.verdict = 'REJECT')                             AS times_rejected,
       AVG(pm_test.cagr)                                      AS avg_test_cagr,
       AVG(pm_test.sharpe)                                    AS avg_test_sharpe,
       AVG(pm_stress.max_drawdown_pct)                        AS avg_stress_mdd
FROM parameter_deltas pd
JOIN experiments e        ON e.id = pd.experiment_id
LEFT JOIN research_decisions rd ON rd.experiment_id = e.id
LEFT JOIN performance_metrics pm_test   ON pm_test.experiment_id = e.id AND pm_test.source = 'test'
LEFT JOIN performance_metrics pm_stress ON pm_stress.experiment_id = e.id AND pm_stress.source LIKE 'stress_%'
GROUP BY pd.param_key;
```

This is the concrete answer to "which parameters consistently help/hurt" — a `SELECT` against
rows that already exist for another reason, not a table someone has to remember to update.

### 4.11 `research_debt`, `technical_debt`, `open_questions` (Phase 3)

Shared shape (three tables, one pattern — kept separate because their owners and review cadence
genuinely differ, unlike §3's collapsed eight):

```sql
CREATE TABLE research_debt (   -- technical_debt, open_questions: same shape
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    title               TEXT NOT NULL,
    description         TEXT NOT NULL,
    business_impact     TEXT,
    priority            TEXT NOT NULL DEFAULT 'MEDIUM',  -- LOW|MEDIUM|HIGH|P0
    blocked_experiment_ids TEXT,   -- JSON array of experiments.id
    owner               TEXT,
    estimated_effort    TEXT,
    status              TEXT NOT NULL DEFAULT 'OPEN',   -- OPEN|CLOSED|WONT_FIX
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at           TEXT
);
```

Update rule: manual — these are judgment calls by design, same as `docs/43` §9's queue is today.

## 5. Attribution design (the causal-inference mandate — the actual deliverable)

The closing instruction — decompose a performance change into stock selection / universe /
ranking / portfolio construction / risk management / entry timing / exit timing / regime / sector
/ execution / random variation — is only answerable if every `parameter_deltas` row is tagged with
which of those dimensions it belongs to, **and** every experiment changes exactly one dimension at
a time whenever a clean attribution is wanted.

**Correction from the earlier draft**: `random_variation` is not a peer of the other ten values.
The other ten answer "what did you change" (a property of the parameter). `random_variation` answers
"was the observed effect real," which is a property of the *evidence* — already captured by
`performance_metrics.p_value` / `effective_n` — not of the parameter itself. Folding it into the
same enum forces a false choice: a change tagged `entry_timing` whose effect turns out to be noise
has nowhere to record that. The two axes are kept separate:

```
attribution_dimension ENUM (enforced via CHECK constraint, not free text; parameter_deltas only):
  'stock_selection' | 'universe_construction' | 'ranking' | 'portfolio_construction'
  | 'risk_management' | 'entry_timing' | 'exit_timing' | 'regime' | 'sector'
  | 'execution_assumptions'
```

"Real vs. noise" is derived at query time from `performance_metrics` (`p_value`, `effective_n`,
stress-window agreement per `docs/47` §3), never stored as a tag on the dimension itself — a change
is reported as e.g. "changed `entry_timing`, effect not distinguishable from noise (p=0.41)", not
mis-filed under a `random_variation` dimension as if noise were something you could deliberately
change.

The `param_key → attribution_dimension` mapping is a small lookup table, not an inline column
filled ad hoc per row (an inline column invites two rows for the same `param_key` getting tagged
inconsistently by different researchers):

```sql
CREATE TABLE param_attribution_map (
    param_key             TEXT PRIMARY KEY,
    attribution_dimension TEXT NOT NULL CHECK (attribution_dimension IN (
        'stock_selection','universe_construction','ranking','portfolio_construction',
        'risk_management','entry_timing','exit_timing','regime','sector',
        'execution_assumptions')),
    notes                 TEXT
);
```

Seeded once (manual, ~50-100 known config keys from `config/settings.py` + `risk_config.yaml`),
extended whenever a genuinely new parameter is introduced (semi-auto: ingest script flags any
`param_key` in an incoming run with no map entry rather than silently leaving
`parameter_deltas.attribution_dimension` null). `parameter_deltas.attribution_dimension` is filled
by joining against this table at ingest time, not typed freehand per experiment.

Query shape this enables (illustrative, not exhaustive):

```sql
-- "Which dimension most often explains a TEST-window CAGR improvement?"
SELECT pd.attribution_dimension,
       COUNT(*)                              AS n_experiments,
       AVG(pm.cagr - base_pm.cagr)           AS avg_cagr_delta
FROM experiments e
JOIN parameter_deltas pd      ON pd.experiment_id = e.id
JOIN performance_metrics pm   ON pm.experiment_id = e.id AND pm.source = 'test'
JOIN performance_metrics base_pm ON base_pm.experiment_id = e.baseline_experiment_id AND base_pm.source = 'test'
GROUP BY pd.attribution_dimension
ORDER BY avg_cagr_delta DESC;
```

**Honest limitation, stated per the charter's "never invent certainty":** this only produces clean
per-dimension attribution when an experiment's `parameter_deltas` touch a single dimension. A
multi-dimension commit (this project has already logged this exact failure once — `f2238b8`
bundling a new lever with an unrelated live-wiring fix, `docs/47` §2.2) produces rows where the
attribution is ambiguous by construction, not by a schema flaw. The mitigation is `docs/47` §2.2's
existing rule (no bundled commits) doing double duty — it's now also a data-quality requirement for
this table, not just a git-hygiene one. A `is_multi_dimension` flag (auto-computed: `COUNT(DISTINCT
attribution_dimension) > 1` per experiment) should be surfaced in `feature_registry` and excluded
from clean-attribution queries by default, so contaminated experiments visibly don't get to vote.

## 6. Analytics layer

The layer is the set of parameterized queries in §4.10 and §5, plus:

- "Which strategy versions survive crash stress" → join `strategy_config_snapshot` (by commit
  range = version) to `performance_metrics WHERE source LIKE 'stress_crash%'`.
- "Which exit rules fail most" → `trade_attribution GROUP BY exit_reason, is_winner` once §4.8's
  backtest-side capture exists (currently live-only, per §3).
- "Has this been tested before" → `SELECT * FROM experiments WHERE hypothesis_id = ?` plus a
  full-text match against `research_hypotheses.statement` (SQLite FTS5 virtual table — Postgres
  equivalent is `tsvector`, both supported, no redesign needed at migration time).

No new tables needed for the analytics layer — this is intentional; an analytics layer that needs
its own storage on top of the spine tables would be evidence the spine is under-designed.

## 7. Dashboards

Per §1.2: dashboards that are pure rollups generate from SQL (`feature_registry`,
`Acceptance Rate`, `Research Debt`, `Open Questions`, `Rejected Ideas` — all `SELECT`s or thin
Python scripts rendering one to markdown/HTML). Dashboards the spec named that are actually
narrative synthesis (`Research Summary`, `Future Roadmap`) stay AI-role-written documents that
*cite* query output, same relationship `docs/43` already has to the raw commit log.

## 8. Automatic rules — what's real, what's a prerequisite

**8.1 — Prerequisite, not yet built**: `robustness_gate.py` must emit one JSON file per run
(candidate config, baseline config, all metrics rows, regime breakdown) to a
`research_runs/<slug>.json` directory. This is the single piece of new code this design depends on
— every "auto" tag in §4 assumes this exists. Until it does, Phase 1 tables populate by a manual
one-time backfill script reading `docs/38-40`'s reported numbers, not from live gate runs.

**8.2 — Ingest script**: a `scripts/research_db_ingest.py` reads a `research_runs/*.json` file,
inserts into `experiments`/`parameter_deltas`/`performance_metrics`/`market_context`, and computes
`evidence_ledger`'s auto-fillable flags. Run manually after each gate run (or as a post-gate hook)
— not silently automatic, so a bad run doesn't self-insert without the researcher noticing.

**8.3 — What stays manual forever, restated**: hypothesis text, economic reasoning, verdict,
reasoning, lessons learned, research/technical debt entries. No amount of tooling changes this —
§1.1 already said so; repeating it here because the user's "no manual tracking should be required"
line is the one part of the spec this design does not honor as written, and that needs to stay
visible, not buried in a table footnote.

## 9. Phased migration plan

**Phase 0 (blocking, before any Phase 1 code)**: close the open Repository Integrity items this
design would otherwise sit alongside dishonestly — commit the 11 untracked `docs/` files +
`CLAUDE.md`, fix the `data/universe.py` P0 gitignore bug, resolve the two uncommitted working-tree
files. Per §1.4, this is a hard gate, not a suggestion — building a "source of truth" database next
to known reproducibility holes undermines the platform's own purpose on day one.

**Phase 1** (spine, Phase-1-tagged tables in §3/§4): `experiments`, `parameter_deltas`,
`performance_metrics`, `evidence_ledger`, `research_decisions`, plus §8.1's gate-output-JSON change
and §8.2's ingest script. Backfill: one-time manual entry of `docs/35-47` (13 documents, already
read this session) as `experiments` rows with their known metrics — this is bounded, cheap, and
immediately makes `feature_registry` non-empty. Do **not** attempt to backfill `docs/01-34`;
those predate the fidelity fixes (`docs/24`'s rejected-forever table, the charges-model fix) and
backfilling stale numbers as if current would violate `docs/47` §8.1's stale-labeling rule on
arrival.

**Phase 2**: `research_hypotheses`, `market_context`, `trade_attribution` (live side only),
`strategy_config_snapshot`, `feature_registry` view. Backfill hypotheses from `docs/42`'s existing
"Proven/durable" section (already curated) plus `docs/24`'s rejected-forever table.

**Phase 3**: `research_debt`/`technical_debt`/`open_questions` (seed from `docs/43` §9 and `docs/
44/45`'s open-items lists), `trade_attribution` backtest side — **blocked** until
`backtest/engine.py` gets a trade-dump path (a real code change, out of scope for this design doc,
flagged for a future proposal of its own, subject to the same APPROVE/REJECT process).

**Deferred indefinitely**: `dependencies` table (§3 — no reliable source until doc 41's dynamic-
import gap from doc 45 Finding 3 is closed).

## 10. Verdict (per `docs/47` §2.1 — written here, not left in chat)

**Proposing role (Claude)**: architecture above satisfies the user's request for a complete,
scalable design while resolving three internal contradictions the literal spec contained (§1.1-1.3)
rather than silently implementing a promise ("zero manual tracking," "all docs become views") that
doesn't hold up.

**Reviewing role (Codex)**: the design is sound and each table traces to a real or explicitly-
deferred source (§3) — the thing doc 45/46 found missing elsewhere in this repo (structure with no
verification path) has been designed against directly. Two residual risks, not blocking:
(a) `attribution_dimension` tagging is only as honest as commit hygiene — a repeat of `f2238b8`'s
bundling silently degrades the analytics layer's core promise rather than erroring loudly; consider
a CI check that rejects multi-file commits touching more than one `attribution_dimension`'s files
as a future hardening step, not required for this design to be approved. (b) Phase 1's backfill
from `docs/35-47` is manual data entry and could introduce transcription error versus the source
docs; the ingest should diff its own output against the source doc's reported numbers before being
trusted, not assumed correct because it's now "in a database."

**Verdict: APPROVE the architecture (this document) as the target design. REQUEST MORE EVIDENCE
before Phase 1 code begins** — specifically, confirmation that Phase 0 (§9) is closed, since
building this on top of known-open P0 reproducibility gaps would repeat the exact failure pattern
(`docs/44-46`) this platform exists to stop happening again.

**User approval (2026-08-03)**: user approved this document together with its addendum
(`docs/49`, which amends §5's `param_attribution_map` → `param_taxonomy` and adds
`strategy_family`/`research_questions`/`daily_strategy_state`/two column fixes) as the Research
Intelligence Platform architecture. Written record of approval per `docs/47` §2.1 — see
`docs/49`'s own "User approval" note for full scope. Phase 1 code start still gated on Phase 0
closure per the REQUEST MORE EVIDENCE above; approval covers the design, not a green light to
start building yet.
