# 09 — Open Questions & Known Gaps

Everything here is documentation-only as of this dossier — none of these have been fixed as part of
this task, per the standing rule that live-affecting changes require explicit user confirmation
before implementation/deployment. Ordered roughly by live-trading-safety impact.

## 1. Loser-leak recurrence — STRUCTURAL FIX WRITTEN 2026-07-06, live DB correction still pending

`LAURUSLABS.NS` and `THERMAX.NS` were found `status='core'` in the live universe despite being
documented as removed losers; a full audit found **26** leaked symbols total (2 in `core`, 24 in
`watchlist`/`lockout`), not just the 4 originally suspected. Root cause: no structural link
between `config/watchlist_nse.py`'s comments and the DB's promotion state, compounded by the
existing manual-removal `lockout` being time-limited (8 weeks) rather than permanent.

**Fix applied to code**: `config/universe_removed.py::REMOVED_SYMBOLS` — a permanent, no-expiry
block-list of 39 documented strategy-loser symbols, enforced in `universe/manager.py`'s
`refresh()` (self-heals any leak found in any status on every weekly run), `manual_promote()`,
and `add_to_watchlist()`. Verified with 5 new isolated tests (`tests/test_universe_blocklist.py`)
plus the full existing suite (88 tests, all passing). This will self-correct the live DB
automatically on the next scheduled weekly cron refresh with no further action needed.

**Not yet applied**: `scripts/enforce_universe_blocklist.py` (no `--dry-run`) would apply the
correction immediately instead of waiting for the next weekly refresh — this is a live production
DB write, blocked by the sandbox's own permission classifier pending explicit user confirmation
(asked, no response yet). Neither leaked `core` symbol has an open position, so this is not
actively worsening in the meantime. See `08_Project_Memory.md` for full incident detail.

## 2. `portfolio/optimizer.py` is not just dead, it's unimportable

