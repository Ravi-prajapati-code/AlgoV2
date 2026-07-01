"""
Universe scanner — discovers and filters NSE EQ stocks for the WATCHLIST_200.
Runs during monthly/quarterly rebalances to find new candidates.

Does NOT modify the trading strategy. Only feeds the universe layer.
"""
import logging
from datetime import date, timedelta
from typing import List, Dict, Optional

import pandas as pd

from config.settings import UPSTOX_ACCESS_TOKEN
from data.instruments.mapper import InstrumentMapper

logger = logging.getLogger(__name__)


class UniverseScanner:
    """
    Scans the full NSE EQ universe and returns stocks that pass
    fundamental + liquidity filters defined in universe_config.yaml.
    """

    def __init__(self, config: Dict):
        self.cfg = config.get("universe", {})
        self.min_cap_cr = self.cfg.get("min_market_cap_cr", 2000)
        self.min_vol_cr = self.cfg.get("min_avg_daily_volume_cr", 3)
        self.min_listing_days = self.cfg.get("min_listing_days", 180)
        self.nse_series = set(self.cfg.get("nse_series", ["EQ"]))
        self.exclude_sectors = set(self.cfg.get("exclude_sectors", []))
        self._mapper: Optional[InstrumentMapper] = None
        if UPSTOX_ACCESS_TOKEN:
            try:
                self._mapper = InstrumentMapper()
            except Exception as e:
                logger.warning("InstrumentMapper init failed: %s", e)

    # ── Public API ──────────────────────────────────────────────────────────

    def scan_all(self) -> List[Dict]:
        """
        Return list of candidate dicts that pass all filters.
        Each dict: {symbol, name, sector, market_cap_cr, isin, listing_date}
        """
        instruments = self._get_nse_instruments()
        if not instruments:
            logger.error("[Scanner] No instruments loaded — cannot scan.")
            return []

        passed = []
        for inst in instruments:
            try:
                result = self._evaluate(inst)
                if result:
                    passed.append(result)
            except Exception as e:
                logger.debug("Skip %s: %s", inst.get("trading_symbol", "?"), e)

        logger.info("[Scanner] %d / %d instruments passed filters.",
                    len(passed), len(instruments))
        return passed

    def scan_incremental(self, existing_symbols: set) -> List[Dict]:
        """Scan only stocks NOT already in the universe (new candidates)."""
        all_candidates = self.scan_all()
        new = [c for c in all_candidates if c["symbol"] not in existing_symbols]
        logger.info("[Scanner] %d new candidates found.", len(new))
        return new

    # ── Internal ────────────────────────────────────────────────────────────

    def _get_nse_instruments(self) -> List[Dict]:
        if self._mapper:
            try:
                self._mapper._load_mapping()
                return self._mapper.get_all_instruments()
            except Exception as e:
                logger.warning("Mapper.get_all_instruments() failed: %s", e)
        # Fallback: read cached JSON
        import json, os
        cache = "data/instruments/nse_instruments.json"
        if os.path.exists(cache):
            with open(cache) as f:
                return json.load(f)
        logger.error("[Scanner] No instrument data available.")
        return []

    def _evaluate(self, inst: Dict) -> Optional[Dict]:
        """Return candidate dict or None if filtered out."""
        symbol_raw = inst.get("trading_symbol", "")
        series = inst.get("segment", inst.get("series", ""))
        name = inst.get("name", "")
        isin = inst.get("isin", "")

        # Series filter — EQ only
        if not any(s in series for s in self.nse_series):
            return None

        # Normalise symbol to .NS suffix
        symbol = symbol_raw if symbol_raw.endswith(".NS") else f"{symbol_raw}.NS"

        # Skip indices, ETFs based on name patterns
        if any(skip in name.upper() for skip in
               ("ETF", "INDEX", "NIFTY", "SENSEX", "LIQUID", "GILT", "FUND")):
            return None

        # Listing date filter
        listing_date = self._parse_date(inst.get("listing_date") or inst.get("last_trading_date"))
        if listing_date:
            age_days = (date.today() - listing_date).days
            if age_days < self.min_listing_days:
                return None

        # Market cap filter (approximate from instrument data if available)
        market_cap = inst.get("market_cap_cr") or inst.get("market_cap", 0)
        if market_cap and float(market_cap) < self.min_cap_cr:
            return None

        # Sector exclusion
        sector = inst.get("sector", inst.get("industry", "Unknown"))
        if sector in self.exclude_sectors:
            return None

        return {
            "symbol":        symbol,
            "name":          name,
            "sector":        sector,
            "market_cap_cr": float(market_cap) if market_cap else None,
            "isin":          isin,
            "listing_date":  listing_date.isoformat() if listing_date else None,
        }

    @staticmethod
    def _parse_date(val) -> Optional[date]:
        if not val:
            return None
        if isinstance(val, date):
            return val
        try:
            return date.fromisoformat(str(val)[:10])
        except Exception:
            return None

    def apply_volume_filter(self, candidates: List[Dict],
                            price_data: Dict[str, pd.DataFrame]) -> List[Dict]:
        """
        Filter candidates by avg daily volume value (price × volume).
        Call after fetching OHLCV for candidates.
        price_data: {symbol: DataFrame with 'close' and 'volume' columns}
        """
        passed = []
        for c in candidates:
            sym = c["symbol"]
            df = price_data.get(sym)
            if df is None or df.empty:
                continue
            last_90 = df.tail(90)
            if len(last_90) < 20:
                continue
            avg_turnover_cr = (last_90["close"] * last_90["volume"]).mean() / 1e7
            if avg_turnover_cr >= self.min_vol_cr:
                c["avg_daily_vol_cr"] = round(avg_turnover_cr, 2)
                passed.append(c)
        logger.info("[Scanner] %d / %d passed volume filter (≥₹%dCr/day).",
                    len(passed), len(candidates), self.min_vol_cr)
        return passed
