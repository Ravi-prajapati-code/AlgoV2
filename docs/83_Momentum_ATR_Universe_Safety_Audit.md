# 83 — momentum_atr Universe Safety Audit (P0 forced exit, P1 universe mismatch, P2 exit price, P3 docs)

Date: 2026-10-12 · Status: **fixes prepared and tested, NOT committed or
deployed** (awaiting approval).
- Fix branch: `fix/momentum-atr-universe-safety` (git worktree
  `../AlgoV2-universe-safety`, uncommitted).
- Research: `research/momentum_atr_universe_audit/`.
- No live configuration, broker order, production data, or MAIN behaviour
  was changed.
- Server access was read-only, except for one temporary export file
  created and deleted in the home directory.

## 1. Confirmed findings vs. unverified assumptions

| # | Claim | Status | Evidence |
|---|---|---|---|
| P0 | A holding removed from the universe gets no score and is sold at 09:17 | **CONFIRMED (code path)** | `scripts/precompute_momentum_atr_ranking.py`: `symbols = get_all_symbols()`, holdings never added → `compute_live_scores` omits it → `execution.run_daily`: `rank_of.get(sym, 10**9) > 3` → `RANK_RULE_EXIT` |
| P0 | It has happened historically | **NOT observed** | All 29 closed live trades checked against `universe_history`: none was removed while held |
| P0b | A held stock with **missing data** (fetch stall) is also sold as "rank 10⁹" | **CONFIRMED (code path)** | `compute_live_scores` drops symbols with no or short data; same `rank_of.get` default |
| P1 | Live = 504 static + 16 dynamic = 520; research = 153; overlap 146 | **CONFIRMED** | live-only = **374**, not the earlier figure of 358: 358 static-only + 16 dynamic-only, and none of the 16 are in the research set |
| P1 | The dynamic manager uses MAIN's trade P&L and prefers low volatility | **CONFIRMED** | `db/universe_repo.get_strategy_stats` reads `trades` (MAIN, trading.db); `config/universe_config.yaml` weights `volatility_atr` = 1/ATR% at 0.10 |
| P1 | The static 504 list was built from MAIN-specific research | **PARTLY WRONG** | git `faffa3a` (2026-07-21): the list is **the full Nifty 500 as of that date**. The "removed Adani / PSU / FMCG" notes in the `watchlist_nse.py` docstring describe an *older* curated list and are themselves stale (Adani, NTPC, ONGC are in the current list) |
| P2 | A missing fill price records the trade at entry price (zero P&L) | **CONFIRMED (code path)**, never observed | `execution.py:354` `closes.get(sym, pos.entry_price)`; reached only if the broker confirms the fill with `avg_price` = 0 **and** the symbol is missing from `closes` |
| P3 | `data/universe.py` says "119 stocks" | **CONFIRMED** | the docstring of `get_all_symbols()` |

## 2. Root causes

- **P0:** universe membership (a *new-entry* concern, owned by MAIN's
  manager) and position management (the *exit* decision) share one
  input. The precompute only scores the universe, so leaving the
  universe is indistinguishable from a rank drop. The same channel turns
  a data gap into a "rank 10⁹" sale.
- **P2:** the fallback chain `avg_price → yesterday's close → entry_price`
  fabricates a price instead of representing "unknown".
- **P1:** the backtest universe is "names with full 9:16 minute data"
  (153), chosen for data reasons. Live is "Nifty 500 (2026-07-21
  snapshot) + MAIN's dynamic core".

## 3. Files inspected / changed

**Inspected:**
- `momentum_atr/{scoring,execution,risk,models,shadow}.py`
- `scripts/precompute_momentum_atr_ranking.py`
- `scripts/run_momentum_atr_live.py`
- `db/momentum_atr_repo.py`, `db/universe_repo.py`
- `data/universe.py`, `config/watchlist_nse.py`,
  `config/universe_config.yaml`
- `universe/manager.py`, `reconciliation/gate.py`
- dashboard readers of `net_pnl`
- server crontab and logs (read-only)

**Changed** (worktree only; 5 files, +236 / −12, plus 1 new test file):

| File | Change |
|---|---|
| `momentum_atr/scoring.py` | `compute_live_scores(..., diagnostics=None)`: optional; records why each symbol was left out. Output identical when not passed. New `build_ranking_inputs(universe, held)` |
| `scripts/precompute_momentum_atr_ranking.py` | Scores universe ∪ OPEN holdings via `build_ranking_inputs`; logs/alerts held-symbol cases |
| `momentum_atr/execution.py` | Rank-exit loop: a held symbol **absent from `closes`** → `HOLD_DATA_MISSING` + alert (not a sale). `_execute_sell`: avg_price → live LTP (*estimate*, tagged + alerted) → else **UNRESOLVED** (exit_price/P&L NULL, position closed, no cash credit). New `resolve_unresolved_exit()` |
| `db/momentum_atr_repo.py` | `get_trade()`, `resolve_trade_exit()`: atomic, only matches a still-unresolved row, so it is idempotent |
| `data/universe.py` | Docstring: 504 symbols as of 2026-10-12 + dynamic CORE. Code unchanged |

