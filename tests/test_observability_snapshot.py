"""
Tests for scripts/observability_snapshot.py's pure logic (docs/60 Phase 1):
3-way position snapshotting + collision detection, capital snapshot math
for both strategies (MAIN's no-allocation-cap case vs momentum_atr's
capped case), and the alert-only reconciliation checks -- including the
new momentum_atr-vs-broker check that has no prior implementation
anywhere in the codebase. Uses a tmp-path reporting DB; never touches
trading.db or momentum_atr.db.
"""
from types import SimpleNamespace

import pytest

from reconciliation.classifier import Classification


@pytest.fixture
def rrepo(tmp_path, monkeypatch):
    from db import reporting_repo as repo

    db_path = str(tmp_path / "reporting_test.db")
    monkeypatch.setattr("db.reporting_repo.REPORTING_DB_PATH", db_path)
    repo.init_db()
    return repo


@pytest.fixture
def snap(rrepo, monkeypatch):
    import scripts.observability_snapshot as snap_mod
    monkeypatch.setattr(snap_mod, "rrepo", rrepo)
    return snap_mod


def _pos(symbol, shares, entry_price=100.0, entry_date=None):
    # Default predates MAIN_STRATEGY_PAPER_SINCE (2026-09-03) so existing
    # callers keep meaning "real" MAIN position unless a test explicitly
    # passes a post-cutover entry_date.
    from datetime import date
    return SimpleNamespace(symbol=symbol, shares=shares, entry_price=entry_price,
                            entry_date=entry_date or date(2026, 1, 1))


def _broker_snap(qty_by_symbol=None, ltp_by_symbol=None, cash=0.0, total_equity=0.0):
    return {
        "cash": cash,
        "qty_by_symbol": qty_by_symbol or {},
        "ltp_by_symbol": ltp_by_symbol or {},
        "total_equity": total_equity,
    }


# ── snapshot_positions: 3-way view + collision detection ────────────────

def test_snapshot_positions_no_collision_when_qtys_match(snap):
    main_positions = [_pos("RELIANCE.NS", 5)]
    atr_positions = [_pos("RELIANCE.NS", 3)]
    broker = _broker_snap(qty_by_symbol={"RELIANCE.NS": 8})

    rows = snap.snapshot_positions("t1", main_positions, atr_positions, broker)

    assert rows == [("RELIANCE.NS", 5, 3, 8, Classification.MATCH)]
    saved = snap.rrepo.load_position_snapshots_for_ts("t1")
    assert saved[0]["collision_flag"] == 0
    assert saved[0]["classification"] == Classification.MATCH.value


def test_snapshot_positions_collision_when_qtys_mismatch(snap):
    main_positions = [_pos("TCS.NS", 5)]
    atr_positions = []
    broker = _broker_snap(qty_by_symbol={"TCS.NS": 2})  # broker only shows 2, DB thinks 5

    snap.snapshot_positions("t1", main_positions, atr_positions, broker)

    saved = snap.rrepo.load_position_snapshots_for_ts("t1")
    assert saved[0]["collision_flag"] == 1


def test_snapshot_positions_excludes_post_cutover_main_paper_qty(snap):
    """GOLDBEES.NS-shaped: a MAIN position opened after
    MAIN_STRATEGY_PAPER_SINCE is simulated and must not count toward
    ownership math -- momentum_atr's real 175 sh already matches the
    broker exactly, so this must classify MATCH, not DUPLICATE_OWNERSHIP,
    even though MAIN's own ledger shows 102 shares of the same symbol."""
    from datetime import date
    main_positions = [_pos("GOLDBEES.NS", 102, entry_date=date(2026, 9, 7))]  # after cutover
    atr_positions = [_pos("GOLDBEES.NS", 175)]
    broker = _broker_snap(qty_by_symbol={"GOLDBEES.NS": 175})

    rows = snap.snapshot_positions("t1", main_positions, atr_positions, broker)

    assert rows == [("GOLDBEES.NS", 0, 175, 175, Classification.MATCH)]
    saved = snap.rrepo.load_position_snapshots_for_ts("t1")
    assert saved[0]["classification"] == Classification.MATCH.value
    assert '"main_paper_qty": 102' in saved[0]["evidence_json"]


