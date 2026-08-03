# Doc 41 — Architecture Map

**Date**: 2026-07-31
**Purpose**: Standing reference for how the codebase is actually wired — call chains, shared vs. duplicated logic, dead/orphaned code. Read this before proposing any change that touches strategy/portfolio/backtest, per `CLAUDE.md`'s "study the repository first" step. Companion doc: `docs/42_Research_State_Registry.md` (what's been tested and decided).

Two live pipelines exist and do **not** share an execution engine — only shared decision-logic functions. §5 traces both call chains explicitly and flags every divergence point; read that before trusting any backtest result to predict live behavior.

---

## 1. config/

Precedence: **env var > `risk_config.yaml` > `strategy_config.yaml` > hardcoded default**, resolved through `config/settings.py` (sole Python interface — nothing else reads env/YAML directly).

- `config/settings.py` — reads `strategy_config.yaml` for `BLOCKED_SECTORS`/`BLOCKED_SYMBOLS`/a few entry sub-params; everything else env-driven with hardcoded fallback.
- `config/risk_config.yaml` — position sizing / risk knobs, outranks `strategy_config.yaml`.
- `config/watchlist_nse.py` (748 lines) — static universe (`WATCHLIST`, `SYMBOL_TO_SECTOR`, `ALL_SYMBOLS`). Hand-revised multiple times with no dated record before the DB-tracked snapshot start — pre-2026-07-06/07 point-in-time membership is **structurally unrecoverable** (single squashed git commit).
- `config/universe_removed.py` — blacklist for the dynamic universe, separate from `BLOCKED_SYMBOLS`.
- `config/universe_config.yaml` — dynamic universe manager (`universe/` package) config only; explicitly scoped by its own header comment to not touch strategy params.
- **`EXECUTION_TIMES=["09:17"]`** — read only by `backtest/engine.py` and `scripts/live_backtest_parity_check.py`, never by `runner/daily_runner.py` (live). Not a live/backtest clock mismatch — it's an intraday-minute-bar filter that's a no-op on the standard daily-bar backtest (`00:00:00 <= 09:17:00` always true). Only matters if a minute-resolution backtest is ever run for real validation, in which case the 09:17 cutoff vs. live's ~14:50 fill would need reconciling.

## 2. data/

- `data/fetcher.py` — cache order **Parquet → DB → Upstox API**, incremental gap-fill. `REQUIRE_CACHED_DATA=1` hard-fails instead of silently pulling fresh live data (docs/29 Rule 1 guardrail). `live_mode=True` bypasses cache for a fresh "today" fetch.
- `data/providers/upstox_provider.py`, `data/instruments/mapper.py` — Upstox API wrapper, instrument-key lookup.
- `data/universe.py` — sector/symbol metadata, distinct from `universe/` (dynamic promotion/demotion). `get_all_symbols()` (today's list, static ∪ dynamic CORE) vs. `get_all_symbols_as_of(as_of)` (point-in-time correct; **raises `UniverseHistoryUnavailable`** rather than silently substituting today's list — the direct fix for the docs/13/14 universe look-ahead finding).

## 3. indicators/

Shared library (`composite.py`, `momentum.py`, `trend.py`, `volatility.py`, `volume.py`).

**Standing divergence**: `backtest/engine.py::_precompute_all()` independently **reimplements** EMA/ATR/ADX/RSI/MACD/turnover/composite_rank rather than calling `indicators/`. docs/33 found a real historical bug from exactly this split (backtest's ATR/RSI used simple rolling mean vs. live's correct Wilder's smoothing, invalidating docs/32's EMA-sweep numbers) — that bug is fixed, but the underlying duplication was never eliminated, so the risk of recurrence stands.

## 4. strategy/ — the one genuinely shared layer

`signals.py`, `entry.py`, `exit.py`, `regime.py` (plus `charges/calculator.py`) are called **identically** by both live and backtest — no divergence here.

- `signals.py::generate_signals()` — exit eval (skips non-`strategy`-origin positions, docs/30), safe-haven/GOLDBEES logic, BEAR-regime defensive switch, `check_entry()` gating, and the `ENTRY_MODE` research-variant branch (`SHUFFLE_RS`, `REVERSE_RS`, `RANDOM_ALL/ELIGIBLE`, `PURE_ADX_BREAKOUT`, `SURVIVAL_RANK`, `PURE_RS`, `FULL`). In-code comment marks `SURVIVAL_RANK` explicitly **not validated for live use** (same bar as other rejected/untested modes).
- `entry.py::check_entry()` — RS threshold, optional 200-EMA gate, VCP/breakout, overextension cap, turnover floor, EMA/SuperTrend/ADX/MACD trend-strength — `ENTRY_MODE` skip-flags bypass specific gates for attribution runs.
- `exit.py` — `update_trailing_stop()` is a **no-op stub**. `HARD_STOP_LOSS_ENABLED=False`, `PROFIT_LOCK_ENABLED=False` by default (both explicitly rejected/test-only, docs/24). Primary exit driver: `MOMENTUM_RSI_THRESHOLD=50` + `TREND_BREAK` (lives in `signals.py`, not `exit.py`).
- `regime.py` — `detect_regime()` (BULL/BEAR vs. index 100-EMA), `is_strong_bull()`, `is_buy_allowed()`, `is_index_confirming()`.

