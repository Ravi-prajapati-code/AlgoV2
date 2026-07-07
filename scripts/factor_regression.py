"""
Factor regression (docs/16.6 Issue 7): does any alpha survive once known factor exposures are
controlled for?

Regresses strategy daily excess returns on (1) Midcap 150 ETF alone (CAPM re-check on the common
window) and (2) Midcap 150 ETF + Nifty200 Momentum 30 ETF (MOM30IETF.NS, live since 2022-08 —
an investable momentum-factor proxy). If the two-factor alpha is indistinguishable from zero, the
strategy's return stream is replicable with passive index products and contains no residual
selection alpha. Plain OLS standard errors (no HAC correction) — stated limitation.
"""
import os
import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.fetcher import fetch_symbol
from scripts.benchmark_attribution import (
    run_backtest_equity_curve, RISK_FREE_RATE_ANNUAL, TRADING_DAYS,
)

START = "2022-01-01"
END = str(date.today())


def daily_returns(ticker: str, start: date, end: date) -> pd.Series:
    df = fetch_symbol(ticker, start=start, end=end)
    df.index = pd.to_datetime(df.index).normalize()
    return df["close"].sort_index().pct_change().dropna()


def ols(y: np.ndarray, X: np.ndarray):
    """Returns (coefs, t_stats, r2). X should NOT include the intercept column."""
    Xc = np.column_stack([np.ones(len(y)), X])
    coefs, _, _, _ = np.linalg.lstsq(Xc, y, rcond=None)
    resid = y - Xc @ coefs
    dof = len(y) - Xc.shape[1]
    sigma2 = resid @ resid / dof
    cov = sigma2 * np.linalg.inv(Xc.T @ Xc)
    t_stats = coefs / np.sqrt(np.diag(cov))
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - (resid @ resid) / ss_tot
    return coefs, t_stats, r2


def main():
    start = datetime.strptime(START, "%Y-%m-%d").date()
    end = datetime.strptime(END, "%Y-%m-%d").date()

    print("Running baseline backtest for strategy equity curve...", flush=True)
    equity, _ = run_backtest_equity_curve()
    strat_ret = equity.pct_change().dropna()

    print("Fetching factor proxies...", flush=True)
    midcap = daily_returns("MIDCAPETF.NS", start - timedelta(days=30), end)
    mom30 = daily_returns("MOM30IETF.NS", start - timedelta(days=30), end)

    rf_daily = RISK_FREE_RATE_ANNUAL / TRADING_DAYS
    df = pd.concat(
        [strat_ret - rf_daily, midcap - rf_daily, mom30 - rf_daily],
        axis=1, join="inner",
    ).dropna()
    df.columns = ["strat", "midcap", "mom30"]
    print(f"Common window: {df.index[0].date()} -> {df.index[-1].date()}, n={len(df)}")
    print(f"Factor correlation midcap~mom30: {df['midcap'].corr(df['mom30']):.3f}\n")

    y = df["strat"].values

    coefs, t, r2 = ols(y, df[["midcap"]].values)
    print("(1) One-factor: strat ~ midcap")
    print(f"    alpha = {coefs[0] * TRADING_DAYS * 100:+.2f}%/yr  (t={t[0]:.2f})")
    print(f"    beta_midcap = {coefs[1]:.3f}  (t={t[1]:.2f})   R2={r2:.3f}\n")

    coefs, t, r2 = ols(y, df[["midcap", "mom30"]].values)
    print("(2) Two-factor: strat ~ midcap + momentum30")
    print(f"    alpha = {coefs[0] * TRADING_DAYS * 100:+.2f}%/yr  (t={t[0]:.2f})")
    print(f"    beta_midcap = {coefs[1]:.3f}  (t={t[1]:.2f})")
    print(f"    beta_mom30  = {coefs[2]:.3f}  (t={t[2]:.2f})   R2={r2:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