def test_snapshot_positions_keeps_pre_cutover_main_qty(snap):
    """ASIANENE.NS-shaped: a MAIN position opened before
    MAIN_STRATEGY_PAPER_SINCE is real regardless of MAIN's current
    live-trading flag, and must stay counted -- a blanket 'ignore MAIN
    whenever paper mode is on today' check would wrongly blind this."""
    from datetime import date
    main_positions = [_pos("ASIANENE.NS", 8, entry_date=date(2026, 8, 27))]  # before cutover
    atr_positions = [_pos("ASIANENE.NS", 8)]
    broker = _broker_snap(qty_by_symbol={})

    rows = snap.snapshot_positions("t1", main_positions, atr_positions, broker)

    assert rows == [("ASIANENE.NS", 8, 8, 0, Classification.GHOST_DB_POSITION)]


def test_snapshot_positions_covers_broker_only_symbol(snap):
    """A symbol neither ledger knows about must still appear (broker_qty
    with 0 on both DB sides) -- this is exactly the unknown-position case
    reconcile_positions.py's auto-fix logic exists for on the MAIN side."""
    broker = _broker_snap(qty_by_symbol={"NEWSTOCK.NS": 4})

    rows = snap.snapshot_positions("t1", [], [], broker)

    assert rows == [("NEWSTOCK.NS", 0, 0, 4, Classification.UNKNOWN_POSITION)]


# ── snapshot_main_capital: no fabricated allocation cap ──────────────────

def test_snapshot_main_capital_allocated_cash_is_null(snap):
    main_snapshot = SimpleNamespace(cash=50000.0, strategy_value=150000.0)

    snap.snapshot_main_capital("t1", main_snapshot)

    loaded = snap.rrepo.load_latest_strategy_capital_snapshot("main")
    assert loaded["strategy_allocated_cash"] is None
    assert loaded["strategy_available_cash"] == 50000.0
    assert loaded["strategy_invested_value"] == 100000.0
    assert "no allocation cap" in loaded["source_note"]


def test_snapshot_main_capital_skips_when_no_snapshot_yet(snap):
    snap.snapshot_main_capital("t1", None)
    assert snap.rrepo.load_latest_strategy_capital_snapshot("main") is None


# ── snapshot_atr_capital: real 40%-of-account cap ─────────────────────────

def test_snapshot_atr_capital_uses_live_allocation_cap(snap, monkeypatch):
    monkeypatch.setattr(snap, "MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT", 0.40)
    atr_positions = [_pos("INFY.NS", 10, entry_price=1400.0)]
    atr_state = SimpleNamespace(cash=20000.0)
    broker = _broker_snap(ltp_by_symbol={"INFY.NS": 1500.0}, total_equity=200000.0)

    snap.snapshot_atr_capital("t1", atr_positions, atr_state, broker)

    loaded = snap.rrepo.load_latest_strategy_capital_snapshot("momentum_atr")
    assert loaded["strategy_invested_value"] == 15000.0  # 10 * live ltp 1500, not stale entry_price
    assert loaded["strategy_equity"] == 35000.0           # cash 20000 + invested 15000
    assert loaded["strategy_allocated_cash"] == 80000.0   # 200000 * 0.40


def test_snapshot_atr_capital_falls_back_to_entry_price_on_missing_ltp(snap, monkeypatch):
    monkeypatch.setattr(snap, "MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT", 0.40)
    atr_positions = [_pos("OBSCURE.NS", 4, entry_price=250.0)]
    atr_state = SimpleNamespace(cash=1000.0)
    broker = _broker_snap(ltp_by_symbol={}, total_equity=50000.0)  # no live quote for this symbol

    snap.snapshot_atr_capital("t1", atr_positions, atr_state, broker)

    loaded = snap.rrepo.load_latest_strategy_capital_snapshot("momentum_atr")
    assert loaded["strategy_invested_value"] == 1000.0  # 4 * fallback entry_price 250


