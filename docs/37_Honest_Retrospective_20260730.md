# 37. Honest Retrospective — Answering the Hard Questions (2026-07-30)

User asked 22 blunt questions about whether this system actually works, what's
proven vs assumed, and what the real lessons are. Every answer below is
sourced from an existing audit (`docs/23`, `docs/26`, `docs/28`, `docs/29`,
`docs/35`, `docs/36`) or current repo state — nothing here is newly invented
for this doc. Where there's no test on record, that's stated as a gap, not
filled with a guess.

---

## 1. Did our strategy work?

Partially, and only in a specific window. Most current honest numbers
(post-Nifty500-expansion, post-charges-fix, TEST window 2025-01→2026-06-04,
`docs/36`): **CAGR +41.64%, Sharpe 1.58, PF 2.11, N=133 trades.** That looks
like a clear win. But the FULL window (2022-2026, same fix) flips to
**CAGR -4.64%** (memory: `charges_model_fix_20260722`). Same strategy, same
code — the TRAIN-period years (2022-2024) are where it loses money, the last
~18 months are where it wins. That split alone is reason for skepticism, not
celebration — see Q7/Q9 below.

The one number that isn't in dispute: **the entry signal itself beats random
selection**, decisively (+5.62% CAGR vs -3.38%/-2.66% for random arms,
Sharpe flips negative→positive, `docs/23` §XIV). That's the one Proven,
adversarially-tested claim in the whole project. Almost everything else is
softer than that.

## 2. Is Nifty 500 suitable for our strategy?

We already ran this experiment in the other direction. Universe went from a
curated ~100-symbol watchlist to the full Nifty 500 (504 symbols,
`config/watchlist_nse.py`, expanded 2026-07-21). Then we tested *shrinking*
it back down to the top 50 by turnover (`docs/36`, 2026-07-28) — **REJECTED,
8 gate failures**, TEST CAGR +41.64%→-15.91%, every stress scenario failed,
two with sign-flips. Cutting to large/liquid names doesn't de-risk this
strategy, it starves it — the smaller/newer names are where the edge lives.
So: yes, broad (Nifty 500-scale) is the right shape for this strategy, not a
guess — it's the losing side of a real A/B test.

Caveat: the *early* 100-symbol curated version was itself never proven
optimal either — it was inherited, not derived. "Full Nifty 500 beats a
50-name cap" is proven. "Full Nifty 500 is the single best possible universe
size" is not — nobody's tested 200, 300, 400.

## 3. Does big vs small stock universe impact profit?

Yes, materially, and in the opposite direction most people would guess.
Restricting to bigger/more liquid names (top-50 by turnover) actively hurts
— see Q2. Two independent tests now confirm this same mechanism:
`liquidity_threshold_experiment_20260710` (tightening liquidity floors at
p25/p10 — REJECTED, "structural not calibration") and `docs/36`
(turnover-cap — REJECTED). Two-for-two, same failure signature both times.
The strategy's edge depends on access to smaller-cap, less-institutionally-
crowded names — which also matches the entry-attribution finding that ADX/
breakout trend-confirmation (not RS/size/liquidity) is doing the real work
(`docs/23` §XIV).

## 4. Do we check confirmation of trade?

Yes — this is real, in code, not assumed. `portfolio/manager.py`'s live
execution path never marks a position as opened/closed on the basis of a
placed order alone: `place_order_with_retry()` → check `res.status` is one
of OPEN/COMPLETE/PENDING (reject otherwise, `continue`, no DB mutation) →
`_await_order_completion()` polls for the real fill → only on
`status == COMPLETE and avg_price > 0` does it record the trade at the real
fill price; otherwise it logs a warning and waits for the next broker-sync
pass to reconcile. This exact pattern is why the 5-day market-close-overrun
bug (`data/providers/upstox_provider.py` fix, 2026-07-29/30) never caused a
phantom position — every rejected broker order correctly fell through to
"skip, don't record" rather than assuming success.

## 5. After we buy a stock, does it go down the next day?

