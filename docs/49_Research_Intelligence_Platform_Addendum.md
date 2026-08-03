# Doc 49 — Research Intelligence Platform: Addendum (Strategy DNA, Alpha Sources, Questions, Daily State)

**Date**: 2026-08-03. Addendum to `docs/48_Research_Intelligence_Platform_Design.md` — does not
replace it. Written in response to user review of doc 48 that identified four real structural
gaps and one scope risk. This document covers only those four additions, per the user's own
instruction to keep this "just these enhancements," not a rewrite.

**Supersedes within doc 48**: §5's `param_attribution_map` table is renamed and extended below
to `param_taxonomy` (adds an `alpha_source` column alongside the existing `attribution_dimension`
column). Everywhere doc 48 says `param_attribution_map`, read `param_taxonomy`. No other part of
doc 48 changes.

## 0. The scope risk, addressed first

The user's own closing note is the load-bearing part of their message, not a footnote: nine
consecutive docs (41-48) have been governance/architecture/platform-design with zero strategy
research since docs 35-36 (both REJECTs, 2026-07-28). That is a real pattern, not a coincidence,
and the user named the fix themselves — roughly 20% effort on infrastructure, 80% on research.

This addendum honors that by doing two things the user did not explicitly ask for but implied:

1. It stays small — four additions plus two direct fixes, not a re-architecture.
2. It explicitly **declines** the "Research Warehouse" framing's full scope (experiment → trades
   → daily positions → signals → market → universe → parameters → results, all cross-queryable).
   That is the right long-term shape and nothing here contradicts it, but most of the links in that
   chain (per-trade signal snapshots, per-day breadth/candidate-count telemetry) have no data
   source today — building them means instrumenting `backtest/engine.py` and the live runner,
   which is engineering work on the trading system, not database design. Building it now, before
   any research resumes, would repeat exactly the pattern the user flagged. §3 below scopes each
   piece to what already exists; everything else is named and deferred, not built.

Verdict on the four additions individually is APPROVE (§5). The scope decision is: adopt them,
then stop and return to research — see §6.

## 1. Strategy Family — real gap, but not `ENTRY_MODE` verbatim

The user is right that nothing today answers "which strategy family survives" — `experiments`
links to a `commit_hash`, not to a stable family identity that persists across many commits.

The natural source is `config/settings.py`'s `ENTRY_MODE` (`strategy/entry.py`,
`strategy/signals.py`), which already has 8 concrete values:

```
FULL, PURE_RS, PURE_ADX_BREAKOUT, SURVIVAL_RANK   -- deployable/candidate strategies
RANDOM_ALL, RANDOM_ELIGIBLE, REVERSE_RS, SHUFFLE_RS -- null-test control arms (scripts/entry_attribution.py)
```

**Correction to the user's framing**: `ENTRY_MODE` is not directly "strategy family" — 4 of its 8
values are randomized/reversed null-test controls used to prove signal beats random
(`entry_attribution_suite_20260709`), not investment philosophies. If `strategy_family` is seeded
1:1 from `ENTRY_MODE`, the headline query "which family survives" would rank `RANDOM_ALL` as a
family alongside `PURE_RS`. A boolean distinguishes them:

```sql
CREATE TABLE strategy_family (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,       -- 'PURE_RS', 'FULL', 'PURE_ADX_BREAKOUT', ...
    description   TEXT,
    is_control_arm INTEGER NOT NULL DEFAULT 0,  -- 1 for RANDOM_ALL/RANDOM_ELIGIBLE/REVERSE_RS/SHUFFLE_RS
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
```

`experiments.strategy_family_id INTEGER REFERENCES strategy_family(id)` — added as a new column
on the existing `experiments` table from doc 48 §4.1.

