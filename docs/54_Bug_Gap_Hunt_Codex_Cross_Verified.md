# Doc 54 — Repo-Wide Bug/Gap Hunt (Codex CLI + Claude Cross-Verification)

**Date**: 2026-08-04
**Trigger**: user directive — "eliminate uncertainty... find bugs and gaps... use Codex... check every place." Explicitly a find-only pass, not a fix pass; no code changed in this doc's scope.
**Method**: Codex CLI (`codex exec --sandbox read-only`, actual tool, not role-played) ran an independent full-repo review and produced 10 numbered findings. Every finding was then cross-verified line-by-line against source by Claude before being trusted, per this repo's "trust but verify" standard already applied to sub-agents this session. Three additional Claude Explore sub-agents (live path / backtest engine / universe+data layer) were launched for a second independent pass but failed mid-run on a session usage-limit error before reaching conclusions — two lower-severity leads were salvaged from their partial output and verified directly; the rest is unfinished, not negative evidence.

## Critical (live-trading correctness — silent behavior divergence from intent)

### C1. `shares_override` is set by the runner but read nowhere

- **Where set**: `runner/daily_runner.py` (5 sites, incl. lines ~707, ~711, ~747) — defensive entries, LIQUIDBEES funding sells, parking buys, rebalances all request a partial share quantity via `Signal.indicators["shares_override"]`.
- **Where ignored**: `portfolio/manager.py` — sells always submit `pos.shares` (full position), buys use ordinary slot sizing. Confirmed by grep: zero reads of `shares_override` anywhere in `portfolio/manager.py` or `backtest/engine.py`.
- **Failure scenario**: runner asks to trim 100 of a 1,000-share LIQUIDBEES holding to fund a bear-swing entry (`shares_override=100`). Manager sells all 1,000, closes the position entirely, and downstream cash/portfolio-state logic proceeds on a wrong basis. Every defensive/rebalance trim silently becomes a full exit.
- **Backtest divergence**: `backtest/engine.py` has zero references to `shares_override` — it applies rebalance quantities through a structurally separate direct-computation path. Backtest and live are not simulating the same mechanism here; historical backtest results do not reflect what live actually does on these trades.
- **Status**: CONFIRMED. Not yet fixed.

### C2. `cancel_stale_gtts()` return value is discarded at every call site

- **Contract**: `portfolio/manager.py:99-120` — returns `bool`. Docstring/comment states `False` means "unverified, treat as still there, don't proceed."
- **Call sites**: lines 261, 474, 617, 748, 825 — all 5 call the function and immediately proceed regardless of the result.
- **Failure scenario**: a stale sell GTT's cancel request times out (`False` returned). The runner sends a replacement market order anyway. The stale GTT can later trigger and sell shares no longer held, or combine with the new order into an unintended oversell/duplicate.
- **Status**: CONFIRMED. Not yet fixed.

### C3. Upstox `"partial"` fill status has no explicit mapping

- **Where**: `broker/upstox.py:488`, `_parse_order_response`'s `status_map` — keys are `complete`, `rejected`, `cancelled`, `open`, `pending` only. `.get(raw_status, OrderStatus.PENDING)` silently maps `"partial"` → `PENDING`.
- **Failure scenario**: a 100-share sell part-fills 40 before timing out. DB still shows 100 open (`PENDING`). Next sync only corrects the share count to 60 — no trade record is created for the 40 already sold, no realized P&L, no charges recorded for that fill. Equivalent buy-side behavior overstates holdings and debits cash for shares never actually bought.
- **Status**: CONFIRMED (root-cause mapping gap). Downstream `portfolio/manager.py` consequences (lines 624, 655, 832 per Codex) reasoned through but not independently re-derived line-by-line this pass — treat as PLAUSIBLE, not fully re-verified, pending the resumed live-path audit.

## High

### H1. Backtest pyramid ("add to winner") uses same-bar price, no lag, no slippage

- **Where**: `backtest/engine.py:900-921` — `raw_add_price = prices[best_pos.symbol]` and `price_at_add = round_to_tick(prices[best_pos.symbol])`, both direct same-bar closes.
- **Contrast**: normal entries/exits route through `_lagged_fill_price()` (defined and called at engine.py lines 411, 463, 548, 694, 787, 1188) which applies next-day-fill + slippage. The pyramid branch bypasses this entirely.
- **Failure scenario**: a leader triggers a pyramid add off yesterday's close; backtest fills at that stale close while live buys at today's actual market price (~14:50 IST). In a fast-rising stock this systematically overstates backtest capacity and return for every ADD_TO_WINNER trade.
- **Status**: CONFIRMED by direct read.

