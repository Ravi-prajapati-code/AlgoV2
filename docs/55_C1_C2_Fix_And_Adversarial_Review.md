# Doc 55 — C1/C2 Fix Implementation + Codex Adversarial Review

**Date**: 2026-08-04
**Scope**: implements fixes for docs/54 findings C1 (`shares_override` ignored) and C2
(`cancel_stale_gtts()` return value discarded), per the charter's propose→implement→
adversarial-review→respond cycle. Not yet committed — see status at bottom.

## C1 fix — `shares_override` now honored

**`portfolio/manager.py` `_execute_sell()`**: reads `sig.indicators["shares_override"]`.
A valid override (`0 < override < pos.shares`) sells only that quantity and leaves the
position OPEN with the remainder (`repo.reduce_position_and_save_trade`, new function
mirroring `close_position_and_save_trade`'s atomicity). An override `>= pos.shares` or
`<= 0` is treated as invalid and falls back to a full sell (the pre-existing behavior),
but now logs a warning and sends a Telegram alert rather than silently proceeding as if
nothing were wrong — an invalid override means something upstream miscalculated, and
that should be visible, not masked.

**BUY-side sizing block**: a `shares_override` present in the signal now sizes the buy
directly from it instead of going through equal-weight/safe-haven slot-cash math —
covers defensive entries, bear-swing entries, and LIQUIDBEES cash-park buys.

**`db/repository.py`**: new `reduce_position_and_save_trade(symbol, remaining_shares, t)`
— atomically updates the OPEN position's share count and inserts the trade row for the
sold portion in one `with conn:` transaction, same commit/rollback guarantee as
`close_position_and_save_trade`. Includes an `UPDATE ... rowcount` check: if zero rows
matched (position already closed/reduced elsewhere), the whole transaction — including
the trade insert — is rolled back rather than partially committing.

## C2 fix — `cancel_stale_gtts()` gates order placement

Added `if not cancel_stale_gtts(...): return/continue` immediately before order
placement at the two **gating** call sites (pre-BUY, pre-SELL — inside the branch that's
about to place a live order). The other 3 call sites (batch legacy-GTT sweep at run
start, post-hoc ROTATE_ADD/ADD-pyramiding cleanup) don't immediately gate an order in
the same branch and were left unchanged — different risk class, out of scope here.

## Codex adversarial review of the C1 diff — findings and disposition

Per the charter, the C1 diff (before C2 was added) was sent to Codex CLI for
independent adversarial review. All 5 findings triaged below; charter requires
responding to every objection, not defending the original implementation by default.

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | High | Paper-mode (no-broker) override BUYs crash with `UnboundLocalError` — a downstream allocation-display log line read `slot_cash`/`useable_cash`, neither assigned when the new override branch is taken. Real bug, introduced by the C1 diff; my own tests didn't catch it because all 4 used `FakeBroker` (live-mode path), never exercising paper mode. | **FIXED.** `alloc_display` is now assigned explicitly in all three sizing branches (override / safe-haven / equal-weight) instead of being re-derived from branch-specific variable names at the log line. New regression test `test_shares_override_buy_paper_mode_does_not_crash` (`broker=None`) added — reproduces the crash pre-fix, passes post-fix. |
| 2 | High | Timeout/partial-fill sells don't use `OrderResult.filled_qty` — a part-filled sell (e.g. 40 of 100 before timeout) leaves the DB stale with no trade record for the filled portion. | **Deferred, documented, not fixed here.** Same root cause as already-tracked docs/54 C3 (Upstox `"partial"` status maps to `PENDING`, `broker/upstox.py:488`). A shares_override-scoped patch would be incomplete — needs `filled_qty`-aware accounting across the whole order lifecycle (buys too, not just this sell path). Rolled into C3's scope rather than half-fixed here. |
| 3 | High | `reduce_position_and_save_trade()` has no optimistic-concurrency guard — a 0-row `UPDATE` (position already closed/reduced concurrently) wouldn't stop the trade insert from committing, same limitation as the pre-existing `close_position_and_save_trade`. | **FIXED**, but note the fix required a second decision: raising inside the `with conn:` block does correctly roll back both statements (free — sqlite3's context manager), but nothing upstream caught the exception, so it would have crashed the rest of `process_signals` on what should be a rare, recoverable mismatch. By the time this code runs, a live broker sell has often already filled — a DB write failure here means real money moved but the DB missed it, not that the sell failed. Wrapped the DB write in try/except at the call site: logs + Telegram-alerts on failure, keeps in-memory state (cash, position) consistent with what the broker actually did, and lets next-day broker sync reconcile the DB — matches the existing timeout-handling pattern elsewhere in this function rather than inventing a new failure mode. |
| 4 | Med | Failed GTT cancellation doesn't block a partial sell. | **Stale — no action.** Codex reviewed the C1-only diff, before the C2 gating fix was added. Re-read of current `_execute_sell`: the `cancel_stale_gtts` check happens before order placement and before the `is_partial` branch does anything — it already covers both partial and full sells. Confirmed by direct re-read, not just diff timing inference. |
| 5 | Med | Malformed/oversized override silently becomes a full liquidation, masking an upstream bug. | **FIXED.** Behavior (fall back to full sell) is unchanged — it matches the pre-C1 baseline for this edge case, not a new risk — but it's no longer silent: logs a warning and sends a Telegram alert naming the invalid override value and current share count. |

