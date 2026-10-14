"""
momentum_atr v2 (2026-10-14): 5 names, keep while rank <= 10, swap off,
slot-capped realloc, drawdown exposure scaling, kill switch off by default
with cooldown reset, netted Upstox holdings, in-place position updates,
per-buy charges. Temp DB + fake broker only.
"""
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from broker.base import LivePosition
from momentum_atr.models import Position
from tests.test_momentum_atr_execution import (  # noqa: F401  (fixture)
    TODAY, FakeBroker, FlakyStatusBroker, _seed_positions, _seed_ranking, momentum_atr_env,
)


@pytest.fixture
def v2_env(momentum_atr_env, monkeypatch):
    """momentum_atr_env pins the v1 rules; restore the v2 defaults."""
    execution, repo, sent = momentum_atr_env
    import momentum_atr.risk as risk
    monkeypatch.setattr(execution, "PORTFOLIO_SIZE", 5)
    monkeypatch.setattr(execution, "KEEP_RANK", 10)
    monkeypatch.setattr(execution, "MOMENTUM_ATR_USE_SWAP_RULE", False)
    monkeypatch.setattr(execution, "MOMENTUM_ATR_REALLOC_NEW_SLOTS_ONLY", True)
    monkeypatch.setattr(risk, "MOMENTUM_ATR_DD_SCALE_ENABLED", True)
    return execution, repo, sent


def _rank(n):
    return [f"R{i}" for i in range(1, n + 1)]


def test_initial_fill_buys_five(v2_env):
    execution, repo, _ = v2_env
    repo.update_state(cash=10_000.0, peak_equity=10_000.0)
    closes = {s: 100.0 for s in _rank(8)}
    _seed_ranking(repo, closes, _rank(8))
    execution.run_daily(FakeBroker(closes), TODAY)
    assert {p.symbol for p in repo.load_positions("OPEN")} == set(_rank(5))


def test_holding_ranked_within_keep_rank_is_kept(v2_env):
    execution, repo, _ = v2_env
    held = ["R2", "R3", "R4", "R5", "R9"]               # R9 is rank 9 <= 10
    holdings = _seed_positions(repo, held)
    repo.update_state(cash=100.0, peak_equity=5100.0)
    closes = {s: 100.0 for s in _rank(12)}
    _seed_ranking(repo, closes, _rank(12))
    summary = execution.run_daily(FakeBroker(closes, holdings=holdings), TODAY)
    assert not [a for a in summary["actions"] if a["type"] == "RANK_EXIT_SELL"]
    assert {p.symbol for p in repo.load_positions("OPEN")} == set(held)


def test_rank_exit_beyond_keep_rank_refills_only_the_free_slot(v2_env):
    execution, repo, _ = v2_env
    held = ["R1", "R2", "R3", "R4", "R11"]               # R11 > keep rank 10
    holdings = _seed_positions(repo, held)
    repo.update_state(cash=100.0, peak_equity=5100.0)
    closes = {s: 100.0 for s in _rank(12)}
    _seed_ranking(repo, closes, _rank(12))
    summary = execution.run_daily(FakeBroker(closes, holdings=holdings), TODAY)
    realloc = [a for a in summary["actions"] if a["type"] == "RANK_EXIT_REALLOC"]
    assert realloc and set(realloc[0]["bought"]) == {"R5"}   # new name only, not a top-up of R1-R4
    assert {p.symbol for p in repo.load_positions("OPEN")} == {"R1", "R2", "R3", "R4", "R5"}


def test_swap_rule_off_by_default(v2_env):
    execution, repo, _ = v2_env
    holdings = _seed_positions(repo, ["R1", "R2", "R3", "R4", "R5"])
    repo.update_state(cash=100.0, peak_equity=5100.0)
    closes = {"R1": 90.0, "R2": 110.0, "R3": 100.0, "R4": 100.0, "R5": 100.0}
    _seed_ranking(repo, closes, _rank(5))
    summary = execution.run_daily(FakeBroker(closes, holdings=holdings), TODAY)
    assert not [a for a in summary["actions"] if a["type"].startswith("SWAP")]


