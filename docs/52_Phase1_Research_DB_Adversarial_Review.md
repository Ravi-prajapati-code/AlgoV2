# Doc 52 — Phase 1 Research DB: Adversarial Review (Codex role)

**Date**: 2026-08-03
**Scope**: everything built under docs/48 §9 Phase 1 — `db/research_schema.sql`,
`db/research_repo.py`, `config/settings.py`'s `RESEARCH_DB_PATH`,
`scripts/robustness_gate.py`'s `write_research_json()` and its helpers,
`scripts/research_db_ingest.py`, `scripts/backfill_historical_experiments.py`,
and `tests/test_research_db_ingest.py`.

Per the charter: "If only one AI is present in a given session, it must
still perform both the proposing role and the adversarial-review role
explicitly and in sequence — not skip straight to implementation." The
proposing pass (design docs/48+49, review/approval, then the build) ran
across several prior windows without a dedicated adversarial pass on the
*code* itself (docs/48/49 were reviewed as *designs*). This doc is that
missing pass, run after the fact against the finished Phase 1 code —
later than the charter's sequencing calls for, but done before Phase 1 is
declared closed.

## What I tried to break

### 1. Does `write_research_json()` actually run unconditionally?

Checked `main()`'s control flow (`scripts/robustness_gate.py:355-408`): both
early-exit paths (`sys.exit(1)` for no `--env` given, and for config drift)
happen *before* `write_research_json()` is called. So a run that never
reaches the arms doesn't emit a JSON file — correct, there's nothing to
record. But a run that *fails inside* `run_full_and_oos_arm` or
`run_stress_both_arms` (e.g. a crashed subprocess) also currently produces
no `research_runs/*.json`, because those calls aren't wrapped — an
uncaught exception there propagates out of `main()` before reaching
`write_research_json()`. That's arguably correct too (a half-finished gate
run shouldn't be recorded as if it completed), but it means "a
`research_runs/*.json` file exists" is not proof the underlying gate
*passed cleanly through to a verdict* in the crash-free sense — only that
it reached the verdict print without raising. Not a bug, just a boundary
worth stating precisely: `write_research_json()` is unconditional *given
that `main()` reaches it*, not unconditional across all gate invocations.

### 2. `effective_n` / `p_value` — confirmed genuinely absent, not silently defaulted

Grepped `_oos_metric_row`/`_stress_metric_row` (`robustness_gate.py:283-301`)
and `_insert_metrics` (`research_db_ingest.py`) — neither computes or writes
either column; both stay NULL end to end. Matches the stated design and
docs/47 §3.2's "NULL means not checked, not zero." No finding.

### 3. `peak_mem_mb` — real bug found and fixed this session

`write_research_json()` originally used `RUSAGE_SELF`, but every backtest
this gate runs happens inside a `subprocess.run()` child
(`out_of_sample_validator.py:52`, `stress_test_scenarios.py`) — so the
metric was measuring the orchestrator script's own (small, ~97MB) memory
footprint, not the backtest's. Fixed to `RUSAGE_CHILDREN` (commit
`1e65b25`), which reports the largest RSS among reaped children —
i.e. the actual peak backtest footprint. Confirmed via `resource` module
semantics: `RUSAGE_CHILDREN` accumulates only *terminated and waited-for*
children, which is exactly what `subprocess.run()` guarantees (it blocks
until the child exits and reaps it), so no undercounting from
still-running children.

### 4. Ingest idempotency — tried duplicate re-run, tried dry-run leakage

Ran `scripts/backfill_historical_experiments.py` twice against a fresh
`research.db`: first run inserted 8 experiment rows (4 candidates + 4
baselines), second run printed "Already ingested... Skipping." for all 4
and wrote nothing further — confirmed via row counts before/after. Ran
`--dry-run` and confirmed `conn.rollback()` leaves zero rows (the
`init_research_db()` schema-creation itself does commit, but that's
idempotent `CREATE TABLE IF NOT EXISTS` — no data rows survive a dry run).

### 5. `param_taxonomy` unmapped-key path — tried an unmapped override

