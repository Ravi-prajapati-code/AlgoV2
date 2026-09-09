"""
Tests for reconciliation/gate.py (docs/60 M3, plan optimized-humming-crayon).

Unit tests exercise pre_trade_check() directly against a tmp_path-scoped
reporting.db (same monkeypatch pattern as
tests/test_portfolio.py::TestPeakValueExcludesOwnershipCorrections). The
final test is an integration check on portfolio/manager.py's BUY loop
proving the wiring uses `continue` (per-symbol), not `break` (portfolio-wide)
-- one poisoned symbol must not block an unrelated symbol in the same cycle.
"""
import logging
from datetime import date, timedelta

import pytest

from db import reporting_repo as rrepo
from reconciliation.classifier import Classification
import reconciliation.gate as gate_module
from reconciliation.gate import pre_trade_check


TODAY = date(2026, 9, 9)  # a Wednesday


@pytest.fixture(autouse=True)
def isolated_reporting_db(tmp_path, monkeypatch):
    monkeypatch.setattr(rrepo, "REPORTING_DB_PATH", str(tmp_path / "reporting_gate_test.db"))
    monkeypatch.setattr(gate_module, "PRE_TRADE_INTEGRITY_GATE_ENABLED", True)
    monkeypatch.setattr(gate_module, "RECONCILIATION_STALENESS_TRADING_SESSIONS", 2)
    # Freeze "today" inside the gate so staleness math is deterministic
    # regardless of what real-world date the suite runs on.
    class _FixedDate(date):
        @classmethod
        def today(cls):
            return TODAY
    monkeypatch.setattr(gate_module, "date", _FixedDate)
    yield


def _seed_registry():
    rrepo.init_db()
    rrepo.register_strategy("main", "MAIN Strategy", "db/trading.db")


def _seed_snapshot(symbol, classification, ts):
    rrepo.save_strategy_position_snapshot(
        ts=ts, symbol=symbol, broker_qty=10, main_qty=10, momentum_atr_qty=0,
        classification=classification.value if classification else None,
    )


def test_match_allowed():
    _seed_registry()
    _seed_snapshot("ABC.NS", Classification.MATCH, TODAY.isoformat())

    allowed, reason = pre_trade_check("ABC.NS", "main")

    assert allowed is True
    assert "MATCH" in reason


def test_duplicate_ownership_blocked():
    _seed_registry()
    _seed_snapshot("GOLDBEES.NS", Classification.DUPLICATE_OWNERSHIP, TODAY.isoformat())

    allowed, reason = pre_trade_check("GOLDBEES.NS", "main")

    assert allowed is False
    assert "DUPLICATE_OWNERSHIP" in reason


def test_new_symbol_no_history_allowed():
    """Registry populated (not a bootstrap case) but this specific symbol has
    never been through a reconciliation cycle -- genuinely new, not stale."""
    _seed_registry()

    allowed, reason = pre_trade_check("BRANDNEW.NS", "main")

    assert allowed is True
    assert "no reconciliation history" in reason


def test_stale_row_blocked():
    _seed_registry()
    stale_ts = (TODAY - timedelta(days=10)).isoformat()  # well past 2 trading sessions
    _seed_snapshot("OLD.NS", Classification.MATCH, stale_ts)

    allowed, reason = pre_trade_check("OLD.NS", "main")

    assert allowed is False
    assert "trading sessions old" in reason


def test_bootstrap_empty_registry_allowed(caplog):
    """reporting.db never initialized on this box (strategy_registry empty)
    -- the one deliberate exception to fail-closed, and it must be loud."""
    rrepo.init_db()  # schema exists, but zero rows in strategy_registry

    with caplog.at_level(logging.WARNING, logger="reconciliation.gate"):
        allowed, reason = pre_trade_check("ANY.NS", "main")

    assert allowed is True
    assert "bootstrap" in reason
    assert any("strategy_registry is empty" in r.message for r in caplog.records)


