"""
Execution-logic tests for portfolio/manager.py.

Uses a FakeBroker double (no real API calls) against a temp SQLite DB, so
these run in-process in seconds. Scope: the exact bug classes found live on
2026-07-01/02 and fixed the same day —

  - trail-breach immediate exit (commit bcf4441): a same-run trailing-stop
    ratchet that lands the new stop AT/ABOVE current price must trigger an
    immediate MARKET sell, not a doomed GTT (Upstox executes GTT-SINGLE as an
    unfillable LIMIT at the trigger price once price has already passed it).
  - GTT cancel-failure blocks replacement (commit 162726b): if cancelling the
    stale GTT fails, the new one must NOT be placed (avoids the CGPOWER
    duplicate-GTT incident).
  - trailing_stop/peak_price persisted to DB on ratchet-only runs
    (commit c5460f6): a run with no buy/sell must still save the new trail.
  - SAFE_HAVEN_SYMBOL (GOLDBEES) is excluded from the generic ATR trailing
    stop ratchet entirely (commit 96df9bd) — it has its own static floor.

Strategy scoring/ranking/backtest logic is out of scope here — see
tests/test_signals.py and tests/test_portfolio.py for that.
"""
import pytest
from datetime import date

import portfolio.manager as pm_module
from portfolio.manager import PortfolioManager
from db.models import Position
from db.repository import init_db, load_positions, save_position
from broker.base import OrderResult, OrderStatus, OrderSide, OrderType
from config.settings import SAFE_HAVEN_SYMBOL

TODAY = date(2026, 7, 2)


class FakeBroker:
    """Minimal broker double covering every method portfolio.manager calls.
    No network, no real orders — just enough state to assert on.

    Note: cash must be > 0 — PortfolioManager._load_state() treats an
    available-cash of exactly 0 as a broker API error and falls back to the
    DB-tracked cash instead (see manager.py's "[Live] Broker returned ₹0
    cash" branch), which would defeat tests relying on a low-cash broker to
    keep the buy/pyramid path from firing."""

    def __init__(self, cash=1.0, portfolio_value=100000.0):
        self.cash = cash
        self.portfolio_value_ = portfolio_value
        self.placed_orders = []            # list[OrderRequest]
        self.cancelled_gtt_ids = []
        self.pending_gtts = {}             # symbol -> [gtt_id, ...]
        self.cancel_should_fail = set()    # gtt_ids that fail to cancel

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
    """Redirect the DB and score-history file to tmp, and disable the
    rotation/ride-winner/score-drop features (out of scope here, and their
    multi-position preconditions would otherwise interfere with these
    single-position, single-purpose tests)."""
    monkeypatch.setattr("db.repository.DB_PATH", str(tmp_path / "test.db"))
    init_db()
    monkeypatch.setattr(pm_module, "_SCORE_HISTORY_PATH", str(tmp_path / "score_history.json"))
    monkeypatch.setattr(pm_module, "RIDE_WINNER_ENABLED", False)
    monkeypatch.setattr(pm_module, "ROTATION_ENABLED", False)
    monkeypatch.setattr(pm_module, "SCORE_DROP_EXIT_ENABLED", False)
    # Never let a test fire a real Telegram alert.
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


# ── Trail-breach immediate exit (bcf4441) ──────────────────────────────────

def test_trail_breach_triggers_immediate_market_sell(monkeypatch):
    pos = make_position(symbol="ABC.NS", trailing_stop=90.0, peak_price=110.0)
    broker = FakeBroker()
    mgr = make_manager(broker, [pos])

    # Simulate an aggressive same-run ratchet that lands the new stop AT the
    # current price — the exact GOLDBEES 2026-07-01 scenario, generalized.
    def fake_update(p, cp, atr=0, regime=None):
        p.trailing_stop = cp
        p.peak_price = cp
    monkeypatch.setattr(pm_module, "update_trailing_stop", fake_update)

    cp = 95.0
    mgr.process_signals(TODAY, signals=[], prices={"ABC.NS": cp},
                         indicators={"ABC.NS": {"atr": 1.0}}, regime="BULL")

    assert not any(p.symbol == "ABC.NS" for p in mgr.open_positions)
    market_sells = [o for o in broker.placed_orders
                    if o.symbol == "ABC.NS" and o.side == OrderSide.SELL and not o.is_gtt]
    assert len(market_sells) == 1
    assert all(p.symbol != "ABC.NS" for p in load_positions(status="OPEN"))
    # No GTT should have been placed for a breach — it exits via market order.
    assert "ABC.NS" not in broker.pending_gtts or broker.pending_gtts["ABC.NS"] == []


def test_ratchet_landing_above_price_also_breaches(monkeypatch):
    """A ratchet that overshoots past current price (not just equals it) must
    also be caught — the bug was `>`, the fix checks `>=`."""
    pos = make_position(symbol="ABC.NS", trailing_stop=90.0, peak_price=110.0)
    broker = FakeBroker()
    mgr = make_manager(broker, [pos])

    def fake_update(p, cp, atr=0, regime=None):
        p.trailing_stop = cp + 5.0  # overshoots above current price
        p.peak_price = cp
    monkeypatch.setattr(pm_module, "update_trailing_stop", fake_update)

    mgr.process_signals(TODAY, signals=[], prices={"ABC.NS": 95.0},
                         indicators={"ABC.NS": {"atr": 1.0}}, regime="BEAR")

    assert not any(p.symbol == "ABC.NS" for p in mgr.open_positions)


# ── Normal ratchet: GTT refresh path, no premature exit ───────────────────

