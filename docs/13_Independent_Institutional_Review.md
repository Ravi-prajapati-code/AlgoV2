# 13 — Independent Institutional Quant Review (Phase 4)

**Role framing**: This review is written as an independent Head of Quantitative Research seeing
this project for the first time, with a mandate to find flaws — not to defend `10_`, `11_`, or
`12_`. Where I disagree with those documents, I say so explicitly and state why. Every claim below
is labeled **[Fact]** (directly verifiable from code/data), **[Opinion]** (my professional judgment,
stated as such), or **[Unknown]** (insufficient evidence — flagged per the review's own rule rather
than guessed at). Confidence is labeled High/Medium/Low on every major conclusion.

**Method**: In addition to re-reading `10_Quantitative_Research_Review.md`, `11_Critical_Issues_P0_P1_P2.md`,
and `12_Institutional_Roadmap.md`, I went back to primary sources these three documents did not
themselves re-verify: `docs/06_Validation.md`, `scripts/robustness_gate.py`, `scripts/stress_test_scenarios.py`,
`config/watchlist_nse.py`'s own revision history, `config/settings.py`, `config/risk_config.yaml`,
`backtest/engine.py`'s slippage handling, and `main.py`'s use of `risk/manager.py`. Several findings
below come from this primary-source pass and were not identified in Phases 1–3 at all.

---

## SECTION 1 — Executive Assessment

| Dimension | Score /10 | Basis |
|---|---|---|
| Overall maturity | **4** | Real engineering discipline undermined by a foundational data-integrity problem (§2, §10) |
| Research maturity | **5** | Genuine validation *process* (rare at this scale) sitting on top of a contaminated *input* |
| Software maturity | **6** | Clean module boundaries, real test coverage, active bug-fix discipline; offset by dead-code sprawl and a live Sharpe-methodology inconsistency |
| Quantitative maturity | **3** | No multiple-testing correction anywhere, one statistically invalid significance test found (§4), single-split OOS, single-seed synthetic stress tests |
| Strategy maturity | **3** | Edge is real as a *mechanism* (§5) but its *magnitude* cannot currently be distinguished from a favorable-episode luck draw at n=156 |
| Production readiness | **4** | Live broker integration works; 3-position/₹100,000 scale and open P0 items (per `11_`) are not an institutional production profile by any standard definition |

**Would I approve this for live institutional capital? Limited capital only.**

Not "No" — the team has demonstrated something genuinely uncommon: it caught its own inflated
32.04% CAGR number, published the honest 12.85% figure, and kept trading the worse-but-honest
number rather than deploy an unvalidated fix. That instinct is worth more than any single backtest
result and is the reason this isn't a flat rejection.

Not "Yes" — because of one finding that, on its own, is disqualifying at any capital scale until
fixed: the tradeable universe was constructed and repeatedly revised using each symbol's own
historical trade P&L (§2, §10), with at least one revision dated after the official out-of-sample
test window had already begun. **This has since been confirmed by direct code trace, not left as
an inference from docstring timing**: `main.py`'s `cmd_backtest()` calls `get_all_symbols()` with
no date argument; `data/universe.py::get_all_symbols()` returns `ALL_SYMBOLS`, imported directly
from `config/watchlist_nse.py`'s **current working-tree contents**; and `out_of_sample_validator.py`
runs both the TRAIN (2022-2024) and TEST (2025-present) windows through this identical code path.
No date-conditional universe logic exists anywhere in the codebase — every historical backtest date,
regardless of when it falls, is evaluated against today's post-2026-06-17-revision symbol list. This
means the project's own "out-of-sample" validation has not been run on a genuinely held-out universe.
Every Sharpe, CAGR, and win-rate number downstream of that universe — including the ones in
`06_Validation.md` and `10_`'s entire evidence base — inherits this contamination. No amount of
correct statistical technique applied *after* that point can fix it. **[Fact, High confidence]**:
this single issue is more damaging to the project's credibility than any of the individually-flagged
statistical weaknesses in §4, and it means every existing OOS/backtest number must be regenerated
against a point-in-time-correct universe before it can be treated as evidence of anything.

**[Fact, High confidence] — MECHANISM FIXED 2026-07-07, evidence base NOT yet regenerated.** The
unconditional-current-list code path is closed: `main.py::cmd_backtest()` now calls
`data/universe.py::get_all_symbols_as_of(start)`, which reconstructs static-watchlist membership
from dated snapshots in `db/universe_repo.py` (`universe_history`, `operator='static_watchlist_sync'`)
instead of always importing today's `ALL_SYMBOLS`. `config/watchlist_nse.py` now requires
`scripts/sync_static_universe.py` to be run after every edit, and `tests/test_static_universe_sync.py`
(7 tests, passing) guards the mechanism. This closes the bug **going forward only** — git holds a
single squashed commit for `config/watchlist_nse.py` (`a26a4df`, 2026-07-01), so no dated record of
past revisions (including the 2026-06-17 "Quality revision" itself) can be reconstructed. Any date
before the tracking baseline (seeded 2026-07-06/07) raises `UniverseHistoryUnavailable` rather than
silently falling back to the old contaminated behavior — but the fallback path still exists and is
used, loudly, for exactly that reason: **every existing OOS/backtest number described in this
document, `06_Validation.md`, and `10_`'s evidence base remains unregenerated and still contaminated**.
The fix prevents recurrence; it does not retroactively validate anything already produced. Scope
note: only the static-watchlist side was addressed — the dynamic "extras" mechanism
(`data/universe.py::_get_dynamic_symbols()`) is not yet point-in-time filtered and is a known
remaining gap.

"Limited capital only" reflects the project's actual current posture (₹100,000, 3 positions) being
approximately the right size *given* what's actually known — small enough that a full evaporation of
the apparent edge is not financially consequential, which is the correct way to hold a strategy
whose edge magnitude is this uncertain. I would not increase the allocation until §10's top risk
items are addressed.

---

## SECTION 2 — Challenge Every Conclusion from Phase 1

