# Doc 42 — Research State Registry

**Date**: 2026-07-31
**Purpose**: One-stop chronological + thematic index of every research doc (docs/01–40), what was tested, and the current verdict. Read this alongside `docs/41_Architecture_Map.md` before proposing anything new, per `CLAUDE.md`'s research process — check here first whether the idea already exists, was tried, or was killed. Failed research is kept, not deleted, per the same charter.

## How to read this

Docs 01–21 are the original 2026-07-06/07 forensic dossier (Phases 1–4): built the honest baseline, found and partially fixed the universe look-ahead contamination, proved the RS signal beats random via permutation test, and located the leak in portfolio construction rather than stock-picking. Docs 22–40 are the iterative lever-by-lever research that followed, governed from doc 29 onward by a formal fidelity→gate→production pipeline. **Doc 38 is the project's most authoritative single verdict** ("not proven, leaning no, as currently built") and doc 39 is the current live operating decision. Where an earlier doc's number conflicts with a later one, the later date wins unless noted otherwise below.

## Chronological index

| # | Date | Topic | Verdict |
|---|---|---|---|
| 01 | 07-06 | Project overview, honest baseline CAGR +12.85%/Sharpe 0.83 (fails own gates, live anyway) | INFORMATIONAL |
| 02 | 07-06 | Live vs. backtest data-flow architecture | INFORMATIONAL |
| 03 | 07-06 | Strategy logic; found `ema_50` key actually holds EMA(100) | BUG FOUND (fixed doc 31) |
| 04 | 07-06 | Backtest engine/metrics; Sharpe methodology inconsistency flagged | INFORMATIONAL |
| 05 | 07-06 | History of rejected levers pre-dossier; central conclusion: nothing beats honest baseline yet | REJECTED (all) |
| 06 | 07-03/06 | TRAIN/TEST/stress validation methodology (`robustness_gate.py`) | ACCEPTED methodology; gate FAILED on then-live config |
| 07 | 07-06 | File-by-file live/dead code catalog | INFORMATIONAL |
| 08 | 07-06 | Live incident history; loser-leak (26 symbols) found, fix written | MOSTLY RESOLVED, 1 item pending |
| 09 | 07-06 | 15 ranked open bugs/gaps | UNRESOLVED (source list) |
| 10 | 07-06 | Phase 1 quant review: top 5 trades = 90.4% of P&L; entry indicators don't predict pnl | ACCEPTED descriptively, later heavily qualified (doc 13) |
| 11 | 07-06 | P0/P1/P2 severity triage (4/8/6 items) | UNRESOLVED (multiple) |
| 12 | 07-06 | 12-24mo institutional roadmap | SUPERSEDED (doc 13 disputes sequencing) |
| 13 | 07-07 | **Independent adversarial review — found universe look-ahead: watchlist revised 06-17 using each symbol's own future P&L, contaminating the entire OOS TEST window** | REJECTED (institutional capital) / ACCEPTED (limited capital only) |
| 14 | 07-07 | Universe contamination sensitivity test: restoring 33 removed losers flips TRAIN -2.19pp but TEST **+6.93pp** — opposite of naive prediction, confound not resolved | UNRESOLVED (magnitude unknown) |
| 15 | 07-07 | Economic hypothesis for why the edge should exist/persist | ACCEPTED as plausible, later partly vindicated (doc 16.6) |
| 16 | 07-07, updated 07-15 | Benchmark attribution: beats Nifty50 but loses to Midcap150 and own equal-weight universe; N=4 position-count "win" REJECTED as overfit by the gate | REJECTED (Gate 1, full-window) |
| 16.5 | 07-07 | Investment mandate definition (Sharpe>Calmar>MDD>Sortino>CAGR proposed) | UNRESOLVED (not signed off) |
| 16.6 | 07-07 | CIO adversarial review; **permutation test: real RS ranking beats all 40 random permutations, p=0.024, ~+9.6pp/yr** | REJECTED (institutional) / ACCEPTED (limited capital, forward paper) |
| 18 | 07-07 | Alpha leakage report: signal worth +9.6pp/yr gross, ~-4.9pp net vs. equal-weight — stranded capital (~27% idle) is leak #1 | ACCEPTED (measured leak map) |
| 19 | 07-07/11 | Pre-registered leak ablations; E1 DD-throttle removal **PASSED** full gate (TRAIN CAGR +13.44%→+17.98%) but kept off pending deploy decision | MIXED — E1 passed/undeployed, E2-E6 pending |
| 20 | 07-07 | Portfolio construction code audit; confirms `MAX_OPEN_POSITIONS=3` binds 90.2% of BULL days; exits/stops cleared | ACCEPTED (diagnosis) |
| 21 | 07-07 | 10-item ranked research priority list | INFORMATIONAL (plan) |
| 22 (Final Rec) | 07-07 | Closing verdict on docs 14-21: signal real, construction is the bottleneck, benchmark-beating unlikely on raw CAGR | INFORMATIONAL (mixed) |
| 22 (Handoff) | 07-08 | Session handoff toward a core-satellite wrapper concept | INFORMATIONAL |
| 23 | 07-08/10 | **46-assumption audit** (8 Proven, 12 Supported, 8 Plausible, 18 Unknown). §XIV entry attribution: REVERSE_RS best arm, RS-rank order has no positive value. §XVI: daily rank-vs-forward-return Spearman ≈ 0. MAX_POSITIONS=5 REJECTED (TEST flips CAGR-negative) | INFORMATIONAL (audit) + many embedded verdicts |
| 24 | rolling→07-22 | **Master "Rejected Forever" catalog** — dozens of dead levers by category | REJECTED (catalog) |
| 25 | 07-10/12 | Synthesis: leak is portfolio construction not stock-picking; `crash_v_recovery` identified as recurring killer of 3+ unrelated levers. Contains an unreconciled "MAX_POSITIONS=5 PASSED" update box | INFORMATIONAL, contains contradiction (see below) |
| 26 | 07-10 | Portfolio truth audit: conviction-sizing confirmed dead code; E1 DD-throttle PASS; E3 churn-cohort (later, 07-22): <31d cohort -₹95,244, 82% in MOMENTUM_DECAY | INFORMATIONAL (audit) |
| 27 | 07-11 | Standing ~30-concept strategy catalog (PROVEN/REJECTED/OPEN/UNTESTED) | INFORMATIONAL (reference) |
| 28 | 07-11 | **Found live and backtest were running different strategies** — 3 default-ON live rules (rotation, ride-winner, score-drop-exit) entirely absent from backtest, invalidating prior gate verdicts | UNRESOLVED → resolved by doc 29 |
| 29 | 07-11 | **Governance response**: freeze new research until fidelity checklist passes; all 4 items closed same day; declares all prior gate verdicts provisional | ACCEPTED (policy adopted) |
| 30 | 07-13 | Remove `IGNORE_SYMBOLS`, add origin-tagging + two-lens value model; found 2 real live bugs | ACCEPTED / DEPLOYED (paper, local) |
| 31 | 07-13 | Fixed `ema_50` mislabel (was EMA(100)) in live only — backtest was always correct, so past gate verdicts stand | DEPLOYED |
| 32 | 07-13 | EMA entry/exit sweep; `ENTRY_EMA_MEDIUM=40`+`EXIT_TREND_EMA=65` flagged as deploy candidate | SUPERSEDED by doc 33 |
| 33 | 07-14 | **Live/backtest parity checker built; found backtest's ATR/RSI used wrong smoothing (simple MA vs. Wilder's)** — re-running doc 32's EMA sweep on the fix downgrades it to a marginal/mixed result | DEPLOYED (fix); doc 32 downgraded |
| 34 | 07-21 | 8-arm entry-gate ablation on expanded 504-symbol universe: live `FULL` mode was worst of 8 arms; `PURE_RS` best, +24pp CAGR over FULL, clean gate PASS | **ACCEPTED / DEPLOYED** — `ENTRY_MODE` default → `PURE_RS` |
| 35 | 07-27 | Long-term buy-hold sleeve (RS≥85, 2mo sustained): TRAIN Sharpe 7.11 → TEST Sharpe **-0.13** | REJECTED |
| 36 | 07-28 | Universe cap to top-50-by-turnover: TEST CAGR +41.64%→**-15.91%**, 2 stress sign-flips | REJECTED |
| 37 | 07-30 | 22-Q honest retrospective; restates MAX_POSITIONS=5 as REJECTED (final word, doesn't acknowledge doc 25's contradictory update); flags regime-tiered sizing shipped-but-not-gated | INFORMATIONAL (synthesis) |
| 38 | 07-30 | **Blind chronological 2022-2026 run of current live config**: TRAIN CAGR -1.10% FAIL, TEST +41.64% PASS, FULL -4.64% FAIL. Validator's own verdict: "UNSTABLE." Bootstrap on 133 TEST trades: p=0.246 vs. mean=0 | **Not proven — leaning no, as currently built** |
| 39 | 07-30 | Production decision: run `PURE_RS` frozen, add 20%-DD kill-switch (not yet wired), out-of-domain Microcap-250 test found mirror-image TRAIN-good/TEST-bad (confounded by sector-cap artifact) | ACCEPTED (operating decision), several sub-items unresolved |
| 40 | 07-31 | Signal-level attribution: FULL rejects future winners (TEST p<0.0001 after symbol-dedupe; TRAIN p=0.26, noise). Two competing mechanisms (momentum-maturity vs. mean-reversion) not distinguished. New fidelity gap: VCP/`ema_200` never simulated in any backtest | INFORMATIONAL / mechanism color, not independent proof |

