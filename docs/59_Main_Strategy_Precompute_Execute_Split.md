# 59 — Main Strategy Precompute/Execute Split

Date: 2026-08-10

## Incident

On 2026-08-07 the main strategy's live 14:55 IST cron hit 7 consecutive
45s fetch stalls while pulling the ~full symbol universe and was
SIGTERM-killed by the cron's own `timeout 900` wrapper mid-fetch — zero
orders placed, zero exit/trailing-stop checks that day. The provider-level
root cause (a poisoned connection pool after a stall) is already fixed —
`data/providers/upstox_provider.py` now rebuilds its session after a
stall — but the structural risk remained: the slow, stall-prone
fetch/scoring phase and the time-critical order-placement phase shared
one 900s budget immediately before market close.

## Finding that motivated the split

Confirmed empirically via a live DB query (not inferred): Upstox's
historical-candle API never returns a same-day candle mid-session. This
strategy's indicators, relative strength, and regime detection are
therefore provably T-1-close data no matter what time of day they run —
the same property that motivated momentum_atr's own precompute/execute
split (docs/57, docs/58). The slow part can run hours earlier without
changing what it computes.

## Why this is a scoped split, not a full mirror of momentum_atr

Full trace of `runner/daily_runner.py::run()` (890 lines) found a
complication momentum_atr's simpler pipeline didn't have. The normal BULL
momentum path (`generate_signals()`) only decides signal *identity*
(T-1-determined — its `portfolio_value`/`cash` params are dead, never
read in the function body); sizing happens later against live cash. But
the BEAR/defensive/regime-transition branches (`daily_runner.py:556-772`
pre-split line numbers) bake live broker cash directly into each signal's
share count at generation time — `cash_bal = broker.get_available_cash()`
feeds `bear_capital`, `slot_cash`, and `shares_override`. Precomputing
those at 09:30 and executing at 14:55 would size against 6-hour-stale
cash, a real over/under-sizing risk on real capital, specifically in bear
markets.

**Decision**: scope this split to what's safely separable. Fetch + RS +
indicators move to an early precompute step; everything broker-dependent
(sync, live cash, bear/defensive sizing, order placement, kill-switch)
stays in the 14:55 execute step unchanged. This fixes the actual
2026-08-07 failure mode without touching live capital-sizing logic. A
full split (refactoring bear-branch sizing to defer like the BULL path
already does) is deferred as a separate, larger, higher-risk follow-up —
not part of this change.

## Missing-precompute behavior: hard-abort, not fallback

If the 09:30 precompute step fails or never runs, the 14:55 execute step
hard-aborts with a Telegram alert rather than silently falling back to a
live fetch. A fallback would quietly reintroduce the exact 2026-08-07
failure mode on precisely the bad-connectivity days it matters most.
Mitigated by hours of lead time — the failure surfaces by ~09:30-10:00,
not at 14:55 — plus an explicit `--force-live-fetch` CLI flag as a manual
recovery escape hatch that reproduces the original inline fetch path.

## What moved to precompute (`scripts/precompute_main_indicators.py`)

`symbols = get_all_symbols() + ALL_DEFENSIVE_SYMBOLS` → `fetch_all(symbols,
live_mode=True)` → the existing `min_required = max(10, len(symbols)//2)`
partial-universe abort guard (same threshold `daily_runner.py` always
used, not a new one) → the `MIN_WARMUP = 450` thin-symbol warmup warning
→ `compute_rs_for_all(data, index_df)` → `compute_all(data,
rs_data=rs_data)` → `indicators`. Persisted via
`db.repository.save_precompute_indicators()` into the new
`precompute_indicators` table (`UNIQUE(date)`).

`regime`/`market_bullish`/`strong_bull` are stored too, but as
diagnostics only — execute always recomputes those fresh from its own
cheap single-symbol index fetch and only logs a drift warning if they
disagree with the stored value. Trusting the stored row is limited to the
genuinely expensive computation.

## What stays in execute, unchanged

Broker init, `sync_portfolio_with_broker`, live cash override, portfolio
value recompute, hybrid-mode detection, the BEAR/defensive/bear-swing
branches and their sizing, `generate_signals()`, `PortfolioManager
.process_signals()` (order placement, kill-switch, GTT cleanup, snapshot
save), Telegram summary, drift-monitor check.

## New pieces

- `db/schema.sql` — `precompute_indicators` (`UNIQUE(date)`) and
  `sla_checkpoints` (`UNIQUE(date, step)`, step ∈ {PRECOMPUTE, EXECUTION})
  tables. No SLA-watchdog table existed for the main strategy before this
  — same gap momentum_atr had before docs/57/58 closed it there.
