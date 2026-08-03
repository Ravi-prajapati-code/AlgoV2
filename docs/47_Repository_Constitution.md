# Doc 47 — Repository Constitution

**Date**: 2026-08-01. Jointly authored by Claude (Chief Quantitative Research Director) and
Codex (Principal Software Engineer + Repository Integrity), per `CLAUDE.md`'s peer-review
process — since this session runs one AI, both roles were performed explicitly and in sequence,
same as docs/43/44/45/46.

Docs 41-44 described the current state of the codebase and its research history. Docs 45-46
found that description incomplete. This document is different in kind: it does not describe the
repository — it defines the rules every future change to it must follow. It exists because
every specific failure catalogued in docs/44-46 (a gitignored source file, an unpinned working
tree, undocumented cron jobs, a hidden scheduler, six results that only live in a
non-git-tracked memory store, a five-day silent live-trading outage with no incident record)
is the same underlying failure repeating in different clothes: **a real change happened and the
repository's permanent record didn't capture it.** The rules below close that gap going forward,
not retroactively.

## 1. Research workflow

1. Every hypothesis must state, before any code is written: why alpha should exist, what market
   behavior creates it, why the market hasn't arbitraged it away, what assumptions are required,
   what evidence would reject it. (Already required by `CLAUDE.md` §"Research process" — restated
   here because it is load-bearing for §3 below.)
2. Before proposing anything, check `docs/43`'s duplicated-research clusters (§5) and doc/24's
   rejected-forever table. A hypothesis that re-tests a closed cluster must explain what's
   materially different, not just be re-run.
3. New-lever research is explicitly lower priority than fidelity/validity work whenever the
   active queue (`docs/43` §9 or its successor) says so. Don't skip the queue because a new idea
   is more interesting than the validity backlog.

## 2. Engineering workflow

1. No implementation begins until both roles reach APPROVE (or explicit REJECT/REQUEST-MORE-
   EVIDENCE resolution) on a proposal — per `CLAUDE.md`'s existing rule. This constitution adds:
   the APPROVE/REJECT/REQUEST-MORE-EVIDENCE verdict and its reasoning must be written into a
   docs/NN.md file, not just stated in chat. Verbal-only agreement does not satisfy this rule.
2. A commit that bundles an unrelated bug fix with a new experimental feature is not permitted.
   (`f2238b8` did this — SECTOR_RS_WEIGHT lever + live sector_durability wiring fix in one commit
   — repeating the exact anti-pattern already logged in
   `cross_feature_commit_contamination_20260728` three days earlier. Doc 46 Finding 1.) Split
   commits by concern even when the diff is small.
3. Every experimental lever must ship behind a flag defaulted OFF until gated PASS, consistent
   with existing practice (`UNIVERSE_CAP_SIZE`, `SECTOR_RS_WEIGHT`) — this is already the norm,
   codified here so it isn't accidentally dropped under time pressure.

## 3. Evidence standards ("definition of accepted evidence")

Evidence is accepted only if all of the following hold. Historical CAGR alone never satisfies
this list on its own (`CLAUDE.md` already states this; this section makes it checkable):

1. **Economic reasoning** — a stated causal mechanism, not just a correlation.
2. **Effective-sample-size check** — symbol-day rows with overlapping forward windows are not
   independent observations; dedupe to distinct symbols/episodes before trusting n. (`docs/40`'s
   own case: a TRAIN-window "signal" that was row-autocorrelation noise.)
3. **TRAIN and TEST both reported**, never TRAIN alone. A result that only wins TRAIN is not
   evidence of alpha — it's evidence of overfitting until TEST says otherwise.
4. **Stress-tested**, minimum the existing 4-scenario `robustness_gate.py` suite including
   `crash_v_recovery`. A result that wins calendar TRAIN/TEST but loses under stress is reported
   as exactly that — "wins TRAIN/TEST, loses under stress" — never rounded up to "PASS".
   (This is the precise correction doc/43 made to the `PURE_RS` accept — the fix is to always
   report it this way going forward, not just after the fact.)
5. **Config parity confirmed** — the candidate and baseline configs must be verified to actually
   differ before a gate run is trusted (`max_pos5_dd_throttle_combined_gate` caught this failure
   mode once already: candidate/baseline both resolved to the same yaml default).
6. **Backtest/live parity for anything the claim depends on** — if the evidence rests on an
   indicator or rule, confirm the backtest computes it the same way live does before trusting the
   result. (`docs/43` §3 flagged the MACD-gate reject as resting on unverified parity — this is
   now a standing requirement, not a one-off flag.)

## 4. Statistical requirements

1. Effective sample size (§3.2) is mandatory for any claim resting on row counts in the
   thousands — a large row count from a small number of symbols/episodes is not strong evidence.
2. A single permutation test or single statistical study is not sufficient for a claim to be
   treated as durably proven (`docs/43` §8's "single-study-syndrome" finding — the one proven
   claim in this project, "entry signal beats random," rests on n=1 permutation test never
   independently re-derived). Durable claims need either an independent re-derivation or
   continued survival across multiple unrelated gate runs before being called "proven."
3. p-values are reported alongside effect size and stress-window agreement, never alone.

## 5. Review process

1. Proposing role states the hypothesis and evidence. Reviewing role's job is to reject it, not
   rubber-stamp it — actively search for implementation flaws, hidden assumptions, data leakage,
   look-ahead bias, survivorship bias, unnecessary complexity, and (as of docs/45-46) repository-
   integrity failures: reproducibility, hidden schedulers, dynamic config, undocumented cron/prod
   changes.
2. Every review verdict (APPROVE / REJECT / REQUEST MORE EVIDENCE) must be written down with its
   reasoning, per §2.1.
