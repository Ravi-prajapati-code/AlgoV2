#!/usr/bin/env python3
"""Robustness Gate (Phase 5 of the research roadmap).

Codifies, as ONE command, the three-part validation sequence that was
manually re-run by hand this session for both the extension-filter and
staged-entry candidates: full-window backtest -> out-of-sample train/test
split -> all 4 synthetic stress scenarios. Both those candidates looked good
on some subset of this sequence and were rejected only because a LATER stage
caught a problem a clean earlier stage missed (staged-entry: exposure
collapse; extension-filter: clean full-window win, PF>1 on out-of-sample, but
failed 2 of 4 stress scenarios). This script exists so no future candidate
can be judged on a partial run of that sequence.

Runs BASELINE (no overrides) and CANDIDATE (given env-var overrides) through
all three checks against the SAME data each time, prints them side by side,
and applies fixed, inspectable pass/fail rules -- automating the mechanical
comparison this session did by hand, not the final judgment call. A PASS
here is necessary, not sufficient, for a live-deployment conversation.

Reuses scripts/out_of_sample_validator.run_window and
scripts/stress_test_scenarios.run_backtest verbatim (both shell out to
`main.py backtest` as a subprocess with the parent's env, which is how the
--env overrides reach the engine via the existing settings.py env-var
pattern, e.g. EXTENSION_FILTER_EMA100_MAX_PCT). Zero engine.py changes, zero
live DB writes -- stress scenarios run against a scratch DB exactly as in
stress_test_scenarios.py, and UPSTOX_ACCESS_TOKEN is stripped before every
subprocess call.

Usage:
    python3 scripts/robustness_gate.py --env EXTENSION_FILTER_EMA100_MAX_PCT=17.8
    python3 scripts/robustness_gate.py --env FOO=1 --env BAR=2 --seed 7
"""
import argparse
import os
import sys
from datetime import date, timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import MARKET_INDEX_SYMBOL
from data.universe import get_all_symbols
from scripts.out_of_sample_validator import (
    DEFAULT_HIST_START, DEFAULT_TRAIN_END, DEFAULT_TEST_START,
    DIVERGENCE_FLAG_PCTPOINTS, run_window, fmt,
)
from scripts.stress_test_scenarios import (
    SCENARIOS, load_real_history, populate_scratch_db, run_backtest,
)

SCRATCH_DIR = "outputs/robustness_gate_scratch"

# Fixed, inspectable gate rules -- same bright lines that caught the two
# rejections this session (extension-filter's sideways-chop PF<1 flip).
OOS_TEST_TOLERANCE = {"sharpe": 0.10, "pf": 0.10}  # candidate TEST-window may not be worse than baseline by more than this (absolute)
STRESS_PF_DROP_MAX = 0.10                          # candidate PF may not fall more than this below baseline PF in any scenario


def apply_env(overrides: dict):
    for k, v in overrides.items():
        os.environ[k] = v


def clear_env(overrides: dict):
    for k in overrides:
        os.environ.pop(k, None)


def run_full_and_oos_arm(overrides: dict, active: bool) -> dict:
    clear_env(overrides)
    if active:
        apply_env(overrides)
    train = run_window(DEFAULT_HIST_START, DEFAULT_TRAIN_END)
    test = run_window(DEFAULT_TEST_START, str(date.today()))
    full = run_window(DEFAULT_HIST_START, str(date.today()))
    clear_env(overrides)
    return {"train": train, "test": test, "full": full}


def run_stress_both_arms(overrides: dict, history: dict, seed: int) -> dict:
    os.makedirs(SCRATCH_DIR, exist_ok=True)
    rows = {}
    for name, scenario in SCENARIOS.items():
        rng = np.random.default_rng(seed)
        scratch_path = os.path.join(SCRATCH_DIR, f"{name}.db")
        tail_start, tail_end = populate_scratch_db(scratch_path, history, scenario, rng)
        warmup_start = tail_start - timedelta(days=90)

        clear_env(overrides)
        baseline = run_backtest(scratch_path, str(warmup_start), str(tail_end))
        apply_env(overrides)
        candidate = run_backtest(scratch_path, str(warmup_start), str(tail_end))
        clear_env(overrides)

        rows[name] = {"baseline": baseline, "candidate": candidate}
    return rows