- `db/repository.py` — `save_precompute_indicators` /
  `load_precompute_indicators`, `record_sla_checkpoint` /
  `load_sla_checkpoints`. `save_precompute_indicators` serializes via
  `json.dumps(indicators, default=_json_safe)` — `indicators/composite.py`
  builds per-symbol dicts from pandas `.iloc[-1]` reads, which can yield
  `numpy.int64`/`numpy.bool_`; these don't subclass Python `int`/`bool`
  and raise `TypeError` under plain `json.dumps`. Covered by a regression
  test (`tests/test_main_precompute_sla.py
  ::test_save_precompute_indicators_numpy_types_round_trip`).
- `scripts/precompute_main_indicators.py` — the new ~09:30 cron entry
  point described above.
- `runner/daily_runner.py::run()` — new `force_live_fetch: bool = False`
  param. Gates the fetch/RS/indicator block on
  `load_precompute_indicators(today)`; hard-aborts via the existing
  `_alert_run_abort()` helper (now also centrally records an `ABORTED`
  `EXECUTION` checkpoint as a side effect, covering every abort path in
  the function, not just the new one) if missing and `force_live_fetch`
  is `False`. `run()` now returns `True` on full completion, `False` on
  any abort (already checkpointed), `None` on a benign skip (market
  holiday, or a snapshot already exists for today) — `main.py::cmd_run`
  uses this to decide whether to record `EXECUTION OK`, avoiding a false
  "OK" overwrite on an internally-aborted run.
- `main.py::cmd_run` — wraps the `run(...)` call in try/except recording
  `EXECUTION CRASHED` (`_alert_run_abort` fires first, so the CRASHED
  write is the final state on record, not overwritten by the ABORTED side
  effect) for the case the SIGTERM-specific handler
  (`_install_timeout_kill_alert`, added after 2026-08-07) doesn't cover —
  an ordinary uncaught exception mid-run. Adds a `--force-live-fetch` CLI
  flag threaded into `run(...)`.
- `scripts/main_sla_check.py` — mirrors
  `scripts/momentum_atr_sla_check.py`'s `evaluate_sla()` shape exactly.
  `REQUIRED_STEPS = ["PRECOMPUTE", "EXECUTION"]`, RED if either step is
  missing entirely or present but not `OK`.

## Cron changes

New lines (confirmed against the live crontab via SSH, not assumed from
`docs/56` or `scripts/setup_cron.sh`, both previously found stale):

```
30 9 * * 1-5 cd /home/ubuntu/AlgoV2 && flock -n /tmp/algov2_main_precompute.lock -c "timeout 2400 .venv/bin/python scripts/precompute_main_indicators.py" >> logs/main_precompute.log 2>&1
0 16 * * 1-5 cd /home/ubuntu/AlgoV2 && .venv/bin/python scripts/main_sla_check.py >> logs/main_sla_check.log 2>&1
```

The existing 14:55 EXECUTE line is unchanged. 09:30 is staggered 10
minutes after momentum_atr's own 08:50 precompute's worst-case completion
(`timeout 1800` → ends ≤09:20), so the two strategies' full-universe
fetch loops don't compete for the same Upstox account's connections/rate
limit at once. Own lock file, `/tmp/algov2_main_precompute.lock` — never
`/tmp/algov2_runner.lock` or either momentum_atr lock file (docs/56's
flock-race incident is exactly the failure mode this avoids). `timeout
2400` is an estimate (this strategy computes a richer indicator set than
momentum_atr's simpler score) and must be measured on the first real runs.

## Tests

`tests/test_main_precompute_sla.py` (12 tests) — DB round-trip for both
new tables including the numpy-safety regression, plus `evaluate_sla()`
unit tests mirroring momentum_atr's own. `tests/
test_daily_runner_precompute_gate.py` (3 tests) — precomputed row present
skips fetch entirely; missing row without `force_live_fetch` hard-aborts
with no fetch and no fallback; `force_live_fetch=True` reproduces the
original inline fetch/RS/compute path and never consults the precompute
table. Full suite: 184 passed (169 pre-existing + 15 new).

## Verification checklist (post-deploy)

- First live precompute run: `precompute_indicators` row present, `SLA
  PRECOMPUTE OK`, Telegram silent (no alert = success).
- First live execute run after that: log shows `[Precompute] Using
  precomputed indicators from...`, `SLA EXECUTION OK`, order-placement
  timing unaffected (still starts promptly at 14:55).
- Forced-missing-precompute check: execute hard-aborts, Telegram alert
  fires, `SLA EXECUTION ABORTED` recorded.
- `main_sla_check.py` reports RED when only one checkpoint is present,
  GREEN when both are OK.
- Measure actual precompute wall-clock time over the first several
  trading days; adjust `timeout 2400` if needed; confirm no rate-limit
  contention with momentum_atr's 08:50 job in practice.
