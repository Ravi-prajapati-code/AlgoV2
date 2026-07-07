# 07 — Code Map

File-by-file catalogue of the codebase, organized by the six research partitions used to produce
this dossier. "LIVE" means reachable from `main.py` / `runner/daily_runner.py` /
`backtest/engine.py`; "DEAD" means confirmed unreachable from any of those.

## Backtest / metrics / research scripts / tests (Partition A)

| File | Status | Notes |
|---|---|---|
| `backtest/engine.py` | LIVE | `BacktestResult`, `BacktestEngine.run()`, hybrid regime state machine, `_precompute_all()`, `_entry_fill_price()`. See `04_Backtest.md`. |
| `backtest/metrics.py` | LIVE | `calculate_metrics()`; population-variance Sharpe/Sortino; empty-vs-full dict key-parity risk. |
| `backtest/reporter.py` | LIVE | `stop_loss_2pct_est` is a documented estimate, not measured; `save_daily_scan_log` has a stale schema. |
| `backtest/slippage.py` | DEAD | `apply_slippage` zero callers; `simulate_partial_fill` imported but unused. |
| `charges/calculator.py` | LIVE | Full Upstox NSE delivery-charge schedule; GST correctly excludes STT/stamp duty. |
| `scripts/correlation_analysis.py` | tool | Sector-cap validity + sizing headroom finding, see `05_Research.md`. |
| `scripts/feature_importance.py` | tool | Exploratory. |
| `scripts/trade_attribution.py` | tool | 31+ day-hold edge finding. |
| `scripts/early_heat_experiment.py` | tool | Research script. |
| `scripts/out_of_sample_validator.py` | tool | See `06_Validation.md`. |
| `scripts/robustness_gate.py` | tool | See `06_Validation.md`. |
| `scripts/stress_test_scenarios.py` | tool | Backs `robustness_gate.py`'s stress stage. |
| `scripts/walk_forward.py` | tool | Monthly decay monitor; uses sample-variance Sharpe (methodology divergence, see `06_Validation.md`). |
| `scripts/ensemble_eval.py` | tool | Has a stale hardcoded `END_DATE` needing manual bumps. |
| `scripts/scenario_runner.py` | tool | `THRESHOLDS` dict duplicated elsewhere — drift risk. |
| `scripts/verify_indicators.py` | tool | Source of the Cutler's-RSI/simple-ATR divergence finding. |
| `scripts/universe_audit.py` | tool | Depends on a stale CSV, no freshness check. |
| `tests/test_backtest.py`, `test_indicators.py`, `test_signals.py`, `test_portfolio.py`, `test_charges.py` | tests | Standard unit coverage. |
| `tests/test_manager_execution.py` | tests | Tied to 4 dated, git-log-verified incident-fix commits: `bcf4441`, `162726b`, `c5460f6`, `96df9bd`. |

## Strategy / indicators (Partition B)

