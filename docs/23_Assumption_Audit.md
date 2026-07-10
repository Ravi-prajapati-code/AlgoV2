# 23 — Assumption Audit (2026-07-08)

Every load-bearing assumption in the system, classified: **Proven**, **Supported**,
**Plausible**, or **Unknown**. Nothing left unclassified. Values pulled directly from
`config/settings.py`, `config/strategy_config.yaml`, `strategy/`, `portfolio/manager.py`.

Definitions used:
- **Proven** — directly tested with a statistical method (permutation test, code
  inspection of a deterministic formula, direct grep verification). Direction can be
  negative — "proven false" still counts as Proven.
- **Supported** — real evidence exists (backtest audit, robustness_gate result,
  A/B test) but not a rigorous standalone proof. Evidence can point negative.
- **Plausible** — reasonable economic/technical rationale exists, never tested on
  this system or dataset.
- **Unknown** — no rationale on record and no test on record. Value exists only
  because it was set once and never revisited.

## I. Signal (foundational)

| # | Assumption | Class | Basis |
|---|---|---|---|
| 1 | RS ranking carries real stock-selection info (aggregate, full history) | Proven | permutation test, p=0.024 |
| 2 | RS is stable/durable across time | Proven false | rolling-window test: 2/15 STRONG, 3/15 ANTI-PREDICTIVE, 10/15 WEAK/MIXED (`outputs/signal_stability_rolling.csv`) |
| 3 | Regime (BULL/BEAR) explains when RS works | Unknown | tested this session — breadth/vol/correlation buckets mostly flat or non-monotonic vs STRONG/WEAK/ANTI (`outputs/signal_regime_diagnostics.csv`) |
| 4 | Mean-reversion sign / rank-persistence gates RS quality | Plausible | STRONG windows show negative lag-1 autocorr (-0.024) vs ANTI-PREDICTIVE positive (+0.027); n=2 strong windows, circularity risk with the outcome variable itself |
| 5 | RS measures price momentum only, no fundamentals | Proven | by construction — 126d rolling price ratio vs index, cross-sectional percentile, verified in code |
| 6 | Underreaction / slow information diffusion causes momentum | Plausible | standard academic prior (Jegadeesh-Titman), never tested against this dataset |
| 7 | Flow-chasing / benchmark-mandate rebalancing amplifies RS | Plausible | general market mechanism, not verified here |
| 8 | RS is a lagging indicator of institutional behavior, not leading | Proven | formula uses only trailing price data, verified in code |

## II. Entry gates (`strategy/entry.py`, `strategy_config.yaml`)

| # | Assumption | Class | Basis |
|---|---|---|---|
| 9 | RS_THRESHOLD ≥ 72 is the right cutoff | Unknown | never ablation-tested vs 65/80/etc |
| 10 | RSI 55–85 buy band correct | Unknown | no individual test on record |
| 11 | ADX≥20 + SuperTrend filter (commit 9fc9782) improves entries | Unknown | just added; no robustness_gate result on record this session |
| 12 | Breakout within 5% of 20d high OR VCP pivot + RVOL≥1.5 | Unknown | untested individually |
| 13 | 10d momentum ≥2% floor | Unknown | untested individually |
| 14 | Overextension cap 15% above EMA50 | Unknown | untested individually |
| 15 | MIN_VOLUME_RATIO ≥1.5x | Unknown | untested individually |
| 16 | Liquidity floor ≥₹2Cr/day turnover | Plausible | independent execution-cost rationale (slippage avoidance), not curve-fit only |
| 17 | Fixed 100-symbol universe is a good hunting ground | Supported (negative) | equal-weight own universe underperforms Midcap150 by -3.85pp — composition itself costs return (doc 18-21) |
| 18 | Universe construction free of look-ahead bias | Proven flawed | `get_all_symbols_as_of()` fix closes it going forward only (2026-07-07); historical contamination magnitude unknown; one sensitivity test came back confounded |

## III. Exit (post-removal, 2026-07-08)

| # | Assumption | Class | Basis |
|---|---|---|---|
| 19 | Exits/stops are not the alpha leak | Supported | docs 18-21 audit conclusion |
| 20 | Signal-only exit (stop/trail fully removed) is net-positive for CAGR/risk | Unknown | not backtested since removal — no fresh numbers run |
| 21 | PROFIT_TARGET ceiling at 50% | Unknown | arbitrary, code-labeled "emergency ceiling", never ablated |
| 22 | GOLDBEES_MAX_LOSS_PCT = 7% floor | Unknown | arbitrary pick, not individually tested |
| 23 | LAGGARD_RS<50 / MOMENTUM_RSI<50 soft-exit thresholds | Unknown | untested individually |
| 24 | MIN_PROFIT_FOR_SOFT_EXIT = 25% gate | Unknown | untested individually |

## IV. Regime detection

| # | Assumption | Class | Basis |
|---|---|---|---|
| 25 | EMA100-based smoothed BULL/BEAR is the right regime classifier | Plausible | internally consistent now (backtest fixed to match live's `detect_regime()`, 2026-07-02); never proven optimal vs alternatives |
| 26 | REGIME_SWITCH_DAYS hysteresis buffer (~45d) correct | Unknown | tuned parameter, no independent test |

## V. Portfolio construction / sizing

| # | Assumption | Class | Basis |
|---|---|---|---|
| 27 | MAX_OPEN_POSITIONS=3 is adequate | Supported (negative) | slots full 90.2% of BULL days → ~27% stranded capital (doc 20); slot-count sweep tried, rejected, no fix applied |
| 28 | MAX_STOCK_ALLOCATION_PCT=34% sizing | Plausible | 1/3 + buffer arithmetic, not stress-tested independently |
| 29 | score_to_size_factor sizing is active | Proven false | confirmed dead code (doc 20) |
| 30 | 100% investment / zero cash drag (recent commit) is safe | Unknown | no robustness_gate result referenced for this specific change |
| 31 | Rotation / Ride-Winner / Score-Drop-Add thresholds correct | Unknown, except RANK_REPLACED specifically = Supported | doc 20: rank-replacement shows no material leak; other rotation thresholds never separately validated |
| 32 | ATR / correlation-aware position sizing not needed | Supported | tested via `robustness_gate.py`, rejected — taxes CAGR, no stress-scenario benefit |
| 33 | Sector caps are a valid lever | Supported | correlation analysis explicitly confirmed |

## VI. GOLDBEES safe-haven

| # | Assumption | Class | Basis |
|---|---|---|---|
| 34 | GOLDBEES needs separate static-floor + regime-exit, not ATR trail | Supported | explicit design decision, validated no-op via A/B test (commit 59bf83c) |
| 35 | 7% specific floor number is right | Unknown | arbitrary, not ablated |

## VII. Risk / drawdown

| # | Assumption | Class | Basis |
|---|---|---|---|
| 36 | DRAWDOWN_KILL_SWITCH_PCT=10% / DRAWDOWN_REDUCE_SIZE_PCT=5% correct | Unknown | untested; note other drawdown-gate *variants* (hard BEAR_SWING gate, slot/allocation resize) were tried and reverted for whipsaw — these specific live numbers never separately tested |
| 37 | BACKTEST_MAX_DRAWDOWN / MIN_WIN_RATE / MIN_PROFIT_FACTOR pass/fail gates | Plausible | standard practitioner heuristics, not empirically derived for this system |

## VIII. Data integrity

| # | Assumption | Class | Basis |
|---|---|---|---|
| 38 | Universe look-ahead bug fully fixed | Unknown (partial) | fixed going forward only; dynamic-extras side still unfixed |
| 39 | Backtest regime signal now matches live exactly | Supported | fixed 2026-07-02, verified |
| 40 | Honest baseline (+12.85% CAGR, Sharpe 0.83, MDD 23.67%) reflects true current performance | Supported, with caveat | best available bug-fixed number; still sits on partially-contaminated universe data |

## IX. Live execution / infra

| # | Assumption | Class | Basis |
|---|---|---|---|
| 41 | GTT-based stop-loss placement was safe infra | Proven false | LAURUSLABS incident (2026-07-08) — GTT fired at broker, DB still showed position open |
| 42 | Removing GTT stop placement eliminates this bug class | Supported (in code) | verified via grep — no remaining independent broker-side price-trigger path; not yet observed live |
| 43 | Once-daily broker sync now sufficient | Plausible | lower risk post-GTT-removal, but manual sells/corporate actions could still desync same-day — not stress-tested |
| 44 | Existing live GTTs will be safely cancelled by next sync | Plausible | code path exists (`cancel_stale_gtts` in `sync_portfolio_with_broker`), not yet observed running in production |

## X. Backtest validity

| # | Assumption | Class | Basis |
|---|---|---|---|
| 45 | Backtest and live share identical exit/signal logic | Proven | verified via import grep — single shared source (`strategy/exit.py`, `strategy/signals.py`) |
| 46 | Full test suite passing = correctness | Supported | 90/91 pass; 1 known pre-existing unrelated failure (`test_rejects_over_stock_cap`, stale vs `MAX_STOCK_ALLOCATION_PCT` bump) |

## Tally

- Proven: 8 (2 of them proven *false*)
- Supported: 12 (4 of them negative — evidence says the assumption is wrong)
- Plausible: 8
- Unknown: 18

## Reading

Core claim — signal real, portfolio construction is the bigger leak, exits weren't
it — solidly Proven/Supported. Everything downstream of that — every entry-gate
number, every soft-exit threshold, every sizing constant — is Unknown: 18 of 46
load-bearing parameters have zero individual justification beyond "didn't hurt the
aggregate backtest number." Any future tuning pass should start by ablation-testing
items in this Unknown bucket individually (via `scripts/robustness_gate.py`) rather
than adding new levers on top of an unaudited base.

Note: #22 and #35 (GOLDBEES 7% floor) are the same parameter listed twice across
sections III and VI — 17 distinct Unknowns, one double-counted.

## XI. Prioritization (2026-07-08 follow-up)

### Highest impact if proven false

**#20** (signal-only exit, stop/trail fully removed) — shipped live with zero
backtest confirmation since removal; removes the one mechanism bounding
single-position loss. Close second: **#30** (100% invested, zero cash reserve) —
also just shipped, removes the portfolio-level buffer at the same time. Both
downside-protection layers pulled simultaneously, neither re-tested.

### Ranked by Expected Information Gain (not CAGR)

1. #38 universe contamination — gates trust in every other backtest number in this audit
2. #20 signal-only exit — cheap, resolves a live-deployed unknown
3. #30 zero cash drag — cheap, live-deployed
4. #36 drawdown kill-switch/reduce % — moderate cost, resolves the tail-risk breaker
5. #9 RS_THRESHOLD=72 — cheap sweep, core selection gate
6. #26 REGIME_SWITCH_DAYS — cheap sweep
7. #31 rotation/ride-winner/score-drop bundle — moderate cost, resolves 3 at once
8-14. #10, #11, #12, #13, #14, #15 entry sub-filters — cheap, lower leverage than #9
15. #21 PROFIT_TARGET 50%
16. #22/35 GOLDBEES 7% floor — isolated to one instrument, low portfolio leverage
17. #23 LAGGARD_RS/MOMENTUM_RSI
18. #24 MIN_PROFIT_FOR_SOFT_EXIT — lowest leverage, gates an already-narrow soft exit

### Safe to test together vs must isolate

**Safe combos** (independent subsystems, no shared decision path): #26 + #22/35
(different instrument, zero shared code); #38 must run alone/first, everything
else re-baselines after it.

**Must isolate** (same decision funnel, confounds attribution): #9/#10/#11/#12/#13/#14/#15
(shared entry funnel); #21/#22/#23/#24 (sequential exit-gate logic); #20 + #30
(both remove downside protection at the same failure mode); #36 + #30 (both
exposure-sizing levers on the same failure mode); #31 (internally coupled by
design — test as one bundle, not mixed with the entry sweep).

