"""
Lean Indicator Engine — calculates Trend, Momentum (RS), and Liquidity.
Enhanced for "Minimum Data" robustness.
"""

import pandas as pd
import numpy as np
from typing import Optional, Dict


def _detect_vcp(df: pd.DataFrame) -> dict:
    """Detect Volatility Contraction Pattern (VCP).

    Looks for ≥2 contracting swing ranges (each ≤65% of prior) plus volume
    dry-up (20d avg vol ≤ 60% of 100d avg vol), excluding today's candle from
    volume averages to avoid RVOL contamination.
    """
    empty = {"vcp_detected": False, "vcp_pivot": 0.0, "vol_dry_up": False}
    if len(df) < 60:
        return empty

    highs = df["high"].values
    lows  = df["low"].values
    n = 5  # bars each side for local swing high

    swing_highs = []
    for i in range(n, len(highs) - n):
        if highs[i] == highs[i - n : i + n + 1].max():
            swing_highs.append((i, highs[i]))

    if len(swing_highs) < 3:
        return empty

    last_3 = swing_highs[-3:]
    ranges = []
    for k in range(len(last_3) - 1):
        idx1, h1 = last_3[k]
        idx2, _  = last_3[k + 1]
        low_between = lows[idx1 : idx2 + 1].min()
        ranges.append(h1 - low_between)

    if any(r <= 0 for r in ranges):
        return empty

    contracting = all(ranges[i + 1] <= 0.65 * ranges[i] for i in range(len(ranges) - 1))

    vol = df["volume"]
    vol_20  = vol.iloc[-21:-1].mean()   # exclude today to avoid RVOL contamination
    vol_100 = vol.iloc[-101:-1].mean() if len(vol) >= 101 else vol.iloc[:-1].mean()
    vol_dry_up = bool(vol_20 <= 0.60 * vol_100) if vol_100 > 0 else False

    vcp_pivot    = float(last_3[-1][1])
    vcp_detected = contracting and vol_dry_up

    return {"vcp_detected": vcp_detected, "vcp_pivot": vcp_pivot, "vol_dry_up": vol_dry_up}


