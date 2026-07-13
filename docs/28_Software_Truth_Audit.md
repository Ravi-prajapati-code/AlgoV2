# 28. Software Truth Audit

2026-07-11. The fourth pillar, alongside `docs/23` (entry-rule
assumptions), `docs/26` (portfolio-construction truth), and `docs/27`
(strategy-concept catalog). Those three ask "is the *strategy* right."
This one asks a different, prior question: **is the code actually
doing what we think it does — bug-free, config-consistent,
reproducible, and tested — independent of whether the strategy itself
is good?** A profitable backtest run through buggy or unreproducible
machinery is not evidence of anything.

Built from four parallel research passes (dead code, duplicate logic,
config drift, determinism/test coverage) across the whole repo.

## Headline finding: backtest doesn't simulate ~half of live's active position management

`strategy/defensive_portfolio.py:45,50,53` defines three flags, **all
default-ON in live right now**:

- `ROTATION_ENABLED=1` — rotate the weakest open position into a
  materially stronger candidate.
- `RIDE_WINNER_ENABLED=1` — sell the weakest position when a winner
  diverges strongly (winner-pyramiding via eviction).
- `SCORE_DROP_EXIT_ENABLED=1` — exit a held position on sustained RS
  rank decline.

All three are wired into `portfolio/manager.py:250-443` (the live/paper
path) and called from `runner/daily_runner.py:750-751`. **None of the
three names appear anywhere in `backtest/engine.py`** (confirmed via
repo-wide grep — zero matches). `scripts/robustness_gate.py` calls
`backtest/engine.py` exclusively, so it cannot see this logic either.

**What this means concretely**: every `robustness_gate.py` "PASS" or
"REJECT" verdict produced this entire research arc — including today's
E1 idle-cash ablation and the RSI-threshold sweep — was computed
against a simulated strategy that is missing three enabled live
position-management rules. This is not the same class of bug as the
`df9856f` fidelity fixes (which corrected *mismatched* implementations
of shared logic); this is logic that live runs and backtest **does not
run at all**. The backtest CAGR/Sharpe/MDD numbers this whole project
treats as ground truth may not reflect what live capital is actually
doing.

**Not invalidated by this**: the earlier "no real-time rotation
trigger works" conclusion
([[rotation_logic_synthesis_20260710]], `scripts/rotation_opportunity.py`)
was derived from direct trade-log/decision-log analysis, not from a
backtest simulation toggling these flags — so that specific finding
stands. But it means nobody has ever backtested what
`ROTATION_ENABLED`/`RIDE_WINNER_ENABLED`/`SCORE_DROP_EXIT_ENABLED`
actually do to portfolio-level returns, only whether a *better*
real-time trigger exists for the first one.

**Priority: highest in this document.** Before trusting any future
`robustness_gate.py` verdict, either (a) port this logic into
`backtest/engine.py` so gate runs reflect real live behavior, or (b) at
minimum, explicitly document every existing gate verdict as
conditional on "assuming rotation/ride-winner/score-drop are net-zero
or net-positive," which has never been checked.

## Second finding: a related, smaller backtest/live exit-logic gap

`runner/daily_runner.py:592-600` — live bear-swing positions get an
extra `TREND_BREAK` exit (2 consecutive days below EMA50). This is
**absent** from `backtest/engine.py`'s bear-swing exit block
(`:335-349`, RSI/momentum-decay only). Backtest holds bear-swing losers
longer than live actually would. Smaller in scope than the headline
finding but the same category of bug.

## Dead code

