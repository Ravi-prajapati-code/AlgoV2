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
        "adx":            25.0,
        "st_direction":   1,
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

    def test_trend_not_aligned_fails(self, monkeypatch):
        # Trend/ADX/breakout gate is skipped under the live default (PURE_RS) —
        # pin FULL to test that gate specifically.
        monkeypatch.setattr("strategy.entry.ENTRY_MODE", "FULL")
        # Break alignment: price < ema_20
        ok, _ = check_entry(_make_ind(close=90.0, ema_20=98.0))
        assert ok is False

    def test_overextended_fails(self):
        # (150 - 95)/95 = 57% > 15%
        ok, _ = check_entry(_make_ind(close=150.0, ema_50=95.0))
        assert ok is False


class TestExitConditions:
    def test_stop_loss_does_not_trigger(self):
        """Hard stop-loss removed — only system sell signals exit positions."""
        pos = _make_position(stop_loss=94.0)
        ok, reason = check_exit_conditions(pos, 93.0, rs_rank=100)
        assert ok is False
        assert reason == ""

    def test_take_profit_does_not_trigger(self):
        """Profit ceiling removed — only system sell signals exit positions."""
        pos = _make_position(take_profit=113.0)
        ok, reason = check_exit_conditions(pos, 114.0, rs_rank=100)
        assert ok is False
        assert reason == ""

    def test_trailing_stop_does_not_trigger(self):
        """Trailing stop removed — price below trail does not auto-exit."""
        pos = _make_position(entry_price=90.0, peak_price=100.0, trailing_stop=97.0)
        ok, reason = check_exit_conditions(pos, 96.0, rs_rank=100)
        assert ok is False
        assert reason == ""

    def test_momentum_decay_exit_triggers(self):
        ok, reason = check_exit_conditions(_make_position(), 130.0, indicators={"rsi": 40})
        assert ok is True
        assert "MOMENTUM_DECAY" in reason

    def test_momentum_decay_exit_triggers_while_underwater(self):
        # No profit gate — a deteriorating position exits regardless of P&L
        # (previously an underwater laggard had no exit path once price-based
        # stops were removed; docs/23_Assumption_Audit.md #24).
        pos = _make_position(entry_price=100.0)
        ok, reason = check_exit_conditions(pos, 90.0, indicators={"rsi": 40})
        assert ok is True
        assert "MOMENTUM_DECAY" in reason

    def test_low_rs_rank_alone_does_not_trigger_exit(self):
        # RS-decay exit removed (docs/23_Assumption_Audit.md #23) — a low rs_rank
        # with no RSI decay should no longer exit a position.
        ok, reason = check_exit_conditions(_make_position(), 130.0, rs_rank=10, indicators={"rsi": 70})
        assert ok is False
        assert reason == ""

    def test_hold_when_fine(self):
        ok, reason = check_exit_conditions(_make_position(), 105.0, rs_rank=100)
        assert ok is False
        assert reason == ""


class TestInitialStops:
    def test_stop_loss_disabled(self):
        stops = initial_stops(100.0)
        assert stops["stop_loss"] == 0.0

    def test_take_profit_disabled(self):
        stops = initial_stops(100.0)
        assert stops["take_profit"] == 0.0

    def test_trailing_stop_disabled(self):
        stops = initial_stops(100.0)
        assert stops["trailing_stop"] == 0.0


class TestTrailingStop:
    def test_peak_price_tracks_new_high(self):
        pos = _make_position(peak_price=100.0, trailing_stop=85.0)
        pos = update_trailing_stop(pos, 110.0)
        assert pos.peak_price == 110.0
        assert pos.trailing_stop == 85.0  # trail no longer ratchets

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
        score = score_signal(_make_ind(composite_rank=95.0))
        assert score >= 60   # Should score well with all conditions met

    def test_weak_signal_lower_score(self):
        weak = _make_ind(
            composite_rank=40.0
        )
        strong_score = score_signal(_make_ind(composite_rank=95.0))
        weak_score   = score_signal(weak)
        assert strong_score > weak_score