### H2. `main.py:137` — universe-history-unavailable fallback warns but still runs

- **Where**: `main.py:135-144`. On `UniverseHistoryUnavailable`, prints a WARNING to stderr citing docs/13 §2/§4/§10, then proceeds using today's static watchlist applied retroactively to the backtest start date.
- **Failure scenario**: a 2022–2024 backtest run before point-in-time universe tracking began (tracking starts 2026-07-06 per prior session findings) silently includes/excludes symbols based on today's survivorship, not the historical universe. The command still produces a result — the warning does not block execution.
- **Status**: CONFIRMED by direct read. Not new — same underlying look-ahead class as the already-closed `universe_expansion_lookahead_reject_20260717` finding, but this is the first time the *fallback path itself* (rather than the expansion feature) was confirmed to run to completion instead of hard-failing.

### H3. `performance_metrics` has no `profit_factor` column despite PF being a real gating input

- **Where**: `scripts/robustness_gate.py:65,257-259,287,296-298` computes PF, uses `STRESS_PF_DROP_MAX`/`OOS_TEST_TOLERANCE["pf"]` as actual pass/fail criteria, and has a self-aware comment at the JSON-emission line: `# not in performance_metrics schema; kept for full fidelity`. `db/research_schema.sql`'s `performance_metrics` CREATE TABLE (lines 75-89) has no `profit_factor`/`pf` column; `scripts/research_db_ingest.py` never persists it.
- **Failure scenario**: a candidate is rejected specifically because stress-test PF dropped too far. The research DB retains CAGR/Sharpe/MDD for that decision but not the PF numbers that actually drove the REJECT — a later reviewer re-examining `research_decisions` cannot independently confirm or re-derive the reasoning.
- **Note**: this was independently re-discovered by Claude while investigating a different question, before cross-checking Codex's list and finding it as Codex's own #6 — same bug, two independent discovery paths, not two separate bugs.
- **Status**: CONFIRMED. This is a gap in Phase 1 research-DB code built earlier this session — an honest miss in my own prior work, not just an external finding.

### H4. `baseline_value` in `parameter_deltas` is reconstructed at ingest time, not preserved from the run

- **Where**: `scripts/research_db_ingest.py:81` — docstring states plainly: `baseline_value` is the coded `os.getenv(...)` default for each overridden param, read from the *currently checked-out* source at ingest time, not stored in the original gate JSON.
- **Failure scenario**: a gate runs with `ENTRY_MODE` defaulting to `PURE_RS`. Weeks later the coded default changes. Ingesting the *old* gate JSON now records the *new* default as that historical run's baseline — silently rewriting what the experiment actually compared, even though the commit hash field is separately still correct.
- **Status**: CONFIRMED. This is a known, self-documented design tradeoff (not an oversight the author was unaware of), but the failure mode is real and the doc comment does not warn future readers of `parameter_deltas` rows that the baseline column can drift out of sync with history. Recommend either snapshotting the default into the gate JSON at run time, or adding a loud comment on the table itself (not just the ingest script) that `baseline_value` is ingest-time-relative, not run-time-relative.

## Medium

### M1. `dry_run=True` on research-DB ingest still commits schema/seed writes

- **Where**: `scripts/research_db_ingest.py` — `ingest_payload()` calls `init_research_db()` unconditionally at line 138, before the `dry_run` branch at line 183. `init_research_db()` runs `executescript()` (CREATE TABLE/INDEX/VIEW IF NOT EXISTS, plus any `INSERT OR IGNORE` seed rows in `research_schema.sql`) and commits immediately.
- **Failure scenario**: running ingest with `dry_run=True` against a fresh or older `research.db` creates all tables, applies the `market_context_id` ALTER migration, and seeds `param_taxonomy` rows — before the payload-insert transaction (which *is* correctly rolled back) ever begins. A user relying on "dry run = nothing written" is wrong about the schema/seed layer.
- **Status**: CONFIRMED by direct read of call order.

### M2. `strategy_value()` attributes 100% of broker cash to the strategy regardless of origin

- **Where**: `portfolio/manager.py` — `self.cash` is synced directly from `self.broker.get_available_cash()` (lines 136, 764) with no origin split, while manual/imported *positions* are explicitly excluded from strategy value elsewhere in the same function (lines ~232-256).
- **Failure scenario**: an operator deposits cash for an unrelated purpose, or sells a manual holding. The full broker cash balance is immediately counted as strategy capital, inflating strategy sizing calculations and distorting the drawdown denominator used for strategy-only performance reporting — even though the code's stated intent is strategy/manual isolation.
- **Status**: CONFIRMED by direct read.

