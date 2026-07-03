"""Tests for portfolio sizer, allocator, and risk controls."""

import pytest
from datetime import date

from portfolio.sizer import calculate_shares, position_value
from portfolio.allocator import can_open_position, sector_allocation
from portfolio.risk import can_open_new_trades
from db.models import Position


def _pos(symbol, sector, price=100.0, shares=10):
    return Position(
        symbol=symbol, sector=sector,
        entry_date=date(2024, 1, 1), entry_price=price, shares=shares,
        stop_loss=price * 0.94, take_profit=price * 1.13,
        trailing_stop=price * 0.95, peak_price=price,
    )


class TestPositionSizer:
    def test_basic_sizing(self):
        # ₹75,000 portfolio, 1% risk, entry 100, stop 94 → risk/share=6
        # max_loss=₹750 → 125 shares by risk
        # stock allocation cap = 15% of 75k = ₹11.25k → 112 shares
        # cash cap = 75k*0.95/100 = 712 shares
        # min(125, 112, 712) = 112
        shares = calculate_shares(75_000, 100, 94, 75_000, risk_pct=1.0, alloc_pct=0.15)
        assert shares == 112

    def test_capped_by_allocation(self):
        # If shares_by_risk > max_stock_allocation cap, should be capped
        # max allocation = 75000 * 0.25 = 18750 → 18750/100 = 187 shares
        shares = calculate_shares(75_000, 100, 1, 75_000, risk_pct=1.0, alloc_pct=0.25)
        # But capped by allocation
        assert shares <= 187

    def test_zero_shares_no_cash(self):
        shares = calculate_shares(75_000, 100, 94, 0)
        assert shares == 0

    def test_zero_shares_bad_stop(self):
        shares = calculate_shares(75_000, 100, 100, 75_000)  # stop == entry
        assert shares == 0

    def test_position_value(self):
        assert position_value(10, 100.5) == pytest.approx(1005.0)


class TestAllocator:
    def test_can_open_new_position(self):
        positions = [_pos("TCS.NS", "IT", 100, 100)]
        ok, _ = can_open_position("INFY.NS", 5000, 75000, positions, {"TCS.NS": 100})
        assert ok is True

    def test_rejects_duplicate_symbol(self):
        positions = [_pos("TCS.NS", "IT")]
        ok, reason = can_open_position("TCS.NS", 5000, 75000, positions, {"TCS.NS": 100})
        assert ok is False
        assert "already" in reason.lower()

    def test_rejects_over_stock_cap(self):
        # Trade value = ₹20,000 on ₹75,000 portfolio = 26.7% > 25% cap
        ok, reason = can_open_position("INFY.NS", 20_000, 75_000, [], {})
        assert ok is False

    def test_rejects_over_sector_cap(self):
        # can_open_position groups by data.universe.get_sector(pos.symbol), a real
        # symbol->sector lookup — it ignores the sector label passed to _pos(). Use
        # symbols actually in SYMBOL_TO_SECTOR (config/watchlist_nse.py) so the test
        # doesn't depend on DB state. TCS.NS/MPHASIS.NS/TATAELXSI.NS are all
        # "Information Technology" in that static map.
        #
        # Sector cap is 67% (config/risk_config.yaml: max_sector_pct), sized for a
        # concentrated 3-position live portfolio where a stricter cap would be
        # unworkable. Existing IT: TCS ₹40k + MPHASIS ₹10k = ₹50k on ₹75k = 66.7%.
        # Adding another IT stock (within the 25% per-stock cap) should tip it over.
        positions = [
            _pos("TCS.NS",     "IT", 100, 400),   # ₹40,000
            _pos("MPHASIS.NS", "IT", 100, 100),   # ₹10,000
        ]
        prices = {"TCS.NS": 100, "MPHASIS.NS": 100}
        ok, reason = can_open_position("TATAELXSI.NS", 15_000, 75_000, positions, prices)
        assert ok is False
        assert "sector" in reason.lower()


class TestRiskControls:
    def test_daily_limit_reached(self):
        ok, reason = can_open_new_trades(6, [], 75_000, 75_000)
        assert ok is False
        assert "limit" in reason.lower()

    def test_max_positions_reached(self):
        # With 4 positions, should reject 5th
        positions = [_pos(f"STOCK{i}.NS", "IT") for i in range(4)]
        ok, reason = can_open_new_trades(0, positions, 75_000, 75_000)
        assert ok is False
        assert "Max open positions" in reason

    def test_drawdown_circuit_breaker(self):
        # Portfolio dropped from 100k to 75k = 25% drawdown → halt
        ok, reason = can_open_new_trades(0, [], 75_000, 100_000)
        assert ok is False
        assert "drawdown" in reason.lower()

    def test_within_limits_allowed(self):
        ok, _ = can_open_new_trades(1, [], 75_000, 75_000)
        assert ok is True