## 5. Call chains (highest-value section)

### 5.1 Live/paper
```
main.py::cmd_run() → runner/daily_runner.py::run()
  → data/fetcher.py::fetch_all(live_mode=True)
  → strategy/regime.py::detect_regime()
  → strategy/relative_strength.py::compute_rs_for_all()
  → backtest/engine.py::_compute_sector_durability()   [imported directly from backtest — see note]
  → strategy/signals.py::generate_signals() → entry.py::check_entry(), exit.py::check_exit_conditions()
  → portfolio/manager.py::PortfolioManager.process_signals()
      → portfolio/allocator.py, portfolio/sizer.py, portfolio/risk.py
      → ml/model.py (buy veto, gated by ML_ENABLED=False)
      → broker/upstox.py::UpstoxBroker.place_order() (only --live)
  → notifications/telegram.py::send_daily_summary()
```
`_compute_sector_durability` is imported straight from `backtest/engine.py` — an explicit in-code comment confirms this was pulled out specifically to close a real prior live/backtest divergence bug, i.e. it's the one place duplication was fixed by sharing code instead of maintaining two copies.

### 5.2 Backtest
```
main.py::cmd_backtest() → backtest/engine.py::BacktestEngine.run()
  → _precompute_all()  [standalone indicator reimplementation, §3]
  → strategy/regime.py, relative_strength.py, signals.py→entry.py/exit.py  [SHARED]
  → INLINE execution in run(): sells, buys, rank-replacement, rotation,
    ride-the-winner, score-drop-exit, pyramid-add — NOT PortfolioManager,
    reimplemented directly in the ~125-985 line run() loop
  → _lagged_fill_price()  [models live's ~14:50 fill vs backtest's same-bar close]
  → backtest/metrics.py, backtest/reporter.py
```

### 5.3 Explicit fidelity gaps

| Layer | Live | Backtest | Status |
|---|---|---|---|
| Portfolio execution | `portfolio/manager.py::PortfolioManager` | Inline reimplementation in `BacktestEngine.run()` | **Real, standing duplication** — fix must be manually mirrored both places |
| Indicators | `indicators/` | `_precompute_all()` | **Real, standing duplication** — docs/33's ATR/RSI bug came from exactly this |
| ML buy-veto | `portfolio/manager.py` checks `ML_ENABLED` | No ML code in `backtest/engine.py` | **Dormant gap** — inert while `ML_ENABLED=False` (26% win rate, judged harmful), would silently break parity if re-enabled without a backtest-side equivalent |
| VCP / `ema_200` | live-only (`indicators/composite.py`) | `_precompute_all()` never sets `vcp_detected`/`vcp_pivot`/`ema_200` | **Newly found gap (docs/40, 2026-07-31)** — VCP entries and `TREND_GATE_200_ENABLED` have **never been simulated in any backtest run in this project's history** |
| Fill timing | ~14:50 IST same-day | Same-bar close, mitigated by `NEXT_DAY_CLOSE_FILL_ENABLED`/`_lagged_fill_price()` | Modeled, not accidental |
| `_compute_sector_durability` | imported from backtest | native | **Fixed** — shared, not duplicated |

### 5.4 `risk/manager.py::RiskManager` — orphaned

Instantiated **only** by `main.py::cmd_risk_report()`, a standalone CLI command — not called anywhere in the live or backtest execution path. Live position sizing/gating actually runs through `portfolio/sizer.py` and `portfolio/risk.py::can_open_new_trades()`.

## 6. portfolio/ (the live path's real engine)

- `manager.py` (879 lines) — `PortfolioManager.process_signals()`: Score-Drop-Exit, Ride-the-Winner, Rotation, sell/buy execution, ML-veto, broker orders, snapshot persistence. `strategy_value()`/`account_value()` two-lens model (docs/30). `portfolio_value()` is a **deprecated alias**.
- `allocator.py` — `portfolio_invested_value()`, `sector_allocation()`, `can_open_position()`.
- `sizer.py` — `calculate_shares_for_value()` (used). `calculate_shares()` (ATR-risk sizer) is **dead code**, referenced only from `tests/test_portfolio.py`.
- `risk.py` — `can_open_new_trades()`, separate from and not routed through `risk/manager.py`.

## 7. broker/

