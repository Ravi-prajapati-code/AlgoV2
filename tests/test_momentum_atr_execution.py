"""
Tests for the standalone momentum x ATR live strategy (docs/57, docs/58;
plan velvet-cooking-minsky) -- the isolated vertical slice in
momentum_atr/ and db/momentum_atr_repo.py, never wired into the main
live-trading path (portfolio/manager.py, db/schema.sql's positions table).

Covers: scoring formula parity against the backtest source
(scripts/momentum_atr_experiment/engine.py:compute_scores, FULL mode) that
momentum_atr/scoring.py claims to port verbatim, and run_daily()'s full
branching logic (initial fill, swap rule, rank-exit rule, kill-switch
trip/block, dry-run isolation, reject/partial-fill safety) against a fake
broker. Runs entirely against a temp SQLite DB -- never touches
db/momentum_atr.db or db/trading.db.
"""
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, "scripts/momentum_atr_experiment")

from broker.base import BaseBroker, LivePosition, OrderResult, OrderStatus
from momentum_atr.models import Position


# ── scoring parity vs the backtest source ───────────────────────────────

def _synthetic_ohlcv(seed: int, n: int = 80) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.3, 1.5, n))
    high = close + rng.uniform(0.1, 2.0, n)
    low = close - rng.uniform(0.1, 2.0, n)
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({"close": close, "high": high, "low": low}, index=idx)


def test_scoring_matches_backtest_engine_formula(monkeypatch):
    """momentum_atr/scoring.py:compute_live_scores claims to port
    engine.py:compute_scores (FULL mode) verbatim, substituting the data
    source only. Run both against the identical synthetic OHLCV and assert
    the last score is byte-identical -- catches formula drift silently
    introduced by the port."""
    import engine as backtest_engine
    from momentum_atr.scoring import compute_live_scores

    df = _synthetic_ohlcv(seed=1)

    monkeypatch.setattr(backtest_engine.pd, "read_parquet", lambda path: df.copy())
    backtest_out = backtest_engine.compute_scores(["TEST"], mode="FULL")
    backtest_last_score = backtest_out["TEST"]["score"].iloc[-1]

    monkeypatch.setattr("data.fetcher.fetch_all", lambda symbols, live_mode=True: {"TEST": df.copy()})
    live_scores, live_closes = compute_live_scores(["TEST"])

    assert live_closes["TEST"] == pytest.approx(df["close"].iloc[-1])
    assert live_scores["TEST"] == pytest.approx(backtest_last_score)


def test_scoring_forces_zero_on_negative_momentum(monkeypatch):
    """score = momentum * atr_pct, but forced to 0 whenever momentum <= 0
    -- a down-trending symbol must never carry a positive score even if
    ATR_PCT is large, or rank_symbols would rank pure-volatility above
    real momentum."""
    from momentum_atr.scoring import compute_live_scores

    n = 80
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    close = 100 - np.arange(n) * 0.5  # steadily declining -> momentum < 0 throughout
    df = pd.DataFrame({"close": close, "high": close + 3.0, "low": close - 3.0}, index=idx)

    monkeypatch.setattr("data.fetcher.fetch_all", lambda symbols, live_mode=True: {"DOWN": df})
    scores, _ = compute_live_scores(["DOWN"])
    assert scores["DOWN"] == 0.0


def test_scoring_drops_short_history(monkeypatch):
    """Fewer than MIN_HISTORY_DAYS(50) rows -> symbol omitted entirely,
    matching data.fetcher.fetch_all's own >=50 filter (no partial-SMA
    score should ever leak through)."""
    from momentum_atr.scoring import compute_live_scores

    idx = pd.date_range("2026-01-01", periods=30, freq="D")
    df = pd.DataFrame({"close": np.linspace(100, 110, 30),
                        "high": np.linspace(101, 111, 30),
                        "low": np.linspace(99, 109, 30)}, index=idx)

    monkeypatch.setattr("data.fetcher.fetch_all", lambda symbols, live_mode=True: {"SHORT": df})
    scores, closes = compute_live_scores(["SHORT"])
    assert "SHORT" not in scores
    assert "SHORT" not in closes


def test_rank_symbols_excludes_nonpositive_scores():
    from momentum_atr.scoring import rank_symbols
    ranked = rank_symbols({"A": 10.0, "B": -5.0, "C": 0.0, "D": 30.0})
    assert ranked == ["D", "A"]


# ── run_daily() branching logic against a fake broker ───────────────────

