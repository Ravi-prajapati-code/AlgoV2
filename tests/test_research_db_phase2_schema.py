"""
Tests for the Phase 2 research DB schema (docs/48 §4.6-4.10, docs/49 §3-6):
research_questions, research_hypotheses, confidence_history, market_context,
trade_attribution, strategy_config_snapshot, feature_registry (view),
daily_strategy_state, and the research_decisions.market_context_id
column-add migration.

Runs entirely against a temp SQLite DB -- never touches db/research.db or
db/trading.db.
"""
import os
import tempfile

import pytest


@pytest.fixture
def temp_research_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # init_research_db() creates it fresh via executescript

    monkeypatch.setattr("db.research_repo.RESEARCH_DB_PATH", path)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_schema_creates_all_phase2_tables(temp_research_db):
    from db.research_repo import init_research_db, get_research_connection
    init_research_db()
    conn = get_research_connection()
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()}
    conn.close()
    for expected in ("research_questions", "research_hypotheses", "confidence_history",
                      "market_context", "trade_attribution", "strategy_config_snapshot",
                      "daily_strategy_state", "feature_registry"):
        assert expected in names


def test_init_research_db_idempotent_with_alter_migration(temp_research_db):
    from db.research_repo import init_research_db
    init_research_db()
    init_research_db()  # second call must not raise "duplicate column" or similar


def test_research_decisions_has_market_context_id_column(temp_research_db):
    from db.research_repo import init_research_db, get_research_connection
    init_research_db()
    conn = get_research_connection()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(research_decisions)")}
    conn.close()
    assert "market_context_id" in cols


def test_research_hypotheses_requires_failure_mechanism(temp_research_db):
    from db.research_repo import init_research_db, get_research_connection
    init_research_db()
    conn = get_research_connection()
    with pytest.raises(Exception):
        conn.execute(
            """INSERT INTO research_hypotheses
               (statement, economic_reasoning, expected_alpha_mechanism)
               VALUES ('X helps', 'reasoning', 'mechanism')"""
        )
        conn.commit()
    conn.close()


def test_research_questions_status_check_constraint(temp_research_db):
    from db.research_repo import init_research_db, get_research_connection
    init_research_db()
    conn = get_research_connection()
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO research_questions (question_text, status) VALUES ('q', 'NOT_A_STATUS')"
        )
        conn.commit()
    conn.close()


def test_confidence_history_links_hypothesis_and_experiment(temp_research_db):
    from db.research_repo import init_research_db, get_research_connection
    init_research_db()
    conn = get_research_connection()

    conn.execute("INSERT INTO research_questions (question_text) VALUES ('does X help?')")
    qid = conn.execute("SELECT id FROM research_questions").fetchone()["id"]
    conn.execute(
        """INSERT INTO research_hypotheses
           (question_id, statement, economic_reasoning, expected_alpha_mechanism,
            expected_failure_mechanism)
           VALUES (?, 'X helps', 'reasoning', 'mechanism', 'failure')""",
        (qid,),
    )
    hid = conn.execute("SELECT id FROM research_hypotheses").fetchone()["id"]
    conn.execute(
        "INSERT INTO confidence_history (hypothesis_id, old_score, new_score, reason) "
        "VALUES (?, 0.5, 0.6, 'test evidence')",
        (hid,),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM confidence_history WHERE hypothesis_id = ?", (hid,)).fetchone()
    conn.close()
    assert row["old_score"] == 0.5
    assert row["new_score"] == 0.6


def test_market_context_and_trade_attribution_require_valid_experiment_fk(temp_research_db):
    from db.research_repo import init_research_db, get_research_connection
    init_research_db()
    conn = get_research_connection()
    with pytest.raises(Exception):
        conn.execute("INSERT INTO market_context (experiment_id) VALUES (999)")
        conn.commit()
    conn.close()

    conn = get_research_connection()
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO trade_attribution (experiment_id, source) VALUES (999, 'live')"
        )
        conn.commit()
    conn.close()


def test_trade_attribution_source_check_constraint(temp_research_db):
    from db.research_repo import init_research_db, get_research_connection
    init_research_db()
    conn = get_research_connection()
    conn.execute(
        """INSERT INTO experiments (slug, title, author_role, strategy_family_id)
           VALUES ('exp1', 'Test', 'claude', NULL)"""
    )
    eid = conn.execute("SELECT id FROM experiments").fetchone()["id"]
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO trade_attribution (experiment_id, source) VALUES (?, 'not_a_source')",
            (eid,),
        )
        conn.commit()
    conn.close()


def test_feature_registry_view_is_queryable(temp_research_db):
    from db.research_repo import init_research_db, get_research_connection
    init_research_db()
    conn = get_research_connection()
    rows = conn.execute("SELECT * FROM feature_registry").fetchall()
    conn.close()
    assert rows == []  # empty DB, but the view resolves and joins without error


def test_feature_registry_aggregates_real_ingested_data(temp_research_db, tmp_path):
    """Reuses the Phase 1 ingest path so this exercises the real join shape
    feature_registry depends on: parameter_deltas -> experiments ->
    research_decisions/performance_metrics, not a hand-rolled row set."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.research_db_ingest import ingest
    from db.research_repo import get_research_connection
    from tests.test_research_db_ingest import _sample_payload, _write_payload

    json_path = _write_payload(str(tmp_path), _sample_payload(overrides={"SECTOR_RS_WEIGHT": "1.0"}))
    ingest(json_path, author_role="claude", title="Test experiment",
           docs_nn_path=None, strategy_family="FULL")

    conn = get_research_connection()
    candidate_id = conn.execute(
        "SELECT id FROM experiments WHERE slug NOT LIKE '%_baseline'").fetchone()["id"]
    conn.execute(
        """INSERT INTO research_decisions
           (experiment_id, proposing_role, reviewing_role, verdict, reasoning)
           VALUES (?, 'claude', 'codex', 'REJECT', 'test')""",
        (candidate_id,),
    )
    conn.commit()

    row = conn.execute(
        "SELECT * FROM feature_registry WHERE param_key = 'SECTOR_RS_WEIGHT'"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["times_tested"] == 1
    assert row["times_rejected"] == 1
    assert row["times_accepted"] == 0
    assert row["avg_test_cagr"] == pytest.approx(-11.95)
    assert row["avg_test_sharpe"] == pytest.approx(0.5)