# ── run_reconciliation: alert-only, classifier-driven ────────────────────

def test_reconciliation_all_pass_when_everything_matches(snap):
    rows = [
        ("RELIANCE.NS", 5, 0, 5, Classification.MATCH),
        ("INFY.NS", 0, 3, 3, Classification.MATCH),
    ]
    snap.run_reconciliation("t1", rows)

    results = {r["check_name"]: r["result"] for r in snap.rrepo.load_recent_reconciliation()}
    assert results["position_classification"] == "PASS"
    assert snap.rrepo.load_recent_alerts() == []


def test_reconciliation_flags_mismatch_and_alerts_critical(snap):
    # broker shows 8, ledgers only account for 5 -- QUANTITY_MISMATCH shape
    rows = [("RELIANCE.NS", 5, 0, 8, Classification.QUANTITY_MISMATCH)]
    snap.run_reconciliation("t1", rows)

    results = {r["check_name"]: r["result"] for r in snap.rrepo.load_recent_reconciliation()}
    assert results["position_classification"] == "FAIL"
    alerts = snap.rrepo.load_recent_alerts()
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "CRITICAL"
    assert alerts[0]["category"] == Classification.QUANTITY_MISMATCH.value


def test_reconciliation_flags_main_ghost_position(snap):
    """DB open, broker has nothing -- a possibly-failed sell, alert-only
    (never auto-closed), same convention as reconcile_positions.py."""
    rows = [("RELIANCE.NS", 5, 0, 0, Classification.GHOST_DB_POSITION)]
    snap.run_reconciliation("t1", rows)

    results = {r["check_name"]: r["result"] for r in snap.rrepo.load_recent_reconciliation()}
    assert results["position_classification"] == "FAIL"
    alerts = snap.rrepo.load_recent_alerts()
    assert alerts[0]["severity"] == "WARNING"
    assert alerts[0]["category"] == Classification.GHOST_DB_POSITION.value


def test_reconciliation_flags_momentum_atr_ghost_position_new_check(snap):
    """This check does not exist anywhere else in the codebase today
    (docs/60 Page 14) -- confirms it actually fires and raises a WARNING
    alert, not just a silent log line."""
    rows = [("INFY.NS", 0, 3, 0, Classification.GHOST_DB_POSITION)]
    snap.run_reconciliation("t1", rows)

    results = {r["check_name"]: r["result"] for r in snap.rrepo.load_recent_reconciliation()}
    assert results["position_classification"] == "FAIL"
    alerts = snap.rrepo.load_recent_alerts()
    assert any(a["category"] == Classification.GHOST_DB_POSITION.value for a in alerts)


# ── get_broker_snapshot: CNC-only filter, fail-closed on empty ──────────

def test_get_broker_snapshot_filters_non_cnc_positions(snap, monkeypatch):
    fake_positions = [
        SimpleNamespace(symbol="RELIANCE.NS", quantity=5, avg_price=2400.0, ltp=2500.0, product="CNC"),
        SimpleNamespace(symbol="NIFTY.NS", quantity=1, avg_price=90.0, ltp=100.0, product="MIS"),
    ]
    fake_broker = SimpleNamespace(
        get_positions=lambda: fake_positions,
        get_available_cash=lambda: 10000.0,
    )
    monkeypatch.setattr("broker.upstox.UpstoxBroker", lambda: fake_broker)

    result = snap.get_broker_snapshot()

    assert result["qty_by_symbol"] == {"RELIANCE.NS": 5}
    assert result["total_equity"] == 10000.0 + 5 * 2500.0
    assert result["holdings"] == {
        "RELIANCE.NS": {"qty": 5, "avg_price": 2400.0, "ltp": 2500.0},
    }


