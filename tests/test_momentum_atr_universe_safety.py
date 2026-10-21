"""docs/83: universe-removal forced-exit risk (P0) and missing exit price (P2).
Fake broker + temp DB only (fixtures from test_momentum_atr_execution)."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from broker.base import OrderResult, OrderStatus
from tests.test_momentum_atr_execution import (  # noqa: F401  (fixture re-export)
    FakeBroker, FlakyStatusBroker, TODAY, _seed_positions, _seed_ranking, momentum_atr_env,
)


# ----------------------------------------------------------------- helpers
def _bars(closes, volume=1_000_000, end="2026-08-05"):
    idx = pd.bdate_range(end=end, periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": volume}, index=idx)


def _trend(start, end, n=260):
    return list(np.linspace(start, end, n))


@pytest.fixture
def fetch(monkeypatch):
    """Install a fake data.fetcher.fetch_all over a dict the test fills."""
    import data.fetcher as fetcher
    store = {}
    monkeypatch.setattr(fetcher, "fetch_all", lambda symbols, live_mode=True: {s: store[s] for s in symbols if s in store})
    return store


def _orders(broker):
    return [(r.side, r.symbol) for r in broker._orders.values()]


class NoPriceBroker(FakeBroker):
    """Confirms fills COMPLETE but returns avg_price 0 for `no_fill_price`
    symbols, and LTP 0 for `no_ltp` symbols."""

    def __init__(self, *a, no_fill_price=(), no_ltp=(), **k):
        super().__init__(*a, **k)
        self.no_fill_price, self.no_ltp = set(no_fill_price), set(no_ltp)

    def place_order(self, request):
        res = super().place_order(request)
        if request.symbol in self.no_fill_price:
            res = OrderResult(order_id=res.order_id, status=OrderStatus.COMPLETE, symbol=res.symbol, side=res.side,
                              requested_qty=res.requested_qty, filled_qty=res.filled_qty, avg_price=0.0)
            self._orders[res.order_id] = res
        return res

    def get_ltp(self, symbol):
        return 0.0 if symbol in self.no_ltp else super().get_ltp(symbol)


# ============================================================ P0: ranking input
def test_1_held_symbol_removed_from_universe_is_still_scored(fetch):
    from momentum_atr.scoring import build_ranking_inputs
    fetch.update({"U1": _bars(_trend(100, 150)), "U2": _bars(_trend(100, 140)), "GONE": _bars(_trend(100, 200))})
    ranked, closes, rep = build_ranking_inputs(["U1", "U2"], held=["GONE"])
    assert "GONE" in ranked and "GONE" in closes          # scored like any other symbol
    assert rep["held_outside_universe"] == ["GONE"]
    assert rep["held_data_missing"] == {}


def test_4_held_symbol_back_in_universe_is_not_flagged(fetch):
    from momentum_atr.scoring import build_ranking_inputs
    fetch.update({"U1": _bars(_trend(100, 150)), "BACK": _bars(_trend(100, 200))})
    ranked, closes, rep = build_ranking_inputs(["U1", "BACK"], held=["BACK"])
    assert rep["held_outside_universe"] == [] and "BACK" in ranked


def test_3_held_symbol_with_missing_or_stale_data_is_flagged_not_ranked(fetch):
    from momentum_atr.scoring import build_ranking_inputs
    fetch.update({"U1": _bars(_trend(100, 150)), "U2": _bars(_trend(100, 140)),
                  "STALE": _bars(_trend(100, 200), end="2026-07-30")})   # no NODATA entry at all
    ranked, closes, rep = build_ranking_inputs(["U1", "U2", "STALE"], held=["NODATA", "STALE"])
    assert rep["held_data_missing"]["NODATA"] == "no_data"
    assert rep["held_data_missing"]["STALE"].startswith("stale_last_bar_2026-07-30")
    for s in ("NODATA", "STALE"):
        assert s not in closes and s not in ranked


def test_3b_unheld_symbol_with_stale_bar_is_not_ranked_and_reported(fetch):
    """2026-10-21: HFCL lost its Upstox key and was ranked on a frozen
    2026-09-25 close for 4 weeks. A stale unheld symbol must drop out."""
    from momentum_atr.scoring import build_ranking_inputs
    fetch.update({"U1": _bars(_trend(100, 150)), "U2": _bars(_trend(100, 140)),
                  "FROZEN": _bars(_trend(100, 200), end="2026-07-30")})
    ranked, closes, rep = build_ranking_inputs(["U1", "U2", "FROZEN", "NOKEY"], held=[])
    assert "FROZEN" not in ranked and "FROZEN" not in closes
    assert rep["stale_unheld"] == {"FROZEN": "2026-07-30"}
    assert rep["no_data_unheld"] == ["NOKEY"]
    assert set(ranked) == {"U1", "U2"}


def test_5_held_symbol_below_volume_floor_stays_a_genuine_exit(fetch):
    from momentum_atr.scoring import build_ranking_inputs
    fetch.update({"U1": _bars(_trend(100, 150)), "THIN": _bars(_trend(100, 200), volume=50_000)})
    ranked, closes, rep = build_ranking_inputs(["U1", "THIN"], held=["THIN"])
    assert "THIN" not in ranked                # ineligible -> no rank -> rank-exited as before
    assert closes["THIN"] == pytest.approx(200.0)  # but its real close is kept: NOT "data missing"
    assert rep["held_volume_floor"] == ["THIN"] and rep["held_data_missing"] == {}


def test_10_scoring_without_diagnostics_is_unchanged(fetch):
    """MAIN / every other caller passes no diagnostics: identical output."""
    from momentum_atr.scoring import compute_live_scores
    fetch.update({"A": _bars(_trend(100, 150)), "B": _bars(_trend(150, 100)), "T": _bars(_trend(100, 120), volume=10)})
    s0, c0 = compute_live_scores(["A", "B", "T"])
    diag = {}
    s1, c1 = compute_live_scores(["A", "B", "T"], diagnostics=diag)
    assert (s0, c0) == (s1, c1)
    assert diag["T"]["status"] == "volume_floor" and diag["A"]["status"] == "scored"


def test_10_universe_module_behaviour_unchanged():
    from config.watchlist_nse import ALL_SYMBOLS
    import data.universe as u
    orig = u._get_dynamic_symbols
    try:
        u._get_dynamic_symbols = lambda: []
        assert u.get_all_symbols() == ALL_SYMBOLS
        u._get_dynamic_symbols = lambda: [ALL_SYMBOLS[0], "ZZNEW.NS"]
        assert u.get_all_symbols() == ALL_SYMBOLS + ["ZZNEW.NS"]
    finally:
        u._get_dynamic_symbols = orig


# ============================================================ P0: 09:17 execution
def test_9_universe_removed_top3_holding_generates_no_orders(momentum_atr_env):
    """GONE left the universe but the precompute (fixed) still ranked it #2."""
    execution, repo, _ = momentum_atr_env
    holdings = _seed_positions(repo, ["A", "GONE", "C"])
    repo.update_state(cash=100.0, peak_equity=3200.0)
    closes = {"A": 100.5, "GONE": 100.5, "C": 100.5, "D": 100.5}
    _seed_ranking(repo, closes, ["A", "GONE", "C", "D"])
    broker = FakeBroker(closes, holdings=holdings)
    summary = execution.run_daily(broker, TODAY)
    assert _orders(broker) == []
    assert {p.symbol for p in repo.load_positions("OPEN")} == {"A", "GONE", "C"}
    assert not [a for a in summary["actions"] if a["type"] in ("RANK_EXIT_SELL", "SWAP_SELL")]


