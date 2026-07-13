# 27. Trading Strategy Research Framework

2026-07-11. A standing catalog of trading-strategy concepts, evaluated
against one litmus test the user proposed for any rule in this system:

> **Does changing this rule improve out-of-sample expectancy across
> multiple market regimes? If the answer is no, the rule probably
> doesn't deserve to stay.**

This doc is the entry point for *any* future lever proposal. Before
building a new experiment, find the concept here first — many are
already tested (cross-referenced to `docs/24_Rejected_Forever.md`,
`docs/19-23`, and memory), so the question is usually "has this been
tried," not "should I try this."

## How to use this document

Each concept below is a candidate lever, not a recommendation. A
concept only earns a place in the live strategy after:

1. It passes `scripts/robustness_gate.py` (full-window + train/test OOS
   split + the 4 stress scenarios: `crash_v_recovery`,
   `extended_bear_grind`, `gap_down_bleed`, `prolonged_sideways_chop`).
2. It doesn't flip the sign of CAGR in any stress scenario (the hard
   rule that has killed 3 otherwise-good candidates so far — see
   `docs/25` §4).
3. It's evaluated for interaction, not just in isolation — several
   concepts here are mutually exclusive with what's already live (e.g.
   mean-reversion entries conflict with the trend-following core).

**Per-concept fields:**
- **Why it should work** — the market behavior/inefficiency it claims
  to exploit.
- **Evidence** — academic or practitioner sourcing. Marked `[strong]`
  (peer-reviewed, replicated), `[practitioner]` (widely used, weaker
  formal backing), or `[folklore]` (common claim, no rigorous backing
  found — treat with extra skepticism).
- **Implementation** — how it would concretely map onto this codebase.
- **Failure modes** — the known way this class of rule breaks.
- **Tests needed** — the specific `robustness_gate.py` run(s) required
  before acceptance.
