from typing import Tuple, Dict
from config.settings import (
    VOLUME_SPIKE_MULTIPLIER, RS_THRESHOLD,
    RSI_BUY_MIN, RSI_BUY_MAX, MIN_VOLUME_RATIO,
    TREND_GATE_200_ENABLED,
)

MIN_DAILY_TURNOVER = 20_000_000  # ₹2 Cr/day minimum liquidity

def check_entry(
    ind: Dict,
    symbol: str = "",
    regime: str = "BULL",
    index_confirming: bool = True
) -> Tuple[bool, str]:
    close = float(ind.get("close", 0))
    rs_rank = float(ind.get("rs_rank", 0) or 0)
    ema_50 = float(ind.get("ema_50", 0))
    ema_100 = float(ind.get("ema_100", 0))
    high_20d = float(ind.get("high_20d", 0))
    rsi = float(ind.get("rsi", 0))

    if rs_rank < RS_THRESHOLD:
        return False, f"Low RS ({rs_rank:.1f})"

    # 0. Long-term trend gate (optional): price must be above 200-day EMA
    if TREND_GATE_200_ENABLED:
        ema_200 = float(ind.get("ema_200", 0))
        if ema_200 > 0 and close < ema_200:
            return False, f"Below 200 EMA ({close:.1f} < {ema_200:.1f})"

    # 1. RSI Check
    if rsi < RSI_BUY_MIN or rsi >= RSI_BUY_MAX:
        return False, f"RSI out of bounds ({rsi:.1f})"

    # 2. Breakout Check — VCP or standard near-high
    vol_ratio    = float(ind.get("vol_ratio", 1.0))
    vcp_detected = bool(ind.get("vcp_detected", False))
    vcp_pivot    = float(ind.get("vcp_pivot", 0))
    if vcp_detected and vcp_pivot > 0:
        # VCP: entry within 2% below last contraction pivot + RVOL ≥ 1.5
        if close < vcp_pivot * 0.98:
            return False, f"VCP: not at pivot ({close:.1f} vs {vcp_pivot:.1f})"
        if vol_ratio < 1.5:
            return False, f"VCP: low RVOL ({vol_ratio:.1f}x < 1.5x)"
    else:
        # Standard: within 5% of 20-day high
        if close < high_20d * 0.95:
            return False, f"Not at Breakout (Price {close:.1f} < 95% of 20d high {high_20d:.1f})"

    # 3. Recent Momentum Check: Must have at least 2% gain in last 10 days
    perf_10d = float(ind.get("perf_10d", 0))
    if perf_10d < 2.0:
        return False, f"Weak recent momentum ({perf_10d:.1f}% in 10d)"

    # 4. Overextension Check: Price shouldn't be too far from EMA 50
    if ema_50 > 0:
        extension = (close - ema_50) / ema_50
        if extension > 0.15:
            return False, f"Overextended ({extension:.1%})"

    # 5. Volatility Check: Filter out extremely high-volatility moves
    atr_pct = float(ind.get("atr_pct", 0))
    if atr_pct >= 10.0:
        return False, f"High Volatility (ATR:{atr_pct:.1f}%)"

    # 6. Volume Confirmation: Must show institutional participation
    if vol_ratio < MIN_VOLUME_RATIO:
        return False, f"Weak volume conviction ({vol_ratio:.1f}x < {MIN_VOLUME_RATIO}x)"

    # 7. Minimum daily turnover: ₹2 Cr/day for execution safety
    turnover = float(ind.get("turnover", 0))
    if 0 < turnover < MIN_DAILY_TURNOVER:
        return False, f"Low liquidity (₹{turnover/1e6:.1f}M < ₹{MIN_DAILY_TURNOVER/1e6:.0f}M)"

    # 8. Institutional Trend Strength: EMA, SuperTrend, ADX alignment
    adx = float(ind.get("adx", 0))
    st_dir = ind.get("st_direction", "down")
    
    if not (close > ema_50 and ema_50 > ema_100):
        return False, "Weak Trend (Price or EMAs not aligned)"
        
    if st_dir != "up":
        return False, "SuperTrend is bearish"
        
    if adx < 20.0:
        return False, f"Weak ADX trend strength ({adx:.1f} < 20)"
        
    return True, f"STRENGTH_CONFIRMED (RS:{rs_rank:.1f}, ADX:{adx:.1f})"
