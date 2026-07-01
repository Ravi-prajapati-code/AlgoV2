#!/usr/bin/env python3
"""
Bear market scenario runner.

Runs multiple backtest configurations sequentially and compares results.
All no-GOLDBEES scenarios use SAFE_HAVEN_ENABLED=false LIQUIDBEES_ENABLED=0 BEAR_SWING_SLOTS=3.

Usage (from AlgoV2 root):
    python3 scripts/scenario_runner.py
    python3 scripts/scenario_runner.py --start 2022-01-01
    python3 scripts/scenario_runner.py --dry-run   # list scenarios only
"""

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
from datetime import date

# ── Acceptance thresholds (must match config/settings.py) ─────────────────
THRESHOLDS = {
    "cagr":   22.0,
    "sharpe":  1.0,
    "mdd":    20.0,   # lower is better
    "wr":     40.0,
    "pf":      1.80,
}

# ── Stdout regex (matches backtest/reporter.py print_summary output) ───────
METRIC_RE = {
    "cagr":   re.compile(r"CAGR\s*:\s*([+-]?[\d.]+)%"),
    "sharpe": re.compile(r"Sharpe Ratio\s*:\s*([\d.]+)"),
    "mdd":    re.compile(r"Max Drawdown\s*:\s*([\d.]+)%"),
    "wr":     re.compile(r"Win Rate\s*:\s*([\d.]+)%"),
    "pf":     re.compile(r"Profit Factor\s*:\s*([\d.]+)"),
}

# ── Shared base env for all no-GOLDBEES scenarios ─────────────────────────
_NO_GOLD = {
    "SAFE_HAVEN_ENABLED": "false",
    "LIQUIDBEES_ENABLED": "0",
    "BEAR_SWING_SLOTS":   "3",
}