- **Status** — `PROVEN/LIVE`, `REJECTED` (cite the memory/doc), `OPEN`
  (never tested), or `UNTESTED-VARIANT` (a close relative was rejected,
  this specific formulation wasn't).
- **Interaction** — known conflicts or synergies with other live rules.

---

## A. Trend / Momentum

### A1. Cross-sectional momentum (relative strength ranking)
- **Why**: stocks that have outperformed peers over 3-12 months tend to
  keep outperforming over the next 1-3 months — under-reaction to
  information that diffuses slowly through the market.
- **Evidence**: `[strong]` Jegadeesh & Titman (1993), one of the most
  replicated results in empirical finance, holds across countries and
  decades (with a well-documented momentum-crash risk in sharp
  reversals — directly relevant to this system's `crash_v_recovery`
  weak spot).
- **Implementation**: already live — RS-first slot fill.
- **Failure modes**: momentum crashes hard in V-shaped reversals off a
  crash (exactly the mechanism suspected in `docs/25` §4).
- **Tests needed**: n/a, already validated in production.
- **Status**: `PROVEN/LIVE`. Ranking refinements on top of it are
  `REJECTED` — see `docs/24` Entry/ranking table (composite scores,
  freshness, ADX-as-sort, turnover-as-sort all null, `|rho|<0.06`).
- **Interaction**: this is the core the whole entry gate is built on;
  most rejected ranking ideas were attempts to improve *this*.

### A2. RSI threshold tuning (momentum-decay exit)
- **Why**: RSI falling below a threshold signals momentum has
  decayed enough that the trend justifying entry may be over.
- **Evidence**: `[practitioner]` Wilder (1978) introduced RSI as a
  bounded oscillator; no strong academic consensus on an optimal
  threshold — 30/50/70 are conventions, not derived constants.
- **Implementation**: `strategy/exit.py:23` `MOMENTUM_RSI_THRESHOLD`,
  env-overridable via `MOMENTUM_RSI` (currently 50). This is the
  system's literal instance of the user's litmus-test example.
- **Failure modes**: a threshold tuned to one regime (e.g. trending
  bull market) whipsaws in chop; RSI is noisy at daily granularity for
  Indian mid/small caps.
- **Tests needed**: ~~sweep `MOMENTUM_RSI`~~ **done 2026-07-11** — 7
  values (35/38/40/42/45/55/60) through `robustness_gate.py`, same
  protocol as the liquidity-threshold sweep.
- **Status**: `REJECTED` — structural two-sided local optimum. Lowering
  (35-42) improves TRAIN/TEST/`crash_v_recovery` but every value is
  killed by `gap_down_bleed` PF drop (38/40 are single-failure
  near-misses). Raising (45-60) fails via the opposite mechanism —
  TEST-window degrades, `prolonged_sideways_chop` breaks, 60 flips
  `crash_v_recovery` negative. 50 is a genuine optimum, not an
  arbitrary default. See `docs/24` Exit section for the full writeup.
  `gap_down_bleed` is now a second recurring-killer scenario, distinct
  from `crash_v_recovery` — this one specific to exit-timing levers.
- **Interaction**: comment in `strategy/exit.py:35` notes MOMENTUM_DECAY
  "always triggers first" across backtest history — tightening or
  loosening it likely dominates total exit behavior, worth prioritizing.

### A3. ADX trend-strength gate
- **Why**: filters out low-trend/choppy conditions where
  trend-following entries have poor expectancy.
- **Evidence**: `[practitioner]` Wilder (1978), the same source as RSI
  and ATR; widely used, weaker formal backing than momentum itself but
  extensively practitioner-validated as a chop filter.
- **Implementation**: live, pass/fail threshold gate (not a ranking
  key — ranking by ADX magnitude is separately `REJECTED`).
- **Failure modes**: none identified in this system's testing.
- **Tests needed**: none — already isolated and confirmed
  value-added on train+test (`supertrend_adx_isolation_20260710`).
- **Status**: `PROVEN/LIVE`.
- **Interaction**: works alongside SuperTrend; removing either degrades
  results (isolation test).

### A4. SuperTrend
- **Why**: ATR-based trailing trend-direction flip, smooths out
  single-bar noise vs. a raw MA crossover.
- **Evidence**: `[practitioner]` no primary academic source (indicator
  is a retail/practitioner invention), but this system's own isolation
  test is the actual evidence that matters here.
- **Implementation**: live, entry gate.
- **Failure modes**: none identified in this system's testing.
- **Status**: `PROVEN/LIVE` — confirmed value-added,
  `supertrend_adx_isolation_20260710`.
- **Interaction**: see A3.

### A5. Trend Alignment (EMA)
- **Why**: only take trades aligned with a longer-horizon EMA to avoid
  fighting the dominant trend.
- **Evidence**: `[practitioner]` standard trend-following construct,
  underlies most systematic trend systems (Turtle-style, Donchian, CTA
  replication studies).
- **Implementation**: live, part of the frozen entry gate.
- **Status**: `PROVEN/LIVE`, part of the baseline gate
  (`docs/25` §1). No alternative exit/entry EMA scheme has beaten it.
- **Interaction**: foundational — most other filters here layer on top
  of this, not instead of it.

### A6. Breakout entry (Donchian-style)
- **Why**: a new high over a lookback window signals a supply/demand
  imbalance resolving in the breakout direction.
- **Evidence**: `[strong→practitioner]` Donchian channel breakout is
  the basis of the (well-documented, historically profitable) Turtle
  Trading system; more recent literature shows breakout edges have
  decayed/crowded in liquid developed markets, less studied in Indian
  mid/small-cap universes specifically.
- **Implementation**: live, part of the frozen entry gate.
- **Status**: `PROVEN/LIVE`, part of baseline gate.
- **Interaction**: extension-as-sort-key (how *far* past breakout) is
  separately `REJECTED` (`docs/24`) — the pass/fail gate works, ranking
  by it doesn't.

---

## B. Mean-Reversion

### B1. Short-term oversold bounce (RSI/Stochastic mean reversion)
- **Why**: sharp short-term drops overshoot fair value and mean-revert
  within days — the opposite mechanism to momentum, operating on a
  shorter horizon.
- **Evidence**: `[strong]` well documented in US large-caps at 1-5 day
  horizons (Lehmann 1990, Lo & MacKinlay 1990 contrarian profits); much
  weaker/mixed evidence in momentum-dominated, less liquid markets —
  and this system's own entry-attribution work found RS ranking
  (momentum) clearly beats reverse-RS at the entry-selection level for
  its universe... actually the opposite: `REVERSE_RS` (worst-first) was
  the best-performing arm in `entry_attribution_suite_20260709`. That
  result is about *entry selection order*, not a standalone
  mean-reversion *system*, so it doesn't settle this concept — flagged
  as a real tension worth resolving before testing B1, not glossed over.
- **Implementation**: would require a structurally different entry
  path (buy weakness, not strength) — likely a separate sleeve/strategy
  rather than a parameter change to the existing trend-following core.
- **Failure modes**: mean-reversion entries during a genuine trend
  change (not a dip) become "catching a falling knife" — this is
  plausibly the same failure family as `crash_v_recovery`, just from
  the opposite side.
- **Tests needed**: isolated sleeve backtest before considering any
  blend; do not bolt onto the existing trend gate.
- **Status**: `OPEN`, but flagged high-risk given the REVERSE_RS finding
  above already hints something in this direction has edge — worth
  investigating *why* before designing a naive RSI-oversold system.
- **Interaction**: **structurally conflicts** with A1-A6 (trend
  filters would reject exactly the setups B1 wants to buy). Any test
  must be a separate sleeve, not a modification of the live gate.

### B2. Bollinger Band mean reversion
- **Why**: price touching/exceeding a volatility-scaled band is
  statistically extended and likely to revert toward the mean band.
- **Evidence**: `[practitioner]` Bollinger's own writing plus general
  volatility-band literature; band-touch reversion is well known to
  work poorly in trending regimes (bands "walk" the trend) — exactly
  this system's live market character.
- **Implementation**: not implemented. Would need its own entry
  path, same conflict as B1.
- **Failure modes**: false reversion signals in strong trends (bands
  walk along the trend rather than reverting) — likely a poor fit
  for a system whose entire frozen gate is trend-alignment-based.
- **Tests needed**: isolated sleeve, same caution as B1.
- **Status**: `OPEN`, low priority given the structural conflict with
  the trend-following core.
- **Interaction**: same conflict as B1.

### B3. Pairs trading / statistical arbitrage
- **Why**: two historically co-integrated stocks temporarily diverge
  and converge back; market-neutral, exploits relative not directional
  mispricing.
- **Evidence**: `[strong]` Gatev, Goetzmann & Rouwenhorst (2006), one
  of the most-cited empirical pairs-trading studies; returns have
  documented decay/crowding since the original sample period.
  Requires genuine cointegration testing, not just correlation.
- **Implementation**: entirely new subsystem — market-neutral,
  long/short, no live short-selling infrastructure exists in this
  codebase currently (equity-only long book).
- **Failure modes**: cointegration relationships break down
  structurally (M&A, index reclassification, delisting); requires
  short-selling and margin infrastructure this system doesn't have.
- **Tests needed**: infrastructure build (shorting) before any
  backtest is even possible — largest lift of any concept in this doc.
- **Status**: `OPEN`, but out of scope until/unless the platform adds
  short-selling capability.
- **Interaction**: orthogonal to the existing long-only book — could
  run as a genuinely separate capital sleeve rather than competing with
  it.

---

## C. Volatility

### C1. ATR-based position sizing
- **Why**: size positions inversely to recent volatility so each trade
  risks a comparable dollar amount regardless of the underlying's
  choppiness.
- **Evidence**: `[practitioner]` core to most CTA/trend-following risk
  frameworks (e.g. Turtle-system risk unit sizing); less about raw
  return edge, more about risk normalization.
- **Implementation**: previously tested (3x ATR variant).
- **Status**: `REJECTED` — taxes CAGR with no stress-scenario benefit
  at `MAX_POSITIONS=3` (`phase2_improvements`). Note the caveat found
  2026-07-11: any sizing lever at N=3 is structurally capped by
  `MAX_STOCK_ALLOCATION_PCT=0.34` (`e1_idle_cash_ablation_20260711`) —
  worth re-testing only if that cap is revisited first.
- **Interaction**: see the idle-cash / conviction-sizing rows in
  `docs/26` — this whole class of lever is gated by the 0.34 cap.

### C2. Volatility targeting (portfolio-level vol scaling)
- **Why**: scale overall portfolio exposure to hold realized volatility
  roughly constant over time, cutting exposure into rising volatility
  regimes rather than after a drawdown has already happened.
- **Evidence**: `[strong]` well-documented in the risk-parity/vol-target
  literature (e.g. Moreira & Muir 2017 "Volatility-Managed Portfolios"
  showing vol-scaling improves Sharpe across several factor premia).
- **Implementation**: distinct from the existing drawdown throttle
  (which reacts to realized loss, i.e. after the fact) — this would act
  on forward-looking realized/implied volatility directly, before a
  drawdown accumulates. Would need an India VIX or realized-vol proxy
  feed.
- **Failure modes**: cutting exposure ahead of vol spikes can miss the
  sharp-recovery leg — the same `crash_v_recovery` failure family,
  from a portfolio-level angle instead of a stock-selection angle.
- **Tests needed**: full `robustness_gate.py` protocol, with particular
  attention to `crash_v_recovery` given the failure-mode note above.
- **Status**: `OPEN` — genuinely untested, and structurally different
  from the DD-throttle candidate already gate-tested (E1). Worth
  distinguishing clearly: DD-throttle reacts to *drawdown already
  happened*; this reacts to *volatility rising*, which can lead a
  drawdown rather than follow it.
- **Interaction**: would likely replace or interact directly with
  `DRAWDOWN_REDUCE_SIZE_PCT`/`DD_THROTTLE_DISABLED_ENABLED` — should
  not be tested as a pure add-on to the current throttle without first
  resolving whether the throttle itself stays live (open deploy
  decision, see `e1_idle_cash_ablation_20260711`).

### C3. Regime filter (existing macro-trend gate)
- **Why**: broad market regime (BULL/BEAR via smoothed EMA-based
  detection) should gate aggressiveness — don't run a long-only
  trend system the same way in a confirmed bear tape.
- **Evidence**: `[practitioner]` standard trend-overlay concept; this
  system's own regime-signal-divergence incident
  (`regime_signal_divergence_20260702`) is the most relevant evidence —
  a backtest/live mismatch here previously produced a false CAGR
  inflation, now fixed.
- **Implementation**: live (`strategy/regime.py`), gates the crash
  protection exit and drawdown behavior.
- **Status**: `PROVEN/LIVE` per `docs/24` "What is NOT on this list."
- **Interaction**: interacts with the regime-flip forced exit
  (D-section below) and the `crash_v_recovery` weak spot generally.

### C4. India VIX / implied-vol term structure as macro filter
- **Why**: a rising or inverted vol term structure often precedes
  broad market stress, ahead of price-based regime detection.
- **Evidence**: `[practitioner]` VIX term-structure signals are widely
  used in professional vol-trading/hedging desks; equity-strategy
  overlay evidence is mixed and mostly proprietary/non-replicated.
- **Implementation**: would need an India VIX data feed (not currently
  ingested anywhere in this codebase).
- **Failure modes**: term-structure signals can whipsaw around
  earnings/event risk unrelated to the underlying equity trend.
- **Tests needed**: data availability check first; then standard
  robustness-gate protocol.
- **Status**: `OPEN`, blocked on data ingestion, not yet worth
  prioritizing given cheaper open items (A2) exist.
- **Interaction**: overlaps conceptually with C3 (regime filter) —
  would need to show it adds information beyond the existing
  EMA-based regime signal, not just a redundant confirmation.

---

## D. Volume / Liquidity

### D1. Volume confirmation on breakout
- **Why**: a breakout on high relative volume is more likely genuine
  (real demand) than a low-volume breakout (easily faded).
- **Evidence**: `[practitioner]` classic technical-analysis heuristic
  (volume "confirms" price); weak formal academic backing as a
  standalone factor.
- **Implementation**: would be a new entry-gate condition (volume
  ratio vs. average, at breakout bar).
- **Failure modes**: this is adjacent to "institutional volume surge,"
  already tested as an entry *ranking* factor and found null
  (`signal_mechanism_analysis_20260710`, `|rho|<0.06`). A pass/fail
  *gate* formulation (not a ranking key) has not specifically been
  isolated — same distinction as ADX (ranking rejected, gate kept).
- **Tests needed**: isolate as a gate, not a rank — direct analogy to
  how ADX/turnover were correctly separated in `docs/24`.
- **Status**: `UNTESTED-VARIANT` — the ranking form is rejected, the
  gate form is not the same claim and hasn't been tested.
- **Interaction**: would stack with the existing breakout gate (A6).

### D2. Institutional volume surge (as a ranking factor)
- **Status**: `REJECTED` — null, `signal_mechanism_analysis_20260710`.
  Listed here only for completeness/cross-reference; do not re-test
  the ranking form. See D1 for the untested gate variant.

### D3. Liquidity / turnover floor
- **Why**: avoid thin-liquidity names where slippage/impact would
  erode any modeled edge, even if the backtest can't see the impact
  cost directly.
- **Evidence**: `[practitioner]` standard institutional practice;
  not really a backtest-testable "edge," more a live-execution safety
  constraint.
- **Implementation**: live, `MIN_DAILY_TURNOVER`, currently dormant
  (never fires at the current ₹2 Cr/day floor).
- **Status**: `REJECTED` for *tightening* (tested at p25 and p10,
  structural `crash_v_recovery` failure both times — the
  crash-recovery winner is disproportionately a thin-liquidity name).
  Kept as-is as a live safety net, not a proven backtest edge.
- **Interaction**: this is the clearest documented instance of the
  `crash_v_recovery` failure family — see `docs/25` §4.

---

## E. Portfolio Construction / Risk

### E1. Kelly criterion position sizing
- **Why**: size each bet proportional to edge/odds to maximize
  long-run geometric growth rather than a flat fraction.
- **Evidence**: `[strong]` Kelly (1956); practitioners near-universally
  use "fractional Kelly" (e.g. half-Kelly) because full Kelly is far
  too volatile for real capital and highly sensitive to edge
  mis-estimation — a mis-estimated win-rate/payoff ratio produces
  wildly wrong sizing.
- **Implementation**: would require a reliable, stable estimate of
  this system's true edge (win rate × avg win / avg loss) — the
  trade-attribution work already shows entry-time signals can't
  distinguish winners from losers (`trade_attribution_engine_20260701`),
  which is a bad sign for estimating a *stable* Kelly fraction with any
  confidence.
- **Failure modes**: edge mis-estimation → over-betting → catastrophic
  drawdown; this is the single most dangerous concept in this entire
  document if implemented carelessly.
- **Tests needed**: would need a robust, out-of-sample-stable edge
  estimate *before* even attempting sizing — likely blocked on the
  same open question as conviction-sizing (dead code, `docs/26`).
- **Status**: `OPEN`, but flagged as low-priority/high-risk given the
  edge-estimation prerequisite isn't met.
- **Interaction**: directly supersedes/conflicts with the dead
  `score_to_size_factor`/`SCORE_BUCKETS` code (`docs/26`) — don't
  build both; resolve conviction-sizing redesign first, Kelly would be
  one candidate formulation of it, not an addition on top.

### E2. Correlation-aware position sizing
- **Status**: `REJECTED` — no headroom to help, avg pairwise
  correlation only ~0.21 (`phase2_improvements`). Listed for
  cross-reference.

### E3. Sector caps / diversification limits
- **Why**: prevent correlated pile-up in one sector amplifying
  drawdowns.
- **Evidence**: `[strong]` standard portfolio-theory risk-reduction
  argument (diversification across imperfectly correlated assets).
- **Status**: `PROVEN/LIVE` as a *risk control*, not a *return* lever —
  sector labels track real co-movement (p<0.0001) but entry-time
  correlation doesn't significantly predict trade outcome (rho=-0.101,
  p=0.218). Sector *blacklist* (excluding whole sectors) is separately
  `REJECTED` as overfit.