### Minimum experiment set (18 → Proven/Rejected)

Eight experiments on one shared harness (`robustness_gate.py` + a new
threshold-sweep wrapper):

- **E1** — universe contamination bound (new script). Resolves #38. Run first.
- **E2** — signal-only exit A/B, full window + 4 stress scenarios. Resolves #20.
- **E3** — cash allocation A/B (100% vs buffered), isolated from E2. Resolves #30.
- **E4** — entry-gate one-at-a-time sweep (#9/#10/#11/#12/#13/#14/#15), 7 sub-runs.
- **E5** — exit soft-threshold one-at-a-time sweep (#21/#23/#24) + GOLDBEES floor separately (#22/35).
- **E6** — rotation bundle test (#31), bundle-vs-off first, then ablate inside if it shows value.
- **E7** — regime hysteresis sweep (#26).
- **E8** — drawdown kill-switch/reduce calibration (#36), judged only against the 4 stress scenarios.

### Delete now, no test needed

- **#21 PROFIT_TARGET=50%** — contradicts the signal-only-exit principle just
  adopted; a hard price trigger, same category as the stop-loss just removed.
- **#14 overextension cap 15%** — self-contradicts the RS/RSI selection premise
  (penalizes exactly the momentum the filter is designed to find).
- **#24 MIN_PROFIT_FOR_SOFT_EXIT=25%** — flagged for urgent redesign, not
  removal: combined with #20 (stop/trail gone), an underwater laggard now has
  zero exit path except regime/crash/trend-break. Gap didn't exist before
  stop-loss removal; nobody revisited this gate after that change.

### Simplest valid strategy (Proven+Supported only)

Daily RS-rank the universe (Proven). Hold top-decile, equal-weight (score-based
sizing proven dead, ATR/correlation sizing Supported-not-helpful), sector caps
applied (Supported). No RS_THRESHOLD cutoff, no RSI/ADX/breakout/volume overlay
(all Unknown) — pure rank membership. No stop-loss/trail/profit-ceiling
(Supported: not the leak). No fixed 3-slot cap (Supported-negative: strands
~27% of capital) — size across however many names qualify. Exit rule: fall out
of the qualifying rank band. Performance: unknown, not fabricated — this is the
one genuinely missing experiment (see ⭐ below).

### Likely compensating for another flaw

- #21 PROFIT_TARGET — compensating for the trailing-stop's removal; a blunt
  substitute for the nuanced ratchet that used to exist.
- #24 MIN_PROFIT_FOR_SOFT_EXIT — likely patching RS/RSI noise (whipsaw) rather
  than a principled gate.
- #14 overextension cap — compensating for RS_THRESHOLD/RSI band being too
  loose, letting in already-exhausted breakouts the core filter should screen.
- #36 drawdown kill-switch — likely compensating for concentration risk from
  only 3 slots (#27, Supported-negative).
- #26 REGIME_SWITCH_DAYS — same story as #24: smoothing signal noise, not a
  derived economic lag.

### Threshold economics — real convention or arbitrary artifact

| Value | Verdict |
|---|---|
| RS≥72 | Arbitrary — percentile cutoffs are sample-dependent |
| RSI 55-85 | Arbitrary — tweak off Wilder's 30/70 folklore |
| ADX≥20 | Plausible convention (Wilder's trending/choppy line), never validated for this system |
| Breakout 5% / RVOL≥1.5x | Concept justified (volume confirms informed buying), numbers are round-number artifacts |
| Momentum ≥2%/10d | Arbitrary |
| Overextension 15% | Arbitrary, self-contradicting |
| PROFIT_TARGET 50% | Arbitrary, inconsistent with signal-only design |
| GOLDBEES floor 7% | Concept justified, number arbitrary |
| LAGGARD_RS/MOM_RSI <50 | Arbitrary — midpoint-as-default |
| MIN_PROFIT_FOR_SOFT_EXIT 25% | Arbitrary |
| REGIME_SWITCH_DAYS ~45d | Concept justified (avoid whipsaw), day count arbitrary |
| MAX_STOCK_ALLOC 34% | Derived (1/3 + buffer), only as sound as #27 (Supported-negative) |
| DD_KILL 10% / REDUCE 5% | Industry-convention level, never validated for this strategy's own vol profile |

### ⭐ Rebuild from Proven+Supported only

Same construction as "simplest valid strategy" above. What's lost/gained: not
known, not guessed. This is the one experiment in the whole audit that doesn't
depend on resolving any other Unknown first — fully specifiable today, zero
dependency on E1-E8. Highest-priority next deliverable if the goal is the
actual truth about this strategy's edge.

## XII. Underlying economic principle per assumption (2026-07-08 follow-up)

For every assumption above: the economic principle it's attempting to
represent, and whether that principle is defensible.

- **Delete without testing (5)** — no defensible principle, or the principle is
  redundant/contradicted by other parts of the system:
  - #10 RSI 55-85 band — RSI is a derived oscillator, not a market mechanism; no
    institutional-flow story ties to RSI levels specifically. Redundant with
    #14's real underlying concept (recency of move).
  - #13 10d momentum ≥2% floor — redundant overlay; RS(126d) already measures
    this, a 10d floor adds no distinct mechanism.
  - #15 MIN_VOLUME_RATIO≥1.5x — duplicate of #12's volume-confirmation concept.
  - #21 PROFIT_TARGET 50% — contradicts the signal-only-exit principle adopted
    2026-07-08.
  - #24 MIN_PROFIT_FOR_SOFT_EXIT 25% — principle as implemented is backwards
    (protects winners from early exit, leaves underwater laggards with no exit
    path); needs redesign, not a threshold sweep.

- **Real principle exists, minimum experiment to validate it separate from the
  numerical threshold (~17)**:
  - #4 mean-reversion sign — AR(1) return structure distinguishes trending vs
    choppy regimes. Test: split by sign only (not magnitude) of lag-1 autocorr
    on a larger sample to escape n=2 and circularity with the IC outcome.
  - #6 underreaction/slow diffusion — test: event-study, does RS climb *after*
    analyst estimate-revision events, with a measurable lag?
  - #7 flow-chasing amplification — test: correlate FII/MF net-flow data (if
    obtainable) against forward RS-rank change.
  - #9 RS≥72 — test: IC as a continuous function of RS percentile (decile by
    decile). Smooth scaling with no discontinuity means there's no real
    threshold — 72 is a false binary on a continuous relationship.
  - #11 ADX≥20+SuperTrend — test: forward IC/hit-rate in ADX-rising vs
    ADX-falling periods (continuous), not the fixed 20 level.
  - #12 breakout/RVOL — test: rank-correlate volume-ratio-at-entry (continuous)
    against forward return, independent of the 5%/1.5x specifics.
  - #14 overextension cap — real principle (short-horizon reversal after a big
    run), wrong proxy. Test: forward return of "RS built gradually over 126d"
    vs "RS mostly from a spike in the last ~15d" — replaces the %-above-EMA50
    cap with a recency-of-move measure if validated.
  - #16 liquidity floor ₹2Cr/day — operational constraint, not a signal claim.
    Test: realized slippage for names near the boundary vs comfortably above it.
  - #22/35 GOLDBEES floor — test: gold ETF's own historical drawdown
    distribution — does any floor reduce realized loss vs regime-flip-only exit?
  - #23 LAGGARD_RS/MOMENTUM_RSI<50 — test: IC of current RS rank (while held)
    against forward return from that point, separate from picking 50.
  - #25 EMA100 regime classifier — test: check regime labels are robust across
    EMA spans (50/100/200d); if labeling barely changes, "regime exists and is
    measurable" holds independent of the 100-day pick.
  - #26 REGIME_SWITCH_DAYS — test: sweep hysteresis length (10/20/30/45/60d),
    measure flip frequency vs whipsaw cost.
  - #27 MAX_OPEN_POSITIONS=3 — reframe from "optimal N" search (already
    tried/rejected) to a continuous capital-utilization-% vs slot-count curve.
  - #30 100% invested — test: max-drawdown of 100%-invested vs a buffered
    variant specifically in the crash stress scenario.
  - #31 rotation/ride-winner/score-drop — test: regress forward-return-
    improvement on RS-rank-gap continuously, instead of the specific
    ROTATE_EXIT/INTO gap numbers.
  - #36 DD_KILL/DD_REDUCE — test: sweep trigger level (8/10/12/15%) across the
    crash stress scenario only, independent of calibrating the exact 10%.
  - #43 daily broker sync cadence — operational, not backtest. Test: log actual
    broker-vs-DB divergence over a live 30-day window, check daily cadence
    never misses one.

- **N/A — not a market assumption at all (data hygiene / software correctness /
  governance policy), forcing these into delete-or-test would be dishonest**:
  #2, #5, #8, #18, #19, #29, #34, #37, #38, #39, #40, #41, #42, #44, #45, #46.
  #37 (backtest pass/fail gates) in particular is an investor risk-tolerance
  policy decision, not something to prove empirically.

## XIII. Empirical results — E1-E8 bracket tests (2026-07-08/09)

Ran `scripts/robustness_gate.py` bidirectionally (loosen + tighten from the
live default) for every testable parameter. Gate = full-window + train/test
OOS + 4 stress scenarios (crash_v_recovery, extended_bear_grind,
prolonged_sideways_chop, gap_down_bleed), fixed pass/fail tolerances (see
§header of the script). A PASS with no test/stress upside and a train-only
gain is treated as overfit, not adopted, even though the gate itself doesn't
reject it.

### Removed (proven inert — zero measurable effect on any window/scenario)

| Param | Was | Evidence |
|---|---|---|
| RSI_BUY_MIN/MAX (55-85) | entry gate | dead since RS/ADX gates already select this range; deleted w/ #10 |
| MIN_VOLUME_RATIO≥1.5x | entry gate | duplicate of breakout/RVOL check; deleted w/ #15 |
| TAKE_PROFIT_PCT 50% | exit ceiling | contradicted signal-only-exit design; deleted w/ #21 |
| ATR_PCT_MAX | entry filter | byte-identical results at every bracket value — never binds on real data |
| VCP_RVOL_MIN | entry filter (VCP branch) | byte-identical results at every bracket value — never binds |
| LAGGARD_RS (rs_rank<30/40/50/65) | exit condition | MOMENTUM_DECAY(RSI) always fires first in every backtest window; at 65 it bound independently and *hurt* crash-recovery capture (CAGR 11.02%→0.06% in crash_v_recovery, PF flat — gate blind spot, caught manually) with no offsetting gain anywhere |
| MIN_PROFIT_FOR_SOFT_EXIT 25% | exit gate | principle backwards (trapped underwater laggards); redesigned away, not swept |

### Proven (kept unchanged — both directions reject or degrade)

| Param | Default | Loosen result | Tighten result |
|---|---|---|---|
| ADX_TREND_THRESHOLD | 20 | overfit (train↑, test flat/worse) | REJECT |
| EXTENSION_CAP_PCT | 15% | REJECT | REJECT |
| MOMENTUM_RSI (exit) | 50 | REJECT | REJECT |
| REGIME_CONFIRM_DAYS | 3 | overfit train-only | REJECT |
| BREAKOUT_PCT | 5% | overfit train-only | REJECT |
| DD_KILL_PCT | 10% | PASS technically (test/stress byte-identical) but train-only cost, no upside anywhere — not adopted | REJECT (train N crashes 164→64, test Sharpe fails tolerance, new instability) |
| DD_REDUCE_PCT | 5% | REJECT (sideways_chop PF 0.88→0.73) | REJECT (test Sharpe fails tolerance) |
| GOLDBEES_MAX_LOSS_PCT | 7% | REJECT (sideways_chop PF 0.88→0.69) | REJECT, badly (test Sharpe 0.22→0.02, PF 1.28→1.14, sideways_chop PF 0.88→0.49) |

Pattern across all 8 proven params: current live values are genuine local
optima, not curve-fit artifacts. Every "improvement" direction that showed a
train/full-window gain was flat-or-worse on the held-out test window or a
stress scenario — the classic overfit signature — and every degrading
direction failed outright. No lever in E4/E5/E7/E8 moved the needle without
a cost the gate (or manual inspection) caught.

### Not run — E1/E2/E3/E6 out of scope this pass

- **E1 (universe contamination)** — already resolved 2026-07-07, see
  [[universe_lookahead_confirmed_20260707]]; fix is forward-only, not re-run.
- **E2 (signal-only exit)** — already shipped 2026-07-08 as a design decision,
  not re-tested as an experiment.
- **E3 (cash allocation)** — already shipped (100% invested), not re-tested.
- **E6 (rotation/SCORE_DROP bundle)** — not env-driven in `config/settings.py`
  (hardcoded in `portfolio/manager.py`); needs wiring before it can go through
  `robustness_gate.py`. Lower priority per original ranking — deferred, not
  abandoned.

### Net effect on codebase this session

- `config/strategy_config.yaml`: 4 dead keys removed.
- `config/settings.py`: 4 dead constants removed (RSI_BUY_MIN/MAX,
  MIN_VOLUME_RATIO, TAKE_PROFIT_PCT); ADX/EXTENSION/BREAKOUT/REGIME_CONFIRM
  wired to env vars for testability, kept at proven defaults.
- `strategy/entry.py`: ATR% volatility filter and VCP-branch RVOL check fully
  deleted (44 lines net removed).
- `strategy/exit.py`: LAGGARD_RS constant + exit branch fully deleted; only
  MOMENTUM_DECAY(RSI) remains as a soft exit (105 lines net removed,
  including the already-prior stop-loss/trailing-stop/take-profit removal).
- Test suite: 89/90 passing (1 pre-existing unrelated failure in
  `test_portfolio.py::test_rejects_over_stock_cap`, not touched this session).

## XIV. Entry Attribution Suite (2026-07-09) — is the entry signal itself real?

All prior sections (I-XIII) proved individual *parameters* sit at local optima.
None of them tested whether the entry/stock-selection SIGNAL (RS rank + ADX/
breakout/SuperTrend trend gate) has any real predictive edge at all, as
opposed to the rest of the system (regime filter, exits, sizing, universe)
doing all the work. This section closes that gap.

**Method**: `scripts/entry_attribution.py`, 7 arms, same full window
(2022-01-01 → 2026-07-09), same regime filter / exits / sizing / universe /
slippage held fixed — only `ENTRY_MODE` varies (`strategy/entry.py`,
`strategy/signals.py`). RANDOM_ALL, RANDOM_ELIGIBLE, SHUFFLE_RS averaged over
3 seeds (42, 7, 123) to separate signal from noise.

Mid-session bug found and fixed: `backtest/engine.py` independently re-sorts
BUY signals by `.score` descending before filling slots — this was silently
collapsing RANDOM/REVERSE/PURE_ADX_BREAKOUT execution order back to real-RS-
descending regardless of `signals.py`'s intended ranking (run 1 showed
RANDOM_ELIGIBLE byte-identical to PURE_RS across all 3 seeds — the tell).
Fixed by encoding each mode's intended ranking into the `score` value itself.
Verified via `robustness_gate.py` smoke test (byte-identical FULL-mode output)
before and after the fix. Results below are from the corrected run
(`outputs/entry_attribution_run2.log`); run 1 is invalid, discarded.

| Arm | CAGR | Sharpe | MDD | WR | PF | N |
|---|---|---|---|---|---|---|
| FULL (live) | +5.62% | 0.39 | 19.99% | 40.2% | 1.30 | 266 |
| RANDOM_ALL (avg 3 seeds) | -3.38% | -0.14 | — | — | 1.06 | ~429 |
| RANDOM_ELIGIBLE (avg 3 seeds) | -2.66% | -0.10 | — | — | 1.09 | ~460 |
| REVERSE_RS | +12.95% | 0.75 | 19.31% | 37.7% | 1.48 | 305 |
| SHUFFLE_RS (avg 3 seeds) | +0.44% | 0.12 | — | — | 1.16 | ~292 |
| PURE_RS (RS gate only) | +7.30% | 0.45 | 22.58% | 39.8% | 1.33 | 329 |
| PURE_ADX_BREAKOUT (trend gate only) | +8.52% | 0.54 | 21.30% | 38.5% | 1.41 | 273 |

**Findings**:
1. **Signal beats random, decisively.** FULL beats RANDOM_ALL/RANDOM_ELIGIBLE
   by ~8-9pp CAGR and flips Sharpe from negative to +0.39. The entry gates
   are filtering for real edge, not noise — this is the headline result and
   it's unambiguous even accounting for seed variance in the random arms.
2. **ADX/breakout (trend gate) is the stronger single component, not RS.**
   PURE_ADX_BREAKOUT beats PURE_RS on every metric (CAGR, Sharpe, PF, MDD).
3. **The two gates are not additive — combining them in FULL actively costs
   CAGR.** FULL (+5.62%) underperforms BOTH single-factor arms (PURE_RS
   +7.30%, PURE_ADX_BREAKOUT +8.52%). Double-gating over-constrains the
   candidate pool. FULL does buy this back partially as the best (lowest)
   MDD of the ungated arms — a real risk/return trade-off, not a wash.
4. **RS ranking (order) carries no positive value, and directionally
   negative.** REVERSE_RS — same gates as FULL, worst-RS-first instead of
   best-RS-first — is the single best-performing arm of all seven
   (+12.95% CAGR, Sharpe 0.75, PF 1.48). Within the RS>=72-qualified pool,
   higher RS does not predict better forward returns.
5. **RS *labeling* (which stock gets which value) still matters for gating.**
   SHUFFLE_RS collapses to near-breakeven (+0.44%, Sharpe 0.12) — decoupling
   a stock's RS value from its real momentum destroys most of the edge, even
   though ranking by that value (finding 4) doesn't help. The RS>=72
   threshold is doing real admission work; the RS *sort order* is not.

**Reading**: the live system's edge comes overwhelmingly from the
ADX/SuperTrend/breakout trend-confirmation gate, with RS acting as a
correctly-labeled but non-informative threshold filter rather than a ranking
signal. Sorting by RS (current live behavior) is very likely leaving CAGR on
the table relative to either dropping the RS sort or reversing it — but this
is a single full-window result on a possibly universe-contaminated dataset
(see docs/13 caveat below) and has NOT yet been through the OOS/stress-test
gate that every other lever in this doc was required to clear. Do not act on
REVERSE_RS without running it through `robustness_gate.py` first.

**Caveats**:
- Universe look-ahead contamination (docs/13 §2/§4/§10 — static watchlist
  applied retroactively) affects all 7 arms identically (same universe, same
  window), so relative comparisons between arms should be largely unaffected,
  but every absolute CAGR number above still carries that known caveat.
- RANDOM_ALL/RANDOM_ELIGIBLE showed wide per-seed variance (roughly -7pp to
  +3pp CAGR) — the *qualitative* conclusion (signal beats random) is robust
  to this, the *magnitude* (~8-9pp) is not precise.
- The opportunistic mid-day slot-replacement mechanism in `backtest/engine.py`
  (fires on `score >= 85` vs weakest-held-RS) is inert for REVERSE_RS/
  RANDOM_ALL/RANDOM_ELIGIBLE/PURE_ADX_BREAKOUT since their score values sit on
  a different scale than the RS-calibrated thresholds. This only suppresses
  one secondary mid-cycle mechanism, not the primary top-N slot-fill logic
  the experiment's validity rests on.

**Next step (not yet done)**: run REVERSE_RS and PURE_ADX_BREAKOUT through
`robustness_gate.py` as candidate levers before considering any live change —
same bar as every other parameter in this document.

## XV. Signal-frequency diagnostics (2026-07-09) — how often does the signal fire, and why

`scripts/signal_diagnostics.py` on `outputs/backtest_decisions.csv` (fresh
FULL-mode full-window run, 838 trading days, 2022-01-01→2026-07-09). Added
`regime`/`market_bullish` columns to the decision log (`backtest/engine.py`
line ~666, `backtest/reporter.py`) to make this possible.

- **Zero-signal days: 8 / 838 (1.0%), all 8 during BEAR regime.** Every single
  BULL-regime day in the window produced at least one qualified candidate.
- **Candidate count is abundant, not scarce**: mean 10.4/day, median 11, when
  >0. Only 4 days ever had exactly 1 candidate.
- **Rejection reasons (70,859 symbol-day NO evaluations)**: Low RS dominates
  at 82.3%, distantly followed by Not at Breakout (9.8%), Weak ADX (3.3%),
  Overextended (2.4%), SuperTrend bearish (1.6%), Weak Trend (0.6%).
  RS_THRESHOLD=72 is by far the binding constraint on the universe.
- **Ranking has a real choice to make on 99.5% of signal days** (826/830
  had >1 qualified candidate) — REVERSE_RS's outperformance (§XIV) is
  therefore not an edge-case artifact, it's exercised almost daily.
- **Qualified vs executed: only 2.1%** (177/8,633) of qualified symbol-day
  rows are actually bought. The bottleneck is not signal scarcity — it's slot
  scarcity (MAX_OPEN_POSITIONS) and holding-period throttling, i.e. portfolio
  construction, not the entry signal. Consistent with the standing finding in
  memory `portfolio_construction_is_the_leak`.

## XVI. Opportunity Attribution Engine (2026-07-09) — best available vs actually bought

`scripts/opportunity_attribution.py` — scores EVERY qualified candidate
(not just executed trades) by forward 20-trading-day return, using the
same FULL-mode decision log as §XV joined against cached OHLCV
(`db.repository.load_ohlcv`). Answers "among all valid signals today, did
we buy the best one, and if not, exactly why" — a different question from
§XIV/§XV ("why did we buy this stock").

8,633 qualified symbol-day rows, 100 symbols, 830 trading days.

**Why qualified signals weren't bought:**

| Reason | Count | % |
|---|---|---|
| NO_SLOT_AVAILABLE (nothing bought anywhere that day) | 6,435 | 74.5% |
| OUTRANKED_SAME_DAY (lost to a same-day higher-RS pick) | 1,154 | 13.4% |
| BEAR_BLOCKED (market_bullish=False) | 850 | 9.8% |
| BOUGHT | 177 | 2.1% |
| SKIPPED_DESPITE_QUALIFYING (rank ≥ what was bought, still skipped) | 17 | 0.2% |

**Forward-return quality (20-trading-day horizon):**

| Group | Mean | Median |
|---|---|---|
| All qualified candidates | +1.14% | +0.57% |
| Best available each day (perfect-hindsight ceiling) | +13.48% | +11.37% |
| Actually bought | +0.87% | +0.62% |

**Was the ranking correct?** Daily Spearman(rank_score, forward return)
across 804 days with ≥3 candidates: mean **+0.016** — statistically
indistinguishable from zero. 52.6% of days positive, 45.9% negative. RS rank
carries no forward-return information. This corroborates §XIV's REVERSE_RS
backtest finding (one full-window realization) with a proper day-by-day
correlation test across the whole sample.

**Actually bought (+0.87%) underperforms the average qualified candidate
(+1.14%).** A naive random draw from the qualified pool would have done
slightly better than the RS-first selection rule, at this horizon.

**Forced to skip winners?** Narrow definition (qualified candidate ranked ≥
what was actually bought that day, yet unbought): 17 cases (0.2%), none
exceeded 10% return over the horizon. The rank-priority mechanism itself is
not leaving big obvious winners behind — the dominant leak is upstream:
slot scarcity (74.5%) blocks almost all qualified signals regardless of rank.

**Caveats**: "best available" is a perfect-hindsight ceiling (max of ~10
draws/day), not an achievable benchmark — included to show the theoretical
opportunity size, not as a target. Fixed 20-day horizon may understate real
trade P&L since actual positions often hold 31+ days (`trade_attribution_engine_20260701`
memory) — chosen for apples-to-apples comparability across bought and
unbought candidates alike, not as a claim about real exit timing. Same
universe look-ahead caveat as all other historicals in this document (docs/13).

**Reading**: this sharpens §XV's finding. The leak is not entry-signal
quality (signal beats random, §XIV) and not really the ranking mechanism's
selection failures (only 0.2% clear misses) — it's that 3 in 4 qualified
signals never get a chance because no slot is open. Where ranking DOES
apply (the 13.4% OUTRANKED_SAME_DAY cases), it's selecting on a signal
(RS) with ~zero correlation to forward return — so those exclusions are
close to random, not principled.

## XVII. Rotation & capacity follow-up (2026-07-09) — answering "is the leak fixable"

User pushed 4 sharper questions on §XVI. `scripts/rotation_opportunity.py`,
`scripts/ranking_metric_comparison.py`, and a direct capacity backtest
(`MAX_POSITIONS` env override x3).

**Q1 — Is ranking informative at the ACTUAL holding horizon?** Real
hold_days from 266 trades: median 11, mean 16.4, p75 21. Daily Spearman
(rank_score, fwd_return) swept across horizons 5–90d:

| horizon | mean corr | %pos | %neg |
|---|---|---|---|
| 5d | +0.004 | 50.7% | 48.1% |
| 10d | -0.008 | 50.2% | 48.3% |
| **11d (median hold)** | **-0.005** | 49.6% | 48.5% |
| **16d (mean hold)** | **+0.011** | 53.4% | 45.6% |
| 20d | +0.016 | 52.6% | 45.9% |
| 30–90d | +0.038 to +0.046 | 55.8–57.5% | 41.7–43.6% |

No. At the real holding horizon (11–16d) correlation is ~zero to
slightly negative. It's marginally less-zero at 30–90d but never exceeds
~0.05 (still not usefully predictive at any horizon tested).

**Q2 — Within NO_SLOT_AVAILABLE, how many skipped signals beat the
weakest held position?** 6,435 NO_SLOT_AVAILABLE rows, each compared to
the currently-held position with the lowest same-window (20d) forward
return on that date:

- 4,671 / 6,435 (**72.6%**) of skipped signals would have outperformed
  the weakest held position over the same forward window.
- Mean forward return of skipped signal: **+1.50%**
- Mean forward return of weakest held position (same window): **-3.77%**
- Mean edge when the skip does beat the weak hold: **+8.97%**

The portfolio is routinely sitting on a losing position while a better
qualified candidate goes unbought for lack of a slot — this is a rotation
problem, not a signal-quality problem.

**Q3 — Can a different ordering metric improve selection without changing
qualification rules?** Same daily-Spearman-vs-forward-return test applied
to adx, "freshness" (−extension from EMA50), "at_pivot" (−|distance from
20d high|), and turnover (liquidity), all logged fresh into the decision
log (`backtest/engine.py` extended with `adx`, `extension_pct`,
`breakout_dist_pct`, `turnover` fields):

| metric | mean corr |
|---|---|
| rs_rank (current) | **+0.016** |
| adx | -0.007 |
| freshness | -0.033 |
| at_pivot | -0.027 |
| turnover | -0.031 |

No. rs_rank is already the best of the 5 tested, and it's still
statistically indistinguishable from zero. The leak is not "wrong ranking
metric" — none of the readily-available per-candidate fields carry real
forward-return signal at this stage of the pipeline.

**Q4 — How much of the missed opportunity could capacity increase
actually capture?** Real backtest rerun (not a proxy) at MAX_POSITIONS =
3 (baseline), 5, 8:

| slots | CAGR | Sharpe | MaxDD | Trades | WinRate | PF |
|---|---|---|---|---|---|---|
| 3 (current) | +5.62% | 0.39 | 19.99% | 266 | 40.2% | 1.30 |
| **5** | **+8.89%** | **0.54** | 20.50% | 405 | 36.3% | 1.36 |
| 8 | +5.19% | 0.38 | 20.08% | 593 | 34.9% | 1.30 |

5 slots meaningfully beats 3 (CAGR +3.27pp, Sharpe +0.15, MDD roughly
flat +0.5pp, PF better despite lower win rate — bigger average winners).
8 slots gives it all back (CAGR below baseline, Sharpe worse) — likely
dilution into lower-conviction candidates once real slot scarcity is
relieved, consistent with Q1/Q3 showing the ranking that decides who gets
the *next* slot carries no real signal. There is a real, capturable
capacity effect, but it is **not monotonic** — more is not always better.

**Caveat**: full-window only, not yet run through `scripts/robustness_gate.py`
(OOS + 4 stress scenarios) — required before any capacity change is
considered for live deployment, per this project's standing protocol
(`robustness_gate` memory). Naive proxy (best-missed-candidate/day, mean
+14.78%) from `rotation_opportunity.py` is NOT a capture estimate — it
ignores capital ties and overlapping holds; the real backtest rerun above
supersedes it as the actual answer to Q4.

## XVIII. MAX_POSITIONS=5 robustness gate REJECT, and the replacement-policy gap (2026-07-09)

User's framing for §XVII's capacity finding: don't ask "is 5 better than 3,"
ask "is the improvement robust across conditions." Ran
`scripts/robustness_gate.py --env MAX_POSITIONS=5` (full-window + OOS
train/test + 4 synthetic stress scenarios, baseline=3 vs candidate=5).

**Verdict: REJECT — 5 gate failures.**

| Window | baseline (3) | candidate (5) |
|---|---|---|
| TRAIN 2022-2024 | CAGR +8.71%, Sharpe 0.55 | CAGR +18.01%, Sharpe 0.92 |
| TEST 2025-2026 | CAGR +1.26%, Sharpe 0.22, PF 1.28 | **CAGR -2.85%, Sharpe -0.02, PF 1.14** |
| FULL | CAGR +5.62%, Sharpe 0.39 | CAGR +8.89%, Sharpe 0.54 |

The full-window win reported in §XVII was almost entirely a TRAIN-period
artifact (CAGR more than doubles train, +8.71%→+18.01%) that inverts
out-of-sample (candidate goes CAGR-negative, Sharpe-negative on TEST).
Candidate introduces new train/test instability not present in baseline.

Stress scenarios: `crash_v_recovery` CAGR flips negative (base +11.02% →
candidate -0.74%); `prolonged_sideways_chop` PF drops materially (0.88 →
0.72). Exactly the "wins because of one favorable regime" failure mode the
user flagged as disqualifying before even seeing the numbers.

**MAX_POSITIONS=5 is rejected. Do not deploy.** Confirms the non-monotonic
3→5→8 shape from §XVII was, more precisely, a train-period-only artifact —
not a real, robust capacity effect.

---

**Replacement-policy diagnostic** (`scripts/replacement_diagnostic.py`,
per user's request): for each of 421 days with a full portfolio and a
skipped qualified candidate, compared the single weakest held position
(lowest own 20d-forward return from that day) against the single best
skipped candidate that day:

- Swap would have helped (positive future difference): **415 / 421 (98.6%)**
- Swap would have hurt: 6 / 421 (1.4%)
- Mean future difference: **+19.60%** (helped: +19.92%, hurt: -2.82%)
- Median: +16.39%

**Caveat on magnitude**: this compares the single WORST holding to the
single BEST candidate each day — both tails cherry-picked, same inflation
mechanism as the "best available" ceiling in §XVI. The magnitude is not a
capture estimate. The 98.6% directional consistency is the real signal:
it is not a fluke, it is structural.

**Why wasn't the weakest holding ever replaced?** Checked the code
directly: `strategy/exit.py::check_exit_conditions` exits ONLY on RSI
momentum decay (`RSI < 50`) — no comparison to available candidates
exists in the exit path at all. `strategy/defensive_portfolio.py` defines
`ROTATE_EXIT_RS`, `ROTATE_INTO_RS`, `ROTATE_MIN_GAP` constants but **they
are never called anywhere in `backtest/engine.py`** — dead, unwired
config, not a tuned-conservative parameter. This is not "rotation policy
is too conservative" — there is no rotation policy in the live code.

**Reading**: user's proposed reframe (signal → candidate pool → portfolio
optimizer → N holdings, not signal → rank → top-K) is directionally
supported by all of §XVI/§XVII/§XVIII: ranking is uninformative at every
horizon tested, more static slots don't robustly help, but a policy that
compares held positions against the available pool (rotation) shows a
98.6%-consistent directional edge with no ranking-quality dependency
required — it only needs "is there something in the pool better than my
worst holding," which needs no predictive ranking at all, just a forward-
looking comparison mechanism that does not currently exist.

**Next**: build and robustness-gate an actual replacement-policy backtest
(state-dependent: swap changes what's held for subsequent days, needs its
own simulated run, not a static day-by-day table) before any live change.

---

## XIX. Correction: rank-replacement mechanism DOES exist — two real bugs found + fixed (2026-07-09)

**§XVIII's claim was wrong.** "There is no rotation policy in the live
code" — false. A rank-replacement mechanism DOES exist in
`backtest/engine.py`'s "Execute Buys" section (separate from the genuinely
-dead `ROTATE_*` constants in `strategy/defensive_portfolio.py`, which
really are unused). It uses its own locally-scoped thresholds and
`exit_reason="RANK_REPLACED"`. The grep in §XVIII searched for
`ROTATE_EXIT_RS|ROTATE_INTO_RS|ROTATE_MIN_GAP|SCORE_DROP_EXIT` and missed
it because it uses different names. Re-reading `engine.py` line-by-line
(not grep) surfaced it.

It fires **0 times** in the live 266-trade backtest. Two independent bugs,
both now fixed:

**Bug 1 — self-defeating outer gate.** The branch only runs when
`available_slots == 0` (portfolio full — the only time replacement is
useful), but was gated on `trades_ok` from `can_open_new_trades()`, which
returns False whenever `len(open_positions) >= MAX_OPEN_POSITIONS` — i.e.
always False in exactly the state this branch requires. Unreachable by
construction, for any threshold values. Confirmed via `DEBUG_REPLACE=1`
instrumentation: 0 debug rows logged across the full backtest before the
fix, 478 after. Fixed by replacing `trades_ok` with a narrower
`replace_risk_ok` (daily-trade-limit + drawdown-breaker only, no
capacity check) for the outer gate, and by **recomputing** `trades_ok`
immediately after the eviction so the buy loop below — which still uses
`trades_ok` — sees the freed slot instead of the stale pre-eviction
`False`. Without the second half of this fix, the branch would evict the
weak holding and then fail to buy the replacement (pure eviction, net
negative).

**Bug 2 — inner thresholds never empirically checked, and wrong by an
order of magnitude.** Once the outer gate was reachable, `DEBUG_REPLACE`
data on all 478 full-portfolio-with-better-candidate days showed:
`weakest_rs <= 55` (config default `REPLACE_MAX_HELD_RS`) was **never**
true — actual weakest-held RS ranges 56.5–98, mean 88.8. `weakest_profit
>= 0.25` (`MIN_PROFIT_SOFT`) was true in **1 of 478** days — actual mean
turnover on the weakest holding is +2.2%, not +25%. Both thresholds were
invented, never checked against what the strategy actually holds. This
also retroactively explains the "vacuous PASS" in the earlier
`MIN_PROFIT_SOFT=-1.0` gate test in §XVIII's working notes: it changed a
threshold gated behind an already-unreachable outer condition (Bug 1), so
it could not have had any effect regardless of value — the PASS was
correctly flagged as vacuous at the time, and this is the full explanation
why.

**Fix applied to `backtest/engine.py`**: `trades_ok` → `replace_risk_ok`
on the outer gate; `trades_ok` recomputed post-eviction. Inner thresholds
(`REPLACE_MAX_HELD_RS`, `REPLACE_MIN_GAP`, `MIN_PROFIT_SOFT`,
`REPLACE_MIN_NEW_RS`) left at old defaults for now — still need
recalibration against the real distribution above before this can fire in
the default/live-equivalent path. Default-threshold behavior after the
Bug 1 fix alone: **0 RANK_REPLACED trades still** (Bug 2 blocks it), so
this fix by itself changes nothing about default backtest output — safe,
no re-validation needed for the Bug-1-only state.

**Candidate recalibration tested via `robustness_gate.py`**:
`REPLACE_MAX_HELD_RS=90 REPLACE_MIN_GAP=5 MIN_PROFIT_SOFT=0.0` (picked
near the actual observed weakest_rs floor/median, and profit>=breakeven
instead of >=25%; chosen from grid search on the same 478-day debug
sample, not blind guessing).

**Mechanism confirmed working**: 28 `RANK_REPLACED` trades fired (vs 0
before both fixes), mean net PnL on the replaced-out leg +1.9% (exits
clean, not forced losses).

**Gate verdict: mechanical PASS, but not a result worth shipping.**
`robustness_gate.py`'s pass rule only checks TEST-window Sharpe/PF deltas
and stress-scenario CAGR-sign-flips/PF-drops — it does not gate on
full-window or TRAIN-window degradation. Both degraded here:

| Window | baseline | candidate |
|---|---|---|
| TRAIN 2022-2024 | CAGR +8.71%, MDD 19.99%, N=164 | CAGR +4.31%, MDD 23.17%, N=190 |
| TEST 2025-2026 | CAGR +2.33%, Sharpe 0.27, PF 1.28 | CAGR +3.18%, Sharpe 0.32, PF 1.31 |
| FULL | CAGR +5.98%, Sharpe 0.41, MDD 19.99%, N=266 | CAGR +2.98%, Sharpe 0.26, MDD 23.17%, N=293 |

Full-window CAGR nearly halved, Sharpe fell 0.41→0.26, MDD worsened
20.0%→23.2%, trade count up 266→293 (churn from the extra replacement
trades). `crash_v_recovery` stress CAGR fell 22.60%→13.02% (PF improved
0.56→0.70, but CAGR is the more decision-relevant number). Only the
narrow TEST window and one stress PF number improved. This is the exact
"looks fine by the mechanical gate, isn't actually a win" case the user's
framing exists to catch — **not recommended for deployment** at this
calibration. Not pursuing further threshold grid-searching on top of this
result — repeated re-tuning against the same historical window risks
p-hacking a pass rather than finding a real edge, contrary to this
project's standing "every parameter must be empirically proven" rule.

**Where this leaves the three research questions from §XVIII**: (1) 5
slots — REJECTED (§XVIII). (2) smarter replacement of weak holdings — the
mechanism now works mechanically but this calibration is a net full-window
negative; the 98.6% static-diagnostic consistency (§XVIII) did not survive
being turned into an actual day-by-day policy. (3) portfolio-objective
improvements beyond ranking/replacement — not yet explored. Net: no
further finding this phase supports changing the live system.

**Memory correction**: `replacement_policy_gap_20260709.md`'s claim "there
is no rotation/replacement mechanism in the live system at all, full
stop" is superseded by this entry — mechanism exists, was unreachable due
to two bugs, now fixed and confirmed to fire, but the tested calibration
underperforms baseline on the full window and is not recommended.

---

## XX. Signal mechanism analysis: why does one qualified signal win and another lose? (2026-07-10)

User's question, restated: not "which ranking formula orders candidates
best" (closed — §XVII, all of rs_rank/adx/freshness/at_pivot/turnover
~0 correlation) but "what MARKET MECHANISM separates a qualified signal
that becomes a winner from one that becomes a loser." Four candidate
mechanisms, all newly available in the data (added `vol_ratio`, `sector`,
`sector_rel_rs` to `backtest/engine.py`'s decision log — `sector_rel_rs`
computed from the FULL day's universe via a new per-day sector-average-RS
precompute at the top of the daily loop, not from the filtered candidate
pool, to avoid selection bias):

