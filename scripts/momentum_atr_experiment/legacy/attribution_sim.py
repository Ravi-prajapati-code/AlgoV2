"""
Attribution harness for momentum_score_sim.py's FULL strategy — does NOT
modify strategy rules, only isolates/disables individual mechanisms one at
a time (holding everything else identical: same universe, same 783-day
window 2022-11-01->2026-05-07, same data, no brokerage) to attribute the
observed 59.5% CAGR to its components.

Variants run:
  FULL          - exact strategy from momentum_score_sim.py (reference)
  NO_SWAP       - disable the -3%/+3% stop/take rule only
  NO_RANK_EXIT  - disable the "drops to 4th" exit/realloc rule only
  NO_CASCADE    - disable the greedy max-cash-deployment cascade (only the
                  proportional split executes; leftover cash sits idle)
  MOM_ONLY      - score = momentum (ATR_PCT term removed) — ranking driven
                  by momentum magnitude alone
  ATR_ONLY      - score = ATR_PCT for momentum>0 names — ranking driven by
                  volatility alone, momentum magnitude ignored
  RANDOM_RANKx30- score-based rank replaced by a random permutation of
                  momentum>0-eligible names, every day, 30 different seeds
                  -> distribution used to test whether the real ranking
                  signal beats chance
  BUYHOLD_UNIVERSE - equal-weight buy&hold all 112 symbols, day 1, no
                  rebalancing at all (pure "being in this universe" return)
  BUYHOLD_NIFTY - Nifty 50 buy & hold same window (pure market beta)
"""
import pandas as pd
import numpy as np
import os
import json
import random

DDIR = "data/parquet_bak"
MDIR = "data/parquet_minute_backup"
START_CAPITAL = 100_000.0


def load_universe():
    common = sorted(
        set(f[:-8] for f in os.listdir(MDIR) if f.endswith(".parquet"))
        & set(f[:-8] for f in os.listdir(DDIR) if f.endswith(".parquet"))
    )
    full = []
    for s in common:
        df = pd.read_parquet(f"{MDIR}/{s}.parquet")
        d916 = df[(df.index.hour == 9) & (df.index.minute == 16)]
        if len(d916) == 783:
            full.append(s)
    return full


def compute_scores(symbols, mode="FULL"):
    out = {}
    for s in symbols:
        df = pd.read_parquet(f"{DDIR}/{s}.parquet").sort_index()
        close = df["close"]
        sma50 = close.rolling(50).mean()
        momentum = (close - sma50) / sma50 * 100

        high_low = df["high"] - df["low"]
        high_close = (df["high"] - close.shift()).abs()
        low_close = (df["low"] - close.shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0 / 14, adjust=False).mean()
        atr_pct = atr / close * 100

        if mode == "MOM_ONLY":
            score = momentum.where(momentum > 0, 0.0)
        elif mode == "ATR_ONLY":
            score = atr_pct.where(momentum > 0, 0.0)
        else:
            score = (momentum * atr_pct).where(momentum > 0, 0.0)

        out[s] = pd.DataFrame({"close": close, "momentum": momentum, "score": score})
    return out


def load_916(symbols):
    out = {}
    for s in symbols:
        df = pd.read_parquet(f"{MDIR}/{s}.parquet")
        d916 = df[(df.index.hour == 9) & (df.index.minute == 16)]
        d916 = d916.copy()
        d916.index = d916.index.date
        out[s] = d916["open"]
    return out


def build_matrices(symbols, scores):
    opens916 = load_916(symbols)
    trading_days = sorted(opens916[symbols[0]].index)
    score_mat = pd.DataFrame(index=trading_days, columns=symbols, dtype=float)
    for s in symbols:
        sdf = scores[s]
        s_score = pd.Series(sdf["score"].values, index=sdf.index.date)
        score_mat[s] = s_score.reindex(trading_days).values
    open_mat = pd.DataFrame({s: opens916[s].reindex(trading_days) for s in symbols})
    close_by_date = {s: pd.Series(scores[s]["close"].values, index=scores[s].index.date) for s in symbols}
    return trading_days, score_mat, open_mat, close_by_date


