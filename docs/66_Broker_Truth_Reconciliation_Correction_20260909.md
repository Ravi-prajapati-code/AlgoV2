# 66 — Broker-Truth Reconciliation Correction (2026-09-09)

## Context

The Phase 26 live scan (docs/65) ran the M0 classifier against the live
server's real broker snapshot and both strategy ledgers. After the GOLDBEES
false-positive fix (a44bf12), four symbols remained genuinely mismatched —
`GHOST_DB_POSITION` or `QUANTITY_MISMATCH`, not classifier noise:

| Symbol | Strategy | Ledger qty | Broker qty | Classification |
|---|---|---|---|---|
| ASIANENE.NS | main | 8 | 0 | GHOST_DB_POSITION |
| ASIANENE.NS | momentum_atr | 8 | 0 | GHOST_DB_POSITION |
| ATHERENERG.NS | momentum_atr | 1 | 0 | GHOST_DB_POSITION |
| CYIENT.NS | momentum_atr | 6 | 3 | QUANTITY_MISMATCH |
| WELCORP.NS | momentum_atr | 8 | 3 | QUANTITY_MISMATCH |

Unlike GOLDBEES — a pure bookkeeping fiction (momentum_atr's real 175 sh
double-counted by a stale MAIN paper-position filter) — none of these four
has any exit/trade record on either DB explaining the gap. Read-only trade-
history queries against both `trading.db` and `momentum_atr.db` confirmed
this: no matching `exit_date`/`exit_reason` row for the missing shares on
either ledger, for any of the four symbols. This rules out a second
GOLDBEES-shaped fix; the evidence points to real shares having left the
broker account through an uncoordinated channel.

Root cause is **confirmed** for one symbol and **unconfirmed** for three:

- **CYIENT.NS**: MAIN manually liquidated 3 sh at the broker terminal
  pre-paper-cutover (`trading.db.trades.exit_reason =
  'MANUAL_LIQUIDATION_PRE_PAPER_SWITCH'`, from a prior session's
  investigation) — Upstox holds CYIENT as one fungible balance, not
  segregated by strategy, so that manual sell ate into momentum_atr's real
  holding without momentum_atr's ledger ever being told.
- **WELCORP.NS, ATHERENERG.NS, ASIANENE.NS**: same no-exit-record signature
  as CYIENT, but no matching manual-liquidation (or other) trade record was
  found to explain them. Cause not established — this is stated honestly
  rather than assumed.

## Decision

User instruction: "in this case broker is right make changes accordingly
and currently momentum atr strategy only live other are not." Broker
quantity is ground truth; the DBs must be corrected to match it.

Before acting, the magnitude was checked against momentum_atr's kill switch
(the only strategy currently trading `--live`): correcting all four symbols
to broker truth drops momentum_atr's equity from ~₹50,999.77 (current,
phantom-share-inclusive) to a realized loss against a persisted
`peak_equity` of ₹52,430.588 — a ~46% drawdown, well past the 25%
`MOMENTUM_ATR_DD_KILL_PCT` threshold. This was surfaced to the user rather
than decided unilaterally, given the combination of live capital, an
ambiguous root cause for 3 of 4 symbols, and a system-halting consequence.

User's answer: **record it as a real loss and let the kill switch trip if
it does** — the kill switch tripping on a genuine 46% drawdown is the
system working as designed, not something to suppress or work around.

## Accounting model

Corrections do **not** credit `cash`. No real sale proceeds were ever
received for shares that were never actually sold by the strategy —
crediting cash would fictitiously inflate it on top of an already-wrong
ledger. Instead:

- `exit_price = 0.0` — a full write-off of cost basis. This sidesteps
  needing to guess an unknowable current market price for shares the
  strategy no longer holds, and produces the correct sign/magnitude of
  loss: `gross_pnl = net_pnl = -(entry_price × lost_shares)`, `charges = 0`.
- `momentum_atr/risk.py::compute_equity()` sums `cash + shares×price` over
  the `positions` table (not derived from `trades` history), so once the
  phantom shares are removed from `positions` with no offsetting cash
  credit, the next equity computation reflects the loss honestly.

Two distinct `exit_reason` values were used, matching the confirmed vs.
unconfirmed root-cause split — deliberately not overclaiming a cause for
the three unconfirmed symbols:

