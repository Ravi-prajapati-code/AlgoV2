# Production Readiness Plan — 2026-07-30

**Context**: `docs/38` concluded NOT PROVEN, leaning no, as a "blind 2022 investor" — and that no further backtesting on 2022-2026 data can change that, because that data has already been used to make 15+ keep/reject decisions. The only clean test left was framed as "freeze and observe forward for 6-12 months." That is correct as far as it goes, but it is not a full answer to "I need the system in an acceptable state today, not in a year." This doc splits the ask into the part that's answerable today and the part that genuinely needs more evidence, and gives a concrete plan for both — instead of either (a) pretending more backtesting proves something it can't, or (b) offering only "wait and see."

## Part 1 — What runs today (a decision, not a hedge)

**Config**: `ENTRY_MODE=PURE_RS`, current live parameters (full Nifty-500 universe, regime-tiered sizing), frozen. This is the best of what has been tested — confirmed again this session by re-gating it against the old strict-AND gate under current honest costs (see `docs/38` Addendum 3): `PURE_RS` wins, `FULL` is REJECTed. It is not proven in an absolute sense, but "best of what's been tested, frozen, and risk-capped" is a real, defensible thing to run today. That's the decision.

**Blocking item — live/local drift check**: attempted to SSH into the trading server (`ubuntu@3.109.104.170`) to confirm the deployed code actually matches this local `PURE_RS` default. Got `Permission denied (publickey)` — no access from this session. This has bitten the project before ([[server_drift_reconcile_deploy_20260717]] found a config live at 50% while the deploy commit claimed 40%). **Cannot honestly call anything "final" until this is checked.** Needs the user to either grant SSH access or run the check directly:
```
git -C ~/AlgoV2 rev-parse HEAD
grep ENTRY_MODE ~/AlgoV2/.env
```
and compare `HEAD` against local `main`, and confirm no `ENTRY_MODE` override sits in the server's `.env` (local has none; the default is `PURE_RS`).

**Operational kill-switch** (ops risk control — not an in-strategy backtest lever, distinct from the previously-rejected drawdown-gate experiment, which was about backtest performance, not live risk ops):
- **Halt new entries and force manual reassessment at 20% drawdown from live equity peak.** Chosen as the point between the TEST-window MDD (18.77%, the good window) and the TRAIN-window MDD (30.13%, the bad one) — stops before the system re-enters "bad-window" territory, not after.
- Existing positions are not force-liquidated at the trigger — it stops new risk, not an emergency exit. (Separately, the existing regime-flip forced-exit logic still runs regardless.)
- This needs to actually be wired into the live/paper run loop as a real guard, not just a policy on paper — that's an implementation task, not done yet.

**Standing discipline, unchanged from docs/38**: no more parameter changes justified by "TEST window improved." That data is used up.

## Part 2 — What can move the needle on confidence, starting today

Waiting for the calendar isn't the only way to get evidence the project hasn't already seen. Two things can be run now:

**A. Out-of-domain validation (the real unlock).** `docs/38` treated "uncontaminated data" as synonymous with "future data." That's incomplete — data that was simply never in the tuning set is also uncontaminated, regardless of when it happened. Confirmed feasible this session: `broker/upstox.py:_resolve_instrument()` resolves *any* NSE trading symbol generically via `InstrumentMapper` / `data/instruments/nse_instruments.json`, not just the 504 symbols in `config/watchlist_nse.py`. Plan: build a symbol list of NSE-listed, sufficiently-liquid stocks **outside** the current 504-symbol universe (a market-cap tier below Nifty 500, e.g. companies ranked roughly 501-750 — the "Microcap 250" range — since Nifty 500 itself already subsumes Midcap150/Smallcap250), fetch full 2022-2026 history for them, and run the exact same `PURE_RS` config through the same TRAIN/TEST engine, unmodified. If the entry-admission mechanism is real signal and not overfit to this specific 504-symbol set, it should show *some* edge on this set too. If it doesn't, that's a real downgrade to the verdict, today, not in a year.
- **Caveat, stated plainly**: this answers "is the mechanism real or tuned-to-this-exact-list" — it does not answer "will next year look like TEST did." It's also not spotless: same class of point-in-time-membership uncertainty the gate script already warns about, and a different market-cap tier has different liquidity/volatility characteristics, so it's evidence, not proof, either direction.
- **Status: done this session.** 242-symbol Nifty Microcap-250 list built (only 2/251 overlapped the current 504-symbol universe — genuinely never tuned on), full history fetched live via the same Upstox pipeline, `PURE_RS` run unmodified through the same engine:

```
TRAIN 2022-01-01->2024-12-31  CAGR +5.24%   Sharpe 0.38   MDD 21.36%  WR 39.2%  PF 1.44  N=222
TEST  2025-01-01->2026-06-04  CAGR -11.79%  Sharpe -0.49  MDD 32.91%  WR 26.7%  PF 0.71  N=90
FULL  2022-01-01->2026-06-04  CAGR -2.10%   Sharpe 0.03   MDD 47.78%  WR 37.2%  PF 1.12  N=301
```

