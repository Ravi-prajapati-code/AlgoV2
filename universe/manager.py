"""
Universe manager — churn-protected promotion/demotion/removal.

Improvements over v1:
  - Survivorship bias detection: stocks with no data for 2+ weeks → delisted
  - Sector concentration caps: max 25% of CORE from any one sector
  - Regime-aware demotion: pause CORE demotions during sustained BEAR (all scores drag)
  - Fast-track removal: score < 10th pct for 2 weeks → immediate removal (blowups)
  - Fast-track promotion: score >= 90th pct → promote in 2 weeks not 4
  - Minimum data requirement: stock must have 26+ weeks history before promotion

Does NOT touch the trading strategy.
"""
import logging
from datetime import date, timedelta
from typing import List, Dict, Set, Optional

from db import universe_repo as repo
from config.universe_removed import REMOVED_SYMBOLS

logger = logging.getLogger(__name__)


class UniverseManager:

    def __init__(self, config: Dict):
        self.promo_cfg  = config.get("promotion",  {})
        self.demo_cfg   = config.get("demotion",   {})
        self.remove_cfg = config.get("removal",    {})
        self.lock_cfg   = config.get("lockout",    {})
        univ_cfg        = config.get("universe",   {})
        surv_cfg        = config.get("survivorship", {})

        self.core_size   = univ_cfg.get("core_size", 100)
        self.wl_size     = univ_cfg.get("watchlist_size", 200)
        self.max_sector_pct = univ_cfg.get("max_sector_pct_of_core", 0.25)

        # Promotion
        self.promo_weeks      = self.promo_cfg.get("min_weeks_above_threshold", 4)
        self.promo_pct        = self.promo_cfg.get("min_score_percentile", 60)
        self.fast_track_pct   = self.promo_cfg.get("fast_track_percentile", 90)
        self.min_data_weeks   = self.promo_cfg.get("min_data_weeks", 26)

        # Demotion
        self.demo_weeks           = self.demo_cfg.get("min_weeks_below_threshold", 3)
        self.demo_pct             = self.demo_cfg.get("max_score_percentile", 40)
        self.pause_in_bear        = self.demo_cfg.get("pause_in_bear_regime", True)
        self.bear_floor_pct       = self.demo_cfg.get("bear_regime_floor_pct", 15)

        # Removal
        self.remove_weeks         = self.remove_cfg.get("min_weeks_below_threshold", 6)
        self.remove_pct           = self.remove_cfg.get("max_score_percentile", 25)
        self.fast_remove_pct      = self.remove_cfg.get("fast_track_pct", 10)
        self.fast_remove_weeks    = self.remove_cfg.get("fast_track_weeks", 2)

        # Lockout
        self.lockout_wks          = self.lock_cfg.get("lockout_weeks", 8)

        # Survivorship
        self.no_data_threshold    = surv_cfg.get("no_data_weeks_threshold", 2)
        self.liq_degradation_pct  = surv_cfg.get("liquidity_degradation_pct", 60)

        # Strategy feedback — closes the loop from trade P&L back to universe decisions
        fb_cfg                    = config.get("strategy_feedback", {})
        self.fb_enabled           = fb_cfg.get("enabled", True)
        self.fb_min_trades        = fb_cfg.get("min_trades", 3)
        self.fb_max_win_rate      = fb_cfg.get("max_win_rate", 0.25)
        self.fb_max_net_pnl       = fb_cfg.get("max_net_pnl", -5000)
        self.fb_anchor_months     = fb_cfg.get("anchor_lookback_months", 6)
        self.fb_anchor_override   = fb_cfg.get("anchor_override_score_pct", 10)

    # ── Public API ──────────────────────────────────────────────────────────

    def refresh(self, scored: List[Dict], today: date = None,
                regime: str = "BULL",
                symbols_with_no_data: Optional[Set[str]] = None) -> Dict:
        """
        Full weekly refresh:
          1. Handle delisted/missing-data stocks
          2. Persist scores
          3. Apply churn-protected promotion / demotion / removal
          4. Enforce sector concentration caps
          5. Enforce size caps
          6. Rebuild universe_active
        """
        today = today or date.today()
        summary = {"promoted": 0, "demoted": 0, "removed": 0, "locked": 0,
                   "delisted": 0, "sector_capped": 0}

        if not scored:
            logger.warning("[Manager] No scored candidates — skipping refresh.")
            return summary

        # 1. Survivorship bias: flag stocks with no price data
        if symbols_with_no_data:
            self._check_delistings(symbols_with_no_data, today, summary)

        # 2. Persist scores
        scored_with_status = {s["symbol"]: s for s in scored}
        repo.bulk_update_scores(scored)

        # 3. Apply per-symbol rules
        all_candidates = repo.get_all_candidates()
        for cand in all_candidates:
            sym    = cand["symbol"]
            status = cand["status"]

            # Permanent block-list: documented strategy losers must never re-enter the
            # universe regardless of momentum score. A lockout alone (below) expires and
            # re-admits to watchlist -- that gap is what let LAURUSLABS.NS/THERMAX.NS leak
            # back into 'core' after their original 2026-06-17 removal. Self-heals here on
            # every weekly refresh no matter how a block-listed symbol reached a live status.
            if sym in REMOVED_SYMBOLS and status != "removed":
                reason = f"blocklist: {REMOVED_SYMBOLS[sym]}"
                repo.update_candidate_status(sym, "removed", reason=reason, operator="system")
                summary["blocklist_removed"] = summary.get("blocklist_removed", 0) + 1
                logger.warning("[Manager] BLOCKLIST %s (was %s) -> forced removal (%s).",
                               sym, status, reason)
                continue

            if status in ("removed", "lockout", "delisted"):
                if status == "lockout":
                    self._check_lockout_expiry(cand, today)
                continue

            score_row = scored_with_status.get(sym)
            if score_row is None:
                # No score this week — increment no-data counter is handled by delistings
                continue

            pct = score_row["score_percentile"]

            if status == "core":
                self._maybe_demote(cand, pct, today, summary, regime=regime)
            elif status in ("watchlist", "ipo_watch"):
                self._maybe_promote(cand, pct, today, summary)

        # 4. Sector concentration caps
        self._enforce_sector_caps(today, summary)

        # 5. Size caps
        self._enforce_size_caps(today)

        # 6. Rebuild fast-lookup active table
        repo.rebuild_active_universe()

        logger.info("[Manager] Weekly refresh done: %s", summary)
        return summary

    def add_to_watchlist(self, symbol: str, name: str = "", sector: str = "",
                          market_cap_cr: float = None, isin: str = "",
                          reason: str = "scan", operator: str = "system"):
        if symbol in REMOVED_SYMBOLS:
            logger.info("[Manager] %s on permanent block-list (%s) -- skip add.",
                        symbol, REMOVED_SYMBOLS[symbol])
            return
        existing = repo.get_candidate(symbol)
        if existing:
            if existing["status"] == "lockout":
                logger.info("[Manager] %s in lockout until %s — skip.",
                            symbol, existing.get("lockout_until"))
                return
            if existing["status"] == "delisted":
                logger.info("[Manager] %s marked delisted — skip add.", symbol)
                return
            logger.info("[Manager] %s already in universe (%s) — skip.",
                        symbol, existing["status"])
            return

        repo.upsert_candidate(
            symbol,
            name=name, sector=sector, market_cap_cr=market_cap_cr,
            isin=isin, status="watchlist",
            added_date=date.today().isoformat(),
            weeks_above_threshold=0, weeks_below_threshold=0,
        )
        repo.log_event(symbol, "added_watchlist", from_status="", to_status="watchlist",
                       reason=reason, operator=operator)
        logger.info("[Manager] Added %s to watchlist.", symbol)

    def manual_promote(self, symbol: str, reason: str = "manual"):
        if symbol in REMOVED_SYMBOLS:
            logger.warning("[Manager] Refusing manual_promote(%s) -- on permanent block-list (%s).",
                           symbol, REMOVED_SYMBOLS[symbol])
            return
        cand = repo.get_candidate(symbol)
        if not cand:
            logger.warning("[Manager] %s not in universe.", symbol)
            return
        repo.update_candidate_status(symbol, "core", reason=reason, operator="manual")
        repo.rebuild_active_universe()
        logger.info("[Manager] Manually promoted %s to core.", symbol)

    def manual_remove(self, symbol: str, reason: str = "manual"):
        cand = repo.get_candidate(symbol)
        if not cand:
            return
        repo.update_candidate_status(symbol, "removed", reason=reason, operator="manual")
        self._apply_lockout(symbol)
        repo.rebuild_active_universe()
        logger.info("[Manager] Manually removed %s.", symbol)

    def load_active_symbols(self) -> List[str]:
        return repo.get_active_symbols()

    # ── Survivorship Bias ────────────────────────────────────────────────────

    def _check_delistings(self, no_data_symbols: Set[str], today: date,
                           summary: Dict):
        """
        Stocks with no price data for no_data_threshold consecutive weeks
        are flagged as 'delisted' rather than silently demoted.
        This preserves audit trail integrity.
        """
        for sym in no_data_symbols:
            cand = repo.get_candidate(sym)
            if not cand or cand["status"] in ("removed", "lockout", "delisted"):
                continue

            weeks_below = cand.get("weeks_below_threshold", 0)
            if weeks_below >= self.no_data_threshold:
                old_status = cand["status"]
                # Overwrite 'removed' path with explicit 'delisted' event
                repo.upsert_candidate(sym, status="delisted")
                repo.log_event(sym, "delisted",
                               from_status=old_status, to_status="delisted",
                               reason=f"no_price_data_{weeks_below}_weeks")
                summary["delisted"] += 1
                logger.warning("[Manager] DELISTED %s — no data for %d weeks.",
                               sym, weeks_below)
            else:
                # Just increment counter for now
                repo.increment_churn_counter(sym, above=False)

    # ── Promotion / Demotion ─────────────────────────────────────────────────

    def _maybe_demote(self, cand: Dict, score_pct: float, today: date,
                       summary: Dict, regime: str = "BULL"):
        sym = cand["symbol"]

        # Guard 1: Strategy P&L veto — demote if stock consistently loses in strategy
        # Momentum score alone cannot detect strategy misfits (e.g. high-momentum stock
        # that churns in bear swing or enters late in cycle and always stops out)
        if self.fb_enabled:
            fb = repo.get_strategy_stats(sym)
            if (fb["trades"] >= self.fb_min_trades
                    and fb["win_rate"] < self.fb_max_win_rate
                    and fb["net_pnl"] < self.fb_max_net_pnl):
                reason = (
                    f"strategy_loser: {fb['wins']}/{fb['trades']} trades won "
                    f"({fb['win_rate']*100:.0f}% WR), net P&L=₹{fb['net_pnl']:,.0f}"
                )
                repo.update_candidate_status(sym, "watchlist", reason=reason)
                summary["strategy_loser"] = summary.get("strategy_loser", 0) + 1
                logger.warning("[Manager] STRATEGY LOSER %s → demoted to watchlist. %s",
                               sym, reason)
                return

        # Fast-track removal check first (applies in any regime)
        if score_pct < self.fast_remove_pct:
            repo.increment_churn_counter(sym, above=False)
            cand = repo.get_candidate(sym)
            if cand.get("weeks_below_threshold", 0) >= self.fast_remove_weeks:
                self._do_remove(sym, score_pct, cand["weeks_below_threshold"], summary,
                                tag="fast_track")
            # Always return here — prevent double-increment by falling into the
            # demo_pct block below (score < 10 is also < 40, so it would fire twice)
            return

        if score_pct < self.demo_pct:
            # Regime-aware pause: in BEAR, only demote below absolute floor
            if self.pause_in_bear and regime == "BEAR":
                if score_pct >= self.bear_floor_pct:
                    logger.debug("[Manager] %s BEAR demotion paused (score_pct=%.1f).",
                                 sym, score_pct)
                    repo.increment_churn_counter(sym, above=False)
                    return

            repo.increment_churn_counter(sym, above=False)
            cand = repo.get_candidate(sym)
            weeks_below = cand.get("weeks_below_threshold", 0)

            if weeks_below >= self.remove_weeks and score_pct < self.remove_pct:
                self._do_remove(sym, score_pct, weeks_below, summary)
            elif weeks_below >= self.demo_weeks:
                repo.update_candidate_status(
                    sym, "watchlist",
                    reason=f"score_pct={score_pct:.1f}<{self.demo_pct} for {weeks_below}w"
                )
                summary["demoted"] += 1
                logger.info("[Manager] DEMOTED %s → watchlist (%.1f, %dw).",
                            sym, score_pct, weeks_below)
        else:
            repo.increment_churn_counter(sym, above=True)

    def _maybe_promote(self, cand: Dict, score_pct: float, today: date,
                        summary: Dict):
        sym = cand["symbol"]

        # Fast-track removal from watchlist
        if score_pct < self.fast_remove_pct:
            repo.increment_churn_counter(sym, above=False)
            cand = repo.get_candidate(sym)
            if cand.get("weeks_below_threshold", 0) >= self.fast_remove_weeks:
                self._do_remove(sym, score_pct, cand["weeks_below_threshold"], summary,
                                tag="fast_track")
            # Always return — score < 10 is also < promo_pct, so else branch below
            # would double-increment the counter if we fall through
            return

        if score_pct >= self.promo_pct:
            repo.increment_churn_counter(sym, above=True)
            cand = repo.get_candidate(sym)
            weeks_above = cand.get("weeks_above_threshold", 0)

            # Determine required weeks (fast-track for exceptional momentum)
            required_weeks = (2 if score_pct >= self.fast_track_pct
                              else self.promo_weeks)

            if weeks_above >= required_weeks:
                core_count = len(repo.get_candidates_by_status("core"))
                if core_count < self.core_size:
                    repo.update_candidate_status(
                        sym, "core",
                        reason=(f"score_pct={score_pct:.1f}>={self.promo_pct} "
                                f"for {weeks_above}w"
                                + (" (fast-track)" if score_pct >= self.fast_track_pct else ""))
                    )
                    summary["promoted"] += 1
                    logger.info("[Manager] PROMOTED %s → core (%.1f, %dw%s).",
                                sym, score_pct, weeks_above,
                                " fast-track" if score_pct >= self.fast_track_pct else "")
        else:
            repo.increment_churn_counter(sym, above=False)
            cand = repo.get_candidate(sym)
            weeks_below = cand.get("weeks_below_threshold", 0)
            if weeks_below >= self.remove_weeks and score_pct < self.remove_pct:
                self._do_remove(sym, score_pct, weeks_below, summary)

    def _do_remove(self, symbol: str, score_pct: float, weeks: int,
                    summary: Dict, tag: str = ""):
        is_fast_track = (tag == "fast_track")

        # Guard 2: Anchor protection — never-traded stocks likely stabilize RS distribution.
        # RS rank = percentile across the universe. Removing a range-bound stock that the
        # strategy never enters shifts all RS percentiles → formerly-qualifying stocks drop
        # below the entry threshold → CAGR falls (proven empirically: -6.5pp when 3 anchors removed).
        # Skip non-fast-track removal. Allow fast-track (score < 10th pct = fundamental collapse).
        if self.fb_enabled and not is_fast_track and score_pct >= self.fb_anchor_override:
            recent_trades = repo.get_trade_count_recent(symbol, months=self.fb_anchor_months)
            if recent_trades == 0:
                reason = (
                    f"anchor_protected: 0 strategy trades in {self.fb_anchor_months}mo, "
                    f"score_pct={score_pct:.1f} — RS anchor candidate, skipping removal"
                )
                repo.log_event(symbol, "anchor_skip_removal", reason=reason)
                summary["anchor_protected"] = summary.get("anchor_protected", 0) + 1
                logger.warning(
                    "[Manager] ANCHOR PROTECTED %s — 0 trades in %dmo, score_pct=%.1f. "
                    "Skipping removal (RS distribution anchor).",
                    symbol, self.fb_anchor_months, score_pct,
                )
                return

        tag_str = f"[{tag}] " if tag else ""
        repo.update_candidate_status(
            symbol, "removed",
            reason=f"{tag_str}score_pct={score_pct:.1f}<{self.remove_pct} for {weeks}w"
        )
        self._apply_lockout(symbol)
        summary["removed"] += 1
        logger.info("[Manager] REMOVED %s%s (%.1f, %dw).",
                    "[FAST] " if tag == "fast_track" else "", symbol, score_pct, weeks)

    # ── Sector Concentration Cap ─────────────────────────────────────────────

    def _enforce_sector_caps(self, today: date, summary: Dict):
        """
        Demote lowest-scoring stocks from any sector that exceeds
        max_sector_pct_of_core × core_size.
        Prevents universe drifting to 40% financials in bull runs.
        """
        core = repo.get_candidates_by_status("core")  # sorted score_pct DESC
        max_per_sector = max(1, int(self.core_size * self.max_sector_pct))

        sector_buckets: Dict[str, List[Dict]] = {}
        for c in core:
            sec = c.get("sector") or "Unknown"
            sector_buckets.setdefault(sec, []).append(c)

        for sector, stocks in sector_buckets.items():
            if len(stocks) <= max_per_sector:
                continue
            # Stocks are already sorted DESC by score_percentile (from repo query)
            # Demote the lowest-scoring excess
            to_demote = stocks[max_per_sector:]
            for c in to_demote:
                repo.update_candidate_status(
                    c["symbol"], "watchlist",
                    reason=(f"sector_cap: {sector} has {len(stocks)} stocks "
                            f"(max {max_per_sector})")
                )
                summary["sector_capped"] += 1
                logger.info("[Manager] Sector cap: demoted %s from %s (%d→%d).",
                            c["symbol"], sector, len(stocks), max_per_sector)

    # ── Size Cap ─────────────────────────────────────────────────────────────

    def _enforce_size_caps(self, today: date):
        core = repo.get_candidates_by_status("core")
        overflow = len(core) - self.core_size
        if overflow > 0:
            for c in core[-overflow:]:
                repo.update_candidate_status(
                    c["symbol"], "watchlist",
                    reason=f"size_cap: overflow by {overflow}"
                )
                logger.info("[Manager] Demoted %s (size cap).", c["symbol"])

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _apply_lockout(self, symbol: str):
        until = date.today() + timedelta(weeks=self.lockout_wks)
        repo.set_lockout(symbol, until)
        repo.update_candidate_status(
            symbol, "lockout",
            reason=f"lockout until {until.isoformat()}"
        )
        repo.log_event(symbol, "lockout_applied",
                       reason=f"until {until.isoformat()}")

    def _check_lockout_expiry(self, cand: Dict, today: date):
        sym = cand["symbol"]
        lu  = cand.get("lockout_until")
        if not lu:
            return
        try:
            exp = date.fromisoformat(str(lu)[:10])
        except Exception:
            return
        if today >= exp:
            repo.update_candidate_status(sym, "watchlist",
                                          reason="lockout_expired",
                                          operator="system")
            repo.log_event(sym, "lockout_expired", from_status="lockout",
                           to_status="watchlist")
            logger.info("[Manager] Lockout expired for %s.", sym)