- `RECONCILED_SHARES_LOST_MAIN_MANUAL_LIQUIDATION` — CYIENT.NS only.
- `RECONCILED_SHARES_LOST_UNEXPLAINED_BROKER_GAP` — WELCORP.NS,
  ATHERENERG.NS, ASIANENE.NS (both ledgers).

## Implementation

`scripts/apply_broker_truth_corrections_20260909.py` (dry-run by default,
`--apply` to write) applies five corrections:

- momentum_atr CYIENT.NS: 6 → 3 (reduce)
- momentum_atr WELCORP.NS: 8 → 3 (reduce)
- momentum_atr ATHERENERG.NS: 1 → 0 (close)
- momentum_atr ASIANENE.NS: 8 → 0 (close)
- main ASIANENE.NS: 8 → 0 (close)

The two reductions needed a primitive momentum_atr's repo didn't have:
`db/momentum_atr_repo.py::reduce_position_and_save_trade()` was added,
mirroring `db/repository.py`'s existing MAIN equivalent exactly (atomic
transaction, position stays OPEN with unchanged entry_price/entry_date/
entry_order_id, raises `ValueError` if no OPEN position exists).

**Idempotency**: each correction skips if the recorded share count is
already `<=` the target broker quantity — safe to re-run after a partial
failure, or as a no-op once already applied. Verified live: the apply run
was executed twice; the second run produced five `SKIP` lines and did not
write a second trade for any symbol.

Test coverage: `tests/test_momentum_atr_repo.py` (2 tests, the new reduce
primitive) and `tests/test_apply_broker_truth_corrections_20260909.py` (9
tests — reduce, close, idempotency, missing-position, dry-run-writes-
nothing, for both `main` and `momentum_atr` paths), all against tmp-path
DBs. Full suite: 263 passed (same 4 pre-existing unrelated failures in
`test_momentum_atr_execution.py`/`test_universe_research.py`, untouched by
this change).

## Execution and live verification (2026-09-09)

Applied against the live server (`ubuntu@3.109.104.170`) via
`.venv/bin/python scripts/apply_broker_truth_corrections_20260909.py
--apply`:

```
[momentum_atr] REDUCE momentum_atr CYIENT.NS: 6 -> 3 (lost 3 sh, write-off -3,451.14)
[momentum_atr] REDUCE momentum_atr WELCORP.NS: 8 -> 3 (lost 5 sh, write-off -12,067.50)
[momentum_atr] CLOSE momentum_atr ATHERENERG.NS: 1 -> 0 (lost 1 sh, write-off -1,683.50)
[momentum_atr] CLOSE momentum_atr ASIANENE.NS: 8 -> 0 (lost 8 sh, write-off -3,979.20)
[main] CLOSE main ASIANENE.NS: 8 -> 0 (write-off -3,950.80)
```

Total momentum_atr write-off: ₹21,181.34.

Re-run immediately after confirmed idempotency (all five `SKIP`).

Post-correction state (read-only query, entry-price marks):

```
cash: -3997.73
peak_equity (persisted): 52430.588
kill_switch_tripped: False   <- not yet re-evaluated; see below
positions remaining: WELCORP.NS×3, CYIENT.NS×3, GOLDBEES.NS×175
equity (at entry-price marks): 28297.66
drawdown vs persisted peak: 46.03%
```

`kill_switch_tripped` reads `False` because `check_kill_switch()` is only
invoked from inside momentum_atr's live BUY execution path
(`momentum_atr/execution.py:309`), not by this correction script or by the
read-only verification query — it was deliberately **not** manually
triggered out-of-band. The next scheduled momentum_atr cron run (09:17 IST,
2026-09-10) will evaluate it naturally against the now-honest ~46%
drawdown and trip it as part of its normal execution flow, exactly as the
user's decision intended. This should be confirmed at that time.

## Follow-ups

- **Verify the kill switch actually trips** at the next momentum_atr run
  (2026-09-10 ~09:17 IST) and that live BUY submission halts as designed.
  If it does not trip, that is a bug in `check_kill_switch()` or a stale
  `peak_equity`/state issue, not evidence the correction was wrong.