SCENARIOS = [
    # ── Baseline references ───────────────────────────────────────────────
    {
        "name": "01_baseline_gold",
        "desc": "Baseline: GOLDBEES on, all defaults",
        "env":  {
            "SAFE_HAVEN_ENABLED":     "true",
            "MIN_DEFENSIVE_HOLD_DAYS": "45",
            "REGIME_SWITCH_DAYS":     "25",
            "BEAR_SWING_RS_THRESHOLD": "60",
            "ENTRY_CONFIRM_DAYS":     "0",
        },
    },
    {
        "name": "02_no_gold_baseline",
        "desc": "No GOLDBEES, all defaults — known ~22.88% CAGR",
        "env":  {**_NO_GOLD,
            "MIN_DEFENSIVE_HOLD_DAYS": "45",
            "REGIME_SWITCH_DAYS":     "25",
            "BEAR_SWING_RS_THRESHOLD": "60",
            "ENTRY_CONFIRM_DAYS":     "0",
        },
    },

    # ── Category A: MIN_DEFENSIVE_HOLD_DAYS sweep ─────────────────────────
    {
        "name": "03_hold0",
        "desc": "No GOLDBEES, MIN_DEFENSIVE_HOLD_DAYS=0 (zero lockout)",
        "env":  {**_NO_GOLD,
            "MIN_DEFENSIVE_HOLD_DAYS": "0",
            "REGIME_SWITCH_DAYS":     "25",
            "BEAR_SWING_RS_THRESHOLD": "60",
            "ENTRY_CONFIRM_DAYS":     "0",
        },
    },
    {
        "name": "04_hold5",
        "desc": "No GOLDBEES, MIN_DEFENSIVE_HOLD_DAYS=5",
        "env":  {**_NO_GOLD,
            "MIN_DEFENSIVE_HOLD_DAYS": "5",
            "REGIME_SWITCH_DAYS":     "25",
            "BEAR_SWING_RS_THRESHOLD": "60",
            "ENTRY_CONFIRM_DAYS":     "0",
        },
    },
    {
        "name": "05_hold15",
        "desc": "No GOLDBEES, MIN_DEFENSIVE_HOLD_DAYS=15",
        "env":  {**_NO_GOLD,
            "MIN_DEFENSIVE_HOLD_DAYS": "15",
            "REGIME_SWITCH_DAYS":     "25",
            "BEAR_SWING_RS_THRESHOLD": "60",
            "ENTRY_CONFIRM_DAYS":     "0",
        },
    },

    # ── Category B: REGIME_SWITCH_DAYS sweep ─────────────────────────────
    {
        "name": "06_regime5",
        "desc": "No GOLDBEES, REGIME_SWITCH_DAYS=5 (very fast switch)",
        "env":  {**_NO_GOLD,
            "MIN_DEFENSIVE_HOLD_DAYS": "45",
            "REGIME_SWITCH_DAYS":     "5",
            "BEAR_SWING_RS_THRESHOLD": "60",
            "ENTRY_CONFIRM_DAYS":     "0",
        },
    },
    {
        "name": "07_regime10",
        "desc": "No GOLDBEES, REGIME_SWITCH_DAYS=10",
        "env":  {**_NO_GOLD,
            "MIN_DEFENSIVE_HOLD_DAYS": "45",
            "REGIME_SWITCH_DAYS":     "10",
            "BEAR_SWING_RS_THRESHOLD": "60",
            "ENTRY_CONFIRM_DAYS":     "0",
        },
    },
    {
        "name": "08_regime15",
        "desc": "No GOLDBEES, REGIME_SWITCH_DAYS=15",
        "env":  {**_NO_GOLD,
            "MIN_DEFENSIVE_HOLD_DAYS": "45",
            "REGIME_SWITCH_DAYS":     "15",
            "BEAR_SWING_RS_THRESHOLD": "60",
            "ENTRY_CONFIRM_DAYS":     "0",
        },
    },

    # ── Category C: BEAR_SWING_RS_THRESHOLD sweep ─────────────────────────
    {
        "name": "09_rs50",
        "desc": "No GOLDBEES, BEAR_SWING_RS_THRESHOLD=50 (more trades)",
        "env":  {**_NO_GOLD,
            "MIN_DEFENSIVE_HOLD_DAYS": "45",
            "REGIME_SWITCH_DAYS":     "25",
            "BEAR_SWING_RS_THRESHOLD": "50",
            "ENTRY_CONFIRM_DAYS":     "0",
        },
    },
    {
        "name": "10_rs55",
        "desc": "No GOLDBEES, BEAR_SWING_RS_THRESHOLD=55",
        "env":  {**_NO_GOLD,
            "MIN_DEFENSIVE_HOLD_DAYS": "45",
            "REGIME_SWITCH_DAYS":     "25",
            "BEAR_SWING_RS_THRESHOLD": "55",
            "ENTRY_CONFIRM_DAYS":     "0",
        },
    },
    {
        "name": "11_rs70",
        "desc": "No GOLDBEES, BEAR_SWING_RS_THRESHOLD=70 (fewer, better trades)",
        "env":  {**_NO_GOLD,
            "MIN_DEFENSIVE_HOLD_DAYS": "45",
            "REGIME_SWITCH_DAYS":     "25",
            "BEAR_SWING_RS_THRESHOLD": "70",
            "ENTRY_CONFIRM_DAYS":     "0",
        },
    },

    # ── Category D: ENTRY_CONFIRM_DAYS sweep ─────────────────────────────
    {
        "name": "12_confirm3",
        "desc": "No GOLDBEES, ENTRY_CONFIRM_DAYS=3 (anti-whipsaw)",
        "env":  {**_NO_GOLD,
            "MIN_DEFENSIVE_HOLD_DAYS": "45",
            "REGIME_SWITCH_DAYS":     "25",
            "BEAR_SWING_RS_THRESHOLD": "60",
            "ENTRY_CONFIRM_DAYS":     "3",
        },
    },
    {
        "name": "13_confirm5",
        "desc": "No GOLDBEES, ENTRY_CONFIRM_DAYS=5",
        "env":  {**_NO_GOLD,
            "MIN_DEFENSIVE_HOLD_DAYS": "45",
            "REGIME_SWITCH_DAYS":     "25",
            "BEAR_SWING_RS_THRESHOLD": "60",
            "ENTRY_CONFIRM_DAYS":     "5",
        },
    },

    # ── Category E: Combined best-guess combos ────────────────────────────
    {
        "name": "14_combo_fast_entry",
        "desc": "No GOLDBEES, hold=5, regime=15, rs=55, confirm=3",
        "env":  {**_NO_GOLD,
            "MIN_DEFENSIVE_HOLD_DAYS": "5",
            "REGIME_SWITCH_DAYS":     "15",
            "BEAR_SWING_RS_THRESHOLD": "55",
            "ENTRY_CONFIRM_DAYS":     "3",
        },
    },
    {
        "name": "15_combo_conservative",
        "desc": "No GOLDBEES, hold=15, regime=10, rs=60, confirm=3",
        "env":  {**_NO_GOLD,
            "MIN_DEFENSIVE_HOLD_DAYS": "15",
            "REGIME_SWITCH_DAYS":     "10",
            "BEAR_SWING_RS_THRESHOLD": "60",
            "ENTRY_CONFIRM_DAYS":     "3",
        },
    },
]

