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

from broker.base import BaseBroker, LivePosition, OrderRequest, OrderResult, OrderSide, OrderStatus
from momentum_atr.models import Position


# ── scoring parity vs the backtest source ───────────────────────────────

def _synthetic_ohlcv(seed: int, n: int = 80, avg_volume: float = 1_000_000.0) -> pd.DataFrame:
    """avg_volume defaults comfortably above scoring.py's MIN_AVG_VOLUME_20D
    (300k) floor -- these tests exercise the score formula itself, not the
    volume gate (see test_scoring_excludes_below_volume_floor for that)."""
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.3, 1.5, n))
    high = close + rng.uniform(0.1, 2.0, n)
    low = close - rng.uniform(0.1, 2.0, n)
    volume = rng.uniform(avg_volume * 0.9, avg_volume * 1.1, n)
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({"close": close, "high": high, "low": low, "volume": volume}, index=idx)


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
    df = pd.DataFrame({"close": close, "high": close + 3.0, "low": close - 3.0,
                        "volume": np.full(n, 1_000_000.0)}, index=idx)

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


def test_scoring_excludes_below_volume_floor(monkeypatch):
    """MIN_AVG_VOLUME_20D=300k floor (docs/61 addendum, 2026-09-02,
    c3324f9) had zero test coverage -- a symbol with real positive momentum
    but thin 20d avg volume must still be excluded entirely, matching a
    too-short-history exclusion (not scored as 0, dropped from the dict)."""
    from momentum_atr.scoring import compute_live_scores

    thin = _synthetic_ohlcv(seed=2, avg_volume=100_000.0)  # below the 300k floor
    monkeypatch.setattr("data.fetcher.fetch_all", lambda symbols, live_mode=True: {"THIN": thin})
    scores, closes = compute_live_scores(["THIN"])
    assert "THIN" not in scores
    assert "THIN" not in closes


def test_scoring_includes_above_volume_floor(monkeypatch):
    """Guard against the fix above becoming too broad: a symbol comfortably
    above the 300k floor with positive momentum must still be scored."""
    from momentum_atr.scoring import compute_live_scores

    liquid = _synthetic_ohlcv(seed=2, avg_volume=1_000_000.0)
    monkeypatch.setattr("data.fetcher.fetch_all", lambda symbols, live_mode=True: {"LIQUID": liquid})
    scores, closes = compute_live_scores(["LIQUID"])
    assert "LIQUID" in scores
    assert "LIQUID" in closes


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

    def get_ltp(self, symbol):
        """Same price map as fills -- in these tests there is no gap
        between "price used to size" and "price the order actually fills
        at" to model; test_sizing_uses_live_quote_not_stale_close below is
        the one test that deliberately makes them differ."""
        return self.prices.get(symbol, 0.0)


class FlakyStatusBroker(FakeBroker):
    """FakeBroker variant whose get_order_status() returns UNKNOWN for the
    first `unknown_polls` calls (or forever, if -1) before revealing the
    real terminal status -- models the ASIANENE.NS incident (2026-09-16):
    order/details 404'd immediately after a real live fill. place_order()
    also mutates self.holdings like a real broker would, so the self-heal
    fallback's before/after comparison has a real fill to detect."""

    def __init__(self, prices, cash=10_000_000, holdings=None, unknown_polls=0):
        super().__init__(prices, cash=cash, holdings=holdings)
        self.unknown_polls = unknown_polls
        self._poll_counts = {}

    def place_order(self, request):
        res = super().place_order(request)
        held = {h.symbol: h.quantity for h in self.holdings}
        delta = request.quantity if request.side == OrderSide.BUY else -request.quantity
        held[request.symbol] = held.get(request.symbol, 0) + delta
        self.holdings = [
            LivePosition(symbol=s, quantity=q, avg_price=self.prices.get(s, 0.0),
                         ltp=self.prices.get(s, 0.0), pnl=0.0, product="CNC")
            for s, q in held.items() if q > 0
        ]
        return res

    def get_order_status(self, order_id):
        n = self._poll_counts.get(order_id, 0)
        self._poll_counts[order_id] = n + 1
        if self.unknown_polls < 0 or n < self.unknown_polls:
            real = self._orders[order_id]
            return OrderResult(order_id=order_id, status=OrderStatus.UNKNOWN,
                                symbol=real.symbol, side=real.side,
                                requested_qty=real.requested_qty,
                                rejection_reason="test-forced-unknown")
        return self._orders[order_id]


