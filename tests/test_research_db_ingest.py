"""
Tests for the Phase 1 research DB pipeline (docs/48+49): schema creation,
scripts/robustness_gate.py's JSON emission contract, and
scripts/research_db_ingest.py reading that JSON into db/research.db.

Runs entirely against a temp SQLite DB -- never touches db/research.db or
db/trading.db.
"""
import json
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


def _sample_payload(overrides=None, verdict="REJECT"):
    metric = lambda source, cagr: {
        "source": source, "cagr": cagr, "sharpe": 0.5, "max_drawdown_pct": 10.0,
        "total_trades": 100, "win_rate": 50.0,
        "window_start": "2022-01-01", "window_end": "2024-12-31",
    }
    return {
        "slug": "gate_TEST_KEY_1.0_20260803T120000",
        "generated_at": "2026-08-03T12:00:00",
        "seed": 42,
        "runtime_ms": 1000,
        "peak_mem_mb": 100.0,
        "overrides": overrides if overrides is not None else {"SECTOR_RS_WEIGHT": "1.0"},
        "verdict": verdict,
        "failures": ["candidate TEST-window SHARPE worse"] if verdict == "REJECT" else [],
        "commit_hash": "abc1234",
        "branch": "main",
        "arms": {
            "baseline": {"metrics": [metric("train", 1.0), metric("test", 41.64), metric("full", -4.64)]},
            "candidate": {"metrics": [metric("train", 10.47), metric("test", -11.95), metric("full", 0.51)]},
        },
    }


def _write_payload(tmp_path, payload):
    path = os.path.join(tmp_path, f"{payload['slug']}.json")
    with open(path, "w") as f:
        json.dump(payload, f)
    return path


def test_schema_creates_all_phase1_tables(temp_research_db):
    from db.research_repo import init_research_db, get_research_connection
    init_research_db()
    conn = get_research_connection()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    for expected in ("experiments", "parameter_deltas", "performance_metrics",
                      "evidence_ledger", "research_decisions",
                      "strategy_family", "param_taxonomy"):
        assert expected in tables


def test_foreign_keys_enforced(temp_research_db):
    from db.research_repo import init_research_db, get_research_connection
    init_research_db()
    conn = get_research_connection()
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO performance_metrics (experiment_id, source) VALUES (999, 'test')"
        )
        conn.commit()
    conn.close()


def test_ingest_creates_baseline_and_candidate_experiments(temp_research_db, tmp_path):
    from scripts.research_db_ingest import ingest
    from db.research_repo import get_research_connection

    json_path = _write_payload(str(tmp_path), _sample_payload())
    ingest(json_path, author_role="claude", title="Test experiment",
           docs_nn_path=None, strategy_family="FULL")

    conn = get_research_connection()
    rows = {r["slug"]: dict(r) for r in conn.execute(
        "SELECT * FROM experiments").fetchall()}
    conn.close()

    assert "gate_TEST_KEY_1.0_20260803T120000" in rows
    assert "gate_TEST_KEY_1.0_20260803T120000_baseline" in rows
    candidate = rows["gate_TEST_KEY_1.0_20260803T120000"]
    baseline = rows["gate_TEST_KEY_1.0_20260803T120000_baseline"]
    assert candidate["baseline_experiment_id"] == baseline["id"]
    assert baseline["baseline_experiment_id"] is None
    assert candidate["status"] == "PROPOSED"  # gate evidence != a written decision


def test_ingest_inserts_three_metrics_rows_per_arm(temp_research_db, tmp_path):
    from scripts.research_db_ingest import ingest
    from db.research_repo import get_research_connection

    json_path = _write_payload(str(tmp_path), _sample_payload())
    ingest(json_path, author_role="claude", title="Test experiment",
           docs_nn_path=None, strategy_family=None)

    conn = get_research_connection()
    counts = dict(conn.execute(
        "SELECT experiment_id, COUNT(*) FROM performance_metrics GROUP BY experiment_id"
    ).fetchall())
    conn.close()
    assert sorted(counts.values()) == [3, 3]


