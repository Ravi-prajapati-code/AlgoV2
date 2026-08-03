# Doc 53 — Phase 2 Research DB: Adversarial Review (Codex role)

**Date**: 2026-08-03
**Scope**: `db/research_schema.sql`'s Phase 2 additions (`research_questions`,
`research_hypotheses`, `confidence_history`, `market_context`,
`trade_attribution`, `strategy_config_snapshot`, `feature_registry` view,
`daily_strategy_state`, the `experiments.hypothesis_id`/
`strategy_version_id` FK activation, the `research_decisions.market_context_id`
column-add migration) and `db/research_repo.py`'s `_add_column_if_missing()`.

Per the charter's solo-session rule: this is the sequenced adversarial pass
against the Phase 2 build, run before declaring it complete — same pattern
as docs/52 for Phase 1.

## What I tried to break

### 1. `research_decisions.market_context_id` — is the ALTER migration actually idempotent?

`init_research_db()` now runs `executescript()` (all `CREATE ... IF NOT
EXISTS`) then `_add_column_if_missing()`, which checks `PRAGMA
table_info(research_decisions)` before issuing `ALTER TABLE ... ADD COLUMN`.
Tested by calling `init_research_db()` twice against the same temp DB
(`test_init_research_db_idempotent_with_alter_migration`) — no "duplicate
column" error. Also confirmed the column is queryable after a single call
(`test_research_decisions_has_market_context_id_column`). No finding.

### 2. `market_context.pct_days_chop` — does a CHOP regime actually exist?

