# 12 — Institutional Roadmap (12–24 Months) (Phase 3)

**Scope**: What to build next, in what order, and why. This builds directly on Phase 1's evidence
(`10_Quantitative_Research_Review.md`) and Phase 2's triage (`11_Critical_Issues_P0_P1_P2.md`) — it
does not re-derive findings, it sequences action on them. Every item below traces to a specific P0/P1/P2
line item or a Phase 1 research direction; nothing here is a new claim about what's wrong.

## Sequencing principles

The order below is deliberate, not arbitrary. Five rules governed it:

1. **Live safety closes before new development opens.** Building new signals or architecture on top
   of unverified live state (unconfirmed cron deployments, unaudited stop-loss records, an uncommitted
   fix) is how this project got its recurring "built but never connected" pattern in the first place.
2. **Exhaust what the existing evidence can tell you before acquiring new data.** Five research
   directions are already scoped and cheap to run (Phase 1's final section). They come before any new
   data source, however promising, because they're faster, free, and might change what a new data
   source should even target.
3. **Resolve architectural ambiguity before building more on an uncertain foundation.** The dead-code
   inventory (P2-1) is a decision debt — every quarter it stays undecided is a quarter new work might
   be built on top of, or duplicate, something that should have been deleted.
4. **New data sources are staged, evidence-gated pilots — not a wishlist commit.** The reviewer
   critique that prompted this review proposed seven orthogonal data sources. Committing to all seven
   is not an institutional roadmap, it's a hope. One pilot, chosen by cost and evidence fit, gates
   whether a second is worth pursuing.
5. **Portfolio construction changes come last, not first.** Sizing and slot-count decisions should be
   informed by the trade-outcome distribution's actual shape (Phase 1 §1: fat-tailed, concentration-
   driven), which Q1's validation work will sharpen. Resizing the book before that is sizing against a
   description of the problem that might change.

**Standing gate, unchanged from current practice**: nothing graduates from research/pilot to live
capital without clearing `robustness_gate.py`'s full OOS + 4-stress-scenario protocol. This roadmap
does not relax that bar for any item below.

---

## Month 0–1: Close the P0s (prerequisite, not a roadmap phase)

Gates everything after it. All four items are from Phase 2 and are verification or low-risk
mechanical fixes, not research:

- Commit the loser-leak block-list fix (P0-1) — safe, no live effect from the commit itself.
- Apply the live DB correction for the 26 leaked symbols (P0-1), pending your explicit confirmation
  as before.
- Audit pre-existing open positions for stale trailing-stop records (P0-2) — read-only query first.
- Verify GTT monitoring cron is actually deployed on the live server (P0-3).
- Verify `--mode daily` universe safety net is actually scheduled (P0-4), deploy if not.

**Exit criterion**: all four P0s are either resolved or explicitly accepted as ongoing/low-risk with
your sign-off. No Q1 research work should start against a live state with open P0s.

---

## Quarter 1 (Months 1–3): Validate before you build

Run all five Phase 1 research directions through `robustness_gate.py`. These are already scoped —
this quarter is execution, not design:

| Item | Traces to | What "done" looks like |
|---|---|---|
| Early-heat day-5 cut rule | Phase 1 final §1 | Pass/fail verdict from full OOS + 4-stress protocol, not just the existing full-window estimate |
| Bootstrap/permutation test on trade concentration | Phase 1 final §2 | A confidence interval on realized CAGR, not just the point estimate — tells you how much of 12.85% is attributable to variance |
| EMA20/50/100/150 redundancy collapse | Phase 1 final §3 | A decision on whether the 4-feature family can become 1–2 without losing the proven overextension effect |
| `BEAR_SWING_BUY` dedicated OOS/stress test | Phase 1 final §4 | A specific verdict on this sleeve, not a general "seems negative" |
| Block-list evidence sensitivity sweep (all 39 symbols) | Phase 1 final §5 | A count of how many exclusions rest on n≥10 vs. n<5 trades |

