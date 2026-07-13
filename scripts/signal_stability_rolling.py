"""
Rolling-window RS-rank signal stability analysis.

The permutation test (docs/16.6) proved rs_rank carries genuine selection information
over the full sample. This script asks a different question: is that signal structurally
stable, or regime-dependent?

For each rolling window it reports (no cross-window averaging):
  - Permutation p-value (IC-based, same symbol-shuffle design as selection_skill_monte_carlo)
  - Information Coefficient (mean daily cross-sectional Spearman rank IC)
  - Hit Rate (top-quintile vs bottom-quintile, and top-quintile positive-return rate)
  - Contribution to portfolio (% of strategy PnL from high-RS entries)
  - Alpha contribution (Jensen's alpha vs Nifty 50 in window)
  - Stability (IC IR, % positive-IC days, quintile-spread significance)

Usage:
    python3 scripts/signal_stability_rolling.py
    python3 scripts/signal_stability_rolling.py --window-months 9 --step-months 3 --permutations 80
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.watchlist_nse import ALL_SYMBOLS
from config.settings import (
    INITIAL_CAPITAL, MARKET_INDEX_SYMBOL, MAX_OPEN_POSITIONS, RS_THRESHOLD,
)
from data.fetcher import fetch_all, fetch_index
from backtest.engine import BacktestEngine
from scripts.benchmark_attribution import (
    RISK_FREE_RATE_ANNUAL, TRADING_DAYS, compute_attribution, sharpe_ratio,
)

START_DEFAULT = "2022-01-01"
FORWARD_DAYS = 20
TOP_Q = 0.80   # top quintile threshold (rs_rank >= 80)
BOT_Q = 0.20   # bottom quintile threshold (rs_rank <= 20)
HIGH_RS_ENTRY = 75.0  # trades above this counted toward portfolio contribution


def parse_args():
    p = argparse.ArgumentParser(description="Rolling RS-rank signal stability analysis")
    p.add_argument("--start", default=START_DEFAULT)
    p.add_argument("--end", default=str(date.today()))
    p.add_argument("--window-months", type=int, default=12,
                   help="Rolling window length in calendar months (default: 12)")
    p.add_argument("--step-months", type=int, default=3,
                   help="Step between window starts in months (default: 3)")
    p.add_argument("--permutations", type=int, default=100,
                   help="Permutation draws per window for p-value (default: 100)")
    p.add_argument("--forward-days", type=int, default=FORWARD_DAYS)
    p.add_argument("--output", default=None,
                   help="CSV output path (default: outputs/signal_stability_rolling.csv)")
    p.add_argument("--skip-backtest", action="store_true",
                   help="Skip per-window backtests (faster; portfolio/alpha metrics omitted)")
    return p.parse_args()


def _month_offset(d: date, months: int) -> date:
    """Shift date by calendar months (day clamped to month end)."""
    y, m = d.year, d.month + months
    while m > 12:
        y += 1
        m -= 12
    while m < 1:
        y -= 1
        m += 12
    import calendar
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def rolling_window_starts(start: date, end: date, window_months: int, step_months: int) -> List[Tuple[date, date]]:
    windows = []
    w_start = start
    while True:
        w_end = _month_offset(w_start, window_months) - timedelta(days=1)
        if w_end > end:
            break
        if w_end >= w_start:
            windows.append((w_start, w_end))
        w_start = _month_offset(w_start, step_months)
        if w_start >= end:
            break
    return windows


def build_rs_panel(data: Dict[str, pd.DataFrame], index_df: pd.DataFrame,
                   forward_days: int) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Daily cross-section: symbol × date with rs_rank and forward returns.
    rs_rank matches backtest engine: cross-sectional percentile of 126d RS ratio.
    """
    if index_df.empty:
        raise RuntimeError("Index data empty — cannot build RS panel")

    idx_close = index_df["close"].copy()
    idx_close.index = pd.to_datetime(idx_close.index).normalize()

    symbols = [s for s in data if s != MARKET_INDEX_SYMBOL and not data[s].empty]
    ratios = {}
    closes = {}
    for sym in symbols:
        df = data[sym]
        if not isinstance(df.index, pd.DatetimeIndex):
            df = df.copy()
            df.index = pd.to_datetime(df.index)
        daily = df.resample("D").agg({"close": "last"}).dropna()
        s_close = daily["close"].reindex(idx_close.index)
        rs_line = s_close / idx_close
        ratios[sym] = (rs_line / rs_line.rolling(window=126, min_periods=20).mean()) * 100
        closes[sym] = s_close

    ratios_df = pd.DataFrame(ratios)
    rs_rank_df = ratios_df.rank(axis=1, pct=True) * 100

    close_df = pd.DataFrame(closes)
    fwd_ret = close_df.pct_change(periods=forward_days).shift(-forward_days)
    idx_fwd = idx_close.pct_change(periods=forward_days).shift(-forward_days)

    regime = (idx_close > idx_close.ewm(span=100, adjust=False).mean()).map(
        {True: "BULL", False: "BEAR"}
    )

    rows = []
    for dt in rs_rank_df.index:
        if pd.isna(dt):
            continue
        day_ranks = rs_rank_df.loc[dt].dropna()
        day_fwd = fwd_ret.loc[dt].dropna() if dt in fwd_ret.index else pd.Series(dtype=float)
        if len(day_ranks) < 10:
            continue
        idx_f = float(idx_fwd.loc[dt]) if dt in idx_fwd.index and not pd.isna(idx_fwd.loc[dt]) else np.nan
        reg = regime.loc[dt] if dt in regime.index else "UNKNOWN"
        for sym in day_ranks.index:
            fr = day_fwd.get(sym)
            if fr is None or pd.isna(fr):
                continue
            rows.append({
                "date": dt.date() if hasattr(dt, "date") else dt,
                "symbol": sym,
                "rs_rank": float(day_ranks[sym]),
                "fwd_ret": float(fr),
                "excess_ret": float(fr - idx_f) if not pd.isna(idx_f) else np.nan,
                "regime": reg,
            })

    panel = pd.DataFrame(rows)
    panel["date"] = pd.to_datetime(panel["date"])
    return panel, idx_close.pct_change().dropna()


