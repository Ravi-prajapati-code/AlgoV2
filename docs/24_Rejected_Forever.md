# 24. Rejected Forever

Every lever in this document was tested empirically (backtest,
attribution analysis, or `robustness_gate.py`'s full-window/train/test/
stress standard) and failed to earn its place. Do not re-implement or
re-propose any of these without genuinely new data — a different
dataset, a different market regime playing out live, or a materially
different formulation of the idea (not just a re-run of the same test).

If a future session is tempted to try one of these again: read the
linked section first. Re-deriving a known-negative result wastes a
robustness-gate run and re-litigates a closed question.

## Entry / ranking

| Lever | Verdict | Evidence |
|---|---|---|
| Complex multi-factor ranking (freshness + institutional volume + sector-leadership + sector-tailwind) | REJECTED — null | `|rho| < 0.06`, n=8,633. No single factor explains win vs. loss. [[signal_mechanism_analysis_20260710]] |
| Freshness ranking | REJECTED — null | Same suite, no signal. |
| ADX ranking (as a sort key, not the existing threshold gate) | REJECTED — null | Same suite. Note: ADX as a pass/fail *threshold* gate is proven and kept — see §XXV in `docs/23_Assumption_Audit.md`. Ranking by ADX magnitude is the rejected idea, not the gate itself. |
| Turnover ranking | REJECTED — null | Same suite. Note: turnover as a pass/fail *floor* gate is a separate, still-live check (never fires, kept as a live safety net — §XXIV). |
| Extension ranking | REJECTED — null | Same suite. |
| Weighted composite scores | REJECTED — never beat plain RS-rank | `entry_attribution_suite_20260709`: REVERSE_RS (worst-first) beat RS-rank ranking; ranking itself adds no value at the point of entry. |
| Sector blacklist (`SECTOR_BLACKLIST`) | REJECTED — overfit | §XXIII, Experiment A. Code deleted 2026-07-10, commit `54afde3`. |
| Streak-priority buy ordering (`STREAK_PRIORITY_ENABLED` / re-tested as `STREAK_POSITION_PREF_ENABLED`) | REJECTED — 2/2 variants, 2 different stress failures | §XXIII, Experiment B (pre-fidelity-fix, ambiguous: train regressed, test improved). Re-tested 2026-07-10 under the corrected post-df9856f engine with a continuous streak-day sort — TRAIN/TEST/FULL all clearly improved, but `crash_v_recovery` CAGR flipped +4.66%→-4.32%. Re-tested again 2026-07-13 with the previously-untested variant (gated off during BEAR/just-flipped-BULL, only active once `regime_streak >= ENTRY_CONFIRM_DAYS` in BULL) — TRAIN/TEST/FULL all improved again (Sharpe 0.70→0.98, PF 1.53→1.78), crash_v_recovery this time clean, but `prolonged_sideways_chop` PF collapsed 0.93→0.63. Both variants' code fully reverted, no trace left in `config/settings.py` or `backtest/engine.py`. Treat as structurally dead: streak-preference trades consistency-across-regimes for CAGR no matter how it's gated. |
| Dynamic replacement (RS/volume/sector rotation triggers for replacing a weak holding) | REJECTED — no live trigger works | [[rotation_logic_synthesis_20260710]]: replacing a weak holding is correct in hindsight, but RS-rank, volume, and sector strength all fail as a real-time trigger. Built, tested, and explained — not an open question. |
| `CAMS.NS` re-entry | REJECTED — 3 trades, 0% win rate, structural loser | Originally enforced via `IGNORE_SYMBOLS` (a broker-position-visibility mechanism, wrong tool for a do-not-trade verdict). Migrated 2026-07-13 to `BLOCKED_SYMBOLS` (`config/strategy_config.yaml`), enforced in `strategy/signals.py`'s entry gate for both backtest and live — same mechanism as `BLOCKED_SECTORS`. See `docs/30_Ignored_Holdings_Removal_Review.md` §2b/Step 4. |
| Widening the entry trend-alignment gate to `close > EMA65 and EMA65 > EMA100` (`ENTRY_EMA_MEDIUM=65`) | REJECTED — same overfit signature as sector-blacklist/streak-priority | `robustness_gate.py`, 2026-07-13. Huge TRAIN/TEST/FULL gains (TEST Sharpe 0.61→0.90, PF 1.46→1.70) but `prolonged_sideways_chop` PF collapses 0.92→0.81. Also REJECTED as the equivalent `ENTRY_EMA_LONG=65` (narrowing the gap from the other side) — same failure mode, same numbers. Stacking `ENTRY_EMA_MEDIUM=40` + `ENTRY_EMA_LONG=150` (each individually passing) together also REJECTS on the identical scenario (PF 0.92→0.81) — the two levers are not independently safe, they compound the same fragility. See `docs/32_Entry_Exit_EMA_Sweep_20260713.md`. |
| Tightening the entry trend-alignment gate to `close > EMA30` (`ENTRY_EMA_MEDIUM=30`) | REJECTED — weaker on every metric | Same sweep. TRAIN/TEST/FULL all down (TEST Sharpe 0.61→0.56, PF 1.46→1.43); technically gate-PASSES (no stress failure) but strictly worse than baseline — no reason to deploy. See `docs/32_Entry_Exit_EMA_Sweep_20260713.md`. |
| MACD confirmation gate on entry (`MACD_CONFIRM_ENABLED`, require `macd_bullish` alongside existing EMA/SuperTrend/ADX trend gate) | REJECTED — classic overfit signature, huge TEST gain masking a stress kill | `robustness_gate.py`, 2026-07-14. TEST window looked spectacular in isolation (Sharpe 0.94→1.62, PF 1.80→2.89, CAGR 16.61%→31.94%) but candidate introduces NEW train/test instability not present in baseline, and `prolonged_sideways_chop` CAGR flips from +3.49% to **-14.82%** (PF 1.06→0.75). Same recurring-killer scenario that has rejected sector-blacklist, streak-priority, and the EMA65 widening — an extra confirmation filter that looks free in the recent bull-ish TEST window is not free in chop. |