def compute_indicators(df: pd.DataFrame, symbol: str = "", rs_metrics: Optional[dict] = None) -> Optional[dict]:
    """
    Calculates lean indicators. Returns None only if data is critically low (< 20 bars).
    """
    if df is None or len(df) < 20:
        return None

    close = df['close']
    length = len(df)
    
    # 1. Trend (EMA & Supertrend)
    from indicators.trend import compute_trend
    trend = compute_trend(df)
    
    last_ema_20 = trend["ema_fast"]
    last_ema_slow_legacy = trend["ema_slow"]  # EMA(EMA_SLOW), default 100 — for ML legacy keys only
    supertrend = trend["supertrend"]
    st_direction = trend["st_direction"]

    # ema_50 must be an independent EMA(50), matching backtest/engine.py's
    # `close.ewm(span=50)`. Previously this key held trend["ema_slow"]
    # (EMA(EMA_SLOW), default 100) — same period as ema_100, just rounded
    # differently, so the entry gate's "ema_50 > ema_100" trend-alignment
    # check was comparing a value to its own rounding noise, not two real
    # timeframes. docs/31_EMA50_Mislabel_Fix.md.
    ema_50  = close.ewm(span=50,  adjust=False, min_periods=1).mean()
    ema_100 = close.ewm(span=100, adjust=False, min_periods=1).mean()
    ema_150 = close.ewm(span=150, adjust=False, min_periods=1).mean()
    ema_200 = close.ewm(span=200, adjust=False, min_periods=1).mean()
    last_ema_50  = ema_50.iloc[-1]
    last_ema_100 = ema_100.iloc[-1]
    last_ema_150 = ema_150.iloc[-1]
    last_ema_200 = ema_200.iloc[-1]

    # Sweepable entry/exit EMA periods (docs/31) — reuse the fixed series above
    # when the configured period matches one already computed, to avoid a
    # duplicate ewm() call for the (common) case of running at defaults.
    from config.settings import ENTRY_EMA_MEDIUM, ENTRY_EMA_LONG, EXIT_TREND_EMA, ADX_TREND_THRESHOLD
    _period_series = {20: last_ema_20, 50: ema_50, 100: ema_100, 150: ema_150, 200: ema_200}

    def _ema_at(period: int):
        cached = _period_series.get(period)
        if cached is not None:
            return cached.iloc[-1] if hasattr(cached, "iloc") else cached
        return close.ewm(span=period, adjust=False, min_periods=1).mean().iloc[-1]

    def _ema_series_at(period: int):
        cached = _period_series.get(period)
        if cached is not None:
            return cached
        return close.ewm(span=period, adjust=False, min_periods=1).mean()

    last_ema_entry_med  = _ema_at(ENTRY_EMA_MEDIUM)
    last_ema_entry_long = _ema_at(ENTRY_EMA_LONG)
    last_ema_exit_trend = _ema_at(EXIT_TREND_EMA)

    # 2. Volatility (ATR & Bollinger Bands) — Wilder's EMA ATR, matches TradingView
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr_series = tr.ewm(alpha=1.0 / 14, adjust=False).mean()
    atr = atr_series.iloc[-1]
    
    sma20 = close.rolling(window=min(20, length)).mean()
    std20 = close.rolling(window=min(20, length)).std()
    bb_upper = sma20 + (std20 * 2)
    bb_lower = sma20 - (std20 * 2)
    
    last_price = close.iloc[-1]
    bb_range = (bb_upper.iloc[-1] - bb_lower.iloc[-1])
    bb_pct = (last_price - bb_lower.iloc[-1]) / bb_range if bb_range > 0 else 0.5
    
    # 3. RSI Calculation — Wilder's EMA (alpha=1/period), matches TradingView
    delta = close.diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1.0 / 14, adjust=False).mean()
    avg_loss = (-delta.clip(upper=0)).ewm(alpha=1.0 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi_series = 100 - (100 / (1 + rs))
    
    rsi = rsi_series.iloc[-1] if not rsi_series.empty else 50
    rsi_prev = rsi_series.iloc[-2] if len(rsi_series) > 1 else rsi
    
    # 4. MACD
    exp1 = close.ewm(span=12, adjust=False).mean()
    exp2 = close.ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    
    last_hist = hist.iloc[-1]
    prev_hist = hist.iloc[-2] if len(hist) > 1 else last_hist
    
    # 5. Liquidity & Volume
    daily_turnover = df['close'] * df['volume']
    avg_turnover = daily_turnover.rolling(window=min(20, length)).mean().iloc[-1]
    
    vol_avg = df['volume'].rolling(window=min(20, length)).mean().iloc[-1]
    vol_today = df['volume'].iloc[-1]
    vol_ratio = vol_today / vol_avg if vol_avg > 0 else 1.0
    
    # 6. Breakout levels (using PREVIOUS days to avoid current-day bias)
    high_20d = df['high'].shift(1).rolling(window=min(20, length-1)).max().iloc[-1] if length > 1 else df['high'].iloc[-1]
    week52_high = df['high'].rolling(window=min(252, length)).max().iloc[-1]
    
    # 10-day performance %
    perf_10d = 0.0
    if length >= 11:
        perf_10d = (close.iloc[-1] / close.iloc[-11] - 1) * 100

    # VCP pattern detection
    vcp = _detect_vcp(df)

    # ADX — trend strength (14-period), Wilder's smoothing (alpha=1/14) matches TradingView
    adx_val = 0.0
    adx_series = None
    if length >= 15:
        high_s = df['high']
        low_s  = df['low']
        plus_dm  = (high_s.diff().clip(lower=0)).where(high_s.diff() > (-low_s.diff()).clip(lower=0), 0.0)
        minus_dm = ((-low_s.diff()).clip(lower=0)).where((-low_s.diff()) > high_s.diff().clip(lower=0), 0.0)
        atr14    = tr.ewm(alpha=1.0 / 14, adjust=False).mean()
        plus_di  = 100 * plus_dm.ewm(alpha=1.0 / 14, adjust=False).mean() / atr14.replace(0, np.nan)
        minus_di = 100 * minus_dm.ewm(alpha=1.0 / 14, adjust=False).mean() / atr14.replace(0, np.nan)
        dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0)
        adx_series = dx.ewm(alpha=1.0 / 14, adjust=False).mean()
        adx_val  = float(adx_series.iloc[-1])

    # Bear-regime crash-protection stock-level trend-confirm streak: how many
    # consecutive days (ending today) has this stock cleared the same
    # EMA-alignment/SuperTrend/ADX bar strategy/entry.py's check_entry() uses.
    # A single-day version of this check was gate-REJECTED (docs/24) --
    # CRASH_PROTECTION_CONFIRM_DAYS requires it to hold for several days
    # running before strategy/signals.py trusts it, same hysteresis idea as
    # REGIME_SWITCH_DAYS/BULL_RECOVERY_DAYS but at the stock level. Computed
    # once here (vectorized) so both the exit-override and entry-override paths
    # can use it without any per-day stateful tracking.
    bear_trend_confirm_days = 0
    if adx_series is not None:
        ema_med_series  = _ema_series_at(ENTRY_EMA_MEDIUM)
        ema_long_series = _ema_series_at(ENTRY_EMA_LONG)
        st_dir_series   = trend.get("st_direction_series")
        if st_dir_series is not None:
            n = min(len(close), len(ema_med_series), len(ema_long_series),
                    len(adx_series), len(st_dir_series))
            intact = (
                (close.iloc[-n:].values > ema_med_series.iloc[-n:].values)
                & (ema_med_series.iloc[-n:].values > ema_long_series.iloc[-n:].values)
                & (st_dir_series.iloc[-n:].isin([1, "up"]).values)
                & (adx_series.iloc[-n:].values >= ADX_TREND_THRESHOLD)
            )
            for v in intact[::-1]:
                if v:
                    bear_trend_confirm_days += 1
                else:
                    break

    return {
        "symbol":     symbol,
        "close":      last_price,
        "high":       df['high'].iloc[-1],
        "low":        df['low'].iloc[-1],
        "ema_fast":   last_ema_20,  # Legacy name for ML
        "ema_slow":   last_ema_slow_legacy,  # Legacy name for ML — preserves pre-fix EMA(EMA_SLOW) semantics, unrelated to the ema_50 fix below
        "ema_20":     last_ema_20,
        "ema_50":     last_ema_50,
        "supertrend": supertrend,
        "st_direction": st_direction,
        "ema_100":    last_ema_100,
        "ema_150":    last_ema_150,
        "ema_200":    last_ema_200,
        "ema_entry_med":   last_ema_entry_med,
        "ema_entry_long":  last_ema_entry_long,
        "ema_exit_trend":  last_ema_exit_trend,
        "atr":        atr,
        "atr_pct":    (atr / last_price * 100) if last_price > 0 else 0,
        "rsi":        round(rsi, 2),
        "rsi_prev":   round(rsi_prev, 2),
        "macd_hist":  round(last_hist, 2),
        "macd_hist_prev": round(prev_hist, 2),
        "macd_bullish":   last_hist > 0,
        "macd_turning_up": last_hist > prev_hist,
        "bb_pct":     round(bb_pct, 4),
        "above_bb_lower": last_price > bb_lower.iloc[-1],
        "above_bb_mid":   last_price > sma20.iloc[-1],
        "turnover":   avg_turnover,
        "rs_ratio":        rs_metrics.get("rs_ratio",        0)   if rs_metrics else 0,
        "rs_ratio_1m":     rs_metrics.get("rs_ratio_1m",     0)   if rs_metrics else 0,
        "rs_rank":         rs_metrics.get("rs_rank",         0)   if rs_metrics else 0,
        "composite_rank":  rs_metrics.get("composite_rank",  0)   if rs_metrics else 0,
        "beta":            rs_metrics.get("beta",            1.0) if rs_metrics else 1.0,
        "vol_avg":    vol_avg,
        "vol_ratio":  round(vol_ratio, 2),
        "vol_spike":  vol_ratio >= 1.5,
        "high_20d":   high_20d,
        "week52_high": week52_high,
        "perf_10d":   round(perf_10d, 2),
        "adx":         round(adx_val, 2),
        "bear_trend_confirm_days": bear_trend_confirm_days,
        "vcp_detected": vcp["vcp_detected"],
        "vcp_pivot":    vcp["vcp_pivot"],
        "vol_dry_up":   vcp["vol_dry_up"],
        "data_points": length
    }



def compute_all(data: Dict[str, pd.DataFrame], rs_data: Dict[str, dict] = None) -> Dict[str, dict]:
    results = {}
    for symbol, df in data.items():
        ind = compute_indicators(df, symbol=symbol, rs_metrics=rs_data.get(symbol) if rs_data else None)
        if ind:
            results[symbol] = ind
    return results
