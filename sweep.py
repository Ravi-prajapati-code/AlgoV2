"""
Comprehensive parameter sweep — runs all combinations in parallel.
All params driven via env vars (no YAML conflicts).
"""
import os, subprocess, itertools, concurrent.futures, sys
from datetime import datetime

BACKTEST_CMD  = ["python3", "main.py", "backtest", "--start", "2022-01-01"]
MAX_WORKERS   = 6

def run(label, env_overrides):
    env = os.environ.copy()
    env.update({k: str(v) for k, v in env_overrides.items()})
    r = subprocess.run(BACKTEST_CMD, capture_output=True, text=True, env=env)
    vals = {}
    for line in r.stdout.split("\n"):
        if "CAGR"     in line: vals["cagr"]  = line.split(":")[1].strip().split()[0].replace("%","").replace("+","")
        if "Sharpe"   in line: vals["sh"]    = line.split(":")[1].strip().split()[0]
        if "Max Draw" in line: vals["mdd"]   = line.split(":")[1].strip().split()[0].replace("%","")
        if "Win Rate" in line: vals["wr"]    = line.split(":")[1].strip().split()[0].replace("%","")
        if "PASS"     in line: vals["pass"]  = True
        if "FAIL"     in line: vals["pass"]  = False
    try:
        return {
            "label": label,
            "cagr":  float(vals.get("cagr", 0)),
            "sh":    float(vals.get("sh",   0)),
            "mdd":   float(vals.get("mdd",  99)),
            "wr":    float(vals.get("wr",   0)),
            "pass":  vals.get("pass", False),
            "env":   env_overrides,
        }
    except Exception as e:
        return {"label": label, "cagr": 0, "sh": 0, "mdd": 99, "wr": 0, "pass": False, "env": env_overrides}


# ── Parameter grid ──────────────────────────────────────────────────────
# Each key maps to list of values to sweep. Baseline value marked with *

SWEEP = {
    "UNIVERSE_TOP_N":   [40, 50, 60, 80],        # baseline: 60
    "MAX_POSITIONS":    [2, 3, 4, 5],             # baseline: 3
    "RS_THRESHOLD":     [60, 65, 72, 78, 85],     # baseline: 72
    "MIN_PROFIT_SOFT":  [0.10, 0.15, 0.20, 0.25, 0.30, 0.35],  # baseline: 0.25
    "REGIME_SWITCH_DAYS": [10, 15, 20, 25, 30],  # baseline: 25
    "LAGGARD_RS":       [40, 50, 60, 70],         # baseline: 50
    "MOMENTUM_RSI":     [0, 40, 50, 60],          # baseline: 50 (0=disabled)
    "BEAR_SWING_SLOTS": [0, 1, 2, 3],             # baseline: 2
}

BASELINE = {
    "UNIVERSE_TOP_N": 60, "MAX_POSITIONS": 3, "RS_THRESHOLD": 72,
    "MIN_PROFIT_SOFT": 0.25, "REGIME_SWITCH_DAYS": 25,
    "LAGGARD_RS": 50, "MOMENTUM_RSI": 50, "BEAR_SWING_SLOTS": 2,
}

# Phase 1: single-param sweeps (hold all others at baseline)
jobs = []
for param, values in SWEEP.items():
    for v in values:
        env = dict(BASELINE)
        env[param] = v
        label = f"{param}={v}"
        jobs.append((label, env))

# Phase 2: promising 2-way combos (added after phase 1 analysis)
# These will be appended dynamically — placeholder
combos_2way = [
    # positions × universe
    {"MAX_POSITIONS": 2, "UNIVERSE_TOP_N": 40},
    {"MAX_POSITIONS": 2, "UNIVERSE_TOP_N": 50},
    {"MAX_POSITIONS": 4, "UNIVERSE_TOP_N": 80},
    # RS × min_profit
    {"RS_THRESHOLD": 65, "MIN_PROFIT_SOFT": 0.20},
    {"RS_THRESHOLD": 78, "MIN_PROFIT_SOFT": 0.30},
    {"RS_THRESHOLD": 65, "MIN_PROFIT_SOFT": 0.30},
    # regime × positions
    {"REGIME_SWITCH_DAYS": 15, "MAX_POSITIONS": 3},
    {"REGIME_SWITCH_DAYS": 20, "MAX_POSITIONS": 3},
    # laggard × min_profit
    {"LAGGARD_RS": 60, "MIN_PROFIT_SOFT": 0.20},
    {"LAGGARD_RS": 40, "MIN_PROFIT_SOFT": 0.30},
    # bear swing × regime
    {"BEAR_SWING_SLOTS": 1, "REGIME_SWITCH_DAYS": 20},
    {"BEAR_SWING_SLOTS": 3, "REGIME_SWITCH_DAYS": 15},
    # triple combos
    {"MAX_POSITIONS": 2, "RS_THRESHOLD": 78, "MIN_PROFIT_SOFT": 0.30},
    {"MAX_POSITIONS": 4, "RS_THRESHOLD": 65, "MIN_PROFIT_SOFT": 0.20},
    {"UNIVERSE_TOP_N": 50, "MAX_POSITIONS": 3, "RS_THRESHOLD": 65},
    {"UNIVERSE_TOP_N": 80, "MAX_POSITIONS": 4, "RS_THRESHOLD": 65},
    {"MIN_PROFIT_SOFT": 0.20, "REGIME_SWITCH_DAYS": 20, "LAGGARD_RS": 60},
    {"MIN_PROFIT_SOFT": 0.30, "REGIME_SWITCH_DAYS": 15, "LAGGARD_RS": 40},
]
for overrides in combos_2way:
    env = dict(BASELINE)
    env.update(overrides)
    label = " ".join(f"{k}={v}" for k, v in overrides.items())
    jobs.append((label, env))

print(f"[sweep] {len(jobs)} experiments | {MAX_WORKERS} parallel workers | started {datetime.now():%H:%M:%S}")
print(f"{'Label':<45} {'CAGR':>7} {'Sharpe':>7} {'MDD':>7} {'WR':>7} {'PASS':>5}")
print("-" * 82)

results = []
with concurrent.futures.ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
    futures = {ex.submit(run, label, env): label for label, env in jobs}
    for fut in concurrent.futures.as_completed(futures):
        res = fut.result()
        results.append(res)
        flag = "✅" if res["pass"] else "  "
        print(f"{res['label']:<45} {res['cagr']:>+7.2f}% {res['sh']:>7.2f} {res['mdd']:>7.2f}% {res['wr']:>7.1f}% {flag}")
        sys.stdout.flush()

# Sort by composite score: CAGR × Sharpe / MDD
def score(r):
    return (r["cagr"] * r["sh"]) / max(r["mdd"], 1)

results.sort(key=score, reverse=True)

print("\n" + "=" * 82)
print("TOP 15 RESULTS (ranked by CAGR × Sharpe / MDD):")
print(f"{'Label':<45} {'CAGR':>7} {'Sharpe':>7} {'MDD':>7} {'WR':>7} {'PASS':>5}")
print("-" * 82)
for r in results[:15]:
    flag = "✅" if r["pass"] else "  "
    print(f"{r['label']:<45} {r['cagr']:>+7.2f}% {r['sh']:>7.2f} {r['mdd']:>7.2f}% {r['wr']:>7.1f}% {flag}")

print(f"\n[sweep] done {datetime.now():%H:%M:%S}")
