# 20 — Portfolio Construction Audit

**Date**: 2026-07-07. Code-level audit of every mechanism between "ranked candidate list" and
"final portfolio", with measured behavior from `scripts/alpha_leakage_audit.py`. References are to
`backtest/engine.py` unless stated; live (`portfolio/manager.py`) shares the same design.

## 1. Slot system

- `MAX_OPEN_POSITIONS = 3` (source of truth: `config/risk_config.yaml` `max_open_positions: 3` —
  NOT the code fallback default of 6 in `config/settings.py`).
- `available_slots = MAX_OPEN_POSITIONS - len(open_positions)` gates both new buys and
  rank-replacement.
- **Measured**: slots at max on 90.2% of BULL days, 65.0% of BEAR days. The slot system is
  binding nearly always — the ranking's cross-sectional information is consumed three names at a
  time.
- **Known dead code**: `BacktestEngine.__init__`'s `max_selected` parameter is assigned and never
  read. Any script passing it silently no-ops (docs/16).

## 2. Position sizing — the stranded-capital mechanism (Leak 1)

- `base_slot_cash = cash / available_slots` (line ~584). Two documented consequences:
  1. The denominator is *configured capacity remaining*, not the number of candidates actually
     being bought — proven by the N-sweep (docs/16): raising the ceiling dilutes every real trade.
  2. At N=3 with slots mostly full, the day-to-day effect is smaller, but after multi-exit days
     the freed cash is split across all reopened slots even if only one candidate qualifies.
- ~~Conviction multiplier `score_to_size_factor`~~ **CORRECTION 2026-07-07: dead code.**
  `score_to_size_factor`/`SCORE_BUCKETS` is imported by both `backtest/engine.py` and
  `portfolio/manager.py` but **called by neither** — confirmed empirically when a gate run with
  `SIZE_FACTOR_UNIFORM=1.0` produced bit-identical results to baseline, then by grep for call
  sites. The conviction-sizing tier system documented in `strategy/scoring.py` (and in earlier
  versions of this audit) does not exist in the running system. This is the third dead-code
  discovery in the sizing path (`max_selected`, the `score_to_size_factor` import, and the
  fallback `MAX_OPEN_POSITIONS=6` default), which collectively indicate the sizing subsystem's
  documentation and code have drifted apart and should be reconciled deliberately.
- Drawdown throttles: `base_slot_cash *= 0.50` beyond 10% drawdown, `*= 0.25` beyond 15%
  (lines ~587-589), plus `DRAWDOWN_KILL_SWITCH_PCT = 0.18` halting new buys entirely.
- **Net measured effect of this stack**: mean exposure 73.1% (71.6% BULL) despite full slots;
  ~27% of equity idle at 0% modeled yield. This is the single largest identified leak.

## 3. Cash model

- Idle cash earns **zero** in the backtest. No liquid-fund or risk-free parking is modeled, while
  the attribution's own risk-free assumption is 6.5%/yr. The backtest therefore *understates* what
  the live system could earn trivially (E2a) — the rare bias that runs against the strategy.
- Safe-haven leg (GOLDBEES) earns its ETF return + `SAFE_HAVEN_YIELD_ANNUAL` treatment; equity
  cash does not.

## 4. Candidate selection at slot-open (Leak 3)

- Buys: qualified signals sorted by score, top `available_slots` taken, executed same day.
- **Measured**: the 111 selected entries underperform the 1,288 passed-over qualified signals by
  ~1.7pp over the following 21 sessions (benchmark-adjusted; t≈1.7). Selection moments cluster
  after exits. Suggestive of an extension/timing effect at the exact moment of purchase —
  see E4 before acting.

## 5. Rank replacement / rotation

- Weakest open position (by rs_rank) can be replaced by a sufficiently stronger candidate;
  defensive positions excluded.
- Trade ledger shows rotation-driven exits are a small cohort; exit-continuation audit found no
  aggregate alpha left behind by exits of any reason except tiny-n TREND_BREAK. **No evidence of
  a rotation leak worth touching.**

## 6. Exits and stops — audited and CLEARED

- All-exit mean forward-21d benchmark-adjusted return: **+0.13%** (n=151). By reason:
  MARKET_CRASH_PROTECTION -1.94% (n=31 — these exits added value), TRAIL_EXIT +1.04% (n=54),
  STOP_LOSS -0.12%, BEAR_SWING exits negative (good), TREND_BREAK +6.06% (n=3, immaterial).
- The regime-aware trailing stop (deployed 2026-07-01) and static stops are performing as
  designed. **Recommendation: freeze this subsystem.** Any "improve the exits" proposal must
  first explain away these measurements.

## 7. Execution & friction (Leak 2)

- Slippage `fixed_pct` 0.1%/side; turnover 8.46x/yr → ~1.69pp/yr. Charges ₹8,828 over 4.5y on
  ~₹127k average equity → 1.54pp/yr; 12% of gross P&L. Combined ≈ **3.2pp/yr**.
- Driven by churn: 61% of closed trades hold <31 days and that cohort is net-negative before
  friction (-₹75k realized). The strategy pays ~20% of its gross return to trade at a frequency
  whose short-duration component loses money — while its own attribution shows the edge lives in
  31+ day holds.
- Live divergences already documented (GTT unfillable-LIMIT gap, historical fill bugs) mean live
  friction is, if anything, *worse* than modeled — E6 reconciles.

## 8. Structural summary

The construction was designed as a risk-management stack (conviction tiers, drawdown throttles,
tight slots, regime gating) and it succeeds at the exit side — but the audit shows its cost is
paid on the deployment side: capital is chronically withheld from a signal that is empirically
worth ~9.6pp/yr on deployed capital, and what is deployed churns at a friction rate of 3.2pp/yr.
**The bottleneck is not what the system sells or when — it is how much of the portfolio the
signal is ever allowed to run, and how much of the running is wasted on sub-31-day round trips.**