def daily_ic_series(panel: pd.DataFrame) -> pd.Series:
    """Cross-sectional Spearman IC per day: rs_rank vs forward return."""
    ics = []
    for dt, grp in panel.groupby("date"):
        sub = grp.dropna(subset=["rs_rank", "fwd_ret"])
        if len(sub) < 10:
            continue
        rho, _ = spearmanr(sub["rs_rank"], sub["fwd_ret"])
        if not np.isnan(rho):
            ics.append((dt, rho))
    if not ics:
        return pd.Series(dtype=float)
    s = pd.Series({d: v for d, v in ics})
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def permute_panel_ic(panel: pd.DataFrame, seed: int) -> float:
    """
    Apply fixed symbol→symbol permutation to rs_rank (docs/16.6 design),
    return mean daily IC on permuted ranks.
    """
    rng = np.random.default_rng(seed)
    symbols = sorted(panel["symbol"].unique())
    shuffled = list(symbols)
    rng.shuffle(shuffled)
    perm = dict(zip(symbols, shuffled))

    orig = panel.pivot_table(index="date", columns="symbol", values="rs_rank", aggfunc="first")
    permuted = orig.copy()
    for s in symbols:
        src = perm[s]
        if src in orig.columns:
            permuted[s] = orig[src]

    long = permuted.stack().reset_index()
    long.columns = ["date", "symbol", "rs_rank_perm"]
    merged = panel.merge(long, on=["date", "symbol"], how="left")

    ics = []
    for dt, grp in merged.groupby("date"):
        sub = grp.dropna(subset=["rs_rank_perm", "fwd_ret"])
        if len(sub) < 10:
            continue
        rho, _ = spearmanr(sub["rs_rank_perm"], sub["fwd_ret"])
        if not np.isnan(rho):
            ics.append(rho)
    return float(np.mean(ics)) if ics else 0.0