def test_2_legitimate_rank_drop_still_exits(momentum_atr_env):
    execution, repo, _ = momentum_atr_env
    holdings = _seed_positions(repo, ["A", "B", "GONE"])
    repo.update_state(cash=100.0, peak_equity=3200.0)
    closes = {"A": 100.5, "B": 100.5, "GONE": 100.5, "D": 100.5}
    _seed_ranking(repo, closes, ["A", "B", "D", "GONE"])      # GONE genuinely rank 4
    broker = FakeBroker(closes, holdings=holdings)
    summary = execution.run_daily(broker, TODAY)
    assert ("SELL", "GONE") in [(s.value if hasattr(s, "value") else s, sym) for s, sym in _orders(broker)]
    assert "GONE" not in {p.symbol for p in repo.load_positions("OPEN")}
    assert any(a["type"] == "RANK_EXIT_SELL" for a in summary["actions"])


def test_3b_held_symbol_missing_from_ranking_data_is_held_and_alerted(momentum_atr_env):
    execution, repo, sent = momentum_atr_env
    holdings = _seed_positions(repo, ["A", "B", "NODATA"])
    repo.update_state(cash=100.0, peak_equity=3200.0)
    closes = {"A": 100.5, "B": 100.5, "D": 100.5}           # NODATA absent from closes
    _seed_ranking(repo, closes, ["A", "B", "D"])
    broker = FakeBroker(closes, holdings=holdings)
    summary = execution.run_daily(broker, TODAY)
    assert "NODATA" in {p.symbol for p in repo.load_positions("OPEN")}
    assert not [o for o in _orders(broker) if o[1] == "NODATA"]
    assert {"type": "HOLD_DATA_MISSING", "symbols": ["NODATA"]} in summary["actions"]
    assert any("no ranking data" in m for k, m in sent if k == "error")


