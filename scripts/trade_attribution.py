#!/usr/bin/env python3
"""Trade Attribution Engine (Phase 1).

Runs a backtest and produces a per-trade attribution table — sector, regime,
weekday, holding-period bucket, entry/exit trigger, rank/ATR/volume context
at entry, and MFE/MAE — so we can see where the strategy's edge (or lack of
it) actually lives, before adding any new signal.

Reuses BacktestEngine's own indicator precomputation (_get_trading_dates /
_precompute_all) and the OHLCV already loaded for the run, so this is a
read-only, backtest-only tool: it does not touch backtest/engine.py, the
live `trades`/`positions` DB tables, or the Trade/Position dataclasses.

Usage:
    python3 scripts/trade_attribution.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]

Output:
    outputs/trade_attribution.csv  (one row per closed trade)
    + breakdown tables printed to stdout
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import INITIAL_CAPITAL, MARKET_INDEX_SYMBOL, MAX_OPEN_POSITIONS, OUTPUTS_DIR
from data.fetcher import fetch_all, fetch_index
from data.universe import get_all_symbols
from backtest.engine import BacktestEngine
from db.repository import init_db
from strategy.defensive_portfolio import GOLD_ETF

HOLD_BUCKETS = [
    (0, 5, "0-5d"), (6, 15, "6-15d"), (16, 30, "16-30d"),
    (31, 60, "31-60d"), (61, 10 ** 6, "60d+"),
]


def bucket_hold_days(days):
    for lo, hi, label in HOLD_BUCKETS:
        if lo <= days <= hi:
            return label
    return "unknown"


def classify_entry_trigger(trade):
    """Heuristic: this backtest engine only has 3 real ways a position opens,
    distinguishable via symbol and the exit_reason pattern it later carries."""
    if trade.symbol == GOLD_ETF:
        return "SAFE_HAVEN"
    reason = trade.exit_reason or ""
    if reason.startswith("BEAR_SWING|"):
        return "BEAR_SWING_BUY"
    if reason == "rebalance_trim":
        return "DEFENSIVE_REBAL"
    return "STRENGTH_CONFIRMED_BUY"


def bucket_exit_trigger(reason):
    if not reason:
        return "UNKNOWN"
    return reason.split("|")[0].split("(")[0].strip()


def compute_mfe_mae(df, entry_date, exit_date, entry_price):
    ts_entry, ts_exit = pd.Timestamp(entry_date), pd.Timestamp(exit_date)
    window = df[(df.index >= ts_entry) & (df.index <= ts_exit)]
    if window.empty or entry_price <= 0:
        return None, None
    mfe_pct = (window["high"].max() - entry_price) / entry_price * 100
    mae_pct = (window["low"].min() - entry_price) / entry_price * 100
    return round(mfe_pct, 2), round(mae_pct, 2)


def _index_pos(df, ts):
    """Position of the first index entry >= ts, or None if past the end."""
    pos = df.index.searchsorted(ts)
    return pos if pos < len(df.index) else None


def price_at_offset(df, ref_date, n_sessions, col="close"):
    """Close price N trading sessions after ref_date, or None if out of range."""
    pos = _index_pos(df, pd.Timestamp(ref_date))
    if pos is None:
        return None
    target = pos + n_sessions
    if target < 0 or target >= len(df.index):
        return None
    return df[col].iloc[target]


def post_exit_mfe(df, exit_date, horizon_sessions, entry_price):
    """Best price reached in the horizon_sessions trading days AFTER exit_date,
    relative to entry_price — answers 'would this trade have recovered had we
    not exited when we did'. None if the data window runs past the fetched range."""
    pos = _index_pos(df, pd.Timestamp(exit_date))
    if pos is None or entry_price <= 0:
        return None
    start, end = pos + 1, pos + 1 + horizon_sessions
    window = df.iloc[start:end]
    if window.empty or end > len(df.index):
        return None
    return round((window["high"].max() - entry_price) / entry_price * 100, 2)


