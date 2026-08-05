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


def _expected_shares(cash, portfolio_value, price, mult):
    """Mirrors the production sizing math in portfolio/manager.py's buy branch."""
    from config.settings import MAX_OPEN_POSITIONS, MAX_STOCK_ALLOCATION_PCT, SIZER_CASH_BUFFER_PCT
    from portfolio.sizer import calculate_shares_for_value
    from charges.calculator import buy_charges
    from portfolio.manager import round_to_tick

    price = round_to_tick(price)
    spendable = cash * (1.0 - SIZER_CASH_BUFFER_PCT)
    base_slot_cash = (spendable / MAX_OPEN_POSITIONS) * mult
    slot_cash = min(base_slot_cash, portfolio_value * MAX_STOCK_ALLOCATION_PCT)
    target_val = slot_cash - buy_charges(slot_cash).total
    return calculate_shares_for_value(target_val, price)


def _buy_signal(price=150.0):
    return Signal(
        date=TODAY, symbol="XYZ.NS", action="BUY",
        score=95.0, price=price, reason="RS leader",
        indicators={"sector": "IT", "atr": 2.0},
    )


def test_bear_regime_applies_bear_size_mult(monkeypatch):
    monkeypatch.setattr(pm_module, "REGIME_SIZE_MULT_BEAR", 0.5)
    broker = FakeBroker(cash=100000.0, portfolio_value=100000.0)
    mgr = make_manager(broker, [])

    mgr.process_signals(TODAY, signals=[_buy_signal()], prices={"XYZ.NS": 150.0},
                         indicators={"XYZ.NS": {"atr": 2.0, "composite_rank": 95}},
                         regime="BEAR")

    buys = [o for o in broker.placed_orders if o.side == OrderSide.BUY]
    assert len(buys) == 1
    assert buys[0].quantity == _expected_shares(100000.0, 100000.0, 150.0, 0.5)


def test_bull_regime_applies_bull_size_mult(monkeypatch):
    monkeypatch.setattr(pm_module, "REGIME_SIZE_MULT_BULL", 1.0)
    broker = FakeBroker(cash=100000.0, portfolio_value=100000.0)
    mgr = make_manager(broker, [])

    mgr.process_signals(TODAY, signals=[_buy_signal()], prices={"XYZ.NS": 150.0},
                         indicators={"XYZ.NS": {"atr": 2.0, "composite_rank": 95}},
                         regime="BULL", strong_bull=False)

    buys = [o for o in broker.placed_orders if o.side == OrderSide.BUY]
    assert len(buys) == 1
    assert buys[0].quantity == _expected_shares(100000.0, 100000.0, 150.0, 1.0)


def test_strong_bull_applies_strong_bull_size_mult(monkeypatch):
    monkeypatch.setattr(pm_module, "REGIME_SIZE_MULT_STRONG_BULL", 1.5)
    broker = FakeBroker(cash=100000.0, portfolio_value=100000.0)
    mgr = make_manager(broker, [])

    mgr.process_signals(TODAY, signals=[_buy_signal()], prices={"XYZ.NS": 150.0},
                         indicators={"XYZ.NS": {"atr": 2.0, "composite_rank": 95}},
                         regime="BULL", strong_bull=True)

    buys = [o for o in broker.placed_orders if o.side == OrderSide.BUY]
    assert len(buys) == 1
    assert buys[0].quantity == _expected_shares(100000.0, 100000.0, 150.0, 1.5)


def test_bear_regime_wins_over_strong_bull_flag(monkeypatch):
    """BEAR check comes first in the if/elif chain — a stray strong_bull=True
    during a BEAR day must not override the bear-sizing branch."""
    monkeypatch.setattr(pm_module, "REGIME_SIZE_MULT_BEAR", 0.5)
    monkeypatch.setattr(pm_module, "REGIME_SIZE_MULT_STRONG_BULL", 1.5)
    broker = FakeBroker(cash=100000.0, portfolio_value=100000.0)
    mgr = make_manager(broker, [])

    mgr.process_signals(TODAY, signals=[_buy_signal()], prices={"XYZ.NS": 150.0},
                         indicators={"XYZ.NS": {"atr": 2.0, "composite_rank": 95}},
                         regime="BEAR", strong_bull=True)

    buys = [o for o in broker.placed_orders if o.side == OrderSide.BUY]
    assert len(buys) == 1
    assert buys[0].quantity == _expected_shares(100000.0, 100000.0, 150.0, 0.5)


