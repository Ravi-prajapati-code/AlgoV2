"""
Tests for db/reporting_repo.py (docs/60 Phase 1): round-trip for all 7
reporting tables against a tmp-path DB. This DB is physically separate from
trading.db/momentum_atr.db (config.settings.REPORTING_DB_PATH) -- these
tests never touch either strategy DB.
"""
import pytest


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from db import reporting_repo as repo

    db_path = str(tmp_path / "reporting_test.db")
    monkeypatch.setattr("db.reporting_repo.REPORTING_DB_PATH", db_path)
    repo.init_db()
    return repo


# ── strategy_registry ────────────────────────────────────────────────────

def test_register_strategy_idempotent(repo):
    repo.register_strategy("main", "Main Strategy", "db/trading.db")
    repo.register_strategy("main", "Main Strategy (renamed)", "db/trading.db")
    rows = repo.load_strategy_registry()
    assert len(rows) == 1
    assert rows[0]["display_name"] == "Main Strategy"


def test_set_strategy_status_is_manual_only(repo):
    repo.register_strategy("momentum_atr", "Momentum x ATR", "db/momentum_atr.db")
    repo.set_strategy_status("momentum_atr", "VALIDATED", set_by="ravi", ts="2026-08-10T10:00:00")
    rows = repo.load_strategy_registry()
    row = next(r for r in rows if r["strategy_id"] == "momentum_atr")
    assert row["status_label"] == "VALIDATED"
    assert row["status_set_by"] == "ravi"


# ── broker_snapshot ──────────────────────────────────────────────────────

def test_broker_snapshot_round_trip(repo):
    repo.save_broker_snapshot("2026-08-10T10:00:00", 50000.0, 250000.0, {"RELIANCE.NS": 10})
    loaded = repo.load_latest_broker_snapshot()
    assert loaded["broker_cash"] == 50000.0
    assert loaded["total_equity"] == 250000.0
    assert loaded["holdings"] == {"RELIANCE.NS": 10}


def test_broker_snapshot_latest_wins(repo):
    repo.save_broker_snapshot("t1", 1.0, 1.0, {})
    repo.save_broker_snapshot("t2", 2.0, 2.0, {})
    assert repo.load_latest_broker_snapshot()["broker_cash"] == 2.0


# ── strategy_capital_snapshot ────────────────────────────────────────────

def test_capital_snapshot_round_trip_no_allocation_cap(repo):
    """MAIN's case -- strategy_allocated_cash is NULL, never fabricated."""
    repo.save_strategy_capital_snapshot(
        "main", "2026-08-10T10:00:00",
        strategy_invested_value=100000.0, strategy_equity=150000.0,
        source_note="MAIN has no allocation cap; this is whole-account cash MAIN currently sees, not a segregated pool.",
        strategy_available_cash=50000.0,
    )
    loaded = repo.load_latest_strategy_capital_snapshot("main")
    assert loaded["strategy_allocated_cash"] is None
    assert loaded["strategy_equity"] == 150000.0
    assert "no allocation cap" in loaded["source_note"]


def test_capital_snapshot_round_trip_with_allocation_cap(repo):
    repo.save_strategy_capital_snapshot(
        "momentum_atr", "2026-08-10T10:00:00",
        strategy_invested_value=40000.0, strategy_equity=100000.0,
        source_note="momentum_atr capped at 40% of real combined account equity",
        strategy_allocated_cash=100000.0, strategy_available_cash=60000.0,
    )
    loaded = repo.load_latest_strategy_capital_snapshot("momentum_atr")
    assert loaded["strategy_allocated_cash"] == 100000.0


def test_capital_snapshot_per_strategy_isolation(repo):
    repo.save_strategy_capital_snapshot("main", "t1", strategy_invested_value=1.0,
                                         strategy_equity=1.0, source_note="x")
    repo.save_strategy_capital_snapshot("momentum_atr", "t2", strategy_invested_value=2.0,
                                         strategy_equity=2.0, source_note="y")
    assert repo.load_latest_strategy_capital_snapshot("main")["strategy_equity"] == 1.0
    assert repo.load_latest_strategy_capital_snapshot("momentum_atr")["strategy_equity"] == 2.0


# ── strategy_position_snapshot ───────────────────────────────────────────

def test_position_snapshot_collision_flag_set_on_mismatch(repo):
    repo.save_strategy_position_snapshot("t1", "RELIANCE.NS", broker_qty=10, main_qty=5, momentum_atr_qty=3)
    rows = repo.load_position_snapshots_for_ts("t1")
    assert rows[0]["collision_flag"] == 1


def test_position_snapshot_collision_flag_clear_on_match(repo):
    repo.save_strategy_position_snapshot("t1", "RELIANCE.NS", broker_qty=8, main_qty=5, momentum_atr_qty=3)
    rows = repo.load_position_snapshots_for_ts("t1")
    assert rows[0]["collision_flag"] == 0


def test_latest_position_snapshot_ts(repo):
    repo.save_strategy_position_snapshot("t1", "A.NS", broker_qty=1)
    repo.save_strategy_position_snapshot("t2", "B.NS", broker_qty=2)
    assert repo.load_latest_position_snapshot_ts() == "t2"


# ── strategy_reconciliation_log ──────────────────────────────────────────

def test_reconciliation_log_pass_round_trip(repo):
    repo.record_reconciliation("t1", "position_collision_sum", "PASS")
    rows = repo.load_recent_reconciliation()
    assert rows[0]["result"] == "PASS"
    assert rows[0]["auto_repaired"] == 0


def test_reconciliation_log_never_silent_repair_fields(repo):
    """If auto_repaired is ever set, the what/why/before/after/source
    fields must be captured -- this is the schema-level guarantee behind
    the project's 'never silently repair a discrepancy' rule."""
    repo.record_reconciliation(
        "t1", "position_collision_sum", "WARNING", auto_repaired=True,
        repair_what="inserted missing position", repair_why="broker-only unknown position",
        repair_previous_value="none", repair_new_value="RELIANCE.NS x10",
        repair_source="observability_snapshot.py",
    )
    row = repo.load_recent_reconciliation()[0]
    assert row["auto_repaired"] == 1
    assert row["repair_what"] == "inserted missing position"
    assert row["repair_source"] == "observability_snapshot.py"


# ── strategy_alert ────────────────────────────────────────────────────────

def test_alert_round_trip_and_default_unacknowledged(repo):
    repo.record_alert("t1", "CRITICAL", "position_collision", "mismatch found", "observability_snapshot.py")
    rows = repo.load_recent_alerts()
    assert rows[0]["severity"] == "CRITICAL"
    assert rows[0]["acknowledged"] == 0


def test_acknowledge_alert(repo):
    alert_id = repo.record_alert("t1", "WARNING", "x", "y", "z")
    repo.acknowledge_alert(alert_id)
    rows = repo.load_recent_alerts()
    assert rows[0]["acknowledged"] == 1


# ── strategy_run_log ──────────────────────────────────────────────────────

def test_run_log_round_trip(repo):
    repo.record_run_log("t1", "observability_snapshot", "OK", detail="5 symbols")
    rows = repo.load_recent_run_log()
    assert rows[0]["status"] == "OK"
    assert rows[0]["job_name"] == "observability_snapshot"