| # | Phase 1 conclusion | Verdict | Confidence | Why |
|---|---|---|---|---|
| 1 | Strategy profits via trend-following/trailing-exit asymmetry, not hit rate | **Probably true** (mechanism) / **Weak evidence** (magnitude) | Medium | The mechanism is directly visible in the exit-reason table and isn't in dispute. But "TRAIL_EXIT + PROFIT_TARGET = 98.7% of realized P&L" is a description of *this specific 156-trade sample*, not a population parameter — see #6 below. |
| 2 | Overextension-at-entry (macd_hist, ema-distance) hurts outcome, p<0.05 | **Weak evidence** | Low-Medium | Untested against multiple-comparisons inflation. ~12–15 features were screened against `pnl_pct` with no Bonferroni/FDR correction (§4). Directionally plausible (mean-reversion-into-trend has real literature support) but the specific p-values overstate confidence. |
| 3 | Sector co-movement is real, Welch t-test p=0.0000 | **Statistical error identified — direction probably true, p-value not trustworthy** | Low (p-value) / Medium (direction) | The t-test treats 584/4,567 concurrent-holding *pairs* as independent observations. They are not: the same stock appears in many pairs simultaneously on the same days (pseudo-replication). This inflates the effective sample size and deflates the reported p-value. See §4 for the correct methodology. |
| 4 | Portfolio correlation at entry doesn't predict outcome, p=0.2185 | **Unsupported (in either direction)** | Low | A non-significant result from an underpowered test is not evidence of "no effect" — it's an absence of evidence. No power analysis was run to say what effect size this test could even detect at n≈156. Phase 1 correctly labeled this "not significant" but the *Institutional Roadmap*'s framing of it as a settled null result goes further than the data supports. |
| 5 | Day1–10 return trajectory cleanly separates LONG_WINNER/OTHER/QUICK_LOSER cohorts, "trade quality is visible early" | **Weak evidence — borderline circular** | Low | **I disagree with Phase 1's confidence here.** The cohorts are *defined* by realized hold-days and P&L sign. A trade labeled `LONG_WINNER` necessarily had a rising price path — that is what makes its P&L positive, not an independent confirmation of anything. This is closer to a tautology than a predictive finding. A genuine test would require a model trained only on day-1–5 features to predict *forward*, out-of-sample outcome on trades not used to define the cohort labels — that has not been done. This directly affects how much confidence the early-heat cut rule (Phase 1 direction #1, Phase 3 Q1) deserves going in. |
| 6 | Top-10-trade concentration (149.9% of P&L) "explains" the CAGR ceiling and past retune failures | **Probably true (descriptive)** / **Weak evidence (causal claim)** | Medium (fact) / Low (causal narrative) | The concentration arithmetic itself is simple and correct — not in dispute. But the leap from "concentration exists" to "this is *why* every retune failed stress tests" is an inferential narrative, not a tested claim. No counterfactual analysis was run showing that rejected retunes specifically altered exposure to the top-10 trades. Plausible, not proven. |
| 7 | `MARKET_CRASH_PROTECTION` exits are mildly net-positive (+₹5,502), contradicting the earlier regime-liquidation-is-harmful hypothesis | **Probably true** | Medium | Simple arithmetic over real trades, low room for error on the direction. n=31 is too small to say the ≈₹177/trade average is distinguishable from zero — no significance test was run on this either, so "mildly positive" should be read as "not shown to be negative," not as a confirmed positive edge source. |
| 8 | (Not examined by Phase 1 at all) Universe construction methodology | **New finding — not addressed in Phase 1** | High | See §10, §2 lead finding above. This is the most consequential gap in Phase 1's scope, not a conclusion it got wrong — it simply never looked at how the universe itself was built. |

**Bias inventory** (found via this pass, not previously named in any document):
- **Data snooping / look-ahead bias**: the universe curation issue above — **[Fact, High confidence]**, directly evidenced in `config/watchlist_nse.py`'s own revision comments (see §10).
- **Survivorship bias**: the "RS anchor" rule — `EICHERMOT.NS`, `GRINDWELL.NS`, `DALBHARAT.NS` were tested for removal, caused a **-6.5pp CAGR drop** in backtest, and were restored specifically because of that backtest sensitivity (`config/watchlist_nse.py` line 27-28: *"Never prune anchors by P&L alone"*). This is universe composition being tuned by its own historical backtest outcome — a textbook curve-fitting pattern, and the project's own documentation is transparent about it. **[Fact, High confidence]**.
- **Multiple testing**: ~12–15 simultaneous correlation tests, no correction (§4). **[Fact, High confidence]**.
- **Selection bias in feature discovery**: features were screened for significance and then reported, on the *same* data the strategy trades — no separate discovery/confirmation split was applied to feature selection the way it was (partially) applied to strategy-level validation. **[Fact, High confidence]**.
- **Non-independence violation**: the sector correlation t-test (§4). **[Fact, High confidence]**.
- **Confirmation bias risk**: I did not find a clear instance of this beyond what's already self-corrected (the 32.04%→12.85% episode is actually evidence *against* systemic confirmation bias — the team reported the number that hurt its own prior work). **[Opinion, Medium confidence]**: process-level confirmation bias appears lower here than in a typical individually-run project, credit due.

---

## SECTION 3 — Hidden Assumptions