The user's example names (`BUFFET`, `SACRIFICE`, `HYBRID`, `REVERSAL`) don't currently exist in
the codebase — `REVERSAL` is presumably shorthand for `REVERSE_RS` (a control arm, per above).
Seed the table from the 8 real `ENTRY_MODE` values only; new families get added the same way new
`ENTRY_MODE` values get added — a deliberate code change, not a free-text row.

Update rule: **semi-auto**. Seeded once from the current `ENTRY_MODE` enum (manual, one-time).
`experiments.strategy_family_id` auto-filled by the capture script reading `ENTRY_MODE` from the
run's resolved config at ingest time (same mechanism as `parameter_deltas`).

## 2. Alpha Source — real, orthogonal axis; merged into one lookup table with §5 of doc 48

The user is right this is different from `attribution_dimension` (doc 48 §5). `attribution_dimension`
answers "where in the pipeline did this parameter act" (an implementation-layer question — ranking,
portfolio construction, entry timing...). `alpha_source` answers "what economic factor is this
parameter expressing" (momentum, quality, liquidity...). They're not redundant: a parameter tuning
the RS-rank threshold is simultaneously `attribution_dimension = ranking` (implementation layer)
and `alpha_source = momentum` (economic layer). Collapsing them into one enum would lose one axis;
keeping them as two separate `param_key`-keyed tables would duplicate the seeding/maintenance
work for no benefit, since every parameter needs both tags from the same source (a human reading
`config/settings.py`). One table, two columns:

```sql
-- Renamed from doc 48 §5's param_attribution_map; this table now owns both taxonomies.
CREATE TABLE param_taxonomy (
    param_key             TEXT PRIMARY KEY,
    attribution_dimension TEXT NOT NULL CHECK (attribution_dimension IN (
        'stock_selection','universe_construction','ranking','portfolio_construction',
        'risk_management','entry_timing','exit_timing','regime','sector',
        'execution_assumptions')),
    alpha_source          TEXT CHECK (alpha_source IN (
        'trend','momentum','quality','value','volatility','liquidity',
        'portfolio','risk','execution','universe','fundamental','sector','macro')),
    notes                 TEXT
);
```

`alpha_source` is nullable where `attribution_dimension` is not: some parameters (e.g. a
position-sizing cap, a max-slot count) are purely structural/portfolio-mechanics and don't map
cleanly to an economic factor. Forcing a value there would produce false precision. Honest
limitation, stated directly: several of the 13 `alpha_source` values (`value`, `quality`, `macro`)
have **zero parameters mapped to them today** — this project has never run a value or quality
factor, per `docs/42`'s registry. Seeding the enum is forward-looking (so a future value-factor
experiment has somewhere to attach), not a claim that these are active alpha sources now.

