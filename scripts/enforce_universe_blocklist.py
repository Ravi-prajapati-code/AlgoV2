#!/usr/bin/env python3
"""
One-off/repeatable sweep: force-remove any symbol in config/universe_removed.py's
REMOVED_SYMBOLS that is currently sitting in a non-'removed' universe_candidates status
(e.g. 'core', 'watchlist').

universe/manager.py's weekly refresh() now self-heals this automatically going forward, but
that only runs on the weekly cron cadence. This script exists to apply the fix immediately
without waiting for the next scheduled refresh -- built specifically to correct the
2026-07-06 loser-leak recurrence (LAURUSLABS.NS/THERMAX.NS back in 'core').

Safe to run anytime, including on a clean DB (no-op if nothing is leaked). Uses
UniverseManager.manual_remove(), the same code path as any other manual removal, so it goes
through the normal status-transition + lockout + event-log machinery -- no raw SQL.

Usage:
    python3 scripts/enforce_universe_blocklist.py           # apply
    python3 scripts/enforce_universe_blocklist.py --dry-run # report only, no DB writes
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from config.universe_removed import REMOVED_SYMBOLS
from db import universe_repo as repo
from universe.manager import UniverseManager


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report leaked symbols, make no DB changes")
    args = ap.parse_args()

    with open("config/universe_config.yaml") as f:
        cfg = yaml.safe_load(f)
    mgr = UniverseManager(cfg)

    leaked = []
    for cand in repo.get_all_candidates():
        sym, status = cand["symbol"], cand["status"]
        if sym in REMOVED_SYMBOLS and status != "removed":
            leaked.append((sym, status))

    if not leaked:
        print("No block-listed symbols found outside 'removed' status. Nothing to do.")
        return

    print(f"Found {len(leaked)} block-listed symbol(s) currently live in the universe:")
    for sym, status in leaked:
        print(f"  {sym:<16} status={status:<12} reason={REMOVED_SYMBOLS[sym]}")

    if args.dry_run:
        print("\n--dry-run: no changes made.")
        return

    print()
    for sym, status in leaked:
        mgr.manual_remove(sym, reason=f"blocklist: {REMOVED_SYMBOLS[sym]}")
        print(f"  removed {sym} (was {status})")

    print(f"\nDone. {len(leaked)} symbol(s) force-removed.")


if __name__ == "__main__":
    main()
