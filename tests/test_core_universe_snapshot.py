"""
Tests for point-in-time CORE-universe tracking — the discovery+hysteresis
engine's counterpart to test_static_universe_sync.py's static-list tracking.

get_all_symbols_as_of() previously unioned only the static list; the CORE
layer (universe/manager.py promotions) was silently dropped for any
historical date, so every backtest/gate run using it evaluated the static
100-symbol list only, even though the live system trades ~40 CORE symbols.

Runs entirely against a temp SQLite DB -- never touches db/trading.db.
"""
import os
import sqlite3
import tempfile
from datetime import date, timedelta

import pytest


@pytest.fixture
def temp_universe_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    monkeypatch.setattr("db.universe_repo.DB_PATH", path)

    with open("db/schema_universe.sql") as f:
        schema = f.read()
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.commit()
    conn.close()

    yield path
    os.remove(path)


def _seed_candidate(symbol: str, status: str = "watchlist"):
    from db.universe_repo import upsert_candidate
    upsert_candidate(
        symbol, status=status, composite_score=50.0, score_percentile=50.0,
        added_date=date.today().isoformat(),
    )


def test_no_core_history_means_empty_not_unknown(temp_universe_db):
    from db.universe_repo import get_core_universe_tracking_start, get_core_symbols_as_of

    assert get_core_universe_tracking_start() is None
    # Empty, not None/raise -- CORE engine genuinely never ran, that's a known fact.
    assert get_core_symbols_as_of(date.today()) == []


def test_promotion_reflected_as_core_member(temp_universe_db):
    from db.universe_repo import update_candidate_status, get_core_symbols_as_of

    _seed_candidate("AAA")
    update_candidate_status("AAA", "core", reason="test promotion")
    assert get_core_symbols_as_of(date.today()) == ["AAA"]


def test_demotion_removes_from_core(temp_universe_db):
    from db.universe_repo import update_candidate_status, get_core_symbols_as_of

    _seed_candidate("AAA")
    update_candidate_status("AAA", "core", reason="promote")
    update_candidate_status("AAA", "watchlist", reason="demote")
    assert get_core_symbols_as_of(date.today()) == []


def test_removal_removes_from_core(temp_universe_db):
    from db.universe_repo import update_candidate_status, get_core_symbols_as_of

    _seed_candidate("AAA")
    update_candidate_status("AAA", "core", reason="promote")
    update_candidate_status("AAA", "removed", reason="delisted")
    assert get_core_symbols_as_of(date.today()) == []


def test_multiple_symbols_mixed_states(temp_universe_db):
    from db.universe_repo import update_candidate_status, get_core_symbols_as_of

    _seed_candidate("AAA")
    _seed_candidate("BBB")
    _seed_candidate("CCC")
    update_candidate_status("AAA", "core", reason="promote")
    update_candidate_status("BBB", "core", reason="promote")
    update_candidate_status("BBB", "watchlist", reason="demote")
    update_candidate_status("CCC", "core", reason="promote")
    assert get_core_symbols_as_of(date.today()) == ["AAA", "CCC"]


def test_date_before_core_tracking_start_returns_empty_not_raise(temp_universe_db):
    from db.universe_repo import update_candidate_status, get_core_symbols_as_of

    _seed_candidate("AAA")
    update_candidate_status("AAA", "core", reason="promote")
    # Unlike the static path, a pre-tracking-start date is a known-empty
    # fact (engine didn't exist yet), not raised as unknowable.
    assert get_core_symbols_as_of(date.today() - timedelta(days=1)) == []


def test_get_all_symbols_as_of_unions_static_and_core(temp_universe_db):
    from db.universe_repo import sync_static_universe_snapshot, update_candidate_status
    from data.universe import get_all_symbols_as_of

    sync_static_universe_snapshot(["AAA", "BBB"], reason="static baseline")
    _seed_candidate("CCC")
    update_candidate_status("CCC", "core", reason="promote")
    assert sorted(get_all_symbols_as_of(date.today())) == ["AAA", "BBB", "CCC"]


def test_get_all_symbols_as_of_dedupes_core_symbol_already_static(temp_universe_db):
    from db.universe_repo import sync_static_universe_snapshot, update_candidate_status
    from data.universe import get_all_symbols_as_of

    sync_static_universe_snapshot(["AAA", "BBB"], reason="static baseline")
    _seed_candidate("AAA")
    update_candidate_status("AAA", "core", reason="promote (already static)")
    result = get_all_symbols_as_of(date.today())
    assert sorted(result) == ["AAA", "BBB"]
    assert len(result) == len(set(result))


def test_get_all_symbols_as_of_still_raises_when_static_unavailable(temp_universe_db):
    from db.universe_repo import sync_static_universe_snapshot, update_candidate_status
    from data.universe import get_all_symbols_as_of, UniverseHistoryUnavailable

    sync_static_universe_snapshot(["AAA"], reason="static baseline")
    _seed_candidate("BBB")
    update_candidate_status("BBB", "core", reason="promote")
    # Static history still gates the whole call -- CORE data existing
    # doesn't paper over unknowable static-list membership pre-tracking.
    with pytest.raises(UniverseHistoryUnavailable):
        get_all_symbols_as_of(date.today() - timedelta(days=1))