`from strategy.regime import regime_position_factor, Regime` — `Regime` doesn't exist in
`strategy/regime.py`. Verified via direct `python3 -c "import portfolio.optimizer"` — literal
`ImportError`. This makes the entire sophisticated sizing system (regime-factor scaling,
ML-confidence scaling, quarter-Kelly sizing, auto-shrink retry loop) unreachable, corroborating
that `risk/manager.py::RiskManager` is similarly stranded. If this system is ever meant to be
revived, it needs the missing `Regime` symbol defined first, then a real integration decision
(does it replace or coexist with `portfolio/manager.py`'s live sizing?).

## 3. "ema_50" is actually EMA(100) everywhere it's used

Traced through `config/settings.py` → `indicators/trend.py` → `indicators/composite.py`'s
misleading `"ema_50"` dict key → `strategy/entry.py`'s trend-confirmation gate and
`strategy/signals.py`'s `TREND_BREAK` exit. Nothing in this codebase currently implements a real
fast/slow (e.g. 50/100) EMA cross; every place that claims to is comparing two near-identical
~100-period EMAs. Not obviously a directional bug (the gate still fires on *something*), but the
semantics are wrong and any future strategy change reasoning about "the 50 EMA" needs to know it
isn't one.

## 4. `--mode daily` universe safety net never actually scheduled

Fully built, config-enabled, documented in `scripts/universe_scheduler.py`'s own crontab docstring
— but `scripts/setup_cron.sh` only installs `--mode weekly`. The daily volume-collapse check has
never run in production.

## 5. Dormant `LIQUIDBEES_TARGET_WEIGHT` NameError

`strategy/defensive_portfolio.py::build_target_weights()` references a name that's never defined.
Currently dormant only because `LIQUIDBEES_ENABLED` defaults to `False`. If that flag is ever
flipped on without this being fixed first, it will crash at runtime.

## 6. GTT monitoring cron fix — confirm it's actually deployed

`gtt_price_audit.py`/`gtt_coverage.py` cron installation was fixed locally (`ba450d5`) but this
dossier's research could not confirm whether that fix has actually been applied on the live server
(vs. just committed locally). Worth a direct check before assuming these monitors are running.

## 7. Sharpe-ratio methodology inconsistency

`backtest/metrics.py` uses population variance; `scripts/walk_forward.py` uses sample variance.
The two tools' "Sharpe" numbers for an identical equity curve are not exactly comparable. Low
urgency (doesn't affect trading decisions directly) but worth reconciling before either number is
used in a cross-tool comparison or reported externally.

## 8. Confirmed dead code inventory (candidates for deletion or revival — needs a decision either way)

`portfolio/optimizer.py`, `portfolio/app.py`, `risk/manager.py`, `strategy/stock_ranker.py`,
`strategy/market_filter.py` (also latently buggy), `strategy/quality_filter.py`,
`indicators/momentum.py`/`volatility.py`/`volume.py`, `runner/repository.py`,
`runner/intraday_runner.py` (unhardened prototype), `backtest/slippage.py`, `universe/ipo.py` (a
fully-built subsystem that has never fired once — `add_ipo()` has zero callers, 0 DB rows). None of
these are causing active harm by sitting unused, but each represents either wasted past effort (if
truly obsolete) or an unfinished integration (if the intent was to eventually wire it in) —
worth an explicit "keep and finish" vs. "delete" decision per module rather than leaving them in
permanent limbo.

## 9. Config parameters with no consumer

`config/settings.py`'s `MAX_HOLD_DAYS`, `ADX_TREND_THRESHOLD`, `MIN_SIGNAL_SCORE`, and a YAML
`RS_THRESHOLD` override are all read into settings but never consumed anywhere downstream. Roughly
half of `config/risk_config.yaml`'s keys are similarly unread. These are silent — no error, no
warning — so a future engineer could reasonably believe tuning one of them changes live behavior
when it doesn't.

## 10. Schema drift: `db/universe_repo.py`'s runtime `ALTER TABLE`

`save_weekly_metrics()` migrates a 5→6-factor scorer redesign via a runtime `ALTER TABLE` that was
never back-ported into the static `db/schema_universe.sql` file. Anyone provisioning a fresh DB
from the schema file alone would get a schema that doesn't match what's actually running in
production.

## 11. `universe/reporter.py` threshold duplication

`_strategy_loser_section()` hardcodes local copies of thresholds that live authoritatively in
`universe/manager.py`. If the manager's thresholds are ever tuned, this report will silently drift
out of sync and describe stale criteria.

## 12. Cross-agent discrepancy, resolved by cross-reference (informational only)

One research pass (portfolio/risk) stated it could not find `gtt_price_audit.py` anywhere in the
repo. A separate, independently-verified pass (ml/monitoring/config) fully documented that file as
existing, working, and cron-scheduled. This is almost certainly a scope/search-path limitation in
the first agent's own tool environment (it may not have searched outside its assigned file list),
not evidence the file is actually missing — noted here only so it isn't mistaken for a real gap by
a future reader.

## 13. Retroactive audit not done: other positions for stale trailing-stop records

The 2026-07-01 trailing-stop-persistence fix (`c5460f6`) addressed the bug going forward but the
existing open positions at the time were not individually audited for already-stale trail/peak
values baked into the DB before the fix landed. If any of those original positions are still open,
worth a one-time manual check that their persisted trail values reflect reality.

## 14. Strategic: no validated way to pass the system's own gates

The honest baseline (CAGR +12.85%/Sharpe 0.83/MDD 23.67%) fails the system's own validation gates,
and no tested alternative configuration has been found that both improves on it and survives all 4
stress scenarios. This is the central unresolved strategic question for the project — see
`05_Research.md` for the full rejected-experiment history and the two backlog items (VCP entry,
multi-factor regime detection) not yet attempted.

## 15. This dossier's own filename gap

The original request that spawned this 9-document dossier was garbled and its `02_` document name
was unrecoverable. `docs/02_Architecture.md` was written as the best inference given the numbering
gap between `01_Project_Overview` and `03_Strategy` — flag to the user if a different topic was
actually intended for that slot.
