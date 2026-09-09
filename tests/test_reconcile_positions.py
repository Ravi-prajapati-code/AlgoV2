"""
Tests for scripts/reconcile_positions.py's log_classifications() -- the
second of the two classify() call sites (see
tests/test_observability_snapshot.py for the first). No prior test
coverage existed for this function; added alongside the
MAIN_STRATEGY_PAPER_SINCE per-position filter fix (GOLDBEES.NS false
DUPLICATE_OWNERSHIP) so both call sites stay pinned against drift.

Uses tmp-path trading.db (for the evidence lookups) and reporting.db;
never touches the real ones.
"""
from datetime import date
from types import SimpleNamespace

import pytest

from reconciliation.classifier import Classification


@pytest.fixture
def trading_db(tmp_path, monkeypatch):
    from db import repository as repo

    db_path = str(tmp_path / "trading_test.db")
    monkeypatch.setattr("db.repository.DB_PATH", db_path)
    repo.init_db()
    return repo


@pytest.fixture
def reconciler(trading_db, tmp_path, monkeypatch):
    from db import reporting_repo as rrepo

    reporting_path = str(tmp_path / "reporting_test.db")
    monkeypatch.setattr("db.reporting_repo.REPORTING_DB_PATH", reporting_path)
    rrepo.init_db()

    import scripts.reconcile_positions as recon_mod
    return recon_mod


def _pos(symbol, shares, entry_date=None):
    return SimpleNamespace(symbol=symbol, shares=shares,
                            entry_date=entry_date or date(2026, 1, 1))


def _broker_pos(symbol, quantity):
    return SimpleNamespace(symbol=symbol, quantity=quantity)


def test_log_classifications_excludes_post_cutover_main_paper_qty(reconciler):
    """GOLDBEES.NS-shaped: MAIN's post-cutover paper position must not
    count toward ownership math -- momentum_atr's real 175 sh already
    matches the broker exactly, so this must classify MATCH, not
    DUPLICATE_OWNERSHIP, even though MAIN's own ledger still shows 102
    shares of the same symbol. log_classifications() only writes a
    strategy_reconciliation_log row for non-MATCH symbols, so MATCH here
    means the run logs a clean PASS with no mention of GOLDBEES."""
    from db import reporting_repo as rrepo

    db_positions = [_pos("GOLDBEES.NS", 102, entry_date=date(2026, 9, 7))]  # after cutover
    atr_positions = [_pos("GOLDBEES.NS", 175)]
    broker_positions = [_broker_pos("GOLDBEES.NS", 175)]

    reconciler.log_classifications("09 Sep 2026", broker_positions, db_positions, atr_positions)

    rows = rrepo.load_recent_reconciliation()
    assert len(rows) == 1
    assert rows[0]["check_name"] == "position_classification"
    assert rows[0]["result"] == "PASS"
    assert "GOLDBEES" not in (rows[0]["detail"] or "")


def test_log_classifications_keeps_pre_cutover_main_qty(reconciler):
    """ASIANENE.NS-shaped: a MAIN position opened before the paper cutover
    is real regardless of MAIN's current live-trading flag and must stay
    counted -- still classifies as GHOST_DB_POSITION (broker holds zero),
    not silently dropped, so the run logs a FAIL naming it."""
    from db import reporting_repo as rrepo

    db_positions = [_pos("ASIANENE.NS", 8, entry_date=date(2026, 8, 27))]  # before cutover
    atr_positions = [_pos("ASIANENE.NS", 8)]
    broker_positions = []

    reconciler.log_classifications("09 Sep 2026", broker_positions, db_positions, atr_positions)

    rows = rrepo.load_recent_reconciliation()
    assert len(rows) == 1
    assert rows[0]["result"] == "FAIL"
    assert f"ASIANENE.NS: main=8 atr=8 broker=0 -> {Classification.GHOST_DB_POSITION.value}" in rows[0]["detail"]
