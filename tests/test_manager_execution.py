"""
Execution-logic tests for portfolio/manager.py.

Uses a FakeBroker double (no real API calls) against a temp SQLite DB, so
these run in-process in seconds. Scope: signal-only exit mode — stop-loss and
trailing-stop GTTs are removed; positions exit only on system sell signals.
"""
import pytest
from datetime import date

import portfolio.manager as pm_module
from portfolio.manager import PortfolioManager
from db.models import Position, Signal
from db.repository import init_db, load_positions, save_position
from broker.base import OrderResult, OrderStatus, OrderSide, OrderType
from config.settings import SAFE_HAVEN_SYMBOL

TODAY = date(2026, 7, 2)


class FakeBroker:
    """Minimal broker double covering every method portfolio.manager calls."""

    def __init__(self, cash=1.0, portfolio_value=100000.0):
        self.cash = cash
        self.portfolio_value_ = portfolio_value
        self.placed_orders = []
        self.cancelled_gtt_ids = []
        self.pending_gtts = {}
        self.cancel_should_fail = set()

    def get_available_cash(self):
        return self.cash

    def get_portfolio_value(self):
        return self.portfolio_value_

    def get_order_status(self, order_id):
        return OrderResult(order_id=order_id, status=OrderStatus.COMPLETE,
                            symbol="", side=OrderSide.SELL, requested_qty=0,
                            avg_price=100.0, raw_response={})

    def place_order_with_retry(self, req):
        self.placed_orders.append(req)
        order_id = f"ORD{len(self.placed_orders)}"
        if req.is_gtt:
            self.pending_gtts.setdefault(req.symbol, []).append(order_id)
        return OrderResult(order_id=order_id, status=OrderStatus.COMPLETE,
                            symbol=req.symbol, side=req.side, requested_qty=req.quantity,
                            filled_qty=req.quantity,
                            avg_price=req.gtt_trigger_price or 100.0, raw_response={})

    def get_pending_gtt_orders(self, symbol):
        return list(self.pending_gtts.get(symbol, []))

    def cancel_gtt_order(self, gtt_id):
        if gtt_id in self.cancel_should_fail:
            return False
        self.cancelled_gtt_ids.append(gtt_id)
        for ids in self.pending_gtts.values():
            if gtt_id in ids:
                ids.remove(gtt_id)
        return True


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setattr("db.repository.DB_PATH", str(tmp_path / "test.db"))
    init_db()
    monkeypatch.setattr(pm_module, "_SCORE_HISTORY_PATH", str(tmp_path / "score_history.json"))
    monkeypatch.setattr(pm_module, "RIDE_WINNER_ENABLED", False)
    monkeypatch.setattr(pm_module, "ROTATION_ENABLED", False)
    monkeypatch.setattr(pm_module, "SCORE_DROP_EXIT_ENABLED", False)
    monkeypatch.setattr("notifications.telegram.send_message", lambda *a, **k: True)
    yield


def make_position(symbol="ABC.NS", entry_price=100.0, shares=10, stop_loss=90.0,
                   take_profit=130.0, trailing_stop=90.0, peak_price=100.0,
                   sector="IT"):
    return Position(
        symbol=symbol, sector=sector, entry_date=TODAY,
        entry_price=entry_price, shares=shares, stop_loss=stop_loss,
        take_profit=take_profit, trailing_stop=trailing_stop, peak_price=peak_price,
    )


def make_manager(broker, positions):
    for p in positions:
        save_position(p)
    return PortfolioManager(initial_capital=100000.0, broker=broker)


def test_trail_breach_does_not_auto_sell():
    """Price below trailing stop no longer triggers an exit — signal-only mode."""
    pos = make_position(symbol="ABC.NS", trailing_stop=100.0, peak_price=110.0)
    broker = FakeBroker()
    broker.pending_gtts["ABC.NS"] = ["OLD_GTT"]
    mgr = make_manager(broker, [pos])

    mgr.process_signals(TODAY, signals=[], prices={"ABC.NS": 95.0},
                         indicators={"ABC.NS": {"atr": 1.0}}, regime="BULL")

    assert any(p.symbol == "ABC.NS" for p in mgr.open_positions)
    market_sells = [o for o in broker.placed_orders
                    if o.symbol == "ABC.NS" and o.side == OrderSide.SELL and not o.is_gtt]
    assert market_sells == []
    assert "OLD_GTT" in broker.cancelled_gtt_ids  # legacy GTT cleaned up


def test_sell_signal_executes_market_sell():
    pos = make_position(symbol="ABC.NS")
    broker = FakeBroker()
    mgr = make_manager(broker, [pos])
    sell_sig = Signal(
        date=TODAY, symbol="ABC.NS", action="SELL",
        score=0, price=105.0, reason="TREND_BREAK",
    )

    mgr.process_signals(TODAY, signals=[sell_sig], prices={"ABC.NS": 105.0},
                         indicators={"ABC.NS": {"atr": 1.0}}, regime="BULL")

    assert not any(p.symbol == "ABC.NS" for p in mgr.open_positions)
    market_sells = [o for o in broker.placed_orders
                    if o.symbol == "ABC.NS" and o.side == OrderSide.SELL and not o.is_gtt]
    assert len(market_sells) == 1


def test_no_gtt_placed_on_buy():
    broker = FakeBroker(cash=50000.0)
    mgr = make_manager(broker, [])
    buy_sig = Signal(
        date=TODAY, symbol="XYZ.NS", action="BUY",
        score=95.0, price=200.0, reason="RS leader",
        indicators={"sector": "IT", "atr": 2.0},
    )

    mgr.process_signals(TODAY, signals=[buy_sig], prices={"XYZ.NS": 200.0},
                         indicators={"XYZ.NS": {"atr": 2.0, "composite_rank": 95}},
                         regime="BULL")

    gtts = [o for o in broker.placed_orders if o.is_gtt]
    assert gtts == []


def test_gtt_stop_limit_price_has_fill_buffer():
    from portfolio.manager import gtt_stop_limit_price
    from config.settings import GTT_LIMIT_BUFFER_PCT

    trigger = 92.0
    limit = gtt_stop_limit_price(trigger)
    assert limit < trigger
    expected = round(trigger * (1 - GTT_LIMIT_BUFFER_PCT) / 0.05) * 0.05
    assert limit == pytest.approx(expected, abs=0.01)


def test_cancel_stale_gtts_on_process_signals(monkeypatch):
    """Legacy stop GTTs are cancelled at the start of each run."""
    pos = make_position(symbol="ABC.NS", trailing_stop=90.0)
    broker = FakeBroker()
    broker.pending_gtts["ABC.NS"] = ["GTT1", "GTT2"]
    mgr = make_manager(broker, [pos])

    mgr.process_signals(TODAY, signals=[], prices={"ABC.NS": 105.0},
                         indicators={"ABC.NS": {"atr": 1.0}}, regime="BULL")

    assert set(broker.cancelled_gtt_ids) == {"GTT1", "GTT2"}
    assert broker.pending_gtts.get("ABC.NS", []) == []
    assert any(p.symbol == "ABC.NS" for p in mgr.open_positions)