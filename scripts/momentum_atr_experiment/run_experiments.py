"""
Runs the three follow-up experiments the user asked for, on top of the
already-run attribution baseline (docs/57). Does not optimize anything —
purpose is causal isolation, not CAGR-chasing:

  A. Concentration vs ranking: portfolio_size in {1,2,3,5,10}, each run
     both with the real score and with a random-rank control (15 seeds) at
     the same size, to separate "fewer names = more concentrated" from
     "the ranking picked good names."
  B. Exit-rule looseness: portfolio_size=3 fixed, exit_mode in
     {immediate (current), rank_gt=6, consecutive=3 days outside top-3} —
     tests whether the aggressive daily "drop to 4th -> exit" rotation is
     load-bearing or just noise.
  C. Rotation freeze: portfolio_size=3, reassess every 30 trading days
     instead of daily — tests whether daily rotation itself is the edge
     or whether a monthly refresh captures most of it.

Run: python3 scripts/momentum_atr_experiment/run_experiments.py
"""
import json
import os
import numpy as np

from engine import (
    load_universe, compute_scores, build_matrices, first_valid_index,
    run_sim, metrics, random_rank_fn,
)

OUT_DIR = os.path.dirname(__file__)


def main():
    symbols = load_universe()
    scores_full = compute_scores(symbols, mode="FULL")
    trading_days, score_mat, open_mat, close_by_date = build_matrices(symbols, scores_full)
    fvi = first_valid_index(trading_days, score_mat, len(symbols))

    results = {}

    # ---- Experiment A: concentration vs ranking ----
    sizes = [1, 2, 3, 5, 10]
    for N in sizes:
        r = run_sim(symbols, trading_days, score_mat, open_mat, close_by_date,
                    portfolio_size=N, first_valid_idx=fvi)
        results[f"A_REAL_TOP{N}"] = metrics(r)

        seed_cagrs = []
        for seed in range(15):
            rf = random_rank_fn(score_mat, seed)
            r = run_sim(symbols, trading_days, score_mat, open_mat, close_by_date,
                        portfolio_size=N, rank_fn=rf, first_valid_idx=fvi)
            m = metrics(r)
            if m["cagr"] is not None:
                seed_cagrs.append(m["cagr"])
        results[f"A_RANDOM_TOP{N}_mean"] = {
            "cagr": round(float(np.mean(seed_cagrs)), 2) if seed_cagrs else None,
            "cagr_std": round(float(np.std(seed_cagrs)), 2) if seed_cagrs else None,
            "n_seeds": len(seed_cagrs),
        }
        print(f"A done: N={N}")

    # ---- Experiment B: exit-rule looseness (N=3 fixed) ----
    r = run_sim(symbols, trading_days, score_mat, open_mat, close_by_date,
                portfolio_size=3, exit_mode="immediate", first_valid_idx=fvi)
    results["B_EXIT_IMMEDIATE_rank_gt3_current"] = metrics(r)

    r = run_sim(symbols, trading_days, score_mat, open_mat, close_by_date,
                portfolio_size=3, exit_mode="rank_gt", exit_threshold=6, first_valid_idx=fvi)
    results["B_EXIT_RANK_GT_6"] = metrics(r)

    r = run_sim(symbols, trading_days, score_mat, open_mat, close_by_date,
                portfolio_size=3, exit_mode="consecutive", exit_threshold=3, first_valid_idx=fvi)
    results["B_EXIT_3_CONSECUTIVE_DAYS_OUTSIDE_TOP3"] = metrics(r)
    print("B done")

    # ---- Experiment C: rotation freeze (N=3, reassess every 30 days) ----
    r = run_sim(symbols, trading_days, score_mat, open_mat, close_by_date,
                portfolio_size=3, freeze_days=30, first_valid_idx=fvi)
    results["C_FREEZE_30D"] = metrics(r)
    print("C done")

    print(json.dumps(results, indent=2, default=str))
    with open(f"{OUT_DIR}/experiment_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)


if __name__ == "__main__":
    main()
