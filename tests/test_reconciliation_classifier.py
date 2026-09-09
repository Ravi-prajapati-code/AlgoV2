"""
Tests for reconciliation/classifier.py (plan optimized-humming-crayon M0):
the single, pure, deterministic three-way (broker/MAIN/momentum_atr)
position classifier shared by scripts/observability_snapshot.py and
scripts/reconcile_positions.py. No DB/broker I/O -- every case here is a
plain function call.

Includes regression fixtures using the real incident numbers (GOLDBEES,
CYIENT, ASIANENE) so a future refactor can't silently reclassify them.
"""
from reconciliation.classifier import Classification, classify


def test_match_when_single_owner_qty_agrees_with_broker():
    cls, ev = classify("RELIANCE.NS", main_qty=5, atr_qty=0, broker_qty=5)
    assert cls == Classification.MATCH
    assert ev["main_qty"] == 5 and ev["atr_qty"] == 0 and ev["broker_qty"] == 5


def test_match_when_split_ownership_sums_to_broker():
    """Both strategies legitimately hold separate lots of the same symbol
    and the sum reconciles exactly -- not a bug, must not alert."""
    cls, _ = classify("INFY.NS", main_qty=3, atr_qty=5, broker_qty=8)
    assert cls == Classification.MATCH


def test_duplicate_ownership_goldbees_regression():
    """Real incident: momentum_atr genuinely held 175 sh; MAIN's DB also
    carried an OPEN row for the identical 175 sh. Both ledgers' qty equals
    the broker's real qty -- double counting the same physical shares."""
    cls, ev = classify("GOLDBEES.NS", main_qty=175, atr_qty=175, broker_qty=175)
    assert cls == Classification.DUPLICATE_OWNERSHIP
    assert ev["main_qty"] == 175 and ev["atr_qty"] == 175


def test_quantity_mismatch_cyient_regression_without_manual_evidence():
    """Real incident: broker holds 3, momentum_atr's DB says OPEN 6, MAIN
    has 0. Without evidence of a manual broker-side action, this must stay
    the unexplained, always-alert-only QUANTITY_MISMATCH tier."""
    cls, _ = classify("CYIENT.NS", main_qty=0, atr_qty=6, broker_qty=3)
    assert cls == Classification.QUANTITY_MISMATCH


def test_manual_broker_position_cyient_regression_with_manual_evidence():
    """Same CYIENT shape, but MAIN's trades table shows a
    MANUAL_LIQUIDATION_PRE_PAPER_SWITCH exit for 3 shares -- evidence
    explains the gap. Still not auto-repaired (UNSAFE-tier), but
    distinguishable from an unexplained mismatch."""
    cls, ev = classify(
        "CYIENT.NS", main_qty=0, atr_qty=6, broker_qty=3,
        evidence={"manual_evidence": True},
    )
    assert cls == Classification.MANUAL_BROKER_POSITION
    assert ev["manual_evidence"] is True


def test_ghost_db_position_single_sided():
    """DB open, broker has nothing -- possibly a failed sell, alert-only,
    never auto-closed (matches scripts/reconcile_positions.py's existing
    'ghost' convention)."""
    cls, _ = classify("TCS.NS", main_qty=5, atr_qty=0, broker_qty=0)
    assert cls == Classification.GHOST_DB_POSITION


def test_ghost_db_position_double_sided_asianene_regression():
    """Real incident: ASIANENE OPEN in both trading.db AND momentum_atr.db,
    broker holds zero. Must classify as ONE correlated GHOST_DB_POSITION
    case (evidence carries both qtys), not two independent single-sided
    alerts the way the old ad hoc checks would have produced."""
    cls, ev = classify("ASIANENE.NS", main_qty=10, atr_qty=4, broker_qty=0)
    assert cls == Classification.GHOST_DB_POSITION
    assert ev["main_qty"] == 10 and ev["atr_qty"] == 4


def test_pending_execution_when_evidence_flags_same_session_order():
    """A same-day order that hasn't reached the broker snapshot yet must not
    be mistaken for a real ghost -- caller passes explicit evidence, never
    inferred silently from timing alone."""
    cls, _ = classify(
        "HDFCBANK.NS", main_qty=2, atr_qty=0, broker_qty=0,
        evidence={"pending_execution": True},
    )
    assert cls == Classification.PENDING_EXECUTION


def test_unknown_position_broker_only_no_prior_record():
    """Broker holds a symbol neither ledger has ever seen -- no prior record
    to infer origin from, most cautious tier."""
    cls, _ = classify("NEWSTOCK.NS", main_qty=0, atr_qty=0, broker_qty=4)
    assert cls == Classification.UNKNOWN_POSITION


def test_broker_only_position_when_prior_record_exists():
    """Same raw shape, but a closed record exists for this symbol in one of
    the strategy DBs -- origin is inferable (mirrors daily_runner's
    origin-recovery heuristic), so this is the more resolvable tier."""
    cls, _ = classify(
        "NEWSTOCK.NS", main_qty=0, atr_qty=0, broker_qty=4,
        evidence={"prior_record_exists": True},
    )
    assert cls == Classification.BROKER_ONLY_POSITION


def test_ownership_conflict_both_claim_different_unexplained_qtys():
    """Both ledgers claim a nonzero qty, sum disagrees with broker, and
    neither ledger's qty equals the broker's -- genuinely ambiguous, not the
    clean double-counting shape of DUPLICATE_OWNERSHIP."""
    cls, _ = classify("WIPRO.NS", main_qty=4, atr_qty=6, broker_qty=3)
    assert cls == Classification.OWNERSHIP_CONFLICT


def test_broker_data_unavailable_never_fabricates_zero():
    """UNKNOWN != ZERO: a failed/empty broker read must be passed as None,
    never treated as broker_qty=0, even when both ledgers show 0 too."""
    cls, ev = classify("ANY.NS", main_qty=0, atr_qty=0, broker_qty=None)
    assert cls == Classification.BROKER_DATA_UNAVAILABLE
    assert ev["broker_qty"] is None


def test_broker_data_unavailable_takes_priority_over_other_shapes():
    cls, _ = classify("ANY.NS", main_qty=5, atr_qty=3, broker_qty=None)
    assert cls == Classification.BROKER_DATA_UNAVAILABLE


def test_reconciliation_error_never_dropped_silently():
    """A classifier-internal exception (e.g. a non-numeric qty slipping
    through) must fail closed to RECONCILIATION_ERROR with the error
    preserved in evidence, never raise past the caller or silently pass."""
    cls, ev = classify("BAD.NS", main_qty="not-a-number", atr_qty=0, broker_qty=5)
    assert cls == Classification.RECONCILIATION_ERROR
    assert "error" in ev


def test_evidence_dict_echoes_caller_supplied_keys():
    """Evidence passed in by the caller must survive into the returned
    evidence_dict verbatim, so a reconciliation-log row is self-explaining
    without a second query."""
    cls, ev = classify(
        "TCS.NS", main_qty=0, atr_qty=6, broker_qty=3,
        evidence={"manual_evidence": True, "note": "checked trades table"},
    )
    assert ev["note"] == "checked trades table"
