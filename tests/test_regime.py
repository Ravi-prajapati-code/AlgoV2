"""
Unit tests for strategy/regime.py's is_strong_bull() overlay.

is_strong_bull() is additive on top of detect_regime() — it never replaces
the BULL/BEAR trigger itself, only flags an already-BULL index as extended
above its own EMA(50) for STRONG_BULL_CONFIRM_DAYS consecutive days.
"""
import pandas as pd
import pytest

from strategy.regime import is_strong_bull, MIN_INDEX_CANDLES
from config.settings import STRONG_BULL_CONFIRM_DAYS


def _index_df(closes):
    return pd.DataFrame({"close": closes})


def test_none_index_returns_false():
    assert is_strong_bull(None) is False


def test_below_min_candles_returns_false():
    df = _index_df([100.0] * (MIN_INDEX_CANDLES - 1))
    assert is_strong_bull(df) is False


def test_insufficient_consecutive_days_returns_false():
    """Extension shows up only in the last 2 of the last N days — not sustained."""
    closes = [100.0] * (MIN_INDEX_CANDLES + 30) + [100.0, 100.0, 100.0, 130.0, 130.0]
    df = _index_df(closes)
    assert len(closes) >= MIN_INDEX_CANDLES
    assert STRONG_BULL_CONFIRM_DAYS >= 3  # test relies on the trailing 3 flat days failing the window
    assert is_strong_bull(df) is False


def test_sustained_extension_returns_true():
    """Index jumps well above EMA50 and stays there for the full confirm window."""
    closes = [100.0] * (MIN_INDEX_CANDLES + 50) + [140.0] * (STRONG_BULL_CONFIRM_DAYS + 10)
    df = _index_df(closes)
    assert is_strong_bull(df) is True