- **institutional/volume acceleration** (`vol_ratio`: today's volume vs
  its own 20d average)
- **breakout freshness** (`breakout_dist_pct`: distance past/short of the
  20d high)
- **sector leadership** (`sector_rel_rs`: stock's rs_rank minus its own
  sector's average rs_rank that day — is this stock the leader within its
  sector)
- **sector tailwind** (`sector_rs_avg`: the sector's own average rs_rank
  that day — is the whole sector strong, independent of which stock in it
  you pick)

`scripts/signal_mechanism_analysis.py`, run at the real median holding
horizon (11d) and cross-checked at 20d for robustness, n=8,633 qualified
signals both times:

| Feature | rho (11d) | p (11d) | rho (20d) | p (20d) |
|---|---|---|---|---|
| vol_ratio (institutional) | +0.008 | 0.376 (n.s.) | +0.013 | 0.288 (n.s.) |
| breakout_dist_pct (freshness) | -0.040 | 0.0002 | -0.038 | ~0.0003 |
| sector_rel_rs (leadership) | -0.0325 | 0.003 | -0.0130 | n.s. borderline |
| sector_rs_avg (tailwind) | +0.0403 | 0.0002 | +0.06 | 0.0002 |
| rs_rank (baseline) | ~+0.02 | — | ~+0.02 | — |
| adx (baseline) | +0.0523 | 0.0134 | — | 0.0105 |