def classify_cohort(trade):
    if trade.hold_days >= 31 and trade.net_pnl > 0:
        return "LONG_WINNER"
    if trade.hold_days <= 15 and trade.net_pnl < 0:
        return "QUICK_LOSER"
    return "OTHER"


def run(start_str: str, end_str: str):
    init_db()
    start = datetime.strptime(start_str, "%Y-%m-%d").date()
    end = datetime.strptime(end_str, "%Y-%m-%d").date()

    symbols = get_all_symbols()
    lookback = (end - start).days + 60
    warmup_start = start - timedelta(days=500)

    print(f"[Attribution] Fetching data ({start} -> {end})...")
    data = fetch_all(symbols, lookback_days=lookback, start=warmup_start, end=end)
    index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=lookback, start=warmup_start, end=end)
    if not index_df.empty:
        data[MARKET_INDEX_SYMBOL] = index_df

    print("[Attribution] Running backtest...")
    engine = BacktestEngine(
        data, start, end, INITIAL_CAPITAL,
        slippage_model="fixed_pct", max_selected=MAX_OPEN_POSITIONS,
    )
    result = engine.run()
    print(f"[Attribution] {len(result.trades)} trades. Rebuilding entry/exit indicator snapshots...")

    all_dates = engine._get_trading_dates()
    all_indicators, _, _ = engine._precompute_all(all_dates)

    rows = []
    for t in result.trades:
        entry_ind = all_indicators.get(t.entry_date, {}).get(t.symbol, {})
        exit_ind = all_indicators.get(t.exit_date, {}).get(t.symbol, {})

        ema50 = entry_ind.get("ema_50")
        ema_dist_pct = ((t.entry_price - ema50) / ema50 * 100) if ema50 else None
        ema20 = entry_ind.get("ema_20")
        ema20_dist_pct = ((t.entry_price - ema20) / ema20 * 100) if ema20 else None
        ema100 = entry_ind.get("ema_100")
        ema100_dist_pct = ((t.entry_price - ema100) / ema100 * 100) if ema100 else None
        ema150 = entry_ind.get("ema_150")
        ema150_dist_pct = ((t.entry_price - ema150) / ema150 * 100) if ema150 else None
        high_20d = entry_ind.get("high_20d")
        dist_from_high20d_pct = ((t.entry_price - high_20d) / high_20d * 100) if high_20d else None

        df = data.get(t.symbol)
        mfe_pct, mae_pct = (None, None)
        post_exit_mfe_30d_pct = None
        day_rets = {f"day{n}_ret_pct": None for n in range(1, 11)}
        if df is not None:
            mfe_pct, mae_pct = compute_mfe_mae(df, t.entry_date, t.exit_date, t.entry_price)
            post_exit_mfe_30d_pct = post_exit_mfe(df, t.exit_date, 30, t.entry_price)
            for n in range(1, 11):
                px = price_at_offset(df, t.entry_date, n)
                if px is not None and t.entry_price:
                    day_rets[f"day{n}_ret_pct"] = round((px - t.entry_price) / t.entry_price * 100, 2)

        entry_value = t.entry_price * t.shares
        pnl_pct = (t.net_pnl / entry_value * 100) if entry_value else None
        mfe_giveback_pct = (
            round(mfe_pct - pnl_pct, 2)
            if (mfe_pct is not None and pnl_pct is not None) else None
        )

        row = {
            "symbol": t.symbol,
            "sector": t.sector,
            "cohort": classify_cohort(t),
            "entry_date": pd.Timestamp(t.entry_date).date(),
            "exit_date": pd.Timestamp(t.exit_date).date(),
            "weekday_entry": pd.Timestamp(t.entry_date).day_name(),
            "month_entry": pd.Timestamp(t.entry_date).strftime("%Y-%m"),
            "hold_days": t.hold_days,
            "holding_bucket": bucket_hold_days(t.hold_days),
            "regime_at_entry": entry_ind.get("regime", "UNKNOWN"),
            "regime_at_exit": exit_ind.get("regime", "UNKNOWN"),
            "entry_trigger": classify_entry_trigger(t),
            "exit_reason": t.exit_reason,
            "exit_trigger": bucket_exit_trigger(t.exit_reason),
            "rs_rank_at_entry": round(entry_ind["rs_rank"], 1) if entry_ind.get("rs_rank") is not None else None,
            "atr_pct_at_entry": round(entry_ind["atr_pct"], 2) if entry_ind.get("atr_pct") is not None else None,
            "vol_ratio_at_entry": entry_ind.get("vol_ratio"),
            "adx_at_entry": entry_ind.get("adx"),
            "ema50_dist_pct_at_entry": round(ema_dist_pct, 2) if ema_dist_pct is not None else None,
            "ema20_dist_pct_at_entry": round(ema20_dist_pct, 2) if ema20_dist_pct is not None else None,
            "ema100_dist_pct_at_entry": round(ema100_dist_pct, 2) if ema100_dist_pct is not None else None,
            "ema150_dist_pct_at_entry": round(ema150_dist_pct, 2) if ema150_dist_pct is not None else None,
            "dist_from_high20d_pct_at_entry": round(dist_from_high20d_pct, 2) if dist_from_high20d_pct is not None else None,
            "rsi_at_entry": entry_ind.get("rsi"),
            "macd_hist_at_entry": entry_ind.get("macd_hist"),
            "macd_bullish_at_entry": entry_ind.get("macd_bullish"),
            "perf_10d_at_entry": entry_ind.get("perf_10d"),
            "turnover_at_entry": entry_ind.get("turnover"),
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "shares": t.shares,
            "gross_pnl": t.gross_pnl,
            "net_pnl": t.net_pnl,
            "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,
            "mfe_giveback_pct": mfe_giveback_pct,
            "post_exit_mfe_30d_pct": post_exit_mfe_30d_pct,
        }
        row.update(day_rets)
        rows.append(row)

    df_out = pd.DataFrame(rows)
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUTS_DIR, "trade_attribution.csv")
    df_out.to_csv(out_path, index=False)
    print(f"[Attribution] Wrote {len(df_out)} rows to {out_path}")
    return df_out, data, result


