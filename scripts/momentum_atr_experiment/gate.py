"""
Standalone robustness gate for the momentum_atr_experiment engine.

NOT scripts/robustness_gate.py and cannot be replaced by it: that script
drives AlgoV2's production backtest/engine.py via `--env KEY=VALUE`
overrides read through config/settings.py's os.getenv() call sites. None
of portfolio_size / exit_mode / exit_threshold / freeze_days exist as env
vars anywhere in production config, and this experiment uses its own
112-symbol universe, its own SMA50-momentum x ATR% score formula, and its
own 9:16-minute-fill execution model entirely separate from
backtest/engine.py. There is nothing for scripts/robustness_gate.py to
compare. This script exists to apply an equivalent-purpose check --
chronological TRAIN/TEST tolerance + synthetic stress-scenario profit-
factor drop -- inside this experiment's own engine, against a fixed
baseline, so "did this change pass or fail" has a real, inspectable answer
instead of an unrun/inapplicable one.

Baseline: portfolio_size=3, exit_mode=immediate, freeze_days=None (the
current/default config used throughout docs/57).

Candidates: every variant actually run in docs/57 --
  A_TOP1, A_TOP2, A_TOP5, A_TOP10   (portfolio_size sweep; TOP3 == baseline)
  B_RANK_GT6, B_CONSECUTIVE3         (exit-rule looseness, N=3)
  C_FREEZE30                         (30-day freeze, N=3)

Pass rules (fixed, inspectable, mirroring robustness_gate.py's spirit):
  1. TRAIN/TEST split: chronological, no shuffle. TRAIN = first 70% of
     trading days from first_valid_idx, TEST = remaining 30%. Both windows
     re-run from a fresh START_CAPITAL (independent-window evaluation, not
     continuation).
  2. Candidate TEST Sharpe >= 0.90 * baseline TEST Sharpe, AND candidate
     TEST profit factor >= 0.90 * baseline TEST profit factor (10%
     relative tolerance, same magnitude as robustness_gate.py's
     OOS_TEST_TOLERANCE).
  3. CAGR-flip check: if baseline TEST CAGR > 0, candidate TEST CAGR must
     not be < 0.
  4. Stress: 4 synthetic scenarios reused verbatim from
     scripts/stress_test_scenarios.py (crash_v_recovery,
     extended_bear_grind, prolonged_sideways_chop, gap_down_bleed) --
     same macro daily-return paths, same per-symbol idiosyncratic noise
     model. A synthetic OHLCV tail is appended after each symbol's real
     last close; SMA50/ATR/score are recomputed over the combined real+
     synthetic series so the warmup is genuine, not a cold start. Approx-
     imation, stated explicitly: since no synthetic minute data exists,
     the synthetic tail's 9:16 fill price is taken as the synthetic daily
     open (real windows use the true 9:16 print; this is a same-bar-open
     proxy for the stress windows only). Candidate profit factor must not
     drop more than 0.10 (absolute) below baseline profit factor in any
     of the 4 scenarios (STRESS_PF_DROP_MAX, same value as
     robustness_gate.py).

Verdict: PASS only if every rule passes for every candidate; else REJECT
with the specific failing checks listed.

Run: python3 scripts/momentum_atr_experiment/gate.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, os.path.join(_HERE, "..", ".."))

from engine import (
    DDIR, START_CAPITAL, load_universe, compute_scores, build_matrices,
    first_valid_index, run_sim, metrics, profit_factor,
)
from stress_test_scenarios import SCENARIOS, macro_daily_returns, synth_tail

OUT_DIR = os.path.dirname(__file__)
TRAIN_FRAC = 0.70
OOS_TOLERANCE = 0.10       # relative, applied to sharpe and pf
STRESS_PF_DROP_MAX = 0.10  # absolute
STRESS_WARMUP_DAYS = 90    # real days of history carried before the synthetic tail
STRESS_SEED = 7

BASELINE = dict(portfolio_size=3, exit_mode="immediate", exit_threshold=None, freeze_days=None)
CANDIDATES = {
    "A_TOP1":       dict(portfolio_size=1, exit_mode="immediate", exit_threshold=None, freeze_days=None),
    "A_TOP2":       dict(portfolio_size=2, exit_mode="immediate", exit_threshold=None, freeze_days=None),
    "A_TOP5":       dict(portfolio_size=5, exit_mode="immediate", exit_threshold=None, freeze_days=None),
    "A_TOP10":      dict(portfolio_size=10, exit_mode="immediate", exit_threshold=None, freeze_days=None),
    "B_RANK_GT6":   dict(portfolio_size=3, exit_mode="rank_gt", exit_threshold=6, freeze_days=None),
    "B_CONSEC3":    dict(portfolio_size=3, exit_mode="consecutive", exit_threshold=3, freeze_days=None),
    "C_FREEZE30":   dict(portfolio_size=3, exit_mode="immediate", exit_threshold=None, freeze_days=30),
}


def run_window(symbols, days, score_mat, open_mat, close_by_date, cfg):
    r = run_sim(symbols, days, score_mat, open_mat, close_by_date, first_valid_idx=0, **cfg)
    m = metrics(r)
    m["pf"] = profit_factor(r["trade_log"])
    return m


def build_stress_matrices(symbols, scenario_name):
    scenario = SCENARIOS[scenario_name]
    macro = macro_daily_returns(scenario["segments"])
    rng = np.random.default_rng(STRESS_SEED)

    combined_scores, combined_opens, combined_closes = {}, {}, {}
    for s in symbols:
        real = pd.read_parquet(f"{DDIR}/{s}.parquet").sort_index()
        last_close = float(real["close"].iloc[-1])
        last_vol = float(real["volume"].iloc[-1]) if "volume" in real.columns else 1_000_000.0
        start_date = (real.index[-1] + pd.Timedelta(days=1)).date()

        sym_rng = np.random.default_rng(rng.integers(0, 2**32 - 1))
        tail = synth_tail(last_close, last_vol, macro, scenario["noise_std"], sym_rng, start_date)
        tail = tail.set_index(pd.to_datetime(tail["date"])).drop(columns=["date"])

        combined = pd.concat([real[["open", "high", "low", "close", "volume"]], tail])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()

        close = combined["close"]
        sma50 = close.rolling(50).mean()
        momentum = (close - sma50) / sma50 * 100
        high_low = combined["high"] - combined["low"]
        high_close = (combined["high"] - close.shift()).abs()
        low_close = (combined["low"] - close.shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0 / 14, adjust=False).mean()
        atr_pct = atr / close * 100
        score = (momentum * atr_pct).where(momentum > 0, 0.0)

        warm_start = real.index[-STRESS_WARMUP_DAYS]
        window = combined.loc[warm_start:]
        combined_scores[s] = pd.Series(score.reindex(window.index).values, index=window.index.date)
        combined_opens[s] = pd.Series(window["open"].values, index=window.index.date)
        combined_closes[s] = pd.Series(window["close"].values, index=window.index.date)

    trading_days = sorted(combined_opens[symbols[0]].index)
    score_mat = pd.DataFrame({s: combined_scores[s].reindex(trading_days) for s in symbols})
    open_mat = pd.DataFrame({s: combined_opens[s].reindex(trading_days) for s in symbols})
    close_by_date = {s: combined_closes[s] for s in symbols}

    return trading_days, score_mat, open_mat, close_by_date


def main():
    symbols = load_universe()
    scores_full = compute_scores(symbols, mode="FULL")
    trading_days, score_mat, open_mat, close_by_date = build_matrices(symbols, scores_full)
    fvi = first_valid_index(trading_days, score_mat, len(symbols))

    split_idx = fvi + int((len(trading_days) - fvi) * TRAIN_FRAC)
    train_days = trading_days[fvi:split_idx]
    test_days = trading_days[split_idx:]

    results = {"baseline": {}, "candidates": {}}
    results["baseline"]["TRAIN"] = run_window(symbols, train_days, score_mat, open_mat, close_by_date, BASELINE)
    results["baseline"]["TEST"] = run_window(symbols, test_days, score_mat, open_mat, close_by_date, BASELINE)
    print("baseline TRAIN/TEST done")

    for name, cfg in CANDIDATES.items():
        results["candidates"][name] = {
            "TRAIN": run_window(symbols, train_days, score_mat, open_mat, close_by_date, cfg),
            "TEST": run_window(symbols, test_days, score_mat, open_mat, close_by_date, cfg),
        }
        print(f"{name} TRAIN/TEST done")

    stress = {"baseline": {}, "candidates": {n: {} for n in CANDIDATES}}
    for scen in SCENARIOS:
        s_days, s_score, s_open, s_close = build_stress_matrices(symbols, scen)
        r = run_sim(symbols, s_days, s_score, s_open, s_close, first_valid_idx=0, **BASELINE)
        stress["baseline"][scen] = {"pf": profit_factor(r["trade_log"]), "n_trades": r["n_trades"]}
        for name, cfg in CANDIDATES.items():
            r = run_sim(symbols, s_days, s_score, s_open, s_close, first_valid_idx=0, **cfg)
            stress["candidates"][name][scen] = {"pf": profit_factor(r["trade_log"]), "n_trades": r["n_trades"]}
        print(f"stress {scen} done")

    verdicts = {}
    for name in CANDIDATES:
        failures = []
        b_test, c_test = results["baseline"]["TEST"], results["candidates"][name]["TEST"]

        if b_test["sharpe"] and b_test["sharpe"] > 0:
            if c_test["sharpe"] is None or c_test["sharpe"] < b_test["sharpe"] * (1 - OOS_TOLERANCE):
                failures.append(f"TEST sharpe {c_test['sharpe']} < {round(b_test['sharpe']*(1-OOS_TOLERANCE),3)} "
                                 f"(baseline {b_test['sharpe']}, tol {OOS_TOLERANCE})")
        if b_test["pf"] and b_test["pf"] not in (None, float("inf")) and b_test["pf"] > 0:
            if c_test["pf"] is None or c_test["pf"] < b_test["pf"] * (1 - OOS_TOLERANCE):
                failures.append(f"TEST pf {c_test['pf']} < {round(b_test['pf']*(1-OOS_TOLERANCE),3)} "
                                 f"(baseline {b_test['pf']}, tol {OOS_TOLERANCE})")
        if b_test["cagr"] is not None and b_test["cagr"] > 0 and c_test["cagr"] is not None and c_test["cagr"] < 0:
            failures.append(f"TEST cagr flips negative: {c_test['cagr']}% (baseline {b_test['cagr']}%)")

        for scen in SCENARIOS:
            b_pf = stress["baseline"][scen]["pf"]
            c_pf = stress["candidates"][name][scen]["pf"]
            if b_pf is not None and b_pf != float("inf") and c_pf is not None:
                if c_pf < b_pf - STRESS_PF_DROP_MAX:
                    failures.append(f"stress[{scen}] pf {c_pf} < {round(b_pf - STRESS_PF_DROP_MAX,3)} "
                                     f"(baseline {b_pf}, drop-max {STRESS_PF_DROP_MAX})")
            elif c_pf is None and b_pf is not None:
                failures.append(f"stress[{scen}] candidate had no closed trades to price a PF (baseline pf {b_pf})")

        verdicts[name] = {"verdict": "PASS" if not failures else "REJECT", "failures": failures}

    out = {"baseline": results["baseline"], "candidates": results["candidates"],
           "stress": stress, "verdicts": verdicts}
    with open(f"{OUT_DIR}/gate_results.json", "w") as f:
        json.dump(out, f, indent=2, default=str)

    print("\n=== VERDICTS ===")
    for name, v in verdicts.items():
        print(f"{name}: {v['verdict']}")
        for f_ in v["failures"]:
            print(f"    - {f_}")


if __name__ == "__main__":
    main()