def print_oos_section(baseline_oos: dict, candidate_oos: dict) -> list:
    failures = []
    print("\n=== 1+2. Full-window & Out-of-Sample (BASELINE vs CANDIDATE) ===")
    for window in ("train", "test", "full"):
        print(f"  [{window.upper()}]")
        print(f"    baseline : {fmt(baseline_oos[window])}")
        print(f"    candidate: {fmt(candidate_oos[window])}")

    print("\n  Per-arm train/test consistency check (informational — a pre-existing baseline")
    print("  instability is not itself a reason to reject a candidate; only NEW instability")
    print("  introduced by the candidate is treated as a gate failure below):")
    divergence = {}
    for label, oos in (("baseline", baseline_oos), ("candidate", candidate_oos)):
        divergent = [m for m, thr in DIVERGENCE_FLAG_PCTPOINTS.items()
                     if abs(oos["train"][m] - oos["test"][m]) > thr]
        divergence[label] = set(divergent)
        if divergent:
            print(f"    {label:<10} UNSTABLE — {', '.join(divergent)} diverge materially train vs test")
        else:
            print(f"    {label:<10} stable")
    new_instability = divergence["candidate"] - divergence["baseline"]
    if new_instability:
        failures.append(f"candidate introduces NEW train/test instability not present in baseline ({', '.join(new_instability)})")

    print("\n  Candidate vs baseline, TEST window only:")
    for metric, tol in OOS_TEST_TOLERANCE.items():
        b, c = baseline_oos["test"][metric], candidate_oos["test"][metric]
        delta = c - b
        flag = ""
        if delta < -tol:
            flag = "  <-- FAIL (candidate worse than tolerance)"
            failures.append(f"candidate TEST-window {metric.upper()} worse than baseline by {abs(delta):.2f} (tolerance {tol})")
        print(f"    {metric.upper():<8} baseline={b:.2f}  candidate={c:.2f}  delta={delta:+.2f}{flag}")

    return failures


def print_stress_section(stress_rows: dict) -> list:
    failures = []
    print(f"\n=== 3. Stress Scenarios (BASELINE vs CANDIDATE, n={len(stress_rows)}) ===")
    print(f"  {'Scenario':<26}{'CAGR base':>11}{'CAGR cand':>11}{'PF base':>9}{'PF cand':>9}")
    for name, arms in stress_rows.items():
        b, c = arms["baseline"], arms["candidate"]
        if "error" in b or "error" in c:
            print(f"  {name:<26} FAILED TO RUN — see raw output")
            failures.append(f"{name}: baseline or candidate backtest failed to run")
            continue
        flag = ""
        if b["cagr"] >= 0 and c["cagr"] < 0:
            flag = "  <-- FAIL (CAGR flips negative)"
            failures.append(f"{name}: candidate CAGR flips negative ({c['cagr']:.2f}%) vs baseline ({b['cagr']:.2f}%)")
        if b["pf"] - c["pf"] > STRESS_PF_DROP_MAX:
            flag += "  <-- FAIL (PF drop)"
            failures.append(f"{name}: candidate PF {c['pf']:.2f} drops more than {STRESS_PF_DROP_MAX} below baseline PF {b['pf']:.2f}")
        print(f"  {name:<26}{b['cagr']:>10.2f}%{c['cagr']:>10.2f}%{b['pf']:>9.2f}{c['pf']:>9.2f}{flag}")
    return failures


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", action="append", default=[], metavar="KEY=VALUE",
                     help="candidate env-var override, repeatable (e.g. --env EXTENSION_FILTER_EMA100_MAX_PCT=17.8)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if not args.env:
        print("No --env overrides given — nothing distinguishes candidate from baseline. Exiting.")
        sys.exit(1)

    overrides = dict(kv.split("=", 1) for kv in args.env)
    print(f"[robustness_gate] candidate overrides: {overrides}")

    baseline_oos = run_full_and_oos_arm(overrides, active=False)
    candidate_oos = run_full_and_oos_arm(overrides, active=True)
    oos_failures = print_oos_section(baseline_oos, candidate_oos)

    symbols = get_all_symbols() + [MARKET_INDEX_SYMBOL]
    history = load_real_history(symbols)
    stress_rows = run_stress_both_arms(overrides, history, args.seed)
    stress_failures = print_stress_section(stress_rows)

    all_failures = oos_failures + stress_failures
    print("\n=== VERDICT ===")
    if all_failures:
        print(f"REJECT — {len(all_failures)} gate failure(s):")
        for f in all_failures:
            print(f"  - {f}")
    else:
        print("PASS — clears full-window, out-of-sample, and all stress-scenario gates.")
        print("This is necessary, not sufficient: still use judgment before any live deployment.")


if __name__ == "__main__":
    main()