def greedy_fill(cash, priority_syms, day, open_mat, enabled=True):
    if not enabled:
        return cash, {}
    bought = {}
    changed = True
    while changed:
        changed = False
        for sym in priority_syms:
            price = open_mat.loc[day, sym]
            if pd.isna(price) or price <= 0:
                continue
            n = int(cash // price)
            if n > 0:
                cash -= n * price
                bought[sym] = bought.get(sym, 0) + n
                changed = True
    return cash, bought


def run_sim(symbols, trading_days, score_mat, open_mat, close_by_date,
            enable_swap=True, enable_rank_exit=True, enable_cascade=True,
            rank_fn=None, first_valid_idx=0):
    """rank_fn(day) -> pd.Series sorted desc, index=symbol, eligible names
    only (score>0 or random-eligible). If None, uses score_mat ranking."""

    def default_rank(day):
        s = score_mat.loc[day].dropna()
        s = s[s > 0]
        return s.sort_values(ascending=False)

    rank_fn = rank_fn or default_rank

    cash = START_CAPITAL
    positions = {}
    trade_log = []
    equity_curve = []
    pending_swap = None
    pending_rank_exit = None
    initial_done = False

    def apply_buys(day, bought, reason):
        nonlocal cash
        for sym, n in bought.items():
            price = open_mat.loc[day, sym]
            if sym in positions:
                old = positions[sym]
                total_shares = old["shares"] + n
                new_entry = (old["shares"] * old["entry_price"] + n * price) / total_shares
                positions[sym] = {"shares": total_shares, "entry_price": new_entry}
            else:
                positions[sym] = {"shares": n, "entry_price": price}
            trade_log.append({"date": str(day), "action": "BUY", "symbol": sym, "shares": n, "reason": reason})

    def apply_sell(day, sym, reason):
        nonlocal cash
        pos = positions.pop(sym)
        price = open_mat.loc[day, sym]
        cash += pos["shares"] * price
        trade_log.append({"date": str(day), "action": "SELL", "symbol": sym, "shares": pos["shares"], "reason": reason})

    for i in range(first_valid_idx, len(trading_days)):
        day = trading_days[i]
        if pd.isna(open_mat.loc[day, symbols]).all():
            continue

        if not initial_done:
            top3 = rank_fn(day)
            top3 = top3.head(3) if hasattr(top3, "head") else top3[:3]
            names = list(top3.index) if hasattr(top3, "index") else list(top3)
            if len(names) < 3:
                continue
            alloc_each = START_CAPITAL / 3.0
            spent_cash = START_CAPITAL
            bought_total = {}
            for sym in names:
                price = open_mat.loc[day, sym]
                if pd.isna(price) or price <= 0:
                    continue
                n = int(alloc_each // price)
                bought_total[sym] = bought_total.get(sym, 0) + n
                spent_cash -= n * price
            spent_cash, extra = greedy_fill(spent_cash, names, day, open_mat, enable_cascade)
            for sym, n in extra.items():
                bought_total[sym] = bought_total.get(sym, 0) + n
            cash = spent_cash
            apply_buys(day, bought_total, "INITIAL_TOP3_SPLIT")
            initial_done = True
        else:
            if enable_swap and pending_swap is not None:
                loser, winner = pending_swap
                if loser in positions:
                    apply_sell(day, loser, "STOP_LOSS_-3%_SWAP")
                price = open_mat.loc[day, winner]
                if pd.notna(price) and price > 0:
                    n = int(cash // price)
                    if n > 0:
                        cash -= n * price
                        apply_buys(day, {winner: n}, "TAKE_PROFIT_+3%_SWAP")
            pending_swap = None

            if enable_rank_exit and pending_rank_exit is not None:
                sym = pending_rank_exit
                if sym in positions:
                    apply_sell(day, sym, "RANK_DROPPED_TO_4TH+")
                    top3 = rank_fn(day)
                    top3 = top3.head(3) if hasattr(top3, "head") else top3[:3]
                    names = list(top3.index) if hasattr(top3, "index") else list(top3)
                    if names and cash > 0:
                        each = cash / len(names)
                        remaining = cash
                        bought = {}
                        for sym2 in names:
                            price = open_mat.loc[day, sym2]
                            if pd.isna(price) or price <= 0:
                                continue
                            n = int(each // price)
                            if n > 0:
                                bought[sym2] = bought.get(sym2, 0) + n
                                remaining -= n * price
                        remaining, extra = greedy_fill(remaining, names, day, open_mat, enable_cascade)
                        for sym2, n in extra.items():
                            bought[sym2] = bought.get(sym2, 0) + n
                        cash = remaining
                        if bought:
                            apply_buys(day, bought, "RANK_EXIT_REALLOC_TOP3")
            pending_rank_exit = None

        if not positions:
            equity_curve.append({"date": str(day), "equity": cash})
            continue

        returns = {}
        for sym, pos in positions.items():
            c = close_by_date[sym].get(day)
            if c is None or pd.isna(c):
                continue
            returns[sym] = (c - pos["entry_price"]) / pos["entry_price"] * 100

        equity = cash + sum(
            positions[sym]["shares"] * (close_by_date[sym].get(day, positions[sym]["entry_price"]))
            for sym in positions
        )
        equity_curve.append({"date": str(day), "equity": equity})

        if i == len(trading_days) - 1:
            break

        if enable_swap and returns:
            worst_sym = min(returns, key=returns.get)
            best_sym = max(returns, key=returns.get)
            if worst_sym != best_sym and returns[worst_sym] <= -3.0 and returns[best_sym] >= 3.0:
                pending_swap = (worst_sym, best_sym)

        if enable_rank_exit:
            today_ranks = rank_fn(day)
            rank_of = {sym: rnk + 1 for rnk, sym in enumerate(today_ranks.index)}
            for sym in list(positions.keys()):
                if enable_swap and pending_swap is not None and sym == pending_swap[0]:
                    continue
                r = rank_of.get(sym, 10**9)
                if r >= 4:
                    pending_rank_exit = sym
                    break

    final_equity = equity_curve[-1]["equity"] if equity_curve else cash
    return {
        "final_equity": final_equity,
        "equity_curve": equity_curve,
        "trade_log": trade_log,
        "n_trades": len(trade_log),
    }


def metrics(result, trading_days):
    eq = pd.DataFrame(result["equity_curve"])
    if eq.empty:
        return {"cagr": None, "total_return_pct": None, "max_dd_pct": None,
                "n_trades": 0, "sharpe": None}
    eq["date"] = pd.to_datetime(eq["date"])
    final_equity = eq["equity"].iloc[-1]
    days_elapsed = (eq["date"].iloc[-1] - eq["date"].iloc[0]).days
    cagr = ((final_equity / START_CAPITAL) ** (365.0 / days_elapsed) - 1) * 100 if days_elapsed > 0 else 0.0
    eq["peak"] = eq["equity"].cummax()
    eq["dd"] = (eq["equity"] / eq["peak"] - 1) * 100
    max_dd = eq["dd"].min()
    daily_ret = eq["equity"].pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else None
    return {
        "cagr": round(cagr, 2),
        "total_return_pct": round((final_equity / START_CAPITAL - 1) * 100, 2),
        "max_dd_pct": round(max_dd, 2),
        "n_trades": result["n_trades"],
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "final_equity": round(final_equity, 2),
    }


def buyhold_universe(symbols, trading_days, open_mat, close_by_date):
    day0 = trading_days[0]
    alloc = START_CAPITAL / len(symbols)
    cash = START_CAPITAL
    shares = {}
    for s in symbols:
        p = open_mat.loc[day0, s]
        if pd.isna(p) or p <= 0:
            continue
        n = int(alloc // p)
        shares[s] = n
        cash -= n * p
    curve = []
    for day in trading_days:
        val = cash + sum(shares[s] * close_by_date[s].get(day, 0) for s in shares if close_by_date[s].get(day) is not None)
        curve.append({"date": str(day), "equity": val})
    return {"equity_curve": curve, "trade_log": [], "n_trades": len(shares)}


def buyhold_nifty(trading_days):
    df = pd.read_parquet(f"{DDIR}/Nifty 50.parquet").sort_index()
    close_by_date = pd.Series(df["close"].values, index=df.index.date)
    dfm = pd.read_parquet(f"{MDIR}/Nifty 50.parquet") if os.path.exists(f"{MDIR}/Nifty 50.parquet") else None
    day0 = trading_days[0]
    entry_price = close_by_date.get(day0)
    if entry_price is None:
        # fallback: nearest available close
        entry_price = close_by_date.iloc[0]
    shares = START_CAPITAL / entry_price
    curve = []
    for day in trading_days:
        c = close_by_date.get(day)
        if c is None:
            continue
        curve.append({"date": str(day), "equity": shares * c})
    return {"equity_curve": curve, "trade_log": [], "n_trades": 1}


def main():
    symbols = load_universe()
    scores_full = compute_scores(symbols, mode="FULL")
    trading_days, score_mat_full, open_mat, close_by_date = build_matrices(symbols, scores_full)

    first_valid_idx = 0
    for i, d in enumerate(trading_days):
        if score_mat_full.loc[d].notna().sum() >= len(symbols) * 0.9:
            first_valid_idx = i
            break

    results = {}

    # FULL (reference)
    r = run_sim(symbols, trading_days, score_mat_full, open_mat, close_by_date,
                first_valid_idx=first_valid_idx)
    results["FULL"] = metrics(r, trading_days)

    # NO_SWAP
    r = run_sim(symbols, trading_days, score_mat_full, open_mat, close_by_date,
                enable_swap=False, first_valid_idx=first_valid_idx)
    results["NO_SWAP"] = metrics(r, trading_days)

    # NO_RANK_EXIT
    r = run_sim(symbols, trading_days, score_mat_full, open_mat, close_by_date,
                enable_rank_exit=False, first_valid_idx=first_valid_idx)
    results["NO_RANK_EXIT"] = metrics(r, trading_days)

    # NO_CASCADE (proportional split only, no max-deploy cascade)
    r = run_sim(symbols, trading_days, score_mat_full, open_mat, close_by_date,
                enable_cascade=False, first_valid_idx=first_valid_idx)
    results["NO_CASCADE"] = metrics(r, trading_days)

    # MOM_ONLY ranking
    scores_mom = compute_scores(symbols, mode="MOM_ONLY")
    _, score_mat_mom, _, _ = build_matrices(symbols, scores_mom)
    r = run_sim(symbols, trading_days, score_mat_mom, open_mat, close_by_date,
                first_valid_idx=first_valid_idx)
    results["MOM_ONLY_RANK"] = metrics(r, trading_days)

    # ATR_ONLY ranking
    scores_atr = compute_scores(symbols, mode="ATR_ONLY")
    _, score_mat_atr, _, _ = build_matrices(symbols, scores_atr)
    r = run_sim(symbols, trading_days, score_mat_atr, open_mat, close_by_date,
                first_valid_idx=first_valid_idx)
    results["ATR_ONLY_RANK"] = metrics(r, trading_days)

    # RANDOM_RANK x N seeds (eligibility = momentum>0, i.e. same universe
    # filter as FULL, but order randomized instead of score-sorted)
    random_cagrs = []
    for seed in range(30):
        rng = random.Random(seed)

        def rand_rank(day, _score_mat=score_mat_full, _rng=rng):
            s = _score_mat.loc[day].dropna()
            eligible = list(s[s > 0].index)
            _rng.shuffle(eligible)
            return pd.Series(range(len(eligible), 0, -1), index=eligible)

        r = run_sim(symbols, trading_days, score_mat_full, open_mat, close_by_date,
                    rank_fn=rand_rank, first_valid_idx=first_valid_idx)
        m = metrics(r, trading_days)
        if m["cagr"] is not None:
            random_cagrs.append(m["cagr"])

    results["RANDOM_RANK_mean"] = {
        "cagr": round(float(np.mean(random_cagrs)), 2),
        "cagr_std": round(float(np.std(random_cagrs)), 2),
        "cagr_min": round(float(np.min(random_cagrs)), 2),
        "cagr_max": round(float(np.max(random_cagrs)), 2),
        "n_seeds": len(random_cagrs),
    }

    # BUYHOLD_UNIVERSE
    r = buyhold_universe(symbols, trading_days, open_mat, close_by_date)
    results["BUYHOLD_UNIVERSE"] = metrics(r, trading_days)

    # BUYHOLD_NIFTY
    r = buyhold_nifty(trading_days)
    results["BUYHOLD_NIFTY"] = metrics(r, trading_days)

    print(json.dumps(results, indent=2, default=str))

    out_dir = os.path.dirname(__file__)
    with open(f"{out_dir}/attribution_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)


if __name__ == "__main__":
    main()
