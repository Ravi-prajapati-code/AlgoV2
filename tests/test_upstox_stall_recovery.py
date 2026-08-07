"""
2026-08-07 incident: main strategy's daily fetch hit 7 consecutive
hard-deadline stalls across different symbols with zero clean fetches in
between, then got SIGTERM-killed by the cron's 900s timeout. Consistent
with every request in that window reusing the same poisoned pooled TCP
connection rather than 7 independently bad instruments -- reusing the
same requests.Session after a stall just retries the same dead
connection. data/providers/upstox_provider.py now rebuilds the session
after any hard-deadline stall so the *next* request gets a clean
connection instead of cascading.
"""
import time
from datetime import date

from data.providers.upstox_provider import UpstoxDataProvider


def test_fetch_historical_rebuilds_session_after_stall(monkeypatch):
    provider = UpstoxDataProvider("fake-token")
    provider._HARD_DEADLINE = 0.05  # instance override -- keep the test fast
    old_session = provider.session

    def _hanging_get(*args, **kwargs):
        time.sleep(1)  # never returns within the tiny hard deadline above
        raise RuntimeError("test bug: should be abandoned before this returns")

    monkeypatch.setattr(old_session, "get", _hanging_get)

    df = provider.fetch_historical(
        "NSE_EQ|FAKE", to_date=date(2026, 8, 7), from_date=date(2026, 8, 1)
    )

    assert df.empty
    assert provider.stall_count == 1
    assert provider.session is not old_session, (
        "session must be rebuilt after a stall -- the next call should get a "
        "fresh connection, not retry the one that just hung"
    )


def test_stall_count_increments_across_repeated_stalls(monkeypatch):
    """Confirms the counter tracks every stall, not just the first --
    get_and_reset_stall_count() is what surfaces the 2026-08-07-style
    burst as a number in the PRECOMPUTE SLA checkpoint detail."""
    provider = UpstoxDataProvider("fake-token")
    provider._HARD_DEADLINE = 0.05

    def _hanging_get(*args, **kwargs):
        time.sleep(1)

    for _ in range(3):
        monkeypatch.setattr(provider.session, "get", _hanging_get)
        provider.fetch_historical(
            "NSE_EQ|FAKE", to_date=date(2026, 8, 7), from_date=date(2026, 8, 1)
        )

    assert provider.get_and_reset_stall_count() == 3
    assert provider.stall_count == 0  # reset after read
