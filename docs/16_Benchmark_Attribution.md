# Benchmark Attribution — Gate 1

**Investment Committee Gate 1: "Is there any alpha?"** A raw CAGR number doesn't answer this — a
strategy with beta 1.4 that outperforms in a bull market has demonstrated leverage, not skill. This
report computes CAPM beta, Jensen's alpha, tracking error, information ratio, and up/down capture
against three external benchmarks plus one internal one, using `scripts/benchmark_attribution.py`
(run 2026-07-07).

**Inherited caveat (read `14_Universe_Verification_Report.md` first)**: the strategy return series
below comes from the current 100-symbol universe applied to the full 2022-2026 backtest window,
which is confirmed-unvalidated evidence per docs/14 — the sensitivity test there could not establish
whether this inflates or deflates the true numbers. Every figure below should be read as "based on
the existing (unvalidated) evidence base," not a final word.

**Risk-free rate**: no risk-free data source exists anywhere in this project. A flat 6.5%/year
assumption is used (rough India 91-day T-bill/repo-rate proxy). Jensen's alpha is sensitive to this
choice — treat it as directionally informative, not exact to the decimal.

**Benchmark choices**: raw index levels weren't used — tradeable ETF proxies were, since the actual
Gate 1 question ("what would a passive investor have earned") is answered more directly by an
investable instrument (real tracking error, real expense drag) than a theoretical index level.
MONIFTY500.NS (Nifty 500 proxy) only has price history from 2023-10-06 onward — its comparison
window is shorter than the other three.

## Results

| vs. | Window | N days | Strategy CAGR | Benchmark CAGR | Excess Return | Beta | Jensen's Alpha | Tracking Error | Info Ratio | Up Capture | Down Capture |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Nifty 50 | 2022-01 → 2026-07 | 1115 | +12.49% | +7.66% | **+4.83pp** | 0.45 | +5.76%/yr | 16.82% | **0.28** | 52.6% | 41.6% |
| Nifty Midcap 150 (ETF proxy) | 2022-03 → 2026-07 | 1066 | +15.98% | +21.05% | **-5.07pp** | 0.50 | +2.73%/yr | 16.38% | **-0.26** | 52.9% | 49.3% |
| Nifty 500 (ETF proxy) | 2023-10 → 2026-07 | 679 | +12.01% | +12.52% | **-0.51pp** | 0.55 | +2.81%/yr | 15.95% | **-0.00** | 60.0% | 52.8% |
| Equal-Weight Universe (own 100 symbols) | 2022-01 → 2026-07 | 1115 | +12.49% | +17.20% | **-4.71pp** | 0.51 | +1.17%/yr | 16.05% | **-0.25** | 57.7% | 53.6% |

### Risk-adjusted comparison (added 2026-07-07, per Investment Committee follow-up)

The excess-return table above answers "did it earn more," not "did it earn more per unit of risk
taken" — the more relevant question for an allocator, and the reason this section was added before
proceeding to Gate 2. Max Drawdown, Sharpe, Sortino, and Calmar are computed on each benchmark's
exact overlapping window (same date range and day count as above), at the same 6.5%/year risk-free
assumption:

| vs. | Strategy MDD | Benchmark MDD | Strategy Sharpe | Benchmark Sharpe | Strategy Sortino | Benchmark Sortino | Strategy Calmar | Benchmark Calmar |
|---|---|---|---|---|---|---|---|---|
| Nifty 50 | -23.67% | -16.47% | 0.41 | 0.13 | 0.56 | 0.18 | 0.53 | 0.47 |
| Nifty Midcap 150 | -18.21% | -20.81% | 0.59 | 0.85 | 0.82 | 1.07 | 0.88 | 1.01 |
| Nifty 500 | -15.34% | -18.64% | 0.38 | 0.46 | 0.48 | 0.61 | 0.78 | 0.67 |
| Equal-Weight Universe | -23.67% | -21.44% | 0.41 | 0.69 | 0.56 | 0.86 | 0.53 | 0.80 |

**[Fact, High confidence]**: this table does not rescue the excess-return result — it sharpens it.
The strategy wins on every risk-adjusted metric against Nifty 50 (lower MDD comparison aside — see
below), but **loses on Sharpe, Sortino, and Calmar against both Midcap 150 and the equal-weight
universe** — the two benchmarks that actually represent the strategy's trading universe. Against the
equal-weight universe specifically, the strategy's own Max Drawdown (-23.67%) is *worse* than the
benchmark's (-21.44%), despite having roughly half its beta. A concentrated, lower-beta strategy is
not automatically a lower-drawdown one — that only held true against Nifty 50 and the two ETF
proxies, not against the strategy's own universe held equally.

