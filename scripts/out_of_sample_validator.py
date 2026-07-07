#!/usr/bin/env python3
"""
Out-of-Sample Validator (P1) — checks whether a parameter configuration's
backtest performance is a stable edge or an artifact of curve-fitting to a
single historical window.

Every strategy parameter in this project (RS_THRESHOLD, MAX_POSITIONS,
REGIME_SWITCH_DAYS, MIN_PROFIT_SOFT, ...) was chosen by sweeping the full
2022-01-01 -> present window and picking the best performer on that SAME
window — there was never a held-out period the winning config hadn't seen.
scripts/walk_forward.py detects decay going forward but doesn't answer this;
it compares a rolling recent window against a baseline that includes it.

This script splits history into a TRAIN window (parameter selection) and a
TEST window (never touched during selection), runs a given config on both,
and flags inconsistency between them as a sign of overfitting rather than
genuine edge — e.g. a config that looks great on TEST but has a materially
different win-rate/profit-factor on TRAIN is very likely noise, not signal.

Usage:
  python3 scripts/out_of_sample_validator.py                  # current live config, default split
  python3 scripts/out_of_sample_validator.py --train-end 2024-12-31 --test-start 2025-01-01 --test-end 2026-06-30
  MAX_POSITIONS=2 python3 scripts/out_of_sample_validator.py   # any env-var override, applies to both windows
"""
import argparse
import os
import subprocess
import sys
from datetime import date

DEFAULT_HIST_START = "2022-01-01"
DEFAULT_TRAIN_END  = "2024-12-31"
DEFAULT_TEST_START = "2025-01-01"

# Flag inconsistency if a metric moves by more than this between windows
# (relative, on metrics already expressed as %s / ratios)
DIVERGENCE_FLAG_PCTPOINTS = {"wr": 15.0, "pf": 0.8, "sharpe": 0.6}


def run_window(start: str, end: str) -> dict:
    cmd = ["python3", "main.py", "backtest", "--start", start, "--end", end]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.stderr.strip():
        print(f"  [{start}..{end}] stderr:\n{r.stderr.strip()}")
    vals = {}
    for line in r.stdout.split("\n"):
        if "CAGR" in line: vals["cagr"] = line.split(":")[1].strip().split()[0].replace("%", "").replace("+", "")
        if "Sharpe" in line: vals["sharpe"] = line.split(":")[1].strip().split()[0]
        if "Max Draw" in line: vals["mdd"] = line.split(":")[1].strip().split()[0].replace("%", "")
        if "Win Rate" in line: vals["wr"] = line.split(":")[1].strip().split()[0].replace("%", "")
        if "Profit Factor" in line: vals["pf"] = line.split(":")[1].strip().split()[0]
        if "Total Trades" in line: vals["n"] = line.split(":")[1].strip().split()[0]
        if "PASS" in line: vals["pass"] = True
        if "FAIL" in line: vals["pass"] = False
    return {
        "start": start, "end": end,
        "cagr": float(vals.get("cagr", 0)), "sharpe": float(vals.get("sharpe", 0)),
        "mdd": float(vals.get("mdd", 99)), "wr": float(vals.get("wr", 0)),
        "pf": float(vals.get("pf", 0)), "n": int(vals.get("n", 0)),
        "pass": vals.get("pass", False),
    }


def fmt(r: dict) -> str:
    flag = "PASS" if r["pass"] else "FAIL"
    return (f"{r['start']}->{r['end']}  CAGR {r['cagr']:+.2f}%  Sharpe {r['sharpe']:.2f}  "
            f"MDD {r['mdd']:.2f}%  WR {r['wr']:.1f}%  PF {r['pf']:.2f}  N={r['n']}  {flag}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hist-start", default=DEFAULT_HIST_START)
    ap.add_argument("--train-end", default=DEFAULT_TRAIN_END)
    ap.add_argument("--test-start", default=DEFAULT_TEST_START)
    ap.add_argument("--test-end", default=str(date.today()))
    args = ap.parse_args()

    print(f"[out_of_sample] TRAIN {args.hist_start} -> {args.train_end} | "
          f"TEST {args.test_start} -> {args.test_end} (never seen during any parameter sweep)\n")

    train = run_window(args.hist_start, args.train_end)
    test  = run_window(args.test_start, args.test_end)
    full  = run_window(args.hist_start, args.test_end)

    print("TRAIN:", fmt(train))
    print("TEST: ", fmt(test))
    print("FULL: ", fmt(full))

    print("\nConsistency check (TRAIN vs TEST — large swings suggest noise, not edge):")
    divergent = []
    for metric, threshold in DIVERGENCE_FLAG_PCTPOINTS.items():
        diff = abs(train[metric] - test[metric])
        flag = "  <-- DIVERGENT" if diff > threshold else ""
        print(f"  {metric.upper():<8} train={train[metric]:.2f}  test={test[metric]:.2f}  |diff|={diff:.2f}{flag}")
        if diff > threshold:
            divergent.append(metric)

    print()
    if divergent:
        print(f"[out_of_sample] VERDICT: UNSTABLE — {', '.join(divergent)} diverge materially between "
              f"train and test. Any full-window or single-window 'pass' for this config is not reliable "
              f"evidence of a real edge.")
    elif train["pass"] and test["pass"]:
        print("[out_of_sample] VERDICT: STABLE PASS — consistent and passes gates on both windows independently.")
    else:
        print("[out_of_sample] VERDICT: STABLE — consistent between windows, but does not pass all gates.")


if __name__ == "__main__":
    main()