**This is the mirror image of the tuned-universe result.** On the tuned Nifty-500 set: TRAIN is bad (-1.10%), TEST is great (+41.64%). On this never-tuned-on set, over the *exact same calendar windows*: TRAIN is the good one (+5.24%) and TEST is the bad one (-11.79%). If the entry mechanism carried a real, durable edge, it should not flip which period is "the good one" just because the underlying stock universe changed — that's the signature of a strategy riding a period-specific factor/style tailwind (small/microcap momentum did well in 2022-2024, badly in 2025-2026 — opposite of what large/midcap RS-momentum did), not a mechanism with edge that transfers across universes. This is independent, corroborating evidence for the "not proven" verdict, arguably stronger than anything gate-tested so far, because it wasn't obtained by tuning on this data at all.

**Confound, must be stated plainly**: every out-of-domain symbol resolves to sector `"Unknown"` (no sector metadata exists outside the curated watchlist), so the portfolio's sector-concentration cap treats the entire 242-symbol universe as a single sector — the cap fired well over 1,000 times during the FULL run, materially throttling diversification in a way the real 504-symbol/dozens-of-sectors universe never experiences. This inflates the FULL-window MDD (47.78%, worse than the tuned universe's 40.40%) and likely distorts the exact CAGR/Sharpe numbers in both windows. It does **not** obviously explain the TRAIN/TEST *flip* itself (the cap applies similarly across both sub-periods within this run), but it means these exact numbers should be read as directional, not precise. A cleaner rerun with sector caps disabled for this universe is a natural follow-up, not done this session — flagged rather than chased, to avoid turning this into another open-ended tuning loop.

**B. Bootstrap/permutation significance check on existing TEST-window trades (N=133).** Quantifies whether +41.64% CAGR / Sharpe 1.58 is statistically distinguishable from noise at this sample size, or well within what chance alone produces. Cheap, run today.
- **Framed correctly**: this can only *downgrade* confidence (reveal the result is statistically indistinguishable from noise) or leave it unchanged. It cannot upgrade anything to "proven" — it's the same already-used data, just examined more rigorously. Treat it as a fragility probe, not new validation.

**C. Ensemble / weighted-consensus entry gate** (the user's proposal from earlier this session). Not a hunch — this session's `FULL`-vs-`PURE_RS` re-validation found a genuine regime split: the strict AND-gate beats `PURE_RS` in trend-driven stress scenarios (crash-recovery, sideways chop) and only loses badly in the real calendar windows, which were a strong directional run. Reads as: a hard AND-gate can't distinguish "chop it should veto" from "real trend-period trades it shouldn't." A partial-consensus (N-of-M) gate is a plausible fix. Scoped as: build the variant, run it through the same `robustness_gate.py` stress suite that's used for everything else. Any resulting gate-PASS gets the same caveat as `PURE_RS` itself — still same historical data, still needs forward tracking, not "proven" on a backtest pass alone.

## Part 3 — Roadmap with predefined checkpoints (not a silent wait)

**Today / this session**:
- [x] Out-of-domain symbol list + historical fetch — 242 Microcap-250 symbols, 2/251 overlap
- [x] Out-of-domain TRAIN/TEST/FULL run — mirror-image result vs tuned universe (see above), corroborates NOT PROVEN
- [x] Bootstrap significance check on TEST-window trades — 95% CI spans zero, p=0.246, not significant (docs/38 Addendum 4)
- [ ] Live-server drift check (blocked on SSH access — `Permission denied (publickey)` from this session, needs user)
- [ ] Wire the 20%-drawdown kill-switch into the run loop

**Next 1-2 weeks**:
- [ ] Build + gate-test the N-of-M ensemble entry gate
- [ ] Optional cleaner out-of-domain rerun with sector-cap confound removed, if the flip result above is worth pinning down more precisely
- [ ] Report all of the above honestly, including any negative results, and fold into docs/38's verdict rather than a separate silo

**Checkpoint 1 — 2026-08-30 (1 month)**: live/paper drawdown vs. the 20% kill-switch, any early divergence from what TEST predicted. No parameter changes triggered by this alone unless the kill-switch itself fires.

**Checkpoint 2 — 2026-10-30 (3 months)**: compare realized forward Sharpe/CAGR against the TEST-window numbers. Predefined go/no-go: if forward Sharpe is materially negative or the kill-switch has fired, that's real signal at 3 months, not "too early to tell."

**Checkpoint 3 — 2027-01-30 (6 months)**: full re-assessment combining forward-track results, the out-of-domain result, and the bootstrap result into an updated verdict — supersedes `docs/38` at that point.

**What this plan deliberately does not do**: run more 2022-2026 same-universe backtests hoping for a better number. That was `docs/38`'s core finding and it still holds — everything in Part 2 either uses data that was never tuned on (A), quantifies fragility rather than claiming proof (B), or is a new, separately-gated candidate (C), not a re-ask of the same question.