class FakeBroker(BaseBroker):
    """Fills every order COMPLETE at the requested symbol's mapped price,
    unless the symbol is registered in force_reject/force_partial."""

    def __init__(self, prices, cash=10_000_000, holdings=None):
        self.prices = dict(prices)
        self.cash = cash
        self._orders = {}
        self._oid = 0
        self.force_reject = set()
        self.force_partial = {}
        # Real-account holdings for the 40%-of-total allocation cap (both
        # strategies' positions combined -- empty by default so existing
        # tests' cap (0.40 * cash) stays far above their small test amounts.
        self.holdings = holdings or []

    def place_order(self, request):
        self._oid += 1
        oid = f"O{self._oid}"
        px = self.prices.get(request.symbol, 100.0)
        if request.symbol in self.force_reject:
            res = OrderResult(order_id=oid, status=OrderStatus.REJECTED, symbol=request.symbol,
                               side=request.side, requested_qty=request.quantity,
                               rejection_reason="test-forced-reject")
        elif request.symbol in self.force_partial:
            res = OrderResult(order_id=oid, status=OrderStatus.COMPLETE, symbol=request.symbol,
                               side=request.side, requested_qty=request.quantity,
                               filled_qty=self.force_partial[request.symbol], avg_price=px)
        else:
            res = OrderResult(order_id=oid, status=OrderStatus.COMPLETE, symbol=request.symbol,
                               side=request.side, requested_qty=request.quantity,
                               filled_qty=request.quantity, avg_price=px)
        self._orders[oid] = res
        return res

    def cancel_order(self, order_id):
        return True

    def get_order_status(self, order_id):
        return self._orders[order_id]

    def get_positions(self):
        return []

    def get_portfolio_value(self):
        return self.cash

    def get_available_cash(self):
        return self.cash

    def get_holdings(self):
        return self.holdings


TODAY = date(2026, 8, 6)
# len(closes) must clear run_daily's >=20-scored-symbols universe-coverage
# guard; these are padded with never-eligible (negative-score) fillers so
# the guard passes without affecting which symbols actually rank top-N.
FILLER = {f"F{i}": 50.0 for i in range(25)}


def _fake_scores(closes, ranked):
    all_closes = {**closes, **FILLER}
    scores = {sym: (len(ranked) - i) * 10.0 for i, sym in enumerate(ranked)}
    scores.update({sym: -1.0 - i for i, sym in enumerate(FILLER)})
    return lambda symbols: (scores, all_closes)


@pytest.fixture
def momentum_atr_env(tmp_path, monkeypatch):
    """Isolated temp DB + silenced Telegram, mirroring
    tests/test_core_universe_snapshot.py's temp-DB isolation pattern."""
    import momentum_atr.execution as execution
    import momentum_atr.risk as risk
    from db import momentum_atr_repo as repo

    db_path = str(tmp_path / "momentum_atr_test.db")
    monkeypatch.setattr("db.momentum_atr_repo.MOMENTUM_ATR_DB_PATH", db_path)

    sent = []
    monkeypatch.setattr(execution, "send_error_alert", lambda msg: sent.append(("error", msg)))
    monkeypatch.setattr(execution, "send_message", lambda msg: sent.append(("msg", msg)))
    monkeypatch.setattr(risk, "send_message", lambda msg: sent.append(("msg", msg)))

    repo.init_db()
    return execution, repo, sent


def _seed_positions(repo, symbols, entry_price=100.0):
    for sym in symbols:
        repo.save_position(Position(symbol=sym, entry_date=TODAY - timedelta(days=5),
                                     entry_price=entry_price, shares=10, status="OPEN"))


def test_initial_fill_establishes_top_n(momentum_atr_env, monkeypatch):
    execution, repo, _ = momentum_atr_env
    closes = {"A": 100.0, "B": 200.0, "C": 50.0, "D": 10.0}
    monkeypatch.setattr(execution, "compute_live_scores", _fake_scores(closes, ["A", "B", "C", "D"]))

    summary = execution.run_daily(FakeBroker(closes), TODAY)

    assert not summary.get("aborted"), summary
    assert {p.symbol for p in repo.load_positions("OPEN")} == {"A", "B", "C"}
    assert summary["open_positions"] == 3


def test_dry_run_makes_no_ledger_mutation(momentum_atr_env, monkeypatch):
    execution, repo, _ = momentum_atr_env
    closes = {"A": 100.0, "B": 200.0, "C": 50.0, "D": 10.0}
    monkeypatch.setattr(execution, "compute_live_scores", _fake_scores(closes, ["A", "B", "C", "D"]))

    summary = execution.run_daily(FakeBroker(closes), TODAY, dry_run=True)

    assert repo.load_positions("OPEN") == []
    assert len(summary["planned_orders"]) == 3


def test_swap_rule_sells_loser_buys_winner(momentum_atr_env, monkeypatch):
    execution, repo, _ = momentum_atr_env
    _seed_positions(repo, ["A", "B", "C"])
    repo.update_state(cash=100.0, peak_equity=3200.0)  # ~= entry equity, well clear of kill-switch

    today_closes = {"A": 96.0, "B": 104.0, "C": 101.0, "D": 90.0}  # A -4%, B +4%
    monkeypatch.setattr(execution, "compute_live_scores", _fake_scores(today_closes, ["B", "C", "A", "D"]))

    summary = execution.run_daily(FakeBroker(today_closes), TODAY)

    syms_after = {p.symbol for p in repo.load_positions("OPEN")}
    assert "A" not in syms_after and "B" in syms_after
    types = [a["type"] for a in summary["actions"]]
    assert "SWAP_SELL" in types and "SWAP_BUY" in types