def test_cancel_stale_gtts_on_process_signals(monkeypatch):
    """Legacy stop GTTs are cancelled at the start of each run."""
    pos = make_position(symbol="ABC.NS", trailing_stop=90.0)
    broker = FakeBroker()
    broker.pending_gtts["ABC.NS"] = ["GTT1", "GTT2"]
    mgr = make_manager(broker, [pos])

    mgr.process_signals(TODAY, signals=[], prices={"ABC.NS": 105.0},
                         indicators={"ABC.NS": {"atr": 1.0}}, regime="BULL")

    assert set(broker.cancelled_gtt_ids) == {"GTT1", "GTT2"}


def test_shares_override_sells_partial_and_keeps_position_open():
    """Regression test for the bug where runner-requested partial sells
    (e.g. a LIQUIDBEES trim to fund another entry) silently liquidated the
    entire position because shares_override was never read."""
    pos = make_position(symbol="ABC.NS", shares=100)
    broker = FakeBroker()
    mgr = make_manager(broker, [pos])
    sell_sig = Signal(
        date=TODAY, symbol="ABC.NS", action="SELL",
        score=0, price=105.0, reason="liquidbees_fund_swing",
        indicators={"shares_override": 40},
    )

    mgr.process_signals(TODAY, signals=[sell_sig], prices={"ABC.NS": 105.0},
                         indicators={"ABC.NS": {"atr": 1.0}}, regime="BULL")

    market_sells = [o for o in broker.placed_orders
                    if o.symbol == "ABC.NS" and o.side == OrderSide.SELL and not o.is_gtt]
    assert len(market_sells) == 1
    assert market_sells[0].quantity == 40  # not the full 100

    remaining = [p for p in mgr.open_positions if p.symbol == "ABC.NS"]
    assert len(remaining) == 1
    assert remaining[0].shares == 60

    db_positions = [p for p in load_positions() if p.symbol == "ABC.NS"]
    assert len(db_positions) == 1
    assert db_positions[0].shares == 60
    assert db_positions[0].status == "OPEN"


def test_shares_override_equal_to_full_position_closes_it():
    """override == current shares is a full sell, not a zero-share partial —
    must still close the position rather than leaving a 0-share OPEN row."""
    pos = make_position(symbol="ABC.NS", shares=100)
    broker = FakeBroker()
    mgr = make_manager(broker, [pos])
    sell_sig = Signal(
        date=TODAY, symbol="ABC.NS", action="SELL",
        score=0, price=105.0, reason="liquidbees_fund_swing",
        indicators={"shares_override": 100},
    )

    mgr.process_signals(TODAY, signals=[sell_sig], prices={"ABC.NS": 105.0},
                         indicators={"ABC.NS": {"atr": 1.0}}, regime="BULL")

    assert not any(p.symbol == "ABC.NS" for p in mgr.open_positions)
    market_sells = [o for o in broker.placed_orders
                    if o.symbol == "ABC.NS" and o.side == OrderSide.SELL and not o.is_gtt]
    assert market_sells[0].quantity == 100


def test_shares_override_greater_than_position_clamps_to_full_sell():
    """A defensive override larger than what's actually held (stale runner
    calc, race with a prior partial fill, etc.) must not oversell — clamp to
    the full position instead of requesting more shares than exist."""
    pos = make_position(symbol="ABC.NS", shares=50)
    broker = FakeBroker()
    mgr = make_manager(broker, [pos])
    sell_sig = Signal(
        date=TODAY, symbol="ABC.NS", action="SELL",
        score=0, price=105.0, reason="liquidbees_fund_swing",
        indicators={"shares_override": 500},
    )

    mgr.process_signals(TODAY, signals=[sell_sig], prices={"ABC.NS": 105.0},
                         indicators={"ABC.NS": {"atr": 1.0}}, regime="BULL")

    market_sells = [o for o in broker.placed_orders
                    if o.symbol == "ABC.NS" and o.side == OrderSide.SELL and not o.is_gtt]
    assert market_sells[0].quantity == 50
    assert not any(p.symbol == "ABC.NS" for p in mgr.open_positions)


def test_shares_override_buy_bypasses_slot_sizing():
    """Regression test for the bug where runner-requested exact buy quantities
    (defensive entry, bear-swing entry, LIQUIDBEES cash park) were silently
    replaced with ordinary equal-weight slot sizing."""
    broker = FakeBroker(cash=100000.0, portfolio_value=100000.0)
    mgr = make_manager(broker, [])
    buy_sig = Signal(
        date=TODAY, symbol="XYZ.NS", action="BUY",
        score=0, price=150.0, reason="bear_swing_entry",
        indicators={"sector": "IT", "shares_override": 37},
    )

    mgr.process_signals(TODAY, signals=[buy_sig], prices={"XYZ.NS": 150.0},
                         indicators={"XYZ.NS": {"atr": 2.0, "composite_rank": 95}},
                         regime="BULL")

    buys = [o for o in broker.placed_orders if o.side == OrderSide.BUY]
    assert len(buys) == 1
    assert buys[0].quantity == 37  # not the ordinary slot-sized quantity


