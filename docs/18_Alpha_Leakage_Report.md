# 18 — Alpha Leakage Report

**Date**: 2026-07-07
**Mission**: The permutation test (docs/16.6) proved the RS ranking contains genuine selection
information (actual +12.49% CAGR vs +2.90% mean for 40 random rank-permutations, p=0.024). The
factor regression proved that information does not survive into statistically demonstrable net
alpha (t=0.71). This document traces where it disappears. Measurements from
`scripts/alpha_leakage_audit.py` (one instrumented baseline run, 2022-01 → 2026-07) unless cited
otherwise.

## The accounting anchors

| Portfolio | CAGR | Source |
|---|---|---|
| Nifty Midcap 150 ETF (natural benchmark) | +21.05% | docs/16 |
| Equal-weight own universe, always invested | +17.20% | docs/16 |
| Strategy (real ranking) | +12.26–12.49% | audit run / docs/16 |
| Strategy wrapper with RANDOM ranking (mean of 40) | +2.90% | docs/16.6 |

Two gaps to explain: **-4.9pp vs its own universe held passively** (pure construction cost) and
**-8.8pp vs the natural benchmark** (construction + universe composition). The permutation test
brackets the selection signal's gross value at **~+9.6pp/yr** inside this wrapper. So the wrapper
costs ~14.3pp/yr (17.20 → 2.90 with no signal), and the signal buys back ~9.6pp of it.

## Where it leaks — measured, ranked largest to smallest

### Leak 1 — Stranded capital from conviction-sizing buckets (~2–4.5pp/yr) — LARGEST, and mechanical

- **Stage**: Position Sizing / Cash Allocation
- **Purpose**: size positions by signal conviction (`SCORE_BUCKETS`: score≥95→1.2x, ≥90→1.0x,
  ≥80→0.8x, ≥70→0.6x of slot cash).
- **Measured evidence**: slots are at MAX (3/3) on **90.2% of BULL days**, yet mean BULL exposure
  is only **71.6%**.
- **CORRECTION (2026-07-07, post-Gate-A)**: this section originally attributed the gap to the
  `SCORE_BUCKETS` conviction haircuts (0.6–0.8x). That was wrong: `score_to_size_factor` is
  **dead code** — imported by both `backtest/engine.py` and `portfolio/manager.py`, called by
  neither (proven when the E1 uniform-sizing gate run produced bit-identical results to
  baseline). The remaining candidate mechanisms for the measured ~27% idle capital are (a) the
  drawdown throttles applied at entry (`base_slot_cash × 0.50` beyond 10% DD, `× 0.25` beyond
  15%) whose withheld cash is never topped up, and (b) refill dynamics — `cash /
  available_slots` splits across all free slots even when fewer candidates exist that day,
  stranding the reserved share until another candidate appears. Their relative contribution is
  **not yet measured**; experiment E1 is re-scoped accordingly (docs/19).
- **Estimated alpha lost**: idle capital averages ~27% of equity earning **0%** in the backtest
  (no cash yield is modeled). Upper bound: 27% × 17.2% universe return ≈ 4.6pp/yr. Lower bound
  (idle cash at the 6.5% risk-free that live could actually capture in a liquid fund): ~1.8pp/yr.
  Timing analysis shows idle cash is NOT concentrated in bear periods (BEAR exposure 76.2% >
  BULL 71.6%), so this is not protective cash — it is stranded cash.
- **Risk added by fixing**: higher deployment → higher MDD; quantified only by experiment E1/E2.
- **Confidence**: High that the mechanism exists and its size (measured); Medium on the pp value
  recovered by fixing it (depends on when the stranded cash would have been deployed).
- **Measurable**: yes — measured. **Experiment**: E1 (uniform-sizing ablation), E2 (cash parking),
  docs/19.

### Leak 2 — Friction: charges + slippage at 8.46x turnover (~3.2pp/yr) — hard-measured

- **Stage**: Execution
- **Measured evidence**: ₹8,828 charges on ₹73,416 gross P&L (12% of gross eaten by charges
  alone) = **1.54pp/yr** of average equity; turnover 8.46x/yr × 0.1%/side slippage = **1.69pp/yr**
  embedded in execution prices. Total ≈ **3.2pp/yr**, roughly 20% of gross return.
- **Root cause is churn, not trade size**: 93 of 151 closed trades (61%) held <31 days, and that
  cohort's aggregate net P&L is **-₹75,204** (the ≥31d cohort made +₹142,496). The short-hold
  cohort generates the majority of turnover, pays the majority of friction, and loses money
  before friction. This is the whipsaw loop: enter → stop/crash-exit → re-enter.
- **Estimated alpha lost**: 3.2pp/yr direct; the whipsaw cohort's pre-friction loss is additional
  (counted under Leak 3 to avoid double-counting).
- **Confidence**: High (direct ledger measurement). Caveat: the charges model itself is unvalidated
  against live contract notes (experiment E7).
- **Measurable**: yes — measured. **Experiment**: E3 (churn-cohort/re-entry-cycle audit) to
  determine how much churn is structural vs avoidable.

### Leak 3 — Entry timing at the moment of selection (~1–2pp/yr, suggestive not proven)

