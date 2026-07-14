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
import re
import subprocess
import sys
from datetime import timedelta

import numpy as np
from dotenv import dotenv_values

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
DOTENV_PATH = os.path.join(REPO_ROOT, ".env")

from config.settings import MARKET_INDEX_SYMBOL
from data.universe import get_all_symbols
from scripts.out_of_sample_validator import (
    DEFAULT_HIST_START, DEFAULT_TRAIN_END, DEFAULT_TEST_START, DEFAULT_GATE_END,
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


_ENV_DEFAULT_RE = re.compile(r'os\.getenv\(\s*["\']([A-Z_][A-Z_0-9]*)["\']\s*,\s*["\']([^"\']*)["\']')
# Credentials are never "strategy config drift" -- exclude by pattern so a
# secret value can never be read into `problems` (and printed) in the first
# place, regardless of which files get scanned above.
_CREDENTIAL_KEY_RE = re.compile(r'(TOKEN|SECRET|KEY|PASSWORD|PIN|TOTP|MOBILE|CHAT_ID)', re.IGNORECASE)


def _coded_env_defaults(filepaths: list) -> dict:
    """KEY -> literal default string from every `os.getenv("KEY", "default")`
    call site in the given files (first occurrence wins)."""
    defaults = {}
    for path in filepaths:
        with open(path) as f:
            for key, val in _ENV_DEFAULT_RE.findall(f.read()):
                defaults.setdefault(key, val)
    return defaults


def check_config_drift(active_overrides: dict) -> list:
    """Rule 1 item 4 (docs/29_Project_Governance.md): a gate run must not
    silently run against an unreviewed config state. Precedent: a stray
    uncommitted `max_open_positions=5` key in risk_config.yaml
    contaminated a full day of gate verdicts on 2026-07-11 before being
    caught by hand.

    Two checks:
    1. `config/` must be git-clean -- an uncommitted edit there is an
       unreviewed change riding along with the gate run.
    2. No strategy-relevant env var (as sourced from the current process
       env or `.env`, which is what `main.py` actually sees via
       `load_dotenv(override=True)`) may silently differ from its coded
       default unless it's part of the explicit `--env` candidate list --
       otherwise the "baseline" arm is quietly non-default.
    """
    problems = []

    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", "config/"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    ).stdout.strip()
    if dirty:
        problems.append(
            "Uncommitted changes under config/ -- gate run would use an "
            "unreviewed config state:\n" +
            "\n".join(f"    {line}" for line in dirty.splitlines())
        )

    defaults = _coded_env_defaults([
        os.path.join(REPO_ROOT, "config", "settings.py"),
        os.path.join(REPO_ROOT, "strategy", "defensive_portfolio.py"),
    ])
    dotenv_path = DOTENV_PATH
    dotenv_vals = dotenv_values(dotenv_path) if os.path.exists(dotenv_path) else {}
    effective = {**os.environ, **{k: v for k, v in dotenv_vals.items() if v is not None}}

    for key, default in defaults.items():
        if key in active_overrides or _CREDENTIAL_KEY_RE.search(key):
            continue
        current = effective.get(key)
        if current is not None and current != default:
            problems.append(
                f"{key}={current!r} differs from coded default {default!r} "
                "and isn't part of --env overrides -- the 'baseline' arm "
                "would silently run non-default."
            )
    return problems


def apply_env(overrides: dict):
    for k, v in overrides.items():
        os.environ[k] = v


def clear_env(overrides: dict):
    for k in overrides:
        os.environ.pop(k, None)


def hide_dotenv():
    """
    Rename .env aside for the duration of the arm runs. clear_env() only pops
    keys from THIS process's os.environ -- but each subprocess's own
    main.py:24 does load_dotenv(override=True), which re-reads .env straight
    off disk and stamps its values back in regardless of what clear_env()
    cleared or what env= was passed to subprocess.run(). Any key currently
    non-default in .env (e.g. ENTRY_EMA_MEDIUM/EXIT_TREND_EMA once they got
    written there during deploy) can then never be cleared for the baseline
    arm -- baseline silently runs candidate config too, producing a false
    PASS (byte-identical baseline/candidate). Caught 2026-07-14 re-gating the
    atr/rsi fix; see docs/33. Safe: every OTHER .env value (DB path, creds)
    is unaffected, since this process already loaded them via
    config.settings' own load_dotenv() at import time, and that's what
    flows into each subprocess's env= dict via {**os.environ}.
    """
    if os.path.exists(DOTENV_PATH):
        os.rename(DOTENV_PATH, DOTENV_PATH + ".gate_bak")


def restore_dotenv():
    bak = DOTENV_PATH + ".gate_bak"
    if os.path.exists(bak):
        os.rename(bak, DOTENV_PATH)


def run_full_and_oos_arm(overrides: dict, active: bool) -> dict:
    clear_env(overrides)
    if active:
        apply_env(overrides)
    train = run_window(DEFAULT_HIST_START, DEFAULT_TRAIN_END)
    test = run_window(DEFAULT_TEST_START, DEFAULT_GATE_END)
    full = run_window(DEFAULT_HIST_START, DEFAULT_GATE_END)
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

    drift = check_config_drift(overrides)
    if drift:
        print("\n=== CONFIG DRIFT DETECTED (Rule 1 item 4, docs/29) ===")
        for d in drift:
            print(f"  - {d}")
        print("\nAborting -- commit or revert config/, and clear any stray "
              "env var, before this gate result can be trusted.")
        sys.exit(1)

    # .env hidden from disk for the arm runs so each subprocess's own
    # load_dotenv(override=True) can't re-populate keys clear_env() just
    # cleared (see hide_dotenv() docstring / docs/33). Restored even on
    # crash so a failed gate run never leaves .env missing.
    hide_dotenv()
    try:
        baseline_oos = run_full_and_oos_arm(overrides, active=False)
        candidate_oos = run_full_and_oos_arm(overrides, active=True)
        oos_failures = print_oos_section(baseline_oos, candidate_oos)

        symbols = get_all_symbols() + [MARKET_INDEX_SYMBOL]
        history = load_real_history(symbols)
        stress_rows = run_stress_both_arms(overrides, history, args.seed)
        stress_failures = print_stress_section(stress_rows)
    finally:
        restore_dotenv()

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
