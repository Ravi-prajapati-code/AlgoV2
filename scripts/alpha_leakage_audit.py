"""
Alpha-leakage audit (docs/18): one instrumented baseline run, pure measurement, no optimization.

Allocates the wrapper drag (established by the permutation test: wrapper with random selection
earns +2.90% vs +17.20% for the passive equal-weight universe) across pipeline stages:

  1. Exposure/cash drag      — daily invested fraction from equity_curve/cash_curve, by regime/year
  2. Per-unit-exposure return — does invested capital beat the universe? (locates drag vs skill)
  3. Slot saturation          — % of days at MAX positions; qualified-but-not-selected counts
  4. Selection cut-off cost   — fwd 21d benchmark-adjusted return of qualified-not-selected names
  5. Exit continuation        — fwd 21d benchmark-adjusted return of exited symbols, by exit_reason
  6. Friction                 — charges from trade ledger + slippage estimate from turnover
  7. Holding-period split     — P&L share of <31d vs >=31d holds (trade-attribution cross-check)
"""
import os
import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.watchlist_nse import ALL_SYMBOLS
from config.settings import INITIAL_CAPITAL, MARKET_INDEX_SYMBOL, SLIPPAGE_FIXED_PCT
from data.fetcher import fetch_all, fetch_index, fetch_symbol
from backtest.engine import BacktestEngine

START = "2022-01-01"
END = str(date.today())
FWD_DAYS = 21


def fwd_return(closes: pd.Series, dt, days: int):
    """Return over `days` trading days starting at first close on/after dt; NaN if unavailable."""
    idx = closes.index.searchsorted(pd.Timestamp(dt))
    if idx >= len(closes) or idx + days >= len(closes):
        return np.nan
    return closes.iloc[idx + days] / closes.iloc[idx] - 1