## Exit

| Lever | Verdict | Evidence |
|---|---|---|
| Momentum-decay RSI threshold (`MOMENTUM_RSI`, live=50) | REJECTED — structural, two-sided local optimum | 7-value sweep (35/38/40/42/45/55/60) via `robustness_gate.py`, 2026-07-11. Lowering (35-42) improves TRAIN/TEST/`crash_v_recovery` but is killed by `gap_down_bleed` PF drop every time (38 and 40 are single-failure near-misses, nothing else wrong). Raising (45-60) fails via the opposite mechanism — TEST-window degrades, `prolonged_sideways_chop` breaks, and 60 flips `crash_v_recovery` CAGR negative. 50 is a genuine local optimum, not an arbitrary default. `gap_down_bleed` is the first recurring-killer scenario found for an *exit-timing* lever, distinct from `crash_v_recovery` (the recurring killer for ranking/filtering levers, `docs/25` §4). See `docs/27_Trading_Strategy_Research_Framework.md` §A2. |
| Tightening `TREND_BREAK` exit basis to `EXIT_TREND_EMA=30`, or its confirmation window to `EXIT_TREND_CONFIRM_DAYS=1` | REJECTED — both fail identically | `robustness_gate.py`, 2026-07-13. Both make the exit fire faster/more often; both are worse on TRAIN/TEST/FULL and both fail `prolonged_sideways_chop` hard (PF 0.92→0.66 and 0.92→0.63 respectively) — premature trend-break exits whipsaw badly in chop. See `docs/32_Entry_Exit_EMA_Sweep_20260713.md`. |

## Regime / crash protection

| Lever | Verdict | Evidence |
|---|---|---|
| Stock-level override of the blanket BEAR-regime crash-protection veto (`CRASH_PROTECTION_STOCK_OVERRIDE`): exempt a position from the forced `MARKET_CRASH_PROTECTION` exit, and allow new entries during BEAR, if the stock's own trend (EMA alignment + SuperTrend + ADX — the same bar `check_entry()` uses) is still intact | REJECTED — same recurring chop-killer as every other veto-loosening lever | `robustness_gate.py`, 2026-07-14. TEST window down across the board (Sharpe 0.94→0.64, PF 1.80→1.50, WR 55%→43%, MDD 13%→16%), FULL/TRAIN also down. `prolonged_sideways_chop` PF 0.74→0.50 — letting individually-still-trending stocks stay in (or newly enter) through a BEAR regime increases whipsaw exposure in choppy conditions, the same failure mode as the rejected full-removal variant ([[crash_protection_exit_isolation_20260710]]), just less severe. Narrower ≠ safe here — any loosening of the blanket veto, partial or total, has now failed. Flag left in code, off by default, no live effect. |