def hit_rate_metrics(panel: pd.DataFrame) -> dict:
    """Hit rates within a window panel."""
    top = panel[panel["rs_rank"] >= TOP_Q * 100]
    bot = panel[panel["rs_rank"] <= BOT_Q * 100]

    top_pos = (top["fwd_ret"] > 0).mean() if len(top) else np.nan
    top_beat_idx = (top["excess_ret"] > 0).mean() if len(top) and top["excess_ret"].notna().any() else np.nan

    daily_spread = []
    for dt, grp in panel.groupby("date"):
        t = grp[grp["rs_rank"] >= TOP_Q * 100]["fwd_ret"]
        b = grp[grp["rs_rank"] <= BOT_Q * 100]["fwd_ret"]
        if len(t) >= 2 and len(b) >= 2:
            daily_spread.append(t.mean() - b.mean())
    q5_q1_spread = float(np.mean(daily_spread)) if daily_spread else np.nan
    q5_beats_q1_day_pct = float(np.mean([s > 0 for s in daily_spread]) * 100) if daily_spread else np.nan

    return {
        "top_quintile_positive_pct": round(top_pos * 100, 1) if not np.isnan(top_pos) else np.nan,
        "top_quintile_beat_index_pct": round(top_beat_idx * 100, 1) if not np.isnan(top_beat_idx) else np.nan,
        "q5_q1_spread_mean_pct": round(q5_q1_spread * 100, 3) if not np.isnan(q5_q1_spread) else np.nan,
        "q5_beats_q1_day_pct": round(q5_beats_q1_day_pct, 1) if not np.isnan(q5_beats_q1_day_pct) else np.nan,
        "n_top_quintile_obs": len(top),
        "n_bottom_quintile_obs": len(bot),
    }


def regime_mix(panel: pd.DataFrame) -> dict:
    by_day = panel.groupby("date")["regime"].first()
    bull_pct = (by_day == "BULL").mean() * 100 if len(by_day) else np.nan
    return {"bull_day_pct": round(bull_pct, 1) if not np.isnan(bull_pct) else np.nan}


def window_signal_metrics(panel: pd.DataFrame, n_perm: int) -> dict:
    ic_series = daily_ic_series(panel)
    mean_ic = float(ic_series.mean()) if len(ic_series) else np.nan
    ic_std = float(ic_series.std(ddof=1)) if len(ic_series) > 1 else np.nan
    ic_ir = mean_ic / ic_std if ic_std and ic_std > 0 else np.nan
    ic_pos_pct = float((ic_series > 0).mean() * 100) if len(ic_series) else np.nan

    perm_ics = [permute_panel_ic(panel, seed) for seed in range(n_perm)]
    perm_arr = np.array(perm_ics)
    n_ge = int((perm_arr >= mean_ic).sum()) if not np.isnan(mean_ic) else n_perm
    perm_p = (n_ge + 1) / (len(perm_arr) + 1)

    hits = hit_rate_metrics(panel)
    reg = regime_mix(panel)

    return {
        "n_ic_days": len(ic_series),
        "mean_ic": round(mean_ic, 4) if not np.isnan(mean_ic) else np.nan,
        "ic_std": round(ic_std, 4) if not np.isnan(ic_std) else np.nan,
        "ic_ir": round(ic_ir, 3) if ic_ir is not None and not np.isnan(ic_ir) else np.nan,
        "ic_positive_day_pct": round(ic_pos_pct, 1) if not np.isnan(ic_pos_pct) else np.nan,
        "permutation_p_ic": round(perm_p, 4),
        "permuted_ic_mean": round(float(perm_arr.mean()), 4),
        "permuted_ic_std": round(float(perm_arr.std(ddof=1)), 4),
        **hits,
        **reg,
    }