| File | Status | Notes |
|---|---|---|
| `strategy/entry.py` | LIVE | 8-stage BUY gate; `MIN_DAILY_TURNOVER` duplicated constant. |
| `strategy/exit.py` | LIVE | `initial_stops`, `update_trailing_stop` (dormant `REGIME_AWARE_TRAIL` flag now active live), `check_exit_conditions`. |
| `strategy/market_filter.py` | DEAD + buggy | Checks regime strings `detect_regime()` never returns. |
| `strategy/quality_filter.py` | DEAD | Superseded by `entry.py`'s looser liquidity floor. |
| `strategy/regime.py` | LIVE | Source of truth post-fix; `regime_max_slots`/`regime_position_factor`/`regime_min_score` all ignore their `regime` arg. |
| `strategy/relative_strength.py` | LIVE | `compute_rs_for_all`; composite = RS-rank × ATR%. |
| `strategy/scoring.py` | LIVE | `score_signal`; `score_label` dead. |
| `strategy/signals.py` | LIVE | `generate_signals()` orchestrator; BEAR only buys safe-haven; no top-N cap here; `index_confirming` param silently dropped. |
| `strategy/stock_ranker.py` | DEAD | Fully built, fully disconnected. |
| `strategy/defensive_portfolio.py` | LIVE (dormant path buggy) | `LIQUIDBEES_TARGET_WEIGHT` NameError, dormant since `LIQUIDBEES_ENABLED=False`. |
| `indicators/composite.py` | LIVE | Production indicator engine; inline RSI/MACD/ATR/BB/volume (Cutler's convention). |
| `indicators/momentum.py`, `volatility.py`, `volume.py` | DEAD | Test-only. |
| `indicators/trend.py` | LIVE | `compute_trend()` — source of the ema_50/EMA(100) mislabeling. |

## Portfolio / risk (Partition C)

| File | Status | Notes |
|---|---|---|
| `portfolio/manager.py` | LIVE (981 lines) | Central order orchestrator: `gtt_stop_limit_price()`, `cancel_stale_gtts()`, `_reconcile_gtt_stops()`, `TRAIL_BREACH_IMMEDIATE`, score-drop/ride-winner/rotation overlays. |
| `portfolio/allocator.py` | LIVE | `can_open_position` — where the daily BUY-signal cap actually gets enforced. |
| `portfolio/optimizer.py` | DEAD, unimportable | `from strategy.regime import regime_position_factor, Regime` — `Regime` doesn't exist; verified `ImportError` on direct import. |
| `portfolio/risk.py` | LIVE | The actually-live simple circuit breaker, `can_open_new_trades`. |
| `portfolio/sizer.py` | LIVE (partial) | `calculate_shares_for_value` live; ATR-based `calculate_shares` dead/test-only. |
| `portfolio/app.py` | DEAD | Orphaned duplicate of `dashboard/app.py`. |
| `risk/manager.py` | DEAD | `RiskManager`; only referenced by dead `optimizer.py` and a CLI diagnostic in `main.py`. |

## Universe management (Partition D)

| File | Status | Notes |
|---|---|---|
| `universe/scanner.py` | LIVE | Candidate discovery. |
| `universe/scorer.py` | LIVE | 6-factor weighted percentile-rank composite score. |
| `universe/manager.py` | LIVE | Churn-protected promotion/demotion; Guard 1 (P&L veto), Guard 2 (anchor protection, empirically -6.5pp CAGR if anchors removed); `min_data_weeks`/`liq_degradation_pct` read but never enforced. |
| `universe/rebalancer.py` | LIVE | `RebalancingEngine`, 4-cadence orchestration. |
| `universe/audit.py` | LIVE | Quarterly health report; stale TODO comment. |
| `universe/ipo.py` | DEAD (no-op) | `add_ipo()` never called; 0 rows in DB. |
| `universe/reporter.py` | LIVE (drift risk) | `_strategy_loser_section()` duplicates `manager.py`'s thresholds as hardcoded local constants. |
| `data/universe.py` | LIVE | `get_all_symbols()` static-list-plus-extras union — center of the loser-leak bug; docstring's "119 stocks" is stale vs actual 100. |
| `scripts/universe_scheduler.py` | LIVE (partial) | Cron dispatch; `--mode daily` never actually scheduled. |
| `scripts/universe_audit.py` | tool | Standalone CSV-based analyst tool. |
| `db/universe_repo.py`, `db/schema_universe.sql` | LIVE | `save_weekly_metrics()` does a runtime `ALTER TABLE` for a 5→6-factor redesign never reflected in the static schema file. |

## Data / DB / broker / runner (Partition E)

| File | Status | Notes |
|---|---|---|
| `data/fetcher.py`, `data/providers/upstox_provider.py` | LIVE | Price fetching. |
| `data/instruments/mapper.py` | LIVE | Symbol/instrument mapping. |
| `db/models.py`, `db/repository.py` | LIVE | `close_position_and_save_trade()` atomic transaction; `was_sold_today()` T+1-residue guard. |
| `db/schema.sql`, `db/schema_universe.sql` | LIVE | Full schema; see universe schema drift note above. |
| `broker/base.py`, `paper.py`, `token_refresh.py`, `upstox_auth.py`, `upstox.py` | LIVE | Full order lifecycle incl. GTT; GOLDBEES unfillable-limit-price fix lives here. |
| `runner/daily_runner.py` | LIVE | Hybrid BULL/BEAR/defensive state machine; `sync_portfolio_with_broker`; hardcoded `_NSE_HOLIDAYS`. |
| `runner/intraday_runner.py` | DEAD (prototype) | Simpler, unhardened, not cron-wired. |
| `runner/repository.py` | DEAD | Orphaned legacy duplicate, zero callers. |
| `runner/signal_output.py` | LIVE | Signal formatting/output. |
| `main.py` | LIVE | CLI subcommand table; `_audit_live_session()` deliberately bypasses `get_available_cash()`'s silent-401-swallowing. |

## ML / monitoring / config (Partition F)

| File | Status | Notes |
|---|---|---|
| `ml/features.py`, `model.py`, `trainer.py` | DEAD (disabled) | `ML_ENABLED=False`, win_rate=26% documented reason; two inconsistent `ML_ENABLED`/`ML_MIN_CONFIDENCE` definitions between `ml/model.py` and `config/settings.py`. |
| `monitoring/drift_monitor.py` | LIVE | |
| `monitoring/gtt_coverage.py`, `gtt_price_audit.py` | LIVE (now cron-installed) | Previously built but never installed — fixed 2026-07-06. |
| `monitoring/logger.py`, `performance.py` | LIVE | |
| `notifications/telegram.py` | LIVE (minor bug) | Static "Paper trading" disclaimer regardless of actual live/paper mode. |
| `dashboard/*` | LIVE | Read-only view layer. |
| `config/settings.py` | LIVE | Central parameter table; several params (`MAX_HOLD_DAYS`, `ADX_TREND_THRESHOLD`, `MIN_SIGNAL_SCORE`, YAML `RS_THRESHOLD`) have no downstream consumer. |
| `config/risk_config.yaml`, `strategy_config.yaml` | LIVE (partial) | ~Half of `risk_config.yaml`'s keys unread by any code. |
| `scripts/health_check.py`, `backup_db.py`, `reconcile_positions.py`, `recovery_manager.py`, `refresh_token.py`, `send_token_reminder.py`, `auto_token.py`, `auto_token_playwright.py`, `inject_capital.py`, `daily_pnl_summary.py` | LIVE | Operational scripts, all cron- or manually-invoked. |
| `scripts/setup_cron.sh` | LIVE | Full ~10-job cron table. |