**No test on record at the literal 1-day horizon** — don't want to guess
here. The closest real data: Spearman correlation between entry rank-score
and forward return, swept across horizons 5d/10d/11d(median hold)/16d(mean
hold)/20d/30-90d (`docs/23` §XVII Q1). At 5 days out, correlation is
**+0.004** — indistinguishable from zero, both directions roughly balanced
(50.7% positive days vs 48.1% negative). That's the nearest honest answer:
in the first few days after entry, whether the pick goes up or down looks
close to a coin flip *conditional on it having qualified at all* — the edge
this system has doesn't come from short-term direction-calling, it comes
from the entry gate filtering out bad candidates in the first place (Q1's
"signal beats random" finding). A true next-day-only stat has never been
pulled — flag as an open item if it matters.

## 6. Next day up, down, or flat?

Same gap as Q5 — no 1-day-bucketed up/down/flat breakdown exists in any doc.
What does exist: real holding-period stats from 266 live-equivalent trades —
median hold 11 days, mean 16.4 days, p75 21 days (`docs/23` §XVII). The
system is not built or tested around next-day behavior at all; it's a
multi-week-hold momentum strategy. Asking "what happens the next day" is
answering a question this system was never designed to answer well — the
proven edge (31+ day holds, `docs/26` row 22) lives much further out than
next-day.

## 7. Is our parameter proven, but market reject them?

This is close to the single most important finding in the whole project.
`docs/23`'s Assumption Audit classified 46 load-bearing parameters:
**8 Proven, 12 Supported, 8 Plausible, 18 Unknown.** Of the 8 that went
through a real bidirectional bracket test (`docs/23` §XIII — loosen AND
tighten from the live default, checked against TRAIN, TEST, and 4 stress
scenarios), **every single one is a genuine local optimum** — every
loosening direction that showed a gain was a TRAIN-only artifact that went
flat-or-worse on the held-out TEST window or a stress scenario (classic
overfit signature), and every tightening direction failed outright. So: no,
it's not "our parameters are right but the market rejects them" — it's the
opposite risk. The parameters that *have* been tested are holding up
honestly. The danger is the other 18 that were never individually tested at
all — those are unproven, not proven-and-rejected.

The place where "looked right, market said no" literally happened:
`MAX_POSITIONS=5` (`docs/23` §XVIII). Full-window and diagnostic numbers
looked like a clear win (+8.89% vs +5.62% CAGR). Ran it through the real
gate (TRAIN/TEST split + 4 stress scenarios) — **REJECTED**. The full-window
win was almost entirely a TRAIN-period artifact; TEST window flipped
CAGR-negative (+1.26%→-2.85%), Sharpe negative. `crash_v_recovery` stress
CAGR flipped negative too. That's the textbook version of your question —
and the honest answer is the market was right, not the parameter.

## 8. What market condition?

Regime is detected via a smoothed EMA(100) crossover on the index
(`strategy/regime.py:19`), classified BULL/BEAR with a ~45-day hysteresis
buffer (`REGIME_SWITCH_DAYS`) to avoid whipsaw-driven flip-flopping. As of
the latest commit (`a731b5d`, regime-tiered sizing) there's now a third tier,
STRONG_BULL, layered on top. This classifier is Plausible-not-Proven-optimal
(`docs/23` #25) — it's internally consistent (backtest now matches live
exactly, fixed 2026-07-02) but was never tested against alternative EMA
spans (50/200) to see if 100 is actually the best choice, just the one that
was picked. Signal-frequency diagnostics (`docs/23` §XV) show BEAR regime is
where the system goes quiet — 8 of 838 trading days had zero qualified
candidates, and every single one of those 8 was during BEAR. Every BULL day
in the sample produced at least one candidate.

## 9. Do we decide the market based on bull/bear?

Yes, and it's now the most recently-touched part of the whole system
(commit `a731b5d`, regime-tiered sizing: BEAR/BULL/STRONG_BULL slot
multipliers layered on top of the existing regime-gated candidate filtering
and drawdown throttles). But be precise about what's tested vs shipped:
regime *detection* is real and matches live (Q8). Regime *sizing tiers*
were just added and are **not yet gate-validated** — the honest position per
the project's own standing rule (`docs/29` Rule 2: nothing enters production
until `robustness_gate.py` PASS) is that this is a shipped-but-unproven
lever until it goes through the same TRAIN/TEST/4-stress gate every other
parameter in `docs/23` §XIII had to clear.