def attach_trade_contribution(panel: pd.DataFrame, data: dict, w_start: date, w_end: date) -> dict:
    """Run backtest and attribute PnL to high-RS entries using panel lookup."""
    warmup = w_start - timedelta(days=500)
    engine = BacktestEngine(
        data, w_start, w_end, INITIAL_CAPITAL,
        slippage_model="fixed_pct", max_selected=MAX_OPEN_POSITIONS,
        fund_injections={},
    )
    result = engine.run()

    rank_lookup = {
        (row["date"], row["symbol"]): row["rs_rank"]
        for _, row in panel.iterrows()
    }
    total_pnl = 0.0
    high_rs_pnl = 0.0
    qualifying_pnl = 0.0  # entries meeting RS_THRESHOLD

    for t in result.trades:
        pnl = t.net_pnl or 0.0
        total_pnl += pnl
        ed = pd.Timestamp(t.entry_date).normalize()
        key = (ed, t.symbol)
        rs = rank_lookup.get(key)
        if rs is None:
            # nearest prior date in panel for symbol
            sym_panel = panel[panel["symbol"] == t.symbol]
            prior = sym_panel[sym_panel["date"] <= ed]
            if not prior.empty:
                rs = prior.iloc[-1]["rs_rank"]
        if rs is not None:
            if rs >= HIGH_RS_ENTRY:
                high_rs_pnl += pnl
            if rs >= RS_THRESHOLD:
                qualifying_pnl += pnl

    equity = pd.Series(result.equity_curve).sort_index()
    equity.index = pd.to_datetime(equity.index).normalize()
    strat_ret = equity.pct_change().dropna()
    idx_df = data.get(MARKET_INDEX_SYMBOL)
    bench_ret = pd.Series(dtype=float)
    if idx_df is not None and not idx_df.empty:
        idx = idx_df["close"].copy()
        idx.index = pd.to_datetime(idx_df.index).normalize()
        bench_ret = idx.pct_change().dropna()

    attr = compute_attribution(strat_ret, bench_ret, "Nifty50") if len(bench_ret) else {}

    # Top-RS basket: equal-weight top quintile each rebalance week (signal-only contribution)
    top_basket_rets = []
    for dt, grp in panel.groupby("date"):
        top = grp[grp["rs_rank"] >= TOP_Q * 100]
        if len(top) >= 3:
            top_basket_rets.append(top["fwd_ret"].mean())
    signal_basket_ann = np.nan
    if top_basket_rets:
        mean_basket = float(np.mean(top_basket_rets))
        signal_basket_ann = mean_basket * (TRADING_DAYS / FORWARD_DAYS) * 100

    return {
        "strategy_trades": len(result.trades),
        "strategy_total_pnl": round(total_pnl, 0),
        "high_rs_pnl_share_pct": round(100 * high_rs_pnl / total_pnl, 1) if total_pnl else np.nan,
        "qualifying_rs_pnl_share_pct": round(100 * qualifying_pnl / total_pnl, 1) if total_pnl else np.nan,
        "jensen_alpha_annual_pct": round(attr.get("jensen_alpha_annual_pct", np.nan), 2),
        "alpha_contribution_ir": round(attr.get("information_ratio", np.nan), 3),
        "excess_return_vs_nifty_pct": round(attr.get("excess_return_pct", np.nan), 2),
        "top_quintile_basket_ann_pct": round(signal_basket_ann, 2) if not np.isnan(signal_basket_ann) else np.nan,
    }


def stability_label(row: dict) -> str:
    """Classify window signal strength from per-window metrics only."""
    ic_ok = row.get("mean_ic", 0) > 0.02
    p_ok = row.get("permutation_p_ic", 1) < 0.10
    hit_ok = row.get("q5_beats_q1_day_pct", 0) > 55
    stable_ok = row.get("ic_positive_day_pct", 0) > 55
    if ic_ok and p_ok and hit_ok and stable_ok:
        return "STRONG"
    if row.get("mean_ic", 0) < 0 or row.get("permutation_p_ic", 1) > 0.25:
        return "WEAK"
    return "MIXED"


