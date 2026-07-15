"""
Market breadth — % of the universe trading above its EMA(50).

Mirrors the formula already validated (offline-only, unused in production)
in scripts/signal_regime_diagnostics.py:77-78. This module is the wired-in
version, used to corroborate strategy/regime.py::detect_regime()'s
index-only signal.
"""

import pandas as pd
from typing import Dict, Optional

from indicators.trend import ema


def compute_breadth_series(stock_data: Dict[str, pd.DataFrame], ema_span: int = 50) -> pd.Series:
    """
    % of symbols with close > EMA(ema_span), per date, across the given universe.
    stock_data: {symbol: daily OHLC DataFrame with a 'close' column}.
    """
    close_wide = pd.DataFrame({
        symbol: df["close"] for symbol, df in stock_data.items() if not df.empty
    })
    if close_wide.empty:
        return pd.Series(dtype=float)

    close_wide = close_wide.sort_index()
    ema_wide = close_wide.apply(lambda s: ema(s.dropna(), ema_span)).reindex(close_wide.index)
    return (close_wide > ema_wide).sum(axis=1) / close_wide.notna().sum(axis=1) * 100


def compute_breadth(stock_data: Dict[str, pd.DataFrame], ema_span: int = 50) -> Optional[float]:
    """Latest breadth reading (%), or None if no usable data."""
    series = compute_breadth_series(stock_data, ema_span)
    if series.empty:
        return None
    return float(series.iloc[-1])
