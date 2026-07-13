# 19 — Leakage Experiments (Ranked)

**Date**: 2026-07-07. Companion to `18_Alpha_Leakage_Report.md`. Every experiment isolates ONE
variable. None is a parameter sweep. Validation for any code-touching experiment is
`scripts/robustness_gate.py` in full (full-window + OOS train/test + 4 stress scenarios), with the
explicit acknowledgment that the OOS window is statistically spent (docs/16.6 Issue 2) — gate
passage is *necessary, not sufficient*; only forward data confirms.

---

## E1 — Stranded-capital diagnosis, then ablation (RE-SCOPED 2026-07-07, EXECUTED 2026-07-11)

- **Status**: the original uniform-sizing arm was RUN and produced a null — `SCORE_BUCKETS` is
  dead code (never called in engine or live manager; gate run bit-identical to baseline). The
  measured leak (slots full 90.2% of BULL days, exposure only 71.6%) is real but its mechanism is
  now unattributed.
- **Re-scoped step 1 (measurement, executed)**: instrumented one N=3 baseline run
  (2022-01-01→2026-07-11) logging drawdown-throttle tier and slot/candidate counts on every
  buy-decision day (n=167). Result: the two candidate mechanisms are **near-parity, not one
  dominant** — DD-throttle leak ≈ ₹1.09M summed, candidate/slot-shortfall leak ≈ ₹1.01M summed
  (ratio 0.93x), firing on 19.8% vs ~9% of buy-decision days respectively.
- **Re-scoped step 2a (denominator ablation, executed)**: `available_slots → num_to_buy` arm
  tested — **structural no-op**. `MAX_STOCK_ALLOCATION_PCT=0.34` (`config/settings.py:58`,
  explicitly sized for "full deployment over 3 positions") clips any bigger per-slot allocation
  straight back down; gate run was byte-identical to baseline on every metric. Code reverted, no
  trace left.
- **Re-scoped step 2b (DD-throttle-removal ablation, executed)**: `DD_THROTTLE_DISABLED_ENABLED`
  flag (off-by-default, `backtest/engine.py`) skips the 0.50x/0.25x size reduction entirely.
  **PASSED** the full gate: TRAIN CAGR +13.44%→+17.98% (Sharpe 0.73→0.86, MDD 18.94%→18.20%),
  TEST CAGR +11.00%→+12.44% (Sharpe 0.64→0.71), FULL CAGR +11.29%→+14.88% (Sharpe 0.63→0.76).
  `crash_v_recovery`/`extended_bear_grind`/`gap_down_bleed` stress scenarios byte-identical
  (DD threshold never crossed in those windows); `prolonged_sideways_chop` degrades
  (-24.77%→-28.66%, PF 0.49→0.46) but no sign flip, so it does not trip the hard-fail rule.
  Kept in code, off by default — pending a deploy decision since it removes a deliberate
  risk-management control. See [[e1_idle_cash_ablation_20260711]].
- Note the rejected-experiments list (docs/05) already covers *adding* throttles; *removing* an
  existing throttle was untested territory before this run.
- **Hypothesis (updated)**: deploying the measured ~27% idle capital recovers 1.5–3pp CAGR.
- **Economic rationale**: the permutation test prices the signal at ~+9.6pp/yr on deployed
  capital; capital not deployed against the signal earns 0%. Conviction tiering only pays if the
  score→forward-return relationship is monotonic *within* qualifiers — nothing in the audit
  supports that (Leak 3 evidence mildly suggests the opposite at the top end).
- **Expected effect**: CAGR +1.5–3pp; Sharpe up or flat; MDD worse by ≤3pp.
- **Failure criteria**: TEST-window Sharpe or PF worse than baseline beyond gate tolerance (0.1);
  any stress scenario materially worse; MDD deterioration >5pp.
- **Validation**: full `robustness_gate.py`; report Sharpe/Sortino/Calmar, not just CAGR.
- **OOS requirement**: gate's train/test split mandatory + flagged for forward-shadow confirmation.
- **Success threshold**: TEST Sharpe ≥ baseline AND full-window CAGR +1pp AND no stress regression.
- **Effort**: trivial (one constant table). **Expected ROI: highest of all experiments** —
  largest measured leak, cleanest isolation.

## E2 — Idle-cash parking ablation (two arms)

- **Hypothesis**: idle cash earning (a) the 6.5% risk-free rate, or (b) the Midcap ETF return
  during BULL regime only, adds ~1.3–2.5pp CAGR (arm a, near-deterministic) / ~2–4pp (arm b) with
  no change to any trading decision.
- **Why it should work**: arithmetic — 27% average idle × 6.5% = 1.75pp/yr currently modeled as
  zero. Arm (a) is not even a strategy change; it corrects the backtest's unrealistically
  pessimistic cash model *and* is directly implementable live (liquid-fund parking).
