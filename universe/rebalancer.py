"""
Rebalancing engine — orchestrates all universe management tasks on schedule.
Entry point for all automated runs (daily, weekly, monthly, quarterly).
"""
import logging
from datetime import date
from typing import Dict, List, Optional

import yaml
import pandas as pd

from db import universe_repo as repo
from universe.scanner import UniverseScanner
from universe.scorer import UniverseScorer
from universe.manager import UniverseManager
from universe.ipo import IPOScanner
from universe.audit import UniverseAuditEngine

logger = logging.getLogger(__name__)


def _load_config(path: str = "config/universe_config.yaml") -> Dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error("Failed to load universe config: %s", e)
        return {}


def _fetch_price_data(symbols: List[str], lookback_days: int = 200) -> Dict[str, pd.DataFrame]:
    """Fetch OHLCV for all symbols using the existing data layer."""
    from datetime import timedelta
    from data.fetcher import fetch_symbol
    price_data = {}
    end = date.today()
    start = end - timedelta(days=lookback_days + 50)
    for sym in symbols:
        try:
            df = fetch_symbol(sym, lookback_days=lookback_days, start=start, end=end)
            if not df.empty:
                price_data[sym] = df.reset_index()
                if "close" not in price_data[sym].columns and "Close" in price_data[sym].columns:
                    price_data[sym].rename(columns={"Close": "close", "Volume": "volume",
                                                     "Open": "open", "High": "high",
                                                     "Low": "low"}, inplace=True)
        except Exception as e:
            logger.debug("Price fetch failed for %s: %s", sym, e)
    return price_data


