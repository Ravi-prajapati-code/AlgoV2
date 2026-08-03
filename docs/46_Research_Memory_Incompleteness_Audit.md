# Doc 46 — Research Memory Incompleteness Audit (Codex role)

**Date**: 2026-08-01. Assignment: assume `docs/42_Research_State_Registry.md` has errors — find missing experiments, duplicate experiments, wrong chronology, contradictory conclusions, stale assumptions, and undocumented production changes. Verified by cross-referencing doc 42's chronological index against `git log` (the ground truth of what actually happened), not by re-reading doc 42's own summary of itself.

## Finding 1 — the central issue: a growing share of real research/production history exists only in Claude's memory store, not in the git-tracked docs series

This is the most important finding in this audit, and it's structural, not a one-off miss. Cross-referencing `git log --since=2026-07-11` (the governance start date, doc 29) against doc 42's chronological index (which only indexes `docs/01-40`) surfaces at least **six substantive results with zero `docs/NN.md` file**, each one currently preserved only as a Claude-Code memory entry:

| Result | Commit(s) | Only documented in |
|---|---|---|
| Sector-durability soft entry score — gated, "marginal PASS," plus a Timestamp/date bug that had faked an earlier REJECT | `4965f21`, `5fa3852` (07-13) | Memory (`sector_durability_gate_pass_20260713`, `sector_durability_deployed_20260713`) |
| Regime-gated streak-position preference — 2 variants, both rejected | `e0c26a0`, `5fa3852` (07-13) | Memory (`streak_pref_regime_gated_reject_20260713`, `streak_position_pref_retest_20260710`) |
| Reconciler auto-fix for broker-only position mismatches | `eeb07e2` (07-22) | Memory (`reconciler_autofix_deploy_20260722`) |
| CEMPRO orphan-position bug — live buy not persisted before sync, would have permanently misclassified origin | `9f235cf` (07-22) | Memory (`cempro_orphan_position_bug_20260722`) |
| Charges-model bug fix — brokerage assumed free, STT assumed sell-only; materially changes CAGR (~27pp per doc/38 Addendum 2) | `b70335f` (07-22) | One retroactive paragraph inside doc/38 (written 8 days later), not its own doc |
| **SECTOR_RS_WEIGHT lever — built, gated, rejected (helps TRAIN/stress, guts TEST -44% trades)** | `f2238b8` (07-31, current HEAD) | Memory only (`sector_rs_weight_reject_20260731`) — **has no docs/NN.md file at all, and isn't in doc 42's own index despite doc 42 being written the same day** |

**Why this matters beyond "some docs are missing"**: the project's memory store lives at `/home/ravi.prajapati@brainvire.com/.claude/projects/.../memory/`, which is **outside the `AlgoV2` git repository entirely** — on this one developer's machine, tied to this specific tool. A teammate cloning the repo, a CI system, Codex operating in a separate session without this memory context, or this same project six months from now after the memory store is pruned/migrated, would have **zero record** that the sector-durability gate result, the reconciler, the CEMPRO bug, or SECTOR_RS_WEIGHT ever happened. This directly violates the charter's own rule — "every experiment must be permanently documented... failed research is valuable knowledge, do not delete rejected-hypothesis docs" — the rule doesn't say "documented somewhere," it implies durable, versioned, repository-resident documentation, which memory structurally cannot provide.

**The SECTOR_RS_WEIGHT case is the sharpest example**: it was gated and rejected on 2026-07-31, the exact same day `docs/41-43` were written by this same review process — and still didn't get a doc. The convention (numbered `docs/NN_Title.md`, one per result) is being followed carefully for "big" research questions and is silently lapsing for anything that feels operational or minor, even though several of these (the CEMPRO bug, the charges fix) are not minor by any reasonable standard.

## Finding 2 — one severe incident is undocumented anywhere, including memory

