#!/usr/bin/env python3
"""One-time manual backfill of docs/35-47 as `experiments` rows (docs/48 §9).

These four docs predate scripts/robustness_gate.py's JSON emission (added
2026-08-03), so there is no research_runs/*.json to ingest from -- the
payloads below are transcribed directly from each doc's reported numbers
and fed to scripts.research_db_ingest.ingest_payload().

Only docs with a real baseline-vs-candidate CAGR/Sharpe/stress result are
included. Explicitly excluded, and why:
  - docs/01-34: predate the 2026-07-22 charges-model fix and other fidelity
    corrections -- backfilling them would violate docs/47 §8.1 (no stale-
    labeled results presented as current).
  - docs/37, 39, 41-47: narrative retrospectives, planning docs, architecture
    maps, registries, governance/constitution docs -- not experiments, no
    baseline/candidate metrics to store.
  - docs/40: real result, but a forward-return cohort study (per-symbol-day
    mean/win-rate by gate-pass, not a baseline-vs-candidate CAGR/Sharpe/stress
    run) -- doesn't fit performance_metrics' shape. Left for a future Phase 2
    table if this kind of study becomes common (docs/48 doesn't define one
    today); not forced into this schema.

Run:
    python3 scripts/backfill_historical_experiments.py [--dry-run]
"""
import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from scripts.research_db_ingest import ingest_payload

GENERATED_AT = "2026-08-03T00:00:00"
TRAIN_WINDOW = ("2022-01-01", "2024-12-31")
TEST_WINDOW = ("2025-01-01", "2026-06-04")
FULL_WINDOW = ("2022-01-01", "2026-06-04")


def _row(source, cagr, sharpe, mdd=None, n=None, wr=None, window=None):
    row = {"source": source, "cagr": cagr, "sharpe": sharpe,
           "max_drawdown_pct": mdd, "total_trades": n, "win_rate": wr}
    if window:
        row["window_start"], row["window_end"] = window
    return row


# -- docs/35: Long-Term Sleeve Ablation REJECT (2026-07-27) -----------------
# Standalone validator (scripts/backtest_long_term_sleeve.py), not
# robustness_gate.py -- single-arm result (no baseline), so "baseline" here
# is populated with the same numbers as a structural placeholder marked
# not-a-real-baseline in evidence notes. Correction 2026-08-03 in docs/35
# itself: the validator was never committed and no longer exists on disk --
# independently_rederived is explicitly 0.
DOC_35 = {
    "slug": "docs35_long_term_sleeve_ablation_20260727",
    "generated_at": GENERATED_AT,
    "seed": None,
    "runtime_ms": None,
    "peak_mem_mb": None,
    "overrides": {"LONG_TERM_REBALANCE_ENABLED": "true"},
    "verdict": "REJECT",
    "failures": ["TRAIN->TEST sign flip: CAGR +54.09% -> -2.85%, Sharpe 7.11 -> -0.13"],
    "commit_hash": None,
    "branch": None,
    "arms": {
        # single-arm study; candidate is the only real result, baseline arm
        # intentionally omitted from performance_metrics (no control run existed)
        "baseline": {"metrics": []},
        "candidate": {"metrics": [
            _row("train", 54.09, 7.11, mdd=16.31, n=5, wr=40.0, window=TRAIN_WINDOW),
            _row("test", -2.85, -0.13, mdd=26.70, n=5, wr=40.0, window=TEST_WINDOW),
            _row("full", 31.56, 5.19, mdd=29.84, n=10, wr=50.0, window=FULL_WINDOW),
        ]},
    },
}

# -- docs/36: Universe Cap-50 REJECT (2026-07-28) ----------------------------
DOC_36 = {
    "slug": "docs36_universe_cap_50_20260728",
    "generated_at": GENERATED_AT,
    "seed": None,
    "runtime_ms": None,
    "peak_mem_mb": None,
    "overrides": {"UNIVERSE_CAP_SIZE": "50"},
    "verdict": "REJECT",
    "failures": ["8 gate failures: TEST window + all 4 stress scenarios"],
    "commit_hash": "2c37fd1",
    "branch": "main",
    "arms": {
        "baseline": {"metrics": [
            _row("test", 41.64, 1.58, n=133, window=TEST_WINDOW),
            _row("stress_crash_v_recovery", 9.29, None),
            _row("stress_extended_bear_grind", -7.66, None),
            _row("stress_prolonged_sideways_chop", 6.88, None),
            _row("stress_gap_down_bleed", -11.01, None),
        ]},
        "candidate": {"metrics": [
            _row("test", -15.91, -1.18, n=98, window=TEST_WINDOW),
            _row("stress_crash_v_recovery", -8.59, None),
            _row("stress_extended_bear_grind", -42.65, None),
            _row("stress_prolonged_sideways_chop", -14.33, None),
            _row("stress_gap_down_bleed", -49.10, None),
        ]},
    },
}