def main():
    start = datetime.strptime(START, "%Y-%m-%d").date()
    end = datetime.strptime(END, "%Y-%m-%d").date()
    warmup_start = start - timedelta(days=500)
    lookback = (end - start).days + 60

    print("Fetching data...", flush=True)
    data = fetch_all(ALL_SYMBOLS, lookback_days=lookback, start=warmup_start, end=end)
    index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=lookback, start=warmup_start, end=end)
    if not index_df.empty:
        data[MARKET_INDEX_SYMBOL] = index_df
    bench_df = fetch_symbol("MIDCAPETF.NS", start=start - timedelta(days=30), end=end)
    bench_close = bench_df["close"].sort_index()
    bench_close.index = pd.to_datetime(bench_close.index).normalize()

    closes = {}
    for sym, df in data.items():
        c = df["close"].sort_index()
        c.index = pd.to_datetime(c.index).normalize()
        closes[sym] = c

    print("Running baseline backtest...", flush=True)
    engine = BacktestEngine(data, start, end, INITIAL_CAPITAL,
                            slippage_model="fixed_pct", fund_injections={})
    result = engine.run()

    equity = pd.Series(result.equity_curve).sort_index()
    cash = pd.Series(result.cash_curve).sort_index()
    equity.index = pd.to_datetime(equity.index).normalize()
    cash.index = pd.to_datetime(cash.index).normalize()
    exposure = (1 - cash / equity).clip(lower=0)
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    strat_cagr = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100

    print("\n=== 1. EXPOSURE / CASH DRAG ===")
    print(f"Strategy CAGR (this run): {strat_cagr:+.2f}%  over {years:.2f}y")
    print(f"Average exposure: {exposure.mean()*100:.1f}%   median: {exposure.median()*100:.1f}%")
    for yr, grp in exposure.groupby(exposure.index.year):
        print(f"  {yr}: mean exposure {grp.mean()*100:5.1f}%   days<50% invested: {(grp < 0.5).mean()*100:4.1f}%")

    scan = pd.DataFrame(result.daily_scan_log)
    scan["date"] = pd.to_datetime(scan["date"]).dt.normalize()
    scan = scan.drop_duplicates("date", keep="last").set_index("date")
    scan["exposure"] = exposure.reindex(scan.index)
    scan["base_regime"] = scan["regime"].str.split("|").str[0]
    print("\nExposure and slot usage by regime:")
    for rg, grp in scan.groupby("base_regime"):
        print(f"  {rg:<10} days={len(grp):>4}  mean_exposure={grp['exposure'].mean()*100:5.1f}%  "
              f"mean_open={grp['open_positions'].mean():.2f}  days_at_max={(grp['open_positions'] >= 3).mean()*100:4.1f}%")

    print("\n=== 2. PER-UNIT-EXPOSURE RETURN ===")
    daily_ret = equity.pct_change().dropna()
    exp_lag = exposure.shift(1).reindex(daily_ret.index).fillna(0)
    invested_days = exp_lag > 0.05
    ret_on_invested = (daily_ret[invested_days] / exp_lag[invested_days])
    ann_invested = ret_on_invested.mean() * 252 * 100
    print(f"Mean daily return / lagged exposure (annualized, days with >5% exposure): {ann_invested:+.2f}%")
    print("(compare: equal-weight universe +17.20%, Midcap150 ETF +21.05% — if this exceeds them,")
    print(" invested capital outperforms and the leak is idle capital, not picking)")

    print("\n=== 3. SLOT SATURATION & CUT-OFF ===")
    dec = pd.DataFrame(result.decision_log)
    if not dec.empty:
        dec["date"] = pd.to_datetime(dec["date"]).dt.normalize()
        print("decision_log columns:", list(dec.columns), " rows:", len(dec))
        print("signal value counts:", dec["signal"].value_counts().to_dict())
        print("selected value counts:", dec["selected"].value_counts().to_dict())
        qualified = dec[dec["signal"].astype(str).str.upper().isin(["BUY", "TRUE", "YES", "1"])]
        not_sel = qualified[~qualified["selected"].astype(str).str.upper().isin(["TRUE", "YES", "1", "BUY"])]
        sel = qualified[qualified["selected"].astype(str).str.upper().isin(["TRUE", "YES", "1", "BUY"])]
        print(f"Qualified signals: {len(qualified)}   selected: {len(sel)}   passed over: {len(not_sel)}")

        def adj_fwd(rows):
            vals = []
            for _, r in rows.iterrows():
                c = closes.get(r["symbol"])
                if c is None:
                    continue
                fr = fwd_return(c, r["date"], FWD_DAYS)
                fb = fwd_return(bench_close, r["date"], FWD_DAYS)
                if not (np.isnan(fr) or np.isnan(fb)):
                    vals.append(fr - fb)
            return np.array(vals)

        a_sel = adj_fwd(sel)
        a_skip = adj_fwd(not_sel)
        if len(a_sel):
            print(f"Fwd {FWD_DAYS}d benchmark-adjusted return, SELECTED : mean {a_sel.mean()*100:+.2f}%  median {np.median(a_sel)*100:+.2f}%  n={len(a_sel)}")
        if len(a_skip):
            print(f"Fwd {FWD_DAYS}d benchmark-adjusted return, PASSED-OVER: mean {a_skip.mean()*100:+.2f}%  median {np.median(a_skip)*100:+.2f}%  n={len(a_skip)}")
    else:
        print("decision_log EMPTY")

    print("\n=== 4. EXIT CONTINUATION (fwd 21d after exit, benchmark-adjusted) ===")
    rows = []
    for t in result.trades:
        if t.exit_date is None:
            continue
        c = closes.get(t.symbol)
        if c is None:
            continue
        fr = fwd_return(c, t.exit_date, FWD_DAYS)
        fb = fwd_return(bench_close, t.exit_date, FWD_DAYS)
        if np.isnan(fr) or np.isnan(fb):
            continue
        rows.append({"reason": t.exit_reason or "?", "adj": fr - fb,
                     "net_pnl": t.net_pnl or 0, "hold": t.hold_days or 0})
    ex = pd.DataFrame(rows)
    if not ex.empty:
        print(f"All exits: n={len(ex)}  mean adj fwd return {ex['adj'].mean()*100:+.2f}%  "
              f"median {ex['adj'].median()*100:+.2f}%")
        for rsn, grp in ex.groupby("reason"):
            print(f"  {rsn:<28} n={len(grp):>4}  mean_adj_fwd={grp['adj'].mean()*100:+6.2f}%  "
                  f"total_net_pnl=₹{grp['net_pnl'].sum():>12,.0f}")

    print("\n=== 5. FRICTION ===")
    charges = sum((t.charges or 0) for t in result.trades)
    gross = sum((t.gross_pnl or 0) for t in result.trades if t.gross_pnl is not None)
    net = sum((t.net_pnl or 0) for t in result.trades if t.net_pnl is not None)
    n_closed = sum(1 for t in result.trades if t.exit_date is not None)
    avg_equity = equity.mean()
    buy_notional = sum((t.entry_price or 0) * getattr(t, "quantity", getattr(t, "shares", 0))
                       for t in result.trades)
    print(f"Closed trades: {n_closed}   gross P&L: ₹{gross:,.0f}   net P&L: ₹{net:,.0f}   charges: ₹{charges:,.0f}")
    print(f"Charges drag: {charges / avg_equity / years * 100:.2f} pp/yr of avg equity")
    print(f"Buy notional total: ₹{buy_notional:,.0f}  -> turnover {buy_notional / avg_equity / years:.2f}x/yr")
    print(f"Slippage est. ({SLIPPAGE_FIXED_PCT*100:.2f}%/side x2): "
          f"{buy_notional / avg_equity / years * SLIPPAGE_FIXED_PCT * 2 * 100:.2f} pp/yr")

    print("\n=== 6. HOLDING-PERIOD P&L SPLIT ===")
    if not ex.empty:
        short = ex[ex["hold"] < 31]
        long_ = ex[ex["hold"] >= 31]
        print(f"<31d holds : n={len(short):>4}  total net P&L ₹{short['net_pnl'].sum():>12,.0f}")
        print(f">=31d holds: n={len(long_):>4}  total net P&L ₹{long_['net_pnl'].sum():>12,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