def print_window_table(df: pd.DataFrame):
    cols = [
        "window", "bull_day_pct", "mean_ic", "ic_ir", "ic_positive_day_pct",
        "permutation_p_ic", "top_quintile_positive_pct", "q5_beats_q1_day_pct",
        "q5_q1_spread_mean_pct", "high_rs_pnl_share_pct", "jensen_alpha_annual_pct",
        "stability_label",
    ]
    cols = [c for c in cols if c in df.columns]
    with pd.option_context("display.max_rows", 50, "display.width", 200, "display.float_format", "{:.3f}".format):
        print(df[cols].to_string(index=False))


def interpret_results(df: pd.DataFrame):
    print("\n" + "=" * 72)
    print("INTERPRETATION — per-window (not averaged)")
    print("=" * 72)

    valid = df.dropna(subset=["mean_ic", "permutation_p_ic"])
    if valid.empty:
        print("No valid windows.")
        return

    strong = valid[valid["stability_label"] == "STRONG"]
    weak = valid[valid["stability_label"] == "WEAK"]
    mixed = valid[valid["stability_label"] == "MIXED"]

    print(f"\nWindows analysed : {len(valid)}")
    print(f"  STRONG (IC>0.02, p<0.10, Q5>Q1 >55% days, IC+ >55% days): {len(strong)}")
    print(f"  WEAK   (IC<0 or p>0.25)                                    : {len(weak)}")
    print(f"  MIXED  (between)                                           : {len(mixed)}")

    if not strong.empty:
        print("\n── Signal STRONGEST in ──")
        for _, r in strong.sort_values("mean_ic", ascending=False).iterrows():
            print(f"  {r['window']}  IC={r['mean_ic']:+.3f}  p={r['permutation_p_ic']:.3f}  "
                  f"BULL={r.get('bull_day_pct', float('nan')):.0f}%  "
                  f"Q5>Q1 days={r.get('q5_beats_q1_day_pct', float('nan')):.0f}%")

    if not weak.empty:
        print("\n── Signal WEAKEST in ──")
        for _, r in weak.sort_values("mean_ic").iterrows():
            print(f"  {r['window']}  IC={r['mean_ic']:+.3f}  p={r['permutation_p_ic']:.3f}  "
                  f"BULL={r.get('bull_day_pct', float('nan')):.0f}%  "
                  f"Q5>Q1 days={r.get('q5_beats_q1_day_pct', float('nan')):.0f}%")

    # Regime dependence check: compare mean_ic in high-BULL vs low-BULL windows (report, don't average into one number)
    if "bull_day_pct" in valid.columns:
        high_bull = valid[valid["bull_day_pct"] >= 60]
        low_bull = valid[valid["bull_day_pct"] < 40]
        if len(high_bull) and len(low_bull):
            print("\n── Regime dependence (window-level, not pooled) ──")
            print(f"  High-BULL windows (≥60% bull days): n={len(high_bull)}")
            for _, r in high_bull.sort_values("mean_ic", ascending=False).iterrows():
                print(f"    {r['window']}  IC={r['mean_ic']:+.3f}  p={r['permutation_p_ic']:.3f}")
            print(f"  Low-BULL windows (<40% bull days): n={len(low_bull)}")
            for _, r in low_bull.sort_values("mean_ic", ascending=False).iterrows():
                print(f"    {r['window']}  IC={r['mean_ic']:+.3f}  p={r['permutation_p_ic']:.3f}")

    sig_windows = valid[valid["permutation_p_ic"] < 0.10]
    nonsig_windows = valid[valid["permutation_p_ic"] >= 0.10]
    print(f"\n── Structural stability verdict ──")
    if len(sig_windows) >= len(valid) * 0.6:
        print("  Signal appears STRUCTURALLY STABLE: majority of windows show significant IC (p<0.10).")
    elif len(nonsig_windows) >= len(valid) * 0.5:
        print("  Signal appears REGIME-DEPENDENT: IC significance fails in many windows.")
    else:
        print("  Signal is EPISODIC: significant in some windows, absent in others — timing/regime overlay dominates.")

    if not strong.empty and not weak.empty:
        bull_strong = strong["bull_day_pct"].mean()
        bull_weak = weak["bull_day_pct"].mean()
        if bull_strong - bull_weak > 15:
            print(f"  Strong windows skew BULL ({bull_strong:.0f}% vs {bull_weak:.0f}% in weak) — momentum signal is regime-dependent.")
        elif bull_weak - bull_strong > 15:
            print(f"  Strong windows skew BEAR ({100-bull_strong:.0f}% bear vs {100-bull_weak:.0f}% in weak) — contrarian RS works in stress.")
        else:
            print("  No clear bull/bear skew between strong and weak windows — signal is not purely regime-gated.")


