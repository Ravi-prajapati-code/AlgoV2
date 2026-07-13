"""
Signal Regime Diagnostics — final-truth investigation.

Question: does RS ranking have a durable economic edge, or only episodic
usefulness? Reuses the exact 15 rolling windows + strong/weak/anti-predictive
labels from scripts/signal_stability_rolling.py (outputs/signal_stability_rolling.csv)
and computes 10 market-characteristic measures per window, treating the
ranking model in isolation (portfolio construction ignored entirely).

Measures (per window):
  1. cross_sectional_dispersion — mean daily cross-sectional std of stock returns
  2. market_breadth            — mean % of universe above EMA50
  3. sector_concentration      — mean HHI of sector weights in top-RS decile
  4. trend_persistence         — mean rank autocorrelation (t vs t+20d), Spearman
  5. mean_reversion            — mean lag-1 autocorrelation of daily returns
  6. volatility_regime         — annualized realized vol of benchmark index
  7. correlation_regime        — mean pairwise correlation of daily stock returns
  8. liquidity                 — mean daily turnover (close*volume, Rs Cr) across universe
  9. breadth_thrust            — max 10-day swing in market_breadth within window
  10. leadership_rotation      — mean month-over-month turnover of top-decile RS membership
"""
import sys
import numpy as np
import pandas as pd

from datetime import datetime, timedelta

sys.path.insert(0, ".")
from data.fetcher import fetch_all, fetch_index
from data.universe import get_sector
from scripts.signal_stability_rolling import ALL_SYMBOLS, MARKET_INDEX_SYMBOL, build_rs_panel

