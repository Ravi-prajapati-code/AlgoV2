# 32. Entry/Exit EMA Sweep — 2026-07-13

## Origin

User question: "Entry: Above EMA50, Exit: Below EMA65 — do you think we need to change the
condition?" Investigating that question surfaced a real bug first (`docs/31_EMA50_Mislabel_Fix.md`
— live's `ema_50` key actually held `EMA(100)`). Once fixed, the user asked for a full sweep across
entry and exit EMA-related levers to get empirical ground truth on what to keep, rather than
answering the original question by intuition.

All runs: `scripts/robustness_gate.py --env DD_THROTTLE_DISABLED=true --env
SECTOR_DURABILITY_WEIGHT=1.0 --env <LEVER>=<VALUE>` (the two `--env` flags reflect the already-live
`.env` overrides, required so the gate's config-drift check doesn't flag them as undeclared drift).
Baseline in all cases is the current live default (`ENTRY_EMA_MEDIUM=50`, `ENTRY_EMA_LONG=100`,
`EXIT_TREND_EMA=50`, `EXIT_TREND_CONFIRM_DAYS=2`).

**Data-integrity note**: the first 3 candidates were launched in parallel to save time. Two of
three crashed mid-run with `sqlite3.OperationalError` (disk I/O error / readonly database) — all
gate runs share a single fixed scratch-DB path (`outputs/robustness_gate_scratch/`), so concurrent
invocations collide. Worse, one run that completed *without* crashing (`EXIT_TREND_EMA=40`)
produced numbers later proven wrong by a clean serial rerun (contaminated TRAIN/TEST/FULL results,
not just the stress stage). **All results below are from strictly serial runs only** — `robustness_gate.py`
must not be parallelized against itself until it's given a per-run scratch path.

## Results

### EXIT_TREND_EMA (`strategy/signals.py` TREND_BREAK basis, baseline 50)

| Value | Verdict | TEST Sharpe | TEST PF | Stress notes |
|---|---|---|---|---|
| 30 | **REJECT** | 0.60 (base 0.61) | 1.45 | `prolonged_sideways_chop` PF 0.92→0.66 |
| 40 | PASS | 0.61 (identical) | 1.46 | byte-identical to baseline — no-op |
| 50 (baseline) | — | 0.61 | 1.46 | — |
| **65** | **PASS** | 0.61 (identical) | 1.46 | TRAIN/FULL marginally better, TEST unchanged, stress identical |
| 100 | PASS | 0.61 (identical) | 1.46 | byte-identical to 65 — TREND_BREAK stops firing past ~65, other exits (stop/take-profit/regime) dominate |

**Finding**: TREND_BREAK is only load-bearing in a narrow band below ~40. Above that, other exit
mechanisms fire first and the lever goes dormant. 65 is marginally the best value tested but the
effect is small; 40-100 are all functionally safe.

### ENTRY_EMA_MEDIUM (`strategy/entry.py` "close > EMA_MED" gate, baseline 50)

| Value | Verdict | TEST Sharpe | TEST PF | Stress notes |
|---|---|---|---|---|
| 30 | PASS (weaker) | 0.56 (base 0.61) | 1.43 | crash_v_recovery improved (-1.83%→+3.66%) but everything else down |
| **40** | **PASS (best)** | **0.73** | **1.55** | TEST CAGR +9.74%→+12.04%, MDD 12.71%→11.56%; crash_v_recovery slightly worse |
| 50 (baseline) | — | 0.61 | 1.46 | — |
| 65 | **REJECT** | 0.90 | 1.70 | huge gains but `prolonged_sideways_chop` PF 0.92→0.81 — classic overfit signature |

**Finding**: 40 is a genuine, moderate improvement with no stress failure. 65 looks spectacular on
TRAIN/TEST but is the same overfit pattern as the already-rejected sector-blacklist and
streak-priority levers (`docs/24`) — reject on principle even though the gate numbers look great.

### ENTRY_EMA_LONG (`strategy/entry.py` "EMA_MED > EMA_LONG" gate, baseline 100)

