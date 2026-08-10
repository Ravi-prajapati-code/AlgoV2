"""
Tests for the main strategy's precompute/execute split (docs/59): the
precompute_indicators + sla_checkpoints round-trip in db/repository.py,
and the numpy.int64/numpy.bool_ JSON-safety hook that indicators/composite.py's
.iloc[-1]-derived dicts would otherwise raise TypeError on under plain
json.dumps.
"""
from datetime import date

import numpy as np
import pytest

TODAY = date(2026, 8, 10)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from db import repository as repo

    db_path = str(tmp_path / "main_precompute_sla_test.db")
    monkeypatch.setattr("db.repository.DB_PATH", db_path)
    repo.init_db()
    return repo


# ── precompute_indicators round-trip ─────────────────────────────────────

def test_load_precompute_indicators_none_when_nothing_recorded(repo):
    assert repo.load_precompute_indicators(TODAY) is None


def test_save_and_load_precompute_indicators_round_trip(repo):
    indicators = {"RELIANCE": {"close": 2500.5, "rsi": 55.2}}
    repo.save_precompute_indicators(
        TODAY, indicators, regime="BULL", market_bullish=True,
        strong_bull=False, index_candles=250, stalls=2,
    )
    loaded = repo.load_precompute_indicators(TODAY)
    assert loaded["indicators"] == indicators
    assert loaded["regime"] == "BULL"
    assert loaded["market_bullish"] is True
    assert loaded["strong_bull"] is False
    assert loaded["stalls"] == 2


def test_save_precompute_indicators_same_day_rerun_overwrites(repo):
    """UNIQUE(date) -- a same-day rerun (e.g. manual recovery after an
    ABORTED attempt) must replace the row, not collide."""
    repo.save_precompute_indicators(
        TODAY, {"A": {"close": 1}}, regime="BEAR", market_bullish=False,
        strong_bull=False, index_candles=100, stalls=5,
    )
    repo.save_precompute_indicators(
        TODAY, {"B": {"close": 2}}, regime="BULL", market_bullish=True,
        strong_bull=True, index_candles=250, stalls=0,
    )
    loaded = repo.load_precompute_indicators(TODAY)
    assert loaded["indicators"] == {"B": {"close": 2}}
    assert loaded["regime"] == "BULL"


def test_save_precompute_indicators_numpy_types_round_trip(repo):
    """The real gotcha: indicators/composite.py builds per-symbol dicts from
    pandas .iloc[-1] reads, yielding numpy.int64/numpy.bool_ -- these don't
    subclass Python int/bool and raise TypeError under plain json.dumps.
    Must be handled by the _json_safe hook, not hit as a silent runtime
    crash in production."""
    indicators = {
        "RELIANCE": {
            "close": np.float64(2500.5),
            "volume": np.int64(1_234_567),
            "above_ema": np.bool_(True),
        },
    }
    repo.save_precompute_indicators(
        TODAY, indicators, regime="BULL", market_bullish=True,
        strong_bull=False, index_candles=250, stalls=0,
    )
    loaded = repo.load_precompute_indicators(TODAY)
    assert loaded["indicators"]["RELIANCE"]["close"] == 2500.5
    assert loaded["indicators"]["RELIANCE"]["volume"] == 1_234_567
    assert loaded["indicators"]["RELIANCE"]["above_ema"] is True


# ── sla_checkpoints round-trip (same shape as momentum_atr's own table) ──

def test_load_sla_checkpoints_empty_when_nothing_recorded(repo):
    assert repo.load_sla_checkpoints(TODAY) == {}


def test_record_and_load_sla_checkpoint_round_trip(repo):
    repo.record_sla_checkpoint(TODAY, "PRECOMPUTE", "OK", "509 scored, regime=BULL")
    loaded = repo.load_sla_checkpoints(TODAY)
    assert loaded["PRECOMPUTE"]["status"] == "OK"
    assert "509 scored" in loaded["PRECOMPUTE"]["detail"]


def test_record_sla_checkpoint_same_day_rerun_overwrites(repo):
    repo.record_sla_checkpoint(TODAY, "EXECUTION", "CRASHED", "first attempt")
    repo.record_sla_checkpoint(TODAY, "EXECUTION", "OK", "retried")
    loaded = repo.load_sla_checkpoints(TODAY)
    assert loaded["EXECUTION"]["status"] == "OK"
    assert len(loaded) == 1


def test_record_sla_checkpoint_steps_are_independent(repo):
    repo.record_sla_checkpoint(TODAY, "PRECOMPUTE", "OK", "")
    repo.record_sla_checkpoint(TODAY, "EXECUTION", "ABORTED", "no precompute row")
    loaded = repo.load_sla_checkpoints(TODAY)
    assert set(loaded.keys()) == {"PRECOMPUTE", "EXECUTION"}


# ── evaluate_sla decision logic (pure, no DB) ────────────────────────────

def _load_evaluate_sla():
    from scripts.main_sla_check import evaluate_sla
    return evaluate_sla


def test_evaluate_sla_green_when_both_steps_ok():
    evaluate_sla = _load_evaluate_sla()
    checkpoints = {
        "PRECOMPUTE": {"status": "OK", "detail": "", "ts": ""},
        "EXECUTION": {"status": "OK", "detail": "", "ts": ""},
    }
    assert evaluate_sla(checkpoints)["health"] == "GREEN"


def test_evaluate_sla_red_when_step_missing_entirely():
    evaluate_sla = _load_evaluate_sla()
    checkpoints = {"PRECOMPUTE": {"status": "OK", "detail": "", "ts": ""}}
    result = evaluate_sla(checkpoints)
    assert result["health"] == "RED"
    assert any("EXECUTION" in line and "MISSING" in line for line in result["lines"])


def test_evaluate_sla_red_when_step_present_but_not_ok():
    evaluate_sla = _load_evaluate_sla()
    checkpoints = {
        "PRECOMPUTE": {"status": "OK", "detail": "", "ts": ""},
        "EXECUTION": {"status": "ABORTED", "detail": "no precompute row", "ts": ""},
    }
    result = evaluate_sla(checkpoints)
    assert result["health"] == "RED"
    assert any("EXECUTION" in line and "ABORTED" in line for line in result["lines"])


def test_evaluate_sla_red_when_both_missing():
    evaluate_sla = _load_evaluate_sla()
    result = evaluate_sla({})
    assert result["health"] == "RED"
    assert len(result["lines"]) == 2
