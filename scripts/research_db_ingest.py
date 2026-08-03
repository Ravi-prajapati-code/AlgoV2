#!/usr/bin/env python3
"""Research DB ingest (docs/48 §8.2) — reads a research_runs/<slug>.json file
(written by scripts/robustness_gate.py) and inserts Phase 1 rows into
db/research.db: experiments (baseline + candidate), parameter_deltas,
performance_metrics, evidence_ledger.

Run manually after a gate run you intend to keep — not a post-gate hook, so
a bad run doesn't self-insert without the researcher noticing.

`title` and `author_role` must be given explicitly: no silent default, since
`experiments.author_role` records who is accountable for the run per the
charter's two-role process.

Usage:
    python3 scripts/research_db_ingest.py research_runs/gate_..._....json \
        --author-role claude --title "SECTOR_RS_WEIGHT gate rerun" \
        --docs-path docs/50_Sector_RS_Weight_Reject_20260731.md \
        --strategy-family FULL
"""
import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from db.research_repo import get_research_connection, init_research_db
from scripts.robustness_gate import coded_env_defaults

VALID_AUTHOR_ROLES = ("claude", "codex", "joint")
CONTROL_ARM_FAMILIES = {"RANDOM_ALL", "RANDOM_ELIGIBLE", "REVERSE_RS", "SHUFFLE_RS"}


def _get_or_create_strategy_family(conn, name):
    if not name:
        return None
    row = conn.execute("SELECT id FROM strategy_family WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO strategy_family (name, is_control_arm) VALUES (?, ?)",
        (name, 1 if name in CONTROL_ARM_FAMILIES else 0),
    )
    return cur.lastrowid


def _insert_experiment(conn, slug, title, author_role, commit_hash, branch,
                        docs_nn_path, baseline_experiment_id, strategy_family_id,
                        runtime_ms, peak_mem_mb):
    cur = conn.execute(
        """INSERT INTO experiments
           (slug, title, author_role, commit_hash, branch, docs_nn_path,
            baseline_experiment_id, strategy_family_id, runtime_ms, peak_mem_mb)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (slug, title, author_role, commit_hash, branch, docs_nn_path,
         baseline_experiment_id, strategy_family_id, runtime_ms, peak_mem_mb),
    )
    return cur.lastrowid
    # status left at schema default 'PROPOSED' — a gate run is evidence, not a
    # verdict; research_decisions (fully manual, docs/48 §4.5) is what moves
    # an experiment to DECIDED, and this script never writes that table.


def _insert_metrics(conn, experiment_id, metrics_rows):
    for m in metrics_rows:
        conn.execute(
            """INSERT INTO performance_metrics
               (experiment_id, source, cagr, sharpe, max_drawdown_pct, total_trades,
                win_rate, window_start, window_end)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (experiment_id, m["source"], m.get("cagr"), m.get("sharpe"),
             m.get("max_drawdown_pct"), m.get("total_trades"), m.get("win_rate"),
             m.get("window_start"), m.get("window_end")),
        )
        # effective_n / p_value intentionally left NULL — not computed by the
        # gate today (docs/47 §3.2 flags NULL as "not checked", not "zero").


def _insert_parameter_deltas(conn, experiment_id, overrides):
    """baseline_value is the coded os.getenv(...) default for each overridden
    key, re-derived at ingest time from the current config/settings.py +
    strategy/defensive_portfolio.py (same source robustness_gate.py's own
    config-drift check reads) — not stored in the JSON, since it's a property
    of the codebase at ingest time, not of the run."""
    defaults = coded_env_defaults([
        os.path.join(REPO_ROOT, "config", "settings.py"),
        os.path.join(REPO_ROOT, "strategy", "defensive_portfolio.py"),
    ])
    unmapped = []
    for key, candidate_value in overrides.items():
        row = conn.execute(
            "SELECT attribution_dimension FROM param_taxonomy WHERE param_key = ?", (key,)
        ).fetchone()
        if row is None:
            unmapped.append(key)
            continue
        conn.execute(
            """INSERT INTO parameter_deltas
               (experiment_id, param_key, baseline_value, candidate_value, attribution_dimension)
               VALUES (?, ?, ?, ?, ?)""",
            (experiment_id, key, defaults.get(key), candidate_value, row["attribution_dimension"]),
        )
    return unmapped


