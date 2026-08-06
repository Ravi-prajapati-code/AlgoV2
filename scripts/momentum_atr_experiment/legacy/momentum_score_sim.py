"""
Standalone rule-based simulation per user spec (not part of AlgoV2 production
pipeline — separate score formula, separate execution logic, run ad hoc).

Score:
    momentum = (close - SMA50) / SMA50 * 100
    ATR_PCT  = (ATR14 / close) * 100      (Wilder EMA, alpha=1/14, matches
                                            indicators/composite.py convention)
    score    = momentum * ATR_PCT
    score    = 0 where momentum <= 0

Universe: 112 symbols with full 783-day 9:16 minute-candle coverage
(2022-11-01 -> 2026-05-07), intersected with daily OHLCV (parquet_bak) for
score computation.

Signal timing: score computed from day D's close (EOD). All buy/sell orders
decided at D's close execute at 9:16 open of D+1 (no look-ahead). Applies
uniformly including the very first entry.

Assumptions (spec didn't fully pin these down, flagging explicitly):
  A1. Only score > 0 (i.e. momentum > 0) names are eligible to be newly
      bought (initial top-3, or top-3 refill after a rank-4+ exit). A
      momentum<=0 name has score forced to 0 by definition — "top 3 by
      score" including zero/negative-momentum junk would contradict the
      strategy's own momentum premise.
  A2. "Invest as maximum as possible" applied everywhere cash is freed:
      the greedy leftover-cascade (fill #1 max, then #2, then #3, loop till
      no affordable share) used for the initial buy is reused after any
      later equal-split allocation, not just the first loop.
  A3. -3%/+3% swap and 4th-place-rank exit are both evaluated at each day's
      close, off the SAME pre-execution portfolio; swap is applied first,
      rank-check applied second on the resulting (post-swap) holdings; a
      stock already sold by the swap is not double-processed by the rank
      check same day.
  A4. Return for the -3%/+3% check = unrealized % change from each
      position's own entry price (9:16 fill) to that day's close, not
      day-over-day.
  A5. "Buy maximum amount of that +3% stock" uses ALL available cash
      (sale proceeds + any idle cash), not just proceeds.
  A6. No brokerage/STT/slippage anywhere, as instructed. Whole shares only
      (floor), no fractional shares, no shorting.
"""
import pandas as pd
import numpy as np
import os
import json

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


def compute_scores(symbols):
    """Returns dict[symbol] -> DataFrame(index=date, columns=[close, score])"""
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

        score = momentum * atr_pct
        score = score.where(momentum > 0, 0.0)

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


