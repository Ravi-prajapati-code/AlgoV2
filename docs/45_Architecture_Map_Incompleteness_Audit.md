# Doc 45 — Architecture Map Incompleteness Audit (Codex role)

**Date**: 2026-08-01. Assignment: assume `docs/41_Architecture_Map.md` is not wrong, but **incomplete** — find missing execution paths, hidden dependencies, dynamically-loaded config, reflection, dead/duplicate imports, hidden schedulers, cron jobs, and side effects. Every item below is verified against the live repo (`git log`, `grep`, direct reads), not inferred from doc 41's prose.

## Finding 1 — 10 of 14 scheduled cron jobs are undocumented in doc 41

`scripts/setup_cron.sh` installs **14 cron entries**. Doc 41 documents 2 (`gtt_coverage.py`, `gtt_price_audit.py`). Full inventory, with documentation-coverage status:

| Cron | Schedule (IST) | Script | In doc 41? | Documented anywhere current? |
|---|---|---|---|---|
| `auto_token` | 08:30 Mon-Fri | `scripts/auto_token.py` | No | Only `docs/07` (2026-07-06, stale) |
| `token_reminder` | 08:45 Mon-Fri | `scripts/send_token_reminder.py` | No | Only `docs/07` |
| `reconcile` | 09:20 Mon-Fri | `scripts/reconcile_positions.py` | No | `docs/07`, `docs/30` |
| `gtt_price_audit` | 09:22 Mon-Fri | `monitoring/gtt_price_audit.py` | **Yes** | — |
| `pnl_summary` | 09:25 Mon-Fri | `scripts/daily_pnl_summary.py` | No | Only `docs/07` |
| `daily_run` | 15:45 Mon-Fri | `main.py` (live pipeline) | Yes (described narratively) | — |
| `gtt_coverage` | 15:40 Mon-Fri | `monitoring/gtt_coverage.py` | **Yes** | — |
| `cron_integrity` | 15:35 Mon-Fri | `scripts/cron_integrity_check.py` | No | **Nowhere. Not even doc/07.** |
| `shadow_ledger` | 15:52 Mon-Fri | `scripts/shadow_ledger.py` | No | Only `docs/22` (2026-07-08 handoff doc — a proposal, not a build record) |
| `health_check` | 15:55 Mon-Fri | `scripts/health_check.py` | Yes (name only) | — |
| `shadow_ledger_score` | 16:05 Sat | `scripts/shadow_ledger_score.py` | No | Only `docs/22` |
| `db_backup` | 16:30 Mon-Fri | `scripts/backup_db.py` | No | Only `docs/07` |
| `universe_weekly` | 13:00 Fri | `scripts/universe_scheduler.py --mode weekly` | Partial (mentioned as "its own schedule", not the actual cron line) | — |
| `universe_daily` | 12:00 Mon-Fri | `scripts/universe_scheduler.py --mode daily` | Partial (same) | — |

**Most severe sub-finding**: `shadow_ledger.py` and `shadow_ledger_score.py` run on live cron schedules (weekday afternoons + Saturday) but the only documentation of what "shadow ledger" even is lives in `docs/22_Chat_Handoff_2026-07-08.md` — a session-handoff proposal doc from three weeks before doc 41 was written. Doc 41 does not mention this subsystem exists, what it tracks, or why it's scheduled. Either it's a dead/vestigial cron entry that should be removed, or it's a live subsystem nobody has re-verified since inception — doc 41 cannot distinguish these because it never looked.

## Finding 2 — a second, independent scheduler outside `setup_cron.sh` entirely

`scripts/recovery_manager.py` ("operational watchdog" — checks token validity, whether the daily runner completed, DB health, dashboard liveness; alerts via Telegram; can auto-restart the dashboard) documents **its own crontab lines inline in its docstring**, targeting a different timezone convention (`TZ=UTC` explicitly, vs. the IST-relative cron times `setup_cron.sh` uses) and a hardcoded server path (`/home/ubuntu/AlgoV2`). This is not installed by `setup_cron.sh` — it is a second, separately-maintained scheduling mechanism that a reader of `setup_cron.sh` alone (which is what doc 41 appears to have read) would never discover. Doc 41 does not mention `recovery_manager.py` at all.

This matters concretely: doc 44's P0 finding (`data/universe.py` gitignored) argued that a fresh deploy would break `daily_runner.py` silently. `recovery_manager.py` is exactly the kind of watchdog that *should* catch that — but only if it's actually installed on the server via its docstring-documented manual crontab line, which is unverifiable without the still-blocked SSH access. Doc 41 should have flagged this dependency explicitly rather than omitting the watchdog from the map.

## Finding 3 — reflection-based dynamic import, invisible to a static call-chain trace

