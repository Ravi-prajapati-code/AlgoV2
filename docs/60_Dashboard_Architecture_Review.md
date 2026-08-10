# 60 — Dashboard/Reporting Architecture Review

Date: 2026-08-10
Status: **Architecture review only. No implementation started.** Per explicit
user instruction: production trading code is not to be touched building this,
and no dashboard code is to be written until this document has been reviewed.

This document is the required deliverable before any dashboard work begins:
(1) architecture plan, (2) data-source mapping, (3) DB/reporting model
proposal, (4) dashboard page spec, (5) alert spec, (6) reconciliation spec,
(7) performance/caching plan, (8) implementation plan + risk flags.

Every fact below is sourced from direct code reads (three parallel research
passes: MAIN dashboard/DB layer, momentum_atr data layer, cron/health/
reconciliation infra) — file:line cited where it matters, not assumed from
the user's spec. Anywhere the user's spec described a mechanism that doesn't
match the code, that is called out explicitly rather than silently
"corrected."

---

## 0. Hard constraints this document is bound by (restated, not renegotiable)

- MAIN and momentum_atr strategy logic, DBs, signals, ledgers, locks, cron
  jobs, kill-switches, execution processes stay **completely independent**.
  Nothing below merges them.
- Dashboard is **observability/reporting only**. It must never place orders,
  cancel orders, change parameters, change allocation, disable kill-switches,
  or modify positions.
- Two-level view: Level 1 = per-strategy virtual fund (own cash ledger,
  positions, P&L, signals, trades, health). Level 2 = global broker-account
  view. Virtual strategy cash is never presented as real broker cash.
- No repeated live Upstox calls per page load; no repeated 500-symbol
  refetch.
- Do not duplicate trading data unnecessarily; reuse existing tables, add a
  reporting layer only where a genuine gap exists.
- Do not fabricate metrics, reasons, or history. `N/A — insufficient
  history` where data doesn't exist. Use only real stored trigger/reason
  strings.
- Never silently repair a discrepancy — if auto-repair exists anywhere, log
  what changed / why / previous value / new value / source / timestamp.
- Do not change: strategy rules, scoring formulas, allocation rules, rank
  rules, swap rule, execution times, broker order behavior, risk thresholds,
  existing strategy DB write behavior.

---

## 1. What already exists (baseline inventory)

### 1.1 Existing dashboard — MAIN-only, zero momentum_atr awareness

`dashboard/app.py` is a Streamlit app with 6 pages (`app.py:34-39`): Overview,
Open Positions, Today's Signals, Trade History, Strategy Health, Backtest.
`grep -rn "momentum_atr" dashboard/` returns **zero matches** anywhere in the
existing dashboard code.

| View | Data source | Live broker call? |
|---|---|---|
| Overview | `outputs/portfolio_state.json`, falls back to `db.repository.load_baseline_capital()` / `total_capital_injected_ever()`; equity curve from `load_snapshots()` | No |
| Open Positions | `outputs/portfolio_state.json` for strategy positions | **Yes** — if `LIVE_TRADING=true`, calls `UpstoxBroker().get_holdings()` directly (`positions.py:16-27`) to overlay live LTP and to list every broker holding not in MAIN's own symbol list as "Pre-existing Holdings" |
| Today's Signals | `outputs/signals.json` only | No |
| Trade History | `db.repository.load_trades()` | No |
| Strategy Health | `db.repository.load_trades()`/`load_snapshots()`, `monitoring.drift_monitor.compute_drift()`, `outputs/walk_forward/latest.json` | No |
| Backtest | Triggers a live `fetch_all` + `BacktestEngine` run on button click | Yes, but user-triggered compute, not passive page load |

**Finding directly relevant to this project's caching requirement**: Open
Positions already violates the "no repeated live broker call per page load"
requirement — it calls `get_holdings()` on every render when live. It also
already surfaces the collision problem the user is worried about, unlabeled:
any momentum_atr holding shows up as an anonymous "Pre-existing Holding"
because the dashboard has no concept of a second strategy. Both are things
this project must fix, not preserve.

### 1.2 DB inventory

**MAIN — `db/trading.db`** (`db/schema.sql` + `db/schema_universe.sql`, `db/repository.py`):

| Table | Written by | Read by | Dead/unused columns |
|---|---|---|---|
| `ohlcv_cache` | `save_ohlcv` | `load_ohlcv`, cache-date helpers | none |
| `positions` | `save_position`, ghost-close paths | `load_positions`, `get_last_position`, `reconcile_positions.py`, `gtt_coverage.py` | `ml_confidence`, `regime`, `risk_score`, `sizing_method` — declared, never written |
| `trades` | `save_trade`, `close_position_and_save_trade`, `reduce_position_and_save_trade` | `load_trades`, dashboard History/Health, `drift_monitor.py`, `daily_pnl_summary.py` | `fill_type`, `regime`, `ml_confidence` — declared, never written |
| `signals` | `save_signal` | `load_signals`, dashboard Signals (via JSON), `drift_monitor.py` | `ml_win_prob`, `ml_exp_return`, `regime` — declared, never written |
| `portfolio_snapshots` | `save_snapshot` | `load_snapshots`, `total_capital_injected_ever`, `snapshot_exists_for_date`, `load_baseline_capital`, dashboard Overview/Health | `drawdown_pct`, `kill_switch` — declared, never written |
| `risk_events` | **nobody** | **nobody** | entire table unused, zero Python references |
| `strategy_performance` | **nobody** | **nobody** | entire table unused |
| `ml_model_runs` | **nobody** (`cmd_train_ml` saves a model file, not this table) | **nobody** | entire table unused |
| `precompute_indicators` | `scripts/precompute_main_indicators.py` | `runner/daily_runner.py` | none (new, docs/59) |
| `sla_checkpoints` | `runner/daily_runner.py::_alert_run_abort` | `scripts/main_sla_check.py` | none (new, docs/59) |

