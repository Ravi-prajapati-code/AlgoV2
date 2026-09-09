"""
Three-way (broker / MAIN / momentum_atr) position classifier (docs/60, plan
optimized-humming-crayon M0). Pure function, no DB/broker I/O — every caller
(scripts/observability_snapshot.py, scripts/reconcile_positions.py, and
later reconciliation/gate.py) shares this single classification so the two
scripts can never drift into disagreeing about what a mismatch means.

UNKNOWN != ZERO: broker_qty is Optional[int] — None means "broker read
failed/unavailable", never fabricated as 0. Callers must pass None, not 0,
when scripts/observability_snapshot.py::get_broker_snapshot() or
scripts/reconcile_positions.py::get_broker_positions() returned None.

Real incidents this classifier must correctly reproduce (regression fixtures
in tests/test_reconciliation_classifier.py):
  GOLDBEES  main=175 atr=175 broker=175  -> DUPLICATE_OWNERSHIP
  CYIENT    main=0   atr=6   broker=3    -> QUANTITY_MISMATCH (or
            MANUAL_BROKER_POSITION if evidence["manual_evidence"] is set)
  ASIANENE  main>0   atr>0   broker=0    -> GHOST_DB_POSITION (both ledgers
            ghosted -- evidence carries both quantities so this shows up as
            one correlated case, not two independent single-sided alerts)
"""

from enum import Enum
from typing import Optional


class Classification(str, Enum):
    MATCH = "MATCH"
    PENDING_EXECUTION = "PENDING_EXECUTION"
    DUPLICATE_OWNERSHIP = "DUPLICATE_OWNERSHIP"
    GHOST_DB_POSITION = "GHOST_DB_POSITION"
    STRATEGY_ONLY_POSITION = "STRATEGY_ONLY_POSITION"  # reserved: not yet distinguished
    BROKER_ONLY_POSITION = "BROKER_ONLY_POSITION"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    OWNERSHIP_CONFLICT = "OWNERSHIP_CONFLICT"
    MANUAL_BROKER_POSITION = "MANUAL_BROKER_POSITION"
    UNKNOWN_POSITION = "UNKNOWN_POSITION"
    BROKER_DATA_UNAVAILABLE = "BROKER_DATA_UNAVAILABLE"
    RECONCILIATION_ERROR = "RECONCILIATION_ERROR"


# Tiers referenced by reconciliation/gate.py (M3) and the two reconciliation
# scripts' alert severity. Never auto-repaired by this classifier itself --
# it only classifies, callers decide what (if anything) to do.
BLOCKING_CLASSIFICATIONS = frozenset({
    Classification.DUPLICATE_OWNERSHIP,
    Classification.OWNERSHIP_CONFLICT,
    Classification.QUANTITY_MISMATCH,
    Classification.UNKNOWN_POSITION,
    Classification.RECONCILIATION_ERROR,
})


def classify(
    symbol: str,
    main_qty: int,
    atr_qty: int,
    broker_qty: Optional[int],
    evidence: Optional[dict] = None,
) -> "tuple[Classification, dict]":
    """Deterministic, side-effect-free. Returns (Classification, evidence_dict)
    -- evidence_dict always echoes the raw inputs plus whatever the caller
    passed in `evidence`, so a reconciliation-log row is self-explaining
    without a second query.

    `evidence` keys consumed here:
      manual_evidence      -- a manual broker-side action (liquidation, manual
                               buy) plausibly explains a qty mismatch
                               (trading.db.trades.exit_reason LIKE '%MANUAL%'
                               or positions.origin == 'manual')
      prior_record_exists  -- a CLOSED record for this symbol exists in one of
                               the strategy DBs, making broker-only ownership
                               inferable (mirrors runner.daily_runner's
                               origin-recovery heuristic)
      pending_execution    -- an order was placed for this symbol earlier in
                               the same session and may not have reached the
                               broker snapshot yet
    """
    ev = dict(evidence or {})
    ev.update({"main_qty": main_qty, "atr_qty": atr_qty, "broker_qty": broker_qty})

    if broker_qty is None:
        return Classification.BROKER_DATA_UNAVAILABLE, ev

    try:
        total = main_qty + atr_qty
        both_claimed = main_qty > 0 and atr_qty > 0

        if total == broker_qty:
            return Classification.MATCH, ev

        if broker_qty == 0 and (main_qty > 0 or atr_qty > 0):
            if ev.get("pending_execution"):
                return Classification.PENDING_EXECUTION, ev
            return Classification.GHOST_DB_POSITION, ev

        if main_qty == 0 and atr_qty == 0 and broker_qty > 0:
            if ev.get("prior_record_exists"):
                return Classification.BROKER_ONLY_POSITION, ev
            return Classification.UNKNOWN_POSITION, ev

        # broker_qty > 0, at least one ledger nonzero, sum disagrees with broker.
        if both_claimed:
            if main_qty == broker_qty or atr_qty == broker_qty:
                return Classification.DUPLICATE_OWNERSHIP, ev
            return Classification.OWNERSHIP_CONFLICT, ev

        if ev.get("manual_evidence"):
            return Classification.MANUAL_BROKER_POSITION, ev
        return Classification.QUANTITY_MISMATCH, ev

    except Exception as e:
        ev["error"] = str(e)
        return Classification.RECONCILIATION_ERROR, ev
