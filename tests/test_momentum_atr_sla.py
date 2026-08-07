"""
Watchdog tests for momentum_atr's SLA checkpoints (user-requested,
2026-08-07: "I would build it before adding any more features" -- a
step-level watchdog, not a plain log-exists check, since "logs can exist
even when logic failed"). Covers the DB round-trip in
db/momentum_atr_repo.py and the pure RED/GREEN decision logic in
scripts/momentum_atr_sla_check.py.
"""
from datetime import date

import pytest

TODAY = date(2026, 8, 7)


@pytest.fixture
def sla_repo(tmp_path, monkeypatch):
    from db import momentum_atr_repo as repo

    db_path = str(tmp_path / "momentum_atr_sla_test.db")
    monkeypatch.setattr("db.momentum_atr_repo.MOMENTUM_ATR_DB_PATH", db_path)
    repo.init_db()
    return repo


def test_load_sla_checkpoints_empty_when_nothing_recorded(sla_repo):
    """A day nothing ever wrote to must come back as an empty dict, not a
    crash or a fabricated default -- the caller (evaluate_sla) relies on
    'key absent' meaning 'that stage never ran'."""
    assert sla_repo.load_sla_checkpoints(TODAY) == {}


def test_record_and_load_sla_checkpoint_round_trip(sla_repo):
    sla_repo.record_sla_checkpoint(TODAY, "PRECOMPUTE", "OK", "509 scored, 0 stalls")
    loaded = sla_repo.load_sla_checkpoints(TODAY)
    assert loaded["PRECOMPUTE"]["status"] == "OK"
    assert "509 scored" in loaded["PRECOMPUTE"]["detail"]


def test_record_sla_checkpoint_same_day_rerun_overwrites(sla_repo):
    """UNIQUE(date, step) must mean a same-day rerun replaces the row, not
    accumulate duplicates -- matches daily_ranking/equity_snapshots'
    idempotency pattern this table was explicitly modeled on."""
    sla_repo.record_sla_checkpoint(TODAY, "EXECUTION", "CRASHED", "first attempt")
    sla_repo.record_sla_checkpoint(TODAY, "EXECUTION", "OK", "retried, equity=100000")
    loaded = sla_repo.load_sla_checkpoints(TODAY)
    assert loaded["EXECUTION"]["status"] == "OK"
    assert len(loaded) == 1


def test_record_sla_checkpoint_steps_are_independent(sla_repo):
    sla_repo.record_sla_checkpoint(TODAY, "PRECOMPUTE", "OK", "")
    sla_repo.record_sla_checkpoint(TODAY, "EXECUTION", "ABORTED", "no_precomputed_ranking")
    loaded = sla_repo.load_sla_checkpoints(TODAY)
    assert set(loaded.keys()) == {"PRECOMPUTE", "EXECUTION"}


# ── evaluate_sla decision logic (pure, no DB) ────────────────────────────

def _load_evaluate_sla():
    from scripts.momentum_atr_sla_check import evaluate_sla
    return evaluate_sla


def test_evaluate_sla_green_when_both_steps_ok():
    evaluate_sla = _load_evaluate_sla()
    checkpoints = {
        "PRECOMPUTE": {"status": "OK", "detail": "", "ts": ""},
        "EXECUTION": {"status": "OK", "detail": "", "ts": ""},
    }
    result = evaluate_sla(checkpoints)
    assert result["health"] == "GREEN"


def test_evaluate_sla_red_when_step_missing_entirely():
    """The actual failure mode from the repo's documented silent-outage
    incident: the cron never fired at all (flock deadlock, crash before
    any code runs), so nothing gets written -- distinct from a step that
    ran and reported failure."""
    evaluate_sla = _load_evaluate_sla()
    checkpoints = {"PRECOMPUTE": {"status": "OK", "detail": "", "ts": ""}}
    result = evaluate_sla(checkpoints)
    assert result["health"] == "RED"
    assert any("EXECUTION" in line and "MISSING" in line for line in result["lines"])


def test_evaluate_sla_red_when_step_present_but_not_ok():
    """A step that ran and reported ABORTED/CRASHED must also be RED --
    this is the case a plain log-exists check would miss, since the log
    file exists and is non-empty even though the logic failed."""
    evaluate_sla = _load_evaluate_sla()
    checkpoints = {
        "PRECOMPUTE": {"status": "OK", "detail": "", "ts": ""},
        "EXECUTION": {"status": "CRASHED", "detail": "Upstox broker init failed", "ts": ""},
    }
    result = evaluate_sla(checkpoints)
    assert result["health"] == "RED"
    assert any("EXECUTION" in line and "CRASHED" in line for line in result["lines"])


def test_evaluate_sla_red_when_both_missing():
    evaluate_sla = _load_evaluate_sla()
    result = evaluate_sla({})
    assert result["health"] == "RED"
    assert len(result["lines"]) == 2
