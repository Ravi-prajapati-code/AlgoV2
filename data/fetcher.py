"""
Fetcher module — handles data ingestion strictly from Upstox API.
yfinance integration has been removed as per user request.
Standardized to use tz-naive DatetimeIndex for robustness.
"""

import time
import logging
import os
from datetime import date, timedelta
from typing import List, Optional

import pandas as pd

from config.settings import LOOKBACK_DAYS, EMA_WARMUP_DAYS, UPSTOX_ACCESS_TOKEN, MARKET_FILTER_SMA
from db import repository as repo
from data.providers.upstox_provider import UpstoxDataProvider
from data.instruments.mapper import InstrumentMapper

logger = logging.getLogger(__name__)

_RATE_LIMIT_DELAY = 0.3
_MAX_RETRIES = 3

_upstox_provider = UpstoxDataProvider(UPSTOX_ACCESS_TOKEN) if UPSTOX_ACCESS_TOKEN else None
_instrument_mapper = InstrumentMapper() if UPSTOX_ACCESS_TOKEN else None


def _fetch_raw(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Download OHLCV strictly from Upstox API for stocks and indices."""
    if os.getenv("REQUIRE_CACHED_DATA") == "1":
        raise RuntimeError(
            f"[Fetcher] {symbol} {start}..{end} isn't fully cached in db/trading.db and "
            "REQUIRE_CACHED_DATA=1 forbids a live Upstox fetch (docs/29 Rule 1 item 3 — "
            "gate runs must not be silently patched with fresh live data). Pre-populate "
            "the cache or widen its coverage before re-running the gate."
        )
    if not _upstox_provider:
        logger.error(
            "[Fetcher] No data provider available. Set UPSTOX_ACCESS_TOKEN in .env "
            "or pre-populate data/parquet/ with historical data."
        )
        return pd.DataFrame()

    # Determine instrument key
    if symbol == "Nifty 50" or symbol == "^NSEI":
        instrument_key = "NSE_INDEX|Nifty 50"
    elif _instrument_mapper:
        instrument_key = _instrument_mapper.get_key(symbol)
    else:
        instrument_key = None

    if instrument_key:
        try:
            df = _upstox_provider.fetch_historical(instrument_key, to_date=end, from_date=start, interval="day")
            if not df.empty:
                df.index = pd.to_datetime(df.index).tz_localize(None)
                logger.info(f"[Fetcher] Fetched {symbol} from Upstox API ({len(df)} days)")
                return df
        except Exception as e:
            logger.warning(f"[Fetcher] Upstox API failed for {symbol}: {e}")
    
    return pd.DataFrame()


def fetch_symbol(symbol: str, lookback_days: int = LOOKBACK_DAYS, start: Optional[date] = None, end: Optional[date] = None, interval: str = "day", live_mode: bool = False) -> pd.DataFrame:
    """Fetch OHLCV with incremental caching and tz-naive index."""
    # Check Parquet first (Source of truth as per user request)
    parquet_path = f"data/parquet/{symbol}.parquet"
    if os.path.exists(parquet_path) and not live_mode:
        try:
            df_parquet = pd.read_parquet(parquet_path)
            if not df_parquet.empty:
                df_parquet.index = pd.to_datetime(df_parquet.index).tz_localize(None)
                
                # Check actual frequency
                is_minute = (df_parquet.index[1] - df_parquet.index[0]).seconds < 86400 if len(df_parquet) > 1 else False
                
                if interval == "day" and is_minute:
                    df_daily = df_parquet.resample('D').agg({
                        'open': 'first',
                        'high': 'max',
                        'low': 'min',
                        'close': 'last',
                        'volume': 'sum'
                    }).dropna()
                    logger.info(f"[Fetcher] Loaded {symbol} from Parquet (Resampled from 1min)")
                    return df_daily.sort_index()
                elif interval == "1minute" and not is_minute:
                    logger.warning(f"[Fetcher] requested 1minute but {symbol} Parquet is daily data")
                    return df_parquet.sort_index()
                else:
                    logger.info(f"[Fetcher] Loaded {symbol} from Parquet ({interval})")
                    return df_parquet.sort_index()
        except Exception as e:
            logger.warning(f"[Fetcher] Failed to read Parquet for {symbol}: {e}")

    # Fallback to DB Cache or API
    today = date.today()
    fetch_start = (start - timedelta(days=30)) if start else (today - timedelta(days=lookback_days + 40))
    fetch_end   = end if end else today

    cached_latest = repo.latest_cached_date(symbol)
    cached_earliest = repo.earliest_cached_date(symbol)

    df_new = pd.DataFrame()
    if cached_latest is None:
        # First-ever fetch for this symbol — use warmup window so EMA(150) converges properly
        warmup_start = today - timedelta(days=EMA_WARMUP_DAYS + 40)
        fetch_start = min(fetch_start, warmup_start)
        logger.info("[Fetcher] %s: first fetch — using %d-day warmup window", symbol, EMA_WARMUP_DAYS)
        df_new = _fetch_raw(symbol, fetch_start, fetch_end)
    else:
        if cached_earliest > fetch_start:
            df_early = _fetch_raw(symbol, fetch_start, cached_earliest - timedelta(days=1))
            if not df_early.empty: df_new = pd.concat([df_new, df_early])
        
        if cached_latest < fetch_end:
            df_late = _fetch_raw(symbol, cached_latest + timedelta(days=1), fetch_end)
            if not df_late.empty: df_new = pd.concat([df_new, df_late])

    if not df_new.empty:
        repo.save_ohlcv(symbol, df_new.reset_index())

    # Load from cache and ensure index is tz-naive DatetimeIndex
    df = repo.load_ohlcv(symbol, start=fetch_start, end=fetch_end)
    if not df.empty:
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df.sort_index()
    return pd.DataFrame()


def filter_symbols_with_insufficient_history(symbols: List[str], start: date) -> List[str]:
    """Drop symbols whose cache already has data but only starting after
    `start` -- e.g. a recently-listed/demerged symbol (docs/29 Rule 1 item
    3 follow-up: TMCV.NS has no data before 2025-11-12, confirmed via a
    live backfill attempt that returned nothing for the earlier range).
    Used under REQUIRE_CACHED_DATA=1 so one young symbol doesn't hard-fail
    an entire gate run -- a symbol with NO cache entry at all (never
    fetched) is left alone, since that's a first-fetch case, not a
    confirmed data boundary.
    """
    kept = []
    for sym in symbols:
        earliest = repo.earliest_cached_date(sym)
        if earliest is not None and earliest > start:
            logger.info(
                f"[Fetcher] {sym}: cached only from {earliest} (after requested "
                f"start {start}) -- excluding from this run instead of treating "
                "it as a fillable cache gap"
            )
            continue
        kept.append(sym)
    return kept


def fetch_all(symbols: List[str], lookback_days: int = LOOKBACK_DAYS, start: Optional[date] = None, end: Optional[date] = None, interval: str = "day", live_mode: bool = False) -> dict:
    """Fetch OHLCV for all symbols."""
    result = {}
    total = len(symbols)
    for i, symbol in enumerate(symbols, 1):
        logger.info(f"[Fetcher] {i}/{total} {symbol}")
        df = fetch_symbol(symbol, lookback_days, start=start, end=end, interval=interval, live_mode=live_mode)
        if not df.empty and (len(df) >= 50 or interval == "1minute"):
            result[symbol] = df
    return result


def fetch_index(symbol: str = "Nifty 50", lookback_days: int = 250, start: Optional[date] = None, end: Optional[date] = None, interval: str = "day", live_mode: bool = False) -> pd.DataFrame:
    """Fetch index data strictly from Upstox."""
    return fetch_symbol(symbol, lookback_days, start, end, interval=interval, live_mode=live_mode)
