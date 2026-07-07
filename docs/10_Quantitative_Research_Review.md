# 10 — Quantitative Research Review (Phase 1)

**Scope**: This document answers *why the strategy behaves the way it does*, using only evidence
already produced by this project — the trade attribution engine, feature-importance and
correlation scripts, out-of-sample validation, stress tests, and prior research history. It does
**not** recommend new indicators, architecture, or fixes, except the five research directions in
the final section, which are investigations to run, not implementations to build. P0/P1/P2 triage
and the improvement roadmap are deliberately deferred to Phase 2 and Phase 3.

**Evidence base**: `outputs/trade_attribution.csv` (156 trades, fresh backtest 2022-01-01 to
2026-07-06, post regime-signal-fix), `scripts/feature_importance.py`, `scripts/correlation_analysis.py`,
`scripts/early_heat_experiment.py`, `scripts/robustness_gate.py`, out-of-sample validation results,
and the rejected-experiment history in `docs/05_Research.md` / memory `phase2_improvements.md`.

Honest baseline for reference: **CAGR +12.85%, Sharpe 0.83, MDD 23.67%**, which fails the system's
own validation gates (see `06_Validation.md`).

---

## 1. Why does the strategy actually make money?

Not from a high hit rate. Across 156 trades the overall win rate is **40.4%**, and total net P&L
is **+₹68,255**. The return is a classic trend-following payoff structure: a small number of large
winners fund the account, and the majority of trades are a cost of finding them.

- **Trade concentration**: the top 5 trades (3.2% of all trades) account for **₹61,696 — 90.4% of
  total net P&L**. The top 10 trades (6.4% of all trades) account for **₹102,342 — 149.9% of total
  net P&L**, meaning the other 146 trades (93.6% of the book) are net **-₹34,088** collectively.
  All 10 top trades belong to the `LONG_WINNER` cohort, average hold 69.5 days: IRCON, RVNL, TRENT,
  MFSL, SIEMENS, KAYNES, OFSS, GOLDBEES, ASHOKLEY, POLYCAB.
- **Holding-period breakdown** confirms the same structure. Only two buckets are net profitable:
  `31-60d` (44 trades, 63.6% WR, **+₹87,513**) and `60d+` (15 trades, 86.7% WR, **+₹61,256**) — a
  combined **+₹148,770** against a total of ₹68,255. Every shorter bucket is a net loser: `16-30d`
  (31 trades, 29.0% WR, -₹5,225), `6-15d` (34 trades, 17.6% WR, -₹45,598), `0-5d` (32 trades, 21.9%
  WR, -₹29,692).
- **Exit-mechanism attribution** points at the same cause: `TRAIL_EXIT` (55 trades, 50.9% WR,
  **+₹79,742**) and `PROFIT_TARGET` (2 trades, **+₹30,510**) are the two largest profit sources.
  Both are outcomes of the "let the winner run" trailing-stop mechanic, not of entry precision.
- **Entry-trigger attribution**: `STRENGTH_CONFIRMED_BUY` (121 of 156 trades) generated
  **+₹67,342 — 98.7% of total net P&L** on its own. It is, for practical purposes, the strategy's
  only load-bearing entry path.

**Conclusion**: the edge is not "the entry signal is accurate." It is "the exit mechanism captures
outsized gains on the rare trades that develop into multi-week/month trends, and this outweighs a
sub-40% hit rate on everything else."

## 2. Where does it lose money?

- **`STOP_LOSS` exits**: 22 trades, 0% WR (tautological by definition), **-₹53,012** — the single
  largest loss category by exit type.
- **`QUICK_LOSER` cohort**: 53 trades (34% of the book), 0% WR by definition, average hold 7.1
  days, **-₹89,610** — the dominant loss engine in the account. These are entries that never
  develop and get cut early.
- **Short holding buckets combined**: `0-5d` + `6-15d` = **-₹75,290**, more than double the
  `16-30d` bucket's loss (-₹5,225) and the main reason short-hold trades are a persistent drag.