def main():
    args = parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    windows = rolling_window_starts(start, end, args.window_months, args.step_months)

    if not windows:
        print("ERROR: no rolling windows fit in date range.", file=sys.stderr)
        return 1

    print(f"Signal stability analysis: {start} → {end}")
    print(f"Windows: {len(windows)} × {args.window_months}mo (step {args.step_months}mo), "
          f"{args.permutations} permutations/window, {args.forward_days}d forward IC")
    print("Fetching universe data once...", flush=True)

    warmup_start = start - timedelta(days=500)
    lookback = (end - start).days + 120
    data = fetch_all(ALL_SYMBOLS, lookback_days=lookback, start=warmup_start, end=end)
    index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=lookback, start=warmup_start, end=end)
    if not index_df.empty:
        data[MARKET_INDEX_SYMBOL] = index_df

    print("Building RS rank panel...", flush=True)
    full_panel, _ = build_rs_panel(data, index_df, args.forward_days)

    rows = []
    for i, (w_start, w_end) in enumerate(windows):
        wlabel = f"{w_start} → {w_end}"
        print(f"\n[{i+1}/{len(windows)}] {wlabel}", flush=True)

        mask = (full_panel["date"] >= pd.Timestamp(w_start)) & (full_panel["date"] <= pd.Timestamp(w_end))
        wpanel = full_panel.loc[mask].copy()
        if len(wpanel) < 500:
            print(f"  SKIP — insufficient panel rows ({len(wpanel)})")
            continue

        metrics = window_signal_metrics(wpanel, args.permutations)
        metrics["window_start"] = str(w_start)
        metrics["window_end"] = str(w_end)
        metrics["window"] = wlabel
        metrics["n_panel_rows"] = len(wpanel)

        if not args.skip_backtest:
            print("  Running window backtest for portfolio/alpha attribution...", flush=True)
            bt = attach_trade_contribution(wpanel, data, w_start, w_end)
            metrics.update(bt)

        metrics["stability_label"] = stability_label(metrics)
        rows.append(metrics)

        print(f"  IC={metrics['mean_ic']:+.4f}  p={metrics['permutation_p_ic']:.3f}  "
              f"hit(Q5>Q1 days)={metrics.get('q5_beats_q1_day_pct', float('nan')):.0f}%  "
              f"label={metrics['stability_label']}", flush=True)

    if not rows:
        print("No window results produced.", file=sys.stderr)
        return 1

    df = pd.DataFrame(rows)
    out = args.output or os.path.join("outputs", "signal_stability_rolling.csv")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\nWrote {out}")

    print("\n── Per-window results (no averaging) ──")
    print_window_table(df)
    interpret_results(df)
    return 0


if __name__ == "__main__":
    sys.exit(main())