**How each held-stock case is classified at 08:50:**

| Case | Result at 09:17 |
|---|---|
| Scored normally, including after leaving the universe | Existing rank rule decides (rank > 3 → sell) |
| Below the 20-day volume floor (real data) | Close kept, no score → rank-exited **exactly as before and as in the backtest** |
| No data / < 50 bars / NaN score / **last bar older than today's reference bar** | Not in `closes` → **held + alert** |

Unchanged: score formula, SMA50/SMA200 eligibility, volume floor, top 3,
rank > 3 exit, ±3% swap, top-up, realloc split, sizing, allocation cap,
kill switch, order timing, costs. MAIN does not call any changed
function; `get_all_symbols()` output is unchanged (tested).

## 4. Automated tests (worktree)

`tests/test_momentum_atr_universe_safety.py`, 18 tests (fake broker and
temp DB only):
1. Held stock removed from the universe is scored.
2. A legitimate rank drop still sells.
3. Missing and stale data are flagged. 3b: data missing at 09:17 → held
   and alerted.
4. A held stock restored to the universe.
5. Below volume floor → genuine exit. 5b: multiple holdings, the
   ineligible one exits, the others are kept.
6. Restart: positions come from the DB; a same-day precompute re-run
   skips, so the ranking is unchanged.
7. Repeated 09:17 runs → no duplicate orders.
8. Partial fill → ledger untouched.
9. A universe-removed top-3 holding → **no orders**.
10. `compute_live_scores` without diagnostics is identical;
    `get_all_symbols()` is unchanged.

P2 tests:
- LTP estimate.
- No price → unresolved, not zero.
- Resolve after restart, exactly once (cash credited once).
- Resolve refuses a normal trade or a non-positive price.
- Delayed broker status → exact price.

**Mutation check:** run against the *unpatched* code, **11 of 18
fail**. All of those are bug/feature tests. The 7 that pass on both
versions are the deliberate "behaviour must not change" tests.

**Full suite in the worktree: 303 passed, 2 skipped.** The 2 skipped are
the shadow-vs-research parity tests, which need `research/`, absent in
the worktree.
- Initially 11 *pre-existing* tests failed in the worktree on **both**
  old and new code. Cause: no local `db/reporting.db`, so the pre-trade
  gate blocked buys.
- After copying the local `reporting.db` into the worktree: 303 passed.
  No regression is attributable to the change.

## 5. Universe reconciliation (reproducible)

Script: `research/momentum_atr_universe_audit/run_universe_audit.py`.
Sources:
- `universe_snapshot_20261012.json` (server, 2026-10-12)
- the server price cache (read-only export, 2026-10-12)

| Item | Count |
|---|---:|
| Static list (`watchlist_nse.ALL_SYMBOLS`, Nifty 500 as of 2026-07-21) | 504 |
| Dynamic CORE (`universe_active`) | 53 |
| Dynamic-only (not in static) | 16 |
| **Live universe** | **520** |
| Research universe (full 9:16 minute data) | 153 |
| Research ∩ live | 146 |
| Research-only (EASEMYTRIP, EQUITASBNK, MIDHANI, MOIL, RATNAMANI, SURYODAY, UJJIVANSFB) | 7 |
| Live-only | 374 |
| Dynamic-only names ever in the research universe | 0 |
| Symbols in server price cache / with full 2022-2026 history | 2,467 / 432 |
| Symbols excluded as corporate-action artifacts (\|daily move\| > 40%) | 20 |

**Average daily eligibility funnel, 2022-11 → 2026-05 (cached daily
data):**

| Universe | Has a bar | ≥ 300k vol | + above SMA50 | Eligible (+ above SMA200) |
|---|---:|---:|---:|---:|
| A research 153 | 148.7 | 138.8 | 80.1 | 67.5 |
| B static 504 | 456.7 | 354.2 | 203.0 | 165.9 |
| C all cached | 1138.8 | 539.1 | 285.6 | 189.2 |

Live, 2026-10-12 (server, read-only): 520 → 397 scored → **67
eligible**: 123 fail volume, 316 below SMA50, 14 below SMA200.

**Membership dating:**
- The static list has no dated history before 2026-07-21 (a single git
  commit created the 504).
- Dynamic membership history (`universe_history`) starts 2026-06-11.

## 6. Universe backtests

Same live strategy everywhere (mom50 × ATR%, SMA50 + SMA200, vol20 ≥ 300k,
top 3, rank exit, swap, top-up, 0.15%/side). Fills at the **next daily
open** for every universe (only 153 names have 9:16 data). Window
2022-11-01 → 2026-05-07 (871 sessions), TRAIN = first 70%.

