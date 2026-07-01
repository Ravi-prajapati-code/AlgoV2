"""
Record a historical fund injection into portfolio_snapshots.

Use this to retroactively mark a date when you added external capital
in paper mode (where the runner couldn't auto-detect it at the time).

Usage
-----
  python scripts/inject_capital.py --date 2026-05-15 --amount 7500
  python scripts/inject_capital.py --date 2026-05-15 --amount 7500 --dry-run
"""

import argparse
import sys
import os
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from db.repository import get_connection


def get_snapshot(conn, date_str: str):
    return conn.execute(
        "SELECT date, total_value, capital_injected FROM portfolio_snapshots WHERE date = ?",
        (date_str,),
    ).fetchone()


def main():
    parser = argparse.ArgumentParser(description="Record a fund injection on a past date")
    parser.add_argument("--date",    required=True, help="Date of injection YYYY-MM-DD")
    parser.add_argument("--amount",  required=True, type=float, help="Amount injected in ₹")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change, don't write")
    args = parser.parse_args()

    try:
        date_str = datetime.strptime(args.date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        print(f"ERROR: invalid date '{args.date}' — use YYYY-MM-DD")
        sys.exit(1)

    if args.amount <= 0:
        print(f"ERROR: amount must be positive, got {args.amount}")
        sys.exit(1)

    conn = get_connection()
    row = get_snapshot(conn, date_str)

    if not row:
        print(f"ERROR: no snapshot found for {date_str}")
        print("Available dates:")
        rows = conn.execute(
            "SELECT date FROM portfolio_snapshots ORDER BY date DESC LIMIT 10"
        ).fetchall()
        for r in rows:
            print(f"  {r['date']}")
        conn.close()
        sys.exit(1)

    existing = float(row["capital_injected"] or 0)
    new_total = existing + args.amount

    print(f"\nSnapshot on {date_str}:")
    print(f"  total_value:      ₹{row['total_value']:,.2f}")
    print(f"  capital_injected: ₹{existing:,.2f}  →  ₹{new_total:,.2f}  (+₹{args.amount:,.2f})")

    if args.dry_run:
        print("\n[dry-run] No changes written.")
        conn.close()
        return

    conn.execute(
        "UPDATE portfolio_snapshots SET capital_injected = ? WHERE date = ?",
        (new_total, date_str),
    )
    conn.commit()
    conn.close()

    print(f"\nDone. capital_injected for {date_str} updated to ₹{new_total:,.2f}")
    print("cumulative_pnl will correct itself on next runner execution.")
    print("\nTo verify:")
    print(f"  python scripts/inject_capital.py --date {date_str} --amount 0 --dry-run")


if __name__ == "__main__":
    main()
