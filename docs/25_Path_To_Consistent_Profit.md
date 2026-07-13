# 25. Path to Consistent Profit — State of Research Synthesis

2026-07-10. Written in response to a direct question: across everything
tested so far (Trade Attribution, Opportunity Attribution, Signal
Mechanism/Lifecycle Analysis, entry/ranking experiments, portfolio
construction audits), what do we actually know, and what does it take
to get **consistent profit in any market condition** — not just a good
full-window CAGR number.

This is a synthesis, not new research. Every claim below is backed by
an existing doc/memory; see citations.

> **CORRECTION 2026-07-11**: `config/risk_config.yaml` was found with an
> uncommitted `max_open_positions: 5` (should be 3) — a leftover from
> the 2026-07-09 MAX_POSITIONS diagnostic, never reverted. Reverted to
> the committed 3. The "CAGR +18.09%" figure in §1 below was very
> likely generated under this N=5 drift, not the intended N=3. The
> confirmed, re-run **N=3 baseline is CAGR +11.29%, Sharpe 0.63, FULL
> window** (TRAIN +13.44%/0.73, TEST +11.00%/0.64). See
> [[e1_idle_cash_ablation_20260711]]. Treat every "18.09%" reference
> below and elsewhere as N=5-contaminated until restated.

## 1. What's proven and live (the frozen baseline)

- Entry gate: Relative Strength threshold, Trend Alignment (EMA),
  SuperTrend, ADX (as a pass/fail threshold), Breakout. No complex
  ranking variant has ever beaten plain qualification + RS-first slot
  fill. [[docs/24_Rejected_Forever.md]]
- Exit chain: trend break, momentum decay, regime-flip bear protection.
  No alternative exit scheme has beaten it.
