# 29. Project Governance

2026-07-11. Three rules, one classification scheme, one declaration.
This document exists because `docs/28` changed what "trustworthy
result" means for this project — not because of dead code or config
drift, but because **backtest and live are not running the same
strategy**. That's not an engineering-hygiene issue, it's a scientific
validity issue: every experiment run through `robustness_gate.py`
answers the question "does this help the simulated strategy," and
until `docs/28`'s headline finding closes, the simulated strategy is
not the live one.

## Declaration: Research Phase 1 is complete

Phase 1's question was "is there a real edge here." That question is
answered — `docs/25` §2 settled it: the signal is real, ranking
refinement is exhausted and null, the leak was never stock-picking.

Phase 2's question is different: **build a research platform whose
backtests are faithful, reproducible, deterministic, and
architecturally clean enough that every future experiment can be
trusted.** That is now the milestone — not another CAGR number. Every
strategy improvement found before this platform exists rests on
ground that can shift under it.

**Track B (research) is frozen as of 2026-07-11** until Rule 1 (below)
passes. This supersedes the standing "actionable now" list in
`docs/27` §Bottom line — E6 (`MAX_POSITIONS=5`) and every other open
research item wait for the fidelity gate, not the other way around.

## The pipeline change

Before:

```
Idea → Robustness Gate → PASS
```

Now:

```
Idea → Implementation Fidelity → PASS → Robustness Gate → PASS → Production
```

Robustness on the wrong implementation is still the wrong answer.

## Rule 1 — No research is accepted until Implementation Fidelity = PASS

"Implementation Fidelity" is not a feeling, it's a checklist, sourced
directly from `docs/28`'s open findings. All four must hold before any
`robustness_gate.py` verdict counts as trustworthy:

- [x] **Backtest simulates every default-ON live position-management
  rule.** **CLOSED 2026-07-11** — `ROTATION_ENABLED`,
  `RIDE_WINNER_ENABLED`, `SCORE_DROP_EXIT_ENABLED` ported into
  `backtest/engine.py` (both the exit-side logic and the buy-side
  `SCORE_DROP_EXIT` skip). Required adding `composite_rank`
  (RS-rank x ATR%, cross-sectional percentile per day) to
  `_precompute_all` — it never existed in backtest indicators at all,
  a deeper gap than the three missing flag blocks alone. The shared
  `_is_score_declining` helper was promoted out of `portfolio/manager.py`
  into `strategy/defensive_portfolio.py` as public `is_score_declining`
  so both call sites use one implementation (Rule 3). Verified via full
  test suite (90/91 pass, same pre-existing failure as before) plus a
  synthetic multi-symbol smoke run confirming `RIDE_WINNER_OUT`,
  `SCORE_DROP_EXIT`, and the buy-side skip all fire and execute cleanly.
- [x] **No known backtest/live exit-logic gap open.** **CLOSED
  2026-07-11** — live's bear-swing `TREND_BREAK` exit
  (`runner/daily_runner.py:592-600`, 2-day EMA50 break via
  `pos.days_below_ema50`) ported into `backtest/engine.py`'s bear-swing
  exit block, same field already existed on `db.models.Position`.