def print_breakdown(df: pd.DataFrame, group_col: str, label: str):
    print(f"\n=== By {label} ===")
    summary = df.groupby(group_col).agg(
        trades=("net_pnl", "count"),
        win_rate_pct=("net_pnl", lambda s: round((s > 0).mean() * 100, 1)),
        avg_pnl_pct=("pnl_pct", "mean"),
        total_pnl=("net_pnl", "sum"),
        avg_mfe_giveback_pct=("mfe_giveback_pct", "mean"),
    ).sort_values("total_pnl", ascending=False)
    with pd.option_context("display.float_format", "{:.2f}".format, "display.width", 120):
        print(summary.to_string())


def print_stop_recovery_analysis(df: pd.DataFrame):
    """Of the trades that were stopped/cut for a loss, how many had material
    upside in the 30 sessions right after we exited? Answers 'were our stops
    too tight' vs 'was the stop protecting us from a real loser'."""
    loss_triggers = ["STOP_LOSS", "TREND_BREAK", "GOLDBEES_MAX_LOSS"]
    cut = df[df["exit_trigger"].isin(loss_triggers) & df["post_exit_mfe_30d_pct"].notna()]
    print(f"\n=== Post-Exit Recovery Check (loss-exits with 30d forward data, n={len(cut)}) ===")
    if cut.empty:
        print("(no rows with complete forward data — likely all near the end of the fetched range)")
        return
    bins = [-1000, 0, 5, 10, 20, 1000]
    labels = ["no bounce (<=0%)", "0-5%", "5-10%", "10-20%", ">20%"]
    cut = cut.copy()
    cut["recovery_bucket"] = pd.cut(cut["post_exit_mfe_30d_pct"], bins=bins, labels=labels)
    counts = cut["recovery_bucket"].value_counts().reindex(labels)
    pct = (counts / len(cut) * 100).round(1)
    for label in labels:
        print(f"  {label:<18} {counts[label]:>3} trades  ({pct[label]:>5.1f}%)")
    would_have_recovered = (cut["post_exit_mfe_30d_pct"] > 10).sum()
    print(f"  -> {would_have_recovered}/{len(cut)} ({would_have_recovered/len(cut)*100:.1f}%) "
          f"moved >10% above entry within 30 sessions of being cut.")


