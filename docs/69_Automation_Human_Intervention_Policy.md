# 69 — Automation / Human-Intervention Policy

**Date:** 2026-09-22
**Status:** Accepted (design principle, not a single experiment)
**Trigger:** ASIANENE.NS ghost-position incident (2026-09-16 → 2026-09-22)

## Principle

Human intervention is required only for decisions the system cannot
deterministically resolve. Operational failures with a provable outcome
must self-heal automatically. Strategic or configuration changes require
human approval.

This replaces any implicit assumption that "the system paused/alerted"
is itself a sign of good design. A page is a cost (attention, latency,
risk of being missed) and should only be spent when the system genuinely
lacks the information to act correctly on its own — not as a blanket
substitute for handling a known failure mode.

## Why this line, specifically

The dividing question is not "is this operational vs. strategic" — it's
**can the correct action be proven from data already available to the
system?**

- **Provable → self-heal, no human.** Example: `momentum_atr/execution.py`'s
  `_self_heal_via_holdings()` (this incident's fix). When
  `get_order_status()` never reaches a terminal state, the system compares
  broker holdings before/after the order against the *exact* expected
  quantity delta. An exact match proves the fill happened and proves its
  size — the only unknown left (fill price) is filled in from live LTP and
  flagged for a human to audit later, not act on before the fact. Nothing
  about this requires human judgment; it requires data the system already
  has.

- **Not provable → page, don't guess.** Example: `CYIENT.NS`-shaped
  `QUANTITY_MISMATCH` (main=0, atr=6, broker=3 — see
  `reconciliation/classifier.py`). A shared broker account with two
  ledgers disagreeing on ownership has no single answer computable from
  the three numbers alone — it could be a duplicate-buy bug, a partial
  fill race, or a manual trade. Auto-assigning ownership here doesn't fail
  loud, it silently misattributes real shares/cash between two strategies,
  which is strictly worse than the ghost bug this doc's incident fixed.
  `reconciliation/gate.py` and `scripts/reconcile_positions.py`'s paging
  path treat this class as fail-closed / alert-only by design (see
  `docs/65`).

## Concrete application, this incident

This is the first standalone write-up of the ASIANENE.NS incident itself
(no prior docs/NN entry covers it) — the fix and the policy are
documented together here.

Two bugs stacked in the ASIANENE.NS incident:

1. **Should have self-healed, didn't.** `_await_order_completion()`
   treated a failed status lookup (`OrderStatus.UNKNOWN`, e.g. an
   `order/details` 404 right after a real fill) as if the "did it
   execute?" and "do I know the outcome?" questions were the same
   question — a `None`/failed answer to the second got read as an answer
   to the first. Fixed: UNKNOWN is now retried through the same window as
   PENDING/OPEN, and `_self_heal_via_holdings()` provably resolves the
   case where polling exhausts anyway.
2. **Should have paged, didn't.** `log_classifications()`'s
   `BLOCKING_CLASSIFICATIONS` findings (a genuinely unprovable class —
   same tier as CYIENT above) only ever wrote a `reporting.db` row nobody
   read without opening the dashboard. Fixed: now pages via `_send()`.

Both were bugs *relative to this policy*, not because the policy was
violated on purpose — the self-heal path simply didn't exist yet, and the
paging path existed but silently degraded to DB-only.

## Known gap left open (deliberately, for now)

`research/main_strategy/portfolio/manager.py`'s own
`_await_order_completion()` already avoids the early-exit flaw (retries
correctly through the timeout on UNKNOWN/PENDING), but has no self-heal
fallback — the same latent gap as momentum_atr's pre-fix state, just
currently inert because MAIN trades paper-only
(`MAIN_STRATEGY_LIVE_TRADING_ENABLED=False`) and `PaperBroker`'s status
lookup cannot 404. Left unfixed because there is zero live blast radius
today; must be hardened before MAIN is ever reconsidered for live trading
again, per this policy — not after the first live incident repeats it.

## How to apply this policy going forward

When adding or reviewing any failure-handling path, ask in order:
1. What are all the ways the system's stated action could not match the
   broker's real action?
2. For each: is there a piece of ground truth the system can check (a
   count, a balance, a broker-side lookup) that *proves* which state is
   correct, with no case where it could prove the wrong thing?
3. If yes — self-heal from that proof, log it, and still surface it for
   human audit after the fact if money moved. Do not page and block.
4. If no — fail closed, alert a human, do not guess. Do not "handle" it
   with a plausible-sounding default; a wrong silent default is worse than
   an alert.