# -- docs/38 Addendum 3: FULL vs PURE_RS revalidation (2026-07-30) ----------
# Baseline=PURE_RS (live default), candidate=FULL (old strict-AND gate) --
# genuinely different strategy_family, not a parameter delta within one.
DOC_38_ADDENDUM3 = {
    "slug": "docs38_addendum3_full_vs_pure_rs_20260730",
    "generated_at": GENERATED_AT,
    "seed": None,
    "runtime_ms": None,
    "peak_mem_mb": None,
    "overrides": {"ENTRY_MODE": "FULL"},
    "verdict": "REJECT",
    "failures": ["TEST-window Sharpe/PF both far worse than baseline"],
    "commit_hash": "b70335f",
    "branch": "main",
    "arms": {
        "baseline": {"metrics": [
            _row("train", -1.10, 0.08, window=TRAIN_WINDOW),
            _row("test", 41.64, 1.58, window=TEST_WINDOW),
            _row("full", -4.64, -0.13, window=FULL_WINDOW),
            _row("stress_crash_v_recovery", 9.29, None),
            _row("stress_prolonged_sideways_chop", 6.88, None),
        ]},
        "candidate": {"metrics": [
            _row("train", -6.08, -0.30, window=TRAIN_WINDOW),
            _row("test", -12.03, -0.56, window=TEST_WINDOW),
            _row("full", -9.67, -0.54, window=FULL_WINDOW),
            _row("stress_crash_v_recovery", 10.62, None),
            _row("stress_prolonged_sideways_chop", 22.02, None),
        ]},
    },
}

# -- docs/50: SECTOR_RS_WEIGHT REJECT (2026-07-31) ---------------------------
DOC_50 = {
    "slug": "docs50_sector_rs_weight_20260731",
    "generated_at": GENERATED_AT,
    "seed": None,
    "runtime_ms": None,
    "peak_mem_mb": None,
    "overrides": {"SECTOR_RS_WEIGHT": "1.0"},
    "verdict": "REJECT",
    "failures": ["TEST-window Sharpe delta -2.33, PF -1.43 vs tolerance"],
    "commit_hash": "f2238b8",
    "branch": "main",
    "arms": {
        "baseline": {"metrics": [
            _row("train", -1.10, 0.08, window=TRAIN_WINDOW),
            _row("test", 41.64, 1.58, n=133, window=TEST_WINDOW),
            _row("full", -4.64, -0.13, window=FULL_WINDOW),
        ]},
        "candidate": {"metrics": [
            _row("train", 10.47, 0.54, window=TRAIN_WINDOW),
            _row("test", -11.95, -0.75, n=74, window=TEST_WINDOW),
            _row("full", 0.51, 0.14, window=FULL_WINDOW),
        ]},
    },
}


BACKFILL_ITEMS = [
    dict(payload=DOC_35, source_label="docs/35 (transcribed, no research_runs JSON)",
         author_role="claude", title="Long-term sleeve ablation (RS-rank>=85 buy-hold)",
         docs_nn_path="docs/35_Long_Term_Sleeve_Ablation_Reject_20260727.md",
         strategy_family="LONG_TERM_SLEEVE", baseline_strategy_family=None,
         evidence_notes=(
             "Backfilled from docs/35 (scripts/research_db_ingest.py did not exist when "
             "this experiment ran). Single-arm validator study (scripts/backtest_long_term_sleeve.py); "
             "baseline arm has no metrics (no control run). "
             "independently_rederived=0: docs/35 Correction 2026-08-03 found the validator script "
             "was never committed to git and no longer exists on disk -- this result cannot "
             "currently be reproduced from a clean clone.")),
    dict(payload=DOC_36, source_label="docs/36 (transcribed, no research_runs JSON)",
         author_role="claude", title="Universe cap top-50-by-turnover",
         docs_nn_path="docs/36_Universe_Cap_50_Reject_20260728.md",
         strategy_family="PURE_RS", baseline_strategy_family=None,
         evidence_notes=(
             "Backfilled from docs/36. UNIVERSE_CAP_SIZE param ablation within the live "
             "PURE_RS strategy family, run via robustness_gate.py (commit 2c37fd1) predating "
             "JSON emission. independently_rederived=0: not manually re-run for this backfill, "
             "but UNIVERSE_CAP_SIZE flag + gate script both still exist in the codebase.")),
    dict(payload=DOC_38_ADDENDUM3, source_label="docs/38 Addendum 3 (transcribed, no research_runs JSON)",
         author_role="claude", title="FULL vs PURE_RS re-validation, honest costs",
         docs_nn_path="docs/38_Final_Verdict_Blind_2022_Test_20260730.md",
         strategy_family="FULL", baseline_strategy_family="PURE_RS",
         evidence_notes=(
             "Backfilled from docs/38 Addendum 3. Two different strategy families "
             "(baseline=PURE_RS live default, candidate=FULL strict-AND gate), not a parameter "
             "delta -- parameter_deltas will show ENTRY_MODE PURE_RS->FULL. "
             "independently_rederived=0: not manually re-run for this backfill.")),
    dict(payload=DOC_50, source_label="docs/50 (transcribed, no research_runs JSON)",
         author_role="claude", title="SECTOR_RS_WEIGHT raw sector-momentum entry nudge",
         docs_nn_path="docs/50_Sector_RS_Weight_Reject_20260731.md",
         strategy_family="PURE_RS", baseline_strategy_family=None,
         evidence_notes=(
             "Backfilled from docs/50. SECTOR_RS_WEIGHT param ablation within the live PURE_RS "
             "strategy family, run via robustness_gate.py (commit f2238b8) predating JSON "
             "emission. independently_rederived=0: not manually re-run for this backfill, but "
             "SECTOR_RS_WEIGHT flag + gate script both still exist in the codebase.")),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                     help="parse+validate, print what would be inserted, roll back")
    args = ap.parse_args()

    for item in BACKFILL_ITEMS:
        ingest_payload(
            item["payload"], item["source_label"], item["author_role"], item["title"],
            item["docs_nn_path"], item["strategy_family"], dry_run=args.dry_run,
            baseline_strategy_family=item["baseline_strategy_family"],
            evidence_notes=item["evidence_notes"],
        )


if __name__ == "__main__":
    main()