**Verdict: none of the four mechanisms are usable.** At n=8,633 even
|rho|=0.03–0.06 clears p<0.05 — that is a sample-size artifact, not a
tradeable effect. All four stay in the same dead zone as every ranking
metric tested since §XVI (|rho| under ~0.06, versus the ~0.2–0.3 floor
that would make a factor worth building a rule around).

**The one mildly interesting, reproducible-at-both-horizons pattern**:
`sector_rel_rs` is consistently *negative* — being the strongest stock
relative to your own sector predicts marginally *worse* forward returns
than being a laggard inside a strong sector, while `sector_rs_avg` (sector
strength itself) is consistently positive. These roughly cancel inside
plain `rs_rank`, which is why raw RS ranking nets out to ~0 net signal
(§XVII) despite sector strength alone carrying a faint positive signal.
Directionally consistent with the REVERSE_RS finding in the original
entry-attribution suite (individual-stock RS ranking has no positive
value; picking against it does no worse and sometimes better) — but the
magnitude here is too small to act on either.

**Answer to the user's question**: none of institutional-volume-surge,
breakout-freshness, individual-sector-leadership, or sector-tailwind
strength meaningfully separates winning qualified signals from losing
ones in this data. This is a clean null result, not an inconclusive one —
tested at two horizons, large n, explicit significance testing done
correctly (not just eyeballing correlation signs). Consistent with the
project's broader finding that the executable edge lives in "is a signal
real vs random" (§entry_attribution_suite: +5.62% vs -3%), not in which
qualified signal you pick among several. Not recommending further
single-factor mechanism search — same p-hacking-risk reasoning as §XIX.
Remaining unexplored avenue from §XIX's three research questions:
portfolio-objective improvements (diversification, turnover cost, capital
efficiency) rather than picking a "best stock" at all.