## 10. Why do we fail or pass?

Two different failure modes, both documented, and they're not the same
problem:

- **We fail when a lever wins on TRAIN/full-window and loses on TEST or a
  stress scenario** — the overfit signature. `MAX_POSITIONS=5` (Q7), the
  Universe-Cap-50 experiment (Q2/Q3), and multiple entry-gate loosening
  directions in `docs/23` §XIII all failed this exact way.
- **We fail when a recurring market signature breaks unrelated levers** —
  `crash_v_recovery_recurring_killer_20260710`: three separate, unrelated
  candidate improvements were independently killed by the same stress
  scenario. That's not "this one idea was bad," that's "there's a structural
  weakness in how the strategy handles a specific market shape," and it
  hasn't been root-caused yet (`docs/26` bottom line, still open).
- **We pass** when a change is genuinely orthogonal to the mechanisms above —
  e.g., the DD-throttle removal (E1, `docs/26` row "Cash allocation"): FULL
  CAGR +11.29%→+14.88%, Sharpe 0.63→0.76, MDD improved, only one stress
  scenario degraded with no sign flip. Passed the real bar, not just a
  favorable window.

## 11. What is the reason?

The single deepest reason, from `docs/28` (Software Truth Audit,
2026-07-11): for a period, **live and backtest were not running the same
strategy.** Three default-ON live position-management rules
(`ROTATION_ENABLED`, `RIDE_WINNER_ENABLED`, `SCORE_DROP_EXIT_ENABLED`)
existed in the live path and were completely absent from the backtest
engine — not a bug in shared logic, logic that literally didn't exist on
one side. Every `robustness_gate.py` verdict produced before that closed
(2026-07-11) was answering "does this help a simulated strategy that isn't
the real one." That's the root reason a lot of early results in this
project's history need to be read with a caveat, and it's exactly why
`docs/29` (Project Governance) exists — to make sure it can't happen silently
again.

## 12. If we consider some truth, is it actual truth or just our assumption?

This is literally what `docs/23`'s classification scheme (Proven/Supported/
Plausible/Unknown) exists to answer, parameter by parameter, and the honest
tally is: **8 Proven, 12 Supported, 8 Plausible, 18 Unknown — out of 46**.
Under 40% of this system's load-bearing decisions have been through anything
resembling a proof. The rest range from "real evidence but not rigorous"
down to "nobody ever tested this, it's just the value that got set once."
`docs/23` §XI ranks every Unknown by how costly it would be if wrong, and
flags which pairs can be tested independently vs must be isolated (shared
decision funnels confound attribution if tested together). If you want a
one-line rule going forward: before treating any parameter as "known to
work," check whether it's actually in the Proven/Supported list or just
inherited — most of this system's numbers are the latter.

## 13. What if we rebuild the system?

Already specified, not hypothetical (`docs/23` §XI "Rebuild from
Proven+Supported only" / §XI star item): daily RS-rank the universe (Proven
signal exists), hold the top-decile equal-weight (score-based sizing is
confirmed dead code, ATR/correlation sizing tested and rejected — no
sizing scheme beat flat), sector caps applied (Supported — real risk
correlation, not a return lever), **no** RS_THRESHOLD cutoff, RSI band,
ADX/breakout/volume overlay (all Unknown or since found near-zero-signal —
see Q17), **no** stop-loss/trail/profit-ceiling (Supported: exits were never
the leak), **no** fixed 3-slot cap (Supported-negative: strands ~27% of
capital on BULL days). Exit rule: fall out of the qualifying rank band.
Performance of this stripped-down version has never actually been run — it's
fully specifiable today and was flagged as the single highest-priority
missing experiment in the whole audit. It has not been executed as of this
writing. If the goal is the actual truth about this strategy's edge, this
is the next real step, not another parameter tweak.

## 14. What do you take care of?