Analytics this enables directly (no new storage, per doc 48 §6's pattern):

```sql
SELECT pt.alpha_source,
       COUNT(*) AS experiments,
       ROUND(100.0 * SUM(rd.verdict = 'APPROVE') / COUNT(*), 1) AS accept_rate_pct
FROM parameter_deltas pd
JOIN param_taxonomy pt ON pt.param_key = pd.param_key
JOIN experiments e ON e.id = pd.experiment_id
JOIN research_decisions rd ON rd.experiment_id = e.id
GROUP BY pt.alpha_source;
```

This is the exact "momentum: 18 experiments, 72% acceptance, no document reading" query the user
asked for.

Update rule: same as doc 48 §5's original — semi-auto, seeded once (~50-100 keys), extended when
the ingest script flags an unmapped `param_key`.

## 3. Research Questions — the strongest of the four, kept as a distinct layer above hypotheses

This is a genuine second layer, not a duplicate of `research_hypotheses` (doc 48 §4.6). A
hypothesis is one falsifiable claim tied to specific evidence; a question is broader and can
outlive several hypotheses as they're proposed, rejected, and refined. Concrete case already in
this project's own history: the question "does sector rotation help?" has been asked three
different ways —

| Hypothesis | Result |
|---|---|
| Raw sector-momentum weighting helps (`SECTOR_RS_WEIGHT`) | REJECTED (`f2238b8`, docs/46 Finding 1) |
| Sector-durability soft score helps | Gated PASS, marginal (`sector_durability_gate_pass_20260713`) |
| Regime-gated sector weighting helps | Untested |

Under doc 48 as originally written, these are three unrelated `research_hypotheses` rows with no
way to see they're the same underlying question. `research_questions` fixes that:

```sql
CREATE TABLE research_questions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    question_text      TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'OPEN',  -- OPEN|PARTIALLY_ANSWERED|ANSWERED|ABANDONED
    resolution_summary TEXT,                          -- filled when status leaves OPEN
    opened_at          TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at          TEXT
);
```

`research_hypotheses.question_id INTEGER REFERENCES research_questions(id)` — nullable (some
hypotheses are one-off and don't belong to a longer-running question; forcing a parent would
invite fake questions created just to satisfy a NOT NULL).

Update rule: **semi-auto**. `question_text` created manually when a researcher recognizes a
pattern (as with the sector-rotation example above — this could be backfilled as one row linking
the 3 hypotheses in the table). `status` is a bounded auto-suggestion: when every linked
hypothesis reaches a terminal status (ACCEPTED/REJECTED), the ingest script flags the question as
a candidate for `ANSWERED`/`PARTIALLY_ANSWERED` — logged as a suggestion, not silently
auto-closed, same non-silent-overwrite pattern as doc 48 §4.6's `confidence_score` rule.

## 4. Daily Strategy State — real value, half-sourced today, half needs new instrumentation

This is the single highest-value addition for "why did the strategy fail between May and
September" queries doc 48's spine tables genuinely cannot answer (they're keyed by experiment, not
by calendar day). It is also the addition most at risk of overclaiming a data source that doesn't
exist, so each column below is marked with what actually produces it:

```sql
CREATE TABLE daily_strategy_state (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date          TEXT NOT NULL,           -- ISO-8601
    experiment_id        INTEGER NOT NULL REFERENCES experiments(id),
    regime              TEXT,                     -- SOURCED TODAY: strategy/regime.py detect_regime()
    cash_pct            REAL,                     -- SOURCED TODAY: engine per-day loop / portfolio state
    invested_pct        REAL,                     -- SOURCED TODAY: same
    open_positions       INTEGER,                  -- SOURCED TODAY: same
    universe_size        INTEGER,                  -- SOURCED TODAY: universe snapshot for that date
    breadth              REAL,                     -- NOT SOURCED — needs new engine instrumentation
    top_sector           TEXT,                     -- NOT SOURCED — needs new engine instrumentation
    avg_rs_rank          REAL,                     -- NOT SOURCED — needs new engine instrumentation
    avg_atr_pct          REAL,                     -- NOT SOURCED — needs new engine instrumentation
    candidate_count      INTEGER,                  -- NOT SOURCED — needs new engine instrumentation
    replacement_opportunities INTEGER,              -- NOT SOURCED — needs new engine instrumentation
    rolling_sharpe_63d   REAL,                     -- DERIVABLE from equity_curve (backtest/reporter.py) at ingest, not per-day in engine
    rolling_drawdown_pct REAL,                     -- DERIVABLE from equity_curve at ingest
    UNIQUE(trade_date, experiment_id)
);
```

Confirmed by reading the code, not assumed: `backtest/engine.py` already iterates day-by-day and
already has `regime`, cash, and position state in scope at each step (used for the equity curve
maintained per `backtest/reporter.py`'s `equity_curve`/`cash_curve` dicts, currently exported only
to CSV via `save_equity_curve()`, never persisted to a queryable table). `universe_size` for a
given date is available from the point-in-time universe snapshot already used for backtest
selection. That's five of thirteen columns free — capturing them is a small change (write the row
the engine already has in memory instead of only appending to the CSV-bound `equity_curve` dict).