## What's actually live right now

- `ENTRY_MODE=PURE_RS` (doc 34, re-confirmed doc 38 Addendum 3) — RS-rank + safety checks only, no trend/breakout/ADX/SuperTrend gate.
- Regime-tiered sizing (`a731b5d`) — **shipped 2026-07-27 without a robustness_gate run** (doc 37 Q9 flags this directly as a Rule-2 violation, still open).
- `UNIVERSE_CAP_SIZE` flag — added disabled (doc 36 REJECTED the top-50 cap it would enable).
- DD-throttle (the docs/19/26 E1 experiment) — **still on** (removal passed the gate but was never deployed; pending decision since 2026-07-11, unresolved 20+ days).
- 20%-drawdown kill-switch — decided (doc 39) but **not implemented**, not wired into the run loop.
- No hard stop-loss, no profit-lock, no ML veto (`ML_ENABLED=False`) — all explicitly rejected/dormant by design.

## Proven / durable (don't re-litigate without new evidence)

- Entry signal beats random selection: permutation test p=0.024 (docs/16.6, 23 §XIV), ~+9.6pp/yr, replicated multiple times.
- RS-rank *magnitude/order* carries no value — REVERSE_RS ties or beats forward-RS repeatedly (docs/23, 35, 40). Don't propose RS-magnitude-weighted sizing/entry without addressing this.
- Portfolio construction (slot scarcity, stranded capital, friction), not stock selection, is where the gross edge is lost (docs/18, 20, 25).
- Exits/stops are clean — not a leak (docs/18, 20, 24).
- Broad universe beats narrow: two independent narrowing attempts (liquidity-floor tightening, top-50-turnover cap) both failed the same way — "2/2 dead," don't propose a third universe-narrowing-by-liquidity idea without a materially different mechanism.
- `crash_v_recovery` stress scenario has killed at least 3-4 unrelated levers (extension filter, liquidity tightening, streak-position preference, momentum-decay grace period) — never root-caused. Any new lever should be pre-screened against this scenario specifically.

