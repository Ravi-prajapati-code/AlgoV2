# 21 — Research Priorities (Ranked by Expected Information Gain)

**Date**: 2026-07-07. Ranked by how much each item reduces uncertainty about the central question
— *can the proven RS signal become benchmark-beating portfolio alpha?* — NOT by expected CAGR.
Cross-references: E-numbers from docs/19, Issues from docs/16.6.

| # | Item | Question it settles | Info gain | Effort | Risk |
|---|---|---|---|---|---|
| 1 | **Churn-cohort audit (E3)** + **charges validation (E6)** | Is the 3.2pp friction structural or concentrated in fixable whipsaw loops? Is the friction model even right? | Very high | Hours, measurement-only | None |
| 2 | **Sub-period permutation stability** (rerun docs/16.6's permutation test on 2022-24 and 2025-26 separately) | Is the selection signal still alive in the recent regime, or was it all 2022-23? Nothing else matters if the signal is dead forward. | Very high | ~2h compute, script exists | None |
| 3 | **GOLDBEES→cash ablation (E5)** | How much of the track record is a one-off gold windfall? Sets the honest baseline for everything else. | High | Trivial | None |
| 4 | **Idle-cash parking, arm (a) risk-free (E2a)** | Deterministic +~1.75pp corrective — establishes the true achievable baseline before judging any other lever. | High (recalibrates all comparisons) | Small | None |
| 5 | **Uniform-sizing ablation (E1)** | Is stranded capital (largest measured leak) recoverable without destroying the risk profile? The central construction hypothesis. | High | Small + full gate run | Gate-controlled |
| 6 | **Trade-concentration + bootstrap CIs** (docs/16.6 queue item 1, still pending) | Is the whole track record ~5 lucky fills? Puts error bars on every number cited anywhere. | High | Hours | None |
| 7 | **Idle-cash parking, arm (b) BULL-regime ETF (E2b)** | Does "protective cash" have option value exceeding its carry cost? Quantifies the timing overlay's true price. | Medium-High | Small + gate run | Gate-controlled |
| 8 | **Entry-lag ablation (E4)** | Is the slot-open timing effect (t≈1.7) real? Only worth running if E3 implicates entry clustering. | Medium | Moderate | Gate-controlled |
| 9 | **Point-in-time universe rebuild** (docs/16.6 Issue 1) | External validity of *everything* above. Highest importance for truth, ranked here only because effort is large and it gates publication, not direction. | Very high (validity) | Large | None |
| 10 | **Forward shadow ledger** (docs/16.6 Issue 2) | The only clean out-of-sample evidence still obtainable. Start it now regardless of everything else — it accrues value with calendar time. | Compounding | Small to start | None |

## Sequencing logic

Items 1–4 are measurement/deterministic (a few days, zero strategy risk) and recalibrate the
baseline. Item 5 is the one hypothesis-bearing experiment the leakage report justifies today.
Items 7–8 are conditional on what 1 and 5 find. Item 9 runs in parallel as capacity allows; item
10 should start immediately because only calendar time produces it.

## What is deliberately absent

New indicators, ML ranking, parameter sweeps, exit/stop changes (cleared by measurement,
docs/20 §6), slot-count changes (rejected, docs/16), regime-detector redesigns (rejected sweep
history, docs/05). Absence is a finding: the audit says the signal and the sell-side are fine —
every open question is deployment, friction, and validity.
