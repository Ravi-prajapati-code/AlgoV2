"""
Tests for the universe permanent block-list fix (docs/09_Open_Questions.md item 1,
docs/08_Project_Memory.md 2026-07-06 loser-leak recurrence).

Runs entirely against a temp SQLite DB -- never touches db/trading.db.
"""
import os
import sqlite3
import tempfile

import pytest

from config.universe_removed import REMOVED_SYMBOLS


@pytest.fixture
def temp_universe_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    monkeypatch.setattr("config.settings.DB_PATH", path)
    monkeypatch.setattr("db.universe_repo.DB_PATH", path)

    with open("db/schema_universe.sql") as f:
        schema = f.read()
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.commit()
    conn.close()

    yield path
    os.remove(path)


def _minimal_universe_config():
    return {
        "universe": {"core_size": 100, "watchlist_size": 200, "max_sector_pct_of_core": 0.25},
        "promotion": {"min_weeks_above_threshold": 4, "min_score_percentile": 60,
                      "fast_track_percentile": 90, "min_data_weeks": 26},
        "demotion": {"min_weeks_below_threshold": 3, "max_score_percentile": 40,
                     "pause_in_bear_regime": True, "bear_regime_floor_pct": 15},
        "removal": {"min_weeks_below_threshold": 6, "max_score_percentile": 25,
                    "fast_track_pct": 10, "fast_track_weeks": 2},
        "lockout": {"lockout_weeks": 8},
        "survivorship": {"no_data_weeks_threshold": 2, "liquidity_degradation_pct": 60},
        "strategy_feedback": {"enabled": False},
    }


class TestPermanentBlocklist:
    def test_blocklisted_symbol_in_core_is_force_removed_on_refresh(self, temp_universe_db):
        from db import universe_repo as repo
        from universe.manager import UniverseManager

        leaked_symbol = "LAURUSLABS.NS"
        assert leaked_symbol in REMOVED_SYMBOLS

        repo.upsert_candidate(leaked_symbol, name="Laurus Labs", sector="Healthcare",
                               status="core", added_date="2026-01-01")

        mgr = UniverseManager(_minimal_universe_config())
        summary = mgr.refresh(scored=[{"symbol": leaked_symbol, "composite_score": 0.9, "score_percentile": 95.0}])

        cand = repo.get_candidate(leaked_symbol)
        assert cand["status"] == "removed"
        assert summary.get("blocklist_removed") == 1
        assert leaked_symbol not in repo.get_active_symbols()

    def test_blocklisted_symbol_in_watchlist_is_force_removed(self, temp_universe_db):
        from db import universe_repo as repo
        from universe.manager import UniverseManager

        leaked_symbol = "THERMAX.NS"
        repo.upsert_candidate(leaked_symbol, name="Thermax", sector="Capital Goods",
                               status="watchlist", added_date="2026-01-01")

        mgr = UniverseManager(_minimal_universe_config())
        mgr.refresh(scored=[{"symbol": leaked_symbol, "composite_score": 0.8, "score_percentile": 80.0}])

        assert repo.get_candidate(leaked_symbol)["status"] == "removed"

    def test_non_blocklisted_symbol_promotes_normally(self, temp_universe_db):
        from db import universe_repo as repo
        from universe.manager import UniverseManager

        clean_symbol = "ABB.NS"
        assert clean_symbol not in REMOVED_SYMBOLS
        repo.upsert_candidate(clean_symbol, name="ABB India", sector="Capital Goods",
                               status="watchlist", added_date="2026-01-01",
                               weeks_above_threshold=4)

        mgr = UniverseManager(_minimal_universe_config())
        mgr.refresh(scored=[{"symbol": clean_symbol, "composite_score": 0.9, "score_percentile": 95.0}])

        assert repo.get_candidate(clean_symbol)["status"] == "core"

    def test_manual_promote_refuses_blocklisted_symbol(self, temp_universe_db):
        from db import universe_repo as repo
        from universe.manager import UniverseManager

        leaked_symbol = "BEL.NS"
        repo.upsert_candidate(leaked_symbol, name="Bharat Electronics", sector="Capital Goods",
                               status="watchlist", added_date="2026-01-01")

        mgr = UniverseManager(_minimal_universe_config())
        mgr.manual_promote(leaked_symbol)

        assert repo.get_candidate(leaked_symbol)["status"] == "watchlist"

    def test_add_to_watchlist_refuses_blocklisted_symbol(self, temp_universe_db):
        from db import universe_repo as repo
        from universe.manager import UniverseManager

        leaked_symbol = "KEI.NS"
        mgr = UniverseManager(_minimal_universe_config())
        mgr.add_to_watchlist(leaked_symbol, name="KEI Industries")

        assert repo.get_candidate(leaked_symbol) is None