Manually ran `ingest_payload()` with an override key not in
`param_taxonomy` (e.g. a typo'd param name) — confirmed `parameter_deltas`
silently skips it and the ingest still completes, printing a `WARNING`
with the exact key list. Does not silently attribute a guessed dimension.
Matches design intent (docs/48 §5).

### 6. Two-strategy-family split (docs/38 Addendum 3) — the bug this session found and fixed

Before this session's fix, `ingest()` passed one `strategy_family_id` to
both the baseline and candidate `_insert_experiment()` calls. For every
gate run this was fine (baseline and candidate are the same family, one
param differs). It was wrong for docs/38 Addendum 3, where baseline is the
live `PURE_RS` default and candidate is the old `FULL` strict-AND gate —
genuinely different families, not a parameter delta within one. Confirmed
by inspection that no test caught this before the fix (the original 13-test
suite never exercised a baseline≠candidate-family case) — this was a real
coverage gap, not just an untested edge case. Fixed
(`--baseline-strategy-family`) and added
`test_baseline_and_candidate_can_have_different_strategy_families`,
verified it fails against the pre-fix code path (both arms would resolve
to `strategy_family_id=3` / `FULL`) and passes against the fix.

### 7. docs/35's "code kept, costs nothing" claim — tried to reproduce it, couldn't

While transcribing docs/35 for the backfill, checked whether
`scripts/backtest_long_term_sleeve.py`, `strategy/long_term_selection.py`,
`portfolio/long_term_reserve.py` (the exact files docs/35 names as its
implementation) exist anywhere in git history (`git log --all` on all three
paths: zero hits) or on disk (zero hits; only stale `.pyc` cache files
remain). The doc's explicit claim — "Code kept (flagged off) rather than
reverted... leaving them in place costs nothing" — was false when written
(never committed) and is now also false on disk (deleted). This is the
same failure class docs/44/45/46 already catalogued for other files
(`data/universe.py`) but had not caught here. Appended a correction to
docs/35 itself and set `independently_rederived=0` with an explanatory note
on the backfilled `evidence_ledger` row, rather than backfilling it as a
clean, reproducible result. This is a genuine Repository Integrity finding,
not a design flaw in the Phase 1 code — flagged here because the backfill
process is what surfaced it.

### 8. Schema CHECK constraints — tried invalid enum values

Confirmed via a scratch script that inserting `attribution_dimension` or
`alpha_source` values outside the CHECK-constrained lists raises
`sqlite3.IntegrityError` immediately (not silently truncated or accepted).
`author_role` has no DB-level CHECK — only `research_db_ingest.py`'s CLI
`choices=VALID_AUTHOR_ROLES` enforces it. This means any future direct
`INSERT INTO experiments` (not through the CLI) could write an arbitrary
`author_role` string. Low severity (no code path does this today, and
`experiments.author_role` recording "who is accountable" is a soft
convention across a small number of entry points, not a hard security
boundary) — noted as **not fixed**, since docs/47 doesn't currently require
DB-level enforcement of this specific field and adding it would be scope
creep beyond what this review is auditing.

### 9. Backfill script — checked for hidden data fabrication

Every numeric value in `scripts/backfill_historical_experiments.py`'s four
payload dicts was cross-checked token-by-token against the source doc's own
table (docs/35's TRAIN/TEST/FULL table, docs/36's TEST-window + 4-scenario
table, docs/38 Addendum 3's TRAIN/TEST/FULL/stress block, docs/50's
TRAIN/TEST/FULL block) — no invented numbers. Fields the docs don't report
(`seed`, `runtime_ms`, `peak_mem_mb`, `total_trades` for stress rows) are
left `None`, not defaulted to a plausible-looking value.

## What I did not try to break

- Did not attempt a full concurrent-write / locking stress test on
  `research.db` — Phase 1 has exactly one writer (`research_db_ingest.py`,
  run manually, never a hook), so this is out of scope until something
  automated writes concurrently.
- Did not re-run the actual `robustness_gate.py` end-to-end against live
  data to re-verify docs/36/38/50's numbers independently — that's the
  `independently_rederived` gap already flagged honestly (0) in each
  backfilled row's evidence notes, not something this review pass can close
  cheaply.

## Verdict

**APPROVE**, with two items fixed during review (peak_mem_mb RUSAGE
target, baseline/candidate strategy_family split) and one Repository
Integrity finding surfaced and corrected in docs/35 rather than
propagated silently. No remaining defect found that blocks treating
Phase 1 (schema + connection + gate JSON emission + ingest + backfill) as
functionally correct and reproducible from a clean clone, *except* for the
docs/35 experiment specifically, whose non-reproducibility is now recorded
honestly in its own `evidence_ledger` row rather than hidden.

**Open, not blocking**: `author_role` has no DB-level CHECK (item 8) —
low severity, left as-is. `param_taxonomy` is seeded for exactly the 4
params this backfill needed — extending it for other historical levers as
they get their first real gate run is ongoing maintenance, not a Phase 1
gap.

Phase 1 (docs/48 §9) is complete: schema, connection helper, gate JSON
emission, ingest script, and the one-time docs/35-47 backfill (of the
subset that are real experiments) are all built, tested (124/124 passing),
and this adversarial pass is now on record. Phase 2 (`research_hypotheses`,
`market_context`, `trade_attribution` live-side, `strategy_config_snapshot`,
`feature_registry` view) remains not started and not authorized.