def print_winner_dna(df: pd.DataFrame):
    """Compare LONG_WINNER (31+ day holds, net profit) vs QUICK_LOSER (<=15 day
    holds, net loss) at entry — do they look different going in, or only in hindsight?"""
    cohort_df = df[df["cohort"].isin(["LONG_WINNER", "QUICK_LOSER"])]
    print(f"\n=== Winner DNA: LONG_WINNER vs QUICK_LOSER at entry (n={len(cohort_df)}) ===")
    cols = [
        "rs_rank_at_entry", "atr_pct_at_entry", "vol_ratio_at_entry", "adx_at_entry",
        "ema50_dist_pct_at_entry", "day1_ret_pct", "day3_ret_pct", "day5_ret_pct", "day10_ret_pct",
        "mae_pct",
    ]
    summary = cohort_df.groupby("cohort")[cols].mean().round(2)
    counts = cohort_df.groupby("cohort").size().rename("n")
    with pd.option_context("display.float_format", "{:.2f}".format, "display.width", 140):
        print(pd.concat([counts, summary], axis=1).to_string())


def print_early_heat_curve(df: pd.DataFrame):
    """Day-by-day: how early does LONG_WINNER vs QUICK_LOSER forward-return
    separation become statistically distinguishable? Answers 'which day should
    a confirmation/scale-in decision actually be made on' rather than assuming
    day 5 by default."""
    from scipy import stats

    cohort_df = df[df["cohort"].isin(["LONG_WINNER", "QUICK_LOSER"])]
    winners = cohort_df[cohort_df["cohort"] == "LONG_WINNER"]
    losers = cohort_df[cohort_df["cohort"] == "QUICK_LOSER"]
    print(f"\n=== Early-Heat Divergence Curve (LONG_WINNER n={len(winners)} vs QUICK_LOSER n={len(losers)}) ===")
    print(f"{'day':>4}  {'winner_avg':>11}  {'loser_avg':>10}  {'gap':>7}  {'cohens_d':>9}  {'p_value':>9}")
    for n in range(1, 11):
        col = f"day{n}_ret_pct"
        w = winners[col].dropna()
        l = losers[col].dropna()
        if len(w) < 2 or len(l) < 2:
            continue
        pooled_std = ((w.std() ** 2 + l.std() ** 2) / 2) ** 0.5
        cohens_d = (w.mean() - l.mean()) / pooled_std if pooled_std else float("nan")
        _, p = stats.ttest_ind(w, l, equal_var=False)
        flag = " <-- p<0.05" if p < 0.05 else ""
        print(f"{n:>4}  {w.mean():>10.2f}%  {l.mean():>9.2f}%  {w.mean()-l.mean():>6.2f}%  "
              f"{cohens_d:>9.2f}  {p:>9.4f}{flag}")


def print_all_breakdowns(df: pd.DataFrame):
    for col, label in [
        ("sector", "Sector"),
        ("regime_at_entry", "Regime at Entry"),
        ("weekday_entry", "Entry Weekday"),
        ("holding_bucket", "Holding Period"),
        ("entry_trigger", "Entry Trigger"),
        ("exit_trigger", "Exit Trigger"),
    ]:
        print_breakdown(df, col, label)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trade Attribution Engine")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()

    result_df, _data, _result = run(args.start, args.end)
    print_all_breakdowns(result_df)
    print_stop_recovery_analysis(result_df)
    print_winner_dna(result_df)
    print_early_heat_curve(result_df)