def test_ingest_leaves_effective_n_and_p_value_null(temp_research_db, tmp_path):
    from scripts.research_db_ingest import ingest
    from db.research_repo import get_research_connection

    json_path = _write_payload(str(tmp_path), _sample_payload())
    ingest(json_path, author_role="claude", title="Test experiment",
           docs_nn_path=None, strategy_family=None)

    conn = get_research_connection()
    rows = conn.execute("SELECT effective_n, p_value FROM performance_metrics").fetchall()
    conn.close()
    assert all(r["effective_n"] is None and r["p_value"] is None for r in rows)


def test_ingest_is_idempotent_on_duplicate_slug(temp_research_db, tmp_path, capsys):
    from scripts.research_db_ingest import ingest
    from db.research_repo import get_research_connection

    json_path = _write_payload(str(tmp_path), _sample_payload())
    ingest(json_path, author_role="claude", title="Test experiment",
           docs_nn_path=None, strategy_family=None)
    ingest(json_path, author_role="claude", title="Test experiment",
           docs_nn_path=None, strategy_family=None)

    conn = get_research_connection()
    n = conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
    conn.close()
    assert n == 2  # not 4 -- second ingest was a no-op
    assert "Skipping" in capsys.readouterr().out


def test_dry_run_writes_nothing(temp_research_db, tmp_path):
    from scripts.research_db_ingest import ingest
    from db.research_repo import get_research_connection

    json_path = _write_payload(str(tmp_path), _sample_payload())
    ingest(json_path, author_role="claude", title="Test experiment",
           docs_nn_path=None, strategy_family=None, dry_run=True)

    conn = get_research_connection()
    n = conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
    conn.close()
    assert n == 0


def test_unmapped_param_key_skips_parameter_delta_and_warns(temp_research_db, tmp_path, capsys):
    from scripts.research_db_ingest import ingest
    from db.research_repo import get_research_connection

    json_path = _write_payload(str(tmp_path), _sample_payload(
        overrides={"SOME_UNMAPPED_KEY_XYZ": "1.0"}))
    ingest(json_path, author_role="claude", title="Test experiment",
           docs_nn_path=None, strategy_family=None)

    conn = get_research_connection()
    n = conn.execute("SELECT COUNT(*) FROM parameter_deltas").fetchone()[0]
    conn.close()
    assert n == 0
    assert "WARNING" in capsys.readouterr().out


def test_mapped_param_key_inserts_delta_with_coded_baseline_value(temp_research_db, tmp_path):
    from scripts.research_db_ingest import ingest
    from db.research_repo import init_research_db, get_research_connection

    init_research_db()
    conn = get_research_connection()
    conn.execute(
        "INSERT INTO param_taxonomy (param_key, attribution_dimension, alpha_source) "
        "VALUES ('SECTOR_RS_WEIGHT', 'sector', 'sector')"
    )
    conn.commit()
    conn.close()

    json_path = _write_payload(str(tmp_path), _sample_payload(
        overrides={"SECTOR_RS_WEIGHT": "1.0"}))
    ingest(json_path, author_role="claude", title="Test experiment",
           docs_nn_path=None, strategy_family=None)

    conn = get_research_connection()
    row = dict(conn.execute(
        "SELECT * FROM parameter_deltas WHERE param_key = 'SECTOR_RS_WEIGHT'"
    ).fetchone())
    conn.close()
    assert row["candidate_value"] == "1.0"
    assert row["attribution_dimension"] == "sector"
    assert row["baseline_value"] == "0.0"  # SECTOR_RS_WEIGHT's coded os.getenv default