---

## XXI. Rotation logic synthesis: "should A be replaced?" (2026-07-10)

User's framing: Holding A (weak) / B (medium) / C (strong), candidate D
(strong) appears. Should A be replaced? Reframed as "why are we still
holding weak positions when better qualified signals appear," flagged as
"probably underexplored" and possibly mattering more than initial
ranking.

**It isn't underexplored — §XIX already built and tested exactly this,
and §XX explains why it failed.** Putting the two together:

1. By default, nothing in the live exit path compares a held position to
   the available candidate pool at all — the only exit trigger is RSI
   momentum decay. The one mechanism that could do this comparison
   (`RANK_REPLACED` in `backtest/engine.py`) was unreachable by
   construction (§XIX, Bug 1) and, once reachable, gated on thresholds
   never checked against real data (§XIX, Bug 2). Both fixed.
2. With it working, the trigger for "should A be replaced" is RS rank:
   swap when the candidate's RS is well above the weakest holding's RS.
   §XX just showed RS rank (and every other single-day signal tested —
   volume surge, freshness, sector leadership, sector tailwind) carries
   essentially zero forward-return information (|rho| < 0.06, n=8,633,
   two horizons).
3. So "A is weak / D is strong" *by RS* is not a reliable proxy for "A
   will underperform / D will outperform." Confirmed empirically, not
   just by this reasoning: the fixed-and-recalibrated `RANK_REPLACED`
   mechanism (§XIX), tested for real, made the portfolio WORSE — full CAGR
   nearly halved, MDD up — despite passing the narrow OOS-only gate.

