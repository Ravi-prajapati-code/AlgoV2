# 67 — Live Cash Rebaseline & Kill-Switch Clear (2026-09-09)

## Summary

Following docs/66's broker-truth write-off correction (removing phantom
CYIENT/WELCORP/ATHERENERG/ASIANENE shares from momentum_atr's ledger with no
offsetting cash credit, since no real sale occurred), momentum_atr's internal
`state.cash` went negative (−₹3,997.73). This was correct bookkeeping for
that correction, but it created a second, independent problem: the BUY-sizing
formula (`_get_effective_cash()` = `min(internal_cash, real_cash, headroom)`)
floors every trade's budget at the smaller of internal/real cash — so even
though the real broker held ₹38,482.92, momentum_atr could not deploy any of
it, because the internal number was negative.

This doc records two live actions, both explicitly authorized by the user on
2026-09-09, applied via `scripts/live_cash_rebaseline_20260909.py`:

1. **Cash rebaseline**: `state.cash` set to a live-verified broker read
   (`UpstoxBroker.get_available_cash_or_none()` → ₹38,482.92), replacing the
   contaminated −₹3,997.73. `peak_equity` and `kill_switch_tripped` were
   deliberately left untouched by this step.
2. **Kill-switch clear**: `kill_switch_tripped` set back to `False`, allowing
   momentum_atr to resume new BUYs. `kill_switch_tripped_date` (2026-09-09)
   was deliberately preserved as a historical record, not erased.

## Why the sequencing mattered

Rebaselining cash upward, on its own, raises computed equity
(`compute_equity(cash, positions, prices)`), which raises drawdown headroom
against `peak_equity`. Doing this *before* the kill switch had a chance to
evaluate the real drawdown honestly would have risked masking that drawdown.
This rebaseline was deliberately withheld until the kill switch had already
evaluated and recorded the drawdown against the pre-correction numbers — see
the incident below. Once that evaluation was on the record
(`kill_switch_tripped=True`, `kill_switch_tripped_date=2026-09-09`), the
rebaseline could no longer hide it, and both actions were safe to apply.

## Incident: kill switch tripped a day early by a diagnostic mistake

While investigating the negative-cash issue for the user (a read-only status
check), `momentum_atr.risk.check_kill_switch()` was called directly over SSH.
This function is **not pure** — it writes `state.cash` / `peak_equity` /
`kill_switch_tripped` / `kill_switch_tripped_date` via `repo.update_state()`
and fires a real Telegram alert (`send_message()`) on a False→True trip
transition. `compute_equity()` is the actually-pure function and should have
been used instead for a status-only check.

This call tripped the kill switch on 2026-09-09, roughly a day ahead of the
scheduled 09:17 IST cron evaluation, and very likely sent an unintended
Telegram alert (Telegram credentials confirmed live in the server's `.env`).

The underlying substance was correct and not an artifact of the mistake:
real drawdown at that moment was 44.58% (equity ₹29,054.77 vs. peak
₹52,430.588) against a 25% kill threshold — the kill switch would have
tripped on the scheduled run regardless. The error was in *how* the check
was performed (a side-effecting function called for a read-only purpose) and
its *timing* (a day early, via an ad hoc diagnostic query instead of the
scheduled cron), not in the trip decision itself. This was disclosed to the
user immediately and in full when discovered; no attempt was made to reverse
or hide the early trip, since the drawdown it recorded was real.

## Root cause of the negative-cash state, restated

Not a bug in the rebaseline mechanism or in `_get_effective_cash()` — both
behaved exactly as designed. The negative cash was the correct, honest
consequence of docs/66's write-off having no offsetting cash entry (because
no cash was ever actually received — the shares were never really there to
sell). The fix is a deliberate one-time ledger cutover to reality, not a
patch to the write-off logic.

## What was NOT changed

- `peak_equity` (₹52,430.588) — left as-is. It will update via the existing
  `max(peak, equity)` logic inside `check_kill_switch()` on its next natural
  evaluation (the 2026-09-10 09:17 IST cron run), at which point it is
  expected to rise to roughly the rebaselined equity level (~₹71,500+,
  cash + current holdings value). This is a foreseeable, disclosed
  consequence, not a hidden one.
- `kill_switch_tripped_date` — preserved as the historical record of the
  2026-09-09 trip, per the same-day-in-region reasoning as
  `MAIN_STRATEGY_PAPER_SINCE`-style date markers used elsewhere in this repo.
- Open positions, trade history, other `state` fields — untouched.
- `_get_effective_cash()`, `check_kill_switch()`, and every other function
  fixed in commit `023536a` (broker-failure masking, negative-cash alerting)
  — this doc is a one-time data correction, not a further code change.

## User authorization

Both actions were explicitly authorized by the user in direct response to an
`AskUserQuestion` prompt on 2026-09-09:
- "Fix the internal cash ledger now to reflect real broker cash?" →
  **"Yes, rebaseline it now (Recommended)"**
- "Clear the kill switch so momentum_atr can resume new BUYs?" →
  **"Yes, clear it now"** — an explicit override of the stated recommendation
  ("No, leave it tripped"), accepting the residual risk that 3 of 4 docs/66
  symbols' root cause (WELCORP, ATHERENERG, ASIANENE) remains unconfirmed.

## Script

`scripts/live_cash_rebaseline_20260909.py` — idempotent (no-op if
`state.cash` already matches broker cash within ₹1), fail-closed (aborts,
writes nothing, if the broker cash read fails), dry-run by default
(`--apply` required to write, `--clear-kill-switch` required to also clear
the kill switch). Applied live on 2026-09-09 15:39 IST. Verified post-apply:

| Field | Value |
|---|---|
| `cash` | 38482.92 |
| `peak_equity` | 52430.588 (unchanged, as designed) |
| `kill_switch_tripped` | False |
| `kill_switch_tripped_date` | 2026-09-09 (preserved) |

## Outstanding risk carried forward — superseded

WELCORP, ATHERENERG, and ASIANENE root causes were unconfirmed at the time
this doc was written. Later the same day, the user confirmed (in
conversation, not via an independent order/fill record) that these were
manual actions taken directly in the broker app — see docs/66's
"Addendum (2026-09-09, later)". User-reported evidence, not
system-verified the way CYIENT's cause is, but no longer an open question.