| Value | Verdict | TEST Sharpe | TEST PF | Stress notes |
|---|---|---|---|---|
| 65 | PASS (weaker) | 0.56 | 1.43 | identical numbers to `ENTRY_EMA_MEDIUM=30` — narrowing the med/long gap from either side has the same restrictive effect |
| **150** | **PASS (best)** | **1.08** | **1.88** | TRAIN MDD flat (22.80%→22.73%); `prolonged_sideways_chop` PF 0.92→0.89 (within the 0.1 tolerance, but closer to the edge than any other passing candidate) |
| 100 (baseline) | — | 0.61 | 1.46 | — |
| 200 | PASS | 0.84 | 1.67 | good TEST gains but TRAIN MDD degrades 22.80%→26.47% — worse risk profile than 150 |

**Finding**: 150 is individually the single best-looking result of the whole sweep, but it's also
the piece that turns out not to combine safely (see below) — it's already spending most of its
`prolonged_sideways_chop` margin on its own.

### EXIT_TREND_CONFIRM_DAYS (`strategy/signals.py`, baseline 2)

| Value | Verdict | Notes |
|---|---|---|
| 1 | **REJECT** | worse TRAIN/TEST/FULL; `prolonged_sideways_chop` PF 0.92→0.63 (the worst stress failure of the whole sweep) |
| 2 (baseline) | — | — |
| 3 | PASS | byte-identical to `EXIT_TREND_EMA=65` — same "TREND_BREAK loosened" effect via a different mechanism |

**Finding**: confirming faster (1 day) is unambiguously bad. Confirming slower (3 days) is safe but
redundant with `EXIT_TREND_EMA=65`/`100` — pick one lever, not both, since they saturate the same
effect.

## Combination test (does this stack?)

Individually-passing "best" values from each entry dimension were combined:

| Combo | Verdict | TEST Sharpe | Stress notes |
|---|---|---|---|
| `ENTRY_EMA_MEDIUM=40` + `ENTRY_EMA_LONG=150` + `EXIT_TREND_EMA=65` | **REJECT** | 0.73 (same as MEDIUM=40 alone — LONG=150 added nothing on TRAIN/TEST) | `prolonged_sideways_chop` PF 0.92→0.81 |
| `ENTRY_EMA_MEDIUM=40` + `EXIT_TREND_EMA=65` (LONG dropped) | **PASS** | 0.73 | stress identical to baseline |

**Finding**: `ENTRY_EMA_LONG=150` is the destabilizing piece. It passes alone (with only 0.03 of
PF margin to spare on `prolonged_sideways_chop`), but stacked with `ENTRY_EMA_MEDIUM=40` it pushes
the combined entry gate loose enough to fail the same scenario that killed `ENTRY_EMA_MEDIUM=65`
outright. Gate-passing individually does not imply safe-to-combine — each candidate lever spends
some of the same shared stress-tolerance budget.

## Ground truth / recommendation

- **Keep as-is (REJECTED, do not touch)**: `EXIT_TREND_EMA` below ~40, `ENTRY_EMA_MEDIUM=65`,
  `ENTRY_EMA_LONG=65`, `EXIT_TREND_CONFIRM_DAYS=1`. All fail the same `prolonged_sideways_chop`
  gate or are strictly worse than baseline.
- **Candidate for deployment (passes solo AND combined)**: `ENTRY_EMA_MEDIUM=40` +
  `EXIT_TREND_EMA=65`. This is the one combination in the whole sweep that both improves TEST-window
  metrics meaningfully (Sharpe 0.61→0.73, PF 1.46→1.55, MDD 12.71%→11.56%) and clears every stress
  scenario with zero degradation. Not yet deployed — this doc records the gate evidence only, per
  `docs/29_Project_Governance.md`'s Fidelity→Gate→Prod pipeline; a deploy decision is separate and
  should also account for the still-open live/backtest fidelity gaps tracked elsewhere.
- **Do not deploy solo**: `ENTRY_EMA_LONG=150`. Looks best in isolation but is not safe once
  combined with any other passing entry change — treat as fragile, not as ground truth.
- **No-op, leave at default**: `EXIT_TREND_EMA=40/100`, `EXIT_TREND_CONFIRM_DAYS=3` — all
  functionally inert or redundant with the 65 candidate.

## Open follow-up

- `ENTRY_EMA_MEDIUM=40` + `EXIT_TREND_EMA=65` has not been run through the full deploy checklist
  (live dry-run, SSH-blocked as of 2026-07-13 — see prior session memory). Gate-PASS is necessary,
  not sufficient.
- Finer granularity between 40 and 65 for `ENTRY_EMA_MEDIUM` (e.g. 45, 55) was not tested — the
  sweep used coarse round-number steps per the original request scope.