- **`BEAR_SWING_BUY` entry trigger**: 20 trades, 40% WR, net **-₹3,600** — the only entry trigger
  type with negative full-window P&L (n is small; see §5, §8).
- **`GOLDBEES_MAX_LOSS`**: 1 trade, -₹6,185 — an isolated event, not a pattern.
- **`TREND_BREAK`**: 3 trades, 0% WR, -₹3,508 — small n, minor.

## 3. What are the true sources of alpha?

Not entry-timing accuracy: the core trigger's win rate (38.8%) is not far above what a noisy
momentum signal would produce by chance. Not diversified small edges across many trades: realized
P&L is carried by roughly 10 outsized winners (§1), the signature of a fat-tailed, trend-following
return distribution rather than a high-frequency statistical edge.

The two identifiable, evidenced sources of alpha are:

1. **The trailing-stop "let winners run" mechanic.** `TRAIL_EXIT` + `PROFIT_TARGET` alone total
   **+₹110,252** — more than the entire realized total, consistent with the concentration finding
   in §1 that everything else nets negative. The mechanism, not the signal, is what converts a
   sub-40% hit rate into a positive-expectancy system.
2. **A modest, statistically real overextension effect.** `scripts/feature_importance.py` (fresh
   run, Spearman rank correlation vs. `pnl_pct`, chosen over Pearson for the fat-tailed outcome
   distribution and over RF importance to avoid overfitting at n≈155) found `macd_hist_at_entry`
   and the `ema50/100/150_dist_pct_at_entry` features negatively and significantly correlated with
   trade outcome (p<0.05): entries taken closer to trend (less "overheated") perform better. RS
   rank, ADX, RSI, and volume ratio at entry showed no significant relationship.

Sector clustering is a real, separately-evidenced structural property of the portfolio (see §6)
but is a risk-control finding, not a source of return.

## 4. Which assumptions have now been proven?

- **Sector co-movement is real.** `scripts/correlation_analysis.py`: same-sector concurrent-holding
  pairs (n=584) have mean pairwise correlation 0.313 vs. 0.225 for different-sector pairs (n=4,567),
  Welch t=19.89, **p=0.0000**. This validates sector-exposure caps as a genuine risk control, not a
  decorative one.
- **Overextension-at-entry is associated with worse outcomes** (§3), p<0.05, feature_importance.py.
  This validates the *general intuition* behind extension-filter attempts, though not the specific
  hard-threshold implementation previously tested (see §5).
- **Trade quality is visible early, not only in hindsight.** Mean day-by-day return by cohort
  diverges from day 1 and widens monotonically through day 10:

  | Cohort | Day 1 | Day 3 | Day 5 | Day 10 |
  |---|---|---|---|---|
  | LONG_WINNER | +1.50% | +2.87% | +3.72% | +6.23% |
  | OTHER | +0.57% | +1.06% | +1.89% | +2.32% |
  | QUICK_LOSER | -1.81% | -2.91% | -3.54% | -5.02% |

  The three cohorts are already separated on day 1 and never cross. This is descriptive (cohorts
  are defined post-hoc by outcome, not known at entry) but establishes that the *information*
  needed to distinguish these paths exists early in the trade's life (see §9, §10).
- **The core entry trigger is the strategy's only proven contributor.** `STRENGTH_CONFIRMED_BUY`
  carries 98.7% of total P&L (§1); the other two live entry paths are not proven contributors
  (`SAFE_HAVEN` mildly positive, `BEAR_SWING_BUY` negative — see §5).

## 5. Which assumptions have been disproven?

- **Portfolio correlation at entry predicts trade outcome — not supported.**
  `correlation_analysis.py`: Spearman rho=-0.101 between entry-time correlation and outcome,
  **p=0.2185**. This corroborates the already-recorded finding (`phase2_improvements.md`) that
  correlation-aware position sizing had no headroom to help — the null result here is the same
  null result that killed that lever.