EQUITY_SRC  = "outputs/backtest_equity.csv"
OUTPUTS_DIR = "outputs"
RESULTS_CSV = "outputs/scenario_comparison.csv"


def run_scenario(scenario: dict, start: str, end: str) -> dict:
    env = os.environ.copy()
    env.update(scenario["env"])

    proc = subprocess.run(
        [sys.executable, "main.py", "backtest", "--start", start, "--end", end],
        capture_output=True, text=True, env=env,
    )
    stdout = proc.stdout

    result = {
        "name":   scenario["name"],
        "desc":   scenario["desc"],
        "cagr":   float("nan"),
        "sharpe": float("nan"),
        "mdd":    float("nan"),
        "wr":     float("nan"),
        "pf":     float("nan"),
        "status": "ERROR" if proc.returncode != 0 else "OK",
        "stderr": proc.stderr[-500:] if proc.returncode != 0 else "",
    }
    for key, rx in METRIC_RE.items():
        m = rx.search(stdout)
        if m:
            result[key] = float(m.group(1))

    return result


def passes(row: dict) -> bool:
    try:
        return (
            row["cagr"]   >= THRESHOLDS["cagr"]
            and row["sharpe"] >= THRESHOLDS["sharpe"]
            and row["mdd"]    <= THRESHOLDS["mdd"]
            and row["wr"]     >= THRESHOLDS["wr"]
            and row["pf"]     >= THRESHOLDS["pf"]
        )
    except (TypeError, KeyError):
        return False


def _fmt(val, fmt, suffix=""):
    if val != val:  # NaN check
        return "  N/A"
    return format(val, fmt) + suffix


def copy_equity_curve(scenario_name: str) -> str:
    dst = os.path.join(OUTPUTS_DIR, f"scenario_{scenario_name}_equity.csv")
    if os.path.exists(EQUITY_SRC):
        shutil.copy2(EQUITY_SRC, dst)
        return dst
    return ""


def print_table(rows: list):
    pass_rows = sorted([r for r in rows if passes(r)],     key=lambda x: x["cagr"] if x["cagr"] == x["cagr"] else -999, reverse=True)
    fail_rows = sorted([r for r in rows if not passes(r)], key=lambda x: x["cagr"] if x["cagr"] == x["cagr"] else -999, reverse=True)
    sorted_rows = pass_rows + fail_rows

    valid_cagr   = [r["cagr"]   for r in rows if r["cagr"]   == r["cagr"]]
    valid_sharpe = [r["sharpe"] for r in rows if r["sharpe"] == r["sharpe"]]
    valid_mdd    = [r["mdd"]    for r in rows if r["mdd"]    == r["mdd"]]
    best_cagr    = max(valid_cagr)   if valid_cagr   else None
    best_sharpe  = max(valid_sharpe) if valid_sharpe else None
    best_mdd     = min(valid_mdd)    if valid_mdd    else None

    W = 100
    print("\n" + "=" * W)
    print("  BEAR SCENARIO COMPARISON  (ranked: PASS first, then by CAGR)")
    print("=" * W)
    print(f"{'#':<3} {'Name':<28} {'CAGR':>8} {'Sharpe':>7} {'MDD':>7} {'WR':>7} {'PF':>6}  {'Result'}")
    print("-" * W)

    for i, row in enumerate(sorted_rows, 1):
        markers = []
        if best_cagr   is not None and row["cagr"]   == best_cagr:   markers.append("CAGR★")
        if best_sharpe is not None and row["sharpe"] == best_sharpe: markers.append("SHP★")
        if best_mdd    is not None and row["mdd"]    == best_mdd:    markers.append("MDD★")

        verdict = "✅ PASS" if passes(row) else "❌ FAIL"
        if markers:
            verdict += "  [" + " ".join(markers) + "]"

        cagr_s   = _fmt(row["cagr"],   "+.2f", "%")
        sharpe_s = _fmt(row["sharpe"],  ".2f")
        mdd_s    = _fmt(row["mdd"],     ".2f", "%")
        wr_s     = _fmt(row["wr"],      ".1f", "%")
        pf_s     = _fmt(row["pf"],      ".2f")

        print(f"{i:<3} {row['name']:<28} {cagr_s:>8} {sharpe_s:>7} {mdd_s:>7} {wr_s:>7} {pf_s:>6}  {verdict}")

    print("=" * W)
    print(f"  Thresholds: CAGR≥{THRESHOLDS['cagr']}%  Sharpe≥{THRESHOLDS['sharpe']}  "
          f"MDD≤{THRESHOLDS['mdd']}%  WR≥{THRESHOLDS['wr']}%  PF≥{THRESHOLDS['pf']}\n")