Alongside, clear the mechanical P1 items that cost little and remove ambiguity for later work:
back-port the `ALTER TABLE` into `schema_universe.sql` (P1-7), standardize the Sharpe convention
across `backtest/metrics.py` and `walk_forward.py` (P1-8), and decide the fate of the silent config
parameters (P1-5) — wire in or delete `MAX_HOLD_DAYS`, `ADX_TREND_THRESHOLD`, `MIN_SIGNAL_SCORE`,
the YAML `RS_THRESHOLD` override.

**Exit criterion**: every item in the table above has a pass/fail verdict, not an estimate. Nothing
proceeds to Q2 on the strength of a full-window number alone — that's the exact mistake the 32.04%→
12.85% correction (2026-07-02/03) already taught this project.

---

## Quarter 2 (Months 3–6): Resolve the dead-code fork, gated on Q1 results

Per-module decision — **Revive** (fix + integrate + full test coverage) or **Delete** — for every
item in P2-1, no permanent limbo past this quarter:

- `portfolio/optimizer.py` — currently unimportable (`Regime` doesn't exist in `strategy/regime.py`).
  Contains regime-aware sizing, ML-confidence scaling, quarter-Kelly sizing. **Revive only if** Q1's
  EMA-redundancy work or the concentration bootstrap suggests regime-conditioned sizing has a real
  chance of mattering; otherwise delete rather than fix an import error for code with no evidenced
  case for its complexity.
- `risk/manager.py` — stranded because it depends on the optimizer above; decision follows the same
  gate.
- `strategy/market_filter.py` (P1-6) — latently buggy in addition to dead. Fix only if revived;
  otherwise delete, don't leave a known landmine sitting uncalled indefinitely.
- `strategy/stock_ranker.py`, `strategy/quality_filter.py`, `indicators/momentum.py`/`volatility.py`/
  `volume.py`, `runner/repository.py`, `runner/intraday_runner.py`, `backtest/slippage.py`,
  `universe/ipo.py` (zero DB rows, zero callers, never fired once) — each gets an explicit keep-and-
  finish or delete decision. Default to delete unless there's a specific, named reason to finish it;
  "might be useful later" is not a reason, per this project's own standing pattern of unfinished
  integrations accumulating silently.

If Q1's EMA-redundancy investigation concludes the current single-EMA(100)-proximity regime signal
(P1-4) is a meaningful weak point, this quarter is also where multi-factor regime detection (open
since `phase2_improvements.md`) gets designed and tested — **only** on that evidence-gated condition,
not by default.

**Exit criterion**: zero modules left in undecided limbo. Each either has live callers and test
coverage, or is deleted from the repo.

---

## Quarter 3 (Months 6–9): One orthogonal-data pilot, evidence-chosen

The external reviewer's critique — that every existing signal (EMA, RS, ATR, momentum, composite
score) is price-derived — is correct and already corroborated by Phase 1 (§6: the EMA family
measures one thing four ways; §7: RS rank, ADX, RSI, volume ratio show no significant relationship
to outcome on their own). The reviewer proposed seven candidate data sources. This roadmap commits to
**one pilot**, chosen on cost and fit to the evidence, not all seven:

**Primary pilot: NSE delivery-percentage data.** Rationale: publicly available (no vendor contract,
no licensing lead time), genuinely orthogonal to price/EMA/RS (it measures actual investor conviction
via delivered-vs-traded volume, not price geometry), and directly answers the reviewer's core
critique at the lowest integration cost of the seven options. Daily-frequency, compatible with the
existing 7–60 day holding-period structure (unlike quarterly shareholding data or earnings-surprise
events, which are too low-frequency to inform entries at this holding cadence).

**Secondary, lower-cost parallel experiment: sector-relative RS.** Rationale: sector clustering is
the one already statistically validated structural finding in this project (Phase 1 §4, p=0.0000).
Reworking the existing (currently non-predictive, Phase 1 §7) raw RS-rank feature to be
sector-relative reuses data already in the pipeline and tests a hypothesis this project has already
proven the substrate for, rather than starting from zero.

