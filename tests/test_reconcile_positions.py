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


def test_run_reconcile_ghost_excludes_post_cutover_main_paper(reconciler, monkeypatch, capsys):
    """Regression for the real GOLDBEES.NS false ghost alert (fired daily
    2026-09-07 through 2026-09-15): log_classifications() already excluded
    post-cutover MAIN paper positions from its own ownership math (tests
    above), but run_reconcile()'s actual ghost/unknown check -- the one that
    sends the Telegram alert and sys.exit(2)s -- used raw, unfiltered
    db_positions. A paper-only MAIN position with no broker/atr holdings
    must not trip that path."""
    db_positions = [_pos("GOLDBEES.NS", 102, entry_date=date(2026, 9, 7))]  # after cutover

    monkeypatch.setattr(reconciler, "get_broker_positions", lambda: [])
    monkeypatch.setattr("db.repository.load_positions", lambda status: db_positions)
    monkeypatch.setattr("db.momentum_atr_repo.load_positions", lambda status: [])
    sent = []
    monkeypatch.setattr(reconciler, "_send", lambda msg: sent.append(msg))

    reconciler.run_reconcile()  # must return normally, not sys.exit(2)

    assert not sent
    assert "OK" in capsys.readouterr().out


def test_run_reconcile_ghost_still_fires_pre_cutover(reconciler, monkeypatch):
    """Guard against the fix above becoming too broad: a pre-cutover MAIN
    position (real, per-position date rule regardless of MAIN's current
    live-trading flag) with no broker holdings must still trip the real
    ghost alert and sys.exit(2)."""
    db_positions = [_pos("ASIANENE.NS", 8, entry_date=date(2026, 8, 27))]  # before cutover

    monkeypatch.setattr(reconciler, "get_broker_positions", lambda: [])
    monkeypatch.setattr("db.repository.load_positions", lambda status: db_positions)
    monkeypatch.setattr("db.momentum_atr_repo.load_positions", lambda status: [])
    sent = []
    monkeypatch.setattr(reconciler, "_send", lambda msg: sent.append(msg))

    with pytest.raises(SystemExit) as exc_info:
        reconciler.run_reconcile()

    assert exc_info.value.code == 2
    assert any("ASIANENE" in m for m in sent)


def test_run_reconcile_ghost_fires_for_momentum_atr_only_position(reconciler, monkeypatch):
    """Regression for the real ASIANENE.NS incident (2026-09-16 through
    2026-09-22): a RANK_RULE_EXIT sell filled live but get_order_status()
    404'd, so momentum_atr's own DB never learned the position closed.
    run_reconcile()'s ghost check computed `db_syms - broker_syms` where
    db_syms was MAIN's positions only -- an atr-only ghost (MAIN has no
    record of it at all) was invisible to both sides of that comparison
    and this script logged clean "Reconciliation OK" every single day the
    ghost existed. Ghost must span momentum_atr_syms too."""
    atr_positions = [_pos("ASIANENE.NS", 38, entry_date=date(2026, 9, 10))]

    monkeypatch.setattr(reconciler, "get_broker_positions", lambda: [])
    monkeypatch.setattr("db.repository.load_positions", lambda status: [])
    monkeypatch.setattr("db.momentum_atr_repo.load_positions", lambda status: atr_positions)
    sent = []
    monkeypatch.setattr(reconciler, "_send", lambda msg: sent.append(msg))

    with pytest.raises(SystemExit) as exc_info:
        reconciler.run_reconcile()

    assert exc_info.value.code == 2
    assert any("ASIANENE" in m for m in sent)


def test_log_classifications_pages_on_quantity_mismatch(reconciler, monkeypatch):
    """Regression: the CYIENT.NS-shaped incident this classifier's own
    docstring documents (main=0 atr=6 broker=3 -> QUANTITY_MISMATCH) is
    invisible to run_reconcile()'s coarse ghost/unknown check (the symbol
    exists on BOTH sides, just with mismatched quantities -- no set
    difference to catch it) and, before this fix, log_classifications()
    only wrote a reporting.db row nobody sees without opening the
    dashboard. A BLOCKING_CLASSIFICATIONS finding must page via _send()."""
    atr_positions = [_pos("CYIENT.NS", 6)]
    broker_positions = [_broker_pos("CYIENT.NS", 3)]

    sent = []
    monkeypatch.setattr(reconciler, "_send", lambda msg: sent.append(msg))

    reconciler.log_classifications("09 Sep 2026", broker_positions, [], atr_positions)

    assert len(sent) == 1
    assert "CYIENT" in sent[0]
    assert "QUANTITY_MISMATCH" in sent[0]


def test_log_classifications_does_not_page_on_benign_mismatch(reconciler, monkeypatch):
    """Guard against the fix above becoming too broad: a manual-evidence-
    explained mismatch (MANUAL_BROKER_POSITION) still logs FAIL to
    reporting.db for the audit trail, but must not page -- it's already
    explained, not an unresolved discrepancy needing a human interrupt."""
    from db.repository import get_connection

    conn = get_connection()
    conn.execute(
        "INSERT INTO trades (symbol, sector, entry_date, entry_price, shares, exit_reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("WELCORP.NS", "TEST", "2026-08-01", 100.0, 5, "MANUAL_LIQUIDATION_PRE_PAPER_SWITCH"),
    )
    conn.commit()
    conn.close()

    # atr=5, broker=7, no main claim -- mismatch only explained by
    # manual_evidence, so it classifies MANUAL_BROKER_POSITION not
    # QUANTITY_MISMATCH (not in BLOCKING_CLASSIFICATIONS).
    atr_positions = [_pos("WELCORP.NS", 5)]
    broker_positions = [_broker_pos("WELCORP.NS", 7)]

    sent = []
    monkeypatch.setattr(reconciler, "_send", lambda msg: sent.append(msg))

    reconciler.log_classifications("09 Sep 2026", broker_positions, [], atr_positions)

    assert not sent
