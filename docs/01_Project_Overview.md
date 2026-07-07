# 01 — Project Overview

## What this is

AlgoV2 is a live, real-money swing-trading system for NSE (India) equities, trading through
Upstox's broker API. It runs unattended on a server via cron, holds positions for multi-day/week
swings (not intraday), and manages its own dynamic stock universe, position sizing, entries,
exits, and risk controls end to end. There is no human in the loop for individual trade decisions.

## Current live posture (as of 2026-07-06)

- **Strategy**: regime-aware trend/momentum swing system (see `03_Strategy.md`). BULL regime
  trades the active universe normally; BEAR regime rotates into a safe-haven ETF (GOLDBEES) and a
  defensive bear-swing sleeve; regime is detected via `strategy/regime.py::detect_regime()`.
- **Universe**: ~100 actively-traded NSE symbols managed by a dynamic promotion/demotion system
  (`universe/manager.py`) layered on a static candidate list (`config/watchlist_nse.py`), re-ranked
  weekly and broadly rescanned monthly. See `08_Project_Memory.md` for the loser-leak bug currently
  active in this subsystem.
- **Honest baseline performance** (full backtest window, after the 2026-07-02 regime-signal bug
  fix — see `06_Validation.md`): CAGR +12.85%, Sharpe 0.83, MDD 23.67%. **This fails the system's
  own validation gates.** No safe re-tune has been found that both recovers CAGR and survives all
  4 stress-test scenarios; live has been kept unchanged rather than deploy an untested lever.
- **Broker**: Upstox v3 API, live order placement, GTT (Good-Till-Triggered) stop-loss orders,
  token refresh automation. Paper-trading mode exists and shares the same code path.
- **ML**: fully disabled (`ML_ENABLED=False`) — a trained model existed but measured 26% win rate
  and was judged harmful; left in the codebase but not wired into any live decision.

## Subsystem map (see `07_Code_Map.md` for file-level detail)

| Subsystem | Directory | Role |
|---|---|---|
| Strategy | `strategy/`, `indicators/` | Regime detection, entry/exit gates, scoring, indicator math |
| Backtest | `backtest/`, `charges/` | Historical simulation engine, metrics, NSE charge modeling |
| Portfolio | `portfolio/` | Live order orchestration, position sizing, allocation, GTT lifecycle |
| Risk | `risk/` | A parallel, more sophisticated risk-sizing system — **not on the live path** |
| Universe | `universe/`, `data/universe.py` | Dynamic candidate scoring, promotion/demotion, rebalancing cadence |
| Data/Broker | `data/`, `db/`, `broker/` | Price fetching, persistence, Upstox integration |
| Runner | `runner/`, `main.py` | Daily cron entrypoint, state machine, CLI |
| ML | `ml/` | Disabled model training/inference layer |
| Monitoring | `monitoring/`, `notifications/`, `dashboard/` | Drift/GTT/coverage monitors, Telegram alerts, read-only dashboard |
| Config | `config/` | Central settings, YAML overrides, static watchlist |
| Scripts | `scripts/` | Cron jobs, one-off research tools, operational utilities |
| Tests | `tests/` | Unit/integration coverage, several tied to specific incident-fix commits |

## Architectural pattern worth knowing up front

A recurring theme across every subsystem researched for this dossier: the codebase contains
multiple **sophisticated, well-built, well-tested modules that are never actually wired into the
live or backtest execution path**, sitting alongside simpler modules that ARE live but lack
equivalent sophistication or coverage. Examples: `portfolio/optimizer.py` (dead, and literally
can't be imported — `ImportError`), `risk/manager.py` (dead), `strategy/stock_ranker.py` (dead),
`strategy/market_filter.py` and `strategy/quality_filter.py` (dead), `indicators/momentum.py` /
`volatility.py` / `volume.py` (test-only), `universe/ipo.py` (a fully-built subsystem that has
never fired once in production). See `09_Open_Questions.md` for the full list and `07_Code_Map.md`
for per-file detail. This pattern should inform how much weight to give "the code supports X" as a
claim — always check whether X is actually reachable from `main.py` / the daily runner / the
backtest engine before assuming a capability is live.

## How to read this dossier

- `02_Architecture.md` — how data flows through the system end to end, live and backtest.
- `03_Strategy.md` — the actual trading logic: regime, entry, exit, scoring, sizing.
- `04_Backtest.md` — the simulation engine, metrics, and test coverage.
- `05_Research.md` — the history of strategy-improvement attempts, what was tried and rejected/kept, and why.
- `06_Validation.md` — how results are validated (out-of-sample, stress tests, robustness gate) and the current honest baseline.
- `07_Code_Map.md` — file-by-file catalogue of the whole codebase.
- `08_Project_Memory.md` — live incident history and how each was fixed.
- `09_Open_Questions.md` — known bugs, dead code, and gaps not yet acted on.

This dossier reflects the state of the codebase as documented by six exhaustive subsystem research
passes completed 2026-07-06, cross-referenced against `git log`, the live SQLite DB where directly
queried, and this project's accumulated session memory. No code changes were made in the course of
producing it.