def test_5b_multiple_holdings_one_ineligible_exits_others_kept(momentum_atr_env):
    """THIN is held but below the volume floor: the fixed precompute keeps
    its close and gives it no rank -> it is rank-exited, others untouched."""
    execution, repo, _ = momentum_atr_env
    holdings = _seed_positions(repo, ["A", "B", "THIN"])
    repo.update_state(cash=100.0, peak_equity=3200.0)
    closes = {"A": 100.5, "B": 100.5, "THIN": 100.5, "D": 100.5}
    _seed_ranking(repo, closes, ["A", "B", "D"])             # THIN has a close but no rank
    broker = FakeBroker(closes, holdings=holdings)
    execution.run_daily(broker, TODAY)
    after = {p.symbol for p in repo.load_positions("OPEN")}
    assert "THIN" not in after and {"A", "B"} <= after


def test_7_repeated_run_same_day_places_no_duplicate_orders(momentum_atr_env):
    execution, repo, _ = momentum_atr_env
    holdings = _seed_positions(repo, ["A", "B", "GONE"])
    repo.update_state(cash=100.0, peak_equity=3200.0)
    closes = {"A": 100.5, "B": 100.5, "GONE": 100.5, "D": 100.5}
    _seed_ranking(repo, closes, ["A", "B", "GONE", "D"])
    broker = FakeBroker(closes, holdings=holdings)
    execution.run_daily(broker, TODAY)
    execution.run_daily(broker, TODAY)
    assert _orders(broker) == []


def test_8_partial_fill_on_sell_leaves_ledger_untouched(momentum_atr_env):
    execution, repo, sent = momentum_atr_env
    holdings = _seed_positions(repo, ["A", "B", "GONE"])
    repo.update_state(cash=100.0, peak_equity=3200.0)
    closes = {"A": 100.5, "B": 100.5, "GONE": 100.5, "D": 100.5}
    _seed_ranking(repo, closes, ["A", "B", "D", "GONE"])
    broker = FakeBroker(closes, holdings=holdings)
    broker.force_partial["GONE"] = 4
    execution.run_daily(broker, TODAY)
    assert "GONE" in {p.symbol for p in repo.load_positions("OPEN")}
    assert repo.load_trades() == []


def test_6_restart_recovery_precompute_is_idempotent(monkeypatch, tmp_path, fetch):
    """Positions persist in the DB across a process restart; a second
    precompute run the same day skips instead of recomputing."""
    import importlib
    import sys as _sys
    from db import momentum_atr_repo as repo
    from momentum_atr.models import Position
    monkeypatch.setattr("db.momentum_atr_repo.MOMENTUM_ATR_DB_PATH", str(tmp_path / "m.db"))
    repo.init_db()
    repo.save_position(Position(symbol="GONE", entry_date=TODAY - timedelta(days=3), entry_price=100.0, shares=5))
    fetch.update({f"U{i}": _bars(_trend(100, 120 + i)) for i in range(25)})
    fetch["GONE"] = _bars(_trend(100, 300))
    _sys.path.insert(0, "scripts")
    pre = importlib.import_module("precompute_momentum_atr_ranking")
    monkeypatch.setattr("runner.daily_runner._is_market_holiday", lambda d: False)
    monkeypatch.setattr("data.universe.get_all_symbols", lambda: [f"U{i}" for i in range(25)])
    monkeypatch.setattr(pre, "_alert_abort", lambda *a: None)

    class _D(date):
        @classmethod
        def today(cls):
            return TODAY
    monkeypatch.setattr(pre, "date", _D)
    assert pre.main() == 0
    ranked, closes = repo.load_daily_ranking(TODAY)
    assert "GONE" in ranked and "GONE" in closes            # held, outside universe, still ranked
    assert pre.main() == 0                                  # second run: skip, no recompute
    assert repo.load_daily_ranking(TODAY) == (ranked, closes)