### M3. 5 dead constants in `config/settings.py`

- `CASH_RESERVE_PCT`, `CURRENCY`, `ML_MODEL_DIR`, `PARTIAL_REGIME_MIN_CANDLES`, `SAFE_HAVEN_YIELD_ANNUAL` — each defined once, referenced nowhere else in the codebase (repo-wide grep, single occurrence = the definition line itself).
- Not a correctness bug — likely leftover scaffolding from partially-built or abandoned features (e.g. `SAFE_HAVEN_YIELD_ANNUAL` implies a yield-bearing safe-haven calc that was never wired in; `CASH_RESERVE_PCT` implies a cash-buffer feature that isn't read anywhere). Worth a decision: delete, or note in `Technical_Debt.md` as intentionally-reserved-for-future-use.
- **Status**: CONFIRMED.

### M4. `scripts/signal_regime_diagnostics.py` depends on a gitignored, not-regenerated-on-clone file

- `outputs/` is gitignored (`.gitignore:10`). `signal_regime_diagnostics.py` reads `outputs/signal_stability_rolling.csv`, which is produced by a separate script (`scripts/signal_stability_rolling.py`) that must be run first. On a fresh clone, `outputs/` doesn't exist, so this script crashes with no explanation unless the prerequisite step is known out-of-band.
- **Contrast with docs/44**: much lower severity than the `data/universe.py` gitignore incident — this script is a standalone research/diagnostic tool, not on the live or backtest execution path (confirmed via repo-wide grep: no cron, no `main.py` command, no import from `runner/` or `backtest/` references it).
- **Status**: CONFIRMED, low severity.

## Refuted

### R1. Codex claimed `config/risk_config.yaml` is gitignored and invisible to the gate's config-drift check — FALSE

- Codex's finding #8 stated the gate's `git status --porcelain -- config/` drift check can't see edits to `config/risk_config.yaml` because it's gitignored, allowing an operator to silently change live risk parameters (max positions, sector caps, drawdown thresholds) without the gate detecting it.
- **Direct check**: `git ls-files config/risk_config.yaml` returns the file (tracked). `git check-ignore -v config/risk_config.yaml` returns nothing (exit 1, not ignored). `.gitignore` only excludes `db/*.db`, `db/*.db.bak`, `db/backups/`, `*.log`, `outputs/`, `.playwright-mcp/` — no config-path pattern.
- **Conclusion**: the gate's drift check *does* see this file. Codex's finding was wrong. Flagging the refutation itself as a data point: an independent second reviewer (Codex) is useful specifically because it produces claims that must be checked, not because its claims are automatically trustworthy — this is the adversarial-review discipline the charter asks for, applied to Codex itself, not just to Claude's own proposals.

## Server drift (charter Repository Integrity check #5 — does production match git?)

- Live server `ubuntu@3.109.104.170:/home/ubuntu/AlgoV2` reachable, dashboard (`:8501`) returns HTTP 200, crontab and running processes normal.
- **3 commits behind `origin/main`**: `998deea`, `840706e` (both docs-only, no functional impact), and **`eeb07e2`** ("Reconciler: auto-fix broker-only positions, keep ghost side alert-only") — a real, undeployed functional fix directly addressing the CEMPRO orphan-position bug class (see `cempro_orphan_position_bug_20260722` memory). This fix has been sitting undeployed since it was committed.
- Not yet pulled to server — awaiting explicit go-ahead before touching the live deployment (deploying to a live-trading server is a hard-to-reverse, shared-state action per this session's operating rules).

## Not completed this pass

Three Claude Explore sub-agents (live execution path deep-dive; backtest engine + strategy signal/ranking deep-dive; universe/data layer + repo-integrity deep-dive) were launched for a second independent pass beyond Codex's review, and all three failed mid-run on a session usage-limit error before reaching conclusions. Two lower-severity leads (M3, M4 above) were salvaged from their partial output and verified directly; nothing else from those three runs should be treated as either confirmed or refuted — it's simply unfinished. Worth resuming if a deeper pass on those three areas specifically is still wanted.

## Disposition

This is a find-only pass per the user's explicit framing — nothing above has been fixed. C1–C3 (shares_override, cancel_stale_gtts, partial-fill mapping) are live-trading correctness bugs with real capital exposure and should be prioritized first if/when a fix pass is authorized. H1–H4 and M1–M2 affect backtest fidelity and research-DB trustworthiness rather than live capital directly. M3/M4 are housekeeping. R1 should be dropped, not fixed.
