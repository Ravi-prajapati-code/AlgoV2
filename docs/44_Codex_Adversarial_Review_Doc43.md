# Doc 44 — Adversarial Review of docs/43 (Codex role)

**Date**: 2026-07-31. Per `CLAUDE.md`'s peer-review process (step 3: adversarial review must actively try to reject, not implement as given), this is the Principal-Software-Engineer pass against `docs/43_Research_Director_Audit_And_Queue.md`. Every finding below is grounded in the actual working tree, not a re-read of doc/43's prose — verified with `git log`, `git diff`, `grep`, and direct file reads. No implementation happens in this doc; per the user's explicit instruction, implementation starts only once both roles agree.

## P0 — a finding outside doc/43's scope that overrides its entire premise

**`data/universe.py` is permanently excluded from version control, and both the live entrypoint and the backtest engine depend on it.**

- `.gitignore:11` is a bare `data/` line. Almost certainly intended to exclude data *artifacts* (price caches, etc.), it also swallows a real Python **source module**: `data/universe.py` (5,628 bytes, mtime 2026-07-30, holds `get_all_symbols`, `get_all_symbols_as_of`, `get_sector`, `UniverseHistoryUnavailable`).
- Confirmed via `git log --all --full-history -- data/universe.py`: **empty**. This file has never once been committed, in any branch, since the repo's first commit.
- Confirmed callers:
  - `runner/daily_runner.py:30` — `from data.universe import get_all_symbols, get_sector`. This is the live cron entrypoint (`main.py`/`daily_runner.py` docstring: "Daily runner — the full hybrid pipeline, run once after market close. Supports both Paper and Live (Upstox) modes").
  - `backtest/engine.py:39` — `from data.universe import get_sector`.
  - `main.py:122,137` — `from data.universe import get_all_symbols, get_all_symbols_as_of, UniverseHistoryUnavailable`.
  - `scripts/robustness_gate.py` shells out to `main.py backtest` as a subprocess (per its own docstring) — meaning **every gate run in this project's entire methodology transitively imports the gitignored file.**

**Consequence**: on any fresh `git clone` or `git pull`-based deploy (the deploy mechanism this project has used historically per prior memory — server drift incidents on 2026-07-13, 2026-07-17, and the still-open 2026-07-31 "server 8 commits behind" item), `runner/daily_runner.py` and every `robustness_gate.py`/backtest run fail immediately with `ModuleNotFoundError: No module named 'data.universe'`. This is not hypothetical: it only works right now because the file happens to physically exist on this machine's disk. It is a coin flip whether the live server currently has a manually-copied version of this file at all, and if it does, there's no guarantee it matches the version in this working tree (see next finding).

**This directly contradicts doc/43 §0's premise** that "Engineering Effort, Dependencies, Blocked/Not-Blocked... are stated as facts" derived from docs/41's architecture map — docs/41 did not catch this, and it invalidates "current config" as a stable reference point for every queue item that says "re-run under current config."