# ============================================================ P2: exit price
def _exit_setup(repo):
    holdings = _seed_positions(repo, ["A", "B", "X"])
    repo.update_state(cash=100.0, peak_equity=3200.0)
    closes = {"A": 100.5, "B": 100.5, "X": 100.5, "D": 100.5}
    _seed_ranking(repo, closes, ["A", "B", "D", "X"])        # X rank 4 -> rank exit
    return holdings, closes


def test_p2_missing_fill_price_uses_alerted_ltp_estimate(momentum_atr_env):
    execution, repo, sent = momentum_atr_env
    holdings, closes = _exit_setup(repo)
    broker = NoPriceBroker({**closes, "X": 120.0}, holdings=holdings, no_fill_price={"X"})
    execution.run_daily(broker, TODAY)
    t = [t for t in repo.load_trades() if t.symbol == "X"][0]
    assert t.exit_price == pytest.approx(120.0) and t.net_pnl is not None
    assert t.exit_reason.endswith("|EXIT_PRICE_ESTIMATED_LTP")
    assert any("ESTIMATE" in m for k, m in sent if k == "error")


def test_p2_no_price_at_all_is_unresolved_not_zero(momentum_atr_env):
    execution, repo, sent = momentum_atr_env
    holdings, closes = _exit_setup(repo)
    broker = NoPriceBroker(closes, holdings=holdings, no_fill_price={"X"}, no_ltp={"X"})
    summary = execution.run_daily(broker, TODAY)
    t = [t for t in repo.load_trades() if t.symbol == "X"][0]
    assert t.exit_price is None and t.net_pnl is None and t.gross_pnl is None
    assert t.exit_reason.endswith("|EXIT_PRICE_UNRESOLVED")
    assert "X" not in {p.symbol for p in repo.load_positions("OPEN")}   # shares are gone: position closed
    assert summary["cash"] == pytest.approx(100.0)                      # no proceeds credited
    assert any("UNRESOLVED" in m for k, m in sent if k == "error")


def test_p2_resolve_once_after_restart_then_idempotent(momentum_atr_env):
    execution, repo, _ = momentum_atr_env
    holdings, closes = _exit_setup(repo)
    execution.run_daily(NoPriceBroker(closes, holdings=holdings, no_fill_price={"X"}, no_ltp={"X"}), TODAY)
    tid = [t for t in repo.load_trades() if t.symbol == "X"][0].id
    cash0 = repo.get_state().cash
    # "restart": fresh reads from the DB only
    credit = execution.resolve_unresolved_exit(tid, 110.0)
    assert credit and credit > 0
    t = repo.get_trade(tid)
    assert t.exit_price == pytest.approx(110.0) and t.net_pnl is not None
    assert t.exit_reason.endswith("|EXIT_PRICE_RESOLVED")
    assert repo.get_state().cash == pytest.approx(cash0 + credit)
    assert execution.resolve_unresolved_exit(tid, 110.0) is None     # second call: nothing
    assert repo.get_state().cash == pytest.approx(cash0 + credit)   # cash credited exactly once


def test_p2_resolve_refuses_normal_trade(momentum_atr_env):
    execution, repo, _ = momentum_atr_env
    holdings, closes = _exit_setup(repo)
    execution.run_daily(FakeBroker(closes, holdings=holdings), TODAY)
    tid = [t for t in repo.load_trades() if t.symbol == "X"][0].id
    cash0 = repo.get_state().cash
    assert execution.resolve_unresolved_exit(tid, 999.0) is None
    assert repo.get_state().cash == pytest.approx(cash0)
    with pytest.raises(ValueError):
        execution.resolve_unresolved_exit(tid, 0.0)


def test_p2_delayed_broker_response_still_records_exact_price(momentum_atr_env, monkeypatch):
    execution, repo, _ = momentum_atr_env
    monkeypatch.setattr(execution.time, "sleep", lambda s: None)
    holdings, closes = _exit_setup(repo)
    broker = FlakyStatusBroker(closes, holdings=holdings, unknown_polls=2)
    execution.run_daily(broker, TODAY)
    t = [t for t in repo.load_trades() if t.symbol == "X"][0]
    assert t.exit_price == pytest.approx(100.5) and not t.exit_reason.endswith(("ESTIMATED_LTP", "UNRESOLVED"))
