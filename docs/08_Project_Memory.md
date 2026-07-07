# 08 — Project Memory (Live Incident History)

Chronological record of live incidents, what caused them, and how each was fixed. This is a
narrative companion to the raw session-memory files under `.claude/.../memory/` — those are the
canonical, continuously-updated source; this file is a point-in-time snapshot for the dossier.

## 2026-06-11 — Dynamic Universe Management System built

Built the promotion/demotion state machine (`universe/manager.py`) that maintains the live
tradeable universe beyond the static `config/watchlist_nse.py` list.

## 2026-06-15 — Universe re-seeded to fix a loser-leak

Discovered stocks documented as "confirmed losers, removed" (BEL, KEI, LUPIN, SOLARINDS) were being
fed back into the live universe via the DB "extras" mechanism. Re-seeded so `candidates` = the
static 119 (at the time), making extras always 0 immediately after the fix. **This same class of
bug has recurred** — see below.

## 2026-07-01 — Live bugfixes batch (4 real bugs)

- Upstox v3 GTT schema break (broker API contract change).
- Trailing-stop floor gap.
- GOLDBEES max-loss dead during BEAR regime.
- `atr_at_entry` dead column.
Git repo freshly initialized and pushed to GitHub this same session.

## 2026-07-01 — Regime-Aware Trailing Stop deployed

Tightens the trail off `peak_price` on a BEAR regime flip, not only on new-high days. Validated on
2 backtest windows before deploy.

## 2026-07-01 — GTT Cancel incident

The regime-trail deploy exposed an Upstox v3 cancel-endpoint bug (fixed same day) plus a
GTT-fires-as-unfillable-LIMIT gap (flagged as an open follow-up at the time — now understood to be
the same class of issue fixed via `gtt_stop_limit_price()`, see `portfolio/manager.py`). Two
separate live incidents needed manual same-day resolution: a CGPOWER duplicate-GTT and a GOLDBEES
naked (unprotected) position period.

## GOLDBEES Safe-Haven Design

Uses a static 7%-from-entry floor plus a regime-flip exit — deliberately NOT an ATR trail (ATR on a
gold ETF behaves differently than on an equity swing position). `manager.py`'s missing-exclusion
bug (commit `96df9bd`) and a matching `backtest/engine.py` fix were both applied; the engine fix
was confirmed to be a no-op via A/B test (commit `59bf83c`) — i.e. it was correct-but-redundant in
practice, not a wasted fix.

## Trailing-Stop Not Persisted bug (fixed `c5460f6`)

The trailing-stop ratchet only lived in memory unless the position was also bought/added/rotated in
the same run. The DB always reloaded a stale trail/peak value otherwise, which could silently
loosen live stop-loss protection on positions that just sat quietly with no other activity. Fixed;
**other pre-existing open positions were not audited retroactively** for stale records at the time
— worth a one-time check if this hasn't been done since.

## 2026-07-02 — Regime Signal Divergence discovered and fixed

Backtest used a raw EMA100 crossover for regime detection; live used the smoothed
`detect_regime()`. Fixed so both use the identical signal, then extensively re-tuned regime
hysteresis and buy/switch-gate decoupling to try to recover the old (bug-inflated) CAGR — every
CAGR-recovering config found failed stress tests. Live kept unchanged. Full detail in
`05_Research.md` / `06_Validation.md`.

## 2026-07-03 — Out-of-Sample Validation built; honest baseline confirmed

Built `scripts/out_of_sample_validator.py`; confirmed the corrected (post-2026-07-02) baseline
fails the system's own gates (CAGR +12.85%/Sharpe 0.83/MDD 23.67%). Old 32.04% number confirmed to
be a backtest-bug artifact, not a real edge.

## 2026-07-03 — GTT Monitoring discovered never installed

`gtt_price_audit.py` and `gtt_coverage.py` were built 2026-07-01 but never added to
`scripts/setup_cron.sh` — they existed, worked, but had never actually run in production. Fixed
locally (commit `ba450d5`); **verify this fix has actually been deployed/applied on the live
server**, since local fix and live deployment are not automatically the same thing for this
project.

## 2026-07-03 — SCORE_DROP_ADD missing broker order

The live-enabled `SCORE_DROP_ADD` path was updating DB shares/cash without ever placing the
corresponding broker buy order — a state-desync bug. Fixed (`c3d4f54`). Related GTT
duplicate-risk and quantity-refresh gaps after ADDs also fixed same batch (`c8c0032`, 2026-07-06).

## 2026-07-06 — Robustness Gate built (Phase 5)

See `06_Validation.md` for full detail — formalizes the validation sequence as one command.

## 2026-07-06 — Loser-leak recurrence discovered (dossier research, DB-verified, UNRESOLVED)

A direct read-only SQL query against the live `db/trading.db` during this dossier's research
confirmed `LAURUSLABS.NS` and `THERMAX.NS` — both explicitly documented as "confirmed losers,
removed" in `config/watchlist_nse.py`'s 2026-06-17 revision comments — currently have
`status='core'` in `universe_candidates`, meaning they are **part of the live tradeable universe
right now**. This is the same class of bug fixed once already on 2026-06-15 (see above); the root
cause each time is that editing the static config file's comments/list does not automatically
propagate to the database — no re-seed, no `manual_remove` entry for already-promoted offenders,
and no structural block-list guard exists to prevent a documented-loser symbol from being
re-promoted. A full audit (below) found the leak was larger than initially suspected: 26 symbols,
not 4.

**Fixed same day**: `config/universe_removed.py::REMOVED_SYMBOLS`, a permanent no-expiry
block-list of 39 documented losers, wired into `universe/manager.py` at every promotion/
watchlist-add/weekly-refresh entry point — enforced going forward regardless of how a symbol
re-enters, and self-healing on the very next weekly refresh for anything already leaked in.
Verified via 5 new isolated tests plus the full 88-test suite, all passing. A companion script,
`scripts/enforce_universe_blocklist.py`, found 26 currently-leaked symbols (2 in `core` —
LAURUSLABS.NS/THERMAX.NS, neither with an open position; 24 in `watchlist`/`lockout`) but applying
it (a live production DB write) was correctly held for explicit user confirmation rather than run
on a general "fix the bug" instruction — still pending as of this writing. See
`09_Open_Questions.md` item 1 for current status.