| Location | Item | Note |
|---|---|---|
| `strategy/scoring.py` | (was `score_to_size_factor`/`SCORE_BUCKETS`/`score_label`) | **Deleted this session** — see [[rsi_threshold_sweep_20260711]]. `score_signal` itself is only referenced by `tests/test_signals.py`, not by any production code path (`signals.py`/`manager.py`/`engine.py` all read `ind.get("composite_rank")` inline) — low-priority residual, not urgent. |
| `strategy/signals.py:15` | `MIN_DAILY_TURNOVER = 20_000_000` | Orphaned — superseded by `config/settings.py:134`'s centralized version per that file's own comment, never deleted here. |
| `strategy/exit.py:17` | `update_trailing_stop()` | Documented no-op, leftover from the removed trailing-stop feature. Tests-only. |
| `strategy/defensive_portfolio.py:26,29-31,57` | `DD_BRAKE_PCT`, `ENTRY_CONFIRM_ADAPTIVE`, `ENTRY_CONFIRM_DIVISOR`, `CASH_PARK`, `QUALITY_STOCKS=[]` | Genuine abandoned feature flags — described in the module docstring as intended behavior, never read anywhere, not even within the same file. |
| `portfolio/sizer.py:18` | `calculate_shares()` (ATR risk-sizer) | Abandoned earlier sizing model — consistent with [[phase2_improvements]]'s ATR-sizing REJECTED verdict. Live/backtest both use `calculate_shares_for_value` instead. Tests-only. |
| `portfolio/manager.py:69,76` | `gtt_stop_limit_price()`, `_alert_naked_stop()` | Leftover from the removed GTT-stop-loss feature (module header explicitly says stop-loss/trailing-stop GTTs were removed). |
| `risk/manager.py` (whole module) | `RiskManager` class | Only wired to `main.py`'s `risk_report` CLI diagnostic — never touches the live/backtest trading path. See "Disconnected tooling" below — this one actively **misreports** live behavior, not just unused. |
| `indicators/momentum.py`, `volatility.py`, `volume.py` | `compute_rsi`/`compute_macd`/`compute_momentum`/`compute_bollinger`/`compute_atr`/`compute_volatility`/`compute_volume` | Entire modularized indicator library, tests-only — `indicators/composite.py` (the real live/backtest path) reimplements all of these inline instead of calling them. Abandoned first-pass modularization bypassed by a later rewrite, never deleted. |
| `config/settings.py:64,97,174,189,191` | `CASH_RESERVE_PCT`, `MARKET_FILTER_SMA`, `SAFE_HAVEN_YIELD_ANNUAL`, `PARTIAL_REGIME_MIN_CANDLES`, `EMA_WARMUP_DAYS` | Defined, never imported anywhere. `MARKET_FILTER_SMA=200` is actively misleading — regime detection (`strategy/regime.py:19`) hardcodes EMA(100), not this constant. |

## Duplicate / parallel logic

Beyond the two backtest/live gaps above (which are *missing* logic, not
duplicated-and-diverged logic):

- **Rank-replacement eviction** (`backtest/engine.py:558-603` vs
  `portfolio/manager.py:465-489`) — identical, deliberately kept in
  sync per comments, but still two independently-typed copies.
- **Drawdown-throttle sizing** — `backtest/engine.py`/`manager.py`
  (identical 2-tier 0.25x/0.50x) **vs.** `risk/manager.py:112`
  (binary single 0.5x, no tier-2 step) — **diverged**. Since
  `risk/manager.py` only feeds the CLI `risk_report`, this means the
  risk report an operator runs to sanity-check live exposure shows the
  wrong sizing behavior under deep drawdown.
- **Bear-swing entry turnover floor** — the literal `20_000_000`
  constant is hardcoded independently in `backtest/engine.py:388-396`
  AND `runner/daily_runner.py:626-638`, on top of the already-orphaned
  copy in dead `strategy/signals.py:15`. Three copies of one magic
  number.
- **Full indicator stack** (EMA20/50/100/150, ATR, RSI, turnover,
  vol_ratio) — `indicators/composite.py::compute_indicators` (live) vs
  `backtest/engine.py::_precompute_all` (`:760-902`) are two
  independent from-scratch reimplementations of the same formulas.
  Only `compute_supertrend` is genuinely shared. This exact pattern —
  the same logic written twice — is what produced the historical
  fidelity bugs fixed in `df9856f`; any future indicator tweak has to
  be applied by hand in two places or it silently diverges again.
- **MACD spans** — `backtest/engine.py`/`indicators/momentum.py`
  correctly import `MACD_FAST/SLOW/SIGNAL` from settings;
  `indicators/composite.py:112-115` hardcodes `12/26/9` directly.
  Currently the same values — latent divergence, not yet triggered.

## Config truth

- **Uncommitted `config/strategy_config.yaml` diff** removes
  `entry.rsi_buy_min/max`, `entry.min_volume_ratio`, and the entire
  `exit:` block (8 keys: stop_loss_pct, take_profit_pct,
  trailing_stop_pct, trail_tighten_threshold/pct,
  atr_trail_mult_initial/tight, max_hold_days) relative to git HEAD.
  Same *shape* of risk as the 2026-07-11 `max_open_positions` incident
  (uncommitted local drift) — functionally inert here (see next point)
  but should be committed or reverted deliberately, not left dangling.