- **`BEAR_SWING_BUY` as a value-adding defensive sleeve — not supported over this sample.** Net
  **-₹3,600** across 20 trades, 40% WR (§2). n=20 is small enough that this should be read as "not
  currently evidenced," not "definitively negative" (see §8).
- **Individual "confirmed loser" symbol judgments are statistically fragile at the sample sizes
  actually used.** Cross-checking the 39-symbol block-list (`config/universe_removed.py`) against
  this fresh full-window backtest: 37 of 39 generated zero trades (already excluded from the live
  DB's tradeable universe, so not testable here). The 2 that had leaked back into `core` status
  diverged from their documented removal evidence:
  - `THERMAX.NS`: documented as "6 trades, 17% WR, -₹4,281." Fresh run: 2 trades, 50% WR, -₹1,609
    — same direction, much smaller sample and magnitude.
  - `LAURUSLABS.NS`: documented as "4 trades, 25% WR, -₹4,966." Fresh run: 3 *different* trades,
    net **+₹2,427** — opposite sign.

  This is an n=2 anecdote, not a general claim that the block-list is wrong. But it directly shows
  that symbol-level exclusion decisions made on 1-6 historical trades do not reliably reproduce,
  which bears on how much confidence the broader universe-pruning evidence base deserves (§8, §10).
- **Binary regime-driven liquidation as a clear loss source — not supported by trade-level P&L.**
  `MARKET_CRASH_PROTECTION` exits: 31 trades, 38.7% WR, net **+₹5,502** (positive, not negative).
  Prior reasoning (from code-structure inspection, documented in `08_Project_Memory.md` /
  `09_Open_Questions.md`) treated the binary full-book liquidation mechanism as a plausible primary
  driver of OOS instability and stress-test failures. The realized-P&L evidence does not support
  "this exit type loses money" — it is mildly profitable on average. It may still carry a
  whipsaw/variance cost not visible in average net P&L (transaction costs, gap risk, missed
  re-entry opportunity cost), but that is a distinct, currently untested claim (§10), not the same
  as the exit type itself being a loss source.

## 6. Which parts of the strategy are correlated with each other?

- **The four EMA-distance features are one factor, not four.** Spearman intercorrelation on entry
  features:

  |  | ema20 | ema50 | ema100 | ema150 |
  |---|---|---|---|---|
  | ema20 | 1.00 | 0.83 | 0.59 | 0.41 |
  | ema50 | 0.83 | 1.00 | 0.85 | 0.64 |
  | ema100 | 0.59 | 0.85 | 1.00 | 0.93 |
  | ema150 | 0.41 | 0.64 | 0.93 | 1.00 |

  `ema100` and `ema150` correlate at 0.93 — essentially redundant measurements. This is consistent
  with, and quantifies, the previously-documented finding that the codebase's `"ema_50"` label
  actually computes an EMA(100) (`09_Open_Questions.md` item 3): two nominally different lookbacks
  are producing near-identical signals.
- **Sector membership is a real, independent source of correlated risk** (§4): same-sector pairs
  correlate meaningfully higher than cross-sector pairs (p=0.0000), and this is a *separate* axis
  from the EMA/trend-extension factor family above.

## 7. Which parts are genuinely independent?

- **RS rank, ADX, RSI, and volume ratio at entry** showed no statistically significant correlation
  with `pnl_pct` (feature_importance.py). This does not prove they carry independent *predictive*
  signal — only that they are not shown to be redundant with the EMA-extension family, and not
  shown to add value either. Absence of evidence, not evidence of a distinct real edge.
- **Portfolio-level concurrent-holdings correlation is a separate concern from single-trade
  quality.** Sector clustering is real and significant (§4, §6), yet correlation at entry does not
  predict individual trade outcome (p=0.2185, §5). These are genuinely two different problems: one
  about correlated drawdown risk across the book, the other about single-trade selection quality.
  Evidence for one does not transfer to the other.

## 8. Which findings are statistically supported versus observational?

**Statistically supported** (formal test, explicit p-value, from a dedicated script):

| Finding | Test | Result |
|---|---|---|
| Sector co-movement is real | Welch t-test, correlation_analysis.py | t=19.89, p=0.0000 |
| Overextension-at-entry hurts outcome | Spearman, feature_importance.py | p<0.05 |
| Entry-time portfolio correlation does not predict outcome | Spearman, correlation_analysis.py | rho=-0.101, p=0.2185 |
| Realized volatility scales weakly with holdings correlation | Spearman, correlation_analysis.py | rho=0.075, p=0.0252 (marginal) |
| OOS instability (train vs. test win-rate swing) | out-of-sample validator | 19.3pp swing, documented |

**Observational** (a pattern visible in the trade-level data, no significance/null-hypothesis test
run against it yet):

- Top-5/top-10 trade concentration (90.4% / 149.9% of total P&L) — descriptive; no bootstrap or
  permutation test has established how likely this level of concentration is under the strategy's
  assumed edge versus being consistent with a large luck component.
- Cohort-level day1–10 trajectory separation (§4) — descriptive; `early_heat_experiment.py`
  computed a full-window counterfactual P&L estimate (a rule cutting trades on weak day-5 returns)
  but this has **not** been run through `robustness_gate.py`'s OOS + 4-stress-scenario protocol,
  unlike every other lever in the rejected-experiment history.
- `BEAR_SWING_BUY` net-negative P&L (§5) — n=20 is too small for a meaningful significance test;
  flagged as low-confidence observational, not a proven negative.
- The 2-symbol block-list reproducibility check (§5) — explicitly an n=2 anecdote, not a
  statistical claim about the block-list as a whole.

## 9. What is the most likely explanation for the current 12.85% CAGR ceiling?

Three evidenced factors, together, are the most likely explanation:

1. **The realized return is concentrated in a handful of episodic, large trend moves** (§1: 10
   trades = 149.9% of total P&L, all `LONG_WINNER`/31d+ holds). Because so much of the return
   depends on whether a given window happens to contain one of these episodes (e.g., the 2022-23
   PSU/railway/defense re-rating that produced IRCON/RVNL, or individual breakouts like
   TRENT/SIEMENS/KAYNES), the realized CAGR is highly sensitive to *which* trend episodes fall
   inside a given backtest or live window — directly consistent with the already-documented 19.3pp
   OOS win-rate swing between train and test splits.
2. **Every past parameter retune that improved the full-window number failed stress tests**
   (`05_Research.md` rejected-experiment history). This is explained by (1): tightening or loosening
   entry/exit thresholds changes which of the small number of pivotal trades get captured or missed
   — it looks like tuning a stable statistical edge but is actually reshuffling exposure to a
   handful of outcome-dominant trades, which is why stress scenarios (which perturb exactly the
   conditions around those trades) break the "improvement."
3. **The 93% of trades outside the top 10 are a persistent, largely unavoidable drag** (§1, §2:
   -₹34,088 net from 146 trades; -₹75,290 from 0-15 day stop-outs alone). The core entry trigger's
   38.8% win rate means every dollar of alpha from the rare big winners is partially offset by the
   cost of taking trades that don't develop — a cost the strategy currently has no evidenced way to
   reduce (§10).

Together: a small-N, high-variance source of return, sitting on top of a costly, currently
unavoidable false-positive rate, using an entry-side factor family with confirmed internal
redundancy (§6) rather than diversified signal sources. All three are evidenced above, not new
inferences introduced in this section.

## 10. Which hypotheses remain untested?

- **The early-heat / day-5 counterfactual cut rule.** `early_heat_experiment.py` estimated a
  full-window net P&L improvement (roughly ₹6,000-8,200) from cutting trades showing sharply
  negative day-5 returns, but this has only been tested as a full-window counterfactual — it has
  **not** been run through `robustness_gate.py`'s OOS + 4-stress-scenario protocol, the bar every
  other candidate lever in the rejected-experiment history has had to clear. This is the most
  directly evidenced untested hypothesis, because §4's day1-10 cohort divergence gives it a
  plausible mechanism, not just a backtested coincidence.
- **VCP entry and stage-based exits** — listed open in `phase2_improvements.md`, never attempted.
- **Multi-factor regime detection** — listed open in `phase2_improvements.md`, never attempted;
  motivated by the confirmed EMA(100)-mislabeling (§6) and the fact that the current regime signal
  is a single EMA(100)-proximity heuristic.
- **Whether the EMA20/50/100/150 redundancy (§6, rho up to 0.93) can be collapsed without losing
  the proven overextension effect (§3, §4)** — untested; no experiment has directly measured
  whether deduplicating this factor family changes anything.
- **Whether the observed top-10-trade concentration (§1, §8) is statistically unusual** for a
  strategy of this design, via a formal bootstrap or permutation test — untested; only the raw
  descriptive concentration has been computed so far.
- **Whether `MARKET_CRASH_PROTECTION`'s mild trade-level positive P&L (§5) coexists with a
  whipsaw/variance cost not captured by average net P&L** (transaction costs, gap risk, missed
  re-entry) — untested; the disproof in §5 only concerns average realized P&L, not variance or
  opportunity cost.

---

## Final Section: Five Highest-Confidence Research Directions

These are investigations to run next, not designs to build — consistent with a research review,
not a roadmap. Ranked by confidence, each tied to evidence above.

1. **Run the early-heat/day-5 cut rule through `robustness_gate.py`'s full OOS + stress-scenario
   protocol**, not just the full-window counterfactual it currently has. Justification: §4's
   day1-10 divergence is large, monotonic, and visible from day 1; §10 notes this is the only
   candidate lever with a positive full-window estimate that has *not yet* been held to the same
   validation bar as every rejected lever in the research history.

2. **Quantify, via bootstrap or permutation test, how much of the realized 12.85% CAGR is
   attributable to the top 5-10 trades**, to put a confidence interval around the strategy's edge
   rather than relying on the point estimate. Justification: §1/§8 established the 90.4%/149.9%
   concentration as a descriptive fact only; §9 identifies this concentration as the most likely
   explanation for the CAGR ceiling and the OOS instability, but no formal test yet exists to say
   whether it's consistent with a real, if volatile, edge or a large luck component.

3. **Investigate collapsing the ema20/50/100/150-distance family into a single de-duplicated
   "trend extension" feature**, and re-test whether the already-proven overextension effect (§3,
   §4, p<0.05) survives with less redundancy. Justification: §6 measured rho up to 0.93 between
   nominally distinct lookbacks, and this is the only entry-side factor with a statistically proven
   relationship to outcome — understanding its true dimensionality bears directly on why past
   extension-filter attempts improved full-window returns but failed stress tests (§9).

4. **Run `BEAR_SWING_BUY` through its own dedicated OOS/stress evaluation** rather than continuing
   to treat it as a protective-by-design sleeve. Justification: §2/§5 show it is the only entry
   trigger type with negative full-window P&L (-₹3,600, 20 trades), but n=20 is too small to
   call this disproven (§8) — a dedicated test, not a redesign, is the appropriate next step.

5. **Run a systematic small-sample sensitivity check across the full 39-symbol block-list**
   (not just the 2 examined here), measuring how many "confirmed loser" designations rest on
   n≥10 trades versus n<5. Justification: §5's direct check found both examined symbols
   (`THERMAX.NS`, `LAURUSLABS.NS`) diverged from their documented removal evidence in a fresh
   backtest — one in magnitude, one in sign — which has direct bearing on how much confidence the
   broader universe-pruning evidence base deserves.

---

*Phase 2 (P0/P1/P2 Critical Issues Review) and Phase 3 (12-24 month Institutional Roadmap) build
on this document but are separate deliverables, per instruction, and are not included here.*