| # | Assumption | Justified? | Supporting evidence | Contradicting evidence |
|---|---|---|---|---|
| 1 | Trend persistence exists at the strategy's holding horizons (7–90+ days) | Partially | Medium-term momentum is a well-documented equity anomaly (Jegadeesh & Titman 1993); 31d+ holding buckets are the only profitable ones (`10_` §1) | Short-horizon (~1 month) equity returns show documented *reversal*, not continuation, in some literature (Jegadeesh 1990) — the 0–15 day holding buckets are uniformly net losers (`10_` §2), consistent with short-horizon noise/reversal dominating, not with a clean trend-persistence story at all horizons the strategy trades |
| 2 | The static, manually-curated universe is a stable, point-in-time-valid investable set | **No — contradicted** | — | Direct evidence of P&L-based, backtest-sensitivity-based curation (§2, §10). This is the most consequential false assumption in the project. |
| 3 | RS-rank percentile is a stable, economically meaningful momentum measure | **No — contradicted** | Momentum is a real factor in general | RS rank is a percentile *within this specific curated universe*; the "RS anchor" finding shows the percentile threshold is mechanically dependent on which stocks are kept in the universe, not on any absolute company property. `feature_importance.py`'s own null result (no significant correlation with `pnl_pct`) is consistent with this — the signal may be an artifact of the ranking mechanism, not absent momentum. |
| 4 | Backtest fills (flat 0.1% slippage, full fill) are representative of achievable live execution | Locally yes, generally **no** | At ₹100,000 capital in liquid Nifty-100-adjacent names, flat slippage is a reasonable local approximation | `backtest/slippage.py::simulate_partial_fill()` exists but is **imported and never called** (§4, verified directly) — no market-impact model is actually active. Universe comments explicitly describe some historically-included names as "niche," "low float" (`SOLARINDS.NS`) or too expensive to size (`BOSCHLTD` — "₹37k/share, cannot size at ₹75k capital"). The backtest's fill assumption has never been stress-tested against a larger order size. |
| 5 | 3 concurrent positions is an acceptable risk posture | Untested, not "acceptable by default" | — | Modern portfolio theory (Markowitz 1952 and the broad diversification literature, e.g. Statman 1987) does not generally treat N=3 as diversified — idiosyncratic risk dominates at this position count. Every rejected sizing experiment (ATR sizing, correlation sizing) was rejected *at this fixed N*; the position count itself, the more fundamental lever, was never tested (Phase 3 correctly flags this as an open item — see §7). |
| 6 | A two-state (bull/bear) EMA(100)-proximity signal captures the relevant regime structure | Incomplete | Simple regime filters are standard practice | The concentration finding (`10_` §1) suggests returns are driven more by idiosyncratic single-stock dispersion than by broad market direction — an axis the current regime model doesn't represent at all (§8). |
| 7 | Synthetic stress scenarios (shared macro path + per-symbol noise) adequately proxy real crisis behavior | **No — a specific, verified limitation** | The scenario design is a reasonable *starting point* for macro-shape stress testing | Verified directly in `scripts/stress_test_scenarios.py`: within a scenario, every symbol shares the *same* macro drift path; only idiosyncratic noise differs per symbol. This structurally cannot test a "dispersion regime" (few stocks trend, most don't) — which, per the concentration finding, may be the single most important regime axis this strategy actually depends on. |
| 8 | Documented "confirmed loser" symbol-level P&L statistics are stable, trustworthy estimates | **No — already found to be false for 2/2 checked** | — | `10_` §5: `THERMAX.NS` and `LAURUSLABS.NS` both diverged from their documented removal evidence in a fresh backtest (one in magnitude, one in sign). |
| 9 | Results from this specific configuration (N=3, ₹100,000, this exact 156-trade universe/window) generalize to "the strategy" as a general claim | Not established | — | No external-validity test exists. Every number in every one of `10_`/`11_`/`12_` is implicitly scoped to this exact configuration; none of the three documents states this scoping limitation explicitly. |

---

## SECTION 4 — Statistical Audit

### 4.1 Spearman correlations, entry features vs. `pnl_pct` (`feature_importance.py`, n≈156)

- **Correct test?** Yes — Spearman is a reasonable choice given fat-tailed outcomes; this was a genuinely good methodological decision by the original author, documented as deliberate.
- **Sample size sufficient?** Marginal. At n=156, detecting a "small" true effect (ρ≈0.10) at 80% power is not realistic — Cohen's standard tables put the required n well above 300 for that effect size. Only moderate-or-larger true effects are detectable at all here, and any effect that *does* clear p<0.05 at this n is likely to have its magnitude overestimated in the reported sample (the "winner's curse" / significance filter — conditioning on significance biases the observed effect size upward; see Gelman & Carlin, 2014, on this exact phenomenon).
- **Is the p-value meaningful?** Only loosely. ~12–15 features were tested simultaneously with no correction. The four EMA-distance features are highly intercorrelated (ρ up to 0.93, `10_` §6), so they are not 4 independent tests — the *effective* number of independent hypotheses is closer to 6–9 than 15, which somewhat mitigates but does not eliminate the multiple-comparisons problem.
- **Multiple-testing correction required?** Yes. Recommend Benjamini-Hochberg FDR (q=0.10, appropriate for exploratory screening) applied across the full feature set actually tested, logged explicitly.
- **Would an institutional researcher accept this?** Not as presented. It would be accepted as a *screening* result requiring confirmation on a separate discovery/confirmation data split — which does not currently exist for feature selection (only for strategy-level parameters, via the TRAIN/TEST split).
- **Confidence label**: the "overextension hurts" direction — **Medium** (plausible, has independent literature support for overbought/mean-reversion-into-trend effects). The specific magnitude/p-value as reported — **Low**.

### 4.2 Sector correlation Welch t-test (`correlation_analysis.py`, n=584 vs. n=4,567 "pairs")

- **Correct test?** **No.** This is the most serious statistical error found in this review. Concurrently-held stock pairs are not independent observations — the same symbol appears in many pairs across many overlapping days, so the underlying observations are clustered/pseudo-replicated. A standard two-sample Welch t-test assumes i.i.d. observations and will report an artificially small p-value under this kind of clustering.
- **Sample size sufficient?** Nominally large, but the *effective* independent sample size (after accounting for clustering by day and by stock) is unknown and almost certainly much smaller than 584/4,567.
- **Is p=0.0000 meaningful?** Not as reported. It is very likely an artifact of pseudo-replication rather than solely reflecting a genuinely infinitesimal true p-value.
- **Multiple-testing correction required?** Not applicable here (single test), but the more fundamental problem (wrong test for the data structure) needs fixing first.
- **Would an institutional researcher accept this?** No. A professional risk/quant team would require either (a) clustered/panel-robust standard errors (cluster by trading day), or (b) a block-permutation test that resamples at the whole-trading-day level to preserve the real cross-sectional dependency structure, before accepting a same-sector-vs-different-sector correlation claim.
- **Confidence label**: sector clustering *direction* (same-sector pairs correlate somewhat more than cross-sector pairs) — **Medium/High**, because it is also independently supported by basic equity factor-structure theory (sector is a well-established common risk factor in commercial risk models). The specific **p=0.0000 claim** — **Low**, likely overstated by an unknown but potentially large margin.

### 4.3 Out-of-sample train/test split (`out_of_sample_validator.py`, single split: TRAIN 2022–2024, TEST 2025–present)

- **Correct test?** Partially. A single chronological split correctly avoids shuffling time-ordered data (a real and common error this project avoided). But a *single* split is materially weaker than the field's stronger standard — walk-forward or combinatorial purged cross-validation (López de Prado, *Advances in Financial Machine Learning*, addresses exactly this: a single OOS split's result is itself subject to which specific period happened to fall into "test").
- **Sample size sufficient?** The TEST window (2025-01-01–present) almost certainly contains on the order of 30–50 trades given the full-window total of 156 over 4.5 years — too few to draw firm conclusions on its own; a single unusual quarter can dominate the result.
- **Is the reported 19.3pp win-rate divergence meaningful?** As a descriptive fact, yes — this is a real, correctly-computed number, and flagging it (rather than hiding it) is good practice. As a hypothesis test, no formal significance test or confidence interval was placed around it; the 15pp/0.8/0.6 divergence thresholds in `06_Validation.md` are fixed, somewhat arbitrary cutoffs rather than derived from a sampling-distribution argument.
- **Critical, previously unflagged issue — now code-confirmed, not inferred**: this split's validity as a genuine holdout is **directly undermined by the universe-construction finding** in §2/§10. `config/watchlist_nse.py`'s "Quality revision" is dated **2026-06-17** and removed 20 symbols using cumulative trade-count/win-rate/P&L statistics — necessarily computed using data that includes the entire 2025-01-01+ TEST window, since that revision happened well after TEST begins. Direct code trace confirms the mechanism: `main.py::cmd_backtest()` → `data/universe.py::get_all_symbols()` → `config.watchlist_nse.ALL_SYMBOLS` is called with no date parameter for both the TRAIN and TEST backtest runs alike, so every historical date is evaluated against today's post-revision list. **This means the TEST split is not a clean holdout: the universe it's evaluated on was shaped using information from inside the TEST window itself.** This is a confirmed look-ahead bias finding, not merely a "small sample" caveat.
- **Confidence label**: the 19.3pp divergence *number* — **High** (simple, verifiable computation). Its interpretation as a trustworthy, uncontaminated OOS signal — **Low** (downgraded from "possibly compromised" to **confirmed compromised** by direct code trace).
- **Fix status (2026-07-07)**: the code path producing this contamination is closed (see Executive Assessment). `scripts/out_of_sample_validator.py::run_window()` was also fixed the same day — it previously called `subprocess.run(..., capture_output=True)` and silently discarded `stderr`, which would have swallowed the new `UniverseHistoryUnavailable` warning; it now prints captured stderr. The 19.3pp number itself has **not** been regenerated and remains contaminated evidence until a fresh OOS run is done against the point-in-time universe.