| Universe | FULL CAGR / MDD / Sharpe | PF | TRAIN CAGR | TEST CAGR / MDD | 10 start offsets (CAGR) | Trades / turnover | Costs | Avg empty slots |
|---|---|---|---|---|---|---|---|---|
| **A research 153** | **64.0 / −39.0 / 1.23** | 1.63 | 44.6 | 112.1 / −33.0 | 47.5 … 76.9 | 1305 / 50× | ₹63,364 | 0.30 |
| B static 504 ⚠ look-ahead | **−32.0 / −85.4** / −0.45 | 0.80 | −23.7 | −38.1 / −61.3 | −39.5 … −28.4 | 1541 / 72× | ₹33,069 | 0.46 |
| C all cached ⚠ partial survivorship | **−29.0 / −85.1** / −0.32 | 0.80 | −27.2 | −44.8 / −76.6 | −41.4 … −29.0 | 1477 / 71× | ₹29,399 | 0.49 |
| D live 520 (static + dynamic) | **NOT RUN**: dynamic membership is unknowable before 2026-06-11 | | | | | | | |

By regime (annualized):
- A: BULL +35.8, SIDEWAYS +53.4, BEAR +7.6, HIGH_VOL +127.3
- B: BULL −61.0, SIDEWAYS +74.7, BEAR −103.0, HIGH_VOL +26.3

**Sanity checks:**
- A's daily-open result (64.0 / −39.0) is close to docs/79's 9:16-fill
  result (62.6 / −37.6), so the pipeline and data are consistent.
- The losses in B are real price action, not data errors. The biggest
  losers are buys right after parabolic spikes (IFCI +60% in 4 sessions,
  ATGL +50%, MRPL +30%), and no symbol has ≥ 3 open-vs-prior-close gaps
  above 20%.
- Within B, the **115 trades in the 153 research names made +₹49,766**.
  The **356 trades in the extra names lost −₹1,26,978** (win rate 27%).
  By 5-session run-up before entry: > 30% → −₹39,942 at a 21% win rate;
  15–30% → −₹25,494.

**Interpretation (with limits stated):**
- The strategy's backtested edge is **specific to the 153-name research
  universe**. Applied to a wider Nifty-500-type universe, momentum × ATR%
  ranks speculative spikes highest and loses heavily, in TRAIN, in TEST,
  and at every start offset.
- B's look-ahead bias (today's Nifty 500 applied back in time) *flatters*
  B, because survivors are included. It still loses.
- **Live trades the 520-name universe, closer to B than to A:** 10 of 29
  closed live trades were in dynamic-only names, and both current
  holdings (SHANTIGOLD, MOREPENLAB) are dynamic-only.
- **Caveat:** D (the actual live universe history) cannot be reproduced,
  and C/B are not exact historical memberships. This evidence is strong
  but not a reconstruction of live.

## 7. Historical data limitations

- No point-in-time NSE or Nifty 500 membership in the repo or on the
  server.
- The static list has no history before 2026-07-21.
- Dynamic history only from 2026-06-11.
- The price cache holds only names the project ever fetched (stocks
  delisted before then are absent).
- The cache's adjustment status is undocumented; 20 symbols with > 40%
  one-day moves were excluded, and smaller unadjusted actions could
  remain.
- 9:16 fills are available only for the research 153.

## 8. Remaining risks

1. **The universe mismatch (§6) is the largest open risk**, larger than
   anything P0 or P2 fixes. The live universe is not the one the strategy
   was validated on, and the evidence says wider universes lose.
2. The universe manager can still change which *new* names are buyable
   on any weekday at 18:05 / Friday 18:30, using MAIN's P&L and a
   low-volatility preference.
3. Same-run edge case: a universe-removed holding sold by the swap rule
   could be re-bought by top-up the same morning if it is still top 3.
   This is rare and follows the existing swap/top-up rules.
4. The BUY path has the same pattern as P2 (`execution.py:308`: fill
   price falls back to `closes.get(sym, 0.0)`, i.e. a possible entry
   price of 0). Not changed here (out of scope); recommended as a
   follow-up.
5. The precompute and execution changes **must be deployed together**.
   An old precompute with a new execution would *hold* volume-floor
   failures instead of exiting them.
6. Server disk 85% used (2.3 GB free).

## 9. Deployment recommendation (requires human approval; nothing deployed)

1. **Approve and deploy the P0 + P2 patch** (branch
   `fix/momentum-atr-universe-safety`) as one commit, outside market
   hours, then run a dry-run precompute. Low risk: no strategy rule
   changes; it only removes two ways the system could act on
   non-strategy signals.
2. **Decide on the universe before relying on the strategy further.**
   The evidence supports restricting momentum_atr to a universe like
   the one it was validated on (the research 153 or a similarly liquid
   large/mid-cap set). Detaching it from MAIN's dynamic manager is part
   of that.
   - This is a strategy-scope change and needs its own pre-registered
     test before any live change.
   - Until then, consider the fixed 40–50% allocation (docs/75) as the
     risk layer.
3. Follow-up: apply the P2 treatment to the BUY fill-price fallback.