## Test coverage added this pass

6 new tests in `tests/test_manager_execution.py` (full suite: 141/141 passing):
- 3x `shares_override` SELL (partial, override==full, override>shares clamps)
- 1x `shares_override` BUY, live/FakeBroker mode
- 1x `shares_override` BUY, **paper mode** (`broker=None`) — closes the exact coverage
  gap that let Finding #1 through; would have failed with `UnboundLocalError` pre-fix
- 2x `cancel_stale_gtts` gating (blocks BUY, blocks SELL, on unverified cancellation)

## C3 — narrow fix applied, broader gap remains open

Bug C3 (docs/54): Upstox's `"partial"` order status had no entry in
`UpstoxBroker._parse_order_response`'s `status_map`, so it silently fell through to
`OrderStatus.PENDING`.

Traced the actual runtime effect before touching anything: both `PENDING` and a
correctly-labeled `PARTIAL` already funnel into the identical branches at both call
sites in `portfolio/manager.py` (BUY ~line 640, SELL ~line 884 — `res.status not in
(...)` gates and the subsequent `!= COMPLETE` fallback). So the missing map entry is a
genuine data-correctness bug (wrong status value in `OrderResult.status`, logs, and any
future code that branches on it) but it was **not** silently causing worse trading
behavior than today's already-known gap — because neither BUY nor SELL currently uses
`OrderResult.filled_qty` at all, regardless of status label.

**Fixed (low risk, no behavior change to the money-handling paths):**
- `broker/upstox.py`: added `"partial": OrderStatus.PARTIAL` to `status_map`.
- `portfolio/manager.py`: widened both `res.status not in (...)` gates (BUY and SELL)
  to include `OrderStatus.PARTIAL`, so it now routes into the same pre-existing
  "not confirmed — use estimate / don't close in DB, alert, let sync reconcile" branch
  that timeouts already use, instead of either being dropped (BUY) or silently
  masquerading as a plain pending order.
- New `tests/test_upstox_status_parsing.py` — direct regression test on
  `_parse_order_response`, would fail pre-fix (`status == PENDING` instead of
  `PARTIAL`).