class RebalancingEngine:
    """
    Orchestrates:
      - daily_quality_check()    — fundamental breach removals
      - weekly_ranking_refresh() — score all, promote/demote
      - monthly_universe_refresh()  — scan for new candidates
      - quarterly_major_rebalance() — full NSE rescan
    """

    def __init__(self, config_path: str = "config/universe_config.yaml"):
        self.cfg     = _load_config(config_path)
        self.scanner = UniverseScanner(self.cfg)
        self.scorer  = UniverseScorer(self.cfg)
        self.manager = UniverseManager(self.cfg)
        self.ipo     = IPOScanner(self.cfg)
        self.ipo.mgr = self.manager
        self.auditor = UniverseAuditEngine(self.cfg)

    # ── Daily ────────────────────────────────────────────────────────────────

    def daily_quality_check(self, today: date = None) -> Dict:
        """
        Run after market close every trading day.
        Checks for fundamental breaches: volume collapse, trading halts, delistings.
        Removes stocks that no longer meet minimum criteria.
        """
        today = today or date.today()
        cfg = self.cfg.get("rebalancing", {}).get("daily_quality_check", {})
        max_removals = cfg.get("max_removals_per_day", 3)
        removed = []

        min_vol_cr = self.cfg.get("universe", {}).get("min_avg_daily_volume_cr", 3)
        core_symbols = repo.get_candidates_by_status("core")
        wl_symbols   = repo.get_candidates_by_status("watchlist")
        all_active = core_symbols + wl_symbols

        if not all_active:
            return {"removed": 0}

        symbols = [c["symbol"] for c in all_active]
        price_data = _fetch_price_data(symbols, lookback_days=20)

        for cand in all_active:
            if len(removed) >= max_removals:
                break
            sym = cand["symbol"]
            df = price_data.get(sym)
            if df is None or df.empty:
                logger.warning("[DailyQC] No data for %s — flagging for review.", sym)
                continue
            # Volume collapse check (last 5 days avg turnover)
            last5 = df.tail(5)
            if "close" in last5.columns and "volume" in last5.columns:
                avg_turnover_cr = (last5["close"] * last5["volume"]).mean() / 1e7
                if avg_turnover_cr < min_vol_cr * 0.2:  # 80% collapse
                    self.manager.manual_remove(
                        sym, reason=f"volume_collapse: ₹{avg_turnover_cr:.2f}Cr/day"
                    )
                    removed.append(sym)
                    logger.warning("[DailyQC] Removed %s — volume collapsed.", sym)

        return {"removed": len(removed), "symbols": removed}

    # ── Weekly ───────────────────────────────────────────────────────────────

    def weekly_ranking_refresh(self, today: date = None,
                                regime: str = "BULL") -> Dict:
        """
        Run every Friday after market close.
        Scores all candidates, applies promotion/demotion rules,
        processes IPO qualifications, detects delistings.
        """
        today = today or date.today()
        logger.info("[Weekly] Starting ranking refresh for %s (regime=%s).",
                    today, regime)

        all_candidates = repo.get_all_candidates()
        active_syms = [c["symbol"] for c in all_candidates
                       if c["status"] in ("core", "watchlist", "ipo_watch")]

        if not active_syms:
            logger.warning("[Weekly] No active candidates to score.")
            return {}

        # Fetch price data
        price_data = _fetch_price_data(active_syms)
        index_data = self._get_index_data()

        # Detect regime from index data if not passed in
        if regime == "BULL" and index_data is not None and not index_data.empty:
            try:
                from strategy.regime import detect_regime
                regime = detect_regime(index_data)
            except Exception:
                pass

        # Survivorship bias: symbols with no data this week
        symbols_with_no_data = {sym for sym in active_syms
                                 if sym not in price_data or price_data[sym].empty}
        if symbols_with_no_data:
            logger.info("[Weekly] %d symbols with no data: %s",
                        len(symbols_with_no_data),
                        list(symbols_with_no_data)[:5])

        # Identify young IPOs (lock-in period) to suppress short-term momentum
        ipo_lock_days = self.cfg.get("ipo", {}).get("lock_in_days", 180)
        ipo_young: set = set()
        for ipo in repo.get_watching_ipos():
            listing = ipo.get("listing_date")
            if listing:
                try:
                    listing_d = date.fromisoformat(str(listing)[:10])
                    if (today - listing_d).days < ipo_lock_days:
                        ipo_young.add(ipo["symbol"])
                except Exception:
                    pass

        # Score all (suppress 3M momentum for young IPOs)
        scored = self.scorer.score_all(
            price_data, index_data,
            ipo_young_symbols=ipo_young,
        )
        if not scored:
            logger.warning("[Weekly] Scorer returned no results.")
            return {}

        # Attach status
        status_map = {c["symbol"]: c["status"] for c in all_candidates}
        for s in scored:
            s["status"] = status_map.get(s["symbol"], "watchlist")

        # Apply promotion/demotion rules (regime-aware, survivorship-aware)
        summary = self.manager.refresh(
            scored, today,
            regime=regime,
            symbols_with_no_data=symbols_with_no_data,
        )

        # Process IPOs
        ipo_count = self.ipo.run(scored, today)
        summary["ipo_processed"] = ipo_count

        # Save weekly metrics snapshot
        from datetime import timedelta
        friday = (today - timedelta(days=today.weekday() - 4)
                  if today.weekday() != 4 else today)
        repo.save_weekly_metrics(friday, scored)

        logger.info("[Weekly] Done: %s", summary)
        return summary

    # ── Monthly ──────────────────────────────────────────────────────────────

    def monthly_universe_refresh(self, today: date = None) -> Dict:
        """
        Run last Friday of each month.
        Scans for new stock candidates from full NSE EQ universe.
        """
        today = today or date.today()
        logger.info("[Monthly] Starting universe refresh for %s.", today)

        existing = {c["symbol"] for c in repo.get_all_candidates()}
        max_new = self.cfg.get("rebalancing", {}).get(
            "monthly_universe_refresh", {}
        ).get("max_new_additions", 10)

        # Scan for new candidates
        new_candidates = self.scanner.scan_incremental(existing)

        # Fetch price data for new candidates
        new_syms = [c["symbol"] for c in new_candidates]
        price_data = _fetch_price_data(new_syms)
        volume_passed = self.scanner.apply_volume_filter(new_candidates, price_data)

        # Sort by market cap descending, take top max_new
        volume_passed.sort(key=lambda x: x.get("market_cap_cr") or 0, reverse=True)
        to_add = volume_passed[:max_new]

        added = 0
        for c in to_add:
            self.manager.add_to_watchlist(
                c["symbol"], name=c.get("name", ""),
                sector=c.get("sector", "Unknown"),
                market_cap_cr=c.get("market_cap_cr"),
                isin=c.get("isin", ""),
                reason="monthly_scan",
            )
            added += 1

        # Also run weekly refresh
        weekly = self.weekly_ranking_refresh(today)
        weekly["new_candidates_added"] = added
        logger.info("[Monthly] Added %d new candidates.", added)
        return weekly

    # ── Quarterly ────────────────────────────────────────────────────────────

    def quarterly_major_rebalance(self, today: date = None) -> Dict:
        """
        Run last Friday of each quarter (Mar/Jun/Sep/Dec).
        Full NSE rescan + refresh instrument mapper.
        """
        today = today or date.today()
        logger.info("[Quarterly] Starting major rebalance for %s.", today)

        # Refresh instrument mapper
        try:
            from data.instruments.mapper import InstrumentMapper
            mapper = InstrumentMapper()
            mapper.refresh()
            logger.info("[Quarterly] Instrument mapper refreshed.")
        except Exception as e:
            logger.warning("[Quarterly] Mapper refresh failed: %s", e)

        # Full scan — larger cap for quarterly
        existing = {c["symbol"] for c in repo.get_all_candidates()}
        max_new = self.cfg.get("rebalancing", {}).get(
            "quarterly_major_rebalance", {}
        ).get("max_watchlist_additions", 30)

        all_candidates = self.scanner.scan_all()
        new_candidates = [c for c in all_candidates if c["symbol"] not in existing]
        new_syms = [c["symbol"] for c in new_candidates]
        price_data = _fetch_price_data(new_syms)
        volume_passed = self.scanner.apply_volume_filter(new_candidates, price_data)
        volume_passed.sort(key=lambda x: x.get("market_cap_cr") or 0, reverse=True)
        to_add = volume_passed[:max_new]

        added = 0
        for c in to_add:
            self.manager.add_to_watchlist(
                c["symbol"], name=c.get("name", ""),
                sector=c.get("sector", "Unknown"),
                market_cap_cr=c.get("market_cap_cr"),
                isin=c.get("isin", ""),
                reason="quarterly_scan",
            )
            added += 1

        # Full weekly refresh
        weekly = self.weekly_ranking_refresh(today)
        weekly["new_candidates_added"] = added

        # Run quarterly audit
        run_audit = self.cfg.get("rebalancing", {}).get(
            "quarterly_major_rebalance", {}
        ).get("run_audit", True)
        if run_audit:
            try:
                audit = self.auditor.run_quarterly_audit(today)
                weekly["audit"] = audit
                logger.info("[Quarterly] Audit complete: %s",
                            audit.get("strength_verdict", "?"))
            except Exception as e:
                logger.warning("[Quarterly] Audit failed: %s", e)

        logger.info("[Quarterly] Done. Added %d new candidates.", added)
        return weekly

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _get_index_data() -> pd.DataFrame:
        try:
            from data.fetcher import fetch_symbol
            df = fetch_symbol("Nifty 50", lookback_days=200)
            if not df.empty:
                df = df.reset_index()
                if "Close" in df.columns:
                    df.rename(columns={"Close": "close"}, inplace=True)
                return df
        except Exception as e:
            logger.debug("Index data fetch failed: %s", e)
        return pd.DataFrame()