def test_drawdown_scale_shrinks_allocation(v2_env, monkeypatch):
    execution, repo, _ = v2_env
    import momentum_atr.risk as risk
    repo.update_state(cash=7000.0, peak_equity=10_000.0)   # 30% drawdown
    assert risk.compute_drawdown_scale(7000.0, [], {}) == pytest.approx(0.55)  # 1 - (0.30-0.15)*3
    repo.update_state(cash=9000.0)
    assert risk.compute_drawdown_scale(9000.0, [], {}) == 1.0                  # 10% < 15% start
    assert risk.compute_drawdown_scale(1000.0, [], {}) == pytest.approx(0.25)  # floor


def test_kill_switch_off_by_default(v2_env):
    execution, repo, _ = v2_env
    holdings = _seed_positions(repo, ["R1"])
    repo.update_state(cash=1000.0, peak_equity=1_000_000.0)            # 99.9% drawdown
    closes = {s: 100.0 for s in _rank(6)}
    _seed_ranking(repo, closes, _rank(6))
    summary = execution.run_daily(FakeBroker(closes, holdings=holdings), TODAY)
    assert summary["kill_switch_tripped"] is False


def test_kill_switch_resets_after_cooldown_flat(v2_env, monkeypatch):
    _, repo, sent = v2_env
    import momentum_atr.risk as risk
    monkeypatch.setattr(risk, "MOMENTUM_ATR_DD_KILL_PCT", 0.35)
    repo.update_state(cash=5000.0, peak_equity=10_000.0, kill_switch_tripped=True,
                      kill_switch_tripped_date=TODAY - timedelta(days=25))
    assert risk.check_kill_switch(TODAY, 5000.0, [], {}) is False
    st = repo.get_state()
    assert st.kill_switch_tripped is False and st.peak_equity == 5000.0 and st.kill_switch_tripped_date is None
    # still inside the cooldown -> stays tripped
    repo.update_state(peak_equity=10_000.0, kill_switch_tripped=True,
                      kill_switch_tripped_date=TODAY - timedelta(days=5))
    assert risk.check_kill_switch(TODAY, 5000.0, [], {}) is True


def test_dry_run_kill_switch_changes_nothing(v2_env):
    _, repo, _ = v2_env
    import momentum_atr.risk as risk
    repo.update_state(cash=5000.0, peak_equity=1000.0)
    risk.check_kill_switch(TODAY, 5000.0, [], {}, persist=False)
    assert repo.get_state().peak_equity == 1000.0


def test_topup_keeps_position_id(v2_env):
    _, repo, _ = v2_env
    repo.save_position(Position(symbol="A", entry_date=TODAY, entry_price=100.0, shares=5))
    repo.save_position(Position(symbol="B", entry_date=TODAY, entry_price=100.0, shares=5))
    a = repo.get_position("A")
    repo.save_position(Position(symbol="A", entry_date=a.entry_date, entry_price=101.0, shares=9,
                                buy_charges_paid=40.0, id=a.id))
    order = [p.symbol for p in sorted(repo.load_positions("OPEN"), key=lambda p: p.id)]
    assert order == ["A", "B"]                      # A still exits first
    a2 = repo.get_position("A")
    assert (a2.id, a2.shares, a2.buy_charges_paid) == (a.id, 9, 40.0)


def test_rebought_symbol_gets_new_id(v2_env):
    _, repo, _ = v2_env
    from momentum_atr.models import Trade
    repo.save_position(Position(symbol="A", entry_date=TODAY, entry_price=100.0, shares=5))
    repo.save_position(Position(symbol="B", entry_date=TODAY, entry_price=100.0, shares=5))
    repo.close_position_and_save_trade("A", Trade(
        symbol="A", entry_date=TODAY, entry_price=100.0, shares=5, exit_date=TODAY, exit_price=100.0,
        gross_pnl=0.0, charges=0.0, net_pnl=0.0, exit_reason="T"))
    repo.save_position(Position(symbol="A", entry_date=TODAY, entry_price=100.0, shares=5))
    order = [p.symbol for p in sorted(repo.load_positions("OPEN"), key=lambda p: p.id)]
    assert order == ["B", "A"]