- `upstox.py::UpstoxBroker` — actually used; places GTT orders (V3 API, LIMIT-on-trigger semantics, no true market-on-trigger).
- `paper.py::PaperBroker` — fully implemented but **dead code**, never instantiated (paper mode is DB-only in `daily_runner.py`, doesn't go through a `Broker` at all).
- `base.py`, `upstox_auth.py`, `token_refresh.py`.

## 8. universe/

Dynamic CORE/WATCHLIST promotion-demotion, governed by `config/universe_config.yaml`, distinct from the static list. `manager.py`, `scanner.py`, `scorer.py`, `rebalancer.py`, `audit.py`, `ipo.py`, `reporter.py` — orchestrated by `scripts/universe_scheduler.py`, its own schedule, feeds the DB tables `data/universe.py::get_all_symbols()` reads. Not part of the daily signal-generation call chain.

## 9. ml/

`ML_ENABLED=False` by default (26% win rate, described in code comments as harmful). `features.py`, `model.py` (stale docstring referencing `risk/manager.py::RiskManager` that no real call reflects), `trainer.py`.

## 10. monitoring/ — confirmed active, not stale

- `gtt_coverage.py` — cron `40 15 * * 1-5`, cross-checks live holdings against active GTT orders, alerts on any naked position.
- `gtt_price_audit.py` — checks GTT trigger price matches what the system believes the stop should be; guards specifically against the 2026-07-01 bug (`update_trailing_stop()` ratcheted the broker GTT but never persisted to DB, fixed `c5460f6`).
- `drift_monitor.py`, `logger.py`, `performance.py`.

## 11. db/, broker/, charges/, notifications/, dashboard/ — thin, no known issues

- `db/repository.py` (OHLCV cache CRUD), `db/models.py` (Signal/Position), `db/universe_repo.py` (dynamic universe tables, point-in-time helpers).
- `charges/calculator.py` — shared, no live/backtest divergence, confirmed GST/STT/stamp-duty correct.
- `notifications/telegram.py`, `dashboard/` (Streamlit).

## 12. Known dead/orphaned code

| Item | Status | Evidence |
|---|---|---|
| `portfolio/sizer.py::calculate_shares()` | Dead in prod path | Only called from `tests/test_portfolio.py` |
| `risk/manager.py::RiskManager` | Orphaned | Only instantiated by `cmd_risk_report()` CLI |
| `broker/paper.py::PaperBroker` | Dead | Never instantiated |
| `export_daily/` | Orphaned data | No Python references anywhere |
| `strategy/exit.py::update_trailing_stop()` | No-op stub | Direct read |
| `HARD_STOP_LOSS_ENABLED`, `PROFIT_LOCK_ENABLED` | Off by default | Explicitly rejected/test-only, docs/24 |
| `ENTRY_MODE` research variants (`SURVIVAL_RANK` etc.) | Not validated for live | In-code comment |
| `ML_ENABLED` | Off by default | 26% win rate; re-enabling creates dormant backtest-parity gap |
| `portfolio/optimizer.py` | Unimportable (`ImportError`) per docs/07/09 | Keep-or-delete decision never made (docs/12 Q2 item, still open) |
| `strategy/stock_ranker.py`, `strategy/market_filter.py` | Dead (+buggy) per docs/07 | Same, unresolved |
| `_archive/` (9 scripts) | One-off/superseded, mostly undocumented | `check_integrity.py`, `fix_wipro.py`, `import_backtest_trades.py`, `init_paper_trading.py`, `patch_base.py`, `save_minute_data.py`, `sync_portfolio.py` (superseded by `daily_runner.py::sync_portfolio_with_broker()`), `test_data_availability.py`, `test_upstox_connection.py` |

## 13. tests/ (10 files)

`test_backtest.py`, `test_charges.py`, `test_core_universe_snapshot.py`, `test_indicators.py`, `test_manager_execution.py` (largest, 250 lines — `PortfolioManager.process_signals()`), `test_portfolio.py` (only remaining caller of dead `calculate_shares()`), `test_regime.py`, `test_signals.py`, `test_static_universe_sync.py`, `test_universe_blocklist.py`.

## 14. scripts/ (partial inventory, ~52 files — not exhaustively itemized)

`universe_scheduler.py` (universe promote/demote/audit orchestration), `live_backtest_parity_check.py` (found docs/33's ATR/RSI bug), `health_check.py`, `smoke_test.py`, `trade_attribution.py` (the study referenced in `signals.py`'s `SURVIVAL_RANK` comment), `robustness_gate.py` (the TRAIN/OOS/4-stress validation gate every accepted lever must clear), `signal_level_attribution.py` (docs/40). Remaining ~44 scripts are one-off research/backfill/migration/reporting utilities — consult filenames or `git log -p <file>` directly if a specific one matters for future work; not itemized here to avoid a stale/inaccurate inventory.

---

**Note on repo state as of 2026-07-31**: `docs/35`, `docs/37`, `docs/39`, `docs/40` and this doc are uncommitted, as are working changes in `db/universe_repo.py` and `portfolio/manager.py`. Check `git status` before assuming the committed history alone reflects current state.