TODAY = date(2026, 8, 6)
# len(closes) must clear run_daily's >=20-scored-symbols universe-coverage
# guard; these are padded with never-eligible (negative-score) fillers so
# the guard passes without affecting which symbols actually rank top-N.
FILLER = {f"F{i}": 50.0 for i in range(25)}


def _seed_ranking(repo, closes, ranked):
    """Seeds db/momentum_atr.db's daily_ranking table directly -- what
    scripts/precompute_momentum_atr_ranking.py would have written earlier
    that morning. run_daily() no longer scores/ranks itself (moved to the
    precompute cron), it only reads this."""
    all_closes = {**closes, **FILLER}
    repo.save_daily_ranking(TODAY, ranked, all_closes)


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
    """Seeds OPEN positions in the DB and returns matching LivePositions,
    for tests to hand to FakeBroker(holdings=...) -- run_daily now checks
    broker holdings before selling, so a seeded DB position needs a
    matching broker holding or the sell is (correctly) skipped."""
    holdings = []
    for sym in symbols:
        repo.save_position(Position(symbol=sym, entry_date=TODAY - timedelta(days=5),
                                     entry_price=entry_price, shares=10, status="OPEN"))
        holdings.append(LivePosition(symbol=sym, quantity=10, avg_price=entry_price,
                                      ltp=entry_price, pnl=0.0, product="CNC"))
    return holdings


def test_initial_fill_establishes_top_n(momentum_atr_env):
    execution, repo, _ = momentum_atr_env
    closes = {"A": 100.0, "B": 200.0, "C": 50.0, "D": 10.0}
    _seed_ranking(repo, closes, ["A", "B", "C", "D"])

    summary = execution.run_daily(FakeBroker(closes), TODAY)

    assert not summary.get("aborted"), summary
    assert {p.symbol for p in repo.load_positions("OPEN")} == {"A", "B", "C"}
    assert summary["open_positions"] == 3


def test_dry_run_makes_no_ledger_mutation(momentum_atr_env):
    execution, repo, _ = momentum_atr_env
    closes = {"A": 100.0, "B": 200.0, "C": 50.0, "D": 10.0}
    _seed_ranking(repo, closes, ["A", "B", "C", "D"])

    summary = execution.run_daily(FakeBroker(closes), TODAY, dry_run=True)

    assert repo.load_positions("OPEN") == []
    assert len(summary["planned_orders"]) == 3


def test_swap_rule_sells_loser_buys_winner(momentum_atr_env):
    execution, repo, _ = momentum_atr_env
    holdings = _seed_positions(repo, ["A", "B", "C"])
    repo.update_state(cash=100.0, peak_equity=3200.0)  # ~= entry equity, well clear of kill-switch

    today_closes = {"A": 96.0, "B": 104.0, "C": 101.0, "D": 90.0}  # A -4%, B +4%
    _seed_ranking(repo, today_closes, ["B", "C", "A", "D"])

    summary = execution.run_daily(FakeBroker(today_closes, holdings=holdings), TODAY)

    syms_after = {p.symbol for p in repo.load_positions("OPEN")}
    assert "A" not in syms_after and "B" in syms_after
    types = [a["type"] for a in summary["actions"]]
    assert "SWAP_SELL" in types and "SWAP_BUY" in types


def test_rank_exit_sells_first_out_of_rank_and_reallocs(momentum_atr_env):
    execution, repo, _ = momentum_atr_env
    holdings = _seed_positions(repo, ["A", "B", "C"])
    repo.update_state(cash=100.0, peak_equity=3200.0)

    today_closes = {"A": 100.5, "B": 100.5, "C": 100.5, "D": 100.5, "E": 100.5}
    # A, B stay top-3; D displaces C to rank 4 -- C is the sole held
    # position with rank>3, matching engine.py's break-on-first-exit.
    _seed_ranking(repo, today_closes, ["A", "B", "D", "E", "C"])

    summary = execution.run_daily(FakeBroker(today_closes, holdings=holdings), TODAY)

    syms_after = {p.symbol for p in repo.load_positions("OPEN")}
    assert "C" not in syms_after
    types = [a["type"] for a in summary["actions"]]
    assert "RANK_EXIT_SELL" in types and "RANK_EXIT_REALLOC" in types


def test_kill_switch_blocks_buys_but_not_sells(momentum_atr_env):
    execution, repo, sent = momentum_atr_env
    holdings = _seed_positions(repo, ["A", "B", "C"])
    # peak_equity far above today's equity -> forces a >=25% drawdown trip
    repo.update_state(cash=0.0, peak_equity=10_000.0)

    today_closes = {"A": 96.0, "B": 104.0, "C": 101.0, "D": 90.0}
    _seed_ranking(repo, today_closes, ["B", "C", "A", "D"])

    summary = execution.run_daily(FakeBroker(today_closes, holdings=holdings), TODAY)

    assert summary["kill_switch_tripped"] is True
    types = [a["type"] for a in summary["actions"]]
    assert "SWAP_SELL" in types
    assert "SKIP_SWAP_BUY" in types
    assert "SWAP_BUY" not in types
    assert any("TRIPPED" in msg for _, msg in sent)


