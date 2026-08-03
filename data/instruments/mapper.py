import requests
import gzip
import json
import os
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

class InstrumentMapper:
    """
    Handles mapping between trading symbols (e.g., RELIANCE.NS) and
    Upstox instrument keys (e.g., NSE_EQ|INE002A01018).
    Also exposes full instrument list for universe scanning.
    """
    INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
    CACHE_FILE = "data/instruments/nse_instruments.json"
    FULL_CACHE_FILE = "data/instruments/nse_instruments_full.json"

    def __init__(self):
        self.mapping: Dict[str, str] = {}
        self._instruments: List[Dict] = []
        self._load_mapping()

    def _load_mapping(self):
        """Load mapping from local cache or download if missing."""
        if not os.path.exists(self.CACHE_FILE):
            self._download_and_cache()

        try:
            with open(self.CACHE_FILE, "r") as f:
                self.mapping = json.load(f)
            logger.info("[Instruments] Loaded %d instruments from cache.", len(self.mapping))
        except Exception as e:
            logger.error("[Instruments] Failed to load mapping: %s", e)
            self._download_and_cache()

        # Load full instrument list if available
        if os.path.exists(self.FULL_CACHE_FILE):
            try:
                with open(self.FULL_CACHE_FILE, "r") as f:
                    self._instruments = json.load(f)
            except Exception:
                pass

    def _download_and_cache(self):
        """Download instrument list from Upstox and cache symbol→key mapping + full list."""
        logger.info("[Instruments] Downloading instrument list from Upstox...")
        try:
            os.makedirs(os.path.dirname(self.CACHE_FILE), exist_ok=True)
            resp = requests.get(self.INSTRUMENTS_URL, timeout=30)
            resp.raise_for_status()

            content = gzip.decompress(resp.content)
            data = json.loads(content)

            new_mapping = {}
            full_list = []
            for item in data:
                seg = item.get("segment", "")
                itype = item.get("instrument_type", "")
                if seg == "NSE_EQ" and itype == "EQ":
                    symbol = item.get("trading_symbol")
                    key = item.get("instrument_key")
                    if symbol and key:
                        new_mapping[symbol] = key
                        full_list.append({
                            "trading_symbol": symbol,
                            "instrument_key": key,
                            "name":           item.get("name", ""),
                            "isin":           item.get("isin", ""),
                            "segment":        seg,
                            "series":         item.get("series", "EQ"),
                            "sector":         item.get("sector", item.get("industry", "Unknown")),
                            "market_cap_cr":  item.get("market_cap"),
                            "listing_date":   item.get("listing_date"),
                        })

            if not new_mapping:
                logger.warning("[Instruments] No EQ instruments found, trying fallback.")
                for item in data:
                    if item.get("segment") == "NSE_EQ":
                        symbol = item.get("trading_symbol")
                        key = item.get("instrument_key")
                        if symbol and key:
                            new_mapping[symbol] = key

            with open(self.CACHE_FILE, "w") as f:
                json.dump(new_mapping, f)
            with open(self.FULL_CACHE_FILE, "w") as f:
                json.dump(full_list, f)

            self.mapping = new_mapping
            self._instruments = full_list
            logger.info("[Instruments] Cached %d NSE_EQ instruments.", len(self.mapping))
        except Exception as e:
            logger.error("[Instruments] Failed to download/cache: %s", e)

    def get_key(self, symbol: str) -> Optional[str]:
        """Get Upstox instrument key. Accepts 'RELIANCE' or 'RELIANCE.NS'."""
        clean = symbol.replace(".NS", "").replace(".BO", "").upper()
        return self.mapping.get(clean)

    def get_all_instruments(self) -> List[Dict]:
        """Return full instrument list (for universe scanning)."""
        if not self._instruments and os.path.exists(self.FULL_CACHE_FILE):
            with open(self.FULL_CACHE_FILE) as f:
                self._instruments = json.load(f)
        return self._instruments

    def refresh(self):
        """Force re-download instrument list (call monthly)."""
        self._download_and_cache()