### 4.4 Stress-test scenarios (`stress_test_scenarios.py`, 4 synthetic scenarios, `robustness_gate.py` default single seed=42)

- **Correct test?** Reasonable concept, weak implementation. Two specific issues verified directly in code:
  1. **Single-seed by default.** Each scenario, as run by `robustness_gate.py`, is *one* random draw from the noise distribution, not a distribution of outcomes. A pass/fail verdict from one seed near a threshold is not distinguishable from noise. No confidence interval or pass-rate is reported.
  2. **Shared macro path across all symbols within a scenario.** Verified in `populate_scratch_db()`: every symbol receives the *same* macro drift sequence for a given scenario; only idiosyncratic per-symbol noise differs. This structurally cannot represent a "narrow market" (few names trend, most chop) vs. "broad market" (most names trend together) dispersion regime — precisely the axis §3/§8 flag as likely more relevant to this strategy's actual edge than the four synthetic macro shapes tested.
  3. Scenarios are hand-parameterized (segment lengths, drift magnitudes, noise std), not calibrated against the empirical distribution of actual historical Indian mid/large-cap drawdowns or dispersion episodes.
- **Would an institutional researcher accept this?** As a first-pass sanity check, yes, with credit for existing at all (most retail-scale projects skip stress testing entirely). As a "necessary but not sufficient" gate for the reasons `06_Validation.md` itself states, it is *appropriately labeled* — but the label undersells how much additional rigor is needed before "PASS" should carry real weight.
- **Recommended stronger methodology**: run each scenario across N≥30 seeds and report a pass **rate** (e.g., "27/30 seeds pass"), not a binary; add at least one real historical-block-bootstrap scenario (resampling genuine historical multi-month return blocks for this universe, preserving real cross-sectional dispersion) alongside the synthetic macro-shape scenarios.
- **Confidence label**: "a stress-testing step exists and has caught real false positives" (extension-filter, staged-entry) — **High** (documented, verifiable). "A given PASS verdict reliably indicates robustness" — **Low-Medium**, given the single-seed/shared-macro-path design.

### 4.5 Sharpe-ratio methodology inconsistency (already flagged as P1-8 in `11_`)

Confirmed directly: `backtest/metrics.py` uses population variance (÷N); `scripts/walk_forward.py` uses sample variance (÷N−1). For n≈150–300 return periods the relative difference is modest (roughly 0.3–0.6%) but is compounded whenever a Sharpe number is compared against a fixed external gate threshold. **Recommend standardizing on sample variance (ddof=1)** — the more conservative, standard convention in quantitative finance research. **[Fact, High confidence this inconsistency exists as described]**.

---

## SECTION 5 — Edge Audit