Concretely, from this session and the pattern across the project:

- Never mutating the DB or recording a position/trade on anything less than
  a confirmed broker fill (Q4) — this is why 5 straight days of broker
  order rejections (the market-close-overrun bug) caused zero data
  corruption.
- Never letting a "PASS" stand without the TRAIN/TEST split and 4 stress
  scenarios (`robustness_gate.py`) — a full-window-only win is explicitly
  distrusted by convention now (`docs/29` Rule 2), because of Q7/Q10's
  overfit pattern.
- Keeping unrelated dirty working-tree changes (e.g. `portfolio/manager.py`,
  `db/universe_repo.py` diffs sitting in this repo right now, unrelated to
  the market-close fix) out of any commit I make — cross-feature commit
  contamination has bitten this project before (memory:
  `cross_feature_commit_contamination_20260728`).
- Treating a live-money server deploy as a separate, explicit-confirmation
  action from writing the fix itself — code being correct locally isn't the
  same decision as pushing it to capital that's actually trading.

## 15. What key learning did you find?

The clearest one, stacked across `docs/16`/`docs/23` §XIV/§XVI/§XVII: **the
edge is almost entirely in the entry gate deciding who's admitted, not in
ranking who's admitted.** RS *rank order* has ~zero forward-return
correlation at the real holding horizon (daily Spearman ≈ -0.005 to +0.016
depending on horizon, `docs/23` §XVII Q1) — reversing the sort
(`REVERSE_RS`) even beat the live ranking in one full-window test
(`docs/23` §XIV). But RS *labeling* (the qualification threshold itself)
matters enormously — shuffle the RS values and performance collapses to
near-breakeven (§XIV finding 5). That's a genuinely counterintuitive result:
most of the "skill" people assume lives in picking the best of the
qualified candidates. Here, essentially none of it does. The skill is
entirely in the gate.

Second, closely related: **portfolio construction, not signal quality, is
where opportunity is lost.** 74.5% of qualified signals are never bought —
not because they're ranked badly, but because there's no slot open
(`docs/23` §XVI). 72.6% of the time a signal is skipped for lack of a slot,
it would have outperformed the weakest currently-held position
(`docs/23` §XVII Q2). The system routinely holds a loser while a better
candidate goes unbought — a rotation problem, not a stock-picking problem.

## 16. What hard decision did you make?

Rejecting `MAX_POSITIONS=5` (Q7/Q10) despite it looking like an obvious win
on the surface diagnostic (+8.89% vs +5.62% full-window CAGR) — every
instinct says "more slots capture more of the opportunity §16/§17 already
proved exists," and the TEST-window/stress-scenario result said no anyway.
Following the gate over the intuitive story is the hard version of this
project's core discipline. The same pattern repeated with the rank-
replacement recalibration (`docs/23` §XIX): mechanically fixed two real
bugs, got the mechanism firing (28 trades, clean exits) — and still didn't
ship it, because full-window CAGR nearly halved even though the narrow
TEST window improved. A mechanical PASS that degrades the full picture is
still a reject.

## 17. Why do other traders pass and we fail (or vice versa)?

No direct comparative data against other traders/systems exists in this
project — that would require an external benchmark this repo doesn't track.
What the internal data does show: this strategy's advantage over a naive
approach is concentrated in the entry-admission gate (Q15), and its
documented failure mode is a specific stress signature
(`crash_v_recovery`) that has independently killed three unrelated
candidate improvements (`crash_v_recovery_recurring_killer_20260710`,
`docs/26` bottom line) without ever being root-caused. If another
systematic trader handles that exact market shape differently — faster
regime detection, tighter capital deployment during a crash, a genuine
rotation trigger (which this system's own research concluded doesn't exist
computably in real time, `rotation_logic_synthesis_20260710`) — that would
be the plausible mechanism for a pass/fail gap. That's informed speculation,
not evidence — flagging it as such rather than presenting it as proven.

## 18. How did you overcome bugs?