**Answer**: Should A be replaced? In hindsight, yes, almost always
(98.6% of the time per the §XVIII static diagnostic, which used realized
future returns — a crystal-ball measure, not a tradeable one). Live, with
only present-day signals available, no — not on any trigger tested here,
because none of those triggers predict which position is actually about
to underperform or outperform. A rotation policy is only as good as its
trigger; every trigger available in this dataset is noise for this
purpose. This is not a separate open question from §XX's mechanism
result — it's the same finding, seen twice, through a different lens
(ranking a snapshot vs. triggering a swap).

**What would actually unblock rotation**: a trigger with real forward-
return information that isn't already covered by rs_rank/adx/vol_ratio/
sector features/freshness — e.g. something off this dataset entirely
(earnings calendar, order-flow/promoter-holding changes, index
inclusion/exclusion, analyst-rating changes) — none of which exist in the
current data pipeline. Without that, more rotation-logic engineering on
top of existing signals is not expected to help; the constraint is
data, not mechanism design.

**Next open avenue** (unchanged from §XX): portfolio-objective
improvements not dependent on picking better positions at all —
diversification/correlation-aware sizing (previously rejected once at
MAX_POS=3, [[phase2_improvements]] — worth reconsidering only if slot
count or sizing rules change), turnover/cost reduction, capital
efficiency (cash drag, already at 100% deployment per recent commits).

---

## XXII. Signal environment, sector durability, lifecycle, archetypes (2026-07-10)

User's 5-question follow-up to §XX/§XXI's null result on continuous
factors: economic cause, market environment, sector durability, signal
lifecycle, archetypes. `scripts/signal_lifecycle_archetype_analysis.py`,
n=8,633 qualified signals (6,449 BULL / 753 BEAR), 11d horizon (real
median hold).

**Economic cause — not answerable.** This pipeline is price/volume/
derived-indicator only. No earnings, news, order-flow, or fundamentals
data exists anywhere in the codebase. Any "why did this happen
economically" answer would be fabricated. Technical cause (breakout vs
drift vs gap pattern) is answered via archetypes below instead.

**Market environment (regime) — real, large effect.** BULL-regime
qualified signals: mean fwd_ret +1.02%, win rate 55.1%, n=6,449.
BEAR-regime qualified signals: mean fwd_ret **-0.88%**, win rate 45.3%,
n=753. Mann-Whitney p<0.0001. Unlike every factor tested in §XX, this
isn't a sample-size artifact — the group means have opposite signs.
Caveat: `signal=="YES"` means "passed `check_entry`," not "was bought" —
`selected`/actual purchase additionally requires `market_bullish` for the
main buy path, with a separate BEAR_SWING mechanism
(`strategy/defensive_portfolio.py`) handling some BEAR-regime buys
already. This finding is consistent with, not contradictory to, the
system's existing regime-cautious design — it quantifies that caution was
well-founded, it doesn't reveal a new unexploited gap.

