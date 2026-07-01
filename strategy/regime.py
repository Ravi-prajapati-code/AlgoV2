import pandas as pd
import numpy as np
from config.settings import MAX_OPEN_POSITIONS, MARKET_FILTER_ENABLED

MIN_INDEX_CANDLES = 100
REGIME_CONFIRM_DAYS = 3   # consecutive days required to confirm a regime change

def detect_regime(index_df: pd.DataFrame) -> str:
    """
    Macro-Trend Regime Detection with whipsaw filter.
    Requires REGIME_CONFIRM_DAYS consecutive closes on the same side of EMA(100)
    before declaring a regime change. Mixed signals → majority of last 10 days.
    This eliminates single-day false flips (e.g. Feb 2026 5-flip sequence).
    """
    if index_df is None or len(index_df) < MIN_INDEX_CANDLES:
        return "UNKNOWN"

    close  = index_df['close']
    ema100 = close.ewm(span=100, adjust=False).mean()

    n       = min(REGIME_CONFIRM_DAYS, len(close))
    signals = [bool(close.iloc[-i] > ema100.iloc[-i]) for i in range(1, n + 1)]

    if all(signals):       # all N days above EMA100 → confirmed BULL
        return "BULL"
    if not any(signals):   # all N days below EMA100 → confirmed BEAR
        return "BEAR"

    # Mixed signals: require a strong 65% majority of last 20 days to confirm BULL.
    # This creates asymmetric hysteresis — once BEAR fires (3 down days), recovery
    # back to BULL requires sustained strength, not just a 1-day bounce.
    lookback = min(20, len(close))
    extended = [bool(close.iloc[-i] > ema100.iloc[-i]) for i in range(1, lookback + 1)]
    return "BULL" if (sum(extended) / len(extended)) >= 0.65 else "BEAR"

def is_buy_allowed(regime: str) -> bool:
    if not MARKET_FILTER_ENABLED:
        return True
    return regime == "BULL"

def is_index_confirming(index_df: pd.DataFrame) -> bool:
    """Short-term confirmation: index close above its 20 EMA."""
    if index_df is None or len(index_df) < 20:
        return True
    close = index_df['close']
    ema20 = close.ewm(span=20, adjust=False).mean()
    return bool(close.iloc[-1] > ema20.iloc[-1])

def regime_max_slots(regime: str) -> int:
    return MAX_OPEN_POSITIONS

def regime_position_factor(regime: str) -> float:
    return 1.0

def regime_min_score(regime: str) -> float:
    return 85.0