The other six (`breadth`, `top_sector`, `avg_rs_rank`, `avg_atr_pct`, `candidate_count`,
`replacement_opportunities`) do not exist anywhere today — nothing in `backtest/engine.py` or the
live runner currently aggregates a cross-sectional breadth/RS/ATR average or counts candidates
seen vs. taken per day. Adding them means changing what the engine computes per day, which is a
trading-system engineering change requiring its own review (per `CLAUDE.md`'s implementation
process), not something this DB-design addendum can silently assume into existence. `rolling_sharpe_63d`/
`rolling_drawdown_pct` are computable from the equity curve alone at ingest time (no engine change
needed), so they're marked derivable rather than blocked.

**Phasing**: the 5 sourced-today columns plus the 2 derivable-at-ingest columns are Phase 1/2
(same tier as doc 48's other spine tables). The 6 unsourced columns are named in the schema now
(so the table doesn't need a breaking migration later) but left NULL until a separately-proposed
engine-instrumentation change lands — same "no source exists yet, don't build it silently" rule
doc 48 §3 already applied to backtest-side `trade_attribution`.

## 5. Trade Quality — adopted in spirit, not as literal component scores (no source exists)

Checked directly: `indicators/composite.py`'s `compute_indicators()` and
`strategy/relative_strength.py`'s `composite_rank` produce a **single scalar** (RS-rank × ATR%
band), not a decomposed 92/85/96/78/71/62/88/83-style breakdown by trend/momentum/volume/quality/
risk/sector/market. No sub-component scoring model exists in this codebase. Storing the user's
mockup as literal columns would misrepresent what the system actually computes — inventing a
scoring model to fill a database schema is backwards, and building a real multi-factor entry-score
model is a strategy-research proposal in its own right (needs a hypothesis, adversarial review,
and gate evidence per `CLAUDE.md`), not a byproduct of designing storage for it.

What *is* real and does answer "why did we buy": `compute_indicators()`'s full return dict already
contains rs_rank, composite_rank, trend/EMA alignment, ADX, RSI, MACD state, volume ratio, ATR% —
the actual inputs to the entry decision. The honest version of "trade quality" is persisting that
existing dict at entry and exit time, not a fictional score breakdown:

```sql
-- Extends doc 48 §4.8's trade_attribution table (not a new table)
ALTER TABLE trade_attribution ADD COLUMN entry_indicator_snapshot TEXT;  -- JSON dump of compute_indicators() at entry
ALTER TABLE trade_attribution ADD COLUMN exit_indicator_snapshot  TEXT;  -- JSON dump at exit
ALTER TABLE trade_attribution ADD COLUMN expected_vs_actual_pct   REAL; -- entry composite_rank-implied vs realized return, if a mapping is later derived
```

`expected_vs_actual_pct` is included as a column because it costs nothing to reserve, but there is
no current formula mapping `composite_rank` to an expected return — it stays NULL until (if ever)
that mapping is derived as its own piece of research. Flagging this rather than quietly shipping a
column that always reads NULL forever without explanation.

Same blocking status as doc 48 §3's original `trade_attribution` finding: this only applies once
backtest trades are persisted at all, which doc 48 already marked Phase 3, blocked on an
`backtest/engine.py` change. This addendum doesn't change that phase.

## 6. Decision Context — one-column fix, not a new table

The user's "decision context" concern (a REJECT during a bull market might be a PASS in a bear
market — the verdict needs the regime it was made under) is already answerable once
`daily_strategy_state` (§4) exists, but only if `research_decisions` (doc 48 §4.5) points at a
specific regime at decision time. It currently doesn't. One column closes it:

```sql
ALTER TABLE research_decisions ADD COLUMN market_context_id INTEGER REFERENCES market_context(id);
```

Filled semi-auto: the ingest script resolves the experiment's evaluation window to the
`market_context` row(s) covering it at verdict-recording time. No new table needed — this was a
missing foreign key on an existing table, not a missing concept.