**momentum_atr — `db/momentum_atr.db`** (`db/momentum_atr_schema.sql`, `db/momentum_atr_repo.py`), fully separate file, never imported from MAIN's code path:

| Table | Purpose |
|---|---|
| `positions` | `symbol UNIQUE`, `entry_date`, `entry_price`, `shares`, `status`, `entry_order_id`. No sector/stop_loss/trailing_stop/origin columns — single-strategy DB, no ambiguity to disambiguate. |
| `trades` | Same shape as MAIN's minus `sector`/`fill_type`/`regime`/`ml_confidence`/`slippage_pct`/`hold_days` — narrower schema. `exit_reason` is free text (real values in §1.4). |
| `equity_snapshots` | **Already structured, one row/day**: `date UNIQUE`, `cash`, `invested_value`, `total_equity`, `peak_equity`, `drawdown_pct`, `kill_switch_tripped`. This is momentum_atr's own daily P&L/drawdown record — directly usable for the Cash/Capital Integrity and Risk Monitor pages without new instrumentation. |
| `state` | Singleton (`id=1`) mutable current cash/peak/kill-switch carried between runs — the live working state `equity_snapshots` audits. |
| `daily_ranking` | `ranked_json` (symbol order) + `closes_json` per day. **Does not store the numeric score** — `compute_live_scores()`'s score dict is computed then discarded before save. Score history and score-change-day-over-day are **not derivable** from this table; would need a schema change. |
| `sla_checkpoints` | Identical shape to MAIN's own (`UNIQUE(date, step)`, `PRECOMPUTE`/`EXECUTION`). |

### 1.3 Cash/capital model — confirmed asymmetric between the two strategies

- **momentum_atr**: `MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT` (default 0.40) caps
  deployed capital to 40% of the *whole account's* real combined equity
  (cash + every holding, both strategies), recomputed fresh every run in
  `momentum_atr/execution.py::_get_effective_cash`. It also self-checks:
  fires a Telegram alert if broker cash is <50% of its own internal ledger
  cash — an explicit "possible capital collision with the other strategy"
  guard (`execution.py:172-177`).
- **MAIN**: has **no equivalent concept at all**. `grep` for
  `ALLOCATED_CAPITAL`/`BASELINE_CAPITAL`-style caps in `config/settings.py`
  returns nothing for MAIN. `portfolio/manager.py`'s `self.cash` is set
  directly from `broker.get_available_cash()` — the entire account's
  available margin, every run, uncapped, with no reservation for
  momentum_atr's 40% and no collision self-check. MAIN's `strategy_value()`
  splits *positions* by `origin` but still adds the **whole-account** cash
  figure on top (`manager.py:230-236`) — it is not actually MAIN-exclusive
  cash, only MAIN-exclusive position value.
- **Capital-injection detection is a false-positive risk today, independent
  of the dashboard**: MAIN treats any `broker_cash - self.cash` delta over
  ₹500 as an external deposit/withdrawal (`manager.py:800-809`). It cannot
  distinguish a real injection from momentum_atr moving cash in or out of
  the shared pool — any momentum_atr trade could get misread by MAIN as a
  capital injection/withdrawal event. This is a pre-existing production
  accounting risk, not something this project introduces or is asked to
  fix, but it directly explains why the Cash/Capital Integrity page's
  "impossible state" detection is necessary rather than cosmetic.

### 1.4 Real trigger/reason strings (verified in code, not the user's spec)

**MAIN** — `signals.reason` (BUY) is `gate_reason` from
`strategy/entry.py::check_entry`, e.g.
`"STRENGTH_CONFIRMED (RS:.., ADX:..)"`-style strings. `trades.exit_reason`
(SELL) values seen in code: `STOP_LOSS`, `TAKE_PROFIT`, `TRAILING`,
`SIGNAL`, `MANUAL`, `END_OF_BACKTEST`, `BROKER_SYNC_CLOSE`,
`bear_regime_exit`, `bull_regime_recovery`, `rebalance_trim`,
`bear_swing|<reason>`, `liquidbees_fund_swing`, `TREND_BREAK (...)`. MAIN
persists both entry and exit reasoning — the Trade Journal page has real
data to work with for MAIN.

**momentum_atr** — `INITIAL_TOPN_SPLIT`, `STOP_LOSS_-3%_SWAP`, `SWAP_SELL`,
`TAKE_PROFIT_+3%_SWAP`, `SWAP_BUY`, `SKIP_SWAP_BUY`, `RANK_RULE_EXIT`,
`RANK_EXIT_SELL`, `RANK_EXIT_REALLOC_TOPN`, `RANK_EXIT_REALLOC`,
`SKIP_RANK_EXIT_REALLOC`, `SKIP_INITIAL`. There is **no** `INITIAL_TOP3` and
**no** bare `REBALANCE` — both appeared as examples in the user's spec but
do not exist in code; the Trade Journal must use the real names above. Entry
trigger is **not persisted anywhere queryable** — it only exists transiently
in `run_daily()`'s return dict and the Telegram message text; `trades` has no
entry-reason column. This is a real gap for the momentum_atr Trade Journal,
not something that can be worked around without a schema change.