def test_rank_exit_sells_first_out_of_rank_and_reallocs(momentum_atr_env, monkeypatch):
    execution, repo, _ = momentum_atr_env
    _seed_positions(repo, ["A", "B", "C"])
    repo.update_state(cash=100.0, peak_equity=3200.0)

    today_closes = {"A": 100.5, "B": 100.5, "C": 100.5, "D": 100.5, "E": 100.5}
    # A, B stay top-3; D displaces C to rank 4 -- C is the sole held
    # position with rank>3, matching engine.py's break-on-first-exit.
    monkeypatch.setattr(execution, "compute_live_scores",
                         _fake_scores(today_closes, ["A", "B", "D", "E", "C"]))

    summary = execution.run_daily(FakeBroker(today_closes), TODAY)

    syms_after = {p.symbol for p in repo.load_positions("OPEN")}
    assert "C" not in syms_after
    types = [a["type"] for a in summary["actions"]]
    assert "RANK_EXIT_SELL" in types and "RANK_EXIT_REALLOC" in types


def test_kill_switch_blocks_buys_but_not_sells(momentum_atr_env, monkeypatch):
    execution, repo, sent = momentum_atr_env
    _seed_positions(repo, ["A", "B", "C"])
    # peak_equity far above today's equity -> forces a >=25% drawdown trip
    repo.update_state(cash=0.0, peak_equity=10_000.0)

    today_closes = {"A": 96.0, "B": 104.0, "C": 101.0, "D": 90.0}
    monkeypatch.setattr(execution, "compute_live_scores", _fake_scores(today_closes, ["B", "C", "A", "D"]))

    summary = execution.run_daily(FakeBroker(today_closes), TODAY)

    assert summary["kill_switch_tripped"] is True
    types = [a["type"] for a in summary["actions"]]
    assert "SWAP_SELL" in types
    assert "SKIP_SWAP_BUY" in types
    assert "SWAP_BUY" not in types
    assert any("TRIPPED" in msg for _, msg in sent)


def test_rejected_order_leaves_ledger_untouched(momentum_atr_env, monkeypatch):
    execution, repo, _ = momentum_atr_env
    closes = {"A": 100.0, "B": 200.0, "C": 50.0, "D": 10.0}
    monkeypatch.setattr(execution, "compute_live_scores", _fake_scores(closes, ["A", "B", "C", "D"]))

    broker = FakeBroker(closes)
    broker.force_reject.add("A")
    execution.run_daily(broker, TODAY)

    symbols = {p.symbol for p in repo.load_positions("OPEN")}
    assert "A" not in symbols
    assert {"B", "C"} <= symbols


def test_partial_fill_leaves_ledger_untouched(momentum_atr_env, monkeypatch):
    execution, repo, _ = momentum_atr_env
    closes = {"A": 100.0, "B": 200.0, "C": 50.0, "D": 10.0}
    monkeypatch.setattr(execution, "compute_live_scores", _fake_scores(closes, ["A", "B", "C", "D"]))

    broker = FakeBroker(closes)
    broker.force_partial["A"] = 1  # confirmed-fill funnel requires filled_qty == requested qty
    execution.run_daily(broker, TODAY)

    assert "A" not in {p.symbol for p in repo.load_positions("OPEN")}


def test_allocation_cap_limits_effective_cash(momentum_atr_env):
    """Real broker cash Rs.60k + other-strategy holding Rs.10k = Rs.70k total
    account equity. At 40% allocation that's a Rs.28k ceiling for this
    strategy -- well below both its internal ledger (Rs.1L) and real broker
    cash (Rs.60k) alone, so the allocation cap must be the binding limit."""
    execution, repo, _ = momentum_atr_env
    broker = FakeBroker(
        {}, cash=60_000,
        holdings=[LivePosition(symbol="OTHER.NS", quantity=100, avg_price=100.0,
                                ltp=100.0, pnl=0.0, product="CNC")],
    )
    effective = execution._get_effective_cash(broker, internal_cash=100_000, atr_invested_value=0.0)
    assert effective == pytest.approx(28_000.0)


def test_first_run_bootstraps_capital_from_real_account(momentum_atr_env, monkeypatch):
    """First-ever run (no positions, no trade history) must replace the
    flat deploy-time placeholder cash/peak_equity with 40% of today's real
    combined account equity, not silently keep trading against a fictional
    number nobody funded."""
    execution, repo, _ = momentum_atr_env
    closes = {"A": 100.0, "B": 200.0, "C": 50.0, "D": 10.0}
    monkeypatch.setattr(execution, "compute_live_scores", _fake_scores(closes, ["A", "B", "C", "D"]))

    broker = FakeBroker(
        closes, cash=60_000,
        holdings=[LivePosition(symbol="OTHER.NS", quantity=100, avg_price=100.0,
                                ltp=100.0, pnl=0.0, product="CNC")],
    )
    assert repo.get_state().cash == pytest.approx(100_000.0)  # deploy-time placeholder, pre-run

    execution.run_daily(broker, TODAY)

    state = repo.get_state()
    assert state.peak_equity == pytest.approx(28_000.0)  # 0.40 * (60k cash + 10k other-holding)
