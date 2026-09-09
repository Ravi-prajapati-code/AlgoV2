"""
Tests for scripts/apply_broker_truth_corrections_20260909.py -- the
2026-09-09 broker-truth reconciliation correction (ASIANENE/ATHERENERG/
CYIENT/WELCORP). Exercises the per-symbol apply_momentum_atr()/apply_main()
functions directly against tmp-path DBs; never touches the real ones.
"""
from datetime import date

import pytest

import importlib
correction = importlib.import_module("scripts.apply_broker_truth_corrections_20260909")


@pytest.fixture
def atr_repo(tmp_path, monkeypatch):
    from db import momentum_atr_repo as m_repo
    db_path = str(tmp_path / "momentum_atr_test.db")
    monkeypatch.setattr("db.momentum_atr_repo.MOMENTUM_ATR_DB_PATH", db_path)
    m_repo.init_db()
    return m_repo


@pytest.fixture
def main_repo(tmp_path, monkeypatch):
    from db import repository as repo
    db_path = str(tmp_path / "trading_test.db")
    monkeypatch.setattr("db.repository.DB_PATH", db_path)
    repo.init_db()
    return repo


def test_apply_momentum_atr_reduce_writes_offsetting_loss_no_cash_credit(atr_repo):
    from momentum_atr.models import Position
    atr_repo.save_position(Position(
        symbol="CYIENT.NS", entry_date=date(2026, 9, 1), entry_price=1150.38,
        shares=6, status="OPEN", entry_order_id="260901000015136",
    ))

    msg = correction.apply_momentum_atr(
        "CYIENT.NS", 3, "RECONCILED_SHARES_LOST_MAIN_MANUAL_LIQUIDATION", apply=True
    )

    assert "REDUCE" in msg
    remaining = atr_repo.get_position("CYIENT.NS")
    assert remaining.status == "OPEN"
    assert remaining.shares == 3
    trades = atr_repo.load_trades()
    assert len(trades) == 1
    assert trades[0].shares == 3
    assert trades[0].exit_price == 0.0
    assert trades[0].net_pnl == pytest.approx(-1150.38 * 3)
    assert trades[0].exit_reason == "RECONCILED_SHARES_LOST_MAIN_MANUAL_LIQUIDATION"


def test_apply_momentum_atr_close_removes_open_position(atr_repo):
    from momentum_atr.models import Position
    atr_repo.save_position(Position(
        symbol="ATHERENERG.NS", entry_date=date(2026, 9, 1), entry_price=1683.5,
        shares=1, status="OPEN", entry_order_id="260901000015142",
    ))

    msg = correction.apply_momentum_atr(
        "ATHERENERG.NS", 0, "RECONCILED_SHARES_LOST_UNEXPLAINED_BROKER_GAP", apply=True
    )

    assert "CLOSE" in msg
    assert atr_repo.get_position("ATHERENERG.NS") is None
    trades = atr_repo.load_trades()
    assert len(trades) == 1
    assert trades[0].net_pnl == pytest.approx(-1683.5)


def test_apply_momentum_atr_idempotent_second_run_is_noop(atr_repo):
    from momentum_atr.models import Position
    atr_repo.save_position(Position(
        symbol="WELCORP.NS", entry_date=date(2026, 8, 27), entry_price=2413.5,
        shares=8, status="OPEN", entry_order_id="260827000014433",
    ))

    correction.apply_momentum_atr("WELCORP.NS", 3, "RECONCILED_SHARES_LOST_UNEXPLAINED_BROKER_GAP", apply=True)
    second_msg = correction.apply_momentum_atr("WELCORP.NS", 3, "RECONCILED_SHARES_LOST_UNEXPLAINED_BROKER_GAP", apply=True)

    assert second_msg.startswith("SKIP")
    trades = atr_repo.load_trades()
    assert len(trades) == 1  # not double-applied


def test_apply_momentum_atr_missing_position_is_skip(atr_repo):
    msg = correction.apply_momentum_atr("NOPOS.NS", 0, "RECONCILED_SHARES_LOST_UNEXPLAINED_BROKER_GAP", apply=True)
    assert msg.startswith("SKIP")


def test_apply_main_close_writes_offsetting_loss(main_repo):
    from db.models import Position
    main_repo.save_position(Position(
        symbol="ASIANENE.NS", sector="Industrials", entry_date=date(2026, 8, 27),
        entry_price=493.85, shares=8, stop_loss=0.0, take_profit=0.0,
        trailing_stop=0.0, peak_price=493.85, status="OPEN", origin="manual",
    ))

    msg = correction.apply_main("ASIANENE.NS", 0, "RECONCILED_SHARES_LOST_UNEXPLAINED_BROKER_GAP", apply=True)

    assert "CLOSE" in msg
    remaining = [p for p in main_repo.load_positions("OPEN") if p.symbol == "ASIANENE.NS"]
    assert remaining == []
    trades = main_repo.load_trades()
    assert len(trades) == 1
    assert trades[0].exit_price == 0.0
    assert trades[0].net_pnl == pytest.approx(-493.85 * 8)
    assert trades[0].sector == "Industrials"


def test_apply_main_idempotent_second_run_is_noop(main_repo):
    from db.models import Position
    main_repo.save_position(Position(
        symbol="ASIANENE.NS", sector="Industrials", entry_date=date(2026, 8, 27),
        entry_price=493.85, shares=8, stop_loss=0.0, take_profit=0.0,
        trailing_stop=0.0, peak_price=493.85, status="OPEN", origin="manual",
    ))

    correction.apply_main("ASIANENE.NS", 0, "RECONCILED_SHARES_LOST_UNEXPLAINED_BROKER_GAP", apply=True)
    second_msg = correction.apply_main("ASIANENE.NS", 0, "RECONCILED_SHARES_LOST_UNEXPLAINED_BROKER_GAP", apply=True)

    assert second_msg.startswith("SKIP")
    trades = main_repo.load_trades()
    assert len(trades) == 1


def test_apply_main_missing_position_is_skip(main_repo):
    msg = correction.apply_main("NOPOS.NS", 0, "RECONCILED_SHARES_LOST_UNEXPLAINED_BROKER_GAP", apply=True)
    assert msg.startswith("SKIP")


def test_dry_run_does_not_write(atr_repo):
    from momentum_atr.models import Position
    atr_repo.save_position(Position(
        symbol="CYIENT.NS", entry_date=date(2026, 9, 1), entry_price=1150.38,
        shares=6, status="OPEN", entry_order_id="260901000015136",
    ))

    msg = correction.apply_momentum_atr("CYIENT.NS", 3, "RECONCILED_SHARES_LOST_MAIN_MANUAL_LIQUIDATION", apply=False)

    assert "REDUCE" in msg
    unchanged = atr_repo.get_position("CYIENT.NS")
    assert unchanged.shares == 6
    assert atr_repo.load_trades() == []