- [x] **The data path for a gate run is pinned/versioned, not subject
  to live-API-fallback drift.** **CLOSED 2026-07-11** — replaced
  `str(date.today())` with a fixed `DEFAULT_GATE_END = "2026-06-04"`
  constant (`scripts/out_of_sample_validator.py`, imported by
  `scripts/robustness_gate.py`'s `run_full_and_oos_arm`), verified as
  the latest date `db/trading.db`'s `ohlcv_cache` has for ALL 211
  cached symbols. Also added a fail-loud guard: `run_window()` now sets
  `REQUIRE_CACHED_DATA=1` in the subprocess env, and
  `data/fetcher.py::_fetch_raw` raises `RuntimeError` instead of
  silently hitting the live Upstox API when that flag is set. Verified
  live: running the pinned window immediately surfaced a real gap
  (`TMCV.NS`, cached only from 2025-11-12 — a recently-listed symbol
  with no data before that, likely a demerger) that would previously
  have been silently patched with a live call. That gap is a separate,
  lower-priority data-completeness issue (young/recently-listed
  symbols lacking full 2022-01-01 history) — not a defect in this fix,
  and not required to close this checklist item, since the item's ask
  was "pinned + fails loud instead of silently drifting," which is now
  true and verified. A full gate run will need that cache gap
  addressed (or the young symbol excluded/point-in-time-filtered) as a
  practical prerequisite before `run_full_and_oos_arm` can complete
  end-to-end again.
- [x] **No uncommitted config drift at run time.** **CLOSED
  2026-07-11** — `scripts/robustness_gate.py::check_config_drift()`
  runs at the top of `main()`, before any backtest: (1) `git status
  --porcelain -- config/` must be clean, or it aborts listing the
  dirty files (exact precedent: the stray `max_open_positions=5` key);
  (2) every strategy-relevant env var (parsed from `os.getenv("KEY",
  "default")` call sites in `config/settings.py` and
  `strategy/defensive_portfolio.py`, merged with `.env` via
  `dotenv_values` the same way `main.py`'s `load_dotenv(override=True)`
  would see it) must match its coded default unless it's part of the
  run's explicit `--env` candidate list — otherwise the "baseline" arm
  would silently be non-default. Verified live against the real repo:
  correctly caught the actual uncommitted `config/settings.py` +
  `config/strategy_config.yaml` changes currently sitting in this
  working tree (the `DD_THROTTLE_DISABLED_ENABLED` flag addition and
  dead-yaml-key cleanup from an earlier session) and aborted.
  **Near-miss caught and fixed in the same pass**: the first version of
  this check scanned `config/settings.py`'s `os.getenv` defaults
  without excluding credentials, and printed real secrets (Upstox API
  key/secret/access token, Telegram bot token) in plaintext during
  testing. Fixed immediately with a `_CREDENTIAL_KEY_RE` blocklist
  (`TOKEN|SECRET|KEY|PASSWORD|PIN|TOTP|MOBILE|CHAT_ID`) that skips
  those keys before their value is ever read into the output — but the
  secrets were already printed once in a local test invocation. Flagged
  to the user; those credentials should be considered for rotation
  since they appeared in plaintext in a tool-output transcript.

**Current status: 4 of 4 closed.** Every gate verdict produced before
all four were checked — including this session's E1 DD-throttle
ablation and the RSI-threshold sweep — remains **provisional** until
re-confirmed under the now-fixed engine + data path (see "Next
concrete actions" below). `docs/25`'s core finding (signal real,
ranking exhausted) was derived from trade-attribution work on actual
historical trade/decision logs, not from gate verdicts, and is
unaffected.

**Both practical blockers between "checklist closed" and "next gate run
actually completes" are now also resolved (2026-07-11):**
1. `config/settings.py` + `config/strategy_config.yaml` committed
   (`9649786`) — `config/` is git-clean, `check_config_drift()` passes.
2. `TMCV.NS`'s cache gap: a live backfill attempt confirmed Upstox has
   no data for it before 2025-11-12 (a genuine start-of-series, likely
   the Tata Motors CV demerger listing date — not a fillable cache
   gap). This exposed a bigger pre-existing issue in the process:
   `get_all_symbols_as_of()` raises `UniverseHistoryUnavailable` for
   any date before 2026-07-06 (point-in-time watchlist tracking's
   start), and `main.py`'s fallback for that case was silently using
   *today's* full symbol list applied retroactively to the whole
   historical window — the already-flagged, deliberately-deferred
   universe-lookahead issue, surfacing here as a symptom. Rather than
   reopen that larger question, added a narrowly-scoped guard:
   `data/fetcher.py::filter_symbols_with_insufficient_history()`,
   invoked from `main.py::cmd_backtest` only when
   `REQUIRE_CACHED_DATA=1`, drops any symbol whose cache starts after
   the run's warmup-start instead of hard-failing the whole gate on
   one young symbol. Verified end-to-end: `REQUIRE_CACHED_DATA=1
   python3 main.py backtest --start 2022-01-01 --end 2026-06-04`
   now completes cleanly. Full test suite re-run again: 90/91, same
   pre-existing unrelated failure.

**The full/OOS gate run is now unblocked and ready to execute** — the
next step is item 5 below (re-run the gate suite against the
previously-provisional verdicts).

## Rule 2 — No strategy enters production until Robustness Gate = PASS

Unchanged from existing practice (`scripts/robustness_gate.py`: full
window + train/test OOS + 4 stress scenarios, no sign-flip on any
stress metric). What changes is that a PASS obtained while Rule 1 is
FAIL does not carry forward automatically — it must be re-confirmed
once Implementation Fidelity closes. Concretely: the DD-throttle
removal (E1) and the RSI-threshold rejection (A2) should both be
re-run once the backtest engine simulates rotation/ride-winner/
score-drop-exit, since that's a real change to baseline behavior that
could shift either verdict in either direction.

## Rule 3 — One source of truth

No future code may create duplicate strategy logic, duplicate
indicators, or duplicate configuration. One implementation, always. If
a rule needs to run in more than one execution context (backtest vs.
live), it must be one shared function called from both, not two
independently written copies kept in sync by hand — the current
`docs/28` duplicate-logic list (rank-replacement eviction, drawdown
throttle sizing, the entire indicator stack, `defensive_portfolio.py`'s
21-parameter config surface) is exactly the failure mode this rule
exists to prevent. This is enforced prospectively starting now; the
existing backlog is tracked as Class 3 below, not a blocker for Rule 1
or 2.

## Issue classification

Not every finding deserves the same urgency. Four classes, in priority
order:

| Class | Name | Examples | Why it matters |
|---|---|---|---|
| **1** | Scientific Validity | Live/backtest mismatch, look-ahead, wrong execution timing, non-reproducible data path | Invalidates research — the experiment answers a different question than the one asked |
| **2** | Financial Risk | Wrong sizing, wrong exits, wrong allocations, a diagnostic tool that misreports live behavior | Loses money or misleads a human making a capital decision |
| **3** | Maintainability | Duplicate code, dead config, dead code | Slows development, latent risk if duplicates silently diverge |
| **4** | Cosmetic | Comments, naming, formatting | Lowest priority |

Applied to `docs/28`'s open findings:

| Finding | Class |
|---|---|
| Backtest missing rotation/ride-winner/score-drop-exit | **1** — closed 2026-07-11 |
| Reproducibility gap (live-API fallback, `date.today()` defaults) | **1** — closed 2026-07-11 |
| Cache gap on young/recently-listed symbols (e.g. `TMCV.NS`) blocks full-window gate runs against `DEFAULT_HIST_START` | **1** — closed 2026-07-11 via `filter_symbols_with_insufficient_history()` (narrow fix; the underlying universe-lookahead issue it surfaced remains separately tracked) |
| Bear-swing `TREND_BREAK` exit missing from backtest | **1** — closed 2026-07-11 |
| `risk/manager.py` misreports live drawdown-throttle sizing | **2** |
| DD-throttle live deploy decision (pending, `.env` reverted to off) | **2** |
| Uncommitted config drift currently sitting in the working tree (`config/settings.py`, `config/strategy_config.yaml`) | **2** — committed `9649786`, resolved |
| First draft of `check_config_drift()` briefly printed real credentials (Upstox API key/secret/access token, Telegram bot token) in plaintext before the credential blocklist was added same-session | **2** — fixed same session; those credentials were exposed in a tool-output transcript and should be considered for rotation |
| Dead yaml config (`exit:` block, `RS_THRESHOLD` key), dead `ML_ENABLED` `.env` key, stale `.yaml.bak` | **3** |
| Duplicated indicator stack (`composite.py` vs `engine.py`) | **3** (currently equivalent — becomes Class 1 the moment they silently diverge) |
| Orphaned modular indicators, unused ATR sizer, dead GTT-stop leftovers | **3** |
| — none flagged yet — | **4** |

Any future audit or experiment doc should tag findings with a class.
It changes how they're triaged: a Class 1 blocks Rule 1; a Class 2
needs a deploy decision, not just a fix; a Class 3 is scheduled
whenever convenient; a Class 4 is optional.

## Next concrete actions (Track A only — this is what unblocks everything else)

1. ~~Port `ROTATION_ENABLED`/`RIDE_WINNER_ENABLED`/`SCORE_DROP_EXIT_ENABLED`
   logic into `backtest/engine.py`.~~ **Done 2026-07-11.**
2. ~~Add the bear-swing `TREND_BREAK` exit to `backtest/engine.py`.~~
   **Done 2026-07-11.**
3. ~~Pin a versioned/frozen data snapshot for `robustness_gate.py` runs
   (or fail loudly if the requested window isn't fully cached, rather
   than silently falling through to a live API call).~~ **Done
   2026-07-11** — `DEFAULT_GATE_END="2026-06-04"` + `REQUIRE_CACHED_DATA=1`
   fail-loud guard. Surfaced a real cache gap (`TMCV.NS`) that must be
   addressed before item 4 can run end-to-end (see note above).
4. ~~Config-drift check (`git diff` clean on `config/*.yaml` at run
   time).~~ **Done 2026-07-11** —
   `scripts/robustness_gate.py::check_config_drift()`.
5. ~~Resolve the two practical blockers: commit/revert config drift,
   backfill/exclude the `TMCV.NS`-style cache gap.~~ **Done
   2026-07-11.** **Next up:** re-run the full gate suite against every
   currently-closed finding from this session (RSI-threshold sweep, E1
   DD-throttle ablation) — their verdicts were produced under a
   Rule-1-FAIL state and are provisional until reconfirmed. This
   re-run is now more consequential than originally scoped: baseline
   behavior itself changed (rotation/ride-winner/score-drop-exit and
   TREND_BREAK are now live in the simulation for the first time), not
   just the data path.
6. Only after that re-run: resume Track B, starting with E3
   (churn-cohort audit) per `docs/27`'s ranking.

## How to apply

Rule 1 gates Rule 2 gates production. Rule 3 applies to all new code
from this point forward. Every future audit tags findings with a
class from the table above. Track B stays frozen until the Rule 1
checklist is checked off in full — not partially, not "close enough."