START = datetime.strptime("2022-01-01", "%Y-%m-%d").date()
END = datetime.today().date()


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def main():
    print("Fetching universe + index data (cached where available)...", file=sys.stderr)
    warmup_start = START - timedelta(days=200)
    lookback = (END - warmup_start).days
    data = fetch_all(ALL_SYMBOLS, lookback_days=lookback, start=warmup_start, end=END)
    idx_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=lookback, start=warmup_start, end=END)
    idx_close = idx_df["close"]
    if not isinstance(idx_close.index, pd.DatetimeIndex):
        idx_close.index = pd.to_datetime(idx_close.index)
    idx_close.index = idx_close.index.normalize()
    idx_ret = idx_close.pct_change()

    print("Rebuilding RS panel (rank + regime, same definition as stability script)...", file=sys.stderr)
    panel, _ = build_rs_panel(data, idx_df, 20)
    rs_rank_wide = panel.pivot_table(index="date", columns="symbol", values="rs_rank")

    # Close-price wide panel + daily returns
    close_wide = pd.DataFrame({
        sym: df["close"].pipe(lambda s: (s if isinstance(s.index, pd.DatetimeIndex)
                                          else s.set_axis(pd.to_datetime(s.index))))
        for sym, df in data.items() if not df.empty
    })
    close_wide.index = pd.to_datetime(close_wide.index).normalize()
    close_wide = close_wide.sort_index()
    ret_wide = close_wide.pct_change()

    # Volume wide panel (for liquidity)
    vol_wide = pd.DataFrame({
        sym: df["volume"].pipe(lambda s: (s if isinstance(s.index, pd.DatetimeIndex)
                                           else s.set_axis(pd.to_datetime(s.index))))
        for sym, df in data.items() if not df.empty and "volume" in df.columns
    })
    vol_wide.index = pd.to_datetime(vol_wide.index).normalize()
    vol_wide = vol_wide.sort_index()
    turnover_wide = (close_wide * vol_wide) / 1e7  # Rs Cr

    ema50_wide = close_wide.apply(lambda s: ema(s.dropna(), 50)).reindex(close_wide.index)
    breadth = (close_wide > ema50_wide).sum(axis=1) / close_wide.notna().sum(axis=1) * 100

    sector_map = {sym: (get_sector(sym) or "UNKNOWN") for sym in ALL_SYMBOLS}

    labels_df = pd.read_csv("outputs/signal_stability_rolling.csv")
    labels_df["window_start"] = pd.to_datetime(labels_df["window_start"])
    labels_df["window_end"] = pd.to_datetime(labels_df["window_end"])

    rows = []
    for _, w in labels_df.iterrows():
        ws, we = w["window_start"], w["window_end"]
        ic, p = w["mean_ic"], w["permutation_p_ic"]
        if p < 0.10 and ic > 0:
            bucket = "STRONG"
        elif ic < 0 and p >= 0.90:
            bucket = "ANTI-PREDICTIVE"
        else:
            bucket = "WEAK"

        wret = ret_wide.loc[ws:we]
        wrank = rs_rank_wide.loc[ws:we]
        wbreadth = breadth.loc[ws:we]
        widx_ret = idx_ret.loc[ws:we]
        wturnover = turnover_wide.loc[ws:we]

        # 1. Cross-sectional dispersion
        disp = wret.std(axis=1, skipna=True).mean()

        # 2. Market breadth
        mbreadth = wbreadth.mean()

        # 3. Sector concentration (HHI of sector weights in top RS decile, mean across days)
        hhi_vals = []
        for dt in wrank.index:
            row = wrank.loc[dt].dropna()
            if len(row) < 10:
                continue
            top_decile = row[row >= row.quantile(0.90)].index
            secs = pd.Series([sector_map.get(s, "UNKNOWN") for s in top_decile])
            weights = secs.value_counts(normalize=True)
            hhi_vals.append((weights ** 2).sum())
        sector_hhi = float(np.mean(hhi_vals)) if hhi_vals else float("nan")

        # 4. Trend persistence: rank(t) vs rank(t+20) Spearman, sampled every 5 days
        pers_vals = []
        idx_list = wrank.index
        for i in range(0, len(idx_list) - 20, 5):
            a = wrank.loc[idx_list[i]]
            b = wrank.loc[idx_list[i + 20]]
            both = pd.concat([a, b], axis=1).dropna()
            if len(both) >= 10:
                pers_vals.append(both.iloc[:, 0].corr(both.iloc[:, 1], method="spearman"))
        trend_persistence = float(np.mean(pers_vals)) if pers_vals else float("nan")

        # 5. Mean reversion: lag-1 autocorrelation of daily returns, mean across symbols
        ac_vals = []
        for sym in wret.columns:
            s = wret[sym].dropna()
            if len(s) > 30:
                ac_vals.append(s.autocorr(lag=1))
        mean_reversion = float(np.nanmean(ac_vals)) if ac_vals else float("nan")

        # 6. Volatility regime: annualized realized vol of index
        vol_regime = widx_ret.std() * np.sqrt(252) * 100

        # 7. Correlation regime: mean pairwise correlation of daily stock returns
        sub = wret.dropna(axis=1, thresh=int(len(wret) * 0.8))
        if sub.shape[1] > 5:
            corr = sub.corr()
            mask = ~np.eye(len(corr), dtype=bool)
            corr_regime = corr.values[mask].mean()
        else:
            corr_regime = float("nan")

        # 8. Liquidity: mean daily turnover per stock (Rs Cr)
        liquidity = wturnover.mean(axis=1, skipna=True).mean()

        # 9. Breadth thrust: max abs 10-day change in breadth
        breadth_10d_chg = wbreadth.diff(10).abs()
        breadth_thrust = breadth_10d_chg.max()

        # 10. Leadership rotation: month-over-month turnover of top-decile RS membership
        month_ends = wrank.resample("ME").last().dropna(how="all")
        rot_vals = []
        prev_set = None
        for dt in month_ends.index:
            row = wrank.loc[:dt].iloc[-1].dropna()
            top = set(row[row >= row.quantile(0.90)].index)
            if prev_set is not None and len(prev_set) > 0:
                rot_vals.append(1 - len(top & prev_set) / len(prev_set))
            prev_set = top
        leadership_rotation = float(np.mean(rot_vals)) * 100 if rot_vals else float("nan")

        rows.append({
            "window": f"{ws.date()}→{we.date()}",
            "bucket": bucket,
            "ic": ic, "p": p,
            "cross_sectional_dispersion_pct": disp * 100,
            "market_breadth_pct": mbreadth,
            "sector_concentration_hhi": sector_hhi,
            "trend_persistence": trend_persistence,
            "mean_reversion_ac1": mean_reversion,
            "volatility_regime_pct": vol_regime,
            "correlation_regime": corr_regime,
            "liquidity_cr": liquidity,
            "breadth_thrust_pct": breadth_thrust,
            "leadership_rotation_pct": leadership_rotation,
        })
        print(f"  [{ws.date()}→{we.date()}] {bucket:16s} disp={disp*100:.2f}% breadth={mbreadth:.0f}% "
              f"sectHHI={sector_hhi:.3f} persist={trend_persistence:+.2f} meanrev={mean_reversion:+.3f} "
              f"vol={vol_regime:.0f}% corr={corr_regime:+.2f} liq={liquidity:.0f}Cr "
              f"thrust={breadth_thrust:.0f}pp rotation={leadership_rotation:.0f}%", file=sys.stderr)

    out = pd.DataFrame(rows)
    out.to_csv("outputs/signal_regime_diagnostics.csv", index=False)
    print("\nSaved outputs/signal_regime_diagnostics.csv", file=sys.stderr)

    print("\n=== BUCKET MEANS ===", file=sys.stderr)
    print(out.groupby("bucket")[[
        "cross_sectional_dispersion_pct", "market_breadth_pct", "sector_concentration_hhi",
        "trend_persistence", "mean_reversion_ac1", "volatility_regime_pct",
        "correlation_regime", "liquidity_cr", "breadth_thrust_pct", "leadership_rotation_pct",
    ]].mean().T.to_string(), file=sys.stderr)


if __name__ == "__main__":
    main()
