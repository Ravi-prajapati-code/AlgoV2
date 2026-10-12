"""momentum_atr/scoring.py SMA200 trend gate (docs/79)."""
import numpy as np
import pandas as pd
import pytest

import config.settings as settings
import data.fetcher as fetcher
from momentum_atr import scoring


def _bars(closes, volume=1_000_000):
    idx = pd.bdate_range("2025-01-01", periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.02, "low": c * 0.98, "close": c, "volume": volume}, index=idx)


def _uptrend(n=260):
    return list(np.linspace(100, 200, n))


def _below_200_bounce(n=260):
    # long decline (SMA200 high), then a short bounce above SMA50 only
    down = list(np.linspace(300, 100, n - 30))
    up = list(np.linspace(100, 130, 30))
    return down + up


@pytest.fixture
def fake_fetch(monkeypatch):
    data = {"UP.NS": _bars(_uptrend()), "BOUNCE.NS": _bars(_below_200_bounce()),
            "NEW.NS": _bars(_uptrend(120))}  # < 200 bars of history
    monkeypatch.setattr(fetcher, "fetch_all", lambda symbols, live_mode=True: {s: data[s] for s in symbols})
    return list(data)


def test_bounce_below_sma200_scores_zero_when_filter_on(monkeypatch, fake_fetch):
    monkeypatch.setattr(settings, "MOMENTUM_ATR_SMA200_FILTER", True)
    scores, closes = scoring.compute_live_scores(fake_fetch)
    c = _bars(_below_200_bounce())["close"]
    assert c.iloc[-1] > c.rolling(50).mean().iloc[-1]          # it IS above SMA50...
    assert c.iloc[-1] < c.rolling(200).mean().iloc[-1]         # ...but below SMA200
    assert scores["BOUNCE.NS"] == 0.0
    assert scores["UP.NS"] > 0
    assert scores["NEW.NS"] == 0.0                              # no SMA200 yet -> ineligible
    assert scoring.rank_symbols(scores) == ["UP.NS"]
    assert set(closes) == set(fake_fetch)                       # zero scores still returned (exit path)


def test_filter_off_restores_previous_behaviour(monkeypatch, fake_fetch):
    monkeypatch.setattr(settings, "MOMENTUM_ATR_SMA200_FILTER", False)
    scores, _ = scoring.compute_live_scores(fake_fetch)
    assert scores["BOUNCE.NS"] > 0
    assert scores["NEW.NS"] > 0
    assert set(scoring.rank_symbols(scores)) == {"UP.NS", "BOUNCE.NS", "NEW.NS"}


def test_flag_parsing(monkeypatch):
    import importlib
    for raw, want in (("true", True), ("1", True), ("FALSE", False), ("0", False), ("off", False)):
        monkeypatch.setenv("MOMENTUM_ATR_SMA200_FILTER", raw)
        assert importlib.reload(settings).MOMENTUM_ATR_SMA200_FILTER is want
    monkeypatch.delenv("MOMENTUM_ATR_SMA200_FILTER")
    assert importlib.reload(settings).MOMENTUM_ATR_SMA200_FILTER is True
