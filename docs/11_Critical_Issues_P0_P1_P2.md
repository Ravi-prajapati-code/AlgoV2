# 11 — Critical Issues Review: P0 / P1 / P2 (Phase 2)

**Scope**: A severity-triaged inventory of the system's current known issues — what's broken, what's
silently inert, and what's a real-but-unresolved strategic gap. This is a *diagnosis and priority*
document, not a build plan: each item states status, why it matters, evidence, and the minimum
immediate action, with no multi-step implementation sequencing. That belongs to Phase 3.

**Severity definitions**:
- **P0** — active or latent live-money/data-integrity risk. Warrants action independent of any
  roadmap; a P0 sitting unresolved is not "backlog," it's exposure.
- **P1** — real correctness, reliability, or strategic risk. Not actively losing money today, but
  will cause harm, a bad decision, or a future incident if left as-is.
- **P2** — technical debt or an undecided architectural fork. No active or near-term harm from
  leaving it alone; the cost is drift and accumulated ambiguity, not an incident.

Built from: `docs/08_Project_Memory.md`, `docs/09_Open_Questions.md`, `docs/10_Quantitative_Research_Review.md`,
and direct verification against current code/git state as of this review.

**Current counts**: 4 P0, 8 P1, 6 P2.

---

## P0 — Live-money and data-integrity risk

### P0-1. Loser-leak fix is written and tested but not committed, and the live DB correction is still unapplied
**Status**: `config/universe_removed.py`, the `universe/manager.py` edits, `scripts/enforce_universe_blocklist.py`,
and `tests/test_universe_blocklist.py` all exist on disk, uncommitted (`git status` confirms this as
of this review). 26 block-listed symbols are still live-leaked in the production DB (2 in `core` —
`LAURUSLABS.NS`, `THERMAX.NS`, neither with an open position; 24 in `watchlist`/`lockout`).

**Why it matters**: This is the *second* occurrence of this exact bug class (first: 2026-06-15).
An uncommitted fix is not a deployed fix — if this machine's working tree were lost or reset before
committing, the fix disappears and the leak resumes silently. Separately, `manager.py::refresh()`
self-heals the leak on the next weekly cron run regardless of whether the immediate script is run,
so the *drift* is bounded, but the fix existing only in an uncommitted working tree is the more
urgent exposure.

**Evidence**: `08_Project_Memory.md` (2026-07-06 entry), `09_Open_Questions.md` item 1, 5 passing
isolated tests + full 88-test suite green.

**Immediate action**: Commit the fix (no live-affecting risk in the commit itself). Applying
`scripts/enforce_universe_blocklist.py` without `--dry-run` is a separate live DB write and remains
gated on your explicit confirmation, as before — the two are independent: one is safe to do now,
the other still needs a yes/no from you.

### P0-2. Trailing-stop persistence bug (`c5460f6`) — pre-existing open positions never retroactively audited
**Status**: The bug (DB reloads a stale trail/peak value for any position that wasn't also
bought/added/rotated in the same run, silently loosening live stop protection) was fixed going
forward. Positions that were already open *before* the fix landed were never individually checked
for a stale trail value already baked into the DB.

**Why it matters**: If any position opened before the fix is still open today, its persisted stop
level may be looser than the strategy intends — a live capital-protection gap, not a hypothetical
one, for as long as that position remains open.

**Evidence**: `08_Project_Memory.md` ("Trailing-Stop Not Persisted bug"), `09_Open_Questions.md`
item 13.

**Immediate action**: A one-time read query against `db/trading.db`'s open positions, comparing
persisted `trail_stop`/`peak_price` against what the current ratchet logic would compute from price
history since entry. Read-only; no live write required to find out whether this is still exposure
or already moot.

### P0-3. GTT monitoring cron fix — not confirmed deployed on the live server
**Status**: `gtt_price_audit.py`/`gtt_coverage.py` cron installation was fixed locally (`ba450d5`).
Whether this has actually been applied on the production server (vs. only committed locally) has
not been verified.

**Why it matters**: This directly covers the same incident category that already caused two same-day
live incidents on 2026-07-01 (CGPOWER duplicate GTT, GOLDBEES naked position period). If the fix is
committed but not deployed, the monitoring that would catch a recurrence of that exact incident
class is not actually running.

**Evidence**: `08_Project_Memory.md` (2026-07-03 entry), `09_Open_Questions.md` item 6.