### 1.5 momentum_atr scoring/risk mechanics (verified against code, contradicts parts of the user's spec)

- `momentum = (close - SMA50)/SMA50*100`; `ATR14` via Wilder EMA; `atr_pct =
  atr/close*100`; `score = momentum * atr_pct`, **forced to 0 wherever
  momentum ≤ 0**. Confirmed identical between the live adapter
  (`momentum_atr/scoring.py`) and the backtest engine
  (`scripts/momentum_atr_experiment/engine.py`).
- Swap rule is a **dual** condition, not a single 3% threshold: worst-held
  position ≤ -3.0% **and** best-held position ≥ +3.0% simultaneously
  (`SWAP_LOSS_PCT`/`SWAP_GAIN_PCT`, separate constants).
- Rank-exit rule is independent of the swap rule: fires when a held
  position's current rank exceeds `PORTFOLIO_SIZE` (=3), excluding that
  day's swap-loser.
- **Drawdown kill-switch is a single binary threshold**,
  `MOMENTUM_ATR_DD_KILL_PCT = 0.25` — **not** the tiered 10%/15%/20% ladder
  described in the user's spec. It blocks new BUYs only; SELLs are never
  blocked. This must be surfaced on the Risk Monitor page as the real
  mechanism, not silently reconciled to match the spec's assumption.
- Possible pre-existing doc/code discrepancy (flagged, not resolved here):
  `risk.py`'s docstring claims manual-only recovery from a tripped
  kill-switch, but the code recomputes `tripped` fresh from current
  drawdown on every call and persists it via `update_state()` — meaning it
  could auto-un-trip if drawdown genuinely recovers, contradicting the
  stated manual-only intent. Out of scope to fix here; in scope to display
  honestly (show the actual current `tripped` state from `state`/
  `equity_snapshots`, not an assumed sticky one).

### 1.6 Cron ground truth (SSH-confirmed against the live server, IST literal — no `TZ=` header)

