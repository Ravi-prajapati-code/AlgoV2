# Economic Hypothesis — Gate 0

**Investment Committee Gate 0: "Why should this continue working?"**

This must be answerable *before* backtest quality is trusted, per the reviewer's framing: if the
answer isn't convincing, even a clean backtest deserves skepticism. Labeled `[Fact / Opinion /
Unknown, Confidence]` throughout — most of this section is necessarily Opinion, since it's a
forward-looking causal claim, not a measurement.

## One-sentence hypothesis

**[Opinion, Medium confidence]**: This strategy is a cross-sectional price-momentum strategy on
liquid NSE mid/large-cap equities — buy stocks already in a confirmed uptrend that are outperforming
the broader universe on relative strength, filtered by a market-regime gate that stands aside (or
switches to safe-haven-only) when the index itself is in a downtrend. The mechanism (per
`03_Strategy.md`): entry requires trend confirmation (price above long EMAs), a minimum relative-
strength rank versus the universe, a liquidity/turnover floor, and a volatility (ATR%) filter; a
composite score ranks candidates; a regime detector gates the whole system into BEAR/safe-haven mode
when the market index itself breaks down.

## Why should this continue working?

**[Opinion, Medium confidence]**: Price momentum is one of the most extensively documented
anomalies in equity markets (Jegadeesh & Titman 1993, and a large replication literature since,
including in emerging markets). The leading explanations in the academic literature are behavioral,
not risk-based in the CAPM sense — which matters, because a risk-based explanation implies the
"edge" is compensation for bearing a real risk (and could vanish if that risk stops being priced),
while a behavioral explanation implies a persistent friction that doesn't require investors to be
compensated for anything:

- **Underreaction to information** — investors, analysts, and passive/index flows are slow to fully
  incorporate news (earnings, order wins, capacity expansion) into price, so a real fundamental
  improvement shows up as a *drift*, not a jump — which is exactly what a trend/momentum filter is
  designed to capture.
- **Slow information diffusion in less-covered names** — Indian mid-caps have materially lower
  analyst coverage and institutional ownership than large-caps or US equities. Slower diffusion of
  information is a standard explanation for why momentum tends to be *stronger*, not weaker, in
  mid-cap and emerging-market equities relative to large-cap developed markets.
- **Disposition effect / anchoring** — retail-dominated flow (a materially larger share of NSE
  turnover than in developed markets) is well-documented to under-react early in a trend (anchoring
  to a reference price, reluctance to buy what's already risen) and over-react late (chasing), which
  is consistent with a persistent, exploitable drift in the middle of the move.

## Who is on the other side of these trades?

**[Opinion, Medium confidence]**: Primarily two groups, on both entry and exit:
1. **Sellers into the uptrend** — investors anchored to a lower reference price taking profits too
   early, or investors who mispriced the fundamental catalyst and are only gradually re-rating it.
2. **The regime-gate counterparty on the way down** — when this strategy exits or refuses to enter
   during a BEAR regime, the "other side" is whoever is still buying the dip on the assumption of
   mean reversion, which the strategy's own regime detector is betting against for that window.

**[Unknown]**: This has not been tested — there is no analysis in this project of who actually holds
NSE mid-cap float (retail % vs. DII vs. FII vs. promoter), which would be the direct evidence for or
against this claim. This is an assertion from the general literature applied to this market, not a
finding specific to this strategy's actual trade set.

## Why don't arbitrageurs remove this edge?

**[Opinion, Medium confidence]**: Several standard limits-to-arbitrage arguments apply, and are
plausibly *stronger* here than in developed large-cap markets:
- **Capacity/liquidity constraints** — mid-cap NSE names have limited average daily traded value;
  a momentum strategy trying to scale into a name already trending up pushes against its own entry
  price and against other momentum-following capital doing the same thing, capping how much
  institutional capital can pursue the same signal in the same names before the edge is arbitraged
  away by their own footprint. (This is exactly what `16_Capacity_and_Liquidity.md`, still to be
  built, needs to quantify for *this* strategy's actual position sizes.)
- **Short-selling frictions in Indian mid-caps** — stock lending/borrowing for shorting is
  materially less liquid and more expensive on NSE than in US markets, especially in mid-caps,
  which removes one of the two arbitrage mechanisms (shorting overpriced/overextended names) that
  would otherwise compress a momentum anomaly.
- **Institutional mandate constraints** — many large domestic institutional investors (mutual funds,
  insurers) operate under mandates that discourage rapid rotation or "buying high," which structurally
  keeps a class of large, price-insensitive capital from competing away a pure momentum signal even
  when it is visible.

**[Unknown]**: None of this has been measured for this specific universe (e.g., actual
borrow-availability data, actual mandate-driven flow data). These are standard arguments from the
literature, offered as *plausible*, not verified for this specific 100-symbol universe.

## Why is the effect persistent, and what inefficiency creates it?

**[Opinion, Medium confidence]**: The claim is not that momentum is a permanent free lunch — it's
that the specific frictions above (retail-dominated flow, thin coverage, thin borrow, mandate
constraints) are structural features of the Indian mid-cap market that change slowly, not
anomalies that get arbitraged away in a normal multi-year cycle. The honest caveat: momentum
strategies globally are also known for **crash risk** — sharp, fast reversals during regime
transitions (exactly the scenario this project's own stress-test suite and regime-gate design are
trying to defend against) — which is consistent with, not contradictory to, a behavioral/limits-to-
arbitrage explanation: the same slow-moving capital that fails to arbitrage the drift away also fails
to react fast enough when the trend breaks, and momentum portfolios take the loss in a
concentrated burst rather than a smooth give-back.

## What would falsify this hypothesis

**[Opinion, Medium confidence]** — listed here so it's checkable, not just asserted:
- If benchmark-relative analysis (`16_Benchmark_Attribution.md`) shows the strategy's beta to Nifty
  is materially above 1 and alpha collapses to ~0 once beta is accounted for, the "edge" is just
  leveraged beta exposure, not the momentum mechanism described above.
- If persistence testing (`17_Edge_Persistence.md`) shows the *mechanism* (win rate, avg
  winner/loser, holding period) is unstable across sub-periods even when returns look superficially
  similar, that undermines the "slow information diffusion" story — a real structural inefficiency
  should produce a reasonably stable trade profile, not one that keeps changing shape.
- If capacity analysis (`18_Capacity_and_Liquidity.md`) shows the strategy's actual position sizes
  are already a large fraction of average daily traded value, the "capacity constrains arbitrageurs"
  argument cuts both ways — it also caps how much this strategy itself can scale, which matters for
  whether this is a viable capital allocation, separate from whether the edge is real.

## Bottom line

**[Opinion, Medium confidence]**: The hypothesis is a standard, literature-grounded one (momentum +
limits-to-arbitrage in a retail-dominated, thin-coverage, short-constrained mid-cap market) — it is
plausible and worth testing further, but it is asserted, not measured, for this specific universe and
trade set. It should not be treated as established until Gates 1-3 either corroborate it (stable
mechanism, real alpha net of beta, viable capacity) or contradict it.
