import requests
import pandas as pd
import logging
import threading
from datetime import datetime, date, timedelta
from typing import Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

class UpstoxDataProvider:
    """
    Data provider for Upstox V3 Historical Candle API.
    """
    BASE_URL = "https://api.upstox.com/v2/historical-candle"
    # requests' timeout= is a per-socket-read timeout, not a total-request
    # deadline -- a stalled/trickling connection (observed: 07-23 through
    # 07-29, exactly 600s, symbol varies -- not a single bad instrument)
    # can sit past it without erroring or retrying. This wraps the call in
    # a real wall-clock cap so one bad connection can't eat the run's
    # runway before market close.
    _HARD_DEADLINE = 45

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }
        # Count of hard-deadline stalls this instance has hit -- read via
        # get_and_reset_stall_count(). No lock needed: fetch_historical is
        # only ever called sequentially from a single thread per instance
        # (the deadline-enforcement thread it spawns is joined before
        # returning), so there's never a concurrent writer.
        self.stall_count = 0
        self.session = self._build_session()

    def _build_session(self) -> requests.Session:
        """Institutional retry logic. Also called to replace a session
        after a hard-deadline stall (see fetch_historical) -- a fresh
        session means the next request gets a clean connection instead of
        retrying whatever hung."""
        session = requests.Session()
        retries = Retry(
            total=5,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        session.mount("https://", HTTPAdapter(max_retries=retries))
        return session

    def fetch_historical(
        self, 
        instrument_key: str, 
        interval: str = "day", 
        to_date: Optional[date] = None, 
        from_date: Optional[date] = None
    ) -> pd.DataFrame:
        """
        Fetch historical candle data from Upstox V2.
        Returns DataFrame with DatetimeIndex.
        """
        today = date.today()
        if to_date is None or to_date > today:
            to_date = today
        
        if from_date is None:
            from_date = to_date - timedelta(days=30)
        
        to_date_str = to_date.strftime("%Y-%m-%d")
        from_date_str = from_date.strftime("%Y-%m-%d")
        
        # URL structure: {base}/{instrument_key}/{interval}/{to_date}/{from_date}
        url = f"{self.BASE_URL}/{instrument_key}/{interval}/{to_date_str}/{from_date_str}"

        try:
            logger.debug(f"[UpstoxData] Fetching {instrument_key} {interval} from {from_date_str} to {to_date_str}")

            outcome = {}

            def _do_request():
                try:
                    outcome["resp"] = self.session.get(url, headers=self.headers, timeout=30)
                except Exception as req_err:
                    outcome["error"] = req_err

            req_thread = threading.Thread(target=_do_request, daemon=True)
            req_thread.start()
            req_thread.join(timeout=self._HARD_DEADLINE)

            if req_thread.is_alive():
                self.stall_count += 1
                logger.error(
                    f"[UpstoxData] Hard deadline ({self._HARD_DEADLINE}s) exceeded fetching "
                    f"{instrument_key} — abandoning stalled connection, continuing"
                )
                # 2026-08-07: 7 consecutive stalls across different symbols
                # in one run, zero clean fetches in between -- consistent
                # with every request in that window reusing the same
                # poisoned pooled connection, not 7 independent bad
                # instruments. Reusing self.session for the next call would
                # just hang on the same dead connection again. Drop it and
                # build a fresh session so the next request gets a clean
                # connection instead of retrying the one that just hung.
                try:
                    self.session.close()
                except Exception:
                    pass
                self.session = self._build_session()
                return pd.DataFrame()

            if "error" in outcome:
                raise outcome["error"]

            resp = outcome["resp"]
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "success":
                logger.error(f"[UpstoxData] API error: {data.get('errors')}")
                return pd.DataFrame()

            candles = data.get("data", {}).get("candles", [])
            if not candles:
                return pd.DataFrame()

            # Format: [timestamp, open, high, low, close, volume, oi]
            df = pd.DataFrame(candles, columns=["date", "open", "high", "low", "close", "volume", "oi"])
            
            # Use DatetimeIndex (pd.Timestamp) for consistency
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            
            # Drop open interest
            df = df.drop(columns=["oi"])
            
            return df

        except Exception as e:
            logger.error(f"[UpstoxData] Failed to fetch {instrument_key}: {e}")
            return pd.DataFrame()

    def get_and_reset_stall_count(self) -> int:
        """Read-and-clear the hard-deadline-stall counter -- callers that
        want a per-run figure (not a lifetime total across a long-lived
        singleton) should call this once, at the end of their run."""
        n = self.stall_count
        self.stall_count = 0
        return n