## Unresolved contradiction in the record

**MAX_POSITIONS=5**: docs/23 §XVIII and doc 37 (latest, most authoritative) call it REJECTED (TEST CAGR flips +1.26%→-2.85% under the fixed engine). A 2026-07-11 update box inside doc 25 claims a later re-run PASSED (TRAIN +14.69%→+22.06%, TEST +5.98%→+10.66%), but doc 27 — dated the same day — still lists N=5 as untested/OPEN, and no doc ever reconciles the three. **Treat as REJECTED per doc 37** until someone re-runs it cleanly on the current engine and writes a doc that explicitly addresses the contradiction.

## Consolidated open items (deduped across both digest passes)

1. **Ground-truth rebuild using only Proven+Supported assumptions** (docs/23 §XI ⭐) — specified since 2026-07-08, still not run as of doc 37/40. Single highest-priority missing experiment on record.
2. **`crash_v_recovery` root cause** — never diagnosed, only dodged.
3. **DD-throttle removal deploy decision** — passed gate 2026-07-11, no decision recorded in 20 days.
4. **Regime-tiered sizing gate validation** — shipped without a robustness_gate run, violates doc 29 Rule 2.
5. **MAX_POSITIONS=5 contradiction** — needs one clean rerun + a doc that resolves it explicitly.
6. **Live-server drift/dry-run check** — blocked by `Permission denied (publickey)` SSH since doc 30 (07-13); per prior memory, server confirmed 8 commits behind as of 07-31, not yet pulled.
7. **20%-drawdown kill-switch** — decided doc 39, not implemented.
8. **N-of-M ensemble entry gate** — proposed doc 38 Addendum 3 / doc 39, addresses FULL-vs-PURE_RS regime tradeoff (FULL wins in stress, PURE_RS wins on calendar TRAIN/TEST); not built.
9. **Cleaner out-of-domain rerun** — doc 39's Microcap-250 test had a sector-cap confound (all symbols map to sector "Unknown"), not re-run cleanly.
10. **VCP / `ema_200` backtest-fidelity gap** — never simulated in any backtest ever (doc 40); `TREND_GATE_200_ENABLED` is a structural no-op regardless of live setting.
11. **Forward paper/live checkpoints** — 2026-08-30 / 2026-10-30 / 2027-01-30 (doc 39). None have arrived. This is the project's only remaining clean (non-reused) OOS evidence source.
12. **Dead-code keep-or-delete decisions** — `portfolio/optimizer.py` (ImportError), `risk/manager.py`, `strategy/stock_ranker.py`, `strategy/market_filter.py`, `broker/paper.py` — scheduled for "Q2" in doc 12, never actually decided.
13. **Sharpe methodology inconsistency** (`backtest/metrics.py` population variance vs. `walk_forward.py` sample variance) — flagged docs/04, 06, 09, 11, 13, never fixed.
14. **Schema drift** (`ALTER TABLE` never back-ported) — flagged docs/02, 09, 11, 12, never confirmed fixed.
15. **`LIQUIDBEES_TARGET_WEIGHT` NameError** — dormant, contingent on `LIQUIDBEES_ENABLED` staying False; not fixed.

## Bottom line for "does it work"

Doc 38 is the standing answer: **not proven, leaning no, as currently built.** TEST-window numbers (+41.64% CAGR) that got cited across docs 34-37 as validation were real under the gate methodology used, but (a) the same config loses money TRAIN and FULL-window, (b) the TEST window has been used as a pass/fail filter in 15+ prior experiments so it's no longer clean OOS, (c) an out-of-domain rerun (doc 39) found the mirror-image pattern, and (d) bootstrap CI on TEST trades spans zero (p=0.246). The operating decision (doc 39) is to run `PURE_RS` frozen and treat forward time from 2026-07-30 as the only remaining valid evidence. Any new proposal should be evaluated against "does this move us closer to real forward evidence," not "does this improve the TEST-window backtest number" — that well is contaminated.
