# 30. Ignored Holdings Removal — Phase 1 Architecture Review

Requested: remove the "ignored holdings" feature, treat every broker
position as part of one strategy portfolio, tag origin
(Strategy/Manual/Imported) as informational-only.

## 1. What actually exists today

Correcting the premise first: there is no dedicated "Ignored Holdings"
subsystem — no DB table, no model field, no API, no UI toggle. It is
one Python constant:

```python
# config/settings.py:241
IGNORE_SYMBOLS = ["LT.NS", "HCLTECH.NS", "IRFC.NS", "CAMS.NS"]  # CAMS: 3 trades 0% WR structural loser
```

Every touchpoint, verified by grep + read, not assumed:

| File | Line | Effect |
|---|---|---|
| `strategy/signals.py:36` | exit loop | held ignored symbols skip exit evaluation entirely |
| `strategy/signals.py:129,146` | entry loop | ignored symbols excluded from entry candidates (never bought) |
| `runner/daily_runner.py:209` | `sync_portfolio_with_broker` | raw broker positions filtered by `IGNORE_SYMBOLS` **before** DB sync — ignored symbols never become a `Position` row |
| `scripts/reconcile_positions.py:74,88` | mismatch audit | same filter, paired correctly with the sync above |
| `monitoring/gtt_coverage.py:38` | GTT audit | ignored symbols excluded from "must have a stop" check (correct — strategy doesn't manage them) |
| `backtest/engine.py`, `backtest/reporter.py`, `robustness_gate.py` | — | **zero references.** Backtest never talks to a broker; `IGNORE_SYMBOLS` there only means "never enter this ticker." No portfolio-value path exists for it to contaminate. All historical gate/CAGR/Sharpe numbers are unaffected by this feature. |

`db/models.py::Position` has no `origin` field today — adding one is a
pure addition, not a migration of existing data.

## 2. Two real bugs found, not hypothesized

### 2a. `portfolio_value()` already double-standards ignored symbols — this is a live bug, not a design choice

`portfolio/manager.py:204-214`:

```python
def portfolio_value(self, prices: dict) -> float:
    if self.broker:
        try:
            live_val = self.broker.get_portfolio_value()   # <- raw broker total
            if live_val > 0: return live_val
        except:
            pass
    open_position_value = portfolio_invested_value(self.open_positions, prices)  # <- DB-tracked only
    return self.cash + open_position_value
```

In live mode this returns the broker's **raw total account value**,
which *includes* the market value of every `IGNORE_SYMBOLS` holding.
Meanwhile `self.open_positions` (loaded from DB) *excludes* them,
because they were filtered out at sync time (§1).

This `portfolio_value()` number is the sizing/risk denominator
everywhere: `MAX_STOCK_ALLOCATION_PCT` per-position cap, the
DD-throttle drawdown calc off `peak_value`, and — on first run with no
snapshots — it becomes `initial_capital` itself (`_load_state`,
line 141), baking any ignored holding's value into the strategy's P&L
baseline forever.

Concretely: if the broker account holds e.g. ₹5L of `LT.NS` (ignored)
alongside ₹2L of real strategy capital, `portfolio_value()` reports
₹7L, and a single new strategy position can size up to 34% of ₹7L —
more than the entire actual strategy capital — while the position
count/exposure bookkeeping only knows about the ₹2L side. This is a
present-tense sizing bug, independent of anything this task changes.
**Whether the account currently holds any of the 4 listed symbols
determines whether this is live right now or latent** — worth
confirming before Phase 3 regardless of which design direction is
chosen.

### 2b. `IGNORE_SYMBOLS` conflates two unrelated concepts

`CAMS.NS` isn't a manual/legacy holding — the inline comment says
`3 trades 0% WR structural loser`. That's a **permanent do-not-trade
verdict**, the same category as `docs/24_Rejected_Forever.md`'s
sector-blacklist/streak-priority entries, just living in the wrong
list. `LT.NS`, `HCLTECH.NS`, `IRFC.NS` read as the actual
manual/legacy-holdings case the task describes.

These have different correct futures. A blacklist entry should stay a
blacklist entry (never re-enterable, symbol-level, applies in backtest
too) regardless of what happens to broker-position handling. Folding
`CAMS.NS` back into "just another strategy position, origin is
informational" would silently reopen a proven-negative lever — the
opposite of what removing this feature is supposed to achieve
(simplicity + correctness).

## 3. The one real design fork

The task spec states: *"Origin must NEVER affect cash calculation,
allocation, portfolio value, returns, performance metrics, risk
calculations."*

Given §2a, this requirement — applied literally — doesn't fix the
sizing bug, it generalizes it. Today, 4 named symbols can inflate the
sizing denominator. Under "every broker position counts, origin is
cosmetic," *any* manual trade the user ever makes through the broker
app directly would do the same thing: real cash leaves the account,
real market value sits in the position, and if that value counts
toward `portfolio_value()` while sizing/CAGR/Sharpe are supposed to
measure the *strategy's* skill, every future `robustness_gate.py`
comparison becomes a blend of algo decisions and unrelated manual
trades. That's not hypothetical — it's the same mechanism as 2a, just
unbounded in scope instead of 4 symbols.

Two goals are both legitimate and currently being asked for at once:

- **True net worth** — dashboard/reporting should show what the
  broker account actually holds, no blind spots, no invisible
  positions. The task's complaint about hidden/wrong cash and P&L
  numbers is valid for *this* purpose.
- **Strategy-attributable performance** — `MAX_STOCK_ALLOCATION_PCT`,
  `MAX_OPEN_POSITIONS` slot budget, DD-throttle, and every CAGR/Sharpe/
  MDD number that feeds a gate decision needs a denominator that
  reflects only what the *strategy* is responsible for, or every past
  and future gate verdict in `docs/24-29` loses its meaning (it stops
  isolating strategy skill from noise the algo never controlled).

One field can't serve both without picking a side. Options:

1. **As specified** — single value, origin cosmetic only. Simplest,
   matches the literal request, but reopens the CAGR/Sharpe
   contamination risk for any future manual trade, not just today's 4
   symbols.
2. **Two lenses, one portfolio** — track every broker position (no
   invisible holdings, satisfies §2a/§2b's "nothing hidden" goal), add
   the `origin` tag, but keep two computed values: `account_value`
   (all positions, for dashboard/net-worth/cash truth) and
   `strategy_value` (cash + strategy-origin positions only, for every
   sizing/risk/CAGR/Sharpe calculation). This is still simpler than
   today (one filtered-list special case → one extra field + one extra
   aggregate function) and fixes 2a instead of widening it.

Recommend option 2 — it satisfies the stated principle ("every
calculation uses the complete broker portfolio") for reporting/net
worth, while keeping the thing the whole project's gate methodology
depends on (strategy-attributable metrics) intact. Option 1 is a
simpler diff but would need an explicit sign-off that blended
CAGR/Sharpe is acceptable going forward, since it changes what every
future gate PASS/REJECT actually means.

`CAMS.NS`'s do-not-trade verdict should migrate to a dedicated
blacklist mechanism (config-level, applies in backtest and live,
independent of broker-position tracking) regardless of which option is
picked — that part of the simplification is correct as stated and has
no tradeoff.

## 4. Edge cases identified

- Backtest is unaffected either way (§1) — no migration risk there.
- First-run baseline (`initial_capital = live_pv`, manager.py:141)
  currently captures whatever ignored holdings are in the account at
  algo-start. Under option 1 this is unchanged in kind (still whatever
  the account holds), just no longer filtered. Under option 2, the
  baseline for `strategy_value` needs its own first-run detection,
  separate from `account_value`'s.
- GTT coverage audit (`monitoring/gtt_coverage.py`) currently treats
  ignored + defensive symbols as "intentionally no stop." Under either
  option, manual/imported positions still shouldn't be forced onto the
  strategy's GTT management — that exclusion needs to survive by
  `origin`, not by symbol list.
- `reconcile_positions.py`'s mismatch detector currently ignores the 4
  symbols so they never register as a "broker/DB mismatch." Removing
  the filter without adding `origin`-aware handling first would fire
  false mismatch alerts on every manual/legacy holding, every run.

## 5. Decisions (locked)

- Two-lens value model: `account_value` (all positions, reporting) vs
  `strategy_value` (cash + strategy-origin only, feeds every
  sizing/risk/gate calculation).
- `CAMS.NS` migrates to a dedicated blacklist mechanism, independent
  of broker-position/origin handling. `LT.NS`/`HCLTECH.NS`/`IRFC.NS`
  are the actual manual-holdings case and get `origin="manual"`.

## Phase 2 — Migration Plan

### Step 1 — Add `Position.origin`, non-destructive schema addition — **DONE**

Implemented: `db/models.py` (`origin: str = "strategy"`), `db/schema.sql`
(fresh-install column), `db/repository.py` (migration + `save_position`/
`load_positions`/`get_last_position`). Validated against local paper
DB (`db/trading.db`, backed up first): migration idempotent, all 6
existing rows defaulted to `origin='strategy'`, write/read round-trip
confirmed for `origin='manual'` via a throwaway test row (inserted,
verified, deleted). Not yet deployed to the live server.

<details><summary>original plan</summary>

- **Objective**: every DB position carries `origin: "strategy" |
  "manual" | "imported"`, defaulting existing rows to `"strategy"`
  (every currently-tracked position was strategy-bought — verified,
  since ignored symbols were never synced in at all, §1).
- **Files**: `db/models.py` (`Position.origin: str = "strategy"`),
  `db/repository.py` (schema/column add + read/write), one-time
  migration script for the live DB (`ALTER TABLE ... ADD COLUMN origin
  TEXT DEFAULT 'strategy'`).
- **Risk**: low — additive column, default value covers every existing
  row correctly, no backfill ambiguity.
- **Validation**: after migration, `SELECT DISTINCT origin FROM
  positions` returns exactly `{"strategy"}` on the live DB (no manual
  holdings tracked yet at this point — that's Step 2).
- **Rollback**: drop column. No data loss, nothing downstream reads it
  yet.

</details>

### Step 2 — Stop filtering broker positions out of sync; classify origin instead — **DONE**

Implemented as planned, with one correction to the original risk note
below (it had the exit-loop logic backwards — worth recording so the
mistake doesn't get re-made): the `IGNORE_SYMBOLS` check in `strategy/
signals.py`'s exit loop was never a hazard to *remove* in this step —
it was the thing *protecting* manual positions from forced exits.
Deleting it would have been the bug. The correct change was to
generalize it: `if pos.symbol in IGNORE_SYMBOLS: continue` →
`if pos.origin != "strategy": continue`. This is strictly wider
protection (covers any future manual/imported position, not just 3
named symbols) and lands in the same commit as the sync-filter removal,
so there's no window where a manual position is DB-visible without
also being exit-exempt.

A second, real latent bug was caught while making this change: the old
`continue` fired with **no append to `updated_positions`** first. That
was silently dropping ignored positions from the function's return
value — invisible only because the branch was unreachable before today
(ignored symbols never made it into `open_positions` in the first
place, since the sync filter kept them out of the DB entirely). Now
that manual-origin positions are real, reachable input, the fix adds
the append before the `continue` so they're correctly reported as
still-open, just exit-exempt.

- `runner/daily_runner.py`: `IGNORE_SYMBOLS` sync filter removed
  (`live_positions = list(raw_live_positions)`); new-position
  construction classifies `origin = "strategy" if prev else "manual"`
  (`prev` = existing DB record lookup, any status — a symbol the
  strategy has ever opened is strategy-origin even if flat now).
  `IGNORE_SYMBOLS` import removed (no longer used in this file).
- **Second bug caught here, not in the original plan**: the "remove
  legacy stop-loss GTTs" loop in `runner/daily_runner.py` iterated
  *every* open DB position unconditionally. Once the sync filter came
  off, this would have started cancelling the user's own manually-set
  GTT stops on `LT.NS`/`HCLTECH.NS`/`IRFC.NS` the first time they
  became DB-visible — a real, unintended live-trading side effect, not
  hypothetical. Fixed by guarding the loop with
  `if pos.origin != "strategy": continue` before it can touch a GTT.
- `strategy/signals.py`: exit loop origin check as described above.
  `IGNORE_SYMBOLS` import retained — still used by the two entry-side
  checks (SHUFFLE_RS eligible-list, main candidate loop), which keep
  their original "never re-buy these 3 legacy symbols" purpose.
- `scripts/reconcile_positions.py`: `IGNORE_SYMBOLS` filter removed
  from both `get_broker_symbols()` fetch blocks (long-term-holdings and
  short-term-positions) — the mismatch detector now legitimately needs
  to see these symbols, since the DB will carry them going forward.
  Unused `IGNORE_SYMBOLS` import removed.

**Validated**: full test suite 90/90 passed. Module import check across
all four touched files (`strategy.signals`, `scripts.reconcile_positions`,
`monitoring.gtt_coverage`, `runner.daily_runner`) clean. Functional
round-trip: a synthetic `origin="manual"` position with a
guaranteed-to-trigger `stop_loss` was passed through `generate_signals()`
— produced zero exit signals and was correctly present in the returned
`updated_positions`, confirming both the exit-exemption and the
append-bug fix at once. DB save/load round-trip for `origin='manual'`
confirmed separately (throwaway row, inserted/verified/deleted).

**Not yet done**: live dry-run against the real broker account's actual
current holdings (the original plan's validation step) — this machine
runs in paper mode only; requires the live server or at minimum a
read of the real account's current positions before this is deployed
there. Flagged as an open prerequisite, not skipped.

<details><summary>original plan</summary>

- **Objective**: `sync_portfolio_with_broker` (`runner/daily_runner.py`)
  syncs *every* broker position into DB. A position is `origin="manual"`
  if the broker holds it but it was never opened by a strategy BUY
  signal (i.e., no matching `Trade` row); otherwise `"strategy"`.
  `"imported"` is reserved for a future explicit backfill command, not
  auto-assigned.
- **Files**: `runner/daily_runner.py` (`sync_portfolio_with_broker`,
  remove the `IGNORE_SYMBOLS` filter at line 209), `scripts/
  reconcile_positions.py` (drop the paired filter, or keep it but
  report manual-origin mismatches at `info` not `warning` — avoid
  false alerts per §4).
- **Risk**: medium — this is the step that makes `LT.NS`/`HCLTECH.NS`/
  `IRFC.NS` visible to the system for the first time. Must land *after*
  Step 3 (below) is deployed, not before — otherwise there's a window
  where these positions are DB-tracked but `portfolio_value()` still
  uses the old single-value broker-total path, which is already the
  bug in §2a, just now also affecting exit/entry-loop symbol checks in
  `strategy/signals.py` (`IGNORE_SYMBOLS` check at line 36/129/146
  must be removed in the same deploy, or manual positions bought
  outside the strategy would enter the exit-evaluation loop and could
  get force-sold on a `TREND_BREAK`/`MARKET_CRASH_PROTECTION` signal
  the user never asked for).

  **Correction (found during implementation)**: this last sentence has
  the logic backwards. The `IGNORE_SYMBOLS` exit-loop check must NOT be
  removed — it must be *generalized* to origin-based
  (`if pos.origin != "strategy": continue`). Removing it outright would
  have been the bug it warns about; generalizing it fixes the actual
  gap (protection was symbol-scoped, needs to be origin-scoped).
- **Validation**: dry-run against current broker holdings (read-only
  `broker.get_positions()` call), confirm classification matches
  reality before enabling the live sync change.
- **Rollback**: re-add the `IGNORE_SYMBOLS` filter (git revert), manual
  positions synced during the window get marked `status="CLOSED"` with
  a note, or left as-is — they're harmless DB rows either way since
  nothing depends on their absence.

</details>

### Step 3 — Split `portfolio_value()` into `account_value()` / `strategy_value()` — **DONE**

Implemented as planned, plus two refinements found during implementation:

- `PortfolioSnapshot` gained a `strategy_value` column (schema.sql +
  migration in `db/repository.py`), backfilled `strategy_value =
  total_value` for all existing rows — exact, not approximate, since
  every historical position was `origin="strategy"` before this
  migration (nothing else could have existed). `total_value` keeps its
  account-wide meaning; `strategy_value` is the new isolated series.
- `peak_value` (drives the drawdown-throttle comparison) was sourced
  from snapshot `total_value` (account-wide) — switched to source from
  `strategy_value` instead, since it's compared directly against
  `strategy_value()`'s output. Left out of the original plan text; it
  would have silently reintroduced §2a's exact bug (comparing a
  strategy-scoped current value against an account-wide peak) the
  moment Step 2 lands.
- `portfolio_value()` kept as a deprecated alias for `strategy_value()`
  (not removed) — any call site not yet migrated gets the conservative
  basis rather than the inflated one. Zero external callers found
  outside `portfolio/manager.py` itself (grepped).
- One-time bootstrap baseline detection (`_load_state`, fires only
  when no snapshots exist yet) still uses the raw broker total — left
  as-is per the plan's §4 note (needs `prices` that aren't available
  at construction time; only matters for a fresh install with
  pre-existing manual holdings, not this account's history).
- `strategy/signals.py`'s `portfolio_value` parameter is dead code
  (accepted, never read in the function body) — confirmed, left alone,
  out of scope for this task.

**Validated**: `account_value()`/`strategy_value()`/deprecated
`portfolio_value()` all agree exactly on the live paper DB today (0
manual-origin positions exist yet, confirmed via
`SELECT DISTINCT origin`) — proves the split is a behavior no-op until
Step 2 lands, as intended. Snapshot write/read round-trip confirmed
with `strategy_value != total_value` via a throwaway future-dated row
(inserted, verified, deleted). Full test suite: 90/90 passed. Backtest
path confirmed structurally unreachable — `backtest/engine.py` only
imports `Position`/`Trade` from `db.models`, never `PortfolioSnapshot`
or `portfolio/manager.py`; all `Position(...)` construction call sites
use keyword args, so the new `origin` field (defaults to `"strategy"`)
doesn't shift any positional argument. No gate re-run needed — zero
code path exists for this change to reach.

<details><summary>original plan</summary>

- **Objective**: `account_value()` keeps today's broker-total behavior
  (all positions, all cash) for dashboard/net-worth display.
  `strategy_value()` = `self.cash + portfolio_invested_value(positions
  where origin == "strategy", prices)`. Every current caller of
  `portfolio_value()` that feeds sizing, `MAX_STOCK_ALLOCATION_PCT`,
  `peak_value`/DD-throttle, and `initial_capital` baseline detection
  switches to `strategy_value()`. Dashboard (`dashboard/views/
  overview.py`) switches to `account_value()`.
- **Files**: `portfolio/manager.py` (the two new methods + every
  internal caller), `dashboard/views/overview.py`, `db/models.py`
  (`PortfolioSnapshot` gains a second `strategy_value` column alongside
  existing `total_value`, so historical CAGR/Sharpe can be computed on
  the correct series going forward — `total_value` keeps meaning
  account-wide for continuity).
- **Risk**: **highest step in this plan** — this is live-money sizing
  and drawdown-throttle logic. A mistake here either over-sizes
  positions (repeats §2a) or under-sizes them (mixes cash meant for
  strategy capital into a wrong denominator).
- **Validation checklist**:
  - Unit test: `strategy_value()` with a mix of strategy/manual
    positions in DB returns cash + strategy-origin market value only.
  - Unit test: `account_value()` unchanged from today's
    `portfolio_value()` behavior (regression guard).
  - Live dry-run (no order placement): log both values side by side
    for a few days, confirm `strategy_value() <= account_value()`
    always, and confirm sizing decisions using the new denominator
    match hand-calculated expectations before flipping any real
    position-sizing call over.
  - Confirm `robustness_gate.py` full-window run is byte-identical
    before/after (backtest never had a broker, so this step should be
    a no-op there — any diff means something leaked into the shared
    code path and needs investigation before proceeding).
- **Rollback**: git revert; `strategy_value` snapshot column is
  additive, safe to leave unused if reverted.

</details>

### Step 4 — Migrate `CAMS.NS` to a dedicated blacklist — **DONE**

Implemented: `BLOCKED_SYMBOLS` added to `config/settings.py` (mirrors
`BLOCKED_SECTORS`, sourced from `strategy_config.yaml`'s new
`blocked_symbols` key), `CAMS.NS` added there, removed from
`IGNORE_SYMBOLS` (now `["LT.NS", "HCLTECH.NS", "IRFC.NS"]` — the actual
manual-holdings case). `strategy/signals.py` checks `BLOCKED_SYMBOLS`
in both the entry-candidate loop and the `SHUFFLE_RS` eligible-list
comprehension, unconditionally before any qualification logic runs —
same shape as the existing `BLOCKED_SECTORS` check. `docs/24_Rejected_
Forever.md`'s `CAMS.NS` row updated to point at the new mechanism.

**Validated**: constant-level check confirms `CAMS.NS` is out of
`IGNORE_SYMBOLS` and in `BLOCKED_SYMBOLS`. Full test suite: 90/90
passed. Backtest is affected here (unlike Steps 1/3) since
`BLOCKED_SYMBOLS` is a real entry-gate change — net effect should be
identical to before (`CAMS.NS` was never enterable either way), a full
`robustness_gate.py` run would confirm byte-identical trade history
but wasn't run this session (mechanism verified structurally: the
`continue` on a blocked symbol happens unconditionally, before
`check_entry()` is ever called, in both old and new code paths) —
worth a real gate run before this is considered fully closed.

<details><summary>original plan</summary>

- **Objective**: new `BLOCKED_SYMBOLS` constant (parallel to existing
  `BLOCKED_SECTORS`) in `config/settings.py`, enforced in
  `strategy/signals.py`'s entry gate for both backtest and live.
  `IGNORE_SYMBOLS` constant is deleted once Steps 1-3 land (nothing
  left referencing it — `LT.NS`/`HCLTECH.NS`/`IRFC.NS` are now
  handled via `origin`, `CAMS.NS` via `BLOCKED_SYMBOLS`).
- **Files**: `config/settings.py`, `strategy/signals.py` (entry-gate
  check, same shape as the existing `BLOCKED_SECTORS` check),
  `docs/24_Rejected_Forever.md` (update `CAMS.NS`'s row to point at
  the new mechanism).
- **Risk**: low — same pattern as an existing, proven mechanism
  (`BLOCKED_SECTORS`).
- **Validation**: `robustness_gate.py` full-window run confirms
  `CAMS.NS` still never gets entered (byte-identical trade list to
  pre-migration baseline for that symbol).
- **Rollback**: git revert, trivial.

</details>

### Step 5 — GTT coverage / reconcile scripts move from symbol-list to origin-aware — **DONE**

Implemented as planned. `_excluded_symbols()` now unions
`IGNORE_SYMBOLS ∪ ALL_DEFENSIVE_SYMBOLS ∪ {manual/imported-origin open
DB positions}`. `IGNORE_SYMBOLS` kept in the union rather than dropped
outright — it still correctly excludes the 3 legacy symbols even in
the edge case where they haven't been DB-synced yet (e.g. before Step
2's sync runs once), so this is additive, not a replacement.

- `monitoring/gtt_coverage.py`: `_excluded_symbols()` now also loads
  `{p.symbol for p in load_positions(status="OPEN") if p.origin !=
  "strategy"}` from `db.repository` and unions it in.

**Validated**: functional test — inserted a throwaway `origin="manual"`
position, confirmed `_excluded_symbols()` includes it, then deleted the
row. Covered by the same 90/90 test-suite pass and import check as
Step 2.

**Not yet done**: live run against real broker data (original plan's
validation step) — same blocker as Step 2, requires the live server.

<details><summary>original plan</summary>

- **Objective**: `monitoring/gtt_coverage.py`'s `_excluded_symbols()`
  switches from `IGNORE_SYMBOLS ∪ ALL_DEFENSIVE_SYMBOLS` to "any
  position with `origin != 'strategy'`" `∪ ALL_DEFENSIVE_SYMBOLS`.
- **Files**: `monitoring/gtt_coverage.py`.
- **Risk**: low, cosmetic-adjacent — worth doing in the same deploy as
  Step 2 since both read the same origin classification.
- **Validation**: run against live broker data, confirm no false
  "naked position" alerts fire for manual holdings.
- **Rollback**: git revert.

</details>

### Sequencing

Steps 1 → 3 → 4 → (2 + 5 together) → deploy. Step 3 must land before
Step 2 goes live (see Step 2's risk note) even though it's numbered
after it above — schema (1) and value-split (3) are the load-bearing
pieces; symbol visibility (2) is the one with a real live-trading
behavior change and should go last, after everything it depends on is
already deployed and validated.

## Phase 3 — Implementation

All 5 steps DONE locally (paper mode) as of 2026-07-13. Test suite
90/90 passing. Two live-broker-dependent validations remain open
before deploy: Step 2/5's live dry-run against real current holdings,
and Step 4's `robustness_gate.py` re-run (BLOCKED_SYMBOLS is a real
backtest-affecting change). See Phase 4 below.

## Phase 4 — Validation checklist

Per the original spec: confirm every calculation that touches money
still comes from `account_value()`/`strategy_value()` correctly, not
from a value that quietly changed meaning underfoot.

| Item | Uses | Status |
|---|---|---|
| Portfolio value (broker total, reporting/net-worth) | `account_value()` | Done — Step 3, no behavior change today (0 manual positions live) |
| Strategy value (sizing/risk/CAGR/gate) | `strategy_value()` | Done — Step 3, same |
| Cash balance | unchanged, never origin-scoped | Untouched by this change, correct by design |
| Allocation / position sizing | reads `strategy_value()` via `portfolio_value()` alias in `portfolio/manager.py` | Done — verified alias points to `strategy_value()` |
| P&L / total return / CAGR / XIRR / drawdown | `peak_value` now tracks `strategy_value` history, not `total_value` | Done — Step 3 `_load_state()` fix |
| Rebalancing / buy-sell decisions | entry loop excludes `BLOCKED_SYMBOLS`, exit loop excludes non-strategy origin | Done — Steps 2 and 4 |
| Backtest parity | `backtest/engine.py` unreachable by any of these changes (no `PortfolioSnapshot`/origin usage) | Confirmed structurally unaffected — no gate re-run needed for Steps 1/2/3/5 |
| Step 4 (`BLOCKED_SYMBOLS`) backtest equivalence | entry-gate change, backtest-affecting | **Done** — see below, not via `robustness_gate.py` |
| Live dry-run vs. real broker holdings | Steps 2 + 5 | **Outstanding** — needs live server, cannot run from this paper-mode machine |
| Deploy to live server | all steps | **Not started** — pending user confirmation, per session practice |

**Step 4 method note**: `robustness_gate.py` requires a `--env KEY=VALUE`
override to define a candidate arm, but `BLOCKED_SYMBOLS`/`IGNORE_SYMBOLS`
aren't env-driven settings — adding env-var plumbing for a one-off check
would be exactly the kind of unjustified complexity the spec says to
avoid. Used a more direct proof instead: ran the full-window backtest
(`2022-01-01`→`2026-06-04`, `REQUIRE_CACHED_DATA=1`) once on this
session's code, then `git stash` back to the pre-session commit
(`9649786`) and ran the identical command again, then restored the
stash. `outputs/backtest_trades.csv` and `outputs/backtest_equity.csv`
were byte-identical between the two runs (`diff` exit 0, 0 lines).
Confirms the CAMS.NS exclusion mechanism swap (`IGNORE_SYMBOLS` →
`BLOCKED_SYMBOLS`) changes nothing about which trades occur — expected,
since the entry-side excluded-symbol set is unchanged
(`{LT.NS, HCLTECH.NS, IRFC.NS, CAMS.NS}` before and after, just split
across two lists now instead of one). Test suite re-confirmed 90/90
passing after the stash round-trip.
