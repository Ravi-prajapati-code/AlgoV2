"""
Relative Strength (RS) and Beta calculator.
Focuses on identifying high-momentum, high-beta leaders.
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict

logger = logging.getLogger(__name__)

def compute_rs_for_all(stock_data: Dict[str, pd.DataFrame], index_df: pd.DataFrame) -> Dict[str, dict]:
    """
    Computes RS Rank and Beta for the universe.
    Beta > 1.0 means the stock moves more than the market.
    """
    if index_df.empty:
        return {}

    results = {}
    # Use 1-year lookback for Beta if available
    idx_returns = index_df['close'].pct_change().dropna()
    
    raw_metrics = []
    
    for symbol, df in stock_data.items():
        if df.empty or len(df) < 60:
            continue
            
        # Align dates
        common_idx = df.index.intersection(idx_returns.index)
        if len(common_idx) < 60:
            continue
            
        s_close = df.loc[common_idx, 'close']
        s_returns = s_close.pct_change().dropna()
        i_returns = idx_returns.loc[s_returns.index]
        
        # 1. RS Calculation (Price relative to Index)
        rs_line = (s_close / index_df.loc[common_idx, 'close'])
        # RS Ratio 6-month: current RS vs 126-day average (trend context)
        rs_ratio = (rs_line.iloc[-1] / rs_line.rolling(window=min(126, len(rs_line))).mean().iloc[-1]) * 100
        # RS Ratio 1-month: current RS vs 21-day average (acceleration signal)
        rs_ratio_1m = (rs_line.iloc[-1] / rs_line.rolling(window=min(21, len(rs_line))).mean().iloc[-1]) * 100

        # 2. Beta Calculation (Covariance / Variance)
        # We use the last 120 trading days (~6 months) for Beta to capture recent volatility
        lookback = min(120, len(s_returns))
        s_ret_slice = s_returns.iloc[-lookback:]
        i_ret_slice = i_returns.iloc[-lookback:]

        covariance = np.cov(s_ret_slice, i_ret_slice)[0][1]
        variance = np.var(i_ret_slice, ddof=1)
        beta = covariance / variance if variance > 0 else 1.0

        # 3. ATR% for composite scoring
        df_sym = df.loc[common_idx]
        tr = pd.concat([
            df_sym['high'] - df_sym['low'],
            (df_sym['high'] - df_sym['close'].shift()).abs(),
            (df_sym['low']  - df_sym['close'].shift()).abs(),
        ], axis=1).max(axis=1)
        last_atr = tr.ewm(alpha=1/14, adjust=False).mean().iloc[-1]
        last_price = df_sym['close'].iloc[-1]
        atr_pct = (last_atr / last_price * 100) if last_price > 0 else 0.0

        raw_metrics.append({
            "symbol":     symbol,
            "rs_ratio":   rs_ratio,
            "rs_ratio_1m": rs_ratio_1m,
            "beta":       beta,
            "atr_pct":    atr_pct,
        })

    if not raw_metrics:
        return {}

    df_metrics = pd.DataFrame(raw_metrics)
    df_metrics['rs_rank'] = df_metrics['rs_ratio'].rank(pct=True) * 100
    # composite_score = RS rank × ATR% — rewards high momentum + high volatility leaders
    df_metrics['composite_score'] = df_metrics['rs_rank'] * df_metrics['atr_pct']
    df_metrics['composite_rank']  = df_metrics['composite_score'].rank(pct=True) * 100

    for _, row in df_metrics.iterrows():
        results[row['symbol']] = {
            "rs_ratio":       row['rs_ratio'],
            "rs_ratio_1m":    row['rs_ratio_1m'],
            "rs_rank":        row['rs_rank'],
            "composite_rank": row['composite_rank'],
            "beta":           round(row['beta'], 2),
        }

    return results