def test_gate_disabled_always_allowed():
    _seed_registry()
    _seed_snapshot("GOLDBEES.NS", Classification.DUPLICATE_OWNERSHIP, TODAY.isoformat())
    gate_module.PRE_TRADE_INTEGRITY_GATE_ENABLED = False

    allowed, reason = pre_trade_check("GOLDBEES.NS", "main")

    assert allowed is True
    assert "disabled" in reason


class TestBuyLoopPerSymbolContinue:
    """Integration: portfolio/manager.py must skip only the gate-blocked
    symbol, not the whole BUY cycle -- unlike the portfolio-wide drawdown
    check above it, which correctly does `break`."""

    class FakeBroker:
        def __init__(self, cash=100_000.0):
            self.cash = cash
            self.placed_orders = []

        def get_available_cash(self):
            return self.cash

        def get_portfolio_value(self):
            return 100_000.0

        def get_order_status(self, order_id):
            from broker.base import OrderResult, OrderStatus, OrderSide
            return OrderResult(order_id=order_id, status=OrderStatus.COMPLETE,
                                symbol="", side=OrderSide.SELL, requested_qty=0,
                                avg_price=100.0, raw_response={})

        def place_order_with_retry(self, req):
            self.placed_orders.append(req)
            from broker.base import OrderResult, OrderStatus
            return OrderResult(order_id=f"ORD{len(self.placed_orders)}", status=OrderStatus.COMPLETE,
                                symbol=req.symbol, side=req.side, requested_qty=req.quantity,
                                filled_qty=req.quantity, avg_price=req.gtt_trigger_price or 100.0,
                                raw_response={})

        def get_pending_gtt_orders(self, symbol):
            return []

        def cancel_gtt_order(self, gtt_id):
            return True

    def test_one_blocked_symbol_does_not_block_the_other(self, tmp_path, monkeypatch):
        import portfolio.manager as pm_module
        from portfolio.manager import PortfolioManager
        from db.models import Signal
        from db.repository import init_db

        monkeypatch.setattr("db.repository.DB_PATH", str(tmp_path / "gate_integration_test.db"))
        init_db()
        monkeypatch.setattr(pm_module, "_SCORE_HISTORY_PATH", str(tmp_path / "score_history.json"))
        monkeypatch.setattr(pm_module, "RIDE_WINNER_ENABLED", False)
        monkeypatch.setattr(pm_module, "ROTATION_ENABLED", False)
        monkeypatch.setattr(pm_module, "SCORE_DROP_EXIT_ENABLED", False)
        monkeypatch.setattr("notifications.telegram.send_message", lambda *a, **k: True)

        def fake_pre_trade_check(symbol, strategy_id):
            if symbol == "BLOCKED.NS":
                return False, "blocked: latest reconciliation classification is DUPLICATE_OWNERSHIP"
            return True, "allowed"
        monkeypatch.setattr(gate_module, "pre_trade_check", fake_pre_trade_check)

        broker = self.FakeBroker(cash=100_000.0)
        mgr = PortfolioManager(initial_capital=100_000.0, broker=broker)

        sig_blocked = Signal(date=TODAY, symbol="BLOCKED.NS", action="BUY",
                              score=95.0, price=200.0, reason="RS leader",
                              indicators={"sector": "IT", "atr": 2.0})
        sig_ok = Signal(date=TODAY, symbol="OK.NS", action="BUY",
                         score=90.0, price=150.0, reason="RS leader",
                         indicators={"sector": "Auto", "atr": 1.5})

        mgr.process_signals(
            TODAY, signals=[sig_blocked, sig_ok],
            prices={"BLOCKED.NS": 200.0, "OK.NS": 150.0},
            indicators={"BLOCKED.NS": {"atr": 2.0, "composite_rank": 95},
                        "OK.NS": {"atr": 1.5, "composite_rank": 90}},
            regime="BULL",
        )

        bought_symbols = {o.symbol for o in broker.placed_orders}
        assert "BLOCKED.NS" not in bought_symbols
        assert "OK.NS" in bought_symbols
