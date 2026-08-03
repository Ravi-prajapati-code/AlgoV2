# Doc 51 — Incident: 5 Sessions of Rejected Live Orders (2026-07-23 to 2026-07-29)

**Status**: root cause fixed (`0580cd5`, 2026-07-29). Incident doc written retroactively — per
doc/47 §7.2, a severe live-trading incident (any full-day or multi-day failure to execute intended
orders) requires a dedicated writeup within the same week it's discovered, covering trigger,
detection lag, watchdog gap, and impact estimate. Doc/45-46 flagged that this one had none,
anywhere, not even in memory. This closes that gap.

## 1. What triggered it

`requests`' `timeout=` parameter is a **per-socket-read timeout**, not a total-request deadline —
a connection that keeps trickling bytes (even slowly) never trips it, so a stalled/degraded
connection can sail past the configured timeout uncaught. `data/providers/upstox_provider.py`'s
historical-candle fetch used this parameter as if it were a hard deadline.

On 5 straight trading sessions — 2026-07-23 (Thu), 07-24 (Fri), 07-27 (Mon), 07-28 (Tue), 07-29
(Wed), weekend skipped — a single symbol's candle fetch hung for exactly 600 seconds before giving
up, a different symbol triggering it each day. That 600s overran the live daily runner past market
close, and **every BUY/SELL/ADD/ROTATE_ADD order for that day's run was rejected by the broker**
(orders submitted after close are not accepted).

## 2. How long it went undetected

Fixed same-day as the last (5th) occurrence — `0580cd5` was committed 2026-07-29 19:37 IST, after
that day's market close. That means the first 4 occurrences (07-23, 24, 27, 28 — the pattern
recurred across 4 separate prior sessions before being diagnosed) went completely unnoticed at the
time they happened. Nothing in the live pipeline surfaced the failure as it occurred; it was only
caught retroactively when the pattern was investigated.

## 3. Why the watchdog didn't catch it sooner

`scripts/recovery_manager.py` exists specifically to catch this failure mode — its own docstring
documents a cron check ("check 2": today's daily runner log exists and completed successfully,
matched against `RUNNER_COMPLETE_MARKER = "=== Daily Runner complete:"`) scheduled at 10:15 UTC /
15:45 IST, alerting via Telegram on failure. A runner hung 600s past close would very plausibly
have tripped this check on the very first occurrence (07-23).

It didn't, because **the crontab is only documented in the script's own docstring — it was never
actually installed** via `scripts/setup_cron.sh` (confirmed again just now: `grep recovery_manager
scripts/setup_cron.sh` matches nothing). This is doc/45 Finding 2, still true as of this writing.
The watchdog that should have caught this in one session instead let it recur silently for 5.

## 4. Fix

`0580cd5` wraps the network call in a daemon thread with `join(timeout=45)`, abandoning stalled
connections and continuing the fetch loop instead of blocking indefinitely — closes the root
cause (unbounded per-socket timeout). Does **not** by itself close the detection gap in §3 — that
requires actually installing `recovery_manager.py`'s crontab, which remains open (tracked as a
Phase-0 Repository Integrity item, not resolved by this doc).

## 5. Estimated P&L / opportunity-cost impact

**Not quantified.** Computing this honestly requires reconstructing, for each of the 5 affected
sessions, what the intended BUY/SELL/ADD/ROTATE_ADD orders were (from that day's `daily_run_*.log`
or DB `research_hypotheses`-adjacent trade-intent record, if one exists) and what those positions
would have realized under normal execution versus what — if anything — the strategy did once
execution resumed. That reconstruction was not done here; stating a number without it would be
inventing certainty the evidence doesn't support (`CLAUDE.md`: "never invent certainty"). Flagged
as open follow-up work, not silently omitted.

## 6. Outstanding items (not closed by this doc)

1. Install `recovery_manager.py`'s watchdog crontab on the live server (doc/45 Finding 2, doc/47
   §6.4) — the concrete fix that would have caught this on day 1 instead of day 5.
2. Quantify §5's impact if a trade-intent reconstruction becomes feasible.
3. Confirm no other `requests` call in the live path shares the same per-socket-vs-total-deadline
   mistake — this fix was applied to one call site (`data/providers/upstox_provider.py`'s
   historical-candle fetch); a repo-wide audit for the same pattern was not performed as part of
   this fix.