- **Interaction**: distinct from E2 (position-level correlation
  sizing, rejected) and from the sector-durability entry-filter idea
  (F-section) which is a different, still-open formulation.

### E4. Drawdown-based position-size throttle
- **Why**: cut new-position size during a live drawdown to slow
  capital deployment into a deteriorating environment.
- **Evidence**: `[practitioner]` common risk-management heuristic;
  this system's own E1 ablation is the actual relevant evidence.
- **Status**: **superseded 2026-07-11** — removing it entirely
  (`DD_THROTTLE_DISABLED_ENABLED`) passed the full robustness gate
  (CAGR/Sharpe/MDD improve on TRAIN/TEST/FULL, `crash_v_recovery`
  byte-identical, only `prolonged_sideways_chop` degrades without sign
  flip). Currently a **pending live deploy decision**, not yet
  resolved as of this doc — see `e1_idle_cash_ablation_20260711`.
- **Interaction**: interacts with C2 (volatility targeting, untested) —
  don't test C2 as an add-on until E4's deploy status is settled.

### E5. Conviction / tiered position sizing
- **Why**: intended design — size bigger on higher-score candidates.
- **Status**: was confirmed **dead code** (`score_to_size_factor` /
  `SCORE_BUCKETS` imported, never called; forcing uniform sizing was
  bit-identical to baseline). **Resolved 2026-07-11**: deleted from
  `strategy/scoring.py` (engineering cleanup, zero live behavior
  change, not a research call). Building real conviction-tiered sizing
  from scratch is now a fresh `OPEN` Track-B research item, not a
  "finish the design" task — treat any future attempt as a new
  proposal subject to the same gate as everything else here, not a
  bug fix.