**Sector durability — real, strong, new.** Sector t-stat (mean/SE) as a
durability measure, min n=40:

| Sector | n | mean fwd_ret | win_rate | t |
|---|---|---|---|---|
| (durably positive, t>2.9) — Financial Services, Capital Goods, FMCG, ETF, Auto, Consumer Services, Metals & Mining, Services | — | positive | — | +2.9 to +7.6 |
| (durably negative, t<-3) — IT, Chemicals, Construction Materials | — | negative | — | -3 to -4.7 |
| (noise, \|t\|<1) — Healthcare, Power, Oil & Gas, Consumer Durables, Construction | — | mixed | — | ~0 |

(Exact per-sector figures in script output, not hand-copied here to avoid
transcription error — re-run `scripts/signal_lifecycle_archetype_analysis.py`
for current numbers.) t-stats up to 7.6 across 16 sectors survive Bonferroni
correction (α=0.05/16≈0.003) by a wide margin — not a multiple-comparisons
artifact. **This is the first factor found in the whole opportunity-
attribution research arc (§XVI-XXII) with an effect size worth testing as
an actual entry filter**, unlike every continuous-ranking factor in §XX
(all \|rho\|<0.06). Not yet implemented or gate-tested — sector exclusion
as an entry filter has never been tried in this project (distinct from
the existing correlation-based position-*sizing* caps mentioned in
[[trade_attribution_engine_20260701]], which is about exposure sizing,
not entry eligibility).

**Signal lifecycle — real, monotonic decay.** Grouped by day-position
within a symbol's consecutive-qualifying-day streak (day 1 = just started
qualifying, "birth"; later days = "decay"): day1 mean +1.29%/57.3% win →
day2-3 → day4-7 → day8+ mean +0.50%/51.7% win, monotonically decreasing.
Mann-Whitney day1 vs day4+: p=0.0008. This is a *different* freshness
concept than `breakout_dist_pct` (§XX, null) — that measures price
distance from the 20d high; this measures how many consecutive days the
stock has already been sitting in the qualified pool. A stock that just
started qualifying today has a better expected forward return than one
that's been qualifying for over a week already (likely because slower-
moving structural strength is already partly priced in by then). Also
untested as a lever — e.g. prioritizing day-1/fresh qualifiers over
week-old ones when multiple candidates compete for a slot.

**Archetypes — mostly confirms existing filters, no new edge.**
Pre-specified buckets (not data-mined): `fresh_volume_breakout`
(near-pivot + vol_ratio≥1.5) mean +1.13% vs baseline +0.82%;
`high_adx_trend` (ADX≥30) mean +1.06%; `quiet_sector_drift` near
baseline. `extended_momentum` (breakout_dist_pct>10%) and
`low_adx_weak_trend` (ADX<20) both had **zero** qualified signals — the
existing entry rules (ADX + SuperTrend hardening, commit 9fc9782) already
exclude those patterns entirely. Expected, not a new finding — the recent
entry-rule hardening is doing what it was meant to do.

**Net read**: this round broke the §XX null. Sector and lifecycle-streak-
position are new, real, moderately strong effects that have never been
tested as actual entry/selection levers in this project. Regime confirms
existing design is sound rather than revealing a gap. **Neither sector
filtering nor streak-position prioritization has been implemented or
robustness-gated yet** — next step if pursued: build candidates (e.g.
exclude IT/Chemicals/Construction Materials from entry, or prefer day-1
streak signals when multiple qualify same day) and run them through
[[robustness_gate]] before any live consideration, same bar as every
lever in this project's history.

## §XXIII. Experiment A/B: sector blacklist and streak-priority, gate-tested in isolation

Per explicit instruction: test the two §XXII candidate levers (sector
durability, lifecycle streak-position) as entry/ordering rules, built
*intentionally simple* (reject-only / bucket-ordering, no composite
scoring), run through [[robustness_gate]] **separately**, not combined —
citing this project's own history of narrow-gate PASSes that collapsed on
full-window/train inspection (MAX_POSITIONS=5, replacement-policy
candidate).

**Preliminary: sector-neutrality diagnostic** (before building the
blacklist) — is the §XXII sector effect "avoid bad sectors" or just "the
existing ranking already concentrates in good sectors"? Compared
`top_overall` (single best rank_score/day across all sectors) vs
`top_per_sector` (best rank_score/day within each sector, so weak sectors
still get picked) on the same qualified pool, 11d horizon:
  - top_overall:    mean +0.26%, win 53.4%
  - top_per_sector: mean +0.58%, win 51.7%
  - Mann-Whitney p=0.38 — **not significant**, sample noisy either way.
  - top_overall sector concentration: Capital Goods 27%, Financial
    Services 24.6%, Auto 16% (67.6% combined); IT only 2.8%.
Read: existing top-pick selection already avoids IT-heavy days most of
the time by RS alone; an explicit blacklist wouldn't be diversifying
away from a mechanism the ranking ignores — mixed/inconclusive on
mechanism, doesn't strongly support or kill the blacklist idea on its
own. Proceeded to the isolated gate test per plan.

**Experiment A — sector blacklist.** `SECTOR_BLACKLIST` config
(`config/settings.py`), simple reject gate in `backtest/engine.py`:
buy signal in a blacklisted sector → dropped, no scoring/weighting.
Tested `SECTOR_BLACKLIST="Information Technology,Chemicals,Construction
Materials"` (the 3 durably-negative sectors from §XXII) via
`robustness_gate.py`:

    TRAIN   baseline CAGR +8.71%/Sharpe 0.55   candidate CAGR +26.99%/Sharpe 1.23
    TEST    baseline CAGR +2.33%/Sharpe 0.27   candidate CAGR +2.07%/Sharpe 0.26
    FULL    baseline CAGR +5.98%/Sharpe 0.41   candidate CAGR +17.21%/Sharpe 0.88
    Stress: crash_v_recovery / extended_bear_grind / gap_down_bleed unchanged;
            prolonged_sideways_chop improved (CAGR -14.87%→-4.92%, PF 0.76→0.96)

    VERDICT: REJECT — candidate introduces NEW train/test instability not
    present in baseline (huge train lift, ~flat/slightly worse test) —
    same overfit signature as MAX_POSITIONS=5 and the rank-replacement
    candidate. The gate's own instability check caught this
    automatically this time (earlier candidates needed manual
    full-window/train inspection to catch the same pattern).

