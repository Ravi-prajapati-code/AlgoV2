"""
Tests for the precompute/execute gate in runner/daily_runner.py::run()
(docs/59): a precomputed indicators row must be used instead of a live
fetch when present, a missing row must hard-abort (never silently fall
back to a live fetch this close to close -- that would reintroduce the
2026-08-07 SIGTERM-kill failure mode on exactly the bad-connectivity days
it matters most), and --force-live-fetch must still reproduce the
original inline fetch path for manual recovery.
"""
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

TODAY = date(2026, 8, 10)


class _StopEarly(Exception):
    """Raised by a stubbed post-gate call to stop run() right after the
    precompute gate, so these tests don't have to mock the entire rest of
    a live run (broker sync, signal generation, order placement, Telegram)."""


def _fake_index_df():
    return pd.DataFrame({"close": [100.0] * 60})


@pytest.fixture
def rd(monkeypatch):
    import runner.daily_runner as rd_mod

    monkeypatch.setattr(rd_mod, "init_db", MagicMock())
    monkeypatch.setattr(rd_mod, "fetch_index", MagicMock(return_value=_fake_index_df()))
    monkeypatch.setattr(rd_mod, "_is_market_holiday", MagicMock(return_value=False))
    monkeypatch.setattr(rd_mod, "snapshot_exists_for_date", MagicMock(return_value=False))
    monkeypatch.setattr(rd_mod, "_backup_db", MagicMock())
    monkeypatch.setattr(rd_mod, "detect_regime", MagicMock(return_value="BULL"))
    monkeypatch.setattr(rd_mod, "is_buy_allowed", MagicMock(return_value=True))
    monkeypatch.setattr(rd_mod, "is_strong_bull", MagicMock(return_value=False))
    monkeypatch.setattr(rd_mod, "get_all_symbols", MagicMock(return_value=[f"SYM{i}" for i in range(20)]))
    monkeypatch.setattr(rd_mod, "ALL_DEFENSIVE_SYMBOLS", [])
    monkeypatch.setattr(rd_mod, "sync_portfolio_with_broker", MagicMock(side_effect=_StopEarly))
    monkeypatch.setattr(rd_mod, "_alert_run_abort", MagicMock())
    monkeypatch.setattr(rd_mod, "record_sla_checkpoint", MagicMock())
    return rd_mod


def test_uses_precomputed_indicators_and_skips_fetch(rd, monkeypatch):
    fetch_all = MagicMock()
    compute_rs = MagicMock()
    compute_all = MagicMock()
    monkeypatch.setattr(rd, "fetch_all", fetch_all)
    monkeypatch.setattr(rd, "compute_rs_for_all", compute_rs)
    monkeypatch.setattr(rd, "compute_all", compute_all)
    monkeypatch.setattr(
        rd, "load_precompute_indicators",
        MagicMock(return_value={
            "indicators": {"RELIANCE": {"close": 100}},
            "regime": "BULL", "market_bullish": True, "strong_bull": False,
            "index_candles": 250, "scored_count": 1, "stalls": 0,
            "computed_at": "2026-08-10T09:30:00",
        }),
    )

    with pytest.raises(_StopEarly):
        rd.run(today=TODAY, live_mode=True)

    fetch_all.assert_not_called()
    compute_rs.assert_not_called()
    compute_all.assert_not_called()


def test_missing_precompute_hard_aborts_without_force_flag(rd, monkeypatch):
    """No live-fetch fallback this close to close -- a missing precompute
    row must abort loudly, not quietly reproduce the 2026-08-07 failure."""
    fetch_all = MagicMock()
    monkeypatch.setattr(rd, "fetch_all", fetch_all)
    monkeypatch.setattr(rd, "load_precompute_indicators", MagicMock(return_value=None))

    result = rd.run(today=TODAY, live_mode=True, force_live_fetch=False)

    assert result is False
    fetch_all.assert_not_called()
    rd._alert_run_abort.assert_called_once()


def test_force_live_fetch_reproduces_original_inline_path(rd, monkeypatch):
    """The documented emergency escape hatch -- 'main.py run --live
    --force-live-fetch' -- must still take the original fetch/RS/compute
    path, and must never consult the precompute table at all."""
    fetch_all = MagicMock(return_value={
        f"SYM{i}": pd.DataFrame({"close": [100.0] * 500}) for i in range(20)
    })
    compute_rs = MagicMock(return_value={})
    compute_all = MagicMock(return_value={"SYM0": {"close": 100}})
    load_precompute = MagicMock()
    monkeypatch.setattr(rd, "fetch_all", fetch_all)
    monkeypatch.setattr(rd, "compute_rs_for_all", compute_rs)
    monkeypatch.setattr(rd, "compute_all", compute_all)
    monkeypatch.setattr(rd, "load_precompute_indicators", load_precompute)

    with pytest.raises(_StopEarly):
        rd.run(today=TODAY, live_mode=True, force_live_fetch=True)

    fetch_all.assert_called_once()
    compute_rs.assert_called_once()
    compute_all.assert_called_once()
    load_precompute.assert_not_called()