- **Interaction**: see E1 (Kelly) — any future conviction-sizing
  attempt should consider Kelly-style sizing as one candidate
  formulation, and should expect to hit the same
  `MAX_STOCK_ALLOCATION_PCT=0.34` structural cap that neutered the E1
  denominator-fix.

### E6. Slot count / capacity (`MAX_OPEN_POSITIONS`)
- **Why**: fewer, larger positions could mean more conviction/less
  dilution; more slots capture more of the qualified-signal pool that
  currently goes unbought (74.5% of qualified signals are never bought
  due to `NO_SLOT_AVAILABLE`, `docs/25` §2).
- **Evidence**: this system's own N-sweep and capacity-followup work.
- **Status**: `OPEN` at N=5 specifically — promising diagnostic number
  (CAGR +8.89% vs +5.62% at N=3,
  `rotation_capacity_followup_20260709`) but **never run through
  `robustness_gate.py`**. N=4 was gated and `REJECTED` pre-fidelity-fix
  (needs re-test post-`df9856f`). This is the single most-repeated
  "next step" recommendation across `docs/25` and `docs/26` — still
  not executed as of this doc.
- **Interaction**: interacts with `MAX_STOCK_ALLOCATION_PCT=0.34`
  (sized for N=3) — increasing N likely requires revisiting that cap
  too, not just the slot count in isolation.