def save_csv(rows: list):
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    fieldnames = [
        "name", "desc", "cagr", "sharpe", "mdd", "wr", "pf",
        "pass", "status",
        "safe_haven", "min_hold", "regime_switch", "rs_threshold", "entry_confirm",
    ]
    with open(RESULTS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            sc = next((s for s in SCENARIOS if s["name"] == row["name"]), {})
            env_vars = sc.get("env", {})
            w.writerow({
                "name":          row["name"],
                "desc":          row["desc"],
                "cagr":          row["cagr"],
                "sharpe":        row["sharpe"],
                "mdd":           row["mdd"],
                "wr":            row["wr"],
                "pf":            row["pf"],
                "pass":          "PASS" if passes(row) else "FAIL",
                "status":        row.get("status", "OK"),
                "safe_haven":    env_vars.get("SAFE_HAVEN_ENABLED", ""),
                "min_hold":      env_vars.get("MIN_DEFENSIVE_HOLD_DAYS", ""),
                "regime_switch": env_vars.get("REGIME_SWITCH_DAYS", ""),
                "rs_threshold":  env_vars.get("BEAR_SWING_RS_THRESHOLD", ""),
                "entry_confirm": env_vars.get("ENTRY_CONFIRM_DAYS", ""),
            })
    print(f"[Runner] Results saved: {RESULTS_CSV}")


def main():
    ap = argparse.ArgumentParser(description="Bear market scenario runner")
    ap.add_argument("--start",   default="2022-01-01")
    ap.add_argument("--end",     default=str(date.today()))
    ap.add_argument("--dry-run", action="store_true", help="List scenarios without running")
    args = ap.parse_args()

    if args.dry_run:
        print(f"\nScenarios to run ({len(SCENARIOS)} total, ~{len(SCENARIOS) * 2.5:.0f} min est.):")
        for i, s in enumerate(SCENARIOS, 1):
            print(f"  {i:>2}. {s['name']:<30}  {s['desc']}")
            print(f"       env: {s['env']}")
        return

    print(f"\n[Runner] Starting {len(SCENARIOS)} scenarios | {args.start} → {args.end}")
    print(f"[Runner] Estimated total runtime: ~{len(SCENARIOS) * 2.5:.0f} minutes\n")

    all_results = []
    for i, scenario in enumerate(SCENARIOS, 1):
        print(f"[Runner] ({i}/{len(SCENARIOS)}) {scenario['name']} — {scenario['desc']}", flush=True)

        result = run_scenario(scenario, args.start, args.end)

        if result["status"] == "ERROR":
            print(f"[Runner]   ERROR: {result['stderr']}", flush=True)
        else:
            print(
                f"[Runner]   CAGR={_fmt(result['cagr'], '+.2f', '%')}  "
                f"Sharpe={_fmt(result['sharpe'], '.2f')}  "
                f"MDD={_fmt(result['mdd'], '.2f', '%')}  "
                f"WR={_fmt(result['wr'], '.1f', '%')}  "
                f"PF={_fmt(result['pf'], '.2f')}  "
                f"{'PASS' if passes(result) else 'FAIL'}",
                flush=True,
            )

        copied = copy_equity_curve(scenario["name"])
        if copied:
            print(f"[Runner]   Equity → {copied}", flush=True)

        all_results.append(result)

    print_table(all_results)
    save_csv(all_results)


if __name__ == "__main__":
    main()