- **Stage**: Candidate Selection → Entry Timing
- **Measured evidence**: the 111 selected entries had a **-1.35%** mean benchmark-adjusted forward
  21-day return, while the 1,288 qualified-but-passed-over signals had **+0.39%** (difference
  ~1.7pp over 21d; with ~9% cross-sectional dispersion this is t≈1.7 — suggestive, below
  conventional significance).
- **Interpretation (Opinion, Medium confidence)**: selection happens when a slot opens — typically
  right after an exit — and takes the highest-ranked (often most-extended) name at that moment.
  The passed-over names, observed while slots were full, sit mid-trend. This is consistent with
  the rejected extension-filter experiment (docs/05) and with the edge living in 31+ day holds:
  the signal is right over months and slightly wrong over the first three weeks after entry.
- **Estimated alpha lost**: at ~34 entries/yr × ~30% position size × ~1.7pp of 21d underperformance
  ≈ 1–2pp/yr, wide error bars.
- **Confidence**: Low-Medium — a real measured pattern with a marginal t-stat and a plausible
  mechanism. Do not act on it without experiment E4.
- **Measurable**: partially measured. **Experiment**: E4 (entry-lag ablation).

### Leak 4 — Universe composition (-3.85pp vs natural benchmark; fixed by constraint)

- **Stage**: Universe
- **Evidence**: the strategy's own equal-weight universe earned +17.20% vs Midcap150 ETF's
  +21.05% over the same period. The 100-symbol watchlist simply is not the Midcap 150; nothing
  downstream can recover return the opportunity set doesn't contain.
- **Confidence**: Medium (both series measured, but the universe carries the contamination caveat
  of docs/14 — its true historical composition is unknowable).
- **Measurable**: measured. Per mission constraints the universe is not to be changed; this leak
  is recorded so the benchmark expectation is honest: **even a perfect wrapper on this universe
  targets ~17%, not ~21%.**

### Leak 5 — Concentration variance drag (a Sharpe leak, not primarily a CAGR leak)

- **Stage**: Portfolio Construction (3 slots)
- **Evidence**: MDD -23.67% vs equal-weight's -21.44% at half the beta (docs/16); Sharpe 0.41 vs
  0.69. The N=3 book concentrates idiosyncratic risk that a 100-name equal-weight diversifies
  away. Raising N was tested and rejected — N=4..20 all degrade CAGR via the
  `cash/available_slots` dilution (docs/16), so within current mechanics this leak has no
  available fix; it is priced in.
- **Confidence**: High that the risk cost is real; High that naive slot-count fixes fail (tested).

### Non-leaks — stages measured and CLEARED

- **Exit logic / trade management**: mean benchmark-adjusted forward 21d return after all 151
  exits is **+0.13%** — exits neither abandon winners nor hold losers in aggregate.
  MARKET_CRASH_PROTECTION exits (n=31) preceded further **-1.94%** underperformance (they save
  money); TRAIL_EXIT (n=54, the workhorse) leaves +1.04% on the table — modest; only TREND_BREAK
  (n=3, +6.06%) looks bad and is too small to matter. **Exit rules are not the bottleneck and
  should not be touched.**
- **Stop-loss**: STOP_LOSS exits' forward adjusted return is -0.12% — stopped names do not
  recover vs the benchmark. Stops are not too tight (confirms prior finding).
- **RS Ranking**: proven information-bearing (permutation p=0.024). Not touched.
- **Regime gate at the exit side**: crash exits demonstrably save money (above). The regime
  gate's cost sits on the entry/idle-capital side, already counted in Leaks 1 and 3. The
  GOLDBEES share of BEAR-period return (gold windfall) remains unquantified — experiment E5.

## Pipeline summary table

| Stage | Alpha in → out (pp/yr, approx.) | Leak | Confidence | Measurable |
|---|---|---|---|---|
| Market → Universe | 21.05 → 17.20 | -3.9 (composition; fixed by constraint) | Medium | Measured |
| Universe → RS Ranking | +9.6 gross signal available | none (signal proven) | High | Measured (permutation) |
| Ranking → Candidate Selection | — | ~-1 to -2 (slot-open timing) | Low-Med | Partially; E4 |
| Selection → Sizing/Cash | — | **~-2 to -4.5 (stranded capital)** | High/Med | Measured; E1/E2 |
| Risk constraints (DD throttles) | — | included in stranded-capital figure | Medium | E1 isolates |
| Trade management / Exits | — | ≈ 0 (cleared) | High | Measured |
| Execution | — | **-3.2 (friction at 8.46x turnover)** | High | Measured; E3/E7 |
| Concentration (3 slots) | — | Sharpe/MDD cost, no fixable CAGR term | High | Tested (N-sweep) |
| **Net** | 17.20 → 12.26 | **≈ -4.9 vs own universe** | | |

The three actionable leaks (stranded capital, friction/churn, entry timing) sum to ~6–10pp/yr of
gross drag against a measured net gap of ~4.9pp — the overlap is expected (the whipsaw cohort
contributes to both friction and timing; fixing stranded capital raises friction exposure too).
They cannot simply be added; each experiment in docs/19 isolates one.

## Honest limits of this report

Single backtest window; contaminated universe (docs/14); all pp figures carry ±1–2pp error bars
at best. The *ranking* of leaks (sizing ≥ friction > timing; exits cleared) is far more robust
than any individual number, and that ranking is what docs/19's experiment order follows.