---

## F. Regime / Macro / Cross-Sectional

### F1. Sector durability as an entry filter
- **Why**: some sectors show persistently stronger/longer-duration
  trends than others — a real, non-binary version of "sector matters"
  distinct from the rejected blacklist approach.
- **Evidence**: this system's own signal-lifecycle work — real effect,
  t up to +7.6, Bonferroni-safe.
- **Status**: `OPEN` — the effect is real but only tested as a blunt
  binary exclusion (rejected, overfit). A continuous/soft weighting
  formulation has never been tried.
- **Interaction**: must be tested as a genuinely different mechanism
  from E3/the rejected blacklist, not a re-run of the same idea with a
  softer threshold (`docs/24` "How to apply").

### F2. Regime-gated conditional overrides (crash_v_recovery-specific)
- **Why**: instead of a filter/preference applying unconditionally
  across all regimes, suspend it specifically during a detected
  sharp-crash-then-V-recovery window — since 3 independent, otherwise
  good levers were killed by exactly this scenario.
- **Evidence**: this system's own recurring-failure pattern
  (`crash_v_recovery_recurring_killer_20260710`) — the most
  well-evidenced *specific* open idea in this whole document.
- **Status**: `OPEN`, explicitly recommended as priority #3 in
  `docs/25` (after MAX_POSITIONS re-test and root-cause investigation
  of *which* symbols drive the failure).