## Universe

| Lever | Verdict | Evidence |
|---|---|---|
| Tightening the liquidity/turnover floor (`MIN_DAILY_TURNOVER`) above the current dormant ₹2 Cr/day | REJECTED — structural, not a calibration issue | Tested at p25 (₹118 Cr/day) and p10 (₹66.55 Cr/day) of the current universe's turnover distribution via `robustness_gate.py`. Both REJECT on the same failure: `crash_v_recovery` stress CAGR flips negative (+4.66%→-0.85%/-0.95%). Full-window and train-window CAGR/Sharpe drop meaningfully at both levels; test-window is non-monotonic (p25 ~neutral, p10 *worse* despite being milder) — evidence the effect is driven by which specific stock gets filtered (the crash-recovery winner is liquidity-thin), not the threshold level. Two other stress scenarios (extended_bear_grind, gap_down_bleed) were completely inert at both thresholds — the filtered names never mattered there either way. One real positive found: `prolonged_sideways_chop` improves at both levels (PF 0.68→0.84 at p25), a believable "avoid whipsaw in illiquid names" mechanism — but not enough to offset the crash_v_recovery failure under the standard applied all session. 2026-07-10. |

## Safe-haven (GOLDBEES)

| Lever | Verdict | Evidence |
|---|---|---|
| Gate the BEAR-regime GOLDBEES buy on gold's own trend (`GOLDBEES_TREND_FILTER_ENABLED`, close > own EMA100) instead of buying unconditionally on BEAR flip | REJECTED — no-op where it doesn't bite, net-negative where it does | `robustness_gate.py`, 2026-07-13. TEST window byte-identical to baseline (filter never once blocked an entry — gold was already trending up every time BEAR hit). 3 of 4 stress scenarios byte-identical (`crash_v_recovery`, `extended_bear_grind`, `gap_down_bleed`). The two places it had any effect were both worse: TRAIN CAGR 22.06%→20.89%, Sharpe 1.11→1.06; `prolonged_sideways_chop` CAGR -9.44%→-14.72%, PF 0.86→0.81. Gate mechanically PASSED (no sign-flip, no OOS regression) but judgment-call rejected — same pattern as sector-blacklist/streak-priority: clears the hard thresholds without earning the complexity. Code fully reverted, no trace left. Do not re-test the same EMA100-trend formulation; a materially different gold-timing signal would be a new hypothesis, not a re-run. |

## What is NOT on this list (proven, keep)

- Relative Strength gate, Trend Alignment (EMA), SuperTrend, ADX
  (as a threshold gate), Breakout — the baseline entry gate. Frozen.
- Liquidity/turnover floor — never fires in backtest, kept anyway as a
  live operational safety net (protects against risk a backtest can't
  model, not a proven backtest edge). See §XXIV / §XXVII.
- Exit chain: trend break, momentum decay, regime-flip bear protection.
  No complex-exit variant has ever beaten this. Frozen.
- Regime filter. Justified, keep.
- Drawdown-based position sizing (`DRAWDOWN_REDUCE_SIZE_PCT`/`TIER2_MULT`) —
  **superseded 2026-07-11**: E1 ablation (`DD_THROTTLE_DISABLED_ENABLED`,
  off-by-default) removes it entirely and **PASSED** the full robustness
  gate — CAGR/Sharpe/MDD all improve on TRAIN/TEST/FULL, `crash_v_recovery`
  untouched (byte-identical), only `prolonged_sideways_chop` degrades
  (-24.77%→-28.66%, no sign flip). This is not "rejected," it's a live
  candidate pending a deploy decision — see [[e1_idle_cash_ablation_20260711]]
  and `docs/26_Portfolio_Truth_Audit.md`.

## How to apply

Before proposing any new ranking scheme, replacement trigger, or
weighting formula for entries: check this table first. If it's a
variant of something here (e.g. "rank by ADX *and* turnover together"),
treat it as the same rejected idea unless there's a specific reason to
believe combining previously-null factors produces a non-null result —
that reason needs to be stated up front, not discovered after another
robustness-gate run.