def _auto_evidence_flags(metrics_rows):
    """Only what a script can check from presence of rows — has_economic_reasoning,
    config_parity_confirmed, backtest_live_parity_confirmed, independently_rederived
    require judgment and are left 0 (docs/48 §4.4 update rule)."""
    sources = {m["source"] for m in metrics_rows}
    return {
        "train_and_test_reported": 1 if {"train", "test"} <= sources else 0,
        "stress_tested": 1 if any(s.startswith("stress_") for s in sources) else 0,
        "effective_n_checked": 0,
    }


def ingest(path, author_role, title, docs_nn_path, strategy_family, dry_run=False):
    with open(path) as f:
        payload = json.load(f)

    init_research_db()
    conn = get_research_connection()
    try:
        candidate_slug = payload["slug"]
        baseline_slug = candidate_slug + "_baseline"
        if conn.execute("SELECT id FROM experiments WHERE slug IN (?, ?)",
                         (candidate_slug, baseline_slug)).fetchone():
            print(f"Already ingested: {candidate_slug} (or its baseline) exists in research.db. Skipping.")
            return

        strategy_family_id = _get_or_create_strategy_family(conn, strategy_family)
        commit_hash, branch = payload.get("commit_hash"), payload.get("branch")
        runtime_ms, peak_mem_mb = payload.get("runtime_ms"), payload.get("peak_mem_mb")

        baseline_id = _insert_experiment(
            conn, baseline_slug, f"{title} (baseline arm)", author_role,
            commit_hash, branch, docs_nn_path, None, strategy_family_id,
            runtime_ms, peak_mem_mb,
        )
        _insert_metrics(conn, baseline_id, payload["arms"]["baseline"]["metrics"])

        candidate_id = _insert_experiment(
            conn, candidate_slug, title, author_role,
            commit_hash, branch, docs_nn_path, baseline_id, strategy_family_id,
            runtime_ms, peak_mem_mb,
        )
        _insert_metrics(conn, candidate_id, payload["arms"]["candidate"]["metrics"])
        unmapped = _insert_parameter_deltas(conn, candidate_id, payload.get("overrides", {}))

        flags = _auto_evidence_flags(payload["arms"]["candidate"]["metrics"])
        conn.execute(
            """INSERT INTO evidence_ledger
               (experiment_id, train_and_test_reported, stress_tested, effective_n_checked, notes)
               VALUES (?, ?, ?, ?, ?)""",
            (candidate_id, flags["train_and_test_reported"], flags["stress_tested"],
             flags["effective_n_checked"],
             f"Auto-inserted by research_db_ingest.py from {os.path.basename(path)}. "
             "has_economic_reasoning/config_parity_confirmed/backtest_live_parity_confirmed/"
             "independently_rederived NOT auto-set — require manual judgment (docs/48 §4.4)."),
        )

        if dry_run:
            conn.rollback()
            print(f"[dry-run] would ingest {candidate_slug} as experiment_id={candidate_id} "
                  f"(baseline experiment_id={baseline_id}); rolled back, nothing written.")
        else:
            conn.commit()
            print(f"Ingested {candidate_slug} -> experiment_id={candidate_id} "
                  f"(baseline experiment_id={baseline_id}), gate verdict={payload.get('verdict')}.")
        if unmapped:
            print(f"WARNING: {len(unmapped)} param_key(s) not in param_taxonomy — "
                  f"parameter_deltas skipped for them: {unmapped}. "
                  "Add a row to param_taxonomy and re-run ingest.")
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_path")
    ap.add_argument("--author-role", required=True, choices=VALID_AUTHOR_ROLES)
    ap.add_argument("--title", required=True)
    ap.add_argument("--docs-path", default=None,
                     help="e.g. docs/50_....md, if this run is already documented")
    ap.add_argument("--strategy-family", default=None,
                     help="ENTRY_MODE value this run's config resolves to, e.g. PURE_RS")
    ap.add_argument("--dry-run", action="store_true",
                     help="parse+validate, print what would be inserted, roll back")
    args = ap.parse_args()

    ingest(args.json_path, args.author_role, args.title, args.docs_path,
           args.strategy_family, args.dry_run)


if __name__ == "__main__":
    main()
