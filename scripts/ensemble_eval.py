"""
Ensemble backtest evaluator — runs the backtest from staggered start dates and
reports mean / worst-case metrics, so configs are judged on robustness instead
of one lucky path.

Usage:
    python3 scripts/ensemble_eval.py --label baseline
    ENTRY_CONFIRM_DAYS=7 python3 scripts/ensemble_eval.py --label confirm7

Env vars are inherited by each run, so set strategy knobs the same way as for
main.py. Results appended to outputs/ensemble_results.csv.
"""

import argparse
import csv
import os
import re
import subprocess
import sys
from datetime import date

START_DATES = [
    "2022-01-01", "2022-01-15", "2022-02-01",
    "2022-02-15", "2022-03-01", "2022-03-15",
]
END_DATE = "2026-06-09"

METRIC_RE = {
    "cagr":   re.compile(r"CAGR\s*:\s*([+-][\d.]+)%"),
    "sharpe": re.compile(r"Sharpe Ratio\s*:\s*([\d.]+)"),
    "mdd":    re.compile(r"Max Drawdown\s*:\s*([\d.]+)%"),
    "pf":     re.compile(r"Profit Factor\s*:\s*([\d.]+)"),
    "trades": re.compile(r"Total Trades\s*:\s*(\d+)"),
}


def run_one(start: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "main.py", "backtest", "--start", start, "--end", END_DATE],
        capture_output=True, text=True, env=os.environ.copy(),
    )
    out = proc.stdout
    row = {"start": start}
    for key, rx in METRIC_RE.items():
        m = rx.search(out)
        row[key] = float(m.group(1)) if m else float("nan")
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="config label for the results CSV")
    args = ap.parse_args()

    rows = []
    for start in START_DATES:
        row = run_one(start)
        rows.append(row)
        print(f"[{args.label}] start={start}  CAGR={row['cagr']:+.2f}%  "
              f"MDD={row['mdd']:.2f}%  Sharpe={row['sharpe']:.2f}  "
              f"PF={row['pf']:.2f}  trades={row['trades']:.0f}", flush=True)

    cagrs = [r["cagr"] for r in rows]
    mdds  = [r["mdd"] for r in rows]
    print(f"\n[{args.label}] SUMMARY over {len(rows)} starts")
    print(f"  CAGR  mean {sum(cagrs)/len(cagrs):+.2f}%   worst {min(cagrs):+.2f}%   best {max(cagrs):+.2f}%")
    print(f"  MDD   mean {sum(mdds)/len(mdds):.2f}%   worst {max(mdds):.2f}%")

    os.makedirs("outputs", exist_ok=True)
    path = "outputs/ensemble_results.csv"
    write_header = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["label", "run_date", "start", "cagr", "mdd", "sharpe", "pf", "trades"])
        for r in rows:
            w.writerow([args.label, date.today(), r["start"], r["cagr"], r["mdd"],
                        r["sharpe"], r["pf"], r["trades"]])
    print(f"appended to {path}")


if __name__ == "__main__":
    main()