By pattern, not luck: every serious bug in this project's history was found
by *disbelieving a good-looking number* and tracing it to source, not by
code review alone. Examples: the `RANDOM_ELIGIBLE` arm coming back
byte-identical to `PURE_RS` across all 3 seeds was the tell that exposed
`backtest/engine.py` silently re-sorting signals regardless of intended
ranking (`docs/23` §XIV) — a working experiment result was itself the bug
signal. The rank-replacement mechanism firing 0 times ever was traced to a
self-defeating gate condition plus thresholds that were literally never
checked against the real data distribution (`docs/23` §XIX) — two bugs,
found by asking "why zero," not by staring at the threshold values. The
market-close overrun this week was found the same way — a consistent,
unexplained ~600-second gap in the logs every day, traced to `requests`'
`timeout=` being a per-read timeout rather than a total-request deadline.
Common thread: treat an anomaly (unexpectedly clean number, unexpectedly
inert mechanism, unexplained timing gap) as a bug lead first, not as luck.

## 19. How do you make a proper system?

Per `docs/29`'s explicit pipeline change, adopted after the backtest/live
mismatch was found: **Idea → Implementation Fidelity check → PASS →
Robustness Gate (TRAIN/TEST + 4 stress scenarios) → PASS → Production.**
Not the old two-step (idea → gate → pass). "Robustness on the wrong
implementation is still the wrong answer" — direct quote, and the reason
Track B (new research) was frozen entirely until Track A (fidelity: does
backtest actually simulate what live does) closed. Rule 3 (`docs/29`) adds:
one source of truth — no logic implemented twice in backtest and live
independently, because that's exactly the failure class that caused the
mismatch in the first place (the indicator stack in `composite.py` vs
`engine.py` is flagged as still-duplicated today, currently equivalent but
one accidental edit away from silently diverging again).

## 20. What is the purpose of this system?

To generate real, positive, risk-adjusted return trading Indian equities
systematically — using a relative-strength/trend-confirmation entry signal
that's been adversarially shown to beat random selection, deployed through
a portfolio-construction layer that's been repeatedly shown to leave real
money on the table (Q15) more than the signal itself does. The project's
own stated research-phase framing (`docs/29`) draws the line precisely:
Phase 1 ("is there a real edge") is answered yes. The current phase's
purpose is narrower and more honest than "make more money" — it's "build a
research platform trustworthy enough that a PASS actually means something,"
because for a real stretch of this project's history, it didn't.

## 21. Are you able to do this or not?

Able to run the process — adversarial testing, bug-tracing, honest
REJECT/PASS verdicts, live-bug fixes with local verification before
deploy — yes, and the doc trail above is the evidence, not a claim. Able to
guarantee the strategy is profitable — no, and nobody honestly can from
where this stands today: FULL-window CAGR is currently negative (-4.64%,
Q1), the TEST-window win is real but recent and short (18 months), the
single highest-value missing experiment (Q13's ground-truth rebuild) has
never been run, and 18 of 46 load-bearing parameters are still Unknown
(Q12). The honest answer is "the process is sound and the tooling now
catches what it used to miss; whether the strategy itself has durable edge
outside the tested window is not yet proven either way."

## 22. What is the key learning, one line?

The system's edge lives almost entirely in *who gets admitted* (the entry
gate), not in *how well the admitted are ranked or sized* — and every time
this project trusted a good-looking full-window or diagnostic number
without the TRAIN/TEST + stress-scenario gate, it turned out to be an
artifact, not an edge.

---

## Open items this doc surfaces, not yet closed

1. **`docs/23` §XI star item — the ground-truth rebuild (Q13) has never
   been run.** Fully specified, zero dependency on anything else. Highest-
   priority missing experiment in the project.
2. **`crash_v_recovery_recurring_killer` — never root-caused** (Q17), has
   killed 3 independent levers.
3. **Regime-tiered sizing (`a731b5d`) — shipped, not gate-validated** (Q9).
4. **1-day and next-day-specific up/down/flat stats — never pulled** (Q5/Q6).
   Cheap to add if the user wants a literal answer rather than the nearest
   proxy (5d-horizon correlation).
5. **18 of 46 parameters (`docs/23`) still Unknown** — no individual test
   on record (Q12).