`main.py`'s `universe` subcommand does not import `scripts.universe_scheduler` at module load time — it imports it lazily, inside the command handler, via `importlib.import_module("scripts.universe_scheduler")`, after mutating `sys.argv`. A static "trace the call chain" read of `main.py`'s imports (which is how doc 41 was built) will not surface this — `scripts/universe_scheduler.py` only appears in the dependency graph if someone specifically greps for `importlib` or reads every function body, not just the import block. Doc 41's call-chain map should note this as a dynamic edge, not just describe `universe_scheduler.py`'s behavior as if reached through an ordinary import.

## Finding 4 — config is not centralized in `config/settings.py`; doc 41 doesn't say so

30+ files read `os.environ`/`os.getenv` directly, outside `config/settings.py` (includes `broker/base.py`, `strategy/defensive_portfolio.py`, and most of the one-off experiment scripts). Some of this is intentional and fine — it's exactly the mechanism `scripts/robustness_gate.py --env KEY=value` relies on to override config per-run without touching files. But doc 41 presents `config/settings.py` as *the* configuration surface without noting that live-relevant behavior can also be changed by env vars read ad hoc, in files scattered across the tree, with no single enumerable list of "which env vars actually matter." This is a real audit gap: there is currently no way to answer "what is the full set of environment variables that can change live trading behavior" by reading one file — you'd have to grep the whole repo, which is exactly what doc 41 was supposed to save someone from doing.

## Finding 5 — commit-history duplication suggests an undocumented merge/reconciliation path

At least 4 pairs of near-identical commits exist in history with matching messages but different hashes (`5ff13d5`/`a281ce5` "Remove Ignored Holdings feature...", `8398591`/`c4094a1` "Fix ema_50 mislabel bug...", `1221caa`/`ad5c9e2` "Add cron integrity check...", `7e85480`/`1124d81` "Fix origin misclassification..."), all clustered around 2026-07-13 to 07-14 and bracketing `a52acfe 2026-07-21 "Merge server-only deploy commits (df4f050..646e15f) into main"`. This is architecturally significant: it means there are (or were) **two parallel commit lineages — a local one and a server one — that get manually reconciled**, and the reconciliation process is not itself documented anywhere doc 41 covers. Doc 41 describes the codebase as if it has one lineage; it does not have one, and the reconciliation step is exactly where the kind of drift memory has already caught twice (`server_drift_reconcile_deploy_20260717`, `live_server_drift_and_live_mode_20260731`) actually originates.

## Finding 6 — one severe, silent live-trading failure has no execution-path documentation anywhere

Commit `0580cd5` (2026-07-29): *"Live daily runner hung on this exactly 600s/day on 5 straight sessions (07-23 through 07-29), different symbol each day, overrunning market close and getting every BUY/SELL/ADD/ROTATE_ADD rejected by the broker."* This describes **five consecutive trading days where the live system silently failed to execute any order** due to `requests`' `timeout=` being a per-socket-read timeout rather than a total-request deadline. This is not a doc-41-scope architecture gap exactly — it's a finding that doc 41's execution-path description of the live runner (which presumably describes the happy path) never accounted for the fact that the historical-candle fetch step has no wall-clock deadline anywhere else it's called, and there is no evidence any other network call in the live path has one either. Doc 41 should explicitly flag "unbounded network calls in the live critical path" as an architecture-level risk category, not just note that this one instance was patched.

## What I did not find (stated honestly, not as a gap)

- No evidence of a genuine import cycle between core modules (`backtest/engine.py`, `portfolio/manager.py`, `strategy/*`) — the dependency direction is consistently one-way (strategy/portfolio → indicators/data, not back). I checked this directly; did not find a counterexample. Not claiming certainty this is exhaustive — checked the modules doc 41 already names as central, did not do a full transitive closure over every file.
- No evidence of `eval`/`exec`/monkeypatching-style reflection beyond the one `importlib.import_module` call (Finding 3). `getattr(` usage found was all in test files and the broker abstraction layer's polymorphic dispatch, which is ordinary OOP, not hidden dynamism.
- Did not independently verify dead-import claims beyond what doc 41 already lists (`portfolio/optimizer.py`, `risk/manager.py`, etc.) — a full unused-import sweep across the whole tree was out of scope for this pass; flagging as unverified rather than claiming doc 41's dead-code list is complete.

## Net assessment

Doc 41 is accurate for what it covers but its coverage boundary was effectively "the modules involved in the research questions docs/33-40 were asking," not "every scheduled, live-relevant execution path in the repository." Six real subsystems with live cron schedules (`shadow_ledger`, `shadow_ledger_score`, `cron_integrity_check`, `backup_db`, `auto_token`, `send_token_reminder`, `reconcile_positions`) plus one entirely separate watchdog scheduler (`recovery_manager.py`) sit outside doc 41's map. None of this changes doc 43's research priorities — but it does mean "the architecture is understood" was an overstatement, and the Repository Constitution (doc 47) should require doc 41 to be extended to cover the full `setup_cron.sh` + `recovery_manager.py` inventory before it's cited again as a complete reference.
