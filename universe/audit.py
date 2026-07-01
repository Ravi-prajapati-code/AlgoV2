"""
Universe Audit Engine — quarterly health report for the candidate universe.

Answers: "Is the universe getting stronger or weaker over time?"

Generates:
  - Stocks Added / Removed this quarter
  - Top New Entrants (score + sector + momentum)
  - Worst CORE Members (score deterioration, weeks in decline)
  - Universe Health:
      - Avg composite score (this quarter vs last)
      - Score trend direction
      - Sector concentration (% per sector)
      - IPO exposure (% of CORE < 2 years old)
      - Avg liquidity (volume_quality)
      - Score dispersion (std dev)
  - Universe Strength verdict: STRENGTHENING / STABLE / WEAKENING

Used by:
  - RebalancingEngine.quarterly_major_rebalance()
  - UniverseReporter for quarterly reports
  - Manual: python3 main.py universe --mode audit  (TODO: wire up)
"""
import logging
from datetime import date, timedelta
from typing import Dict, List, Optional, Any

import numpy as np

from db import universe_repo as repo

logger = logging.getLogger(__name__)


class UniverseAuditEngine:
    """Run quarterly universe health analysis."""

    def __init__(self, config: Dict):
        audit_cfg = config.get("audit", {})
        self.deterioration_weeks = audit_cfg.get("deterioration_weeks", 4)
        self.deterioration_slope = audit_cfg.get("deterioration_slope", -2.0)
        self.strong_avg_pct      = audit_cfg.get("strength_strong_avg_pct", 65)
        self.weak_avg_pct        = audit_cfg.get("strength_weak_avg_pct", 45)
        self.ipo_max_age_days     = config.get("ipo", {}).get("lock_in_days", 180) * 4
        # "young" = listing within ~2 years (4 × lock-in periods)

    # ── Public API ──────────────────────────────────────────────────────────

    def run_quarterly_audit(self, today: date = None) -> Dict[str, Any]:
        """
        Full quarterly audit. Returns structured dict ready for reporting.
        """
        today = today or date.today()

        # Quarter boundaries
        q_start, q_end = self._quarter_bounds(today)
        prev_q_start, prev_q_end = self._quarter_bounds(
            q_start - timedelta(days=1)
        )

        report = {
            "as_of":          today.isoformat(),
            "quarter":        self._quarter_label(today),
            "quarter_range":  f"{q_start.isoformat()} → {q_end.isoformat()}",
        }

        # ── 1. Changes this quarter ─────────────────────────────────────────
        report["changes"] = self._quarter_changes(q_start, today)

        # ── 2. Top new entrants ──────────────────────────────────────────────
        report["top_new_entrants"] = self._top_new_entrants(q_start, today)

        # ── 3. Worst CORE members (deteriorating) ───────────────────────────
        report["deteriorating_core"] = self._detect_deterioration()

        # ── 4. Universe health metrics ───────────────────────────────────────
        report["current_health"]  = self._health_metrics(q_start, today)
        report["previous_health"] = self._health_metrics(prev_q_start, prev_q_end)

        # ── 5. Sector concentration ──────────────────────────────────────────
        report["sector_concentration"] = self._sector_analysis()

        # ── 6. IPO exposure ──────────────────────────────────────────────────
        report["ipo_exposure"] = self._ipo_exposure(today)

        # ── 7. Strength verdict ──────────────────────────────────────────────
        report["strength_verdict"] = self._strength_verdict(
            report["current_health"], report["previous_health"]
        )

        return report

    # ── Section Builders ────────────────────────────────────────────────────

    def _quarter_changes(self, q_start: date, today: date) -> Dict:
        events = repo.get_events_in_period(q_start, today)
        added         = [e for e in events if e["event"] in ("added_watchlist", "ipo_qualified")]
        promoted      = [e for e in events if e["event"] == "promoted_core"]
        demoted       = [e for e in events if e["event"] == "demoted_watchlist"]
        strat_losers  = [e for e in events if "strategy_loser" in (e.get("reason") or "")]
        removed       = [e for e in events if e["event"] in ("removed", "lockout_applied")]
        anchors_skip  = [e for e in events if e["event"] == "anchor_skip_removal"]
        delisted      = [e for e in events if e["event"] == "delisted"]
        return {
            "added_to_watchlist":      len(added),
            "promoted_to_core":        len(promoted),
            "demoted_from_core":       len(demoted),
            "strategy_loser_demotions": len(strat_losers),
            "removed":                 len(removed),
            "anchor_protected":        len(anchors_skip),
            "delisted":                len(delisted),
            "net_core_change":         len(promoted) - len(demoted),
        }

    def _top_new_entrants(self, q_start: date, today: date,
                           n: int = 10) -> List[Dict]:
        """
        Stocks promoted to CORE this quarter, ranked by current score.
        Returns top N with score, sector, and momentum.
        """
        events = repo.get_events_in_period(q_start, today)
        promoted_syms = {e["symbol"] for e in events if e["event"] == "promoted_core"}
        if not promoted_syms:
            return []

        results = []
        for sym in promoted_syms:
            cand = repo.get_candidate(sym)
            if not cand:
                continue
            history = repo.get_score_history(sym, weeks=4)
            momentum_6m = None
            if history:
                # Get rs_momentum_6m from latest metrics if available
                conn_row = history[0]
                momentum_6m = conn_row.get("rs_momentum_6m")
            results.append({
                "symbol":          sym,
                "sector":          cand.get("sector", "Unknown"),
                "composite_score": cand.get("composite_score", 0),
                "score_percentile": cand.get("score_percentile", 0),
                "momentum_6m":     momentum_6m,
            })

        results.sort(key=lambda x: x["score_percentile"], reverse=True)
        return results[:n]

    def _detect_deterioration(self) -> List[Dict]:
        """
        CORE stocks with consistently declining score_percentile.
        Uses linear regression slope over last N weeks.
        Flags stocks with slope < deterioration_slope threshold.
        """
        core = repo.get_candidates_by_status("core")
        deteriorating = []

        for cand in core:
            sym     = cand["symbol"]
            history = repo.get_score_history(sym, weeks=self.deterioration_weeks + 2)
            if len(history) < self.deterioration_weeks:
                continue

            # history is DESC (newest first) — reverse for chronological slope
            pcts = [h.get("score_percentile", 0) or 0
                    for h in reversed(history[:self.deterioration_weeks])]

            if len(pcts) < 3:
                continue

            x = np.arange(len(pcts))
            slope = float(np.polyfit(x, pcts, 1)[0])  # percentile points per week

            if slope <= self.deterioration_slope:
                deteriorating.append({
                    "symbol":          sym,
                    "sector":          cand.get("sector", "Unknown"),
                    "current_pct":     cand.get("score_percentile", 0),
                    "weeks_in_decline": self.deterioration_weeks,
                    "weekly_slope":    round(slope, 2),
                    "trend":           "DETERIORATING",
                })

        deteriorating.sort(key=lambda x: x["weekly_slope"])
        return deteriorating

    def _health_metrics(self, from_date: date, to_date: date) -> Dict:
        """Aggregate health metrics for a time period."""
        metrics = repo.get_avg_metrics_for_period(from_date, to_date)
        core    = repo.get_candidates_by_status("core")

        # Score dispersion: std dev of score_percentile across CORE
        pcts = [c.get("score_percentile", 0) or 0 for c in core]
        score_std = float(np.std(pcts)) if pcts else 0.0

        return {
            "avg_composite_score":  round(metrics.get("avg_score") or 0, 2),
            "avg_score_percentile": round(metrics.get("avg_pct") or 0, 2),
            "avg_volume_quality":   round(metrics.get("avg_volume") or 0, 2),
            "n_symbols_tracked":    int(metrics.get("n_symbols") or 0),
            "score_dispersion_std": round(score_std, 2),
            # High dispersion = universe has clear winners AND losers (healthy)
            # Low dispersion = all stocks clustered together (regime fog)
        }

    def _sector_analysis(self) -> Dict:
        """
        Sector breakdown of CORE.
        Returns {sector: {count, pct, avg_score}} sorted by count desc.
        """
        concentration = repo.get_sector_concentration("core")
        core = repo.get_candidates_by_status("core")
        total = max(len(core), 1)

        # Avg score per sector
        sector_scores: Dict[str, List[float]] = {}
        for c in core:
            sec = c.get("sector") or "Unknown"
            sector_scores.setdefault(sec, []).append(
                c.get("score_percentile", 0) or 0
            )

        result = {}
        for sector, count in concentration.items():
            pct   = round(count / total * 100, 1)
            scores = sector_scores.get(sector, [0])
            result[sector] = {
                "count":     count,
                "pct_of_core": pct,
                "avg_score_pct": round(float(np.mean(scores)), 1),
                "flag": "⚠️ CONCENTRATED" if pct > 25 else "",
            }

        return dict(sorted(result.items(),
                            key=lambda x: x[1]["count"], reverse=True))

    def _ipo_exposure(self, today: date) -> Dict:
        """% of CORE that is 'young' (listed < ipo_max_age_days ago)."""
        core = repo.get_candidates_by_status("core")
        ipos = repo.get_watching_ipos()
        ipo_syms = {i["symbol"] for i in ipos}

        young_in_core = []
        for c in core:
            sym = c["symbol"]
            added = c.get("added_date")
            if sym in ipo_syms:
                young_in_core.append(sym)
            elif added:
                try:
                    added_d = date.fromisoformat(str(added)[:10])
                    if (today - added_d).days < self.ipo_max_age_days:
                        young_in_core.append(sym)
                except Exception:
                    pass

        total = max(len(core), 1)
        return {
            "young_in_core":      len(young_in_core),
            "pct_of_core":        round(len(young_in_core) / total * 100, 1),
            "symbols":            young_in_core[:10],  # top 10 for report
            "flag": "⚠️ HIGH IPO EXPOSURE" if len(young_in_core) / total > 0.10 else "",
        }

    def _strength_verdict(self, current: Dict, previous: Dict) -> str:
        """
        Compare current vs previous quarter avg score percentile.
        Returns STRENGTHENING / STABLE / WEAKENING.
        """
        curr_pct = current.get("avg_score_percentile", 0) or 0
        prev_pct = previous.get("avg_score_percentile", 0) or 0

        # Absolute strength
        if curr_pct >= self.strong_avg_pct:
            abs_verdict = "STRONG"
        elif curr_pct >= self.weak_avg_pct:
            abs_verdict = "MODERATE"
        else:
            abs_verdict = "WEAK"

        # Trend vs previous quarter
        if prev_pct == 0:
            trend = "BASELINE"
        elif curr_pct > prev_pct + 3:
            trend = "STRENGTHENING"
        elif curr_pct < prev_pct - 3:
            trend = "WEAKENING"
        else:
            trend = "STABLE"

        return f"{abs_verdict} / {trend}"

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _quarter_bounds(ref: date):
        """Return (quarter_start, quarter_end) for the quarter containing ref."""
        q = (ref.month - 1) // 3
        q_start = date(ref.year, q * 3 + 1, 1)
        q_end_month = q * 3 + 3
        q_end_year  = ref.year
        if q_end_month == 12:
            q_end = date(q_end_year, 12, 31)
        else:
            q_end = date(q_end_year, q_end_month + 1, 1) - timedelta(days=1)
        return q_start, q_end

    @staticmethod
    def _quarter_label(ref: date) -> str:
        q = (ref.month - 1) // 3 + 1
        return f"Q{q}-{ref.year}"
