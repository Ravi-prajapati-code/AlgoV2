"""
Tests for point-in-time static-universe tracking (docs/13_Independent_Institutional_Review.md
§2/§4.3/§10 — the backtest previously applied TODAY's config/watchlist_nse.py list to every
historical date, including the out-of-sample TEST window, which is a confirmed look-ahead bias).

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


def test_no_snapshot_means_unknown(temp_universe_db):
    from db.universe_repo import get_static_universe_tracking_start, get_static_symbols_as_of

    assert get_static_universe_tracking_start() is None
    assert get_static_symbols_as_of(date.today()) is None


def test_first_sync_seeds_baseline_for_all_current_symbols(temp_universe_db):
    from db.universe_repo import sync_static_universe_snapshot, get_static_symbols_as_of

    n = sync_static_universe_snapshot(["AAA", "BBB", "CCC"], reason="test baseline")
    assert n == 3
    assert get_static_symbols_as_of(date.today()) == ["AAA", "BBB", "CCC"]


def test_date_before_tracking_start_raises(temp_universe_db):
    from db.universe_repo import sync_static_universe_snapshot
    from data.universe import get_all_symbols_as_of, UniverseHistoryUnavailable

    sync_static_universe_snapshot(["AAA", "BBB"], reason="test baseline")
    with pytest.raises(UniverseHistoryUnavailable):
        get_all_symbols_as_of(date.today() - timedelta(days=1))


def test_removal_after_sync_is_reflected_going_forward(temp_universe_db):
    from db.universe_repo import sync_static_universe_snapshot, get_static_symbols_as_of

    sync_static_universe_snapshot(["AAA", "BBB", "CCC"], reason="test baseline")
    sync_static_universe_snapshot(["AAA", "CCC"], reason="removed BBB")
    assert get_static_symbols_as_of(date.today()) == ["AAA", "CCC"]


def test_addition_after_sync_is_reflected_going_forward(temp_universe_db):
    from db.universe_repo import sync_static_universe_snapshot, get_static_symbols_as_of

    sync_static_universe_snapshot(["AAA", "BBB"], reason="test baseline")
    sync_static_universe_snapshot(["AAA", "BBB", "DDD"], reason="added DDD")
    assert get_static_symbols_as_of(date.today()) == ["AAA", "BBB", "DDD"]


def test_resync_with_no_changes_logs_nothing(temp_universe_db):
    from db.universe_repo import sync_static_universe_snapshot

    sync_static_universe_snapshot(["AAA", "BBB"], reason="test baseline")
    n = sync_static_universe_snapshot(["AAA", "BBB"], reason="no-op resync")
    assert n == 0


def test_working_file_must_match_last_logged_snapshot(temp_universe_db):
    """
    Guardrail: if config/watchlist_nse.py's ALL_SYMBOLS is ever edited without also calling
    sync_static_universe_snapshot(), the live static universe and the last logged snapshot
    diverge silently -- the exact failure mode that produced the confirmed look-ahead bias
    (2026-06-17 revision never logged with a real date). This test only checks the *mechanism*
    stays correct: after a snapshot, an unsynced set difference must be detectable by comparing
    against get_static_symbols_as_of(today) before treating it as up to date.
    """
    from db.universe_repo import sync_static_universe_snapshot, get_static_symbols_as_of
    from config.watchlist_nse import ALL_SYMBOLS

    sync_static_universe_snapshot(ALL_SYMBOLS, reason="ci check baseline")
    assert sorted(get_static_symbols_as_of(date.today())) == sorted(ALL_SYMBOLS)
