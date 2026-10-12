# 79 — momentum_atr: Stock-Level Trend Filter Comparison (SMA200 / SMA50>SMA200 / SMA100 / B+MOM)

Date: 2026-10-12 · Pre-registration: addendum 2 in
`research/momentum_atr_strategy_variants/README.md` (written before
running) · Code: `run_trend_filters.py` · Results:
`trend_filters_results.json`, `trend_filters_comparison.csv`.

**Status:** Price > SMA200 (T1) **passes** the gate and becomes the
preferred shadow-run candidate. It replaces B+MOM as the recommendation
because it is simpler and passes all stress tests. T2 and T3 REJECT.

**Setup.** Each arm = BASE_LIVE plus ONE eligibility condition, evaluated
at close d (score set to 0 if it fails). Everything else is identical to
docs/76. **The current score already requires close > SMA50**, so
"Price > SMA200" and "Price > SMA50 AND Price > SMA200" are the same rule
here.

| Filter | FULL CAGR / MDD / Sharpe | TRAIN CAGR / MDD | TEST CAGR / Sharpe / MDD | Start offsets (better Calmar; MDD range) | 0.30% cost FULL / TEST CAGR | At 50%: CAGR / MDD | Bear-day return | Gate |
|---|---|---|---|---|---|---|---|---|
| Current (BASE_LIVE) | 53.1 / −42.6 / 1.05 | 37.5 / −42.6 | 87.3 / 1.56 / −35.9 | — ; −42.6…−42.7 | 38.7 / 71.5 | 27.8 / −23.5 | −18.1% | ref |
| **T1 Price > SMA200** | **62.6 / −37.6 / 1.20** | **46.5 / −37.6** | **119.3 / 1.88 / −33.5** | **10/10 ; −37.6…−37.8** | **46.6 / 85.9** | **31.5 / −20.4** | **+1.6%** | **PASS** |
| T2 SMA50 > SMA200 | 65.8 / **−35.7** / 1.25 | **65.7** / −35.7 | 66.1 / 1.39 / −32.8 | 10/10 ; −35.7…−35.9 | 52.6 / **32.0** | 33.3 / −19.7 | −9.2% | REJECT (TEST Sharpe/Calmar, chop + gap stress) |
| T3 Price > SMA100 | 54.7 / −41.8 / 1.08 | 40.1 / −41.8 | 86.2 / 1.56 / −34.5 | 10/10 ; −41.6…−41.8 | 40.7 / 72.6 | 28.4 / −23.0 | −16.9% | REJECT (gap_down PF 0.900 < 0.904) |
| B+MOM (ret252 > 0 & > SMA200) | **69.2** / −37.0 / **1.29** | 56.9 / −37.0 | 106.9 / 1.78 / −34.0 | 10/10 ; −36.9…−37.3 | **54.0** / **91.5** | **34.2 / −19.9** | +7.3% | REJECT (gap_down PF 0.903, docs/76) |

Stress PF (crash / bear grind / chop / gap-down):

| Arm | crash | bear grind | chop | gap-down |
|---|---|---|---|---|
| Current | 1.94 | 0.90 | 0.98 | 1.00 |
| T1 | 2.00 | 0.94 | 1.00 | 0.95 |
| T2 | 2.78 | 0.96 | 0.83 | 0.80 |
| T3 | 1.88 | 0.89 | 0.99 | 0.90 |
| B+MOM | 2.14 | 1.18 | 0.95 | 0.90 |

Block-bootstrap 95% CI of the Sharpe difference vs the current strategy:
- T1: [−0.02, +0.12, +0.35]
- T2: [−0.38, +0.17, +0.76]
- T3: [−0.04, +0.02, +0.09]
- B+MOM: [−0.23, +0.20, +0.72]

Realized P&L on trades entered 2025-26: current −₹12.9k; T1 +₹38.5k;
T2 +₹15.4k; T3 −₹22.4k; B+MOM +₹87.1k.

## Findings

1. **T1 Price > SMA200 is the most robust filter tested.**
   - It is the only arm that passes every gate check, all 4 stress
     scenarios included.
   - MDD improves from −42.6% to −37.6% at every one of the 10 start
     dates.
   - TEST improves (119% vs 87%), and the gain survives 0.30% costs in
     both FULL and TEST.
   - Bear-day return turns from −18.1% to +1.6% annualized.
   - Turnover and cost are essentially unchanged (51.8× vs 52.0×; ₹65.7k
     vs ₹60.9k).
   - Its numbers are identical to docs/76's post-hoc run of the same rule
     (B+MOM_SMA200_ONLY), which confirms reproducibility.
2. **T2 SMA50 > SMA200 ("golden cross" structure) gives the lowest FULL
   drawdown (−35.7%) and the best TRAIN, but it is weak out of sample.**
   TEST is 66% vs 87%, Sharpe 1.39, and at 0.30% cost TEST falls to 32%.
   It also fails chop and gap stress. The moving-average crossover lags,
   so it admits new uptrends late and keeps stocks that have already
   turned down while SMA50 is still above SMA200. The best in-sample,
   weaker out of sample: the classic overfitting signature.
3. **T3 Price > SMA100 does almost nothing.** It is too close to the
   existing SMA50 rule to filter many names (Sharpe CI [−0.04, +0.09]).
4. **B+MOM vs T1.** Adding "12-month return > 0" to the SMA200 rule
   raises FULL and TRAIN returns and bear-grind stress, but lowers TEST
   (107% vs 119%) and fails gap-down stress. The extra condition is not
   clearly adding value, and it is one more thing to get wrong. **T1 is
   the simpler and more robust choice.**
5. Your premise holds in part. "Price > SMA200 is the strongest single
   improvement" is true on robustness. On raw drawdown, SMA50 > SMA200 is
   slightly lower (−35.7% vs −37.6%), but it pays for that out of sample.

## Honest limits on the T1 "pass"

- **It is not independent evidence.** I had already seen this exact
  rule's result (docs/76 post-hoc neighbour check) before pre-registering
  it here. The pre-registration formalises it; it does not re-test it on
  new data.
- **About 55 configurations have now been examined** across docs/74-79.
  Some pass is expected by chance, and T1's Sharpe CI lower bound is
  −0.02, which does not exclude zero.
- **Survivor-only universe:** an SMA200 rule on survivors avoids "falling
  knives" that, in reality, sometimes went to zero. That may *understate*
  the rule's real benefit, but it is unmeasured.
- Against the README's paper-test criteria: it passes at 0.30% (yes); it
  beats on MDD with a higher Sharpe (yes). The neighbour criterion is
  mixed: SMA100 fails gap stress by 0.004; ret126 & SMA200 passed
  (post-hoc, docs/76); B+MOM fails by 0.001. That is a plateau of
  near-passes, not a cliff.

## Recommendation

**Shadow-run T1 (Price > SMA200 added to the current rules), not B+MOM.**
Collect at least 30 closed shadow trades on live data, with no orders.
Keep the fixed 40–50% account allocation (docs/75) as the risk layer:
with it, T1's backtest is 31.5% CAGR / −20.4% MDD. No live code or
config was changed; the adoption decision is the user's.