Commit `0580cd5` (2026-07-29): the live daily runner hung for the full 600-second socket timeout on 5 consecutive trading sessions (2026-07-23 through 2026-07-29), a different symbol triggering it each day, causing every order that day to be rejected by the broker for arriving after market close. I checked the memory index for this — **it is not there either**, unlike the other five findings above. This is the single most operationally severe finding in either audit (a full week of silently-failed live trading), and its only record anywhere is one paragraph in a commit message. Per the charter's own standard ("every experiment must be permanently documented"), a live-trading outage of this severity should have triggered an incident doc at minimum — what triggered it, how long it went undetected, why `recovery_manager.py`'s daily-completion check (doc/45 Finding 2) didn't catch it sooner than day 5, and what the actual P&L/opportunity-cost impact was. None of that analysis exists anywhere.

## Finding 3 — duplicate/near-duplicate commits suggest doc 42's implicit "one clean lineage" chronology may not be reliable at the commit level

Doc 42's chronological table presents docs/01-40 as a clean linear sequence. The underlying commit history is not clean: at least 4 commit pairs (`5ff13d5`/`a281ce5`, `8398591`/`c4094a1`, `1221caa`/`ad5c9e2`, `7e85480`/`1124d81`) carry identical messages with different hashes, clustered right before `a52acfe`'s server-branch merge. This doesn't falsify any specific verdict in doc 42's table, but it does mean anyone trying to reconstruct "what was the state of the code when doc N's experiment ran" by walking git history could pick the wrong twin commit and get a subtly different config. Doc 42 should note that the commit history has a documented dual-lineage period (~07-13 to 07-21) and point to `a52acfe` as the reconciliation point, rather than presenting the whole history as trustworthy for that kind of archaeology.

## Finding 4 — re-checked doc 42's stated contradictions and "proven/durable" claims; found no new contradiction, one new staleness note

I attempted to break each "Proven/durable" line in doc 42 against the commit log directly (not just against other docs, which docs/43 already did):

- "Exits/stops are clean — not a leak" (docs/18, 20, 24) — no commit since suggests this was retested; still resting on pre-`PURE_RS`/pre-Nifty500 evidence, consistent with doc/43 §2.3's own staleness flag. Not a new finding, confirms doc/43's existing one.
- "RS-rank magnitude/order has no value" — the SECTOR_RS_WEIGHT reject (`f2238b8`, Finding 1 above) is actually a **fourth** independent confirmation of a closely related pattern (a magnitude-weighted ranking signal — sector RS strength — helps TRAIN/stress and fails TEST), reinforcing doc 43 §5's "closed hypothesis class" finding, but doc 42 doesn't know this experiment exists at all to cite it. Once SECTOR_RS_WEIGHT gets its own doc (recommended below), doc 42's "Proven/durable" section should cite it as a fourth data point.
- No new contradiction found beyond the already-documented MAX_POSITIONS=5 one (doc 42 lines 75-77, already correctly flagged).

## Finding 5 — chronology is accurate where checked, with one cosmetic gap

Doc/17 does not exist and never has (confirmed no file, no git history under that number) — doc 42 correctly does not index it. This is a numbering gap in the source docs, not an error in doc 42. Doc 22 exists twice (`22_Chat_Handoff` and `22_Final_Recommendation`) and doc 42 correctly disambiguates both. No wrong-chronology dates were found in the spot-checks performed (doc 34's 07-21 date matches the Nifty500-expansion and `PURE_RS`-deploy commits' actual dates; doc 38/39's 07-30 dates match; the charges-fix commit `b70335f` on 07-22 matches doc 42's implicit dating via doc 38's addendum). Doc 42's dates are trustworthy where I checked them.

## Net assessment

Doc 42 is not factually wrong about anything it contains. It is incomplete in exactly the way doc 45 found doc 41 incomplete: both documents describe the subset of the repository that the formal `docs/NN` research pipeline touched, and both silently under-represent a growing body of real, decision-relevant work that has been happening in parallel — in memory, in terse commit messages, and in production incidents — without being promoted into the same durable, versioned record. Six results and one severe incident currently exist only outside the git repository. That gap, not any single wrong verdict, is the finding the Repository Constitution (doc 47) needs to close.