def test_get_broker_snapshot_holdings_avg_price_qty_weighted_across_lots(snap, monkeypatch):
    """Same symbol across short-term + long-term legs must weight-average
    avg_price by qty, not just take the last lot seen."""
    fake_positions = [
        SimpleNamespace(symbol="TCS.NS", quantity=2, avg_price=3000.0, ltp=3200.0, product="CNC"),
        SimpleNamespace(symbol="TCS.NS", quantity=8, avg_price=3100.0, ltp=3200.0, product="CNC"),
    ]
    fake_broker = SimpleNamespace(
        get_positions=lambda: fake_positions,
        get_available_cash=lambda: 0.0,
    )
    monkeypatch.setattr("broker.upstox.UpstoxBroker", lambda: fake_broker)

    result = snap.get_broker_snapshot()

    assert result["holdings"]["TCS.NS"]["qty"] == 10
    assert result["holdings"]["TCS.NS"]["avg_price"] == pytest.approx(3080.0)


def test_get_broker_snapshot_returns_none_when_empty(snap, monkeypatch):
    fake_broker = SimpleNamespace(get_positions=lambda: [], get_available_cash=lambda: 0.0)
    monkeypatch.setattr("broker.upstox.UpstoxBroker", lambda: fake_broker)

    assert snap.get_broker_snapshot() is None


def test_get_broker_snapshot_returns_none_on_exception(snap, monkeypatch):
    def _raise():
        raise ConnectionError("token expired")

    fake_broker = SimpleNamespace(get_positions=_raise, get_available_cash=lambda: 0.0)
    monkeypatch.setattr("broker.upstox.UpstoxBroker", lambda: fake_broker)

    assert snap.get_broker_snapshot() is None


# ── main(): cron-cycle orchestration, discoverable from reporting.db alone ─

def test_main_aborts_with_failed_run_log_when_broker_unavailable(snap, monkeypatch):
    """A cron failure must be discoverable from reporting.db alone (no log
    grepping needed) -- confirms the FAILED row actually lands before exit."""
    monkeypatch.setattr(snap, "get_broker_snapshot", lambda: None)

    with pytest.raises(SystemExit) as exc_info:
        snap.main()

    assert exc_info.value.code == 1
    runs = snap.rrepo.load_recent_run_log()
    assert runs[0]["status"] == "FAILED"
    assert runs[0]["job_name"] == "observability_snapshot"


def test_main_ok_run_log_and_append_only_position_snapshots_across_cycles(snap, monkeypatch):
    """Two consecutive cron cycles for the same symbol must both persist as
    distinct rows -- strategy_position_snapshot has no upsert path, it is
    append-only immutable history by design."""
    calls = {"n": 0}

    def _fake_broker_snap():
        calls["n"] += 1
        qty = 5 if calls["n"] == 1 else 7  # qty changes between cycles
        snap_dict = _broker_snap(qty_by_symbol={"RELIANCE.NS": qty}, cash=1000.0, total_equity=1000.0 + qty * 100)
        snap_dict["holdings"] = {"RELIANCE.NS": {"qty": qty, "avg_price": 100.0, "ltp": 100.0}}
        return snap_dict

    monkeypatch.setattr(snap, "get_broker_snapshot", _fake_broker_snap)
    monkeypatch.setattr(snap, "get_main_state", lambda: ([_pos("RELIANCE.NS", 5)], None))
    monkeypatch.setattr(snap, "get_momentum_atr_state", lambda: ([], SimpleNamespace(cash=0.0)))

    snap.main()
    snap.main()

    runs = snap.rrepo.load_recent_run_log()
    assert len(runs) == 2
    assert all(r["status"] == "OK" for r in runs)

    # Both cycles wrote a row for RELIANCE.NS -- confirm 2 distinct rows exist,
    # not one overwritten row.
    import sqlite3
    conn = sqlite3.connect(snap.rrepo.REPORTING_DB_PATH)
    count = conn.execute(
        "SELECT COUNT(*) FROM strategy_position_snapshot WHERE symbol = 'RELIANCE.NS'"
    ).fetchone()[0]
    conn.close()
    assert count == 2