Traced `strategy/regime.py`'s `detect_regime()` — confirmed it returns only
`"BULL"`, `"BEAR"`, or `"UNKNOWN"`. docs/48 §4.7's `pct_days_chop` column
silently assumed a third regime state that doesn't exist in this codebase
(stress-test scenario names like `prolonged_sideways_chop` are backtest
fixture labels, unrelated to `detect_regime()`'s output space). This is a
real design-vs-implementation gap in docs/48, not a coding bug — caught
before any ingest code could compute `pct_days_chop` as a fabricated
`100 - bull% - bear%` remainder. Fixed by documenting the discrepancy
inline in the schema and keeping the column NULL-only until "should CHOP be
a distinct regime, separate from UNKNOWN" is answered as its own research
question (consistent with docs/49 §0's push toward research over
infra-for-its-own-sake — this is exactly the kind of question that
rebalancing was asking for). No ingest code writes this table yet, so
nothing currently at risk of fabricating the value — but the column would
have invited it later without the comment.

### 3. `trade_attribution` — is "Phase 2, live-side" actually sourced today?

docs/48 §4.8 frames this table as the live-side counterpart with real
sources (backtest-side deferred to Phase 3). Checked `db/schema.sql`'s live
`trades` table (id, symbol, sector, entry_date, exit_date, entry_price,
exit_price, shares, gross_pnl, charges, net_pnl, exit_reason, hold_days,
slippage_pct, fill_type, regime, ml_confidence) against
`trade_attribution`'s column list. Only `symbol`, `sector`, `exit_reason`,
`hold_days`, `regime_at_entry` (from `trades.regime`) have a confirmed
existing source. `entry_reason`, `mfe`, `mae`, `candidate_score`,
`replacement_score`, `expected_alpha`, `realized_alpha` do not exist in
`trades` and are not written anywhere. Grepped `portfolio/manager.py` for
`composite_rank` (lines 267, 415-418) — it's computed at entry/replacement-
decision time but never persisted, discarded after the decision is made.
`entry_indicator_snapshot`/`exit_indicator_snapshot` (docs/49 §5) have the
same gap — `compute_indicators()` output isn't persisted per-trade. This
means most of this table's columns are, today, in the same "reserved but
unsourced" state as `daily_strategy_state`'s six flagged columns, not the
"sourced today" state docs/48 implied. Fixed by rewriting the table's
schema comment to state this explicitly per-column, rather than let the
Phase-2-implies-sourced framing stand uncorrected. No code currently writes
to `trade_attribution` at all, so there's no fabrication risk yet — but the
original comment would have misled whoever builds the ingest path next.

### 4. `experiments.hypothesis_id`/`strategy_version_id` — activating the FK on an existing table

These columns existed since Phase 1 as plain `INTEGER` with a comment
saying "Phase 2 table not yet built." Now that `research_hypotheses` and
`strategy_config_snapshot` exist, I changed the column definitions to real
`REFERENCES` clauses. Checked whether SQLite requires the referenced table
to exist at `CREATE TABLE` time for the *referencing* table — it does not;
SQLite resolves FK targets lazily, only enforcing at DML time when `PRAGMA
foreign_keys = ON` (which `get_research_connection()` sets on every
connection). Confirmed this doesn't retroactively alter any
already-materialized local `db/research.db` (SQLite doesn't support
altering column constraints in place) — that file is gitignored dev state
anyway, not a shipped artifact, so drift there is not a Repository
Integrity concern. No writer sets either column yet (confirmed by grep —
`research_db_ingest.py` never assigns them); this is inert scaffolking
activation, not a behavior change today.

### 5. `research_hypotheses`'s three-way NOT NULL split — tried to insert around it

Tried inserting a hypothesis row omitting `expected_failure_mechanism`
(`test_research_hypotheses_requires_failure_mechanism`) — raised
`IntegrityError` as expected. Confirms the charter's "every feature must
answer... what evidence would reject it" is a schema-enforced constraint
for this table, not just a documentation convention. No finding.

### 6. `confidence_history` — is it actually wired to anything, or decorative?

Confirmed no script writes this table (same check as item 4). This is
intentional per the design note ("table exists so a future updater has
somewhere to log to that isn't a silent overwrite") but worth stating
plainly: as of this commit, `research_hypotheses.confidence_score` has no
automated updater at all — the column exists, defaults to 0.5, and nothing
in this codebase changes it. A human editing it directly via `sqlite3`
today would bypass `confidence_history` entirely (no trigger enforces the
audit trail). Not fixed — adding a trigger to force all updates through an
audit log is real scope, not something to bolt on unreviewed in this pass.
Flagged as a known gap, matching the honesty standard set by the
`author_role` CHECK gap already accepted in docs/52 item 8.

### 7. `feature_registry` view — tried it against real ingested data, not just an empty DB

An empty-DB query proves the view *parses*, not that its three-way LEFT
JOIN (`parameter_deltas` → `experiments` → `research_decisions` /
`performance_metrics` twice, once filtered to `source='test'` and once to
`source LIKE 'stress_%'`) aggregates correctly. Added
`test_feature_registry_aggregates_real_ingested_data`, which runs a real
`ingest()` call (same path Phase 1 already exercises) plus a manual
`research_decisions` insert, then checks `times_tested`, `times_rejected`,
`avg_test_cagr`, `avg_test_sharpe` against the known input values. Passed
on first write — but this is exactly the kind of check that would have
caught a join-fanout bug (e.g. `performance_metrics` having 3 rows per
experiment, silently multiplying `times_tested` if joined without the
`source` filters) before it shipped silently wrong. No finding, but the
test itself is the deliverable here, not just the pass.

### 8. `strategy_config_snapshot` / `daily_strategy_state` — checked for silent scope creep

Both tables are schema-only in this commit — no capture/ingest code was
written for either (docs/48 §8.1 said "gate script should log
`detect_regime()` output... `strategy_config_snapshot` should auto-dump
resolved config," which is Python work, not done here). Confirmed neither
`scripts/robustness_gate.py` nor `research_db_ingest.py` was touched this
pass to populate them. This matches the "Optional Next Step" scope drawn at
the start of this work (schema first), but it means Phase 2 is **schema-
complete, not capture-complete** — worth stating precisely rather than
implying the tables are live.

### 9. CHECK constraints — tried invalid enum values on the two new status columns

`research_questions.status` and `trade_attribution.source` both rejected
out-of-list values (`test_research_questions_status_check_constraint`,
`test_trade_attribution_source_check_constraint`). No finding.

## What I did not try to break

- Did not write or test any capture/ingest path for `market_context`,
  `strategy_config_snapshot`, or `daily_strategy_state` — none exists yet
  (item 8). Nothing to adversarially test until that Python code is
  written as its own reviewed change.
- Did not add a DB-level trigger enforcing `confidence_history` writes on
  every `research_hypotheses.confidence_score` update (item 6) — real
  scope, deferred.

## Verdict

**APPROVE** for the schema itself — all 10 new tests pass, CHECK
constraints hold, the ALTER migration is idempotent, and the two doc-vs-
implementation gaps found this pass (CHOP regime, `trade_attribution`
sourcing) are now documented honestly inline rather than left to mislead
whoever writes the ingest code next.

**Explicitly not claiming**: Phase 2 is not "done" in the sense docs/48 §9
originally scoped it — it's the schema half only. `strategy_config_snapshot`
auto-dump, `market_context` population from gate runs, and any
`trade_attribution`/`daily_strategy_state` capture code are still
unbuilt, unauthorized-for-this-pass follow-up work, consistent with docs/49
§0's push toward spending more of this project's time on research than on
infra scaffolding that has no data flowing through it yet.

**Open, not blocking**: `confidence_history` has no enforcing trigger
(item 6); `experiments.hypothesis_id`/`strategy_version_id` have no writer
(item 4); `author_role` still has no DB-level CHECK (carried over from
docs/52, unchanged this pass).