**Experiment B — streak-priority ordering.** `STREAK_PRIORITY_ENABLED`
config, simple bucket sort in `backtest/engine.py` buy-signal ordering:
day1 → day2-3 → day4+ ahead of RS score, no continuous weighting.
Tested `STREAK_PRIORITY_ENABLED=1` via `robustness_gate.py`:

    TRAIN   baseline CAGR +18.01%/Sharpe 0.92  candidate CAGR +14.29%/Sharpe 0.76
    TEST    baseline CAGR -1.83%/Sharpe 0.04   candidate CAGR +1.98%/Sharpe 0.26
    FULL    baseline CAGR +9.26%/Sharpe 0.56   candidate CAGR +9.14%/Sharpe 0.55
    Stress: crash_v_recovery worse (CAGR 1.14%→0.42%, PF ~flat 0.53→0.52)
            extended_bear_grind unchanged
            prolonged_sideways_chop mildly worse (CAGR -14.87%→-15.70%, PF flat 0.76)
            gap_down_bleed unchanged

    Gate's mechanical VERDICT: PASS (clears TEST-window deltas + no stress
    sign-flips/PF-drops — but per known gap in [[robustness_gate]], this
    check does not look at TRAIN or FULL degradation).

    Applying the full 7-way standard (full/train/test/crash/sideways/
    bear/recovery all healthy, not just the mechanical PASS) — TRAIN
    degrades for real (CAGR -3.7pp, Sharpe 0.92→0.76), crash scenario
    roughly halves (small-base, PF flat — likely noise), sideways mildly
    worse (PF flat — likely noise). TEST is the one clear, large
    improvement: Sharpe 0.04→0.26, CAGR flips -1.83%→+1.98%. FULL flat.

    Note: this is structurally the OPPOSITE pattern from Experiment A
    (which inflated TRAIN at TEST's expense — classic overfit). Here the
    candidate gives up some TRAIN fit for a real TEST-window gain, which
    is the shape you'd want from a genuinely generalizing lever, not an
    overfit one. But it does not meet the literal "all seven stay
    healthy" bar (train regresses) — 3/7 slices are no-better-or-worse,
    NOT deployed on this run; flagged for the user as a genuine judgment
    call rather than decided unilaterally, unlike Experiment A's
    unambiguous reject.

**Baseline-instability caveat (both experiments)**: baseline TRAIN/TEST/
FULL numbers differed between the Exp A and Exp B gate invocations (Exp
A baseline TRAIN N=164, Exp B baseline TRAIN N=254) despite identical
window dates — consistent with the pre-existing, already-documented
point-in-time universe leak ([[universe_lookahead_confirmed_20260707]]):
"TODAY's static watchlist applied retroactively" means the watchlist
consulted at run time, not a historical snapshot, so re-running the
IDENTICAL baseline config on two different days is NOT guaranteed to
reproduce the same trades. This does not invalidate either verdict
(baseline vs candidate are computed in the SAME run, same watchlist
snapshot, so the within-run comparison is apples-to-apples) but means
these absolute numbers are not directly comparable across the two
experiment runs, and are not reproducible run-to-run until the dynamic-
extras side of the universe leak is fixed.

**§XXIII verdict**: Experiment A (sector blacklist) — REJECT, clear
overfit. Experiment B (streak-priority) — NOT mechanically rejected,
but does not cleanly clear the full 7-way bar either; genuine judgment
call flagged to user, not deployed pending decision.

## §XXIV. Liquidity gate isolation

Follow-up to §XIV (entry attribution suite): RS and Trend/Breakout were
each independently isolated and measured there; the ₹2Cr/day turnover
floor never was — it was bundled with the extension-cap under one
"safety" skip flag, only removed together with RS+Trend in the
`RANDOM_ALL` attribution arm, so no clean number existed for its
standalone contribution. Added `SKIP_LIQUIDITY_GATE` (`config/settings.py`,
disabled by default) to `strategy/entry.py`, isolating ONLY the turnover
check, independent of RS/Trend/Breakout/extension-cap.

Ran `robustness_gate.py --env SKIP_LIQUIDITY_GATE=1`:

    TRAIN, TEST, FULL, and all 4 stress scenarios: candidate byte-
    identical to baseline. Same trade counts everywhere (N=254/142/405).

Verified directly, not inferred from the gate output: `grep -c "Low
liquidity" outputs/backtest_decisions.csv` → **0** of 1,388 decision
rows across the full backtest history. The turnover floor has never
once rejected a candidate against the current watchlist.

**Read**: this isn't "the liquidity gate doesn't matter" — it's that the
watchlist itself is already liquid enough (every symbol on it clears
₹2Cr/day) that the floor never becomes the binding constraint. Removing
it changes nothing in this backtest because it was never doing anything
in this backtest. It may still be useful as a safety net against a
future watchlist addition that's illiquid, or against live slippage
scenarios the backtest can't see (spread, partial fills) — those aren't
things this test can speak to. No action taken; gate stays as a
dormant safety check, not a proven-valuable filter.

**§XXIV verdict**: liquidity gate isolation — confirmed non-binding
against current data, zero measurable effect either way. Distinct from
Experiment A (measurable, rejected for overfit) and Experiment B
(measurable, ambiguous) — this one simply never fires.

## §XXV. SuperTrend and ADX isolation

Follow-up to §XIV (entry attribution) and §XXIV (liquidity gate). The
"Institutional Trend Strength" block in `strategy/entry.py` bundles
three checks — EMA alignment, SuperTrend direction, ADX threshold —
under one `skip_trend` flag. §XIV's `PURE_ADX_BREAKOUT` arm tested this
whole bundle together against RS alone; it never isolated SuperTrend
from ADX from each other. Cheap check first (unlike liquidity, both
bind against real data): SuperTrend rejects 1,133/78,267 decision rows,
ADX rejects 2,329/78,267 — worth the full isolation test.

Added `SKIP_SUPERTREND_GATE` and `SKIP_ADX_GATE` (`config/settings.py`,
both disabled by default, independent of each other and of `ENTRY_MODE`).

**SuperTrend isolation** (`SKIP_SUPERTREND_GATE=1`) — clear REJECT:

    TRAIN   baseline CAGR +18.01%/Sharpe 0.92  candidate +7.00%/0.46
    TEST    baseline CAGR -1.83%/Sharpe 0.04   candidate -1.59%/0.05  (~flat)
    FULL    baseline CAGR +9.26%/Sharpe 0.56   candidate +3.59%/0.29
    Stress: prolonged_sideways_chop PF craters 0.76→0.50 (gate's own
            stress-failure rule caught this), crash_v_recovery worse
            (1.14%→0.27%), other two unchanged.

    Removing SuperTrend hurts train, full, and one stress scenario hard,
    barely touches test. Value-added — keep it.

**ADX isolation** (`SKIP_ADX_GATE=1`) — mechanical PASS, but not a clean
accept applying the full standard:

    TRAIN   baseline CAGR +18.01%/Sharpe 0.92  candidate +13.60%/0.75
    TEST    baseline CAGR -1.83%/Sharpe 0.04   candidate -3.26%/-0.05
    FULL    baseline CAGR +9.26%/Sharpe 0.56   candidate +7.20%/0.47
    Stress: prolonged_sideways_chop improved (PF 0.76→0.86), other
            three unchanged.

    Unlike Experiment B (§XXIII), this is NOT a train-vs-test trade-off
    — TRAIN, TEST, and FULL all degrade together when ADX is removed
    (test Sharpe flips positive→negative). Gate's mechanical PASS is
    the same known blind spot as before (checks TEST-window delta
    magnitude + stress sign-flips, not consistent multi-window
    degradation). One stress scenario improves, but three consistent
    same-direction losses outweigh one gain. Value-added — keep it.

**§XXV verdict**: both SuperTrend and ADX are real, load-bearing
conditions, not overcomplexity — removing either measurably hurts
performance across most windows, unlike the liquidity gate (§XXIV,
confirmed inert) and unlike Experiment A (§XXIII, confirmed overfit).
No code change; both gates stay exactly as they are.

## §XXVI. Regime-flip forced exit isolation

User question: is the regime-flip forced exit worth it, or can it be
removed? `strategy/signals.py`: when `regime=="BEAR"`, every held
position (except safe-haven) is force-sold via `exit_reason=
"MARKET_CRASH_PROTECTION"`, regardless of its own exit signal. Fires
on 68/405 (~17%) of trades in the current backtest history — real
usage, never isolated before. Added `SKIP_CRASH_PROTECTION_EXIT`
(`config/settings.py`, disabled by default), gating only this branch;
momentum-decay/trend-break/safe-haven exits stay active either way.

`robustness_gate.py --env SKIP_CRASH_PROTECTION_EXIT=1`:

    TRAIN   baseline CAGR +18.01%/Sharpe 0.92/MDD 20.50%  candidate +11.16%/0.64/MDD 23.35%
    TEST    baseline CAGR -1.83%/Sharpe 0.04/MDD 17.85%   candidate +3.95%/0.38/MDD 13.73%
    FULL    baseline CAGR +9.26%/Sharpe 0.56/MDD 20.50%   candidate +7.12%/0.46/MDD 23.35%
    Stress: crash_v_recovery / extended_bear_grind / gap_down_bleed all
            unchanged; prolonged_sideways_chop mildly worse (CAGR
            -14.87%→-16.26%, PF 0.76→0.67).

    Gate's mechanical VERDICT: PASS (TEST-window Sharpe/PF both improve
    sharply, no stress PF-drop crosses the 0.1 threshold). Same known
    blind spot as before — doesn't see TRAIN/FULL degradation.

    Applying the full 7-way standard: TRAIN and FULL both degrade for
    real (CAGR down ~2-7pp, Sharpe down, and — the metric this
    mechanism exists to protect — MDD gets WORSE in both, 20.50%→23.35%).
    TEST improves sharply (loss→gain, MDD improves too). Sideways
    mildly worse. Crash/bear/gap-down stress scenarios unchanged
    (candidate doesn't get punished harder in a synthetic bear grind —
    consistent with momentum-decay usually firing first regardless,
    same pattern noted for the earlier-removed RS-decay exit in
    `strategy/exit.py`).

    Structurally similar to Experiment B (§XXIII) — train-vs-test
    trade-off — but with one difference that matters: the metric that
    degrades in TRAIN/FULL when this exit is removed is exactly the
    metric the mechanism is named for (drawdown protection). TEST's
    2025-now window apparently didn't contain a bear regime severe
    enough to need it, so removing it only shows up there as "fewer
    premature exits" (N=142→122 test trades). TRAIN's 2022-2024 window
    did, and removing it cost real drawdown control there.

**§XXVI verdict**: NOT removed. Net full-window effect is negative
(CAGR +9.26%→+7.12%, MDD +2.85pp worse), and the metric it protects
(MDD) degrades specifically where it should if the mechanism is doing
its job. The recent-window tax is real and worth remembering (this
exit gave up ~5.8pp of test CAGR versus doing nothing), but a
crash-protection mechanism whose absence widens drawdown in the one
window that actually had a crash is doing what it says on the label —
keep it.

## §XXVII. Cleanup — proven kept, unproven removed

User's closing instruction after §XXII-§XXVI: "keep the proven one else
you can remove." Applied literally:

**Kept, no code change (already live, proven value-added this
session)**: SuperTrend direction gate, ADX threshold gate,
regime-flip forced exit (`MARKET_CRASH_PROTECTION`). All three cost
real full-window performance when removed (§XXV, §XXVI) — nothing to
do, they were already active and already correct.

**Removed (code deleted, not just left disabled)**:
- `SECTOR_BLACKLIST` (§XXIII Experiment A) — clear overfit, REJECTed.
  Deleted from `config/settings.py` and the filter block in
  `backtest/engine.py`.
- `STREAK_PRIORITY_ENABLED` (§XXIII Experiment B) — mechanical PASS but
  a genuine train-vs-test trade-off, never reached a clean accept, never
  deployed. Deleted from `config/settings.py`, the `qualify_streak`
  tracking, and the buy-sort branch in `backtest/engine.py`.
- `SKIP_LIQUIDITY_GATE`, `SKIP_SUPERTREND_GATE`, `SKIP_ADX_GATE`,
  `SKIP_CRASH_PROTECTION_EXIT` — these were diagnostic-only toggles
  built to run the four isolation tests in §XXIV-§XXVI. Their purpose
  was to answer "should this gate exist" — all four questions are now
  answered and the answer for three of them is "yes, permanently."
  Removed as dead scaffolding, not as a judgment on the gates
  themselves. The underlying checks (turnover floor, SuperTrend
  direction, ADX threshold, crash-protection exit) all remain in the
  code, now unconditional again.

**Liquidity gate exception**: the ₹2 Cr/day turnover check itself
(§XXIV) was *not* proven to add backtest value (0 rejections in
78,267 decision rows — it has never once fired). Under a strict
reading of "keep the proven one else remove" this would also be a
removal candidate. It was kept anyway: it protects against a live
operational risk a backtest cannot model (an illiquid symbol entering
the watchlist, slippage on a real fill) rather than a backtest-measurable
edge, so "unproven in backtest" isn't the same evidence standard as
"proven not to help." Flagged explicitly rather than assumed.

Verified via import smoke test + full default backtest: still exactly
405 trades post-cleanup — confirms all changes were pure code
deletion with zero behavior change to what was already running live.
