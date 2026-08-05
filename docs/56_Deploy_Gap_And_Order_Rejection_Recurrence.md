# docs/56 — Deploy-Gap Discovery + Order-Rejection Recurrence (2026-08-05)

## Context

User asked why only 1-2 signals were generated across the ~504-symbol
universe. Answering that question required pulling real data from the live
server (SSH previously failing; user supplied a working key + `algo-key.pem`
mid-session). That investigation surfaced two much larger problems.

## Finding 1 — Order-rejection recurrence (likely = docs/51, not new)

Live server, `logs/daily_run_20260804.log`: both the day's live orders
(SELL CEMPRO.NS, BUY AEGISLOG.NS) were placed at 15:31:15-16 IST and
REJECTED by the broker — "The market is closed." Cron started the run at
15:20 IST; NSE closes 15:30 IST; the run took ~11m15s end-to-end, so order
placement landed a minute past close.

`docs/51` (2026-07-29) already documented this exact failure signature
(runner blows past close -> broker rejects every order) and claimed it
fixed via commit `0580cd5` (thread + `join(timeout=45)` hard deadline around
the network fetch call). That fix would explain why 07-23/24/27/28/29
failed and 07-30 onward should have been clean.

They were not: `daily_run_*.log` was 0 bytes for 07-24, 27, 28, 29, 30, 31
(confirmed via server `journalctl -u cron` — cron fired correctly every
day; 0-byte output is consistent with `flock -n 9 || exit 1` losing the
lock race, not a crash — no tracebacks, no OOM kills, no reboot in 86 days
of uptime). Only 08-04 has real content, and it shows the market-close
rejection, unprotected by any `0580cd5`-style deadline.

**Root cause: `0580cd5` was never deployed.** See Finding 2.

Working theory for the 6 empty-log days (not fully proven — the holding
process is long gone and left no crash trail): a `main.py run --live`
invocation hung (same class of bug docs/51 describes) and held
`/tmp/algov2_runner.lock` for the entire week; every subsequent day's cron
fired, lost the non-blocking `flock -n`, and exited silently with zero
alerting. The hang apparently cleared on its own sometime before 08-04
(no restart, no kill logged) letting that day's run finally proceed — where
it then hit the very close-call this doc opens with.

## Finding 2 — Repository Integrity failure: 3-way divergence

Checked (mandatory before trusting `docs/51`'s "fixed" claim, per charter
Repository Integrity role):

- Local dev machine (this checkout): HEAD 72 commits ahead of GitHub
  `origin/main`. None of those 72 commits — including `0580cd5` — have ever
  been pushed anywhere.
- GitHub `origin/main`: stuck at `998deea`.
- Live server: `646e15f`, confirmed an ancestor of `origin/main` (so it's
  even further behind GitHub, which is itself 72 commits behind local).

Verified directly on the server: `data/providers/upstox_provider.py` still
has the plain `timeout=30` `requests.get` call, no hard-deadline wrapper.
`git cat-file -t 0580cd5` on the server: unknown revision.

This means every fix from this session before today (docs/48-55: C1-C4 live
execution bugs) was also local-only until this doc's commit — none had
reached the server despite being "documented as fixed."

## Fixes applied this session

1. **Server crontab** (deployed directly via SSH, not git-tracked): moved
   the live-run cron from `20 15` to `55 14` IST (~35min buffer before
   close instead of ~10min), wrapped the run in `timeout 900` so a hung
   process can no longer hold the flock indefinitely, and added a Telegram
   alert on flock-acquisition failure (previously: total silence). Old
   crontab backed up to
   `/home/ubuntu/AlgoV2/crontab_backup_20260805_132013.txt` on the server.
   This mitigates the *symptom* (late/stuck runs go undetected) but does
   **not** fix the underlying `0580cd5` deploy gap — that requires an
   actual code deploy, not a cron edit.

2. **`backtest/engine.py` bear-swing BUY loop** (found during task #6
   audit, unrelated to the above): `slot_cash` is computed once at loop
   entry from cash-before-this-loop's-buys and never re-derives from the
   shrinking `cash` across candidates in the same day;
   `can_open_position()` only checks allocation-%% caps, never actual cash.
   Latent — self-balances at the current default `REGIME_SIZE_MULT_BEAR=1.0`
   — but no floor guard existed, so pushing that env-configurable multiplier
   above 1.0 (an active regime-sizing research knob, commit `a731b5d`)
   could silently drive backtest cash negative. Added an explicit
   `est_cost > cash` skip-and-log guard, same pattern as the C1
   `shares_override` cash check. Codex-reviewed (confirmed it closes the
   overspend risk; flagged a minor rank-priority nuance — skip vs. resize —
   accepted as-is, not worth the added complexity for a currently-inactive
   path). Full suite: 144/144 passing after the change.

## Status / open items

- Local repo pushed to `origin/main` and server pulled to match, as of this
  commit — see git log for the actual SHA once done.
- **`0580cd5`'s fix is still not proven to close the 07-30/07-31 gap** —
  those two empty-log days occur *after* its claimed fix date, which is
  itself suspicious independent of the deploy-gap explanation. Worth a
  follow-up read of docs/51 §6 (it already flagged "no repo-wide audit was
  performed" — a different unguarded call site may share the same bug).
  Not re-investigated in this pass; flagging so it isn't lost.
- The stuck-process hypothesis for the 6 empty-log days is plausible and
  consistent with all available evidence but not proven — the process that
  (theoretically) held the lock is gone with no crash trail. The new
  `timeout 900` wrapper prevents a recurrence going forward regardless of
  whether this specific historical explanation is correct.
- No holiday calendar cross-check was done for 07-30/07-31 — treated as
  ordinary trading days based on `date -d` weekday output only (both were
  confirmed non-weekend). Should be low-probability given India's market
  holiday calendar is public knowledge, not revisited here for time.