**Explicitly deferred, not committed**: FII/DII flows, shareholding-pattern changes, earnings
surprises, market breadth (folds into the Q2 regime work if that track is greenlit, rather than
being a separate pilot), and corporate-event/news signals. These require paid data vendors,
low-frequency disclosure cycles, or unstructured-data pipelines this project has no current
infrastructure for — revisit only if the Q3 pilot clears validation and justifies the added
complexity of a second data source.

**Exit criterion**: delivery-% and sector-relative-RS features run through the same feature-importance
and correlation methodology as Phase 1 (Spearman vs. `pnl_pct`, redundancy check against existing
features), then — only if individually significant and non-redundant — through the full
`robustness_gate.py` protocol before any live wiring. A negative or null result here is a valid,
useful outcome (it would mean the reviewer's "orthogonal data" hypothesis, however intuitive,
doesn't survive contact with this strategy's actual trade structure) — not a reason to keep adding
data sources until one sticks.

---

## Quarter 4 (Months 9–12): Portfolio construction, informed by the concentration finding

Two items, both gated on knowing the trade-outcome distribution's actual shape from Q1's bootstrap
work rather than assuming it:

- **Slot-count sensitivity test.** Every past sizing lever (ATR risk sizing, correlation-aware
  sizing) was rejected specifically *at the current `MAX_POS=3` constraint*
  (`phase2_improvements.md`: "any size-throttling lever taxes CAGR w/ no stress-scenario benefit at
  MAX_POS=3"). That phrasing leaves open whether 3 slots itself, not the sizing formula, is the
  binding constraint — untested until now. Test 5–8 slots with reduced per-position size against the
  full OOS + stress protocol.
- **Operationalize the concentration finding (P1-3).** Turn "90.4% of P&L comes from 5 trades" from
  a research finding into a monitoring artifact — e.g., a rolling metric tracking time-since-last-
  large-trend-trade — so that a quiet multi-month live stretch is read against an explicit baseline
  expectation, not mistaken for a new bug and reacted to with an emergency re-tune (the exact failure
  mode the research history shows already happened once, during the 2026-07-02 regime-divergence
  chase).

**Exit criterion**: a specific, tested slot-count recommendation (keep 3, or move to a tested
alternative), and a live dashboard/alert metric for return concentration that didn't exist before.

---

## Months 12–24: Compounding on Year 1 results

This far out, specifics depend on Year 1 outcomes rather than being fixed now. The commitments that
**are** fixed:

- **If the delivery-% or sector-relative-RS pilot cleared validation in Q3**: integrate as a live
  scoring factor, with a dedicated post-integration OOS check using the additional live data
  accumulated since Q3 (a true forward-test, not just another backtest split).
- **If `portfolio/optimizer.py`'s regime-aware sizing was revived in Q2**: complete its integration
  into `portfolio/manager.py`'s live path, with an explicit replace-vs-coexist decision — not left as
  a second, parallel sizing system nobody chose between.
- **Re-run the multi-factor regime investigation (if built in Q2) against a full additional year of
  live data** since the original 2026-07-02 regime-signal fix — the original re-tuning attempts
  failed stress tests on a comparatively short post-fix history; a longer live sample is a
  meaningfully different test, not a repeat of the same one.
- **Revisit the slot-count/sizing decision from Q4** with a full year of the operationalized
  concentration metric in hand — confirm whether the expected "lumpy" return pattern held, or whether
  it needs recalibrating.
- **Final dead-code pass**: anything not revived by the end of Q2 and still sitting in the repo
  should be deleted by this point — no item from P2-1 should still be undecided at the 24-month mark.

**Standing exit criterion for the whole roadmap**: every item above resolves to either "shipped and
live, validated against the full gate" or "explicitly rejected with the evidence that killed it,"
matching how every experiment in this project's history to date has been recorded — no item is
allowed to just quietly disappear from tracking.