- **Interaction**: candidate resurrection path for streak-position
  preference and the liquidity-tightening idea, both otherwise
  permanently rejected — this is the one legitimate way to revisit
  them.

### F3. Seasonality / calendar effects
- **Why**: certain months/periods show systematically different
  average returns (e.g. "Sell in May," pre-holiday drift, month-end
  flows).
- **Evidence**: `[strong→practitioner]` Bouman & Jacobsen (2002)
  documented the Sell-in-May effect across 36 countries including
  India; effect size has been debated/shown to weaken post-publication
  (a common pattern for published seasonal anomalies — publication
  itself can erode the edge).
- **Implementation**: would be a simple calendar-based exposure
  overlay (e.g. reduce new entries May-Oct).
- **Failure modes**: seasonal effects are notorious for being
  data-mined artifacts that don't replicate out-of-sample; the
  post-publication decay noted above is a real risk here specifically.
- **Tests needed**: full OOS split is *mandatory* here more than
  anywhere else in this doc, given the effect's known fragility.
- **Status**: `OPEN`, never tested on this system's data.
- **Interaction**: orthogonal to the entry gate — would act as a
  portfolio-level exposure dial, similar in spirit to C2/E4.

### F4. Sector rotation / sector momentum
- **Why**: capital rotates between sectors in cycles; overweighting
  currently-leading sectors could add to the existing stock-level
  momentum edge.
- **Evidence**: `[strong]` sector/industry momentum is a documented,
  independent slice of the broader momentum literature (e.g.
  Moskowitz & Grinblatt 1999).
- **Implementation**: would need a sector-level RS ranking layered on
  top of stock-level RS ranking.