3. Single-AI sessions must still perform both roles explicitly in sequence and label which role
   is speaking — this constitution does not change that existing rule, it enforces it by making
   the review verdict a required doc artifact.

## 6. Deployment checklist

Before any change reaches the live server:

1. `git status` clean on the deploying machine — no uncommitted diffs contributing to the
   behavior being deployed (doc/44 P0.5).
2. No source `.py` file lives under a gitignored path (doc/44 P0 — `data/universe.py`). A
   pre-commit or CI check should verify this; until that check exists, it is a manual step in
   this checklist.
3. Diff the target commit range for duplicate-message/different-hash commit pairs before merging
   a server-only branch into main (doc/45 Finding 5's 4-pair duplication pattern) — dedupe before
   merge, don't reconcile after.
4. Confirm `recovery_manager.py`'s watchdog crontab is actually installed on the target server
   (doc/45 Finding 2 — it documents its own crontab in its docstring, `setup_cron.sh` does not
   install it) — a deploy without this watchdog running has no automated way to detect a repeat
   of the `0580cd5` five-day silent-failure incident.
5. After deploy, confirm server config values match what the commit message/doc claims (doc's
   `server_drift_reconcile_deploy` and `live_server_drift_and_live_mode` precedent — "deployed"
   has been claimed while the server ran different values before).

## 7. Documentation-update policy

This directly closes doc/46's central finding — documentation-tier drift.

1. Any commit that changes live-relevant behavior (a new lever, a bug fix affecting execution or
   P&L, a new production feature) **must** be paired with a `docs/NN_Title.md` entry. A one-line
   memory note is not sufficient on its own — memory is a local, non-git-tracked convenience
   layer for this developer's Claude Code sessions, not a substitute for the permanent,
   repository-resident record.
2. A severe live-trading incident (any full-day or multi-day failure to execute intended orders)
   must get a dedicated incident doc within the same week it's discovered, covering: what
   triggered it, how long it went undetected, why the watchdog (§6.4) didn't catch it sooner, and
   the estimated P&L/opportunity-cost impact. (`0580cd5`'s five-day hang currently has none.)
3. Architecture (`docs/41`) and research-memory (`docs/42`) registries must be re-verified for
   completeness — not just correctness — before being cited as current. Use docs/45/46's method:
   cross-reference against `setup_cron.sh` + `recovery_manager.py` + full `git log`, not against
   the registry's own prior summary of itself.
4. Numbering stays in the existing flat `docs/NN_Title.md` series; don't create a parallel
   documentation system.

## 8. Forward validation policy

1. A result validated once under a since-changed config (universe size, entry mode, charges
   model, etc.) is stale, not wrong, and must be labeled stale until re-run under the current
   config before being relied on for a new decision. (`docs/43` §2.3's leak-decomposition flag is
   the template for how to phrase this correctly.)
2. Any claim of "proven"/"durable" is re-checked against new commits as they land — doc/46
   Finding 4 showed this is tractable (the SECTOR_RS_WEIGHT reject became a 4th independent
   confirmation of an existing closed hypothesis class, but only because someone checked).
3. No feature reaches unconditional production status purely on backtest evidence — the charter's
   existing rule ("historical performance... never justifies accepting a feature alone") is
   restated here as a hard gate on top of §3's evidence standards, not a soft guideline.

## 9. Reproducibility requirements

Operationalizes the six Repository Integrity questions now permanently assigned to Codex's role
in `CLAUDE.md`:

1. Clean-clone reproducibility: a `git clone` + documented setup steps must produce a runnable
   `main.py backtest` and `robustness_gate.py` without manual file copying. (Currently fails —
   `data/universe.py`, doc/44 P0 — this is the concrete blocking case this rule exists to catch
   next time.)
2. Single-machine reproducibility: no result is trusted if it depends on uncommitted local state
   (doc/44 P0.5).
3. History reproducibility: git log for the commit range behind any claim must be unambiguous —
   no duplicate-message commit pairs left unresolved (doc/45 Finding 5).
4. Production reproducibility: server state is periodically diffed against what git/docs claim is
   deployed, not assumed to match (existing `server_drift_reconcile_deploy` /
   `live_server_drift_and_live_mode` precedent).
5. CI reproducibility: not currently enforced by any automated system in this repository — flagged
   here as an open gap, not claimed as solved. Until CI exists, §6's deployment checklist is the
   manual substitute and must not be skipped under time pressure.

## What this document does not do

It does not resolve any specific open item from docs/43-46 — the P0 gitignore fix, the SECTOR_RS_WEIGHT
missing doc, the `0580cd5` incident writeup, and the CI-enforcement gap in §9.5 all remain open
work, tracked in their originating docs. This constitution defines the rule each of those gaps
violated, so the same class of gap is caught earlier next time — it is not itself the fix.

## Adversarial review (Codex role, applied to this document)

- Risk: a constitution with no enforcement mechanism is just a longer version of `CLAUDE.md`
  that nobody re-reads under deadline pressure. Mitigation: §6 and §7 point at concrete,
  checkable artifacts (docs/NN.md existence, `git status` clean, duplicate-commit diff) rather
  than vague principles — each rule here should be checkable by grep or `git log`, not just by
  memory or intention.
- Risk: §9.5 (CI) is aspirational, not implemented — flagged explicitly rather than left implicit,
  so it isn't mistaken for a solved problem the next time doc 41 or a fresh audit is run.
- Risk: this document itself could suffer the same documentation-tier-drift problem it names in
  §7 if it isn't kept current. No mitigation beyond the existing practice of periodic adversarial
  re-audit (docs/45/46 are the template) — noted as a known residual risk, not solved here.