def test_rejected_order_leaves_ledger_untouched(momentum_atr_env):
    execution, repo, _ = momentum_atr_env
    closes = {"A": 100.0, "B": 200.0, "C": 50.0, "D": 10.0}
    _seed_ranking(repo, closes, ["A", "B", "C", "D"])

    broker = FakeBroker(closes)
    broker.force_reject.add("A")
    execution.run_daily(broker, TODAY)

    symbols = {p.symbol for p in repo.load_positions("OPEN")}
    assert "A" not in symbols
    assert {"B", "C"} <= symbols


def test_partial_fill_leaves_ledger_untouched(momentum_atr_env):
    execution, repo, _ = momentum_atr_env
    closes = {"A": 100.0, "B": 200.0, "C": 50.0, "D": 10.0}
    _seed_ranking(repo, closes, ["A", "B", "C", "D"])

    broker = FakeBroker(closes)
    broker.force_partial["A"] = 1  # confirmed-fill funnel requires filled_qty == requested qty
    execution.run_daily(broker, TODAY)

    assert "A" not in {p.symbol for p in repo.load_positions("OPEN")}


def test_allocation_cap_limits_effective_cash(momentum_atr_env, monkeypatch):
    """Real broker cash Rs.60k + other-strategy holding Rs.10k = Rs.70k total
    account equity. At 40% allocation that's a Rs.28k ceiling for this
    strategy -- well below both its internal ledger (Rs.1L) and real broker
    cash (Rs.60k) alone, so the allocation cap must be the binding limit.

    Pinned to 0.40 regardless of the live MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT
    (server .env runs 1.0) -- this test asserts the cap math, not today's
    live allocation value."""
    execution, repo, _ = momentum_atr_env
    monkeypatch.setattr(execution, "MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT", 0.40)
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
    number nobody funded.

    Pinned to 0.40 regardless of the live MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT
    (server .env runs 1.0) -- this test asserts the bootstrap math, not
    today's live allocation value."""
    execution, repo, _ = momentum_atr_env
    monkeypatch.setattr(execution, "MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT", 0.40)
    closes = {"A": 100.0, "B": 200.0, "C": 50.0, "D": 10.0}
    _seed_ranking(repo, closes, ["A", "B", "C", "D"])

    broker = FakeBroker(
        closes, cash=60_000,
        holdings=[LivePosition(symbol="OTHER.NS", quantity=100, avg_price=100.0,
                                ltp=100.0, pnl=0.0, product="CNC")],
    )
    assert repo.get_state().cash == pytest.approx(100_000.0)  # deploy-time placeholder, pre-run

    execution.run_daily(broker, TODAY)

    state = repo.get_state()
    assert state.peak_equity == pytest.approx(28_000.0)  # 0.40 * (60k cash + 10k other-holding)