- **Failure modes**: this looks adjacent to "sector leadership" and
  "sector tailwind," both already tested as ranking factors and found
  null (`signal_mechanism_analysis_20260710`). Needs a clear argument
  for why a sector-momentum *overlay* differs from those rejected
  single-factor tests before re-attempting.
- **Status**: `UNTESTED-VARIANT` at best — very likely re-litigates a
  closed question (`docs/24` "How to apply") unless a materially
  different formulation is identified first.
- **Interaction**: see F1 (sector durability) — these two ideas should
  be reconciled/merged rather than tested as two separate levers, since
  both are "sector matters, but how" questions.

---

## G. Statistical Rigor / Meta-Methodology

### G1. Walk-forward optimization / purged cross-validation
- **Why**: standard train/test splits leak information when features
  have overlapping lookback windows across the split boundary;
  purging/embargoing removes this leak for time-series data
  specifically.
- **Evidence**: `[strong]` López de Prado, *Advances in Financial
  Machine Learning* (2018) — purged k-fold CV and the embargo technique
  are now close to a standard for backtest validation in
  quant-finance ML contexts.
- **Implementation**: this system's TRAIN/TEST split
  (2022-01-01→2024-12-31 / 2025-01-01→2026-07-11) is a simple
  chronological split, not purged/embargoed — any indicator with a
  lookback window spanning the boundary (e.g. a 100-day EMA computed
  near 2024-12-31) technically leaks a few days of information across
  the split.
- **Failure modes**: without this, OOS numbers are *slightly*
  optimistic — probably a small effect at this lookback scale, but
  unverified.
- **Tests needed**: re-run the standard TRAIN/TEST split with an
  embargo period (e.g. drop the last `N` days of TRAIN equal to the
  longest indicator lookback) and compare — cheap, diagnostic-only.
- **Status**: `OPEN`, never checked. Methodology gap, not a strategy
  lever — but relevant to *trusting* every other result in this
  document and the codebase's existing gate.
- **Interaction**: applies to every gated result in this whole
  framework, not one specific lever — a meta-fix, not a strategy
  change.

### G2. Bootstrap / permutation significance testing for expectancy
- **Why**: a single backtest CAGR/Sharpe number has sampling
  variance; without a confidence interval or null-distribution
  comparison, it's unclear whether a gate "pass" reflects real edge or
  noise, especially for smaller trade-count subsamples (e.g. stress
  scenarios with few trades).
- **Evidence**: `[strong]` standard technique in quantitative
  performance evaluation (block bootstrap for time-series returns,
  given autocorrelation).
- **Implementation**: block-bootstrap the trade ledger's daily returns
  to build a confidence interval around each headline metric, instead
  of reporting a single point estimate.
- **Failure modes**: naive (non-block) bootstrapping on autocorrelated
  return series understates the true variance — must use a block
  bootstrap, not i.i.d. resampling.
- **Tests needed**: build once as a `robustness_gate.py` add-on, apply
  retroactively to a few already-gated candidates (e.g. E4/DD-throttle
  removal) to see whether the "PASS" margin is wide or narrow.
- **Status**: `OPEN` — this system has never quoted a confidence
  interval, only point estimates, for any gate result to date.