| IST | Days | Job | Strategy |
|---|---|---|---|
| 08:30 | Mon-Fri | token refresh | shared |
| 08:45 | Mon-Fri | token reminder | shared |
| 08:50 | Mon-Fri | `precompute_momentum_atr_ranking.py` | momentum_atr |
| 09:00–15:00 (30min) | Mon-Fri | `smoke_test.py` (import check only; does **not** cover either strategy's precompute/SLA scripts) | shared |
| 09:05 | Mon-Fri | `recovery_manager.py --check token` | MAIN-scoped |
| 09:17 | Mon-Fri | `run_momentum_atr_live.py --live` | momentum_atr |
| 09:20 | Mon-Fri | `reconcile_positions.py` | MAIN (momentum_atr symbols excluded from its "unknown" set only, not reconciled themselves) |
| 09:25 | Mon-Fri | `daily_pnl_summary.py` | MAIN-scoped |
| 09:30 | Mon-Fri | `precompute_main_indicators.py` | MAIN |
| 14:55 | Mon-Fri | `main.py run --live` | MAIN |
| 15:35 | Mon-Fri | `health_check.py`, `cron_integrity_check.py` | MAIN-scoped only |
| 15:40 / 15:41 | Mon-Fri | `gtt_coverage.py` / `gtt_price_audit.py` | MAIN + GOLDBEES-fungibility check vs momentum_atr |
| 15:45 | Mon-Fri | `recovery_manager.py --check all` | MAIN-scoped only |
| 16:00 | Mon-Fri | `momentum_atr_sla_check.py`, `main_sla_check.py` | both, separately |
| 16:30 | Mon-Fri | `backup_db.py` | **`trading.db` only — `momentum_atr.db` is never backed up by any cron job** |
| 18:05 / 18:30 Fri | Mon-Fri | `universe_scheduler.py` | shared universe, not strategy-specific |
| 18:45 last-Fri | monthly | `walk_forward.py` | MAIN research gate |
| 00:30 Sun | weekly | log/backup cleanup | shared |

### 1.7 Alerting today — no persistence, no severity, no cross-strategy reconciliation

- `notifications/telegram.py::send_message()` is the only alert channel for
  every script in the table above. No severity field, no alert object — each
  call site hand-builds its own emoji/text prefix. **No DB table records
  that an alert fired, its severity, or an acknowledgement.** Confirmed by
  grepping every `.sql` file in the repo for `reconciliation_log`,
  `alert_log`, `run_log` — none exist anywhere.
- `reconcile_positions.py` reconciles **MAIN vs broker only**; it loads
  momentum_atr's open symbols solely to exclude them from MAIN's own
  "unknown" set (so a momentum_atr symbol isn't wrongly absorbed into
  MAIN's `positions` table). **momentum_atr's own positions are never
  reconciled against the broker by any script.** A momentum_atr ghost
  (DB open, broker doesn't have it) or an un-attributed broker holding that
  is neither MAIN's nor momentum_atr's would not be caught by anything in
  the current pipeline.
- **MAIN's own live path has no momentum_atr awareness at all** — this is
  the most important cross-cutting finding. `grep -rn
  "momentum_atr\|MOMENTUM_ATR" portfolio/ strategy/ main.py
  runner/daily_runner.py` returns **zero matches**. Specifically,
  `runner/daily_runner.py::sync_portfolio_with_broker` pulls in *every*
  broker position unfiltered (`daily_runner.py:304-307`) and would classify
  a momentum_atr-only symbol as `origin="manual"` in MAIN's own `positions`
  table, **before** the 09:20 `reconcile_positions.py` cron (which does
  have momentum_atr awareness) gets a chance to run. This is a pre-existing
  production collision risk, independent of and prior to this dashboard
  project — it is not something the dashboard can fix (that would be
  touching trading code), but the dashboard's Collision/Overlap and Cash
  Integrity pages exist precisely to make this risk visible instead of
  silent.
- Momentum_atr-awareness in the codebase today is limited to exactly three
  places: `config/settings.py` comments, `scripts/reconcile_positions.py`,
  `monitoring/gtt_coverage.py`. Everything else is blind to the other
  strategy.

---

## 2. Architecture plan

### 2.1 Principle

The dashboard is a **read-only reporting layer** sitting on top of two
untouched, independent strategy DBs, fed by a new **observability snapshot
process** that runs on its own cron schedule, writes only to new reporting
tables, and never writes to `positions`/`trades`/`signals` in either
strategy's DB. It reads broker state at most once per snapshot cycle, never
per dashboard page load.

```
┌─────────────────┐        ┌──────────────────────┐
│ MAIN strategy    │        │ momentum_atr strategy │
│ (untouched)      │        │ (untouched)           │
│ db/trading.db    │        │ db/momentum_atr.db    │
└────────┬─────────┘        └──────────┬────────────┘
         │  read-only                  │  read-only
         ▼                              ▼
   ┌─────────────────────────────────────────┐
   │   Observability snapshot job (new cron)  │
   │   - reads both DBs (read-only)           │
   │   - reads broker ONCE per cycle          │
   │   - writes strategy_* reporting tables   │
   │   - runs reconciliation checks           │
   │   - writes strategy_alert rows           │
   └───────────────────┬───────────────────────┘
                        │  read-only
                        ▼
              ┌───────────────────┐
              │  Dashboard (new)   │
              │  reads reporting   │
              │  tables + existing │
              │  trades/signals    │
              │  directly, no      │
              │  broker calls      │
              └───────────────────┘
```

### 2.2 Level 1 / Level 2 separation

- **Level 1 (per-strategy virtual fund)**: every metric is computed
  strategy-scoped — MAIN's own cash ledger view, MAIN's own positions,
  momentum_atr's own `equity_snapshots`-derived cash/equity, momentum_atr's
  own positions. Never blend MAIN and momentum_atr numbers into one figure
  on a Level 1 page.
- **Level 2 (global broker-account view)**: the only place `broker_cash`,
  `unallocated_broker_cash`, and total-account equity appear. Explicitly
  labeled as broker-account-wide, never presented as belonging to either
  strategy.
- Every new reporting row carries an explicit `strategy_id` column, values
  restricted to `'main'` / `'momentum_atr'` / `NULL` (`NULL` reserved for
  genuinely broker-account-scoped rows, e.g. `unallocated_broker_cash`).
  Never a bare `'strategy'` label.

### 2.3 Non-goals (explicit)

- No merged DB, no merged lock, no merged cron, no merged kill-switch.
- No dashboard write path to `positions`/`trades`/`signals`/`state` in
  either strategy DB.
- No new broker-account write capability of any kind.
- No "fixing" of the pre-existing collision risks found in §1.3/§1.7 — those
  are trading-code changes, explicitly out of scope for this project. This
  document surfaces them; it does not resolve them.

---

## 3. Data-source mapping — requirement table

Columns: **Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh Frequency | Risk Level**

### Page 1 — Global Overview

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| Total broker account equity | Broker snapshot (new cached table) | New | both (Level 2) | `broker.get_portfolio_value()`, cached | Once/snapshot cycle | Low |
| Unallocated broker cash | Broker snapshot minus both strategies' `strategy_allocated_cash` | New (derived) | both | `broker_cash - main_allocated - momentum_atr_allocated` | Once/snapshot cycle | Medium — depends on correct allocation figures below |
| MAIN equity | `portfolio_snapshots.strategy_value`/`total_value` | Existing | main | direct read | Per MAIN run (14:55) | Low |
| momentum_atr equity | `equity_snapshots.total_equity` | Existing | momentum_atr | direct read | Per momentum_atr run (09:17) | Low |
| Combined equity curve | new `strategy_equity_snapshot` | New (aggregation) | both | sum of per-strategy equity, kept as two series, never one blended line | Daily | Low |

### Page 2 — Strategy Comparison

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| CAGR/Sharpe/MDD per strategy | MAIN: `portfolio_snapshots`; momentum_atr: `equity_snapshots` | Existing | both, side by side | standard formulas, computed independently per strategy | Daily | Low |
| Win rate / trade count | `trades` (MAIN), `trades` (momentum_atr) | Existing | both | direct aggregation | Daily | Low |
| Research vs Live label | see Page 15 | Existing (`gate_results.json` for momentum_atr) + New (registry flag) | both | static/manual classification, not auto-inferred | On change | Medium — must not auto-promote to VALIDATED |

### Page 3 — Live Positions

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| MAIN positions | `positions` (MAIN) | Existing | main | direct read | Snapshot cycle | Low |
| momentum_atr positions | `positions` (momentum_atr) | Existing | momentum_atr | direct read | Snapshot cycle | Low |
| Broker actual qty per symbol | Broker snapshot (cached) | New | both (Level 2) | `broker.get_holdings()`, cached once/cycle | Snapshot cycle | Medium — must never be fetched live per page view (see §1.1 finding) |
| Per-symbol 3-way display (MAIN qty / momentum_atr qty / broker qty) | join of the three above | New (`strategy_position_snapshot`) | both, explicit rows | no summation, display all three | Snapshot cycle | **High** — this is exactly the collision case the user called out (e.g. HAL) |

### Page 4 — Collision/Overlap View

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| SUM(strategy qtys) == broker qty check | `strategy_position_snapshot` | New | both | `main_qty + momentum_atr_qty == broker_qty`, RED ALERT on mismatch | Snapshot cycle | **High** |
| Unattributed broker holdings | broker snapshot minus both strategy position sets | New | Level 2 (`strategy_id=NULL`) | set difference | Snapshot cycle | High — this is precisely the `origin="manual"` misclassification risk in §1.7 made visible |

### Page 5 — Cash/Capital Integrity

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| `broker_cash` | broker snapshot | New (cached) | Level 2 | `broker.get_available_cash()`, cached | Snapshot cycle | Low |
| `main_strategy_allocated_cash` | MAIN has **no allocation cap** (§1.3) — must show `N/A — no allocation model` or the raw uncapped `self.cash` value clearly labeled as "whole-account cash MAIN currently sees", not a MAIN-exclusive figure | New | main | see §1.3 | Snapshot cycle | **High** — must not misrepresent this as MAIN-exclusive money |
| `momentum_atr_strategy_allocated_cash` | `MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT * real_total_account_equity` | Existing (recomputed each momentum_atr run) | momentum_atr | direct formula, verified | Per momentum_atr run | Medium |
| `main_cash + momentum_atr_cash > broker_cash` impossible-state check | derived | New | both | `IF (main_allocated_cash_estimate + momentum_atr_allocated_cash) > broker_cash THEN CAPITAL INTEGRITY = FAILED` | Snapshot cycle | **High** |
| `strategy_invested_value` / `strategy_equity` | `positions`/`equity_snapshots` respectively | Existing | both | direct/derived | Snapshot cycle | Low |

### Page 6 — Signals

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| MAIN signal list + score + reason | `signals` | Existing | main | direct read, `score` doubles as rank key (no separate rank column) | Per MAIN run | Low |
| momentum_atr ranked list | `daily_ranking.ranked_json` | Existing | momentum_atr | direct read | Per momentum_atr run | Low |
| momentum_atr numeric score | **not stored** — discarded before save (§1.2) | **New required** — schema change to persist `scores_json` alongside `ranked_json`/`closes_json` | momentum_atr | requires code change to `save_daily_ranking` call site | N/A until built | Medium — this is a genuine gap, not fabricable from existing data |
| momentum_atr score change day-over-day | same as above | New (depends on above) | momentum_atr | delta of two days' scores once persisted | N/A until built | Medium |
| momentum_atr rank change day-over-day | `daily_ranking` across multiple days | New (derived, needs new helper) | momentum_atr | compute list-index position per day from existing `ranked_json` rows — derivable without schema change, but no existing function does this | Daily | Low |

### Page 7 — Order Monitor

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| MAIN order/fill detail | none — **correction (2026-08-10, verified against `db/models.py`/`db/schema.sql`)**: MAIN has no `entry_order_id`/`exit_order_id` columns on either `positions` or `trades`. No order-id traceability exists for this strategy at all. | Existing, N/A | main | — | Per run | Medium — no order-id traceability |
| momentum_atr order/fill detail | **correction (2026-08-10, verified against `db/momentum_atr_schema.sql`)**: the original claim that `trades` has both `entry_order_id` and `exit_order_id` was false. `entry_order_id` lives only on the OPEN `positions` row and is dropped when the position closes into a `trades` record; `trades` persists `exit_order_id` only. So traceability is open-position entry-side + closed-trade exit-side — never both for the same closed trade. | Existing, partial | momentum_atr | direct read (positions for entry, trades for exit) | Per run | Medium — entry-side lost on close |

### Page 8 — Trade Journal

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| MAIN entry reason | `signals.reason` joined to `trades` by `(symbol, entry_date)` (same join `drift_monitor.py` already does) | Existing | main | join | Daily | Low |
| MAIN exit reason | `trades.exit_reason` | Existing | main | direct read, real values listed §1.4 | Daily | Low |
| momentum_atr exit reason | `trades.exit_reason` | Existing | momentum_atr | direct read, real trigger strings §1.4 (not the spec's `INITIAL_TOP3`/`REBALANCE`) | Daily | Low |
| momentum_atr entry reason | **not persisted anywhere** (§1.4) | **New required** — schema change to persist the transient `run_daily()` trigger string onto `trades` | momentum_atr | requires code change | N/A until built | Medium — real gap, must show `N/A — not persisted` until fixed, never invent a reason |

### Page 9 — P&L / Equity Curve

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| MAIN equity curve | `portfolio_snapshots` | Existing | main | direct read, kept separate | Daily | Low |
| momentum_atr equity curve | `equity_snapshots` | Existing | momentum_atr | direct read, kept separate | Daily | Low |
| Combined view | rendered as two series on one chart, never summed into one line unless explicitly both scoped correctly per §1.3 | New (chart only) | both | no blended P&L number | Daily | Medium — must not silently sum |

### Page 10 — Risk Monitor

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| momentum_atr drawdown/kill-switch state | `state.kill_switch_tripped`, `equity_snapshots.drawdown_pct` | Existing | momentum_atr | direct read of the real single 25% binary threshold (§1.5) — **must not render a 10/15/20% ladder**, that doesn't exist in code | Per run | **High** — a fabricated ladder would misrepresent real risk state |
| MAIN drawdown / kill-switch / graduated size-reduction | `portfolio_snapshots.drawdown_pct` (dead column, still never written) vs live-computed | **Resolved this session — computable read-only** | main | MAIN's real live risk gate is `portfolio/risk.py::can_open_new_trades()`, called from `portfolio/manager.py:548` (and inline at `:495` for rank-replacement) using `DRAWDOWN_KILL_SWITCH_PCT` (10%, `config/settings.py`) as a hard circuit breaker on new BUYs, plus graduated slot-size cuts at `manager.py:534-542` (`DRAWDOWN_REDUCE_SIZE_PCT`=5% → 50% size, `×DRAWDOWN_REDUCE_TIER2_MULT`=1.5 → 7.5% → 25% size). Both computed **in-memory per run** from `self.peak_value` vs `portfolio_val`, never persisted to the dead column — but `self.peak_value` is itself just `max(portfolio_snapshots.strategy_value)` ever recorded, so the dashboard can reproduce the exact same live number read-only: `peak = max(strategy_value)`, `current = latest strategy_value` (or a fresh broker mark), compare against the three real constants above. **Not** `risk/manager.py::RiskManager` — that class (with its own `DRAWDOWN_REDUCE_SIZE_PCT` reference) is dead in the live trading path, wired only to `main.py`'s manual `risk-report` CLI command, confirmed via grep (no import from `runner/daily_runner.py` or `portfolio/manager.py`). Show the real 10%/5%/7.5% thresholds — never a fabricated ladder. | Per snapshot cycle, computed on the fly | Medium |

### Page 11 — System Health

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| MAIN SLA (PRECOMPUTE/EXECUTION) | `sla_checkpoints` (MAIN DB) | Existing | main | direct read, same as `main_sla_check.py` | Per run | Low |
| momentum_atr SLA | `sla_checkpoints` (momentum_atr DB) | Existing | momentum_atr | direct read | Per run | Low |
| MAIN stall count | `precompute_indicators.stalls` (structured column) | Existing | main | direct read | Per run | Low |
| momentum_atr stall count | **only inside free-text `detail` string** (§1.2) | New (needs a structured column to query cleanly) | momentum_atr | string-parse workaround possible short-term, real column preferred | Per run | Low |

### Page 12 — Cron/Watchdog

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| Full cron inventory table | §1.6 (static, confirmed via SSH) | New (`strategy_run_log` populated by each cron's own completion, or a periodic crontab snapshot job) | both | direct display, plus live "did today's job run" from `sla_checkpoints` where it exists | Daily / on cron change | Low |
| momentum_atr backup coverage | **none exists** — `backup_db.py` only backs up `trading.db` (§1.6) | Flag only — do not silently imply coverage exists | momentum_atr | display as a known gap | Static until fixed | Medium |

### Page 13 — Data Quality

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| Symbols fetched vs expected | Not stored as a clean pair for either strategy today — only derivable from `sla_checkpoints.detail` free text on the abort path | New (requires instrumentation) | both | flag as a genuine gap; do not fabricate a ratio | N/A until built | Low |
| Retry counts (urllib3 layer) | not tracked anywhere | New (requires instrumentation) | both | flag as a genuine gap | N/A until built | Low |

### Page 14 — Reconciliation

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| MAIN vs broker reconciliation | `scripts/reconcile_positions.py` output | Existing (needs a DB sink — currently Telegram/exit-code only) | main | ghost/unknown classification already exists in code | Per run (09:20) | Low |
| momentum_atr vs broker reconciliation | **does not exist anywhere** (§1.7) | **New** — no code currently reconciles momentum_atr's own positions against the broker | momentum_atr | would need a new check, read-only/alert-only per the observability-only constraint; writing an auto-fix would be a trading-code change requiring separate approval | N/A until built | **High** — currently a blind spot in production, not just in the dashboard |

### Page 15 — Overlapping Signals

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| Same-symbol opposing signals (e.g. MAIN BUY, momentum_atr SELL) | `signals` (MAIN) + `daily_ranking`/derived momentum_atr signal-of-the-day | New (join) | both, informational only | detect same symbol, opposing action, same date; CONFLICT display, never auto-merged | Daily | Medium — informational only, must not influence either strategy |

### Page 16 — Daily Report

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| Fixed-template daily summary | aggregation of pages 1-11's data | New (template renderer) | both, clearly sectioned | per user's given template | Daily | Low |

### Page 17 — Research/Live vs Backtest

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| momentum_atr backtest baseline | `scripts/momentum_atr_experiment/gate_results.json` | Existing | momentum_atr | direct read, labeled RESEARCH (docs/57, docs/58: "research only, not wired to production") | Static | Low |
| Labeling: RESEARCH/PAPER/LIVE/VALIDATED | new `strategy_registry` field, manually set | New | both | **never auto-promoted to VALIDATED** — explicit manual field only | On change | **High** — must not auto-infer validation status |

### Page 18 — Alert Center

| Requirement | Data Source | Existing/New | Strategy | Calculation | Refresh | Risk |
|---|---|---|---|---|---|---|
| All alerts, severities | new `strategy_alert` table, populated additively alongside existing Telegram sends | New | both | see §5 | Real-time as generated | Low (additive, doesn't touch existing Telegram path) |

---

## 4. DB / reporting model proposal

### 4.1 Reuse — no duplication

Read directly, at dashboard-query time or snapshot-computation time, from:
`positions`, `trades`, `signals`, `portfolio_snapshots`, `precompute_indicators`,
`sla_checkpoints` (MAIN); `positions`, `trades`, `equity_snapshots`, `state`,
`daily_ranking`, `sla_checkpoints` (momentum_atr). None of these are copied
into new tables — the reporting layer queries them live (from the DB, not
the broker) at snapshot time.

### 4.2 New tables (additive only, no existing table altered except two narrow, additive schema changes flagged below)

```sql
-- One row per strategy, manually maintained. Source of truth for labeling
-- (RESEARCH/PAPER/LIVE/VALIDATED) -- never auto-set.
CREATE TABLE strategy_registry (
    strategy_id         TEXT PRIMARY KEY,   -- 'main' | 'momentum_atr'
    display_name        TEXT NOT NULL,
    db_path             TEXT NOT NULL,
    status_label         TEXT NOT NULL,     -- RESEARCH|PAPER|LIVE|VALIDATED, manual
    status_set_by         TEXT,
    status_set_at         TEXT
);

-- Broker-account-wide cache, written once per snapshot cycle, read by every
-- dashboard page needing broker state. This is what stops per-page-load
-- broker calls (see §7).
CREATE TABLE broker_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    broker_cash     REAL NOT NULL,
    total_equity    REAL NOT NULL,
    holdings_json   TEXT NOT NULL   -- symbol -> qty, as reported by broker
);

-- Per-strategy capital view, one row per snapshot cycle per strategy.
CREATE TABLE strategy_capital_snapshot (
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
CREATE TABLE strategy_position_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    main_qty        INTEGER NOT NULL DEFAULT 0,
    momentum_atr_qty INTEGER NOT NULL DEFAULT 0,
    broker_qty      INTEGER NOT NULL,
    collision_flag  INTEGER NOT NULL DEFAULT 0  -- 1 if main_qty+momentum_atr_qty != broker_qty
);

-- Reconciliation run outcomes, PASS/WARNING/FAIL, one row per check per cycle.
CREATE TABLE strategy_reconciliation_log (
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
CREATE TABLE strategy_alert (
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
CREATE TABLE strategy_run_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    strategy_id     TEXT,               -- NULL for shared/cross-cutting jobs
    job_name        TEXT NOT NULL,
    status          TEXT NOT NULL,      -- OK|FAILED|SKIPPED
    detail          TEXT
);
```

### 4.3 Two narrow additive schema changes flagged (not yet approved — decision points)

These are the only places the requirement table (§3) found a genuine data
gap that reusing existing tables cannot close:

1. **momentum_atr `daily_ranking`**: add `scores_json TEXT` alongside the
   existing `ranked_json`/`closes_json`, populated from the `scores` dict
   `compute_live_scores()` already computes but currently discards before
   `save_daily_ranking()` is called. Needed for Page 6 (Signals) score/score-
   change display. This is a momentum_atr DB schema change — small, additive,
   does not touch scoring logic, but is technically a change to a live
   strategy's DB and should be called out for explicit sign-off rather than
   bundled silently into dashboard work.
2. **momentum_atr `trades`**: add `entry_reason TEXT`, populated from the
   trigger string `run_daily()` already computes but currently only sends to
   Telegram. Needed for Page 8 (Trade Journal) entry-reason display. Same
   caveat as above.

Until approved, both fields render as `N/A — not persisted` rather than
being fabricated or backfilled by inference.

---

## 5. Alert specification

- Severity: `INFO` / `WARNING` / `CRITICAL`, matching the user's spec.
- Every alert written to `strategy_alert` (§4.2) **in addition to**, never
  instead of, the existing Telegram sends — this project does not remove or
  alter any existing alert call site's behavior, only adds a DB record
  alongside it.
- Examples, mapped to real mechanisms found above:
  - `CRITICAL` — Page 4 collision check fails (`main_qty + momentum_atr_qty
    != broker_qty`); Page 5 capital integrity fails (`main + momentum_atr
    cash > broker_cash`); momentum_atr kill-switch tripped
    (`state.kill_switch_tripped = 1`).
  - `WARNING` — SLA step missing/non-OK (mirrors existing `main_sla_check.py`/
    `momentum_atr_sla_check.py` RED); GTT naked/mismatch (mirrors
    `gtt_coverage.py`/`gtt_price_audit.py`); reconciliation `WARNING` result.
  - `INFO` — capital-injection detection fired on MAIN (§1.3 — flagged as
    informational since it may be a false positive caused by momentum_atr
    cash movement, not asserted as fact).
- Dashboard alert center is **display and acknowledge-only**
  (`acknowledged` flag) — no remediation action reachable from the UI.

---

## 6. Reconciliation specification

- **Never silently repair.** Every check writes a `strategy_reconciliation_log`
  row with `result` ∈ {PASS, WARNING, FAIL}. If (and only if) an existing
  auto-repair mechanism already fires (e.g. `reconcile_positions.py`'s
  existing auto-fix of broker-only "unknown" positions into MAIN's DB), the
  observability layer additionally logs `auto_repaired=1` with `repair_what`/
  `repair_why`/`repair_previous_value`/`repair_new_value`/`repair_source`/
  timestamp — it does not perform or extend any repair itself.
- Checks, mapped to existing vs new:
  - `position_collision_sum` (new) — Page 4.
  - `capital_integrity` (new) — Page 5.
  - `main_vs_broker` (existing logic in `reconcile_positions.py`, newly
    sinked to `strategy_reconciliation_log` in addition to Telegram).
  - `momentum_atr_vs_broker` (**new — does not exist today**, §1.7/§3 Page 14).
    Read-only/alert-only by default, consistent with the observability-only
    constraint. Whether to also extend `reconcile_positions.py`'s auto-fix
    logic to momentum_atr is explicitly **not decided here** — that would be
    a change to a script that already writes to a strategy DB, and needs its
    own sign-off, separate from this dashboard project.
  - `gtt_coverage` / `gtt_price_audit` (existing, newly sinked to the log
    table in addition to Telegram).

---

## 7. Performance / caching plan

- One scheduled **broker snapshot job** (new, own lock file, own cron slot —
  e.g. immediately after 09:20 reconcile or on its own short interval)
  fetches broker cash + holdings once per cycle and writes `broker_snapshot`.
  Every dashboard page reads this cached row, never `broker.py` directly.
- **Fixes the existing violation** in `dashboard/views/positions.py` (§1.1) —
  its live `get_holdings()` call on every page render must be redirected to
  read `broker_snapshot` instead. This is the one existing dashboard
  behavior this project needs to change, and it's a caching fix consistent
  with the user's own stated performance requirement, not a trading-behavior
  change.
- Suggested refresh cadence: `broker_snapshot` every 15-30 minutes during
  market hours (matches the existing `smoke_test.py` cadence's order of
  magnitude, avoids adding meaningful new Upstox API load); per-strategy
  snapshot tables refreshed once per that strategy's own run (no faster than
  the strategy itself updates); reconciliation/alert checks run once per
  snapshot cycle, not per page load.
- Dashboard queries only ever hit SQLite (both DBs, read-only connections)
  and the new reporting tables — no network calls at render time except the
  existing Backtest page's explicit user-triggered run (unchanged, out of
  scope).

---

## 8. Implementation plan (not started — pending review of this document)

- **Phase 0 (this document)**: architecture review, gate before any code.
- **Phase 1 — DB only**: create the new tables (§4.2), no UI yet. Build the
  broker-snapshot cron job and the reconciliation-check jobs as new,
  independent scripts (own lock files, own log files, matching this
  project's existing cron conventions) that read both strategy DBs
  read-only and write only to the new tables. No existing script is
  modified in this phase except the two narrow, separately-flagged additive
  schema changes in §4.3, which require explicit go-ahead first.
- **Phase 2 — dashboard read layer**: new pages (either extending the
  existing Streamlit app with clearly separated MAIN/momentum_atr/Level-2
  sections, or a new app — recommend extending the existing app given reuse
  requirement, final call pending user preference) built strictly against
  the new reporting tables plus direct read-only queries to existing
  `trades`/`signals`/`equity_snapshots`/etc. Fix the `positions.py` live
  broker-call violation (§1.1/§7) as part of this phase.
- **Phase 3 — collision/integrity/alert pages**: Pages 3-5, 14, 18 — the
  highest-risk-to-get-wrong pages, built last so the underlying
  snapshot/reconciliation data has had time to be validated in Phase 1/2.
- **Phase 4 — reporting polish**: Daily Report template, Research/Live
  labeling workflow (`strategy_registry` manual field), historical backfill
  of `strategy_run_log` from existing log files where feasible.

## 9. Risk flags / accounting inconsistencies / do-not-change list (consolidated)

- **Do not change**: MAIN strategy rules, momentum_atr scoring formula
  (§1.5), portfolio allocation rules, rank rules, swap rule, execution
  times, broker order behavior, risk thresholds (including the real single
  25% kill-switch, not a ladder), existing strategy DB write behavior for
  `positions`/`trades`/`signals`/`state`/`equity_snapshots`/`daily_ranking`
  (except the two narrow, separately-approved additive columns in §4.3).
- **Accounting inconsistencies found** (pre-existing, surfaced not fixed):
  MAIN has no capital-allocation cap and no momentum_atr-collision
  self-check, unlike momentum_atr (§1.3); MAIN's capital-injection detection
  cannot distinguish a real deposit from momentum_atr's own cash movement
  (§1.3); `portfolio_snapshots.strategy_value` still includes whole-account
  cash, not MAIN-exclusive cash, despite the name (§1.3); several MAIN/
  momentum_atr columns are declared but never written (§1.2).
- **Broker/API risks**: `dashboard/views/positions.py`'s existing live
  `get_holdings()` call on every page render (§1.1/§7) — must be redirected
  to the cached snapshot as part of this project, per the user's own
  caching requirement.
- **Performance risks**: none beyond the above once the snapshot cache is
  in place; SQLite reads against two small local DB files are cheap.
- **Production collision risk, independent of the dashboard, surfaced but
  explicitly not this project's to fix**: MAIN's live
  `sync_portfolio_with_broker` path has zero momentum_atr awareness and
  would misclassify a momentum_atr-only symbol as MAIN's own
  `origin="manual"` position before the 09:20 reconcile cron runs (§1.7);
  momentum_atr has no reconciliation against the broker at all (§1.7/§6);
  `momentum_atr.db` is never backed up (§1.6).

---

**Next step**: user review of this document. No dashboard code or DB schema
change begins until this is approved, per the explicit "only after that
should implementation begin" instruction.