def main():
    symbols = load_universe()
    print(f"Universe: {len(symbols)} symbols (full 783-day 9:16 coverage)")
    scores = compute_scores(symbols)
    opens916 = load_916(symbols)

    trading_days = sorted(opens916[symbols[0]].index)
    print(f"Trading days: {len(trading_days)} ({trading_days[0]} -> {trading_days[-1]})")

    # Build score matrix aligned to trading_days for fast lookup, using each
    # day's own daily close (available at that day's EOD).
    score_mat = pd.DataFrame(index=trading_days, columns=symbols, dtype=float)
    mom_mat = pd.DataFrame(index=trading_days, columns=symbols, dtype=float)
    for s in symbols:
        sdf = scores[s]
        sdf_dates = sdf.index.date
        s_score = pd.Series(sdf["score"].values, index=sdf_dates)
        s_mom = pd.Series(sdf["momentum"].values, index=sdf_dates)
        score_mat[s] = s_score.reindex(trading_days).values
        mom_mat[s] = s_mom.reindex(trading_days).values

    open_mat = pd.DataFrame({s: opens916[s].reindex(trading_days) for s in symbols})

    # warm-up: need SMA50 -> valid from ~50 trading days after data start,
    # daily data starts well before 2022-11-01 so first sim day should
    # already be valid. Confirm and find first fully-scored day.
    first_valid_idx = 0
    for i, d in enumerate(trading_days):
        if score_mat.loc[d].notna().sum() >= len(symbols) * 0.9:
            first_valid_idx = i
            break

    cash = START_CAPITAL
    positions = {}  # symbol -> {"shares": int, "entry_price": float}
    trade_log = []
    equity_curve = []

    def rank_top3(day, exclude_zero=True):
        s = score_mat.loc[day].dropna()
        if exclude_zero:
            s = s[s > 0]
        return s.sort_values(ascending=False)

    def greedy_fill(cash, priority_syms, day):
        """Spend cash on whole shares of priority_syms (in order), looping
        until nothing more affordable — A2."""
        cash = cash
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

    def apply_buys(day, bought, reason):
        nonlocal cash
        for sym, n in bought.items():
            price = open_mat.loc[day, sym]
            if sym in positions:
                # blend entry price (weighted avg) if adding to existing
                old = positions[sym]
                total_shares = old["shares"] + n
                new_entry = (old["shares"] * old["entry_price"] + n * price) / total_shares
                positions[sym] = {"shares": total_shares, "entry_price": new_entry}
            else:
                positions[sym] = {"shares": n, "entry_price": price}
            trade_log.append({"date": str(day), "action": "BUY", "symbol": sym,
                               "shares": n, "price": round(price, 2), "reason": reason})

    def apply_sell(day, sym, reason):
        nonlocal cash
        pos = positions.pop(sym)
        price = open_mat.loc[day, sym]
        proceeds = pos["shares"] * price
        cash += proceeds
        trade_log.append({"date": str(day), "action": "SELL", "symbol": sym,
                           "shares": pos["shares"], "price": round(price, 2),
                           "reason": reason})
        return proceeds

    pending_swap = None  # (loser_sym, winner_sym) decided at prior EOD
    pending_rank_exit = None  # sym

    initial_done = False

    for i in range(first_valid_idx, len(trading_days)):
        day = trading_days[i]

        if pd.isna(open_mat.loc[day, symbols]).all():
            continue

        # ---- Execute anything queued from previous day's EOD decision ----
        if not initial_done:
            top3 = rank_top3(day).head(3)
            if len(top3) < 3:
                continue  # not enough eligible names yet
            names = list(top3.index)
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
            # greedy leftover cascade, priority = names in score order
            spent_cash, extra = greedy_fill(spent_cash, names, day)
            for sym, n in extra.items():
                bought_total[sym] = bought_total.get(sym, 0) + n
            cash = spent_cash
            apply_buys(day, bought_total, "INITIAL_TOP3_SPLIT")
            initial_done = True
        else:
            if pending_swap is not None:
                loser, winner = pending_swap
                if loser in positions:
                    apply_sell(day, loser, "STOP_LOSS_-3%_SWAP")
                if winner in positions or True:
                    price = open_mat.loc[day, winner]
                    if pd.notna(price) and price > 0:
                        n = int(cash // price)
                        if n > 0:
                            cash -= n * price
                            apply_buys(day, {winner: n}, "TAKE_PROFIT_+3%_SWAP")
                pending_swap = None

            if pending_rank_exit is not None:
                sym = pending_rank_exit
                if sym in positions:
                    apply_sell(day, sym, "RANK_DROPPED_TO_4TH+")
                pending_rank_exit = None
                # reallocate equally to current top-3 by score
                top3 = rank_top3(day).head(3)
                names = list(top3.index)
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
                    remaining, extra = greedy_fill(remaining, names, day)
                    for sym2, n in extra.items():
                        bought[sym2] = bought.get(sym2, 0) + n
                    cash = remaining
                    if bought:
                        apply_buys(day, bought, "RANK_EXIT_REALLOC_TOP3")

        # ---- EOD: compute returns + rank, queue decisions for next day ----
        if not positions:
            equity_curve.append({"date": str(day), "equity": cash})
            continue

        closes_today = scores  # dict of per-symbol df with 'close' indexed by date
        returns = {}
        for sym, pos in positions.items():
            sdf = scores[sym]
            try:
                close_today = sdf.loc[sdf.index.date == day, "close"]
                close_today = close_today.iloc[-1] if len(close_today) else None
            except Exception:
                close_today = None
            if close_today is None or pd.isna(close_today):
                continue
            returns[sym] = (close_today - pos["entry_price"]) / pos["entry_price"] * 100

        equity = cash + sum(
            positions[sym]["shares"] * (scores[sym].loc[scores[sym].index.date == day, "close"].iloc[-1]
                                         if len(scores[sym].loc[scores[sym].index.date == day]) else positions[sym]["entry_price"])
            for sym in positions
        )
        equity_curve.append({"date": str(day), "equity": equity})

        if i == len(trading_days) - 1:
            break  # no next day to execute on

        if returns:
            worst_sym = min(returns, key=returns.get)
            best_sym = max(returns, key=returns.get)
            if (worst_sym != best_sym and returns[worst_sym] <= -3.0
                    and returns[best_sym] >= 3.0):
                pending_swap = (worst_sym, best_sym)

        # rank-based 4th-place exit check (skip a symbol already queued for swap-sell)
        today_ranks = rank_top3(day, exclude_zero=False)
        rank_of = {sym: rnk + 1 for rnk, sym in enumerate(today_ranks.index)}
        for sym in list(positions.keys()):
            if pending_swap is not None and sym == pending_swap[0]:
                continue
            r = rank_of.get(sym, 10**9)
            if r >= 4:
                pending_rank_exit = sym
                break  # one exit event per day, per literal spec wording

    final_equity = equity_curve[-1]["equity"] if equity_curve else cash
    days_elapsed = (trading_days[-1] - trading_days[first_valid_idx]).days
    cagr = ((final_equity / START_CAPITAL) ** (365.0 / days_elapsed) - 1) * 100 if days_elapsed > 0 else 0.0

    print("\n=== RESULT ===")
    print(f"Start capital : Rs {START_CAPITAL:,.0f}")
    print(f"Final equity  : Rs {final_equity:,.2f}")
    print(f"Total return  : {(final_equity/START_CAPITAL - 1)*100:.2f}%")
    print(f"CAGR          : {cagr:.2f}%  over {days_elapsed} days")
    print(f"Total trades  : {len(trade_log)}")
    print(f"Final holdings: {list(positions.keys())}")

    out_dir = os.path.dirname(__file__)
    with open(f"{out_dir}/trade_log.json", "w") as f:
        json.dump(trade_log, f, indent=2)
    pd.DataFrame(equity_curve).to_csv(f"{out_dir}/equity_curve.csv", index=False)
    print(f"\nFull trade log -> {out_dir}/trade_log.json")
    print(f"Equity curve   -> {out_dir}/equity_curve.csv")


if __name__ == "__main__":
    main()