- **Interaction**: strengthens (doesn't replace) every other gate
  result already produced — highest-leverage meta-item in this section
  since it applies retroactively.

### G3. Multiple-testing correction (Bonferroni / FDR)
- **Why**: testing many factors/thresholds against one dataset
  inflates the false-positive rate; a correction is needed to avoid
  mistaking noise for signal when dozens of candidates are screened.
- **Evidence**: `[strong]` standard statistical practice; already
  applied once in this codebase's own research
  (`signal_lifecycle_archetype_20260710` explicitly reports
  Bonferroni-safe results).
- **Implementation**: already used ad hoc in one analysis; not a
  standing policy applied to every multi-candidate sweep (e.g. the
  liquidity-threshold p25/p10 sweep, RSI-threshold sweep from A2 if
  run, etc.).
- **Status**: `PROVEN/APPLIED` in isolated cases; `OPEN` as a
  *standing policy* for all future multi-threshold sweeps.
- **Interaction**: should be applied any time A2's RSI sweep or a
  similar multi-value sweep is actually run.

### G4. Overfitting guardrail via out-of-sample + stress-scenario gate
- **Why**: a candidate that wins on the full window but fails OOS or
  flips sign under a stress scenario is very likely fit to noise in
  that specific window, not a real edge.
- **Evidence**: `[strong]` general backtesting best practice; this
  system's own track record is the evidence — this exact gate has
  correctly killed 3+ candidates (extension filter, liquidity
  tightening, streak-position preference) that looked clean on a
  naive full-window test.
- **Implementation**: live — `scripts/robustness_gate.py`.
- **Status**: `PROVEN/LIVE`, the meta-methodology underlying every
  verdict in this entire document.
- **Interaction**: every concept above is evaluated through this gate;
  it is the connective tissue of this whole framework, not a peer
  concept to the others.

---

## Summary table

| # | Concept | Status | Priority if OPEN |
|---|---|---|---|
| A1 | Cross-sectional momentum (RS ranking) | PROVEN/LIVE | — |
| A2 | RSI threshold tuning | REJECTED (structural, two-sided optimum) | — |
| A3 | ADX gate | PROVEN/LIVE | — |
| A4 | SuperTrend | PROVEN/LIVE | — |
| A5 | Trend Alignment (EMA) | PROVEN/LIVE | — |
| A6 | Breakout entry | PROVEN/LIVE | — |
| B1 | Oversold-bounce mean reversion | OPEN, high-risk | Low (structural conflict) |
| B2 | Bollinger mean reversion | OPEN, low-priority | Low |
| B3 | Pairs trading | OPEN, out of scope | Blocked on infra |
| C1 | ATR position sizing | REJECTED | — |
| C2 | Volatility targeting (portfolio) | OPEN | Medium |
| C3 | Regime filter | PROVEN/LIVE | — |
| C4 | India VIX macro filter | OPEN, blocked | Low (data gap) |
| D1 | Volume confirmation (gate form) | UNTESTED-VARIANT | Medium |
| D2 | Institutional volume (rank form) | REJECTED | — |
| D3 | Liquidity floor | PROVEN/LIVE (safety net) | — |
| E1 | Kelly sizing | OPEN, high-risk | Low until edge is stable |
| E2 | Correlation sizing | REJECTED | — |
| E3 | Sector caps | PROVEN/LIVE (risk, not alpha) | — |
| E4 | DD-throttle | **PASSED gate, pending deploy** | **Decision needed now** |
| E5 | Conviction sizing | Dead code **deleted 2026-07-11** | — (any rebuild is fresh research) |
| E6 | Slot count (N=5) | **OPEN, gate-ready** | **High — most-repeated recommendation** |
| F1 | Sector durability filter | OPEN | Medium |
| F2 | Regime-gated crash_v_recovery override | OPEN | **High — explains 3 rejections** |
| F3 | Seasonality | OPEN | Low-medium |
| F4 | Sector rotation/momentum | UNTESTED-VARIANT | Low (likely re-litigates null) |
| G1 | Purged/embargoed CV | OPEN | Medium (trust check on everything else) |
| G2 | Bootstrap significance | OPEN | Medium-high (cheap, retroactive) |
| G3 | Multiple-testing correction | Applied ad hoc | Standing policy going forward |
| G4 | OOS + stress gate | PROVEN/LIVE | — (this is the meta-tool) |

## Bottom line / how to apply

**Update 2026-07-11**: A2 (RSI threshold sweep) is now closed —
REJECTED, structural two-sided optimum, see `docs/24` Exit section.
Conviction-sizing dead code (E5) deleted. Per the user's Stage-1/Stage-2
roadmap, remaining items rank as:

1. **E6 (MAX_POSITIONS=5)** — the single most-repeated "next step"
   across three prior docs, has a promising diagnostic number, never
   gate-tested. Blocks "clean baseline" for Stage 1.
2. **Churn-cohort audit (E3, friction row in `docs/26`)** — top research
   priority once Stage 1 engineering closes: is the 61%-of-trades
   <31-day net-negative cohort concentrated in a few names (fixable) or
   spread structurally across all short holds (a fixed cost)?
3. **F2 (regime-gated crash_v_recovery override)** — directly targets
   the one mechanism that has killed three unrelated candidates; every
   other open item in section B/C/F is secondary to this until it's
   understood. Comes after E3/E4 per the roadmap, not before.

Everything marked `REJECTED` stays rejected without a materially new
formulation (per `docs/24`'s existing rule) — this doc doesn't reopen
those, it exists to stop them from being re-proposed blind, and to give
every *new* idea a documented home with the same litmus test applied
consistently.
