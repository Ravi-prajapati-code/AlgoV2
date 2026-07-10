from typing import Tuple, Dict
from config.settings import (
    VOLUME_SPIKE_MULTIPLIER, RS_THRESHOLD,
    TREND_GATE_200_ENABLED, ADX_TREND_THRESHOLD,
    EXTENSION_CAP_PCT, BREAKOUT_PCT, ENTRY_MODE,
    MIN_DAILY_TURNOVER,
)

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

    # Entry Attribution Suite (docs/23_Assumption_Audit.md §XIV): FULL mode skips
    # nothing below — byte-identical to live behavior. Other modes drop specific
    # gates to isolate which piece of the signal creates edge.
    skip_rs     = ENTRY_MODE in ("PURE_ADX_BREAKOUT", "RANDOM_ALL", "RANDOM_ELIGIBLE")
    skip_trend  = ENTRY_MODE in ("PURE_RS", "RANDOM_ALL", "RANDOM_ELIGIBLE")
    skip_safety = ENTRY_MODE == "RANDOM_ALL"

    if not skip_rs and rs_rank < RS_THRESHOLD:
        return False, f"Low RS ({rs_rank:.1f})"

    if not skip_trend:
        # 0. Long-term trend gate (optional): price must be above 200-day EMA
        if TREND_GATE_200_ENABLED:
            ema_200 = float(ind.get("ema_200", 0))
            if ema_200 > 0 and close < ema_200:
                return False, f"Below 200 EMA ({close:.1f} < {ema_200:.1f})"

        # 2. Breakout Check — VCP or standard near-high
        vcp_detected = bool(ind.get("vcp_detected", False))
        vcp_pivot    = float(ind.get("vcp_pivot", 0))
        if vcp_detected and vcp_pivot > 0:
            # VCP: entry within 2% below last contraction pivot + RVOL floor
            if close < vcp_pivot * 0.98:
                return False, f"VCP: not at pivot ({close:.1f} vs {vcp_pivot:.1f})"
        else:
            # Standard: within BREAKOUT_PCT of 20-day high
            if close < high_20d * (1 - BREAKOUT_PCT):
                return False, f"Not at Breakout (Price {close:.1f} < {(1-BREAKOUT_PCT)*100:.0f}% of 20d high {high_20d:.1f})"

    if not skip_safety:
        # 4. Overextension Check: Price shouldn't be too far from EMA 50
        if ema_50 > 0:
            extension = (close - ema_50) / ema_50
            if extension > EXTENSION_CAP_PCT:
                return False, f"Overextended ({extension:.1%})"

    if not skip_safety:
        # 7. Minimum daily turnover: ₹2 Cr/day for execution safety
        turnover = float(ind.get("turnover", 0))
        if 0 < turnover < MIN_DAILY_TURNOVER:
            return False, f"Low liquidity (₹{turnover/1e6:.1f}M < ₹{MIN_DAILY_TURNOVER/1e6:.0f}M)"
    elif close <= 0:
        return False, "No price data"

    if not skip_trend:
        # 8. Institutional Trend Strength: EMA, SuperTrend, ADX alignment
        adx = float(ind.get("adx", 0))
        st_dir = ind.get("st_direction", -1)

        if not (close > ema_50 and ema_50 > ema_100):
            return False, "Weak Trend (Price or EMAs not aligned)"

        if st_dir not in (1, "up"):
            return False, "SuperTrend is bearish"

        if adx < ADX_TREND_THRESHOLD:
            return False, f"Weak ADX trend strength ({adx:.1f} < {ADX_TREND_THRESHOLD:.0f})"

    if ENTRY_MODE == "FULL":
        adx = float(ind.get("adx", 0))
        return True, f"STRENGTH_CONFIRMED (RS:{rs_rank:.1f}, ADX:{adx:.1f})"
    return True, f"[{ENTRY_MODE}] RS:{rs_rank:.1f}"