**Explicitly NOT fixed here — separate, larger item:** using `res.filled_qty` to record
the actually-filled quantity is a real feature build, not a bug patch — it requires a
design decision (e.g.: on a partial BUY, record a position for `filled_qty` shares and
debit cash for only that amount, vs. wait entirely for sync; on a partial SELL, write an
immediate trade record for the filled portion and leave the remainder open) that touches
money accounting under a rare-but-real live scenario. Doing this properly also needs a
look at the next-day broker-sync reconciliation logic (not yet audited this session —
task #5, live execution path audit, still pending) to confirm it wouldn't double-count
or mis-attribute P&L once the order eventually completes. This deserves its own
hypothesis → adversarial-review cycle per the charter, not a rushed patch bundled into
this fix pass. Tracked as the residual scope of C3.

**Update — confirmed with concrete evidence during the follow-up live-execution-path
audit (task #5):**
- `scripts/reconcile_positions.py`'s daily 09:20 IST cron only does set-difference on
  symbol *presence* (`db_syms - broker_syms`, `broker_syms - db_syms`). A partial fill
  that leaves DB overstated (symbol still open on both sides, wrong share count) is
  invisible to it — reports "OK — positions match" regardless.
- `runner/daily_runner.py`'s `add_or_update_broker_positions()` (~line 274-280), the
  function that actually corrects share-count drift when the reconciler *does* catch a
  mismatch, unconditionally does `db_pos.shares = lp.quantity; save_position(db_pos)` —
  no trade record, in either direction (increase or decrease). This is the exact
  mechanism Codex described, now pinned to a specific function and line range rather
  than a general claim.

Still not fixed — this confirms scope, doesn't change the decision to defer. Building
this properly means the sync path also calling something like
`reduce_position_and_save_trade` when share count decreases, but that needs a fill price
Upstox's position-snapshot API doesn't retain at the point of sync — the design question
(estimate from LTP? mark as unrealized until manually reconciled? something else?) is
exactly the kind of decision that needs its own proposal, not a patch here.

## C4 (new, found this pass) — bear-swing loop double-counted spare cash across candidates

Found during a follow-up Claude-side audit of the live execution path (`runner/`,
`portfolio/manager.py`, `broker/`), not part of the original docs/54 Codex hunt.

`runner/daily_runner.py`'s bear-swing candidate loop (~line 682) checks a local
`cash_bal` against each candidate's `slot_cash_capped` to decide how much LIQUIDBEES to
sell as funding. `BEAR_SWING_SLOTS` defaults to 2, so this loop can run twice per
session — but `cash_bal` was never decremented after a candidate's slot was queued.
Both candidates would see the identical, stale "spare cash" figure, so the second
candidate's LIQUIDBEES top-up would be sized as if the first candidate's slot hadn't
consumed anything. Net effect: the second bear-swing entry's BUY signal could be sized
for more cash than would actually be available at execution time. Compounded by the
fact that `shares_override` BUYs (which this path always uses) had **no cash check at
all** in `portfolio/manager.py` before this pass — a runner-side sizing mistake would
go straight to a live market order with nothing to catch it locally.

**Fixed:**
- `cash_bal` now decrements by `slot_cash_capped - liq_funded_net` after each
  candidate — the portion of that slot actually drawn from the shared cash pool.
- `portfolio/manager.py`: added a defense-in-depth check on the `shares_override` BUY
  path — skip + Telegram alert if the override's cost exceeds `self.cash`, instead of
  sending the order unconditionally. This is deliberate defense-in-depth: it should
  rarely fire if the runner is correct, but a false skip (recoverable — human sees the
  alert) is a far better failure mode than a false send (an order the account can't
  cover).

**Codex adversarial review of this fix caught two more real issues, also fixed:**
- The LIQUIDBEES-funding math used *gross* sale proceeds to compute how much cash
  reached the pool, ignoring `sell_charges()` — overstating available cash by the
  charge amount on every top-up. Now uses net (post-charge) proceeds.
- `liq_pos.shares` (the LIQUIDBEES position's original share count) was reused
  unchanged as the cap on every iteration's sell size, instead of tracking shares
  actually sold so far. A second candidate could have been offered shares the first
  candidate's funding sell had already claimed. Now tracked via a decrementing
  `liq_shares_remaining`. Also replaced `int(needed/liq_price)+1` (an unconditional
  overshoot, even on exact division) with `math.ceil(needed/liq_price)`.

Verified by hand-simulating two candidates against a thin cash balance and a large
LIQUIDBEES position (script in scratchpad, not committed) — confirms `cash_bal` floors
at 0 rather than going negative, and `liq_shares_remaining` prevents overselling.

**Not unit-tested at the runner level.** The bear-swing loop lives inside `run()`, a
single ~450-line function with no seams for isolated testing — building that harness is
a separate, larger testability task, not something to bundle into this fix. The
`portfolio/manager.py` backstop (the cash-sufficiency check) *is* covered:
`test_shares_override_buy_skipped_when_cash_insufficient` in
`tests/test_manager_execution.py`.

**Accepted tradeoff, not a bug:** Codex flagged that the new cash check could
false-positive-skip a legitimate buy if `self.cash` was already conservatively reduced
by an earlier unconfirmed/timed-out order in the same run. Accepted — a false skip is
recoverable (alert fires, human investigates); the alternative (no check) risks sending
an order the account can't afford, which isn't.

## Status

C1, C2, C4 fully fixed + reviewed. C3 narrowly fixed (status-label correctness); the
underlying partial-fill accounting gap remains open and is the next candidate for a
proper design proposal. Full suite: 144/144 passing.

Nothing committed yet. Per docs/47 §2.2 (no bundled commits) and the prior
`cross_feature_commit_contamination` finding (hunk-splitting insufficient when two
features share a file), C1/C2/C4 all touch `portfolio/manager.py`'s BUY/SELL execution
closely enough that clean separation needs deliberate `git add -p` hunk review before
splitting into commits; C3's fix is in different files (`broker/upstox.py` + isolated
tuple edits) and should be its own commit. None of this has been split/staged yet —
awaiting explicit user go-ahead to commit (not inferred from "continue").
