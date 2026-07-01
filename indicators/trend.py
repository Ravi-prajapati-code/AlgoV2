"""Trend indicators: EMA, crossover detection, and Supertrend."""

import pandas as pd
import numpy as np
from config.settings import EMA_FAST, EMA_SLOW, EMA_CROSSOVER_LOOKBACK


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """
    Calculates Supertrend (Trend following + Volatility trailing stop).
    """
    if len(df) < period:
        return pd.DataFrame({'supertrend': [0.0]*len(df), 'direction': [0]*len(df)}, index=df.index)

    high = df['high']
    low = df['low']
    close = df['close']
    
    # ATR calculation — Wilder's EMA (alpha=1/period), matches TradingView Supertrend
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    
    # Basic Upper/Lower bands
    hl2 = (high + low) / 2
    basic_ub = hl2 + (multiplier * atr)
    basic_lb = hl2 - (multiplier * atr)
    
    # Final Upper/Lower bands
    final_ub = pd.Series(0.0, index=df.index)
    final_lb = pd.Series(0.0, index=df.index)
    
    for i in range(len(df)):
        if i == 0:
            final_ub.iloc[i] = basic_ub.iloc[i]
            final_lb.iloc[i] = basic_lb.iloc[i]
        else:
            # Upper Band
            if basic_ub.iloc[i] < final_ub.iloc[i-1] or close.iloc[i-1] > final_ub.iloc[i-1]:
                final_ub.iloc[i] = basic_ub.iloc[i]
            else:
                final_ub.iloc[i] = final_ub.iloc[i-1]
                
            # Lower Band
            if basic_lb.iloc[i] > final_lb.iloc[i-1] or close.iloc[i-1] < final_lb.iloc[i-1]:
                final_lb.iloc[i] = basic_lb.iloc[i]
            else:
                final_lb.iloc[i] = final_lb.iloc[i-1]
                
    # Supertrend direction
    st = pd.Series(0.0, index=df.index)
    direction = pd.Series(0, index=df.index) # 1 for up, -1 for down
    
    curr_dir = -1
    for i in range(len(df)):
        if i == 0:
            st.iloc[i] = final_ub.iloc[i]
            direction.iloc[i] = -1
        else:
            if curr_dir == 1:
                if close.iloc[i] < final_lb.iloc[i]:
                    curr_dir = -1
                    direction.iloc[i] = -1
                    st.iloc[i] = final_ub.iloc[i]
                else:
                    curr_dir = 1
                    direction.iloc[i] = 1
                    st.iloc[i] = final_lb.iloc[i]
            else:
                if close.iloc[i] > final_ub.iloc[i]:
                    curr_dir = 1
                    direction.iloc[i] = 1
                    st.iloc[i] = final_lb.iloc[i]
                else:
                    curr_dir = -1
                    direction.iloc[i] = -1
                    st.iloc[i] = final_ub.iloc[i]
                    
    return pd.DataFrame({
        'supertrend': st,
        'direction': direction
    }, index=df.index)


def compute_trend(df: pd.DataFrame) -> dict:
    """
    Returns:
        ema_fast        — last EMA(20) value
        ema_slow        — last EMA(50) value
        above_ema_fast  — price above EMA20?
        uptrend         — EMA20 > EMA50?
        golden_cross    — EMA20 crossed above EMA50 within last N days?
        death_cross     — EMA20 crossed below EMA50 within last N days?
        supertrend      — last Supertrend value
        st_direction    — last Supertrend direction (1/-1)
        ema_fast_series — full series (for composite use)
        ema_slow_series — full series
    """
    close = df["close"]
    ema_f = ema(close, EMA_FAST)
    ema_s = ema(close, EMA_SLOW)
    
    st_df = compute_supertrend(df)

    last_price = float(close.iloc[-1])
    last_ema_f = float(ema_f.iloc[-1])
    last_ema_s = float(ema_s.iloc[-1])
    last_st = float(st_df['supertrend'].iloc[-1])
    last_dir = int(st_df['direction'].iloc[-1])

    # Detect crossovers in the last N candles
    window = min(EMA_CROSSOVER_LOOKBACK, len(ema_f) - 1)
    golden_cross = False
    death_cross = False
    for i in range(-window, 0):
        prev_diff = ema_f.iloc[i - 1] - ema_s.iloc[i - 1]
        curr_diff = ema_f.iloc[i] - ema_s.iloc[i]
        if prev_diff < 0 and curr_diff >= 0:
            golden_cross = True
        if prev_diff > 0 and curr_diff <= 0:
            death_cross = True

    return {
        "ema_fast": round(last_ema_f, 2),
        "ema_slow": round(last_ema_s, 2),
        "above_ema_fast": last_price >= last_ema_f,
        "uptrend": last_ema_f > last_ema_s,
        "golden_cross": golden_cross,
        "death_cross": death_cross,
        "supertrend": round(last_st, 2),
        "st_direction": last_dir,
        "ema_fast_series": ema_f,
        "ema_slow_series": ema_s,
    }
