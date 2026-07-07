# 03 — Strategy

## Regime detection (`strategy/regime.py`)

`detect_regime()` is the single source of truth for BULL/BEAR classification, used identically by
both the live runner and the backtest engine (this parity was itself a 2026-07-02 bugfix — see
`08_Project_Memory.md`, "regime signal divergence"). Regime-dependent helper functions
(`regime_max_slots`, `regime_position_factor`, `regime_min_score`) are confirmed to **ignore their
`regime` argument entirely and return constants** — i.e. despite the naming, position sizing and
slot counts do not currently vary by regime through these functions; whatever regime sensitivity
exists lives elsewhere (the BULL/BEAR branch in `strategy/signals.py` and `runner/daily_runner.py`).

## Entry (`strategy/entry.py`)

An 8-stage sequential BUY gate — a candidate must clear all stages in order (liquidity/turnover
floor, trend confirmation, momentum, RS rank, overextension check, volatility filter, score
threshold, final regime/market check) to generate a signal. `MIN_DAILY_TURNOVER` is defined twice
(duplicated constant) — a drift risk if one copy is ever tuned without the other.

**The "ema_50 is actually EMA(100)" bug**, traced end to end:
`config/settings.py` (`EMA_SLOW` defaults to 100, no YAML override exists) →
`indicators/trend.py::compute_trend()` computes `EMA(EMA_SLOW)` and returns it under dict key
`"ema_slow"` → `indicators/composite.py` re-assigns this value to the indicator dict under the
**misleading key `"ema_50"`** → `strategy/entry.py`'s overextension check and the final "50 EMA >
100 EMA" trend-confirmation gate end up comparing two near-identical ~100-period EMAs, a
comparison dominated by noise rather than a genuine fast/slow trend cross → `strategy/signals.py`'s
`TREND_BREAK` exit condition is also actually testing against a 100-EMA despite its log message
calling it a 50 EMA. This is a naming/semantics bug, not obviously a directional CAGR bug, but it
means nothing in this codebase currently implements a real 50/100 EMA cross — every "50 EMA" gate
is silently a "100 EMA vs 100 EMA" comparison. See `09_Open_Questions.md`.

## Exit (`strategy/exit.py`)

`initial_stops()` sets the entry stop; `update_trailing_stop()` ratchets it up on new highs and
(via the dormant-by-default `REGIME_AWARE_TRAIL` flag — deployed live 2026-07-01, see
`08_Project_Memory.md`) also tightens the trail on a BEAR regime flip, not only on new-high days.
`check_exit_conditions()` evaluates all exit triggers each day: trailing stop, `TREND_BREAK`
(actually gated on the mislabeled ~100 EMA, above), max-hold-days, score-drop, etc.

## Signal orchestration (`strategy/signals.py`)

`generate_signals()` is the top-level daily orchestrator. In BEAR regime it only ever buys the
safe-haven ETF — no normal-universe entries are generated. **There is no top-N cap applied on daily
BUY signals** at this layer (capping happens downstream in `portfolio/allocator.py`'s
`can_open_position`, not here). The `index_confirming` parameter is accepted by
`check_entry()`'s signature but confirmed **silently dropped/unused** inside the function body.

## Scoring & ranking

- `strategy/scoring.py::score_signal()` computes the composite entry score; a `score_label` helper
  exists but is dead.
- `strategy/relative_strength.py::compute_rs_for_all()` computes cross-sectional RS rank; the
  composite score used for ranking is `RS-rank × ATR%`.
- `strategy/stock_ranker.py` is a **fully-built but completely disconnected** ranking/capping/
  audit-log module — nothing in the live path calls into it.

## Dead / non-live strategy code (confirmed by direct inspection)

- `strategy/market_filter.py` — dead AND latently buggy: it checks regime strings
  (`BULL_TREND`/`SIDEWAYS`/`HIGH_VOL`) that `detect_regime()` never actually returns, so even if
  wired in it would silently never match.
- `strategy/quality_filter.py` — dead, superseded by the looser liquidity floor already inline in
  `strategy/entry.py`.
- `strategy/defensive_portfolio.py::build_target_weights()` — references
  `LIQUIDBEES_TARGET_WEIGHT`, which is never defined anywhere in the file or imported — a latent
  `NameError` that is currently dormant only because `LIQUIDBEES_ENABLED` defaults to `False`.
- `indicators/momentum.py`, `indicators/volatility.py`, `indicators/volume.py` — confirmed
  test-only; production instead reimplements RSI/MACD/ATR/BB/volume inline inside
  `indicators/composite.py`.

## Indicator math note

Both `indicators/composite.py` (production) and `backtest/engine.py`'s inline `_precompute_all()`
independently use **Cutler's RSI** (simple rolling-mean, not Wilder's smoothing) and a simple
rolling-mean ATR, confirmed explicitly by `scripts/verify_indicators.py`'s own docstring ("This is
what composite.py currently uses"). Any indicator value eyeballed against a standard charting
platform (which defaults to Wilder's smoothing) will systematically differ from what the strategy
internally gates on. This is a known, intentional-by-inertia divergence, not something actively
causing incorrect trades — but worth knowing before comparing internal indicator dumps to any
external chart.
