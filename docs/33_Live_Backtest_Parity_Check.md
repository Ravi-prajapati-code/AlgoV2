# 33. Live/Backtest Indicator Parity Check — 2026-07-14

## Origin

Follow-up to the "biggest current risk" discussion: this arc's live/backtest fidelity bugs
(ema_50 mislabel, fill timing, replacement parity, cash buffer, missing rotation/ride-winner/
score-drop rules in backtest — docs/28) all trace to the same root cause: live and backtest each
maintain an **independent implementation** of shared logic, and nothing catches drift between them
until someone happens to investigate an unrelated question. Built `scripts/live_backtest_parity_check.py`
to make that drift loud instead of silent.

## What was verified first

Before building the check: **decision logic is already shared**, not duplicated. `backtest/engine.py`
imports and calls the real `check_entry` (`strategy/entry.py`), `check_exit_conditions`
(`strategy/exit.py`), and `generate_signals` (`strategy/signals.py`) directly — confirmed by reading
both call sites. The actual duplication is narrower than feared: only **indicator computation**
(`indicators/composite.py` for live vs. inline vectorized formulas in `backtest/engine.py`'s
`_precompute_all`) is a genuine second implementation of the same math.

## How the check works

Feeds identical cached daily OHLCV (same `data` dict, fetched once) through both implementations for
a fixed recent window (default: 15 symbols × last 15 trading days before `DEFAULT_GATE_END`), then
diffs every indicator field both sides expose:
- **Live path**: slices the cached df to each test date, calls the real `compute_indicators()`.
- **Backtest path**: instantiates the real `BacktestEngine`, calls its real `_precompute_all()` —
  not a re-transcription of its formulas, the actual production code path.
- Per-field tolerance table absorbs *intentional* rounding differences (live rounds `rsi`/`macd_hist`/
  `adx`/etc. to 2dp for display, backtest doesn't round some of the same fields) — anything not in the
  tolerance table is compared at 1e-6 (should be byte-identical).

Run: `REQUIRE_CACHED_DATA=1 python3 scripts/live_backtest_parity_check.py [--symbols N] [--dates N] [--end YYYY-MM-DD]`

## Result of first run

225 symbol-day snapshots (15 symbols × 15 dates). **Clean** on `close`, `ema_20`, `ema_50`, `ema_100`,
`ema_150`, `ema_entry_med`, `ema_entry_long`, `ema_exit_trend`, `turnover`, `vol_ratio`, `macd_hist`,
`high_20d`, `perf_10d`, `adx`, `st_direction` — zero unexpected divergence. Confirms the ema_50 fix
(docs/31) actually closed that gap, and confirms the DM/ADX formulas (visually different code, same
math) really are equivalent.

**Two new, real formula bugs found** — same class as the ema_50 mislabel, this time in the *math* not
a mislabeled key:

| Field | Live formula | Backtest formula | Effect size (this sample) |
|---|---|---|---|
| `atr` | Wilder's EMA: `tr.ewm(alpha=1/14, adjust=False).mean()` (comment: "matches TradingView") | Simple rolling mean: `tr.rolling(window=14).mean()` | avg diff -0.64 across 225 snapshots |
| `rsi` | Wilder's EMA of gains/losses: `delta.clip(...).ewm(alpha=1/14, adjust=False).mean()` | Simple rolling mean of gains/losses: `delta.where(...).rolling(window=14).mean()` | avg diff -2.45, spikes to ~18 points on individual symbol/dates (51.78 vs 33.75 seen directly) |

Both fields are used downstream: `atr` sizes every stop-loss/take-profit via `initial_stops()`
(`strategy/exit.py`) in *both* live and backtest; `rsi` gates every entry via `RS_THRESHOLD` and the
old `ENTRY_MODE` variants. Backtest's ADX computation internally uses the *correct* Wilder's EMA for
its own `atr14`/`plus_di`/`minus_di` — it just never applied that same smoothing to the separately
exposed `atr` and `rsi` fields. So this isn't "the formula was never known" — Wilder's smoothing is
already correctly used elsewhere in the same file, it just didn't get applied consistently to these
two fields.

**Fixed 2026-07-14.** Unlike the ema_50 fix (which only corrected *live*, backtest was independently
already correct — no past gate verdict affected), fixing `atr`/`rsi` in `backtest/engine.py` changes
backtest's own historical numbers — every past `robustness_gate.py` verdict that used ATR-based sizing
or RSI-gated entries was computed against the wrong smoothing. User explicitly approved fixing now +
re-running the gate to check for flipped verdicts (see baseline re-run below). `atr14` (already
correctly Wilder's-EMA, previously used only internally for ADX) is now reused directly as the
exposed `atr` value instead of a separate SMA calc — one less duplicate computation, not just a
formula swap. Re-ran the parity check post-fix: all 225 snapshots clean, `atr`/`rsi` now match to
float noise (<1e-3).

## Baseline re-run after the fix

**Second bug found doing this: `robustness_gate.py`'s baseline arm was contaminated.**
First re-run came back byte-identical between baseline and candidate on every
metric (N=244/93/336 exactly, all 4 stress scenarios exact matches) —
statistically impossible if the EMA lever still did anything. Root cause:
`clear_env()` only pops override keys from the parent process's `os.environ`;
each subprocess's own `main.py:24` (`load_dotenv(override=True)`) re-reads
`.env` straight off disk and stamps the values back in regardless, because
`ENTRY_EMA_MEDIUM`/`EXIT_TREND_EMA` (and `DD_THROTTLE_DISABLED`/
`SECTOR_DURABILITY_WEIGHT`) are now permanently sitting in `.env` from this
arc's deploy work. Baseline silently ran candidate config too. Same bug class
caught once before for `DD_THROTTLE_DISABLED` ([[max_pos5_dd_throttle_combined_gate_20260713]]),
worked around manually that time, never fixed at the script level — recurred
as more keys accumulated in `.env`.

**Fixed properly this time**: `hide_dotenv()`/`restore_dotenv()` added to
`robustness_gate.py` — renames `.env` aside for the duration of the arm runs
(after `check_config_drift()` has already inspected the real file), restored
in a `finally`. Every other `.env` value (DB path, credentials) is
unaffected since this process already loaded them via `config.settings`'s
own `load_dotenv()` at import time.

**Real result, TEST window (2025-01-01 → 2026-06-04), clean baseline vs candidate:**

| | baseline | candidate | delta |
|---|---|---|---|
| CAGR | +18.02% | +17.62% | -0.40pp |
| Sharpe | 1.01 | 0.98 | **-0.03** |
| MDD | 12.66% | 12.95% | worse |
| PF | 1.87 | 1.87 | flat |
| N trades | 92 | 93 | — |

TRAIN/FULL still marginally favor the candidate (TRAIN CAGR +5.12→+5.56%,
FULL +7.85→+8.18%). `crash_v_recovery` stress CAGR worse (11.84%→10.31%).
Gate verdict is still mechanical PASS (no hard threshold breached, no stress
sign-flip) — but the clear TEST-window win that justified deploying this
lever to the live server (`c4094a1`, real money) was measured on the broken
RSI/ATR formulas. On the corrected engine it's a marginal, mixed result, not
a clean win. Flagged to user for a live-deploy judgment call — see
[[ema_sweep_server_deploy_20260714]].

## Open follow-up

- Wire this script into `robustness_gate.py` or CI as a standing check, same tier as
  `check_config_drift()`.
- Current run only samples 15 symbols × 15 dates for speed — could widen for a one-off deep audit if
  a systematic issue is suspected elsewhere.
