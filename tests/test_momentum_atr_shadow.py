"""momentum_atr/shadow.py (docs/82): paper books for the B2 score comparison."""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd
import pytest

from momentum_atr import shadow
from momentum_atr.shadow import Book, advance, ranked, score_frames

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _bars(closes, volume=1_000_000, start="2025-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c * 0.999, "high": c * 1.02, "low": c * 0.98, "close": c, "volume": volume}, index=idx)


def test_live_formula_matches_live_scoring(monkeypatch):
    """The shadow 'LIVE' book must rank exactly like momentum_atr/scoring.py."""
    import config.settings as settings
    import data.fetcher as fetcher
    from momentum_atr import scoring
    rng = np.random.default_rng(3)
    data = {f"S{k}.NS": _bars(100 + np.cumsum(rng.normal(0.2, 1.5, 260)).clip(5)) for k in range(12)}
    monkeypatch.setattr(fetcher, "fetch_all", lambda symbols, live_mode=True: {s: data[s] for s in symbols})
    monkeypatch.setattr(settings, "MOMENTUM_ATR_SMA200_FILTER", True)
    live_scores, _ = scoring.compute_live_scores(list(data))
    fr = score_frames(data)
    last = fr["LIVE"].iloc[-1]
    for s, v in live_scores.items():
        assert last[s] == pytest.approx(v, rel=1e-12, abs=1e-12)
    assert ranked(last) == scoring.rank_symbols(live_scores)


def test_b2_formula():
    data = {"A.NS": _bars(np.linspace(100, 200, 260))}
    f = shadow._features(data["A.NS"])
    fr = score_frames(data)
    want = f["mom"].iloc[-1] * f["px_over_sma50"].iloc[-1] * f["atr_pct"].iloc[-1]
    assert fr["B2"]["A.NS"].iloc[-1] == pytest.approx(want)


def test_no_orders_no_broker_imports():
    import ast
    src = open(os.path.join(ROOT, "momentum_atr", "shadow.py")).read() + \
        open(os.path.join(ROOT, "scripts", "run_momentum_atr_shadow.py")).read()
    mods = {n.module or "" for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom)} | \
           {a.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m.startswith(("broker", "db", "momentum_atr.execution", "portfolio", "notifications")) for m in mods)


def _frames(days, scores_by_day, px=100.0):
    idx = pd.to_datetime(days)
    syms = sorted({s for row in scores_by_day for s in row})
    sc = pd.DataFrame(scores_by_day, index=idx, columns=syms, dtype=float)
    px_f = pd.DataFrame(px, index=idx, columns=syms)
    return {"LIVE": sc, "B2": sc, "open": px_f, "close": px_f}


def test_book_timing_and_rank_exit():
    days = [str(d.date()) for d in pd.bdate_range("2026-10-13", periods=5)]
    top = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}
    c_drop = {"A": 5, "B": 4, "C": 1.5, "D": 2, "E": 1}
    fr = _frames(days, [top, top, c_drop, c_drop, c_drop])
    b = {"LIVE": Book(name="LIVE")}
    advance(b, fr, days[0])
    t = b["LIVE"].trades
    assert {x["symbol"] for x in t if x["reason"] == "INITIAL_TOPN_SPLIT"} == {"A", "B", "C"}
    assert all(x["date"] == days[1] for x in t if x["reason"] == "INITIAL_TOPN_SPLIT")  # day-0 close -> day-1 open
    sells = [x for x in t if x["action"] == "SELL"]
    assert sells[0]["symbol"] == "C" and sells[0]["date"] == days[3]                 # day-2 close -> day-3 open
    assert b["LIVE"].cash >= 0


def test_missing_bar_for_held_symbol_defers_instead_of_exiting():
    days = [str(d.date()) for d in pd.bdate_range("2026-10-13", periods=4)]
    top = {"A": 5, "B": 4, "C": 3, "D": 2}
    fr = _frames(days, [top] * 4)
    fr["open"].loc[pd.Timestamp(days[3]), "A"] = np.nan
    fr["close"].loc[pd.Timestamp(days[3]), "A"] = np.nan
    b = {"LIVE": Book(name="LIVE")}
    log = advance(b, fr, days[0])
    assert b["LIVE"].last_date == days[2]
    assert any("deferred" in x for x in log)
    assert not [x for x in b["LIVE"].trades if x["action"] == "SELL"]


def test_state_roundtrip_and_idempotent_rerun(tmp_path):
    days = [str(d.date()) for d in pd.bdate_range("2026-10-13", periods=6)]
    rng = np.random.default_rng(0)
    rows = [dict(zip("ABCDE", rng.permutation(5) + 1.0)) for _ in days]
    fr = _frames(days, rows)
    fr["open"] = fr["open"] * pd.DataFrame(rng.uniform(0.9, 1.1, fr["open"].shape), index=fr["open"].index, columns=fr["open"].columns)
    one = {"LIVE": Book(name="LIVE")}
    advance(one, fr, days[0])
    p = tmp_path / "LIVE.json"
    one["LIVE"].save(str(p))
    again = {"LIVE": Book.load(str(p), "LIVE")}
    advance(again, fr, days[0])  # re-running over the same days must change nothing
    assert again["LIVE"].trades == one["LIVE"].trades
    assert again["LIVE"].equity == one["LIVE"].equity


# --- mechanics parity with the research engine (local only: needs research/ + data) ---
_RES = os.path.join(ROOT, "research", "momentum_atr_strategy_variants")


@pytest.mark.skipif(not os.path.isdir(_RES), reason="research code not present (server)")
@pytest.mark.parametrize("arm", ["LIVE_SMA200", "S_B2_MOM_PX"])
def test_book_matches_research_engine(arm):
    sys.path.insert(0, _RES)
    import run_variants as R
    import variants as V
    from run_score_formulas import ARMS
    from audit_sim import audit_sim
    syms, days, feats, om, cb = R.real_data()
    V.ARMS[arm] = ARMS[arm]
    sm, _ = V.mats(feats, days, arm)
    ref = audit_sim(syms, days, sm, om, cb, cost_pct=0.15)
    idx = pd.to_datetime(days)
    fr = {"X": sm.set_axis(idx), "open": om.set_axis(idx),
          "close": pd.DataFrame({s: cb[s].reindex(days).values for s in syms}, index=idx)}
    book = {"X": Book(name="X")}
    advance(book, fr, str(days[0]))
    got = [(t["date"], t["action"], t["symbol"], t["shares"], t["reason"]) for t in book["X"].trades]
    want = [(t["date"], t["action"], t["symbol"], t["shares"], t["reason"]) for t in ref["trade_log"]]
    assert got == want
    assert book["X"].equity[-1]["equity"] == pytest.approx(ref["final_equity"], abs=0.01)  # stored rounded to paise