- **11 yaml keys are dead** — `config/settings.py` never reads any of
  the `exit:` block or `rsi_buy_min/max`/`min_volume_ratio` keys.
  Confirmed by design: `strategy/exit.py` explicitly documents that
  stop-loss/take-profit/trailing-stop were removed in favor of
  signal-only exits (`docs/18-21` core finding). The yaml file still
  describes a stop-loss/take-profit design that no longer exists in
  code.
- **`RS_THRESHOLD` yaml key is fully dead** — `config/settings.py:106`
  reads `os.getenv("RS_THRESHOLD", "72.0")` directly, never
  `entry_cfg.get('rs_threshold', ...)`, unlike the very next line
  (`ADX_TREND_THRESHOLD`) which does read from yaml. Editing
  `strategy_config.yaml`'s `rs_threshold` today does nothing — it only
  "matches" by coincidence (both 72.0).
- **`config/strategy_config.yaml.bak`** — tracked in git since the
  initial commit, never updated, contains drifted values
  (`rs_threshold: 70.0`, `atr_trail_mult_initial: 2.5`) vs the real
  file. Not loaded by anything. A "which file is real" trap for a
  future auditor. Recommend deleting from git.
- **`ML_ENABLED` `.env` key is a dead no-op** — `config/settings.py:182`
  hardcodes `ML_ENABLED = False` directly, not via `os.getenv` at all.
  Setting it in `.env` has zero effect.
- **Core entry-gate thresholds with zero operator visibility** (no
  `.env` key, no yaml fallback, despite living right next to ones that
  do have both): `GTT_LIMIT_BUFFER_PCT`, `EXTENSION_CAP_PCT`,
  `BREAKOUT_PCT` (`config/settings.py:77,108-109`). `EMA_FAST/SLOW`,
  `EMA_CROSSOVER_LOOKBACK`, `MACD_FAST/SLOW/SIGNAL`,
  `VOLUME_SPIKE_MULTIPLIER` all call `entry_cfg.get(...)` but the yaml
  `entry:` section has none of those keys — always silently falls to
  the Python hardcoded default.
- **`strategy/exit.py:23` `MOMENTUM_RSI_THRESHOLD`** reads its own
  independent `os.getenv("MOMENTUM_RSI", "50")` entirely outside
  `config/settings.py`, contradicting that file's own docstring
  ("central configuration... all tunable parameters live here"). This
  is the exact parameter swept in [[rsi_threshold_sweep_20260711]] —
  worth centralizing into `settings.py` now that it's been shown to
  matter.
- **`strategy/defensive_portfolio.py` is an entire second parallel
  config surface** — ~21 parameters (`REGIME_SWITCH_DAYS`,
  `BULL_RECOVERY_DAYS`, `BEAR_SWING_RS_THRESHOLD`, `ROTATION_ENABLED`,
  `RIDE_WINNER_ENABLED`, `SCORE_DROP_EXIT_ENABLED`, etc.) bypassing
  `config/settings.py` entirely — none in `.env`, none in either yaml.
  This is the same module as the headline backtest-gap finding above;
  its config surface being invisible to `settings.py` likely
  contributed to it also being invisible to `backtest/engine.py`.
- **`strategy/regime.py:19`** hardcodes `span=100` for the regime EMA,
  independent of `config/settings.py`'s `EMA_SLOW` (nominally
  yaml-overridable). Same value today by coincidence; tuning
  `EMA_SLOW` would not affect regime detection.

## Reproducibility / determinism

- **Core logic is deterministic.** The only RNG usage
  (`strategy/signals.py:132,141`, `random.Random((ENTRY_MODE_SEED,
  today.toordinal()))`) is seeded from a fixed default (`42`) and the
  backtest's own date parameter, not wall-clock — and it's gated behind
  a non-default experimental `ENTRY_MODE=="SHUFFLE_RS"` path. No
  `np.random`, no unseeded `random`, no global mutable state that leaks
  between runs in `backtest/engine.py` or `portfolio/manager.py`.