- Risk: drawdown-based position sizing, regime filter, liquidity floor
  (never fires in backtest, kept as a live-only safety net against risk
  the backtest can't model).
- Fill model, replacement-eviction, and cash-buffer sizing were
  corrected 2026-07-10 (commit `df9856f`) — the honest full-window
  number is **CAGR +18.09%, Sharpe 0.94, MDD 18.06%**, down from an
  inflated 32.28%/1.72 that had an exit-side look-ahead bug. Any number
  from before this fix is stale. [[backtest_live_fidelity_fix_20260710]]

## 2. The central diagnosis: the leak is not stock-picking

This is the single most important finding in the whole research arc.
Every attempt to find a better way to *rank* or *choose* stocks has
failed. What actually destroys returns is **portfolio construction**,
not signal quality:

- 74.5% of all qualified signals are never bought — not because they're
  outranked, but because **no slot is open that day**
  (`NO_SLOT_AVAILABLE`). [[opportunity_attribution_engine_20260709]]
- Daily rank-vs-forward-return correlation is ~0 (+0.016, n=804 days).
  A random draw from the qualified pool beats the RS-first pick.
  [[opportunity_attribution_engine_20260709]]
- Entry-time technical indicators (RS, ADX, ATR%, vol ratio, EMA
  distance) cannot distinguish a 31+ day winner from a quick loser —
  they're statistically indistinguishable at entry.
  [[trade_attribution_engine_20260701]]
- What *does* separate winners from losers is what happens **after**
  entry (day-1 forward return, Cohen's d=1.09) and **structural**
  properties (sector, streak position) — not anything you could rank
  candidates by at decision time. [[signal_lifecycle_archetype_20260710]]

**Conclusion**: stop looking for a better ranking formula. That avenue
is closed (see §3). The lever that matters is capacity — how many
slots exist and how fast they turn over — not which stock fills them.

## 3. Exhausted avenue: entry/ranking refinement

Every one of these has been tested and rejected — do not re-attempt
without a genuinely new angle (see `docs/24_Rejected_Forever.md` for
full evidence):

- Complex multi-factor ranking, freshness ranking, ADX-as-sort-key,
  turnover-as-sort-key, extension-as-sort-key, weighted composites —
  all null (|rho|<0.06, n=8,633).
- Institutional volume surge, breakout freshness, sector leadership,
  sector tailwind as market *mechanisms* — all null.
- Sector blacklist (IT/Chemicals/Construction Materials excluded) —
  clean overfit (train inflated 3x, test flat).
- Streak-position/priority buy ordering — tested twice (pre- and
  post-fidelity-fix). Post-fix result is clean: TRAIN/TEST/FULL all
  improve, but `crash_v_recovery` CAGR flips +4.66%→-4.32%.
  REJECTED both times. [[streak_position_pref_retest_20260710]]
- Extension filter (skip candidates >17.8% above 100d EMA) — clean
  full-window win, passed OOS, failed 2/4 stress scenarios.
- Dynamic replacement triggers (RS/volume/sector rotation) — correct
  in hindsight, no real-time trigger works.

## 4. The recurring killer: `crash_v_recovery`

Look at *why* candidates fail, not just that they fail. Across this
whole research arc, one stress scenario has killed more otherwise-good
levers than any other:

| Candidate | Full-window result | Killed by |
|---|---|---|
| Extension filter (17.8% EMA100 cap) | CAGR 12.83%→15.27%, every metric up | `crash_v_recovery` + `prolonged_sideways_chop` |
| Liquidity floor tightening (p25/p10) | — | `crash_v_recovery` CAGR +4.66%→-0.85%/-0.95% |
| Streak-position preference | CAGR 18.09%→21.82%, every metric up | `crash_v_recovery` CAGR +4.66%→-4.32% |

Three separate, structurally different levers, three identical failure
signatures. This is not coincidence — it's a real, structural weakness
of the strategy: whatever makes candidate-substitution/filtering
"smarter" in normal conditions keeps picking the wrong name specifically
in the sharp-crash-then-recovery pattern, most likely because the stock
that recovers hardest off a crash is disproportionately a
thin-liquidity/high-volatility/off-plan name that every one of these
filters is designed to screen out.

**This is the actual bottleneck for "consistent profit in any market
condition."** Not entry ranking (closed, all null). Not portfolio
construction slot-scarcity alone (real, but a capacity problem, not a
crash-fragility problem). The strategy has a specific, repeatable blind
spot: it does not know how to hold onto — or get back into — the name
that leads a crash recovery, because every refinement tried so far
optimizes against exactly that name's profile.

**Untested idea worth prioritizing**: instead of another entry/ranking
filter, build a regime-conditional override — detect the
`crash_v_recovery` pattern specifically (e.g. sharp index drawdown
followed by a confirmed V-shaped reversal) and suspend whichever
filter/preference is active during that window only. None of the three
rejected candidates tested a regime-gated variant; all three applied
unconditionally across every regime.

> **UPDATE 2026-07-12**: the table above is now stale for 2 of the 3
> rows. Re-tested extension-filter-tightening (`EXTENSION_CAP_PCT=0.178`)
> and liquidity-floor-tightening (`MIN_DAILY_TURNOVER=1180000000`, the
> old p25 value) through the full gate suite under the current,
> fully-fixed engine (all 4 docs/29 Rule 1 items closed — the original
> rejections predate the rotation/ride-winner/score-drop-exit backtest
> port). **The crash-specific kill mechanism no longer reproduces**:
> `crash_v_recovery` CAGR went baseline -1.87% -> +4.56% (extension) /
> +1.18% (liquidity) — improved, not flipped negative. A direct trade
> pull confirmed why: baseline and both candidates hold the *identical*
> two crash-recovery winners (AUBANK.NS +24.39%, CAMS.NS +44.42%, both
> `bull_regime_recovery` exits, same entry/exit dates/prices in all 3
> runs) — neither filter touches them at all. The divergence is only in
> later, smaller rotation trades.
>
> **But both candidates still fail on the original, more basic
> reason**: real full-window/train CAGR cost (extension: train
> +14.69%->+10.09%, full +14.52%->+10.68%; liquidity: train
> +14.69%->+8.52%, full +14.52%->+9.63%) — this part of the original
> `docs/24_Rejected_Forever.md` writeup was correct and still holds.
> `robustness_gate.py`'s own PASS/FAIL logic only checks for stress-scenario
> sign-flips, not full-window regression, so both runs print "PASS" —
> that PASS is real but narrow, and does not mean these are now good
> candidates. See [[crash_v_recovery_mechanism_retest_20260712]].
>
> Net effect: the "crash_v_recovery repeatedly breaks otherwise good
> ideas" framing above was true of the *old* (fidelity-gap) engine, not
> the current one, for these 2 candidates specifically. A regime-gated
> variant aimed at preserving crash-recovery capture would not help
> here, since capture was never actually lost. Streak-position-preference
> (the 3rd historically-failed candidate) could not be re-tested — its
> code was fully deleted (`docs/24` row: "no trace left in
> config/settings.py") — so its status under the current engine is
> unknown without reimplementing it.

## 5. What remains genuinely open

- **MAX_POSITIONS=5 vs 3**: CAGR +8.89% vs +5.62% in an earlier test,
  not yet run through `robustness_gate.py`.
  [[rotation_capacity_followup_20260709]]
- **Sector durability as an entry filter** (not blacklist-based
  exclusion, which failed overfit) — the underlying effect (t up to
  +7.6) is real and Bonferroni-safe, but only tested as a blunt
  blacklist. A softer, non-binary formulation hasn't been tried.
- **Regime-gated streak-position preference** (§4) — untested variant
  of an otherwise-rejected lever.
- **Faster slot turnover / capacity-focused levers** generally — the
  diagnosed leak (§2) points here, but no capacity-specific lever
  beyond MAX_POSITIONS has been tested yet.

## 6. What this means for "consistent profit, any market condition"

The honest state: the *signal* (which stocks to watch) is solid and
well-validated. The *ranking* of that signal has been tested from
every angle and adds essentially nothing — this is now a closed
question, confirmed by three independent studies (entry attribution,
opportunity attribution, signal mechanism analysis). The real gap is
in **construction**: how many positions the system can hold, how fast
it rotates them, and specifically how it behaves in a sharp-crash/
V-recovery regime, where every attempted refinement so far has made
things worse.

Recommended next steps, in priority order:
1. ~~Robustness-gate MAX_POSITIONS=5.~~ **DONE 2026-07-11 — PASS.**
   Run under the now-fixed engine (docs/29 Rule 1, all 4 items closed):
   TRAIN CAGR +14.69%→+22.06%, TEST CAGR +5.98%→+10.66% (Sharpe
   0.41→0.66, MDD 17.98%→11.68%, PF 1.31→1.50), FULL CAGR
   +14.52%→+18.62% — every metric improved candidate vs baseline, no
   stress-scenario sign-flip (`crash_v_recovery` -1.87%→-2.18%, both
   negative; `prolonged_sideways_chop` actually improved -17.61%→
   -10.06%; the other two scenarios were byte-identical, likely because
   those synthetic scenarios never have enough qualifying symbols to
   fill a 4th/5th slot). First gate-confirmed improvement lever this
   project has found — see [[max_positions_5_gate_pass_20260711]].
   Note: both baseline and candidate still fail `main.py`'s own
   absolute investment-grade bar in every window (a separate, larger,
   already-known gap — this PASS is relative-improvement-only, per
   Rule 2's "necessary, not sufficient" caveat).
2. ~~Investigate the `crash_v_recovery` mechanism directly.~~ **DONE
   2026-07-12.** Result was not "same 1-2 symbols killing all three" —
   it was "the kill mechanism itself doesn't reproduce anymore for 2 of
   3 candidates" (see UPDATE box above and
   [[crash_v_recovery_mechanism_retest_20260712]]). Both still net-reject
   on plain full-window CAGR cost, unrelated to crash dynamics.
3. Regime-gating extension-filter/liquidity-floor specifically to
   protect crash-recovery capture is now moot — capture was never lost
   under the current engine. A regime-gated streak-position-preference
   remains untested but would need the deleted code rebuilt first.
   Given both re-tested candidates net-reject anyway, the higher-value
   open item is `MAX_POSITIONS=5` (item 1, DONE/PASS) — the real
   deployable finding from this pass.
