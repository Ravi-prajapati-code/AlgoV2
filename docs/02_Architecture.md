# 02 — Architecture

> Filename/scope note: the document numbered `02` was unrecoverable from the original garbled
> request that specified this dossier. Given the gap between `01_Project_Overview` and
> `03_Strategy`, "Architecture" (system-wide data flow and design) is the most natural fit and is
> what this document covers. Flag to the user if a different `02_` topic was intended.

## Two parallel execution paths, one shared strategy layer

AlgoV2 has two distinct "engines" that both call into the same `strategy/` and `indicators/`
code, but diverge in how they execute orders and persist state:

1. **Live path**: `runner/daily_runner.py` (cron-invoked, once/day) → `strategy/signals.py` →
   `portfolio/manager.py` (real order placement via `broker/upstox.py`) → `db/repository.py`
   (SQLite persistence) → `notifications/telegram.py`.
2. **Backtest path**: `main.py backtest` → `backtest/engine.py::BacktestEngine.run()` — a
   self-contained simulation loop that reimplements order execution, position sizing, and
   drawdown-tiering *inline* rather than calling `portfolio/manager.py` directly. This means the
   backtest and live paths can drift out of sync (see `06_Validation.md`'s Sharpe-methodology note
   and `09_Open_Questions.md`'s drawdown-tiering duplication finding).

Both paths share: `indicators/composite.py` (indicator computation), `strategy/regime.py`
(regime detection), `strategy/entry.py` / `strategy/exit.py` (signal gates), `strategy/scoring.py`,
`strategy/relative_strength.py`, and `config/settings.py`.

## Daily live cycle (`runner/daily_runner.py`)

A hybrid state machine keyed off `detect_regime()`:
- **BULL**: normal universe scan → `strategy/signals.py::generate_signals()` → entry/exit gates →
  `portfolio/manager.py` places orders and manages GTT stop-losses.
- **BEAR**: rotates into GOLDBEES (safe-haven ETF) and the defensive bear-swing sleeve
  (`strategy/defensive_portfolio.py`); normal universe entries suppressed.
- Every run also does `sync_portfolio_with_broker()` — reconciles local DB state against the
  broker's actual holdings/orders before making any decisions, and `cancel_stale_gtts()` /
  `_reconcile_gtt_stops()` in `portfolio/manager.py` guard against duplicate or orphaned GTTs (the
  mechanism that both surfaced and now defends against the CGPOWER duplicate-GTT incident — see
  `08_Project_Memory.md`).
- Hardcoded `_NSE_HOLIDAYS` list in `daily_runner.py` is a manual annual-maintenance liability —
  no external holiday-calendar source.

## Universe management cycle

`universe/rebalancer.py::RebalancingEngine` orchestrates four cadences:
- **Daily** (`--mode daily`, `universe/manager.py::daily_quality_check`) — volume-collapse safety
  net. **Built, config-enabled, documented — but never actually installed in
  `scripts/setup_cron.sh`.** See `09_Open_Questions.md`.
- **Weekly** (`--mode weekly`) — re-ranks the active universe against the static candidate list
  via `universe/scorer.py`'s 6-factor composite score; the only mode actually cron-installed.
- **Monthly** — broad rescan for new candidates.
- **Quarterly** — `universe/audit.py` health report.

The live tradeable universe is the union of the static list (`config/watchlist_nse.py`) and a DB
table of promoted "extras" (`universe/manager.py` promotion/demotion state machine,
`db/universe_repo.py`), read at runtime by `data/universe.py::get_all_symbols()`. This
static-list-plus-DB-extras union is the exact mechanism implicated in the currently-active
loser-leak bug (`08_Project_Memory.md`).

## Data & persistence

- `data/fetcher.py` + `data/providers/upstox_provider.py` — historical/live price fetching.
- `db/models.py` / `db/repository.py` — SQLite ORM-ish layer; `close_position_and_save_trade()` is
  an atomic transaction; `was_sold_today()` guards against T+1 settlement residue re-triggering a
  same-day re-buy.
- `db/schema.sql` / `db/schema_universe.sql` — table definitions. Note: `save_weekly_metrics()` in
  `db/universe_repo.py` does a **runtime `ALTER TABLE`** to migrate a 5→6-factor scorer redesign
  that was never back-ported into the static schema file — schema file and live DB schema have
  silently diverged.

## Broker integration

`broker/upstox.py` (+ `base.py`, `paper.py`, `token_refresh.py`, `upstox_auth.py`) wraps the Upstox
v3 API: order placement, GTT stop-limit orders, token refresh. Two live-incident fixes are baked
into this layer's current behavior: the v3 GTT schema break fix, and the GOLDBEES
GTT-fires-as-unfillable-LIMIT fix (`gtt_stop_limit_price()` in `portfolio/manager.py`). See
`08_Project_Memory.md` for both incidents in full.

## Config system

`config/settings.py` is the single source of truth for tunable parameters, each with a Python
default and (for many, not all) a YAML override point in `config/risk_config.yaml` /
`config/strategy_config.yaml`. Roughly half of `risk_config.yaml`'s keys are confirmed unread by
any code, and several `settings.py` parameters (e.g. `MAX_HOLD_DAYS`, `ADX_TREND_THRESHOLD`,
`MIN_SIGNAL_SCORE`) have no downstream consumer at all. New experimental levers follow an
established "off-by-default env-var" pattern (e.g. `TREND_GATE_200_ENABLED`,
`EXTENSION_FILTER_EMA100_MAX_PCT`) so they can be toggled per-backtest-run without code changes —
this is the mechanism `scripts/robustness_gate.py` uses to A/B a candidate against baseline.

## Monitoring & operations

Cron-installed jobs (`scripts/setup_cron.sh`, ~10 jobs) cover the daily runner, weekly universe
rebalance, token refresh/reminder, DB backup, health checks, and (as of the 2026-07-06 fix)
`gtt_price_audit.py` / `gtt_coverage.py` (previously built but never actually installed — see
`08_Project_Memory.md`). `dashboard/` is a read-only Flask-style view layer for manual inspection;
`portfolio/app.py` is a dead orphaned duplicate of it.