- **Backtests are not guaranteed byte-identical on rerun.**
  `backtest/engine.py` takes data as a plain constructor argument; the
  real data path (`data/fetcher.py`) falls through parquet (currently
  empty, 0 files) → SQLite cache → **live Upstox API for any date gap**,
  writing results back into the cache. Many driver scripts (including
  `scripts/robustness_gate.py:75-76`) default their end date to
  `date.today()`. Two runs of "the same" backtest window, executed
  months apart, are not guaranteed to see identical underlying data —
  both because the fetch window itself silently shifts and because
  Upstox's historical data can be revised between calls (corporate
  actions/adjustments). This is a real threat to every "gate PASS" this
  project has ever recorded being trustworthy on a future rerun.

## Test coverage gaps

90 tests collected total. Zero direct test-file import anywhere for:

- **`runner/daily_runner.py`** (808 lines) — the entire live/paper
  execution entrypoint. **Zero test coverage.**
- **`runner/signal_output.py`** (94 lines).
- **`backtest/reporter.py`** (459 lines).
- **`strategy/defensive_portfolio.py`** — the exact module containing
  the headline finding's rotation/ride-winner/score-drop logic. This is
  not a coincidence: undertested code and unsimulated-in-backtest code
  are often the same code.
- **`strategy/regime.py`**, **`strategy/relative_strength.py`** —
  covered only indirectly via the end-to-end `test_backtest.py` path,
  never directly.
- **`strategy/signals.py`** (`generate_signals`) — same, indirect only.

## Disconnected tooling that actively misreports

`risk/manager.py`'s `RiskManager` (only used by `main.py`'s
`risk_report` CLI) independently reimplements drawdown-throttle sizing
as a single binary 0.5x cut — the real live/backtest logic is a 2-tier
0.25x/0.50x. Anyone running `risk_report` to sanity-check live exposure
under a drawdown is shown a number that doesn't match what the system
actually does. This is worse than dead code — it's *live, reachable,
wrong* code.

## Bottom line

The strategy-research findings in `docs/25-27` all rest on
`robustness_gate.py`, and `robustness_gate.py` rests on
`backtest/engine.py`. This audit found that engine is missing three
enabled, active live position-management rules entirely (headline
finding) and has a smaller missing-exit gap besides. Every existing
gate verdict should be read as "true for the simulated strategy that
excludes rotation/ride-winner/score-drop-exit," not as "true for what
live capital is doing today." That gap needs to be closed — or at
minimum explicitly labeled — before the next round of research trusts
another gate verdict.

Secondary but real: reproducibility is not guaranteed on distant
reruns (live-API fallback + `date.today()` defaults), a chunk of config
is decorative (dead yaml keys, a dead `.env` key, an entire
undocumented 21-parameter config surface in `defensive_portfolio.py`),
and the one disconnected diagnostic tool (`risk/manager.py`) actively
lies about live sizing behavior rather than just sitting unused.

## How to apply

Per the user's own engineering-vs-research framing
([[trading_strategy_research_framework_20260711]]): everything in this
document is **Track A (engineering)**, not Track B (research) — these
are correctness/consistency defects, not strategy hypotheses. Priority
order:

1. **Port rotation/ride-winner/score-drop-exit into
   `backtest/engine.py`**, or explicitly caveat every existing gate
   verdict as conditional on their absence. This is the one item that
   changes what "trustworthy result" means for this whole project.
   Highest priority, above any Stage 1/2 research item in `docs/27`.
2. Delete confirmed-dead code (`config/strategy_config.yaml.bak`,
   `defensive_portfolio.py`'s dead flags, `risk/manager.py`'s
   diverged sizing or fix it to match reality) — cheap, zero-risk,
   already-scoped by this audit.
3. Centralize `MOMENTUM_RSI_THRESHOLD` into `config/settings.py`
   (it's proven to matter — [[rsi_threshold_sweep_20260711]] — and
   currently lives outside the "central configuration" file that
   claims to hold every tunable parameter).
4. Add tests for `runner/daily_runner.py` and
   `strategy/defensive_portfolio.py` specifically — the second is
   where the headline finding lives; untested and unsimulated-in-backtest
   turned out to be the same code.
5. Reproducibility: consider pinning a versioned data snapshot for
   `robustness_gate.py` runs specifically, so "PASS" means the same
   thing on a rerun six months from now.