class BrokerReadFailure(FakeBroker):
    """Simulates a real Upstox API failure -- fail_cash/fail_holdings make
    the *_or_none() reads return None, matching UpstoxBroker's real failure
    behavior (never 0.0/[])."""

    def __init__(self, *args, fail_cash=False, fail_holdings=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_cash = fail_cash
        self.fail_holdings = fail_holdings

    def get_available_cash_or_none(self):
        return None if self.fail_cash else self.get_available_cash()

    def get_holdings_or_none(self):
        return None if self.fail_holdings else self.get_holdings()


def test_broker_cash_unavailable_blocks_buys_not_zero_cash(momentum_atr_env):
    """A broker API failure must block BUYs with a distinct alert, not be
    silently indistinguishable from a real Rs.0 cash balance."""
    execution, repo, sent = momentum_atr_env
    broker = BrokerReadFailure({}, cash=1_000_000, fail_cash=True)

    budget = execution._get_effective_cash(broker, 50_000.0, 0.0)

    assert budget == 0.0
    assert any("BROKER_CASH_UNAVAILABLE" in msg for kind, msg in sent if kind == "error")


def test_broker_holdings_unavailable_blocks_buys(momentum_atr_env):
    """A get_holdings() failure (equity calc dependency) must also block
    BUYs with a distinct alert rather than computing equity off missing data."""
    execution, repo, sent = momentum_atr_env
    broker = BrokerReadFailure({}, cash=1_000_000, fail_holdings=True)

    budget = execution._get_effective_cash(broker, 50_000.0, 0.0)

    assert budget == 0.0
    assert any("BROKER_DATA_UNAVAILABLE" in msg for kind, msg in sent if kind == "error")


def test_bootstrap_aborts_on_broker_failure_instead_of_seeding_zero(momentum_atr_env):
    """First-ever run with a failed broker read must abort, not silently
    bootstrap cash/peak_equity to Rs.0 (which would falsely trip the kill
    switch and/or permanently poison peak_equity with a fabricated number)."""
    execution, repo, sent = momentum_atr_env
    closes = {"A": 100.0, "B": 200.0, "C": 50.0}
    _seed_ranking(repo, closes, ["A", "B", "C"])
    broker = BrokerReadFailure(closes, cash=1_000_000, fail_cash=True)
    placeholder_cash = repo.get_state().cash

    result = execution.run_daily(broker, TODAY)

    assert result.get("aborted") is True
    assert repo.get_state().cash == placeholder_cash  # untouched, not zeroed
    assert any("BROKER_DATA_UNAVAILABLE" in msg for kind, msg in sent if kind == "error")


def test_negative_internal_cash_always_alerts(momentum_atr_env, monkeypatch):
    """MIN_REAL_CASH_RATIO's check only ever fired for internal_cash > 0 --
    a negative internal ledger (this repo's actual live state post docs/66)
    must alert explicitly instead of being silently invisible."""
    execution, repo, sent = momentum_atr_env
    closes = {"A": 100.0, "B": 200.0, "C": 50.0}
    _seed_ranking(repo, closes, ["A", "B", "C"])
    repo.update_state(cash=-5000.0, peak_equity=100_000.0)
    broker = FakeBroker(closes, cash=1_000_000)

    budget = execution._get_effective_cash(broker, -5000.0, 0.0)

    assert budget <= 0.0  # negative internal cash still caps the budget, blocking BUYs
    assert any("INTERNAL_CASH_NEGATIVE" in msg for kind, msg in sent if kind == "error")
    assert any("-5,000.00" in msg or "-5,000" in msg for kind, msg in sent if kind == "error")


def test_positive_internal_cash_healthy_broker_no_spurious_alert(momentum_atr_env):
    """Happy path: positive internal cash, broker cash well above the 50%
    floor -- must not trigger either the ratio alert or the new negative-cash
    alert."""
    execution, repo, sent = momentum_atr_env
    broker = FakeBroker({}, cash=1_000_000)

    budget = execution._get_effective_cash(broker, 50_000.0, 0.0)

    assert budget > 0.0
    assert not any(kind == "error" for kind, _ in sent)


def test_run_daily_aborts_when_no_precomputed_ranking(momentum_atr_env):
    """No daily_ranking row for today means precompute cron didn't run/failed.
    run_daily must abort loud, not fall back to scoring itself live (defeats
    the point of the precompute/execute split and risks the same cron-timeout
    the split exists to avoid)."""
    execution, repo, sent = momentum_atr_env
    broker = FakeBroker({})

    summary = execution.run_daily(broker, TODAY)

    assert summary["aborted"] is True
    assert summary["reason"] == "no_precomputed_ranking"


def test_sizing_uses_live_quote_not_stale_close(momentum_atr_env):
    """Root cause of the 2026-08-07 negative-cash incident: _buy_split sized
    shares against precompute's stale prior-close price while real MARKET
    fills happened at today's (higher) live price, silently overspending the
    strategy's own budget. Sizing must use broker.get_ltp (live) so the two
    prices used for "how many shares" and "what did we pay" match -- doesn't
    need to be perfect, just not off by a full-day's gap."""
    execution, repo, _ = momentum_atr_env
    stale_closes = {"A": 100.0, "B": 100.0, "C": 100.0}
    ranked = ["A", "B", "C"]
    _seed_ranking(repo, stale_closes, ranked)

    live_price = 110.0  # 10% above the stale precompute close
    broker = FakeBroker({s: live_price for s in ranked}, cash=10_000)

    summary = execution.run_daily(broker, TODAY)

    assert not summary.get("aborted"), summary
    assert summary["cash"] >= 0, (
        "sizing against the stale precompute close instead of broker.get_ltp() "
        f"drove the ledger negative: {summary}"
    )


def test_await_order_completion_retries_through_transient_unknown(momentum_atr_env, monkeypatch):
    """Regression for the ASIANENE.NS incident (2026-09-16): the real sell
    filled live, but get_order_status() 404'd (-> UNKNOWN) on the very first
    poll right after placement. The old code treated UNKNOWN as terminal and
    gave up immediately instead of retrying through the timeout window like
    PENDING/OPEN -- so a transient lookup failure right after a real fill
    was permanently misread as "never resolved". Must keep polling and pick
    up the real COMPLETE once the lookup recovers."""
    execution, _repo, _sent = momentum_atr_env
    monkeypatch.setattr(execution.time, "sleep", lambda s: None)

    broker = FlakyStatusBroker({"A": 100.0}, unknown_polls=2)
    placed = broker.place_order(OrderRequest(symbol="A", side=OrderSide.BUY, quantity=10))

    final = execution._await_order_completion(broker, placed.order_id)

    assert final is not None
    assert final.status == OrderStatus.COMPLETE


def test_self_heal_via_broker_holdings_when_status_never_resolves(momentum_atr_env, monkeypatch):
    """Regression for the ASIANENE.NS incident (2026-09-16 through
    2026-09-22): get_order_status() never recovered for the rest of that
    trading day, so no amount of in-run polling would have confirmed the
    sell -- the ledger stayed OPEN for 6 days with real sale proceeds never
    credited, and reconcile_positions.py had a separate bug that hid the
    resulting ghost from the daily alert. As a last resort before giving up,
    a fill that broker holdings confirm exactly (qty delta matches) must be
    treated as COMPLETE (at live LTP, since the real fill price is
    unrecoverable) rather than left unresolved forever."""
    execution, repo, sent = momentum_atr_env
    monkeypatch.setattr(execution.time, "sleep", lambda s: None)

    holdings = _seed_positions(repo, ["A"], entry_price=100.0)
    pos = repo.get_position("A")
    broker = FlakyStatusBroker({"A": 110.0}, holdings=holdings, unknown_polls=-1)

    proceeds = execution._execute_sell(broker, pos, "TEST_EXIT", {"A": 110.0}, False, [])

    assert proceeds is not None, "self-heal should have confirmed the fill via broker holdings"
    assert repo.get_position("A") is None
    assert any(kind == "error" and "SELF-HEALED" in msg for kind, msg in sent)


def test_topup_fills_empty_slots_when_under_target(momentum_atr_env):
    """Regression, 2026-09-23: after an exit leaves 1-2 positions open with
    no SWAP/RANK_EXIT trigger firing on the remaining holding (still ranks
    fine, no -3%/+3% spread), nothing previously could ever open a new name
    to fill the empty slot(s) -- idle cash sat there indefinitely (WELCORP.NS
    left as the sole holding since a 2026-09-16 RANK_RULE_EXIT, 7 sessions,
    ~88% of equity in cash with no code path able to redeploy it)."""
    execution, repo, _sent = momentum_atr_env
    holdings = _seed_positions(repo, ["A"])
    repo.update_state(cash=1000.0, peak_equity=2000.0)

    today_closes = {"A": 100.5, "B": 100.5, "C": 100.5, "D": 90.0}
    _seed_ranking(repo, today_closes, ["A", "B", "C", "D"])  # A still top-3 -- no swap/rank-exit trigger

    summary = execution.run_daily(FakeBroker(today_closes, holdings=holdings), TODAY)

    syms_after = {p.symbol for p in repo.load_positions("OPEN")}
    assert syms_after == {"A", "B", "C"}, f"expected top-up to fill both empty slots, got {syms_after}"
    types = [a["type"] for a in summary["actions"]]
    assert "TOPUP_FILL_SLOTS" in types


def test_topup_skipped_when_kill_switch_tripped(momentum_atr_env):
    """Same under-target scenario, but kill-switch tripped -- must not
    deploy fresh capital into new names, only log SKIP_TOPUP."""
    execution, repo, _sent = momentum_atr_env
    holdings = _seed_positions(repo, ["A"])
    repo.update_state(cash=1000.0, peak_equity=1_000_000.0)  # huge drawdown from peak -> trips kill-switch

    today_closes = {"A": 100.5, "B": 100.5, "C": 100.5, "D": 90.0}
    _seed_ranking(repo, today_closes, ["A", "B", "C", "D"])

    summary = execution.run_daily(FakeBroker(today_closes, holdings=holdings), TODAY)

    assert summary["kill_switch_tripped"] is True
    syms_after = {p.symbol for p in repo.load_positions("OPEN")}
    assert syms_after == {"A"}, "must not open new positions while kill-switch is tripped"
    types = [a["type"] for a in summary["actions"]]
    assert "SKIP_TOPUP" in types
