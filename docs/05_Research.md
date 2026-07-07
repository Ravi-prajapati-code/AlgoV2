# 05 — Research History

This is the chronological record of strategy-improvement attempts: what was tried, what was kept,
what was rejected, and why. The purpose of keeping this list is explicit and load-bearing: **check
this document before starting any new strategy lever**, so effort isn't spent re-deriving a result
already established here (this rule is itself saved as a standing project-memory instruction).

## Research phase sequence

1. **Trade Attribution** (`scripts/trade_attribution.py`, 2026-07-01) — found the strategy's edge
   concentrates in 31+ day holds. Motivated two follow-up candidate levers, both tested and
   rejected (below).
2. **Feature Importance** (`scripts/feature_importance.py`) — exploratory, informed candidate
   selection for later phases.
3. **Correlation Analysis** (`scripts/correlation_analysis.py`) — showed sector position caps ARE
   empirically valid (positions in the same sector do move together enough to matter); also showed
   correlation-aware position sizing lacked enough headroom at `MAX_POS=3` to help even if
   implemented, since there's rarely room for more than one same-sector position open at once.
4. **Phase 4 (New Data Axes)** — not started. Open item.
5. **Robustness Gate** (`scripts/robustness_gate.py`, 2026-07-06, Phase 5) — built to formalize a
   three-stage validation sequence (full-window → out-of-sample → 4 stress scenarios) that had
   previously only been run correctly, in full, by manual discipline. See `06_Validation.md` for
   how it works.
6. **Phase 6 (Research Database)** — not started. Open item.

## Rejected experiments (do not re-attempt without a genuinely new angle)

- **Staged-entry** — looked good on trade attribution's 31+-day-hold finding, but failed the
  robustness sequence via **exposure collapse** (a later stage a partial validation run would have
  missed).
- **Extension-filter** (`EXTENSION_FILTER_EMA100_MAX_PCT`) — clean full-window CAGR win
  (12.83%→15.27%), passed out-of-sample train/test, but **failed 2 of 4 stress scenarios**
  (`prolonged_sideways_chop` and `extended_bear_grind`). This exact case is the motivating example
  baked into `robustness_gate.py`'s own docstring for why all three validation stages must run
  together, not sequentially-with-early-stopping.
- **ATR-based risk sizing (3x)** — rejected: any size-throttling lever taxes CAGR with no
  stress-scenario benefit at the current `MAX_POSITIONS=3`.
- **Correlation-aware position sizing** — rejected for the same reason above; confirmed by the
  correlation-analysis phase to lack headroom to matter at this position count.
- **Hard `BEAR_SWING_BUY` drawdown gate** — reverted: caused whipsaw, worsened 2 of 4 stress
  scenarios.
- **Slot/allocation resize** — reverted for the same whipsaw/stress-scenario reason. A "smoothed"
  variant of either drawdown-gate idea has not yet been tried and is a plausible next candidate,
  but should go through `robustness_gate.py` in full before being taken seriously.

## Open items on the phase-2 improvements backlog

Still unexplored (not rejected, just not yet attempted): VCP (volatility contraction pattern)
entry, stage-based exits, multi-factor regime detection (current regime detection is fundamentally
single-signal via `detect_regime()`).

## The regime-signal divergence finding (2026-07-02) and its consequences

The backtest engine was discovered to use a raw EMA100 crossover for regime detection, while live
used the smoothed `detect_regime()`. Fixing backtest to match live (so both use the same, real,
regime signal) was itself the single highest-leverage correctness fix in this research line — see
`06_Validation.md` for the resulting honest baseline numbers. After the fix, extensive re-tuning of
regime hysteresis parameters and buy/switch-gate decoupling was attempted to recover the previous
(bug-inflated) CAGR number; **every CAGR-recovering configuration found failed stress tests**
(sideways-chop whipsaw specifically). Live was kept unchanged rather than deploy any of these.

## Net conclusion carried forward

As of this writing, no parameter configuration — live or any swept/tested alternative — has been
found that both (a) passes all validation gates and (b) improves on the current honest baseline.
The honest baseline itself (CAGR +12.85%, Sharpe 0.83, MDD 23.67%) **fails the system's own
gates**, and live is running it anyway because no better, validated alternative exists. This is the
central open strategic problem for the system — see `09_Open_Questions.md`.
