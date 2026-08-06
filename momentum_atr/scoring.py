"""
Live score adapter for the momentum x ATR strategy -- ports
scripts/momentum_atr_experiment/engine.py:compute_scores() (FULL mode)
verbatim, substituting the backtest's static parquet read for a live
daily-bar fetch via data.fetcher.fetch_all(live_mode=True). Universe is
the real live universe (data.universe.get_all_symbols()), not the
backtest's 112-symbol parquet-coverage artifact.
"""
from typing import Dict, List, Tuple

import pandas as pd

MIN_HISTORY_DAYS = 50  # SMA50 warmup floor -- matches data.fetcher.fetch_all's own >=50 filter


def compute_live_scores(symbols: List[str]) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Returns (scores, closes): {symbol: latest score}, {symbol: latest close}.
    Only the most recent COMPLETE daily bar is used -- at a pre-market cron
    run that is necessarily yesterday's close, which is what makes the
    live daily decision naturally reproduce the backtest's
    decide-on-close-N/execute-on-open-N+1 lag (see momentum_atr/execution.py).
    Symbols with insufficient history or a NaN latest score are omitted."""
    from data.fetcher import fetch_all

    data = fetch_all(symbols, live_mode=True)
    scores: Dict[str, float] = {}
    closes: Dict[str, float] = {}
    for s, df in data.items():
        if len(df) < MIN_HISTORY_DAYS:
            continue
        df = df.sort_index()
        close = df["close"]
        sma50 = close.rolling(50).mean()
        momentum = (close - sma50) / sma50 * 100

        high_low = df["high"] - df["low"]
        high_close = (df["high"] - close.shift()).abs()
        low_close = (df["low"] - close.shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0 / 14, adjust=False).mean()
        atr_pct = atr / close * 100

        score = (momentum * atr_pct).where(momentum > 0, 0.0)
        last_score = score.iloc[-1]
        if pd.isna(last_score):
            continue
        scores[s] = float(last_score)
        closes[s] = float(close.iloc[-1])
    return scores, closes


def rank_symbols(scores: Dict[str, float]) -> List[str]:
    """Descending rank by score, score>0 only -- mirrors engine.py's
    default_rank(): s = s[s > 0].sort_values(ascending=False)."""
    eligible = {s: v for s, v in scores.items() if v > 0}
    return [s for s, _ in sorted(eligible.items(), key=lambda kv: kv[1], reverse=True)]