**Immediate action**: Check the live server's crontab directly (`crontab -l` on the production host,
or equivalent) for the `gtt_price_audit.py`/`gtt_coverage.py` lines. Read-only verification.

### P0-4. `--mode daily` universe safety net never actually scheduled
**Status**: The daily volume-collapse check (`scripts/universe_scheduler.py --mode daily`) is fully
built, config-enabled, and documented in its own crontab docstring — but `scripts/setup_cron.sh`
only installs `--mode weekly`. It has never run in production.

**Why it matters**: This is a designed safety net for a fast-moving risk (sudden volume collapse in
a held or candidate symbol) that only checks weekly instead of daily as intended. Not an active
incident, but a live risk-detection gap that has existed since the universe system was built
(2026-06-11) without anyone noticing it wasn't wired in — the same "built but not connected" pattern
as P0-1 and P0-3.

**Evidence**: `09_Open_Questions.md` item 4.

**Immediate action**: Add the `--mode daily` line to `scripts/setup_cron.sh` and deploy. Low blast
radius (it's a read/flag job, not an order-placing one) but still a live-server crontab change —
confirm before applying.

---

## P1 — Real risk, not currently on fire

### P1-1. No validated configuration passes the system's own gates
**Status**: The honest baseline (CAGR +12.85%, Sharpe 0.83, MDD 23.67%) is what's live, and it fails
the system's own validation gates. No tested alternative has been found that both improves on it and
survives the full OOS + 4-stress-scenario protocol.

**Why it matters**: This is the central strategic gap — the system is knowingly running below its
own bar because nothing better has cleared validation, not because the bar was lowered. It's not an
active bug (the running configuration is the correctly-validated *honest* one), but it means every
past improvement attempt has failed for a structural reason now understood via Phase 1: the edge is
concentrated in a small number of large winners (top 10 trades = 149.9% of total P&L), so tuning
thresholds reshuffles which of those trades get captured rather than improving a stable statistical
edge.

**Evidence**: `06_Validation.md`, `09_Open_Questions.md` item 14, `10_Quantitative_Research_Review.md`
§9 (CAGR ceiling explanation) and Final Section (research directions #1-3 target this directly).

**Immediate action**: None owed here beyond what Phase 1 already queued — this is a research
problem, not an operational one. Flagged as P1 rather than P0 because the live system is running a
known-honest, known-validated (if underperforming) configuration, not a broken one.

### P1-2. `BEAR_SWING_BUY` is net-negative in the current live-relevant evidence
**Status**: 20 trades, 40% win rate, net **-₹3,600** over the full backtest window — the only entry
trigger type with negative full-window P&L. It is currently active in live trading.

**Why it matters**: A defensive sleeve designed to add value during bear regimes is, per current
evidence, not earning its keep. n=20 is small enough that this isn't proof it's harmful, but it's
the one live-active mechanism the data doesn't currently support.

**Evidence**: `10_Quantitative_Research_Review.md` §2, §5, §8; Final Section research direction #4
(dedicated OOS/stress test, not a redesign, is the recommended next step).

**Immediate action**: None beyond the already-queued research direction. Not urgent enough to
disable live given the small sample, but worth watching.

### P1-3. Return concentration means near-term live performance can look flat or negative for extended stretches by design
**Status**: 90.4% of total realized P&L comes from 5 trades; 149.9% from 10 trades (the other 146
trades net -₹34,088 collectively). This is a structural property of the live strategy, not a bug.

**Why it matters**: This is an operational/expectations risk rather than a code risk: if the next
several months don't happen to contain one of the rare large-trend episodes the strategy depends on,
realized live performance will look like a drawdown even though nothing is malfunctioning. Without
this being explicit, a normal dry stretch could be misread as a new bug and trigger an unnecessary
emergency re-tune (which the research history shows tends to make things worse, not better).

**Evidence**: `10_Quantitative_Research_Review.md` §1, §9.

**Immediate action**: None code-side. Worth stating plainly as an operating expectation: the
strategy's return profile is lumpy by design, and a quiet multi-month stretch is not by itself
evidence of a problem.

### P1-4. `ema_50` is actually EMA(100) everywhere it's used
**Status**: Traced through `config/settings.py` → `indicators/trend.py` → `indicators/composite.py`'s
`"ema_50"` dict key → `strategy/entry.py`'s trend gate and `strategy/signals.py`'s `TREND_BREAK` exit.
No real fast/slow EMA cross exists anywhere in the codebase.

**Why it matters**: Phase 1 quantified the consequence directly: `ema100_dist_pct_at_entry` and
`ema150_dist_pct_at_entry` correlate at rho=0.93 — two nominally different lookbacks producing
near-identical signal. This isn't just a naming nit; it means a feature family presented as having
4 independent dimensions (20/50/100/150) actually has meaningfully fewer, and any future reasoning
about "the 50 EMA" as a distinct fast signal is working from a false premise.

**Evidence**: `09_Open_Questions.md` item 3, `10_Quantitative_Research_Review.md` §6.

**Immediate action**: None urgent (not a directional bug — the gate still fires on a real signal,
just a mislabeled one). Relevant primarily so it isn't accidentally load-bearing in future changes;
ties into Phase 1 research direction #3.

### P1-5. Silent config parameters with no consumer
**Status**: `config/settings.py`'s `MAX_HOLD_DAYS`, `ADX_TREND_THRESHOLD`, `MIN_SIGNAL_SCORE`, and a
YAML `RS_THRESHOLD` override are read into settings but never consumed downstream. Roughly half of
`config/risk_config.yaml`'s keys are similarly unread.

**Why it matters**: These fail silently — no error, no warning. A future engineer (or you, months
from now) could reasonably tune one of these expecting a live behavior change and get none, with no
signal that anything is wrong. This is a correctness-adjacent trap, not a currently-active bug.

**Evidence**: `09_Open_Questions.md` item 9.

**Immediate action**: None urgent. Worth a decision (wire it in or delete it) whenever these
specific parameters next come up in a tuning conversation, so the trap isn't stepped in blind.

### P1-6. `strategy/market_filter.py` is dead *and* latently buggy
**Status**: Confirmed unreferenced by any live call path, and independently confirmed to contain a
bug if it were ever invoked.

**Why it matters**: Distinct from the rest of the dead-code inventory (P2 below) because this one
isn't just unused, it would misbehave the moment someone wired it back in without first fixing it —
a landmine specifically, not just clutter.

**Evidence**: `09_Open_Questions.md` item 8.

**Immediate action**: None while it stays uncalled. Flag before anyone attempts to revive it.

### P1-7. Schema drift: `db/universe_repo.py`'s runtime `ALTER TABLE` never back-ported to `schema_universe.sql`
**Status**: `save_weekly_metrics()` migrates a 5→6-factor scorer redesign via a runtime `ALTER TABLE`
that the static schema file doesn't reflect.

**Why it matters**: Anyone provisioning a fresh DB from the schema file alone (disaster recovery, a
new environment, a fresh server) gets a schema that doesn't match what's actually running in
production. Not an active issue on the current running instance, but a real gap the moment a fresh
DB is ever needed.

**Evidence**: `09_Open_Questions.md` item 10.

**Immediate action**: Back-port the `ALTER TABLE` into `schema_universe.sql` — low-risk, no live
data touched, purely a file edit for future provisioning correctness.

### P1-8. Sharpe-ratio methodology inconsistency between tools
**Status**: `backtest/metrics.py` uses population variance; `scripts/walk_forward.py` uses sample
variance. The two tools' "Sharpe" for an identical equity curve are not numerically comparable.

**Why it matters**: Doesn't affect any live trading decision directly, but risks a false
apples-to-apples comparison if a Sharpe number from one tool is ever quoted against the other's
threshold (e.g., in a future validation gate or external report).

**Evidence**: `09_Open_Questions.md` item 7.

**Immediate action**: Standardize on one convention next time either tool is touched; not worth a
dedicated pass on its own.

---

## P2 — Technical debt and undecided forks (no active or near-term harm)

### P2-1. The "build but never connect" pattern — dead-code inventory needs an explicit per-module decision
**Status**: `portfolio/optimizer.py` (confirmed **unimportable** — `from strategy.regime import
regime_position_factor, Regime` fails because `Regime` doesn't exist in `strategy/regime.py`),
`portfolio/app.py`, `risk/manager.py`, `strategy/stock_ranker.py`, `strategy/quality_filter.py`,
`indicators/momentum.py`/`volatility.py`/`volume.py`, `runner/repository.py`,
`runner/intraday_runner.py` (unhardened prototype), `backtest/slippage.py`, `universe/ipo.py`
(fully built, zero DB rows, `add_ipo()` has zero callers — never fired once).

**Why it matters**: This is the cross-cutting pattern the external reviewer's critique correctly
identified and that I independently verified against the code: several sophisticated subsystems
(regime-aware position sizing, quarter-Kelly sizing, auto-shrink retry, IPO qualification) were
built to real depth and then never wired into the live execution path. None of this is causing
active harm by sitting unused — but each one represents either wasted effort (if truly abandoned)
or an unfinished integration (if the intent was always to connect it eventually), and leaving it in
permanent limbo means the codebase's actual capability surface is smaller than its file listing
suggests.

**Evidence**: `09_Open_Questions.md` items 2 and 8 (direct `ImportError` verification for
`portfolio/optimizer.py`).

**Immediate action**: None urgent — this is a "decide, don't just discover" item for Phase 3, not a
fix. Listed here because its severity (none active) needed to be established before Phase 3 can
reasonably propose keep/finish/delete per module.

### P2-2. Dormant `LIQUIDBEES_TARGET_WEIGHT` `NameError`
**Status**: `strategy/defensive_portfolio.py::build_target_weights()` references an undefined name.
Currently inert only because `LIQUIDBEES_ENABLED` defaults to `False`.

**Why it matters**: Zero risk today; would crash immediately at runtime the moment that flag is ever
flipped on without this being fixed first.

**Evidence**: `09_Open_Questions.md` item 5.

**Immediate action**: Fix before ever flipping `LIQUIDBEES_ENABLED`, not before.

### P2-3. `universe/reporter.py` threshold duplication
**Status**: `_strategy_loser_section()` hardcodes local copies of thresholds that live authoritatively
in `universe/manager.py`.

**Why it matters**: If the manager's thresholds are ever tuned, this report silently drifts out of
sync and describes stale criteria — a documentation-correctness issue, not a trading-correctness one.

**Evidence**: `09_Open_Questions.md` item 11.

**Immediate action**: None urgent; fix by having the report import from the manager's thresholds
next time either file is touched.

### P2-4. Universe-pruning evidence base has unquantified small-sample fragility
**Status**: Phase 1's spot-check of 2 of the 39 block-listed symbols found both diverged from their
documented removal evidence in a fresh backtest — one in magnitude, one in sign.

**Why it matters**: Doesn't invalidate the block-list policy (removing habitually-underperforming
names as a class remains reasonable), but means an unknown fraction of the 39 documented "confirmed
loser" judgments may rest on samples too small (1-6 trades) to trust individually.

**Evidence**: `10_Quantitative_Research_Review.md` §5, §8; Final Section research direction #5.

**Immediate action**: None urgent — already queued as a Phase 1 research direction (systematic
sensitivity sweep across all 39 symbols), not a code fix.

### P2-5. Cross-agent discrepancy on `gtt_price_audit.py` (informational, resolved)
**Status**: One earlier research pass reported being unable to find `gtt_price_audit.py`; a separate
pass fully documented it as existing and cron-scheduled (the scheduling claim is what P0-3 above
re-verifies independently).

**Why it matters**: Almost certainly a scope/search-path limitation in the first pass's tool
environment, not evidence the file was ever actually missing. Recorded only so a future reader
doesn't mistake it for a live discrepancy.

**Evidence**: `09_Open_Questions.md` item 12.

**Immediate action**: None — informational only.

### P2-6. Dossier numbering gap (`02_Architecture.md`)
**Status**: The original request that spawned the 9-document dossier had a garbled `02_` filename;
`02_Architecture.md` was written as the best inference given the gap between `01_Project_Overview`
and `03_Strategy`.

**Why it matters**: Purely administrative — flagged in case a different topic was actually intended
for that slot.

**Evidence**: `09_Open_Questions.md` item 15.

**Immediate action**: None — confirm with you only if it ever becomes relevant.

---

## What's already resolved (context, not part of this triage)

For completeness, not because they need action: the Upstox v3 GTT schema break, trailing-stop floor
gap, GOLDBEES max-loss-during-BEAR gap, `atr_at_entry` dead column, the GTT cancel-endpoint bug, the
regime-signal divergence (backtest vs. live), `SCORE_DROP_ADD`'s missing broker order, and the GTT
duplicate-risk/quantity-refresh gaps were all found and fixed in the 2026-07-01 through 2026-07-06
window (`08_Project_Memory.md`). None reopened.

---

*Phase 3 (12-24 month Institutional Roadmap) builds on this triage — specifically the P2-1
keep/finish/delete decisions and the P1-1 research-direction follow-through — but is a separate
deliverable, per instruction, and is not included here.*