- **Economic rationale**: cash drag is the one leak with a riskless partial fix.
- **Expected effect**: arm (a) +1.3–1.8pp CAGR, MDD unchanged or better; arm (b) more CAGR, more
  MDD (imports benchmark beta into "protective" cash).
- **Failure criteria**: arm (a) cannot fail (deterministic); arm (b) fails if 2022-type stress
  MDD worsens >3pp or any stress scenario PF degrades beyond tolerance.
- **Validation**: robustness gate for arm (b); arm (a) needs only arithmetic verification.
- **OOS requirement**: arm (b) yes; arm (a) n/a.
- **Success threshold**: arm (b) Calmar ≥ baseline.
- **Effort**: small. **ROI: high (arm a is free money in expectation), decisive info on whether
  "protective cash" has any option value worth its carry (arm b).**

## E3 — Churn-cohort audit (measurement only, no code change)

- **Hypothesis**: a minority of symbols generate repeated enter→stop→re-enter cycles that account
  for most of the -₹75k short-hold-cohort loss and a disproportionate share of the 8.46x
  turnover.
- **Why it matters**: determines whether friction is structural (spread evenly = cost of doing
  business) or concentrated (a few whipsaw loops = addressable pattern). Directs whether any
  entry-side change is even worth considering.
- **Expected effect**: information only. **Failure criteria**: n/a (measurement).
- **Validation**: trade-ledger analysis; re-entry gap distribution; per-cycle friction + P&L.
- **Effort**: hours. **ROI: high information per unit effort; zero risk.**

## E4 — Entry-lag ablation (slot-open timing leak)

- **Hypothesis**: the -1.35% (selected) vs +0.39% (passed-over) forward-21d gap is caused by
  buying at the moment a slot opens; delaying entry by a fixed small lag (or requiring the signal
  to be ≥2 days old) neutralizes it.
- **Why it should work**: selection moments cluster after exits (post-drawdown, extended
  leaders); a lag decouples entry timing from slot mechanics.
- **Economic rationale**: the signal's horizon is months (edge in 31+ day holds); its first-21-day
  realization is noise-to-negative, so entry timing within a few days should cost nothing and may
  avoid the extension effect.
- **Expected effect**: +0.5–1.5pp CAGR, fewer whipsaw stops (interacts with E3 findings).
- **Failure criteria**: missed-entry opportunity cost exceeds timing gain (CAGR down); TEST
  Sharpe below baseline tolerance.
- **Validation**: robustness gate. **OOS**: yes. **Success threshold**: stop-out rate down ≥10%
  AND TEST Sharpe ≥ baseline.
- **Effort**: moderate. **ROI: medium — evidence is only t≈1.7; run after E1/E2/E3.**

## E5 — GOLDBEES→cash ablation (carried from docs/16.6, still pending)

- **Hypothesis**: a material fraction of BEAR-period return is a one-off gold bull windfall, not
  repeatable regime skill.
- **Expected effect**: information — quantifies how much of the 12.26% CAGR is gold.
- **Failure criteria**: n/a (attribution measurement). **Validation**: A/B backtest, identical
  everything, safe-haven leg to cash. **Effort**: trivial. **ROI: high for truth, zero for CAGR.**

## E6 — Charges-model validation against live contract notes (E7 in docs/18 numbering)

- **Hypothesis**: modeled charges (1.54pp/yr) match actual broker contract notes within ±20%.
- **Why it matters**: friction is the second-largest measured leak; if the model understates real
  costs (STT/stamp/DP charges on delivery trades), every backtest number is further inflated.
- **Validation**: reconcile the live trade ledger's actual charges vs model on the same fills.
- **Effort**: hours (live data exists). **ROI: high — validates the largest hard-measured leak.**

---

## Explicitly NOT proposed

- Any change to RS ranking, entry indicators, stops, trailing logic, or exits — the audit
  **cleared** exits/stops empirically (docs/18 "Non-leaks"), and the ranking is the proven asset.
- Slot-count changes — tested and rejected (docs/16).
- Any parameter sweep. E1 and E2 are single-mechanism ablations with pre-registered success
  criteria, not searches.

## Execution order and stopping rule

Run E3 + E5 + E6 first (pure measurement, zero risk, ~a day total), then E2a (deterministic),
then E1, then E2b, then E4 only if E3 implicates entry timing. **Stopping rule**: if E1 + E2
together fail to lift the honest full-window CAGR above ~15% or TEST Sharpe above the Midcap
ETF's 0.85, construction is exonerated as the *recoverable* bottleneck, and docs/22's "next
research direction" (overlay/component role) becomes the operative conclusion — that outcome is a
success by the mission's own criterion.
