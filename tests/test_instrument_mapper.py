"""InstrumentMapper cache refresh + BE-series mapping (2026-10-21 HFCL fix)."""
import gzip
import json
import os
import time

import pytest

from data.instruments import mapper as mod


def _payload(n_eq=1600):
    rows = [{"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": f"S{i}",
             "instrument_key": f"NSE_EQ|S{i}"} for i in range(n_eq)]
    rows += [{"segment": "NSE_EQ", "instrument_type": "BE", "trading_symbol": "HFCL",
              "instrument_key": "NSE_EQ|INE548A01028"},
             {"segment": "NSE_EQ", "instrument_type": "BE", "trading_symbol": "S0",
              "instrument_key": "NSE_EQ|S0_BE"}]
    return gzip.compress(json.dumps(rows).encode())


class _Resp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.InstrumentMapper, "CACHE_FILE", str(tmp_path / "m.json"))
    monkeypatch.setattr(mod.InstrumentMapper, "FULL_CACHE_FILE", str(tmp_path / "f.json"))
    calls = []

    def fake_get(url, timeout):
        calls.append(url)
        return _Resp(cache.content)
    cache.content = _payload()
    monkeypatch.setattr(mod.requests, "get", fake_get)
    cache.calls = calls
    cache.path = tmp_path / "m.json"
    return cache


def test_be_series_mapped_but_eq_wins_and_not_in_universe_list(cache):
    m = mod.InstrumentMapper()
    assert m.get_key("HFCL.NS") == "NSE_EQ|INE548A01028"
    assert m.get_key("S0") == "NSE_EQ|S0"
    assert all(i["trading_symbol"] != "HFCL" for i in m.get_all_instruments())


def test_stale_cache_is_refreshed_on_load(cache):
    cache.path.write_text(json.dumps({"OLD": "NSE_EQ|OLD"}))
    old = time.time() - 2 * 86400
    os.utime(cache.path, (old, old))
    m = mod.InstrumentMapper()
    assert len(cache.calls) == 1 and m.get_key("HFCL") and m.get_key("OLD") is None


def test_fresh_cache_is_not_redownloaded(cache):
    cache.path.write_text(json.dumps({"OLD": "NSE_EQ|OLD"}))
    m = mod.InstrumentMapper()
    assert cache.calls == [] and m.get_key("OLD") == "NSE_EQ|OLD"


def test_broken_download_keeps_existing_cache(cache):
    cache.path.write_text(json.dumps({"OLD": "NSE_EQ|OLD"}))
    old = time.time() - 2 * 86400
    os.utime(cache.path, (old, old))
    cache.content = _payload(n_eq=10)
    m = mod.InstrumentMapper()
    assert m.get_key("OLD") == "NSE_EQ|OLD"
    assert json.loads(cache.path.read_text()) == {"OLD": "NSE_EQ|OLD"}