def test_normal_ratchet_refreshes_gtt_and_persists(monkeypatch):
    pos = make_position(symbol="ABC.NS", trailing_stop=90.0, peak_price=100.0)
    broker = FakeBroker()
    mgr = make_manager(broker, [pos])

    def fake_update(p, cp, atr=0, regime=None):
        p.trailing_stop = 92.0  # ratchets up but stays well below current price
        p.peak_price = cp
    monkeypatch.setattr(pm_module, "update_trailing_stop", fake_update)

    cp = 105.0
    mgr.process_signals(TODAY, signals=[], prices={"ABC.NS": cp},
                         indicators={"ABC.NS": {"atr": 1.0}}, regime="BULL")

    # Position stays open — no premature exit.
    assert any(p.symbol == "ABC.NS" for p in mgr.open_positions)
    assert not [o for o in broker.placed_orders
                if o.symbol == "ABC.NS" and not o.is_gtt]

    # Exactly one GTT placed, at the new trailing stop.
    gtts = [o for o in broker.placed_orders if o.symbol == "ABC.NS" and o.is_gtt]
    assert len(gtts) == 1
    assert gtts[0].gtt_trigger_price == 92.0

    # Persisted to DB even though nothing was bought/sold this run (c5460f6).
    saved = next(p for p in load_positions(status="OPEN") if p.symbol == "ABC.NS")
    assert saved.trailing_stop == 92.0
    assert saved.peak_price == cp


def test_unchanged_trailing_stop_does_not_touch_gtt(monkeypatch):
    """If the ratchet doesn't move the stop, no cancel/replace churn."""
    pos = make_position(symbol="ABC.NS", trailing_stop=90.0, peak_price=100.0)
    broker = FakeBroker()
    mgr = make_manager(broker, [pos])

    def fake_update(p, cp, atr=0, regime=None):
        pass  # no-op: trailing stop stays exactly where it was
    monkeypatch.setattr(pm_module, "update_trailing_stop", fake_update)

    mgr.process_signals(TODAY, signals=[], prices={"ABC.NS": 105.0},
                         indicators={"ABC.NS": {"atr": 1.0}}, regime="BULL")

    assert broker.placed_orders == []


# ── GOLDBEES excluded from generic ATR ratchet (96df9bd) ──────────────────

def test_safe_haven_symbol_excluded_from_ratchet(monkeypatch):
    pos = make_position(symbol=SAFE_HAVEN_SYMBOL, trailing_stop=113.15, peak_price=120.0)
    broker = FakeBroker()
    mgr = make_manager(broker, [pos])

    calls = []
    def fake_update(p, cp, atr=0, regime=None):
        calls.append(p.symbol)
        p.trailing_stop = cp  # would breach if it were ever called
    monkeypatch.setattr(pm_module, "update_trailing_stop", fake_update)

    mgr.process_signals(TODAY, signals=[], prices={SAFE_HAVEN_SYMBOL: 115.0},
                         indicators={SAFE_HAVEN_SYMBOL: {"atr": 1.0}}, regime="BEAR")

    assert calls == []  # update_trailing_stop never invoked for the safe haven
    assert any(p.symbol == SAFE_HAVEN_SYMBOL for p in mgr.open_positions)
    saved = next(p for p in load_positions(status="OPEN") if p.symbol == SAFE_HAVEN_SYMBOL)
    assert saved.trailing_stop == 113.15  # untouched


# ── GTT cancel-failure blocks replacement (162726b) ────────────────────────

def test_cancel_failure_skips_replacement_no_duplicate(monkeypatch):
    pos = make_position(symbol="XYZ.NS", trailing_stop=95.0, peak_price=100.0)
    broker = FakeBroker()
    broker.pending_gtts["XYZ.NS"] = ["OLD1"]
    broker.cancel_should_fail = {"OLD1"}
    mgr = make_manager(broker, [pos])
    mgr._gtt_needs_refresh = {"XYZ.NS"}
    mgr._gtt_synced_this_run = set()

    mgr._reconcile_gtt_stops()

    # Old GTT still there (cancel failed) — and no second one was placed.
    assert broker.pending_gtts["XYZ.NS"] == ["OLD1"]
    new_gtts = [o for o in broker.placed_orders if o.symbol == "XYZ.NS" and o.is_gtt]
    assert new_gtts == []


def test_cancel_success_places_single_replacement_gtt(monkeypatch):
    pos = make_position(symbol="XYZ.NS", trailing_stop=95.0, peak_price=100.0)
    broker = FakeBroker()
    broker.pending_gtts["XYZ.NS"] = ["OLD1"]
    mgr = make_manager(broker, [pos])
    mgr._gtt_needs_refresh = {"XYZ.NS"}
    mgr._gtt_synced_this_run = set()

    mgr._reconcile_gtt_stops()

    assert "OLD1" in broker.cancelled_gtt_ids
    new_gtts = [o for o in broker.placed_orders if o.symbol == "XYZ.NS" and o.is_gtt]
    assert len(new_gtts) == 1
    assert new_gtts[0].gtt_trigger_price == 95.0
    # Exactly one GTT left pending for the symbol (no duplicate).
    assert len(broker.pending_gtts["XYZ.NS"]) == 1


def test_reconcile_skips_symbols_already_synced_this_run(monkeypatch):
    """A position just (re)placed by a BUY/ADD/ROTATE this run already has a
    fresh GTT — reconcile must not touch it again."""
    pos = make_position(symbol="XYZ.NS", trailing_stop=95.0, peak_price=100.0)
    broker = FakeBroker()
    mgr = make_manager(broker, [pos])
    mgr._gtt_needs_refresh = {"XYZ.NS"}
    mgr._gtt_synced_this_run = {"XYZ.NS"}  # already handled this run

    mgr._reconcile_gtt_stops()

    assert broker.placed_orders == []
    assert broker.cancelled_gtt_ids == []