def test_sell_pnl_uses_actual_buy_charges(v2_env):
    execution, repo, _ = v2_env
    holdings = _seed_positions(repo, ["R11"])
    p = repo.get_position("R11")
    repo.save_position(Position(symbol="R11", entry_date=p.entry_date, entry_price=100.0, shares=10,
                                buy_charges_paid=60.0, id=p.id))       # two adds, Rs.30 each
    repo.update_state(cash=100.0, peak_equity=1100.0)
    closes = {s: 100.0 for s in _rank(12)}
    _seed_ranking(repo, closes, _rank(12))
    execution.run_daily(FakeBroker(closes, holdings=holdings), TODAY)
    t = [t for t in repo.load_trades() if t.symbol == "R11"][0]
    from charges.calculator import sell_charges
    assert t.charges == pytest.approx(60.0 + sell_charges(1000.0).total, abs=0.01)


def test_no_swap_into_winner_outside_keep_rank(momentum_atr_env):
    """v1 rules (swap on): B is the best performer but ranks outside the keep
    rank. It is NOT swapped into (it would be rank-exited the same day), and
    broker and ledger hold exactly the same shares afterwards."""
    execution, repo, _ = momentum_atr_env
    holdings = _seed_positions(repo, ["A", "B", "C"])
    repo.update_state(cash=100.0, peak_equity=3200.0)
    closes = {"A": 96.0, "B": 104.0, "C": 101.0, "D": 110.0, "E": 105.0}
    _seed_ranking(repo, closes, ["D", "C", "E", "B", "A"])
    broker = FlakyStatusBroker(closes, holdings=holdings)
    summary = execution.run_daily(broker, TODAY)
    assert not [a for a in summary["actions"] if a["type"] == "SWAP_BUY"]
    assert {h.symbol: h.quantity for h in broker.holdings if h.quantity > 0} == \
        {p.symbol: p.shares for p in repo.load_positions("OPEN")}


def test_upstox_net_holdings_nets_same_day_trades():
    """Payloads as seen live 2026-10-14 after selling 7 MOREPENLAB and buying 2 SHANTIGOLD."""
    from broker.upstox import UpstoxBroker
    b = UpstoxBroker.__new__(UpstoxBroker)
    b._base_url, b._headers = "x", {}
    lt = MagicMock(); lt.json.return_value = {"data": [
        {"tradingsymbol": "SHANTIGOLD", "quantity": 166, "average_price": 380, "last_price": 390, "pnl": 0},
        {"tradingsymbol": "MOREPENLAB", "quantity": 7, "average_price": 120, "last_price": 118, "pnl": 0}]}
    st = MagicMock(); st.json.return_value = {"data": [
        {"tradingsymbol": "MOREPENLAB", "product": "D", "quantity": -7, "average_price": 0, "last_price": 118},
        {"tradingsymbol": "SHANTIGOLD", "product": "D", "quantity": 2, "average_price": 391, "last_price": 390},
        {"tradingsymbol": "NEWBUY", "product": "D", "quantity": 4, "average_price": 50, "last_price": 51},
        {"tradingsymbol": "INTRA", "product": "I", "quantity": 9, "average_price": 10, "last_price": 10}]}
    b._session = MagicMock()
    b._session.get.side_effect = lambda url, **k: lt if "long-term" in url else st
    net = {h.symbol: h.quantity for h in b.get_net_holdings_or_none()}
    assert net == {"SHANTIGOLD.NS": 168, "NEWBUY.NS": 4}

    b._session.get.side_effect = Exception("down")
    assert b.get_net_holdings_or_none() is None


def test_self_heal_does_not_confirm_sell_on_failed_holdings_read(v2_env):
    execution, _, _ = v2_env
    broker = MagicMock()
    broker.get_net_holdings_or_none.return_value = None
    assert execution._self_heal_via_holdings(broker, "SELL", "A", 10, 10, MagicMock()) is None
