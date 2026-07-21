# 34. Entry Gate-Stack Ablation — PURE_RS Deployed (2026-07-21)

## Origin

Extension of the `entry_attribution.py` suite (docs/23 §XIV) after the
Nifty 500 expansion (504 symbols). Two things happened in the same
investigation:

1. A new ranking hypothesis (`SURVIVAL_RANK`, ranking qualified candidates
   by an EMA20/50-extension + distance-from-20d-high composite instead of
   RS) was built off `trade_attribution.py`'s finding that hold-duration,
   not return, is predictable at entry (see memory
   `momentum_decay_grace_period_reject_20260721`). Full-window result:
   CAGR +5.16%, Sharpe 0.34, PF 1.15 — beats live `FULL` but is mid-pack
   among the 8 arms. Not a breakthrough; not deployed.
2. The full 8-arm comparison surfaced something bigger: on the 504-symbol
   universe, live `FULL` (RS + ADX + trend-confirm + breakout + SuperTrend)
   is the **worst of all 8 arms** — worse than `RANDOM_ALL` (buying with
   no gates at all). `PURE_RS` (RS≥72 gate + RS-rank only, ADX/trend/
   breakout/SuperTrend skipped) is the **best** arm, beating `FULL` by
   24pp CAGR full-window. This reconfirms and sharpens
   `entry_attribution_suite_20260709` (REVERSE_RS beat FULL on the old,
   smaller universe) — same direction, much larger gap now.

## Infra bugs found and fixed en route (not the actual finding, but
required to get an honest number)

- **Stale instrument-mapper cache**: `data/instruments/nse_instruments.json`
  was stale; `SCHNEIDER.NS` (new Nifty500 addition) had no instrument-key
  mapping at all, silently returning 0 rows on every fetch attempt.
  Fixed by calling `InstrumentMapper.refresh()` once and re-fetching.
- **`filter_symbols_with_insufficient_history` boundary bug** (`main.py`):
  the filter compared a young symbol's earliest-cached-date against
  `warmup_start`, but `fetch_symbol()` actually fetches from
  `warmup_start - 30 days`. A symbol whose cache starts inside that
  30-day margin (`SBFC.NS`, IPO'd 2023-08-16) passed the filter but still
  hit the unfillable-gap hard-fail inside `fetch_all()`, voiding the
  entire gate's TEST/OOS window (N=0 both sides — not a real REJECT).
  Fixed: `main.py` now passes `warmup_start - timedelta(days=30)` to the
  filter, matching the real fetch boundary. Both fixes are pure infra
  corrections, no behavior change to any strategy logic.

## Gate result (post-fix, real TEST window this time)

`robustness_gate.py --env ENTRY_MODE=PURE_RS`:

| | TRAIN (22-24) | TEST/OOS (25-26) | FULL (22-26) |
|---|---|---|---|
| baseline CAGR / Sharpe / PF | +3.65% / 0.29 / 1.19 | -5.39% / -0.19 / 0.82 | -0.82% / 0.05 / 1.01 |
| candidate CAGR / Sharpe / PF | +27.47% / 1.10 / 1.73 | +42.27% / 1.66 / 1.79 | +22.77% / 0.98 / 1.44 |

Stress (baseline → candidate): `crash_v_recovery` 24.1%/PF1.48 →
47.7%/PF1.86 (**improves**), `extended_bear_grind` -5.44%/0.93 →
-5.37%/0.93 (flat), `prolonged_sideways_chop` 5.55%/1.14 → 2.89%/1.10
(slightly worse, still >1 PF), `gap_down_bleed` -10.63%/0.83 → identical.

**VERDICT: PASS** — clears full-window, OOS, and all 4 stress gates.
First entry-signal-level lever this project has cleared clean (not just
a stress-scenario subset). Breaks the established "gate-loosening always
dies to crash_v_recovery" pattern (5/5 prior instances, see docs/24) —
here `crash_v_recovery` is the scenario that improves most.

## What this means

The additive-component check in `entry_attribution.py` makes the
mechanism explicit: `FULL vs best single component (PURE_RS): -24.05pp`.
Stacking ADX + trend-confirm + breakout + SuperTrend on top of a working
RS gate doesn't add selectivity value — it actively destroys it. This is
the same thesis as `docs/18-21` (Portfolio Construction Is The Leak) —
"signal real, construction destroys it" — reconfirmed from a completely
different angle (whole gate-stack ablation instead of single-lever
sweeps), and now with a large enough effect size and clean-enough
gate-pass to act on.

## Deployment

`config/settings.py`: `ENTRY_MODE` default changed `"FULL"` → `"PURE_RS"`.
`tests/test_signals.py::test_trend_not_aligned_fails` pinned to
`ENTRY_MODE="FULL"` via monkeypatch, since it specifically tests the gate
logic that `PURE_RS` now skips by design in the live default.

Not fully closing the book on *why* ADX/trend/breakout/SuperTrend are net
negative on this universe — deploying because the gate result is honest
and clean, not because the mechanism is fully understood. Worth a follow
-up investigation (does ADX/trend/breakout specifically veto genuinely
good RS-qualified trades, or just add noise with no signal either way?).