**[Fact, High confidence] — tested 2026-07-07, result below**: the concentration-risk hypothesis
(idiosyncratic risk from holding few names dilutes a real systematic alpha; raising position count
should improve Sharpe) was tested directly with `scripts/position_count_sensitivity.py`, re-running
the full 2022-present backtest at position-count ceilings of 3 (the actual live setting —
`config/risk_config.yaml`'s `max_open_positions: 3`, not 6 as an earlier version of this document
incorrectly stated, citing the code's unused fallback default instead of the active config value),
4, 6, 8, 10, 12, 15, and 20:

| N (ceiling) | Trades | CAGR | Max DD | Sharpe | Sortino | Calmar |
|---|---|---|---|---|---|---|
| 3 (live) | 153 | +15.98% | -18.21% | 0.59 | 0.82 | 0.88 |
| 4 | 165 | **+17.91%** | **-15.73%** | **0.66** | **0.85** | **1.14** |
| 6 | 174 | +10.96% | -15.81% | 0.35 | 0.45 | 0.69 |
| 8 | 175 | +8.12% | -16.18% | 0.17 | 0.21 | 0.50 |
| 10 | 174 | +7.96% | -16.27% | 0.16 | 0.19 | 0.49 |
| 12 | 173 | +6.86% | -16.28% | 0.07 | 0.08 | 0.42 |
| 15 | 171 | +7.05% | -16.53% | 0.08 | 0.10 | 0.43 |
| 20 | 164 | +7.03% | -16.61% | 0.08 | 0.10 | 0.42 |
| *Midcap 150 ETF (benchmark)* | — | +21.05% | -20.81% | 0.85 | 1.07 | 1.01 |

**[Fact, High confidence]**: the hypothesis as originally framed is **not supported**. Trade count
stays essentially flat (153-175) across every N tested — the strategy is not finding meaningfully
more positions to hold as the ceiling rises, so no real diversification is occurring. Instead, every
metric — CAGR, Sharpe, Sortino, Calmar, even Max Drawdown — collapses monotonically from N=4 onward
and flattens out around N=12-20 at roughly a third of N=4's risk-adjusted performance.

**[Fact, High confidence] — root cause**: `backtest/engine.py` line 584,
`base_slot_cash = cash / available_slots`, sizes each new position by dividing available cash by the
full *configured* ceiling minus currently-open positions — not by the number of actual buy
candidates that day. Raising the ceiling from 3 to 20 makes `available_slots` larger almost all the
time regardless of how many positions are genuinely opened, which shrinks the dollar size of every
real trade without ever deploying the "extra" reserved capital into more names. This is a capital
dilution artifact, not a diversification effect — the mechanism needed to realize the hypothesis
(actually holding more names) isn't what changes; only the sizing arithmetic does. This also means
`17_Edge_Persistence.md` and any future capacity work should not vary `MAX_OPEN_POSITIONS` without
being aware this sizing coupling exists.

**[Opinion, Medium confidence]**: the practical implication is not "the edge is fake" — it's that
the concentration-risk defense of the Sharpe shortfall doesn't survive testing in the form proposed.

**[Fact, High confidence] — N=4 lead REJECTED by `scripts/robustness_gate.py`, 2026-07-07**: the
full-window table above made N=4 look like a clean win over the live N=3 setting. It was not — it
was an artifact of averaging over the whole 2022-2026 window. Split into train (2022-2024) and test
(2025-present), N=4 wins on train (CAGR +18.90% vs +15.20%, Sharpe 1.12 vs 1.00) and then collapses
on test, the out-of-sample period that actually matters (CAGR +4.43% vs baseline's +13.09%, Sharpe
0.39 vs 0.87, Profit Factor 1.21 vs 1.76) — worse than baseline by more than the gate's tolerance on
both Sharpe (-0.48) and PF (-0.55), and introducing train/test Sharpe instability the baseline
doesn't have. Verdict: **REJECT**. This is the same overfitting-to-the-full-window failure mode
this project has hit before (`out_of_sample_validation_20260703`, the universe-restoration
sensitivity test) — a single aggregate number over the whole backtest window hid a real
recent-period breakdown. **N=4 is not adopted. The live setting stays at 3.** The concentration-risk
hypothesis is now closed on both fronts: raising the ceiling doesn't improve risk-adjusted returns
(refuted, capital-dilution artifact) and the one config that looked better in aggregate doesn't hold
up out-of-sample either (rejected, overfitting). The Sharpe/Sortino/Calmar shortfall against the
Midcap 150 benchmark is not explained by position count at all — the mandate question from
`16.5_Investment_Mandate.md` stays open.

## Reading the results honestly

**[Fact, High confidence]**: Beta is low and consistent across all four comparisons (0.45-0.55).
This directly answers the reviewer's stated concern — this is **not** a leveraged-beta strategy
riding a bull market. Down capture (42-54%) is consistently lower than up capture (53-60%) against
every benchmark, meaning the strategy participates less in benchmark drawdowns than in benchmark
rallies — the asymmetry a regime-gated, trend-following design is supposed to produce, and it shows
up in the data.

**[Fact, High confidence]**: The picture diverges sharply depending on which benchmark is used.
Against **Nifty 50** (broad large-cap index), the strategy shows positive excess return, positive
Jensen's alpha, and a positive (if modest, 0.28) information ratio — a genuinely favorable
comparison. Against **Nifty Midcap 150** and the strategy's **own equal-weight universe** — the
benchmarks that actually match the asset class this strategy trades — excess return and information
ratio both turn **negative**. A passive investor who simply bought the Midcap 150 ETF, or who bought
every symbol in this project's own 100-stock watchlist equally, earned more in raw terms than this
strategy did over the same period.

**[Opinion, Medium confidence] — why this matters**: Nifty 50 is the wrong primary comparison for a
strategy that trades mid-cap momentum names — it's a large-cap benchmark, and this strategy's own
`03_Strategy.md` mechanism explicitly targets a different segment. Leading with the Nifty 50 number
would overstate the case. The Midcap 150 and equal-weight-universe comparisons are the more relevant
ones, and both show the strategy **underperforming a passive buy-everything alternative in raw
terms** over 2022-2026, while still carrying a small positive Jensen's alpha because its risk
(beta ≈ 0.5) is much lower than either passive alternative's.

**[Opinion, Medium confidence] — what this does and doesn't settle**: this is not evidence of zero
edge — low beta with a small positive Jensen's alpha across all four comparisons is consistent with
"real but modest risk-adjusted value, delivered with much less market exposure than a passive
alternative," which could be a legitimate value proposition (lower drawdown risk, not higher return)
rather than a pure return-maximization one. But it directly contradicts any framing of this strategy
as "beating the market" in the segment it actually trades — against the two benchmarks that match its
actual universe, a passive investor did better in absolute terms over this specific 2022-2026 window.
Whether that holds outside this window is exactly what `17_Edge_Persistence.md` needs to test.

**[Opinion, Medium confidence] — the equal-weight-universe comparison's own caveat**: this benchmark
is built from the *same* 100-symbol universe as the strategy, so it inherits the exact same
survivorship-bias question raised in docs/14 — it is not an independent, bias-free comparison the
way the three external ETF/index benchmarks are. Its result (-4.71pp excess return) should be read
as "the strategy's stock selection added negative value relative to owning the same universe passively,"
not as commentary on the universe-construction bias itself.

## Bottom line

**[Opinion, High confidence] — revised 2026-07-07 after the risk-adjusted follow-up**: Gate 1 does
not clear under any framing tested against the strategy's actual trading universe. The strategy is
low-beta (0.45-0.55, real and consistent), and shows a small positive Jensen's alpha against every
benchmark — but that alpha does not show up in Sharpe, Sortino, or Calmar against Midcap 150 or the
strategy's own equal-weight universe, where it loses on *every* metric measured, including a worse
Max Drawdown than the equal-weight comparison despite roughly half its beta. The only benchmark
against which the strategy wins cleanly on every metric — return, risk-adjusted return, and
drawdown — is Nifty 50, a large-cap index that doesn't match this strategy's actual (mid-cap)
trading universe. This is a materially weaker result than the 12.85%/12.49% headline CAGR numbers
suggest, and weaker than an initial read of "low beta, positive alpha" alone would suggest too. The
concentration-risk explanation above is a plausible, untested lead — not a mitigating finding.
See `16.5_Investment_Mandate.md` for how this changes the definition of success before proceeding to
`17_Edge_Persistence.md`.
