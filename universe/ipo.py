"""
IPO Scanner — monitors recent NSE listings and qualifies them for the watchlist.
Runs weekly alongside the ranking refresh.
"""
import logging
from datetime import date, timedelta
from typing import List, Dict, Optional

from db import universe_repo as repo

logger = logging.getLogger(__name__)


class IPOScanner:
    """
    Watches stocks in ipo_watch status.
    After min_listing_days: score them; if >= qualify_percentile, add to watchlist.
    """

    def __init__(self, config: Dict):
        ipo_cfg = config.get("ipo", {})
        self.enabled          = ipo_cfg.get("enabled", True)
        self.min_days         = ipo_cfg.get("min_listing_days", 90)
        self.watch_weeks      = ipo_cfg.get("watch_weeks", 12)
        self.qualify_pct      = ipo_cfg.get("qualify_percentile", 75)
        self.max_ipo_core_pct = ipo_cfg.get("max_ipo_pct_of_core", 0.10)
        self.core_size        = config.get("universe", {}).get("core_size", 100)
        self.mgr              = None  # injected by rebalancer

    def run(self, scored_results: List[Dict], today: date = None) -> int:
        """
        Process watching IPOs.
        scored_results: output of UniverseScorer.score_all() — includes ipo_watch symbols.
        Returns number of IPOs qualified/rejected.
        """
        if not self.enabled:
            return 0

        today = today or date.today()
        score_map = {r["symbol"]: r for r in scored_results}
        watching = repo.get_watching_ipos()
        processed = 0

        for ipo in watching:
            sym = ipo["symbol"]
            result = self._evaluate(ipo, score_map.get(sym), today)
            if result == "qualified":
                self._qualify(sym, ipo, score_map.get(sym))
                processed += 1
            elif result == "rejected":
                repo.update_ipo_status(sym, "rejected",
                                        notes="Below qualify threshold after watch period")
                candidate = repo.get_candidate(sym)
                if candidate:
                    repo.update_candidate_status(
                        sym, "removed",
                        reason="ipo_rejected: below qualify threshold"
                    )
                processed += 1

        return processed

    def add_ipo(self, symbol: str, name: str, sector: str,
                listing_date: date, issue_price: float = None):
        """Register a new IPO for monitoring."""
        qualify_after = listing_date + timedelta(days=self.min_days)
        repo.upsert_ipo(
            symbol,
            name=name, sector=sector,
            listing_date=listing_date.isoformat(),
            issue_price=issue_price,
            qualify_after=qualify_after.isoformat(),
            status="watching",
            last_checked=date.today().isoformat(),
        )
        # Add as ipo_watch candidate so it gets scored
        repo.upsert_candidate(
            symbol,
            name=name, sector=sector,
            status="ipo_watch",
            added_date=listing_date.isoformat(),
        )
        repo.log_event(symbol, "ipo_added", to_status="ipo_watch",
                       reason=f"listing={listing_date.isoformat()}")
        logger.info("[IPO] Added %s (list=%s, qualify_after=%s).",
                    symbol, listing_date, qualify_after)

    # ── Internal ────────────────────────────────────────────────────────────

    def _evaluate(self, ipo: Dict, score: Optional[Dict],
                   today: date) -> Optional[str]:
        qualify_after_str = ipo.get("qualify_after")
        if not qualify_after_str:
            return None
        try:
            qualify_after = date.fromisoformat(str(qualify_after_str)[:10])
        except Exception:
            return None

        if today < qualify_after:
            return None  # Not yet old enough

        # Past the qualification window → must qualify now or be rejected
        watch_expiry = qualify_after + timedelta(weeks=self.watch_weeks)
        if today > watch_expiry:
            return "rejected"

        if score is None:
            return None  # No price data yet, keep watching

        if score["score_percentile"] >= self.qualify_pct:
            return "qualified"
        if today >= watch_expiry:
            return "rejected"
        return None  # Still in watch window, below threshold — keep watching

    def _qualify(self, symbol: str, ipo: Dict, score: Optional[Dict]):
        """Move IPO from ipo_watch → watchlist."""
        repo.update_ipo_status(symbol, "qualified",
                                notes=f"score_pct={score['score_percentile']:.1f}")
        repo.update_candidate_status(
            symbol, "watchlist",
            reason=f"ipo_qualified: score_pct={score['score_percentile']:.1f}"
        )
        repo.log_event(symbol, "ipo_qualified", from_status="ipo_watch",
                       to_status="watchlist",
                       score=score.get("composite_score", 0))
        logger.info("[IPO] %s qualified → watchlist (score_pct=%.1f).",
                    symbol, score["score_percentile"])
