"""Tests for strategy entry/exit/scoring and signal generation."""

import pytest
from datetime import date

from strategy.entry import check_entry
from strategy.exit import check_exit_conditions, initial_stops, update_trailing_stop
from strategy.scoring import score_signal
from db.models import Position


def _make_ind(**overrides) -> dict:
    """Create a baseline 'perfect buy' indicator dict."""
    base = {
        "close":          100.0,
        "high":           102.0,
        "low":            98.0,
        "ema_fast":       98.0,
        "ema_slow":       95.0,
        "ema_20":         98.0,
        "ema_50":         95.0,
        "ema_100":        90.0,
        "ema_150":        85.0,
        "above_ema_fast": True,
        "uptrend":        True,
        "golden_cross":   True,
        "death_cross":    False,
        "rsi":            65.0,
        "rsi_prev":       60.0,
        "macd":           0.5,
        "macd_signal":    0.3,
        "macd_hist":      0.2,
        "macd_hist_prev": 0.1,
        "macd_bullish":   True,
        "macd_turning_up": True,
        "bb_upper":       110.0,
        "bb_mid":         100.0,
        "bb_lower":       90.0,
        "bb_pct":         0.5,
        "above_bb_lower": True,
        "above_bb_mid":   True,
        "atr":            2.0,
        "vol_avg":        500_000,
        "vol_today":      800_000,
        "vol_ratio":      1.6,
        "vol_spike":      True,
        "vol_increasing": True,
        "turnover":       100_000_000,
        "perf_10d":       5.0,   # 5% gain in 10 days
        # RS fields
        "rs_ratio":         1.20,
        "rs_rank":          95.0,  # High RS
        # Price context (breakout check: close >= 95% of high_20d)
        "high_20d":         102.0, # 100 is > 95% of 102
        # 52-week context
        "week52_high":      110.0,
    }
    base.update(overrides)
    
    # Recalculate atr_pct if not explicitly provided
    if "atr_pct" not in overrides:
        close = base["close"]
        atr = base["atr"]
        base["atr_pct"] = (atr / close * 100) if close > 0 else 0
        
    return base


def _make_position(**overrides) -> Position:
    base = dict(
        symbol="TEST.NS", sector="IT",
        entry_date=date(2024, 1, 1), entry_price=100.0, shares=10,
        stop_loss=94.0, take_profit=113.0,
        trailing_stop=95.0, peak_price=100.0,
    )
    base.update(overrides)
    return Position(**base)


class TestEntryConditions:
    def test_all_conditions_met(self):
        ok, reason = check_entry(_make_ind())
        assert ok is True

    def test_low_rs_rank_fails(self):
        ok, _ = check_entry(_make_ind(rs_rank=60.0))
        assert ok is False

    def test_rsi_out_of_bounds_fails(self):
        ok, _ = check_entry(_make_ind(rsi=50.0))
        assert ok is False
        ok, _ = check_entry(_make_ind(rsi=85.0))
        assert ok is False

    def test_trend_not_aligned_fails(self):
        # Break alignment: price < ema_20
        ok, _ = check_entry(_make_ind(close=90.0, ema_20=98.0))
        assert ok is False

    def test_overextended_fails(self):
        # (150 - 95)/95 = 57% > 15%
        ok, _ = check_entry(_make_ind(close=150.0, ema_50=95.0))
        assert ok is False

    def test_excessive_volatility_fails(self):
        ok, _ = check_entry(_make_ind(atr=10.0, close=100.0)) # 10% > 5%
        assert ok is False

    def test_low_volume_fails(self):
        ok, _ = check_entry(_make_ind(vol_ratio=1.0))
        assert ok is False


class TestExitConditions:
    def test_stop_loss_triggers(self):
        pos = _make_position(stop_loss=94.0)
        ok, reason = check_exit_conditions(pos, 93.0, rs_rank=100)
        assert ok is True
        assert "STOP_LOSS" in reason

    def test_take_profit_triggers(self):
        pos = _make_position(take_profit=113.0)
        ok, reason = check_exit_conditions(pos, 114.0, rs_rank=100)
        assert ok is True
        assert "PROFIT_TARGET" in reason

    def test_trailing_stop_triggers(self):
        pos = _make_position(entry_price=90.0, peak_price=100.0, trailing_stop=97.0)
        ok, reason = check_exit_conditions(pos, 96.0, rs_rank=100)
        assert ok is True
        assert "TRAIL_EXIT" in reason

    def test_laggard_exit_triggers(self):
        ok, reason = check_exit_conditions(_make_position(), 100.0, rs_rank=40)
        assert ok is True
        assert "LAGGARD_EXIT" in reason

    def test_hold_when_fine(self):
        ok, reason = check_exit_conditions(_make_position(), 105.0, rs_rank=100)
        assert ok is False
        assert reason == ""


class TestInitialStops:
    def test_stop_loss_below_entry(self):
        stops = initial_stops(100.0)
        assert stops["stop_loss"] < 100.0

    def test_take_profit_above_entry(self):
        stops = initial_stops(100.0)
        assert stops["take_profit"] > 100.0

    def test_trailing_stop_below_entry(self):
        stops = initial_stops(100.0)
        assert stops["trailing_stop"] < 100.0


class TestTrailingStop:
    def test_updates_on_new_high(self):
        # trailing_stop=85 reflects 15% TRAILING_STOP_PCT below entry=100
        # new_trail at price=110 = 110 * 0.85 = 93.5 > 85.0 → should ratchet up
        pos = _make_position(peak_price=100.0, trailing_stop=85.0)
        pos = update_trailing_stop(pos, 110.0)
        assert pos.peak_price == 110.0
        assert pos.trailing_stop > 85.0

    def test_no_update_on_lower_price(self):
        pos = _make_position(peak_price=100.0, trailing_stop=95.0)
        pos = update_trailing_stop(pos, 98.0)
        assert pos.peak_price == 100.0
        assert pos.trailing_stop == 95.0


class TestScoring:
    def test_score_range(self):
        score = score_signal(_make_ind())
        assert 0 <= score <= 100

    def test_perfect_signal_high_score(self):
        score = score_signal(_make_ind())
        assert score >= 60   # Should score well with all conditions met

    def test_weak_signal_lower_score(self):
        weak = _make_ind(
            rs_rank=40.0
        )
        strong_score = score_signal(_make_ind(rs_rank=95.0))
        weak_score   = score_signal(weak)
        assert strong_score > weak_score