## 7. What this addendum explicitly does NOT add (declined, not deferred-silently)

- **Full Research Warehouse cross-join** (signals ↔ daily positions ↔ trades ↔ market, all
  independently queryable at full granularity) — directionally correct long-term, but most of the
  links have no source yet (per §4/§5 above) and building them now is a multi-week engineering
  project with zero research output, which is exactly the imbalance the user flagged. Revisit once
  Phase 1/2 (doc 48 §9) actually has data flowing and a concrete unanswered question needs it.
- **Component entry-score model** (§5) — not built here; would need its own hypothesis + review.
- **`BUFFET`/`SACRIFICE`/`HYBRID` as named families** — no such strategies exist in the codebase
  today (§1); not fabricated into the seed data.

## 8. Adversarial review (Codex role, applied to this addendum)

- Risk: `strategy_family` seeded from `ENTRY_MODE` conflates deployable strategies with null-test
  controls if the `is_control_arm` flag is dropped or ignored in a future query. Mitigation: any
  "which family survives" dashboard query (doc 48 §7) must filter `is_control_arm = 0` by default;
  noted here so it isn't rediscovered as a bug later.
- Risk: `param_taxonomy`'s `alpha_source` enum includes 3 values (`value`, `quality`, `macro`) with
  zero current parameters — a future contributor could read the enum and assume this project has
  factor coverage it doesn't. Mitigation: §2's honest-limitation note stays in the table's
  documentation, not just this doc.
- Risk: `daily_strategy_state`'s 6 unsourced columns sitting NULL forever if the engine-
  instrumentation change never gets proposed. Mitigation: explicitly named as blocked in §4 rather
  than silently included as if sourced — same discipline as doc 48's `trade_attribution` blocker.
- Risk (self-referential): this addendum itself could become a 5th consecutive infra doc if not
  bounded. Mitigation: §9's verdict closes the design phase explicitly.

## 9. Verdict

**Proposing role (Claude)**: APPROVE all four additions (`strategy_family` with the control-arm
correction, `param_taxonomy`'s merged `alpha_source` column, `research_questions`, and
`daily_strategy_state` with its per-column source split) plus the two one-column fixes
(`experiments.strategy_family_id`, `research_decisions.market_context_id`). DECLINE the full
Research Warehouse expansion and the literal component-score `trade_attribution` columns as
scoped-out, not silently dropped (§5, §7).

**Reviewing role (Codex)**: Confirmed against actual code (`strategy/entry.py`,
`strategy/relative_strength.py`, `indicators/composite.py`, `backtest/reporter.py`,
`strategy/regime.py`) rather than assumed — no claim in this document rests on an unverified
source. Concur with APPROVE + DECLINE split above.

**User approval (2026-08-03)**: User approved doc 48 + this addendum (doc 49) together as the
Research Intelligence Platform architecture. Per `docs/47` §2.1, this written record is the
approval — not the chat message. Scope of approval: the target schema and phasing in doc 48 +
this addendum's 4 additions and 2 column fixes. Not yet approved: any Phase 1 implementation
code (schema is design-only until implemented per doc 48 §9).

**Combined verdict: APPROVE this addendum as an amendment to doc 48's target schema.** No further
schema revision is planned after this — this closes the design phase doc 48 opened. The next
action is **not** a doc 50. Per doc 48 §9 and this addendum's §0: Phase 0 (the still-open P0
Repository Integrity items — 11+ untracked docs plus this one and doc 48, the `data/universe.py`
gitignore bug, two uncommitted working-tree files, the missing `0580cd5` incident doc) closes
first, then a minimal Phase 1 build (gate JSON output + ingest script + the spine tables from doc
48 plus this addendum's `strategy_family`/`param_taxonomy`/`research_questions` — the parts with
real sources today). Research resumes in parallel with Phase 1, not after it — the platform is
infrastructure in support of research, not a prerequisite blocking it.
