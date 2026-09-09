"""
Tests for db/momentum_atr_repo.py::reduce_position_and_save_trade() --
new function, added alongside the 2026-09-09 broker-truth reconciliation
correction (CYIENT.NS/WELCORP.NS partial share loss). Mirrors
db/repository.py's MAIN equivalent, which has no existing test coverage
of its own to match against.
"""
from datetime import date

import pytest

from momentum_atr.models import Position, Trade


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from db import momentum_atr_repo as m_repo

    db_path = str(tmp_path / "momentum_atr_test.db")
    monkeypatch.setattr("db.momentum_atr_repo.MOMENTUM_ATR_DB_PATH", db_path)
    m_repo.init_db()
    return m_repo


def test_reduce_position_keeps_remainder_open_and_records_lost_shares_as_trade(repo):
    repo.save_position(Position(
        symbol="CYIENT.NS", entry_date=date(2026, 9, 1), entry_price=1150.38,
        shares=6, status="OPEN", entry_order_id="260901000015136",
    ))

    trade = Trade(
        symbol="CYIENT.NS", entry_date=date(2026, 9, 1), exit_date=date(2026, 9, 9),
        entry_price=1150.38, exit_price=0.0, shares=3,
        gross_pnl=-1150.38 * 3, charges=0.0, net_pnl=-1150.38 * 3,
        exit_reason="RECONCILED_SHARES_LOST_MAIN_MANUAL_LIQUIDATION", exit_order_id="",
    )
    repo.reduce_position_and_save_trade("CYIENT.NS", remaining_shares=3, t=trade)

    remaining = repo.get_position("CYIENT.NS")
    assert remaining.status == "OPEN"
    assert remaining.shares == 3
    assert remaining.entry_price == 1150.38  # unchanged
    assert remaining.entry_order_id == "260901000015136"  # unchanged

    trades = repo.load_trades()
    assert len(trades) == 1
    assert trades[0].shares == 3
    assert trades[0].net_pnl == pytest.approx(-3451.14)


def test_reduce_position_raises_when_no_open_position(repo):
    trade = Trade(
        symbol="NOPOS.NS", entry_date=date(2026, 9, 1), exit_date=date(2026, 9, 9),
        entry_price=100.0, exit_price=0.0, shares=1,
        gross_pnl=-100.0, charges=0.0, net_pnl=-100.0,
        exit_reason="TEST", exit_order_id="",
    )
    with pytest.raises(ValueError, match="No OPEN position"):
        repo.reduce_position_and_save_trade("NOPOS.NS", remaining_shares=0, t=trade)