def test_shares_override_buy_paper_mode_does_not_crash():
    """Regression test: paper-mode (no broker) BUY execution logs an allocation
    line that used to reference slot_cash/useable_cash unconditionally — both
    undefined when shares_override takes the sizing branch, raising
    UnboundLocalError before the position could ever be recorded."""
    mgr = make_manager(None, [])
    buy_sig = Signal(
        date=TODAY, symbol="XYZ.NS", action="BUY",
        score=0, price=150.0, reason="bear_swing_entry",
        indicators={"sector": "IT", "shares_override": 37},
    )

    mgr.process_signals(TODAY, signals=[buy_sig], prices={"XYZ.NS": 150.0},
                         indicators={"XYZ.NS": {"atr": 2.0, "composite_rank": 95}},
                         regime="BULL")

    assert any(p.symbol == "XYZ.NS" and p.shares == 37 for p in mgr.open_positions)


def test_shares_override_buy_skipped_when_cash_insufficient():
    """Regression test for a runner-side bug (docs/55): daily_runner.py's
    bear-swing loop could double-count the same spare-cash pool across two
    qualifying candidates in one run, under-sizing the second candidate's
    LIQUIDBEES funding sell. shares_override bypasses normal cash-derived
    sizing entirely, so with no local check a runner sizing bug would send a
    market order the account can't afford. Manager must refuse instead."""
    mgr = make_manager(None, [])
    mgr.cash = 1000.0  # far less than 37 * 150 + charges
    buy_sig = Signal(
        date=TODAY, symbol="XYZ.NS", action="BUY",
        score=0, price=150.0, reason="bear_swing_entry",
        indicators={"sector": "IT", "shares_override": 37},
    )

    mgr.process_signals(TODAY, signals=[buy_sig], prices={"XYZ.NS": 150.0},
                         indicators={"XYZ.NS": {"atr": 2.0, "composite_rank": 95}},
                         regime="BULL")

    assert not any(p.symbol == "XYZ.NS" for p in mgr.open_positions)
    assert mgr.cash == 1000.0  # untouched


def test_unverified_gtt_cancel_blocks_new_buy():
    """Regression test for the bug where cancel_stale_gtts()'s False return
    (unverified cancellation) was ignored and the BUY proceeded anyway."""
    broker = FakeBroker(cash=100000.0, portfolio_value=100000.0)
    broker.pending_gtts["XYZ.NS"] = ["STALE_GTT"]
    broker.cancel_should_fail.add("STALE_GTT")
    mgr = make_manager(broker, [])
    buy_sig = Signal(
        date=TODAY, symbol="XYZ.NS", action="BUY",
        score=95.0, price=150.0, reason="MOMENTUM",
        indicators={"sector": "IT"},
    )

    mgr.process_signals(TODAY, signals=[buy_sig], prices={"XYZ.NS": 150.0},
                         indicators={"XYZ.NS": {"atr": 2.0, "composite_rank": 95}},
                         regime="BULL")

    buys = [o for o in broker.placed_orders if o.side == OrderSide.BUY]
    assert buys == []  # must not buy alongside an unverified stale GTT
    assert not any(p.symbol == "XYZ.NS" for p in mgr.open_positions)


def test_unverified_gtt_cancel_blocks_sell():
    """Regression test for the same bug on the SELL side — a stale sell-side
    GTT that failed to cancel must not be joined by a second market sell."""
    pos = make_position(symbol="ABC.NS", shares=10)
    broker = FakeBroker()
    broker.pending_gtts["ABC.NS"] = ["STALE_GTT"]
    broker.cancel_should_fail.add("STALE_GTT")
    mgr = make_manager(broker, [pos])
    sell_sig = Signal(
        date=TODAY, symbol="ABC.NS", action="SELL",
        score=0, price=105.0, reason="TREND_BREAK",
    )

    mgr.process_signals(TODAY, signals=[sell_sig], prices={"ABC.NS": 105.0},
                         indicators={"ABC.NS": {"atr": 1.0}}, regime="BULL")

    market_sells = [o for o in broker.placed_orders
                    if o.symbol == "ABC.NS" and o.side == OrderSide.SELL and not o.is_gtt]
    assert market_sells == []  # must abort rather than sell alongside an unverified stale GTT
    assert any(p.symbol == "ABC.NS" for p in mgr.open_positions)  # position left open