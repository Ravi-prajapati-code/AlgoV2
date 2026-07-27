import os
import pandas as pd
from config.settings import MARKET_FILTER_ENABLED, STRONG_BULL_EXTENSION_PCT, STRONG_BULL_CONFIRM_DAYS

MIN_INDEX_CANDLES = 100
REGIME_CONFIRM_DAYS = int(os.getenv("REGIME_CONFIRM_DAYS", "3"))   # consecutive days required to confirm a regime change

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

def is_strong_bull(index_df: pd.DataFrame) -> bool:
    """
    Additive overlay on top of an already-confirmed BULL regime -- never
    modifies or replaces detect_regime() itself (a prior sensitivity/latency
    change to the regime trigger was rejected 3/3 variants on whipsaw).
    Callers must AND this with regime == "BULL"; this function does not
    re-check EMA100 itself.

    True when the index has closed STRONG_BULL_EXTENSION_PCT or more above
    its own EMA(50) for STRONG_BULL_CONFIRM_DAYS consecutive days -- mirrors
    the per-stock EXTENSION_CAP_PCT math (strategy/entry.py) applied to the
    index, with its own multi-day confirm window as the anti-whipsaw guard.
    """
    if index_df is None or len(index_df) < MIN_INDEX_CANDLES:
        return False

    close = index_df['close']
    ema50 = close.ewm(span=50, adjust=False).mean()

    n = min(STRONG_BULL_CONFIRM_DAYS, len(close))
    extended = [
        bool((close.iloc[-i] - ema50.iloc[-i]) / ema50.iloc[-i] >= STRONG_BULL_EXTENSION_PCT)
        for i in range(1, n + 1)
    ]
    return all(extended)

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