def test_evidence_ledger_auto_flags(temp_research_db, tmp_path):
    from scripts.research_db_ingest import ingest
    from db.research_repo import get_research_connection

    json_path = _write_payload(str(tmp_path), _sample_payload())
    ingest(json_path, author_role="claude", title="Test experiment",
           docs_nn_path=None, strategy_family=None)

    conn = get_research_connection()
    row = dict(conn.execute("SELECT * FROM evidence_ledger").fetchone())
    conn.close()
    assert row["train_and_test_reported"] == 1
    assert row["stress_tested"] == 0  # sample payload has no stress_* metric rows
    assert row["effective_n_checked"] == 0
    assert row["has_economic_reasoning"] == 0  # never auto-set


def test_strategy_family_created_and_reused(temp_research_db, tmp_path):
    from scripts.research_db_ingest import ingest
    from db.research_repo import get_research_connection

    json_path = _write_payload(str(tmp_path), _sample_payload())
    ingest(json_path, author_role="claude", title="Test experiment",
           docs_nn_path=None, strategy_family="PURE_RS")

    conn = get_research_connection()
    families = conn.execute("SELECT name, is_control_arm FROM strategy_family").fetchall()
    conn.close()
    assert len(families) == 1
    assert families[0]["name"] == "PURE_RS"
    assert families[0]["is_control_arm"] == 0


def test_control_arm_family_flagged(temp_research_db, tmp_path):
    from scripts.research_db_ingest import ingest
    from db.research_repo import get_research_connection

    json_path = _write_payload(str(tmp_path), _sample_payload())
    ingest(json_path, author_role="claude", title="Test experiment",
           docs_nn_path=None, strategy_family="REVERSE_RS")

    conn = get_research_connection()
    row = conn.execute("SELECT is_control_arm FROM strategy_family WHERE name = 'REVERSE_RS'").fetchone()
    conn.close()
    assert row["is_control_arm"] == 1


def test_gate_json_emission_contract(tmp_path, monkeypatch):
    """scripts.robustness_gate.write_research_json's output shape, in isolation
    from the expensive subprocess pipeline -- the interface both the gate and
    the ingest script depend on."""
    import scripts.robustness_gate as g
    monkeypatch.chdir(tmp_path)

    baseline_oos = {
        "train": {"start": "2022-01-01", "end": "2024-12-31", "cagr": 1.0, "sharpe": 0.1,
                   "mdd": 10.0, "wr": 50.0, "pf": 1.1, "n": 100, "pass": True},
        "test": {"start": "2025-01-01", "end": "2026-06-04", "cagr": 41.64, "sharpe": 1.58,
                  "mdd": 8.0, "wr": 55.0, "pf": 1.9, "n": 133, "pass": True},
        "full": {"start": "2022-01-01", "end": "2026-06-04", "cagr": -4.64, "sharpe": -0.13,
                  "mdd": 20.0, "wr": 45.0, "pf": 0.9, "n": 233, "pass": False},
    }
    candidate_oos = {k: dict(v) for k, v in baseline_oos.items()}
    stress_rows = {
        "chop": {
            "baseline": {"returncode": 0, "cagr": -1.0, "sharpe": -0.1, "mdd": 12.0, "wr": 40.0, "pf": 0.8},
            "candidate": {"returncode": 1, "error": "boom"},
            "window_start": "2024-06-01", "window_end": "2024-09-01",
        },
    }
    g.write_research_json({"FOO": "1"}, 42, 999, baseline_oos, candidate_oos, stress_rows, [])

    files = list((tmp_path / "research_runs").glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text())
    assert payload["verdict"] == "PASS"
    assert set(payload["arms"].keys()) == {"baseline", "candidate"}
    sources = {m["source"] for m in payload["arms"]["baseline"]["metrics"]}
    assert sources == {"train", "test", "full", "stress_chop"}
    stress_candidate = next(m for m in payload["arms"]["candidate"]["metrics"]
                             if m["source"] == "stress_chop")
    assert stress_candidate["error"] == "boom"
    assert stress_candidate["cagr"] is None