**Verdict on doc/43 as a whole: REJECT the "ready to run now" framing on every item that depends on `main.py backtest` or `BacktestEngine`, until this is fixed.** Recommended fix (not implemented here, pending agreement): either move `data/universe.py` out of the gitignored `data/` directory (e.g. to `services/universe.py` or similar, matching where `data/fetcher.py` — check if that's also affected — lives), or add an explicit `!data/universe.py` negation line to `.gitignore`, then commit. Cheap fix, ~15 minutes, but it is a hard prerequisite, not a parallel task.

## P0.5 — the working tree is not pinned, so "current config" is not one thing

Two tracked files carry uncommitted diffs right now:

- `db/universe_repo.py` — adds `get_core_universe_tracking_start()` and `get_core_symbols_as_of()` (CORE-universe point-in-time reconstruction from `universe_history` promotion/demotion events), backed by a real, passing-looking test suite (`tests/test_core_universe_snapshot.py`, itself untracked). This is in-flight work that changes what `get_all_symbols_as_of()` returns for the **live** path (`main.py`/`daily_runner.py`), not the backtest path — `robustness_gate.py`/`walk_forward.py`/`signal_level_attribution.py` never call `get_all_symbols_as_of` at all, so this specific diff doesn't retroactively affect any backtest verdict. Doc/43 §2.1 defended the entry-signal permutation test partly using a memory note that "the backtest never reads the dynamic universe table" — that description is **still accurate for the backtest path** after this diff, so §2.1's conclusion survives, but doc/43 should have surfaced this in-flight change explicitly rather than relying on a three-week-old memory snapshot without checking whether it was still current.
- `portfolio/manager.py` — removes the `long_term_reserve`/`swing_cash` capital-reservation logic, consistent with doc/35's long-term-sleeve REJECT and the memory note that this cleanup was pending. Not a surprise, but still **uncommitted**, meaning it's live-relevant code sitting outside version control's safety net.

**Consequence for the queue**: any item that says "re-run under current config" (leak re-measure, MAX_POSITIONS rerun, DD-throttle re-gate) will silently produce different numbers depending on whether the person running it has this exact uncommitted working tree or a clean checkout. Two engineers running the "same" experiment today could get two different answers with no error or warning.

**Verdict: REQUEST MORE EVIDENCE / procedural fix required.** Commit (or at minimum tag/stash-and-document) the current working-tree state as the explicit baseline before any Q-item in docs/43 §7 is run. Doc/43's dependency graph (§4) does not model this at all.

---

## Per-item verdicts

### Q1 — Re-measure leak decomposition under current config

**Claimed**: Low effort (~1 day), reuse `docs/18-20`'s existing attribution scripts, no dependencies, ready now.

Checked `scripts/alpha_leakage_audit.py` directly (tracked, last touched in commit `afca8e0`). It imports `ALL_SYMBOLS` from `config/watchlist_nse.py` (confirmed 504 symbols at runtime — the Nifty500 expansion is already baked into the static watchlist, not a separate layer) and delegates trade generation to `BacktestEngine`, which reads `ENTRY_MODE` from live config — so it **will** pick up `PURE_RS` automatically. The "reuse existing script" claim mechanically holds, better than I expected on first read.

But: `BacktestEngine` → `data.universe.get_sector` → the gitignored file (P0). And doc/43 lists this as "no dependencies, ready now" when it in fact depends on P0 and P0.5 both being resolved first.

**Verdict: APPROVE, conditional on P0 fix landing first.** Not a rejection of the idea — it's the single best-designed item in the queue — but "no dependencies" is false as written.

### Q2 — Ground-truth rebuild from Proven+Supported assumptions only

**Claimed**: No dependencies, ready now, Medium effort (~2-3 days).

Adversarial angle: the "Proven+Supported" assumption list this would be built from is `docs/23`'s classification — and doc/43's own §2 just reclassified one of its members (PURE_RS's accept, §2.4) and flagged two more as stale-not-wrong (leak decomposition, §2.2/2.3). Building "ground truth" from a list that the very same document just partially revised, without first folding those revisions in, bakes doc/43's own corrections out of the rebuild. Doc/43's dependency graph (§4) shows Q2 with no inputs — that's wrong; it should consume Q1's output and the §2.4 reclassification before being run, or it reproduces exactly the staleness problem §2 just diagnosed.

**Verdict: REQUEST MORE EVIDENCE.** Approach is sound and this is genuinely the single most repeatedly-flagged missing experiment in the project's history — but sequence it after Q1, and explicitly update the Proven/Supported list with §2.2-§2.4's corrections before treating it as the input set. Running it today reproduces the exact "stale evidence treated as current" problem the rest of doc/43 is trying to fix.

### Q3 — `crash_v_recovery` root-cause diagnosis

**Claimed**: High effort (~3-5 days), no existing tooling mentioned, build from scratch.

Found `scripts/trade_attribution.py` (existing, tracked, with output artifacts already in `outputs/trade_attribution.csv`) plus **pre-existing scratch databases from prior stress runs**: `outputs/diagnostic_scratch/crash_v_recovery.db`, `outputs/stress_test_scratch/crash_v_recovery.db`, `outputs/robustness_gate_scratch/crash_v_recovery.db`. This strongly suggests trade-level data from at least the most recent crash_v_recovery-killed run already exists on disk and existing tooling can already query it — doc/43's "3-5 days, build the forensics" estimate doesn't account for this.

Caveat, honestly: these scratch DBs are very likely overwritten on every gate run (that's the normal behavior of a "scratch" path), so they may only hold the *last* run's data, not all 4 historically-killed levers' trade detail — I did not verify retention behavior or whether historical runs are distinguishable within them.

**Verdict: REQUEST MORE EVIDENCE.** Before committing to a 3-5 day fresh build, check whether `scripts/trade_attribution.py` against the existing `crash_v_recovery.db` scratch files (or a quick rerun of the 4 known cases against fresh scratch DBs, reusing the existing tool) gets most of the way there. Could plausibly be Low-Medium effort, not High — but I can't confirm without running it, which is implementation, not review.

### Q4 — VCP / `ema_200` backtest fidelity fix

**Claimed**: Medium effort (~2-3 days), "wire `vcp_detected`/`vcp_pivot`/`ema_200` into `_precompute_all()`."

Confirmed both halves directly: `backtest/engine.py` has zero references to `vcp`/`ema_200` anywhere; `indicators/composite.py` computes both (`_detect_vcp()` at line 11, `ema_200` at line 86) and is the path `daily_runner.py` actually uses live (`indicators.composite.compute_all`). The gap is real — doc/43 is correct here.

But: `backtest/engine.py::_precompute_all` does **not** call `indicators.composite.compute_all()` — it independently reimplements `ema_20/50/100/150` with its own `pandas.ewm()` calls. This is exactly the class of duplication that already produced one real, silent bug (the RSI/ATR Wilder's-smoothing divergence, doc/33). "Wire it in" as doc/43 phrases it hides a fork the doc doesn't name:
1. **Cheap path**: copy-paste `_detect_vcp`/ema_200 logic into `engine.py` as a third independent implementation — perpetuates the exact duplication risk that already burned this project once, and is genuinely ~2-3 days as estimated.
2. **Correct path**: refactor `_precompute_all` to call `indicators.composite.compute_all()` directly, eliminating the duplication class entirely — bigger blast radius (every existing "Proven"/"Accepted" verdict was computed under the old duplicated-indicator path and would need re-verification once the computation path changes, even for indicators that were already correct), and almost certainly more than 2-3 days once that re-verification is included.

**Verdict: REQUEST MORE EVIDENCE.** Doc/43 must pick one of these two paths explicitly before the effort estimate can be trusted — they have materially different cost and materially different risk to the rest of the evidence base. Recommend the correct path (2) given this project has already been burned once by path-1-style duplication, but that's a call for both roles to make together, not to default into silently.

### Q5 — MAX_POSITIONS=5 clean rerun

**Claimed**: Low effort (~1 day), depends on Q1.

`robustness_gate.py` (tracked, `scripts/robustness_gate.py`) takes `--env KEY=value` overrides and shells to `main.py backtest`, which is exactly the right mechanism for this — mechanically sound, matches the project's own established gate methodology, no new tooling needed.

**Verdict: APPROVE, conditional on P0 fix landing first** (same transitive dependency as Q1, since it also runs through `main.py`/`BacktestEngine`). Sequencing after Q1 is correctly specified in doc/43.

### Q6 — Doc/40 mechanism disambiguation (momentum-maturity vs. mean-reversion)

**Claimed**: Low-Medium effort (~1-2 days).

No specific tooling gap found, but this project has a documented track record of "quick" statistical add-ons taking longer once done rigorously — the permutation test, the symbol-level dedupe, and the bootstrap CI were all incremental additions to existing analyses that each surfaced new methodological requirements (effective-sample-size correction, specifically — the charter's own stated concern). A matched-control study done properly (choosing a matching window, avoiding new look-ahead in the control construction, applying the same episode-dedupe discipline docs/40 already had to add) is not obviously a 1-2 day task.

**Verdict: REQUEST MORE EVIDENCE on the time estimate specifically** — approach (control on "days since 20d high") is sound, but budget for this to take longer once the same rigor bar as the rest of the project's recent work is applied.

### Q7 — N-of-M ensemble entry gate

**Claimed**: Medium-High effort, correctly gated on Q6, Low confidence pending Q6.

The gating on Q6 is right and I have nothing to reject there. But doc/43's own committee section (§8) states the TEST window has been used as a pass/fail filter in 15+ prior experiments and is no longer clean OOS. An ensemble gate introduces a new tunable combination space (which signals, what N-of-M threshold) — precisely the kind of researcher-degrees-of-freedom risk §8 warns about. Doc/43 gates Q7 on Q6 but never states an evaluation protocol for Q7 itself.

**Verdict: REQUEST MORE EVIDENCE / REVISE.** Add an explicit constraint before this is approvable: Q7 must not be evaluated against the existing TRAIN/TEST split at all — evaluate only on forward paper/live data from 2026-07-30 onward (consistent with doc/39's own frozen-tracking decision). Without that constraint, this is exactly the 5th variant of a lever tuned against a spent evaluation window, which is the failure mode the rest of doc/43 argues against.

### Q8 — Out-of-domain (Microcap-250) rerun, sector-cap confound removed

**Claimed**: Low effort (~0.5 day), "confound is understood and fixable."

I did not independently verify the sector-mapping code referenced here (inherited from doc/38's "Unknown sector mapping" finding via memory, not re-checked this pass). This project's sector-mapping code has previously hidden a real bug for a while (the Timestamp-vs-date crash in the sector durability gate, per memory) — "0.5 day, understood and fixable" should not be taken on faith without a direct look at the current sector-mapping code path.

**Verdict: REQUEST MORE EVIDENCE.** Approach is fine; time estimate unverified.

### Q9-equivalent — Regime-tiered sizing retroactive gate validation (§1, not separately numbered in §7)

**Claimed**: Low effort (~0.5 day), "run `robustness_gate.py` against the already-shipped config."

Confirmed `REGIME_SIZE_MULT_BEAR` and siblings are plain settings-driven multipliers referenced in `portfolio/manager.py` (`base_slot_cash *= REGIME_SIZE_MULT_BEAR`), consistent with the env-override pattern `robustness_gate.py` expects. Mechanically sound.

**Verdict: APPROVE, conditional on P0.** Also flagging this as materially more urgent than doc/43's "High priority" label suggests, given the next finding.

### Live-server drift check (§1)

**Claimed**: Blocked on SSH, ops task, correctly deprioritized as non-research.

New information this pass surfaces makes this more urgent than doc/43 states: if the eventual server sync happens via `git pull` (the deploy mechanism used historically, per memory of repeated prior drift incidents), and `data/universe.py` is genuinely never committed, the pending server sync (already flagged in memory as "awaiting user go") **will break the live daily runner on import** unless `data/universe.py` is separately, manually copied over as an out-of-band step. This should be called out explicitly to whoever executes that sync, not left implicit.

**Verdict: APPROVE the item as scoped, REQUEST the sync runbook be amended** to explicitly include a manual copy (or the P0 gitignore fix, committed, which is the actually-correct long-term solution) of `data/universe.py` before the server sync is executed.

### Everything else in §1's audit table (dead-code decisions, Sharpe methodology, schema drift, E2/E4/E5/E6 ablations)

No specific rejection found on inspection scope available this pass. These are low-priority, low-risk, correctly scoped as such in doc/43.

**Verdict: APPROVE as written**, no changes recommended.

---

## Reconciliation status

**Not yet agreed for implementation.** Per the charter's process, implementation should not begin until both roles agree, and this pass surfaced a P0 blocker doc/43 didn't have: the entire queue's execution mechanism (`main.py backtest`, `BacktestEngine`, `robustness_gate.py`) transitively imports a file that has never been in version control. That is not a research question — it's a correctness bug that puts every future "re-run under current config" claim (and, less immediately, live deploys) at risk, and it needs a decision (fix `.gitignore` + commit `data/universe.py`, and pin the two other uncommitted diffs) before Q1, Q5, and the regime-tiered-sizing re-gate — the three items doc/43 itself ranked highest — can actually be executed as described.

**Recommended order once P0/P0.5 are resolved**: fix P0 (gitignore + commit) → pin working tree (commit or explicitly document the two in-flight diffs) → Q1 → fold Q1's result into the Proven/Supported list → Q2 → Q3 (check existing trade-attribution tooling first) → Q5 → regime-tiered-sizing re-gate → Q4 (pick a path first) → Q6 → Q8 → Q7 (only with the forward-only evaluation constraint added).

Nothing in this review overturns doc/43's central finding — that the top of the queue should be validity/fidelity work, not new alpha — it sharpens it: the validity gap goes one layer deeper than doc/43 found, down into whether the queue's own execution tooling is even reproducible outside this one working tree.
