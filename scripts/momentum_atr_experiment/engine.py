"""
Momentum x ATR concentrated-rotation experiment — standalone, NOT part of
the AlgoV2 production pipeline. Separate score formula (SMA50 momentum x
ATR%), separate execution logic (fixed 9:16 minute open fills, no
brokerage). Does not import from strategy/, portfolio/, or backtest/ and
is not called by runner/daily_runner.py or backtest/engine.py.

Origin: user-specified rule set (see docs/57_Momentum_ATR_Concentrated_
Rotation_Experiment.md for the full research log). This file is the
promoted, parameterized version of the original one-off scratch script
(momentum_score_sim.py) + its attribution harness (attribution_sim.py) —
kept in this repo under version control per Repository Integrity (git
history is the restore point; nothing here overwrites or is called by
anything in the live/backtest path, so there is nothing "to restore" in
production — restoring means `git log` on this file/folder).

Score:
    momentum = (close - SMA50) / SMA50 * 100
    ATR_PCT  = (ATR14 / close) * 100      (Wilder EMA, alpha=1/14)
    score    = momentum * ATR_PCT, forced to 0 where momentum <= 0

Universe: 112 symbols with full 783-day 9:16 minute-candle coverage
(2022-11-01 -> 2026-05-07), intersected with daily OHLCV (data/parquet_bak).
"""
import pandas as pd
import numpy as np
import os
import random

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DDIR = os.path.join(REPO_ROOT, "data", "parquet_bak")
MDIR = os.path.join(REPO_ROOT, "data", "parquet_minute_backup")
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


def first_valid_index(trading_days, score_mat, n_symbols):
    for i, d in enumerate(trading_days):
        if score_mat.loc[d].notna().sum() >= n_symbols * 0.9:
            return i
    return 0


def _greedy_fill(cash, priority_syms, day, open_mat, enabled=True):
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
            portfolio_size=3,
            exit_mode="immediate",     # "immediate" | "rank_gt" | "consecutive"
            exit_threshold=None,       # rank cutoff (rank_gt) or day-count (consecutive)
            freeze_days=None,          # None = daily reassessment (current default)
            enable_swap=True, enable_cascade=True,
            rank_fn=None, first_valid_idx=0):
    """Generalized version of the FULL strategy. portfolio_size=N replaces
    the hardcoded "top 3"; exit_mode/exit_threshold replace the hardcoded
    "drops to 4th -> exit"; freeze_days replaces daily rotation with
    periodic reassessment. Everything else (swap rule, cash cascade,
    9:16 fills, no brokerage) is unchanged from the original spec."""

    N = portfolio_size
    if exit_mode == "immediate":
        exit_threshold = N  # exit as soon as rank > N

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
    outside_days = {}  # sym -> consecutive days ranked outside top-N
    initial_done = False
    days_since_reassess = 0

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
        pnl = (price - pos["entry_price"]) * pos["shares"]
        trade_log.append({"date": str(day), "action": "SELL", "symbol": sym, "shares": pos["shares"], "reason": reason, "pnl": pnl})

    def topN_names(day):
        r = rank_fn(day)
        r = r.head(N) if hasattr(r, "head") else r[:N]
        return list(r.index) if hasattr(r, "index") else list(r)

    def buy_split(day, names, cash_in):
        if not names:
            return cash_in, {}
        each = cash_in / len(names)
        remaining = cash_in
        bought = {}
        for sym in names:
            price = open_mat.loc[day, sym]
            if pd.isna(price) or price <= 0:
                continue
            n = int(each // price)
            if n > 0:
                bought[sym] = bought.get(sym, 0) + n
                remaining -= n * price
        remaining, extra = _greedy_fill(remaining, names, day, open_mat, enable_cascade)
        for sym, n in extra.items():
            bought[sym] = bought.get(sym, 0) + n
        return remaining, bought

    for i in range(first_valid_idx, len(trading_days)):
        day = trading_days[i]
        if pd.isna(open_mat.loc[day, symbols]).all():
            continue

        if not initial_done:
            names = topN_names(day)
            if len(names) < N:
                continue
            cash, bought = buy_split(day, names, cash)
            apply_buys(day, bought, "INITIAL_TOPN_SPLIT")
            initial_done = True
            days_since_reassess = 0
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

            if freeze_days is None and pending_rank_exit is not None:
                sym = pending_rank_exit
                if sym in positions:
                    apply_sell(day, sym, "RANK_RULE_EXIT")
                    outside_days.pop(sym, None)
                    names = topN_names(day)
                    if names and cash > 0:
                        cash, bought = buy_split(day, names, cash)
                        if bought:
                            apply_buys(day, bought, "RANK_EXIT_REALLOC_TOPN")
                pending_rank_exit = None

            if freeze_days is not None and days_since_reassess >= freeze_days:
                # full reassessment: exit anything not in the new top-N,
                # reinvest freed + idle cash equally (+ cascade) into it
                names = topN_names(day)
                for sym in list(positions.keys()):
                    if sym not in names:
                        apply_sell(day, sym, f"FREEZE_REASSESS_{freeze_days}D")
                if names and cash > 0:
                    to_buy = [n for n in names if n not in positions]
                    # top up existing holders too if cash allows (keep target weights loose;
                    # simplest reading of "freeze then compare": rebalance evenly across new topN)
                    cash, bought = buy_split(day, names, cash)
                    if bought:
                        apply_buys(day, bought, f"FREEZE_REASSESS_{freeze_days}D_BUY")
                days_since_reassess = 0

        if not positions:
            equity_curve.append({"date": str(day), "equity": cash})
            days_since_reassess += 1
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

        if freeze_days is None:
            today_ranks = rank_fn(day)
            rank_of = {sym: rnk + 1 for rnk, sym in enumerate(today_ranks.index)}
            for sym in list(positions.keys()):
                r = rank_of.get(sym, 10 ** 9)
                if r > N:
                    outside_days[sym] = outside_days.get(sym, 0) + 1
                else:
                    outside_days[sym] = 0

            for sym in list(positions.keys()):
                if enable_swap and pending_swap is not None and sym == pending_swap[0]:
                    continue
                r = rank_of.get(sym, 10 ** 9)
                fire = False
                if exit_mode == "immediate" and r > N:
                    fire = True
                elif exit_mode == "rank_gt" and r > exit_threshold:
                    fire = True
                elif exit_mode == "consecutive" and outside_days.get(sym, 0) >= exit_threshold:
                    fire = True
                if fire:
                    pending_rank_exit = sym
                    break
        else:
            days_since_reassess += 1

    final_equity = equity_curve[-1]["equity"] if equity_curve else cash
    return {
        "final_equity": final_equity,
        "equity_curve": equity_curve,
        "trade_log": trade_log,
        "n_trades": len(trade_log),
    }


def metrics(result):
    eq = pd.DataFrame(result["equity_curve"])
    if eq.empty:
        return {"cagr": None, "total_return_pct": None, "max_dd_pct": None, "n_trades": 0, "sharpe": None}
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


def profit_factor(trade_log):
    pnls = [t["pnl"] for t in trade_log if t["action"] == "SELL" and "pnl" in t]
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    if losses == 0:
        return float("inf") if wins > 0 else None
    return round(wins / losses, 3)


def random_rank_fn(score_mat, seed):
    rng = random.Random(seed)

    def rand_rank(day, _score_mat=score_mat, _rng=rng):
        s = _score_mat.loc[day].dropna()
        eligible = list(s[s > 0].index)
        _rng.shuffle(eligible)
        return pd.Series(range(len(eligible), 0, -1), index=eligible)

    return rand_rank