| Component | Estimated contribution | Confidence | Basis |
|---|---|---|---|
| Trend-following / "let winners run" mechanic | **Large, dominant** (`TRAIL_EXIT`+`PROFIT_TARGET` = ₹110,252 vs. total ₹68,255) | Medium-High | Mechanically verifiable in the trade log; the *direction* is not in dispute — the *stability* of this magnitude across other samples is unknown |
| Risk management (stop-loss discipline) | Cost-center, not a return source, on current evidence | Medium | `STOP_LOSS` exits are -₹53,012; stop-losses cap loss per trade but no counterfactual ("what if no stop-loss") has been run to credit them with a positive marginal contribution |
| Trailing exits | (Same mechanism as trend-following above — not cleanly separable) | — | — |
| Position sizing | **Near-zero incremental contribution shown**, at current N=3 | Medium | ATR-sizing and correlation-sizing both tested and rejected — no CAGR or stress benefit at `MAX_POS=3` (`05_Research.md`). Whether *slot count itself* matters is untested (§7). |
| Market regime (bull/bear gate) | **Unknown** — never isolated | Unknown | No ablation test (strategy with regime gating fully disabled, run through the full validation gate) exists in the evidence base. **Unknown — additional research required.** |
| Sector effects | Risk-control contribution plausible; **return contribution not shown** | Low-Medium | Sector caps reduce correlated-drawdown risk (with the §4.2 caveat on the specific significance claim); no evidence sector information adds to expected *return*. |
| Portfolio construction (N=3 concentration) | **Return-amplifier, not an edge source in itself** | High | At 3 slots, any single large winner is mechanically ~1/3 of the book — this amplifies the dollar impact of the trend-following mechanism above; it does not generate the edge, it leverages it (and its variance) |
| Luck / episode-dependence | **Plausibly substantial** | Medium (opinion) | Given 90.4%/149.9% concentration in the top 5/10 trades and no bootstrap confidence interval has ever been computed (`10_` §8 flags this as observational), the honest position is that a meaningful fraction of realized return could be attributable to which specific large-trend episodes fell inside this exact 4.5-year window. This should be **assumed material until disproven**, not assumed away. |
| Unknown / confounded with universe-construction bias | **Cannot be cleanly separated from any of the above** | High (that it's unresolved) | Every component above is estimated on a universe that was itself partly selected using in-sample and TEST-window-overlapping P&L data (§2, §10). Until the universe-construction issue is fixed and the backtest is rerun on a corrected, point-in-time-valid universe, no component estimate above should be treated as final. |

**Overall [Opinion, Medium confidence]**: the strategy's identifiable *mechanism* of edge (buy strength, cut losers fast, let a minority of winners run via a trailing stop) is a coherent, literature-consistent trend-following design. Its *measured magnitude* (12.85% CAGR, Sharpe 0.83) cannot currently be trusted as a stable estimate, both because of small-sample concentration and because of upstream universe-construction contamination.

---

## SECTION 6 — Factor Audit

| Feature | Economic rationale | Statistical support | Redundancy | Predictive power | Verdict |
|---|---|---|---|---|---|
| RS rank (universe percentile) | Momentum (Jegadeesh-Titman) | None shown (`feature_importance.py` null) | Mechanically tied to universe composition (RS-anchor artifact, §3) | None shown | **Research Further** — redesign as an absolute or sector-relative measure, not a percentile-within-a-curated-universe; current null result may be an artifact, not a true absence of signal |
| ATR% at entry | Volatility-normalized sizing input | N/A (used for sizing, not selection) | Low | Not its job | **Keep** |
| Volume ratio | Liquidity/conviction proxy | None shown | Low | None shown | **Research Further** — may need replacement/supplementation by delivery-% (raw volume doesn't distinguish speculative churn from conviction) |
| ADX | Trend-strength filter | None shown | Low-Medium (shares information with EMA-distance family) | None shown | **Research Further** — plausibly another universe-circularity casualty; re-test post-fix before removing |
| EMA20/50/100/150 distance | Overextension / mean-reversion-into-trend | macd_hist, ema100/150 significant at p<0.05 (uncorrected, §4.1) | **High** — ema100↔ema150 ρ=0.93; `"ema_50"` is mislabeled and actually computes EMA(100) | Weak-moderate, directionally coherent | **Keep the concept, remove the redundant parameterization** — collapse to 1–2 genuinely distinct lookbacks. I concur with Phase 1's own research direction #3 here; this is the one place Phase 1's technical judgment holds up under adversarial review. |
| `dist_from_high20d_pct` | Breakout-proximity (Donchian/turtle-style) | Not distinctly isolated in the evidence reviewed | Unknown | Unknown | **[Unknown — additional research required]** rather than asserted either way |
| RSI | Standard oscillator | None shown | Overlaps with EMA family conceptually | None shown | **Research Further / candidate for removal** pending an ablation of the whole "momentum confirmation" layer |
| MACD histogram / bullish flag | Momentum-extension proxy | macd_hist significant (negative correlation w/ outcome — buying already-extended momentum is worse) | Shares information with EMA-distance | Weak-moderate | **Keep as candidate**, re-test after multiple-testing correction and universe fix |
| `perf_10d_at_entry` | Short-term momentum **or** reversal — genuinely ambiguous a priori | Not isolated distinctly | Unknown | Unknown | **[Unknown]** — the theoretically "correct" sign at a ~10-day horizon is disputed in the literature itself (Jegadeesh 1990 short-term reversal vs. 3–12mo momentum continuation); should be empirically settled with a dedicated, corrected test, not assumed |
| `turnover_at_entry` | Liquidity/tradability gate | N/A (executability gate, not a return signal) | Low | Not its job | **Keep** |
| Sector | Risk-factor / correlation control | Direction plausible, magnitude/p-value overstated (§4.2) | Distinct axis from EMA/price family | Not shown to predict return, only correlated risk | **Keep as risk control**, not as a return signal |
| Regime (single EMA(100)-proximity) | Trend/direction filter | Not independently tested via ablation | Shares the EMA(100)-mislabeling issue | Contribution unknown (§5) | **Research Further** (multi-factor regime), appropriately gated in Phase 3 |

**Missing factors [Opinion]**, each tied to a specific gap found in this review:
- **True cross-sectional relative strength** (vs. sector peers, not vs. a curated universe) — directly addresses the RS-anchor artifact.
- **Delivery percentage** — already scoped in `12_`'s Q3 pilot; I independently concur this is the highest-leverage cheap addition.
- **Volatility-regime indicator** distinct from price-trend regime (§8).
- **Liquidity/impact-cost proxy** (average daily traded value relative to intended position size) — needed for any capacity conversation (§10).
- **Benchmark-relative alpha/beta decomposition** — needed to answer whether this is alpha or repackaged small/mid-cap momentum beta (§11).

---

## SECTION 7 — Portfolio Construction Review

**[Fact, verified directly]**: `config/risk_config.yaml` sets `max_open_positions: 3`; the trade log's observed maximum concurrent holdings is exactly 3. This is not a theoretical constraint — it is the actual, binding, always-hit ceiling.

- **Position sizing**: rejected experiments (ATR 3x sizing, correlation-aware sizing) both found "no CAGR benefit, no stress benefit" — but both were tested *at fixed N=3*. This is a weaker finding than it's presented as: it shows sizing sophistication doesn't help *given* 3 slots, not that sizing sophistication doesn't matter in general.
- **Number of positions**: **[Opinion, High confidence]** this is the single dominant portfolio-construction weakness in the project. N=3 is not diversified by any standard finance definition — idiosyncratic, single-name risk is not diversified away at this count (see the broad diversification literature, e.g. Statman 1987, on the number of holdings needed to substantially reduce unsystematic risk in an equity portfolio — figures in that literature range from ~20–30+ names, an order of magnitude above 3). This single structural choice mechanically explains **both** faces of the strategy's return profile: the extreme upside concentration (§5, `10_` §1) and a meaningful share of the drawdown fragility (MDD 23.67%) — they are the same coin.
- **Cash management**: **[Unknown — additional research required]**. Not traced in this pass; idle-capital handling between trades (especially given the ~7-day average QUICK_LOSER cycle) is unexamined.
- **Correlation control**: sector caps exist, but at N=3, there is mechanically rarely room for more than one same-sector position anyway — the correlation-sizing rejection (§5) is really a restatement of "N=3 leaves little room for a correlation control to bind," not an independent finding about correlation control's value in general.
- **Sector exposure**: same logic — the cap is largely redundant at this position count.
- **Capital efficiency**: **[Unknown]** — a direct cash-drag measurement (fully-invested-equivalent return vs. actual, cash-inclusive return) has not been computed anywhere in the evidence base.
- **Drawdown behavior**: MDD 23.67% against CAGR 12.85% (MAR ≈0.54) is not unreasonable *for a 3-name book*, and could reflect real protective value from the stop-loss discipline — but the observed drawdown history may simply not yet include a scenario where 2–3 concentrated positions gap down simultaneously in a genuine correlated tail event, since the stress-test framework's shared-macro-path design (§4.4) doesn't test *idiosyncratic* simultaneous single-name tail risk beyond the shared macro component.

**[Opinion, High confidence]**: I disagree with `12_`'s sequencing here. The Institutional Roadmap places the slot-count sensitivity test in **Q4** of Year 1, after research validation (Q1), dead-code decisions (Q2), and a new data pilot (Q3). Given that nearly every portfolio-construction weakness in this section traces back to the same root cause (N=3), and that root cause has never itself been through the validation gate, I would pull this test forward — ideally run in parallel with Q1, not sequenced after three other quarters of work that may turn out to be secondary to it.

---

## SECTION 8 — Market Regime Review

- **Bull-market dependency**: **High confidence, strong dependency**. 116/156 trades (74%) are BULL-regime entries, contributing ₹56,765 of ₹68,255 total realized P&L (83%). The core strategy is structurally a trending/bull-market strategy.
- **Bear-market dependency**: The BEAR-regime entries (40 trades, 26%) show a *higher* win rate (52.5% vs. 36.2%) but contribute less absolute P&L (₹11,490) and, per Phase 1's own caveat, are likely a different trade *type* (safe-haven/bear-swing mechanisms), not the core strategy performing well in bear conditions. **[Opinion, Medium confidence]**: it would be a mistake to read "BEAR entries have higher win rate" as "the strategy performs well in bear markets" — the core engine (`STRENGTH_CONFIRMED_BUY`) barely operates in genuine sustained bear conditions; the separate `BEAR_SWING_BUY` sleeve that does is net-negative over the full sample (`11_` P1-2).
- **High/low volatility dependency**: **[Unknown — additional research required]**. No feature or backtest slice in the evidence base conditions performance on a volatility *level* as distinct from trend *direction*. This is a real, previously-unnamed gap: the project conflates "regime" with "trend direction" and has never examined realized/implied volatility level as its own axis.
- **Trending vs. sideways dependency**: **High confidence, strong dependency, well-evidenced**. Two independent lines of evidence agree: (1) only 31d+ holding buckets are profitable (`10_` §1); (2) the extension-filter candidate specifically failed the `prolonged_sideways_chop` and `extended_bear_grind` synthetic scenarios, and the research history explicitly states every CAGR-recovering regime retune failed on "sideways-chop whipsaw." This is about as solid a regime-dependency finding as exists anywhere in this project.
- **Missing regime definitions [Opinion]**:
  1. Volatility-level regime, distinct from trend direction.
  2. **Cross-sectional dispersion regime** (narrow market — few names trend strongly — vs. broad market) — directly motivated by the concentration finding (§5) and the stress-test limitation (§4.4); arguably the single most relevant missing regime axis given everything else in this review.
  3. Liquidity/market-stress regime (bid-ask widening, impact-cost spikes) — unaddressed given the dead slippage model (§4.4, §10).
  4. Macro/rate regime — not examined anywhere in the evidence base; **[Unknown]** whether relevant.

---

## SECTION 9 — Research Process Review

**Strengths [Fact, credit due]**:
- A real, standing rejected-experiment record (`05_Research.md`) that is actually consulted before new work — closer to a professional "failed factor" graveyard practice than most individually-run quant projects maintain.
- A real, automated, three-stage validation gate (`robustness_gate.py`) that has demonstrably caught genuine false positives (extension-filter, staged-entry) — not aspirational, verifiably operating.
- Willingness to self-correct publicly and keep the *worse* but *honest* number live (32.04%→12.85%) rather than deploy on the strength of a bug-inflated result. This is genuinely rare, including in professional settings where inconvenient corrections are sometimes quietly buried.

**Weaknesses [Opinion, grounded in facts above]**:
- **Does not avoid overfitting at the universe-construction level** — the single largest research-process gap found in this review (§2, §10). No amount of rigor in the strategy-parameter validation gate can compensate for a universe that was itself selected using in-sample, TEST-window-overlapping outcome data.
- **No multiple-testing correction anywhere** in the feature-discovery process (§4.1).
- **No walk-forward / combinatorial cross-validation** — relies on a single chronological train/test split (§4.3).
- **No structured, queryable research database** — the research history exists as prose (`05_Research.md`), not as versioned records with reproducible run IDs, data snapshots, and commit references tied to each specific claim. This makes it hard to audit, months later, exactly which universe/data snapshot produced a given number — directly relevant to the universe-contamination finding, which is easy to miss in a narrative document and much harder to miss in a structured, point-in-time-stamped record (§13, §14 address this prescriptively).
- **Statistical discipline is uneven**: strong instinct ("don't trust a single-stage result") paired with weak formal technique (no correction for multiple comparisons, an invalid significance test on non-independent data, no bootstrap/permutation confidence intervals anywhere in the evidence base).

**Comparison to professional practice**: see §12 for detail. In summary, this project has adopted the *later-stage* disciplines of professional systematic research (validation gates, failure tracking) noticeably well for its scale, but is missing the *earlier-stage* disciplines (point-in-time data integrity, multiple-testing governance, walk-forward validation) that those later stages depend on to mean what they claim to mean.

---

## SECTION 10 — Research Risk Register

| Risk | Probability | Impact | Evidence | Mitigation |
|---|---|---|---|---|
| **Universe-construction data-snooping** | High (already occurred) | **Critical** | `config/watchlist_nse.py` revision comments: P&L-based removal dated 2026-06-17, after TEST window (2025-01-01+) began; RS-anchor restoration explicitly justified by backtest CAGR sensitivity | Rebuild universe construction as a mechanical, point-in-time rule (no manual P&L-based curation); re-run the entire evidence base on the corrected universe before trusting any existing number |
| **Sample-size risk** | High (structural, n=156) | High | `10_` trade log; §4 power analysis | Bootstrap/permutation confidence intervals (Phase 1 direction #2, endorsed here); accumulate more live/paper history before any capital increase |
| **Return concentration / tail-dependency** | High (already observed) | High | Top-5/10 trades = 90.4%/149.9% of total P&L | Bootstrap CI; slot-count sensitivity test (pulled forward per §7) |
| **Trending-market/regime dependency** | High (well-evidenced) | Medium-High | Sideways-chop stress failures; holding-bucket pattern (§8) | Multi-factor regime research (already queued); explicit live "extended sideways" monitoring alert |
| **Factor redundancy** | High (measured directly) | Medium | EMA family ρ up to 0.93 (§6) | Factor collapse (Phase 1 direction #3, endorsed) |
| **Validation-methodology risk** (single-split OOS, single-seed synthetic stress, invalid pairwise t-test) | High (structural, present today) | Medium-High | §4 full audit | Walk-forward/combinatorial CV; multi-seed stress pass-rates; clustered/permutation significance tests |
| **Capacity / market-impact risk** | **[Unknown]**, but plausible given evidence | Potentially Critical if capital scales | Dead partial-fill model (§4.4); "low float"/"niche" language in universe comments; ₹100,000 current scale | Revive or replace `simulate_partial_fill`; explicitly test behavior under simulated larger order sizes before any capital-scaling decision |
| **Liquidity risk** (correlated exit difficulty in stressed markets) | **[Unknown]** | Medium-High | No evidence base item addresses bid-ask widening or exit liquidity under stress | **[Unknown — additional research required]**; would need real historical liquidity data through past stress periods for this specific universe |
| **Structural market-change risk** (can this factor set decay or be arbitraged away in Indian mid/small-caps) | **[Unknown]** | Medium | No multi-decade or multi-cycle test exists; ~4.5-year window may not span a full cycle | **[Unknown — additional research required]**; needs a much longer history than currently used |
| **Opportunity-cost / cash-drag risk** | Medium-High (53 quick-loser trades, ~7.1-day avg hold) | Low-Medium | Cohort table (`10_` §1) | Direct cash-drag measurement (§7 gap) |
| **Operational/key-person risk** | **[Unknown]** | **[Unknown]** | Outside the quantitative evidence base | Flagged for completeness only — no evidence gathered on this dimension |

---

## SECTION 11 — Missing Research (not already answered elsewhere in this review)

- Is the observed return concentration consistent with a real, reproducible edge, or statistically indistinguishable from a lucky draw of episodic trend events? (Queued, Phase 1 direction #2 — still genuinely open.)
- What is the strategy's **benchmark-relative alpha** (beta-adjusted, vs. Nifty 500 / Nifty Midcap)? Never computed anywhere in the evidence base — a first-order gap for any institutional capital conversation.
- Is the edge **capacity-limited**, and at what AUM does it degrade? Entirely unaddressed given the dead market-impact model.
- Is the edge stable across a longer history or a full market cycle the current ~4.5-year window may not span?
- **Does removing the circular P&L-based universe curation change the results materially?** Directly implied as necessary by this review's own §2/§10 finding — not yet run, and arguably the single most important open question in the entire project.
- What is the strategy's factor exposure against standard equity risk factors (size, value, momentum, quality, low-vol — Fama-French/Carhart-style or a local equivalent)? Never attempted; would clarify whether "the edge" is genuine alpha or repackaged small/mid-cap momentum beta.
- Why do the top 10 trades specifically succeed — is there an identifiable common precursor beyond "long hold, positive outcome"? Not analyzed at the individual-trade-narrative level.
- What is the actual cash-drag/capital-utilization rate given N=3 slots and frequent short holds?
- Has genuine multi-fold walk-forward testing (as opposed to a single split) ever been run? No.
- What is the actual statistical power of the current feature-screening tests at n≈156, and what real effect sizes would currently go undetected?

---

## SECTION 12 — Institutional Comparison (process, not returns)

This compares the project against **publicly known, general characteristics of professional systematic research practice** — not claimed insider knowledge of any specific firm's proprietary process.

| Practice | Standard in professional systematic research | This project |
|---|---|---|
| Point-in-time data / universe integrity | Universe membership and fundamentals are stored with as-of timestamps so no backtest can use information unavailable at that historical date | **Missing** — universe construction directly violates this (§2, §10). Largest single gap. |
| Multiple-testing governance | Formal hypothesis budgets, deflated Sharpe ratio, probability-of-backtest-overfitting metrics (López de Prado) | **Missing** — exploratory correlation scans run uncorrected (§4.1) |
| Walk-forward / combinatorial purged CV | Standard for any time-series backtest claim | **Missing** — single chronological split only (§4.3) |
| Independent validation function separate from the strategy's author | Common in larger organizations ("four-eyes" review) | **Not evidenced / not applicable** at this project's apparent single-developer scale — a reasonable, not damning, gap given scale; notably, this Phase 4 review is itself an attempt to fill that specific function |
| Formal alpha registry / factor lifecycle tracking | Idea → research → candidate → production → retired, with structured records | **Missing** — addressed prescriptively in §13 |
| Capacity / market-impact modeling before scaling | Standard before any AUM increase decision | **Missing** — dead partial-fill model (§4.4, §10) |
| Documented failed-experiment history | Common in mature research teams | **Present** — `05_Research.md`, genuinely credit-worthy |
| Mechanical, automatic validation gates (not narrative-only) | Standard | **Present** — `robustness_gate.py`, unusually good for this project's apparent scale |

**[Opinion, Medium-High confidence]**: this project's process maturity is *bimodal* — it has correctly adopted the later-stage disciplines (validation gates, failure tracking) that many individually-run projects skip entirely, but is missing the earlier-stage discipline (point-in-time data integrity) that those later stages structurally depend on. A professional risk function reviewing this project would flag the data-integrity gap first, before even looking at the validation-gate design, because no amount of downstream rigor can repair an upstream contaminated input.

---

## SECTION 13 — Alpha Registry Design

**Schema** (per factor/rule): Name · Hypothesis · Economic rationale · Data source · Features · Statistical evidence · OOS performance · Stress-test result · Correlation with existing factors · Confidence score (Low/Medium/High) · Status.

**Statuses**: Idea → Research → Candidate → Validated → Production → Retired.

**Initial population, reflecting current project state as found by this review**:

| Name | Status | Confidence | Note |
|---|---|---|---|
| Trailing-stop "let winners run" exit | **Production** | High | Core, validated mechanism (§5) |
| Sector exposure cap | **Production** | Medium | Risk-control validated (direction), not a return source; p-value overstated (§4.2) |
| Trend-extension / overbought-entry filter (EMA-distance + macd_hist family) | **Research** | Low-Medium | Statistically flagged but uncorrected for multiple testing; pending redundancy collapse (§6) |
| Raw RS-rank (universe percentile) | **Candidate for Retirement** | Low | Contaminated by RS-anchor artifact; no shown predictive power (§6) |
| Early-heat day-5 cut rule | **Idea** | Low | Full-window estimate only; not yet through `robustness_gate.py`; underlying cohort-divergence evidence is borderline circular (§2 #5) |
| `BEAR_SWING_BUY` defensive sleeve | **Candidate under review** | Low | Net-negative over full sample; n=20 too small to retire outright, but not currently earning its keep (`11_` P1-2) |
| ATR-based risk sizing (3x) | **Retired** | High | Documented rejection: no CAGR/stress benefit at fixed N=3 |
| Correlation-aware position sizing | **Retired** | High | Documented rejection: no headroom at fixed N=3 |
| Staged entry | **Retired** | High | Exposure collapse |
| Extension filter (hard threshold implementation) | **Retired** | High | Failed 2/4 stress scenarios — note: the *underlying hypothesis* is not retired, it lives on as the "trend-extension filter" Research entry above |
| Delivery-% conviction factor | **Idea** | — | Not yet implemented; scoped in `12_` Q3 |
| Sector-relative RS (redesign) | **Idea** | — | Not yet implemented |
| Multi-factor regime detection | **Idea** | — | Gated on EMA-redundancy findings per `12_` |
| Slot-count (N) as a lever, independent of sizing formula | **Idea** | — | Never tested; this review recommends elevating its priority (§7) |

---

## SECTION 14 — Research Operating System

**Pipeline** (unchanged shape from the brief, with governance rules attached at each gate):

```
Hypothesis
  ↓  [must cite: economic rationale, existing evidence or literature, expected sign]
Implementation
  ↓  [off-by-default env-var pattern, as already practiced — keep this]
Backtest
  ↓  [must run against a POINT-IN-TIME-STAMPED universe snapshot — new requirement]
Validation
  ↓  [any correlation/significance claim logged in a running per-session test ledger;
       Benjamini-Hochberg FDR applied across that ledger before anything is called "significant"]
OOS
  ↓  [minimum 3 non-overlapping walk-forward folds, not a single split, OR a written,
       reviewed exception with explicit reasoning]
Stress Test
  ↓  [minimum 20 seeds per synthetic scenario, report pass RATE not binary;
       at least one real historical-block-bootstrap scenario required, not synthetic-only]
Decision
  ↓  [adopt / reject / retire — logged with full metrics, not narrative-only]
Research Memory
  ↓  [structured record per experiment: hypothesis, data-snapshot ID / commit hash,
       every gate's metrics, and — if rejected — the specific stage that caused rejection]
Production
  ↓  [promotion checklist: passed walk-forward + multi-seed stress + has a bootstrap CI
       on incremental contribution + capacity/impact-checked if it touches sizing or count]
Monitoring
  ↓  [per-factor live decay tracking — rolling realized vs. backtested win-rate/PF,
       extending the existing walk_forward.py decay-monitoring concept below the whole-strategy level]
Retirement
  [status change, not deletion, when live decay or new research shows it's no longer earning its keep —
   formalizes the project's own good existing instinct (05_Research.md) into standing governance]
```

**Governance rules (the "no skip" enforcement)**:
1. No experiment may use symbol-level historical P&L to alter universe membership without a documented point-in-time cutoff, and it must be re-validated only on data strictly after that cutoff.
2. No feature/factor may be declared "significant" without correction for the number of tests run in that research session — every test is logged, corrections applied at the session level, not the individual-test level.
3. No strategy-level decision may rest on a single train/test split — minimum 3 non-overlapping OOS folds, or an explicit, reviewed, written exception.
4. No stress-test verdict may be issued from a single random seed — minimum 20 seeds per scenario, reported as a pass rate.
5. Every decision (adopt/reject/retire) is logged as a structured record (not prose-only) with a data-snapshot identifier, so a claim can be re-audited months later against exactly the inputs that produced it.
6. Production promotion requires the full checklist above signed off explicitly — no exceptions for "it looked good on the full-window number."

---

## SECTION 15 — Final Verdict

**1. Three biggest strengths**
- Honest-baseline discipline: catching and publishing the 32.04%→12.85% correction, and continuing to trade the worse-but-honest number rather than an unvalidated fix.
- A real, working, automated three-stage validation gate that has demonstrably caught genuine false positives.
- A documented, actually-consulted failure history — closer to professional "failed factor" tracking than most projects at this scale maintain.

**2. Three biggest weaknesses**
- Universe construction is circular, contaminated by in-sample and TEST-window-overlapping P&L data — the single most damaging finding in this review.
- Extreme, never-validation-gated concentration (N=3 positions) that mechanically drives both the upside concentration and a meaningful share of the drawdown profile.
- Systemic statistical methodology gaps: no multiple-testing correction, one confirmed-invalid significance test (§4.2), single-split OOS, single-seed synthetic stress tests that structurally erase cross-sectional dispersion.

**3. What should never be changed**
The standing rule to run every candidate through the full validation sequence before trusting a result, and the practice of keeping an honest, consulted record of rejected experiments. These cultural/process assets are rarer and more valuable than any specific signal or parameter currently in the system.

**4. What should be removed**
The current P&L-curated, backtest-sensitivity-preserved universe construction process (replace with a mechanical, point-in-time-safe rule); the redundant 3-of-4 EMA-distance features (collapse to 1–2); the dead `simulate_partial_fill`/slippage sophistication that creates false confidence by existing but not running (wire it in for real, or remove it so nobody mistakes it for active); `BEAR_SWING_BUY`, pending its own dedicated validation-gated test given its current net-negative status.

**5. What should be researched next** (priority order)
1. Rebuild universe construction to be point-in-time-safe, and **re-run the entire Phase 1 evidence base on the corrected universe** — this should happen before any of Phase 3's Q1 items, since it could change all of their answers.
2. A slot-count sensitivity test (pulled forward from Phase 3 Q4 — see §7).
3. A bootstrap/permutation confidence interval on realized CAGR and trade concentration.
4. A benchmark-relative alpha/beta decomposition against a standard equity factor set.

**6. If I had one year, where would I spend the time**
The first quarter entirely on the point-in-time data-integrity problem — fixing universe construction and re-establishing every existing result on that corrected foundation — not on any new factor, data source, or piece of architecture. Everything in `12_`'s roadmap is reasonable *sequencing*, conditional on a trustworthy backtest existing to sequence work on top of. Right now, the backtest's evidentiary foundation is the highest-leverage problem in the entire project, and no research built on top of it — new data sources, new factors, dead-code revival — is trustworthy until this is fixed. **This is my strongest disagreement with `12_`'s roadmap as sequenced**: the universe-construction fix should be Month 0, addressed explicitly, not implicitly assumed away by starting directly at "validate existing research directions."

**7. If I were investing my own money, would I trust this system?**
For a small amount I could afford to lose entirely — yes, and notably, that is close to the scale (₹100,000, 3 positions) the project already runs at. For meaningful capital — not yet. The process discipline on display (validation gating, honest self-correction, failure tracking) gives me more confidence in the *team and process* than in the *current specific edge estimate*. The honest answer to "does this system have a real, capacity-relevant edge" is, after this review: **"Unknown — additional research required"**, specifically pending the universe-construction fix and a bootstrap confidence interval on the concentration finding. That stated uncertainty — not a number — is the most important output of this review.
