"""
Log config/watchlist_nse.py's current ALL_SYMBOLS as a dated snapshot, so backtests can
reconstruct point-in-time universe membership instead of applying today's list to every
historical date (the confirmed look-ahead/survivorship bias in
docs/13_Independent_Institutional_Review.md §2/§4.3/§10).

Run this every time ALL_SYMBOLS changes, before committing:

    python3 scripts/sync_static_universe.py
    python3 scripts/sync_static_universe.py --reason "removed 3 governance-risk names"

The first-ever run seeds a full baseline; membership before that baseline date is permanently
unknowable (git holds only a single squashed commit for this file, and no dated record of past
revisions exists). See config/watchlist_nse.py's docstring for the full revision narrative.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.repository import init_db
from db.universe_repo import sync_static_universe_snapshot, get_static_universe_tracking_start
from config.watchlist_nse import ALL_SYMBOLS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reason", default="static watchlist sync",
                         help="Short note on what changed and why (stored with each logged event)")
    args = parser.parse_args()

    init_db()
    was_seeded = get_static_universe_tracking_start() is not None
    n = sync_static_universe_snapshot(ALL_SYMBOLS, reason=args.reason)

    if not was_seeded:
        print(f"Seeded baseline: {n} symbols logged as of today. "
              f"Point-in-time tracking begins here — backtests before today's date cannot be "
              f"verified free of look-ahead bias regardless of this fix.")
    elif n == 0:
        print("No changes since the last sync — ALL_SYMBOLS already matches the logged snapshot.")
    else:
        print(f"Logged {n} change(s) (additions + removals) with today's date.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