- **WELCORP.NS / ATHERENERG.NS / ASIANENE.NS root cause remains
  unconfirmed.** If it recurs, or if a similar unexplained gap appears on a
  fifth symbol, that's a signal this isn't isolated incidents but a
  systemic leak (e.g. an untracked manual-trading channel, or a broker-side
  corporate action/fee-in-kind not being ingested) — worth investigating
  before dismissing as one-offs.
- `db/repository.py::reduce_position_and_save_trade` (MAIN's existing
  version, used as this fix's template) still has no test coverage of its
  own — a pre-existing gap, not addressed here since out of scope.
- M1's `observability_snapshot.py` cron deployment is still open (docs/65)
  — until it's live, the next drift like this won't be caught until another
  manual scan.

## Addendum (2026-09-09): idle-cash investigation and two broker-safety bugs

Same day, the user asked why ₹38,482.92 of real Upstox cash sat unused
despite `MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT=1.0`. Root cause: this
correction (above) drove `state.cash` — momentum_atr's internal ledger, used
by `_get_effective_cash()`'s `min(internal_cash, real_cash, headroom)` — to
**−₹3,997.73**. `min()` floors the BUY budget at that negative number
regardless of real broker cash. This is the internal ledger correctly
absorbing today's ₹21,181.34 write-off, not a bug in the correction — but it
surfaced two real, independent bugs while investigating:

1. `UpstoxBroker.get_available_cash()` / `get_holdings()` silently return
   `0.0`/`[]` on any API exception — indistinguishable from a genuine zero
   balance. `main.py` already had a comment acknowledging this exact gap
   elsewhere, worked around locally rather than fixed at the source.
2. The existing `MIN_REAL_CASH_RATIO` sanity check
   (`momentum_atr/execution.py`) only evaluates `if internal_cash > 0` — a
   negative internal cash, like the one this correction produced, has never
   fired an alert.

**Fixed, narrowly scoped to momentum_atr** (not a `BaseBroker`-wide
refactor, to avoid touching MAIN's dormant paper path, `runner/
daily_runner.py`, GTT monitoring, and reporting scripts that all call the
existing `get_available_cash()`/`get_holdings()`/`get_positions()` and must
keep their current behavior unchanged):

- `broker/base.py` — additive `get_available_cash_or_none()` /
  `get_holdings_or_none()` on `BaseBroker`, default forwards to the existing
  methods (correct for `PaperBroker`, which never fails).
- `broker/upstox.py` — `UpstoxBroker` overrides both to return `None` on
  failure instead of `0.0`/`[]`.
- `momentum_atr/execution.py` — `_real_total_account_equity()` and
  `_get_effective_cash()` now use the `_or_none` variants. `None` from
  either blocks BUYs (`BROKER_CASH_UNAVAILABLE` / `BROKER_DATA_UNAVAILABLE`,
  distinct alerts) instead of computing off missing data. The one-time
  bootstrap path (`run_daily()`, first-ever run) now aborts rather than
  seeding `cash`/`peak_equity` from a broker failure. `internal_cash <= 0`
  now always alerts (`INTERNAL_CASH_NEGATIVE`), showing both the internal
  and real broker cash side by side, independent of the pre-existing ratio
  check.

**Deliberately not done in this pass**: no change to `state.cash`'s value.
Rebaselining the internal ledger toward real broker cash (a
`LIVE_CASH_REBASELINE`-style cutover) was requested by the user in the same
session but is **sequenced after** confirming the kill switch trips at the
next scheduled run (2026-09-10 ~09:17 IST, per the follow-up above) —
raising `state.cash` before that run would raise the `equity` fed into
`check_kill_switch()`'s `peak = max(peak, equity)`, very likely setting a
new higher peak and suppressing the ~46% drawdown trip the user explicitly
asked to let happen. Flagged to the user; not implemented pending that
confirmation and an explicit decision on the rebaseline policy itself.

Tests: `tests/test_momentum_atr_execution.py` — 6 new (broker-cash-
unavailable blocks BUY, broker-holdings-unavailable blocks BUY, bootstrap
aborts on broker failure rather than seeding Rs.0, negative-internal-cash
always alerts with both figures, healthy-path has no spurious alert, plus
the two pre-existing allocation-cap/bootstrap tests re-verified against the
`_or_none` change). Full suite: 263 passed, same 4 pre-existing unrelated
failures (`test_momentum_atr_execution.py` scoring-formula drift,
`test_universe_research.py` universe-size drift) — zero regressions.
