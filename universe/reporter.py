"""
Universe reporter — generates weekly reports and quarterly audit reports.
"""
import os
import json
import logging
from datetime import date
from typing import Dict, List, Optional, Any

from db import universe_repo as repo

logger = logging.getLogger(__name__)


class UniverseReporter:
    """Generate universe health reports and deliver via file + Telegram."""

    def __init__(self, config: Dict, output_dir: str = "outputs/universe"):
        self.cfg = config.get("notifications", {})
        self.output_dir = output_dir
        self.audit_dir  = config.get("audit", {}).get("output_dir",
                                                        os.path.join(output_dir, "audit"))
        os.makedirs(output_dir,     exist_ok=True)
        os.makedirs(self.audit_dir, exist_ok=True)

    # ── Weekly Report ────────────────────────────────────────────────────────

    def generate(self, summary: Optional[Dict] = None, today: date = None) -> str:
        today = today or date.today()
        stats = repo.get_universe_stats()
        recent = repo.get_history(limit=20)

        lines = [
            f"=== Universe Weekly Report {today.isoformat()} ===",
            f"CORE:      {stats.get('core', 0):3d}  stocks",
            f"WATCHLIST: {stats.get('watchlist', 0):3d}  stocks",
            f"IPO WATCH: {stats.get('ipo_watch', 0):3d}  stocks",
            f"LOCKOUT:   {stats.get('lockout', 0):3d}  stocks",
            f"REMOVED:   {stats.get('removed', 0):3d}  stocks",
            f"DELISTED:  {stats.get('delisted', 0):3d}  stocks",
            "",
        ]

        if summary:
            lines += [
                "── This Week ──────────────────────────────────",
                f"  Promoted:         {summary.get('promoted', 0)}",
                f"  Demoted:          {summary.get('demoted', 0)}",
                f"  Removed:          {summary.get('removed', 0)}",
                f"  Sector capped:    {summary.get('sector_capped', 0)}",
                f"  Delisted:         {summary.get('delisted', 0)}",
                f"  IPO processed:    {summary.get('ipo_processed', 0)}",
                f"  New added:        {summary.get('new_candidates_added', 0)}",
            ]
            # Strategy feedback counters — show only when non-zero
            if summary.get("strategy_loser", 0):
                lines.append(
                    f"  ⚠ Strategy losers demoted: {summary['strategy_loser']}  "
                    f"(low WR + negative P&L in strategy)"
                )
            if summary.get("anchor_protected", 0):
                lines.append(
                    f"  🔒 Anchor-protected (skip removal): {summary['anchor_protected']}  "
                    f"(never-traded stocks kept as RS anchors)"
                )
            lines.append("")

        # Strategy feedback: show current CORE stocks flagged as strategy losers
        lines.append("── Strategy Feedback: CORE Losers ─────────────")
        loser_lines = self._strategy_loser_section()
        lines += loser_lines if loser_lines else ["  ✅ No confirmed strategy losers in CORE."]
        lines.append("")

        # Sector concentration
        concentration = repo.get_sector_concentration("core")
        core_total = max(stats.get("core", 1), 1)
        lines.append("── CORE Sector Concentration ───────────────────")
        for sector, count in list(concentration.items())[:8]:
            pct   = count / core_total * 100
            flag  = " ⚠️" if pct > 25 else ""
            lines.append(f"  {sector:<25} {count:3d} ({pct:.0f}%){flag}")

        lines.append("")
        lines.append("── Recent Events ─────────────────────────────")
        for ev in recent[:10]:
            sym   = ev.get("symbol", "?")
            event = ev.get("event", "?")
            score = ev.get("score_at_event") or 0
            ts    = str(ev.get("ts", ""))[:10]
            lines.append(f"  {ts}  {sym:<18} {event:<22} pct={score:.0f}")

        lines.append("")
        lines.append("── Top 10 CORE Stocks ──────────────────────")
        core = repo.get_candidates_by_status("core")[:10]
        for c in core:
            lines.append(
                f"  {c['symbol']:<18} pct={c.get('score_percentile', 0):5.1f}  "
                f"sector={c.get('sector', '?')}"
            )

        return "\n".join(lines)

    def _strategy_loser_section(self) -> List[str]:
        """
        For each CORE stock, query strategy P&L. Return flagged lines for
        stocks that meet the loser threshold (WR < 25%, net P&L < -5k, 3+ trades).
        These are candidates the momentum scorer doesn't know about.
        """
        MIN_TRADES   = 3
        MAX_WR       = 0.25
        MAX_NET_PNL  = -5000

        core = repo.get_candidates_by_status("core")
        flagged = []
        for c in core:
            sym = c["symbol"]
            fb  = repo.get_strategy_stats(sym)
            if (fb["trades"] >= MIN_TRADES
                    and fb["win_rate"] < MAX_WR
                    and fb["net_pnl"] < MAX_NET_PNL):
                flagged.append((sym, fb))

        if not flagged:
            return []

        lines = []
        flagged.sort(key=lambda x: x[1]["net_pnl"])
        for sym, fb in flagged:
            lines.append(
                f"  ⚠ {sym:<18} P&L=₹{fb['net_pnl']:>+9,.0f}  "
                f"WR={fb['wins']}/{fb['trades']} ({fb['win_rate']*100:.0f}%)  "
                f"→ will demote next weekly run"
            )
        return lines

    # ── Quarterly Audit Report ───────────────────────────────────────────────

    def generate_audit(self, audit: Dict, today: date = None) -> str:
        today = today or date.today()
        lines = [
            f"╔══════════════════════════════════════════════════════╗",
            f"║  UNIVERSE AUDIT REPORT — {audit.get('quarter', '?')}",
            f"║  {audit.get('quarter_range', '')}",
            f"╚══════════════════════════════════════════════════════╝",
            "",
        ]

        # Changes this quarter
        ch = audit.get("changes", {})
        lines += [
            "── Quarter Changes ─────────────────────────────────────",
            f"  Stocks added to watchlist:  {ch.get('added_to_watchlist', 0)}",
            f"  Promoted to CORE:           {ch.get('promoted_to_core', 0)}",
            f"  Demoted from CORE:          {ch.get('demoted_from_core', 0)}",
            f"    of which strategy losers: {ch.get('strategy_loser_demotions', 0)}",
            f"  Removed / locked out:       {ch.get('removed', 0)}",
            f"    of which anchor-protected (skipped): {ch.get('anchor_protected', 0)}",
            f"  Delisted detected:          {ch.get('delisted', 0)}",
            f"  Net CORE change:            {ch.get('net_core_change', 0):+d}",
            "",
        ]

        # Top new entrants
        entrants = audit.get("top_new_entrants", [])
        lines.append("── Top New Entrants ────────────────────────────────────")
        if entrants:
            for e in entrants[:10]:
                m6  = f"{e['momentum_6m']:.1f}%" if e.get("momentum_6m") else "n/a"
                lines.append(
                    f"  {e['symbol']:<18} pct={e.get('score_percentile', 0):5.1f}  "
                    f"sector={e.get('sector', '?'):<20} 6M_rel={m6}"
                )
        else:
            lines.append("  (no new entrants this quarter)")
        lines.append("")

        # Deteriorating CORE members
        det = audit.get("deteriorating_core", [])
        lines.append("── Deteriorating CORE Members (action may be needed) ───")
        if det:
            for d in det[:10]:
                lines.append(
                    f"  {d['symbol']:<18} pct={d.get('current_pct', 0):5.1f}  "
                    f"slope={d['weekly_slope']:+.1f}/wk  "
                    f"sector={d.get('sector', '?')}"
                )
        else:
            lines.append("  ✅ No deteriorating CORE members detected.")
        lines.append("")

        # Universe Health
        curr = audit.get("current_health", {})
        prev = audit.get("previous_health", {})
        lines += [
            "── Universe Health ─────────────────────────────────────",
            f"  Avg Composite Score:   {curr.get('avg_composite_score', 0):.1f}  "
            f"(prev: {prev.get('avg_composite_score', 0):.1f})",
            f"  Avg Score Percentile:  {curr.get('avg_score_percentile', 0):.1f}  "
            f"(prev: {prev.get('avg_score_percentile', 0):.1f})",
            f"  Avg Liquidity Score:   {curr.get('avg_volume_quality', 0):.2f}  "
            f"(prev: {prev.get('avg_volume_quality', 0):.2f})",
            f"  Score Dispersion (σ):  {curr.get('score_dispersion_std', 0):.1f}  "
            f"(higher = clearer separation between winners/losers)",
            f"  Symbols Tracked:       {curr.get('n_symbols_tracked', 0)}",
            "",
        ]

        # Sector concentration
        sec = audit.get("sector_concentration", {})
        lines.append("── CORE Sector Concentration ────────────────────────────")
        for sector, data in list(sec.items())[:10]:
            flag = data.get("flag", "")
            lines.append(
                f"  {sector:<25} {data['count']:3d} ({data['pct_of_core']:4.1f}%)  "
                f"avg_pct={data['avg_score_pct']:.0f}  {flag}"
            )
        lines.append("")

        # IPO exposure
        ipo = audit.get("ipo_exposure", {})
        flag = ipo.get("flag", "")
        lines += [
            "── IPO / Young Stock Exposure ───────────────────────────",
            f"  Young stocks in CORE: {ipo.get('young_in_core', 0)} "
            f"({ipo.get('pct_of_core', 0):.1f}%) {flag}",
        ]
        if ipo.get("symbols"):
            lines.append(f"  Symbols: {', '.join(ipo['symbols'])}")
        lines.append("")

        # Verdict
        verdict = audit.get("strength_verdict", "UNKNOWN")
        verdict_icon = {"STRONG": "💪", "MODERATE": "📊", "WEAK": "⚠️"}.get(
            verdict.split(" / ")[0], "")
        lines += [
            "╔══════════════════════════════════════════════════════╗",
            f"║  UNIVERSE STRENGTH:  {verdict_icon} {verdict}",
            f"╚══════════════════════════════════════════════════════╝",
        ]

        return "\n".join(lines)

    # ── Persistence & Delivery ───────────────────────────────────────────────

    def save(self, report_text: str, today: date = None, prefix: str = "universe") -> str:
        today = today or date.today()
        filename = os.path.join(self.output_dir, f"{prefix}_{today.isoformat()}.txt")
        with open(filename, "w") as f:
            f.write(report_text)
        logger.info("[Reporter] Saved to %s", filename)
        return filename

    def save_audit(self, audit_dict: Dict, audit_text: str,
                   today: date = None) -> str:
        today = today or date.today()
        label = audit_dict.get("quarter", today.isoformat())
        txt_path  = os.path.join(self.audit_dir, f"audit_{label}.txt")
        json_path = os.path.join(self.audit_dir, f"audit_{label}.json")
        with open(txt_path, "w") as f:
            f.write(audit_text)
        with open(json_path, "w") as f:
            json.dump(audit_dict, f, indent=2, default=str)
        logger.info("[Reporter] Audit saved to %s", txt_path)
        return txt_path

    def notify(self, report_text: str, tag: str = ""):
        if not self.cfg.get("telegram", False):
            return
        try:
            from notifications.telegram import send_message
            header = f"[Universe{' ' + tag if tag else ''}]\n"
            msg = header + report_text[:3800]
            send_message(msg)
        except Exception as e:
            logger.warning("[Reporter] Telegram failed: %s", e)

    def generate_and_deliver(self, summary: Optional[Dict] = None,
                              today: date = None,
                              audit: Optional[Dict] = None) -> str:
        today = today or date.today()
        report = self.generate(summary, today)
        path   = self.save(report, today)
        if self.cfg.get("telegram"):
            self.notify(report)

        if audit:
            audit_text = self.generate_audit(audit, today)
            self.save_audit(audit, audit_text, today)
            if self.cfg.get("telegram"):
                self.notify(audit_text, tag="QUARTERLY AUDIT")

        return path
