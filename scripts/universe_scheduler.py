#!/usr/bin/env python3
"""
Universe scheduler — cron entry point for all automated universe management.

Crontab (add to server crontab with: crontab -e):
  # Daily quality check (Mon–Fri 18:05 IST = 12:35 UTC)
  35 12 * * 1-5  cd /path/to/AlgoV2 && python3 scripts/universe_scheduler.py --mode daily

  # Weekly ranking refresh (Friday 18:30 IST = 13:00 UTC)
  0 13 * * 5     cd /path/to/AlgoV2 && python3 scripts/universe_scheduler.py --mode weekly

  # Monthly universe refresh (last Friday of month — checked dynamically)
  5 13 * * 5     cd /path/to/AlgoV2 && python3 scripts/universe_scheduler.py --mode monthly

  # Quarterly major rebalance (last Friday of Mar/Jun/Sep/Dec quarter — checked dynamically)
  10 13 * * 5    cd /path/to/AlgoV2 && python3 scripts/universe_scheduler.py --mode quarterly

Usage:
  python3 scripts/universe_scheduler.py --mode weekly
  python3 scripts/universe_scheduler.py --mode seed    # one-time: seed DB from static watchlist
  python3 scripts/universe_scheduler.py --mode status  # print current stats
"""
import argparse
import logging
import sys
import os
from datetime import date, timedelta
from calendar import monthrange

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("universe_scheduler")


def _is_last_friday_of_month(d: date) -> bool:
    if d.weekday() != 4:  # not Friday
        return False
    next_week = d + timedelta(weeks=1)
    return next_week.month != d.month


def _is_last_friday_of_quarter(d: date) -> bool:
    if not _is_last_friday_of_month(d):
        return False
    return d.month in (3, 6, 9, 12)


def cmd_seed(args):
    """One-time: seed DB from static watchlist so the system has a starting point."""
    from db.repository import init_db
    from db.universe_repo import init_universe_db
    from universe.manager import UniverseManager
    from config.watchlist_nse import WATCHLIST, SYMBOL_TO_SECTOR, SYMBOL_TO_NAME
    import yaml

    with open("config/universe_config.yaml") as f:
        cfg = yaml.safe_load(f)

    init_db()
    init_universe_db()
    mgr = UniverseManager(cfg)

    count = 0
    for item in WATCHLIST:
        sym, sector, name = item if len(item) == 3 else (item[0], item[1], item[0])
        mgr.add_to_watchlist(sym, name=name, sector=sector, reason="seed_from_static_watchlist")
        count += 1

    print(f"[Seed] Added {count} stocks to watchlist from static config.")
    print("Run --mode weekly to score and promote top stocks to CORE.")


def cmd_status(args):
    from db.repository import init_db
    from db.universe_repo import init_universe_db, get_universe_stats, get_active_symbols
    init_db()
    init_universe_db()
    stats = get_universe_stats()
    print("\n=== Universe Status ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    active = get_active_symbols()
    print(f"\nActive CORE symbols ({len(active)}):")
    for sym in active[:20]:
        print(f"  {sym}")
    if len(active) > 20:
        print(f"  ... and {len(active) - 20} more")


def cmd_daily(args):
    from db.repository import init_db
    from db.universe_repo import init_universe_db
    from universe.rebalancer import RebalancingEngine
    from universe.reporter import UniverseReporter
    import yaml

    init_db()
    init_universe_db()
    with open("config/universe_config.yaml") as f:
        cfg = yaml.safe_load(f)

    today = date.today()
    engine = RebalancingEngine()
    result = engine.daily_quality_check(today)
    logger.info("Daily QC result: %s", result)

    reporter = UniverseReporter(cfg)
    reporter.generate_and_deliver(result, today)


def cmd_weekly(args):
    from db.repository import init_db
    from db.universe_repo import init_universe_db
    from universe.rebalancer import RebalancingEngine
    from universe.reporter import UniverseReporter
    import yaml

    init_db()
    init_universe_db()
    with open("config/universe_config.yaml") as f:
        cfg = yaml.safe_load(f)

    today = date.today()

    # Check if monthly or quarterly conditions are met
    if _is_last_friday_of_quarter(today):
        logger.info("Quarterly major rebalance triggered.")
        engine = RebalancingEngine()
        summary = engine.quarterly_major_rebalance(today)
    elif _is_last_friday_of_month(today):
        logger.info("Monthly universe refresh triggered.")
        engine = RebalancingEngine()
        summary = engine.monthly_universe_refresh(today)
    else:
        engine = RebalancingEngine()
        summary = engine.weekly_ranking_refresh(today)

    reporter = UniverseReporter(cfg)
    # Pass audit to reporter if quarterly run produced one
    audit = summary.pop("audit", None)
    reporter.generate_and_deliver(summary, today, audit=audit)
    logger.info("Weekly refresh complete: %s", summary)


def cmd_monthly(args):
    from db.repository import init_db
    from db.universe_repo import init_universe_db
    from universe.rebalancer import RebalancingEngine
    from universe.reporter import UniverseReporter
    import yaml

    if not _is_last_friday_of_month(date.today()):
        logger.info("Not last Friday of month — skipping monthly refresh.")
        return

    init_db()
    init_universe_db()
    with open("config/universe_config.yaml") as f:
        cfg = yaml.safe_load(f)

    engine = RebalancingEngine()
    summary = engine.monthly_universe_refresh(date.today())
    reporter = UniverseReporter(cfg)
    reporter.generate_and_deliver(summary)


def cmd_quarterly(args):
    from db.repository import init_db
    from db.universe_repo import init_universe_db
    from universe.rebalancer import RebalancingEngine
    from universe.reporter import UniverseReporter
    import yaml

    if not _is_last_friday_of_quarter(date.today()):
        logger.info("Not last Friday of quarter — skipping quarterly rebalance.")
        return

    init_db()
    init_universe_db()
    with open("config/universe_config.yaml") as f:
        cfg = yaml.safe_load(f)

    engine = RebalancingEngine()
    summary = engine.quarterly_major_rebalance(date.today())
    reporter = UniverseReporter(cfg)
    reporter.generate_and_deliver(summary)


def cmd_audit(args):
    """Run quarterly audit standalone — can be run any time for a health check."""
    from db.repository import init_db
    from db.universe_repo import init_universe_db
    from universe.audit import UniverseAuditEngine
    from universe.reporter import UniverseReporter
    import yaml

    init_db()
    init_universe_db()
    with open("config/universe_config.yaml") as f:
        cfg = yaml.safe_load(f)

    today = date.today()
    auditor  = UniverseAuditEngine(cfg)
    reporter = UniverseReporter(cfg)

    audit = auditor.run_quarterly_audit(today)
    audit_text = reporter.generate_audit(audit, today)
    path = reporter.save_audit(audit, audit_text, today)

    print(audit_text)
    print(f"\n[Audit] Saved to: {path}")


def main():
    parser = argparse.ArgumentParser(description="Universe management scheduler")
    parser.add_argument(
        "--mode",
        choices=["daily", "weekly", "monthly", "quarterly", "seed", "status", "audit"],
        required=True,
        help="Which rebalancing mode to run",
    )
    args = parser.parse_args()

    dispatch = {
        "seed":      cmd_seed,
        "status":    cmd_status,
        "daily":     cmd_daily,
        "weekly":    cmd_weekly,
        "monthly":   cmd_monthly,
        "quarterly": cmd_quarterly,
        "audit":     cmd_audit,
    }
    dispatch[args.mode](args)


if __name__ == "__main__":
    main()
