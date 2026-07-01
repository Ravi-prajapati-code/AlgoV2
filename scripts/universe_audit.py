#!/usr/bin/env python3
"""Audit every stock in the universe against backtest performance and quality criteria."""

import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_audit():
    from config.watchlist_nse import ALL_SYMBOLS, SYMBOL_TO_SECTOR, SYMBOL_TO_NAME

    stats = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0, "hold": 0, "sector": ""})
    trades_path = "outputs/backtest_trades.csv"
    if not os.path.exists(trades_path):
        print("ERROR: outputs/backtest_trades.csv not found. Run backtest first.")
        sys.exit(1)

    with open(trades_path) as f:
        for row in csv.DictReader(f):
            s = row["symbol"]
            pnl = float(row["net_pnl"])
            stats[s]["pnl"]    += pnl
            stats[s]["trades"] += 1
            stats[s]["hold"]   += int(row["hold_days"])
            stats[s]["sector"]  = row["sector"]
            if pnl > 0:
                stats[s]["wins"] += 1

    traded      = set(stats.keys())
    never_traded = [s for s in ALL_SYMBOLS if s not in traded]

    # ── Never traded ──────────────────────────────────────────────────────────
    print("\n=== NEVER TRADED (0 trades in 4.5 years — pure RS anchors, no P&L contribution) ===")
    for s in sorted(never_traded):
        print(f"  {s:<22} {SYMBOL_TO_SECTOR.get(s, '?'):<35} {SYMBOL_TO_NAME.get(s, '')}")

    # ── Confirmed losers ──────────────────────────────────────────────────────
    print("\n=== CONFIRMED LOSERS (2+ trades, 0% win rate) ===")
    losers = [(sym, s) for sym, s in stats.items()
              if s["trades"] >= 2 and s["wins"] == 0]
    for sym, s in sorted(losers, key=lambda x: x[1]["pnl"]):
        print(f"  {sym:<22} P&L: {s['pnl']:>+9,.0f}  Trades: {s['trades']}  WR: 0%  {s['sector']}")

    # ── Weak performers ───────────────────────────────────────────────────────
    print("\n=== WEAK (2+ trades, WR < 30%, negative total P&L) ===")
    weak = [(sym, s) for sym, s in stats.items()
            if s["trades"] >= 2
            and (s["wins"] / s["trades"]) < 0.30
            and s["pnl"] < 0
            and s["wins"] > 0]
    for sym, s in sorted(weak, key=lambda x: x[1]["pnl"]):
        wr = s["wins"] / s["trades"] * 100
        print(f"  {sym:<22} P&L: {s['pnl']:>+9,.0f}  Trades: {s['trades']}  WR: {wr:.0f}%  {s['sector']}")

    # ── Full table ─────────────────────────────────────────────────────────────
    print("\n=== FULL STOCK AUDIT ===")
    print(f"  {'Symbol':<22} {'P&L':>9}  {'Trades':>6}  {'WR%':>5}  {'AvgHold':>7}  Sector")
    print("-" * 90)
    for sym, s in sorted(stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
        wr       = s["wins"] / s["trades"] * 100 if s["trades"] else 0
        avg_hold = s["hold"] / s["trades"] if s["trades"] else 0
        flag     = " ⚠" if (s["trades"] >= 2 and wr < 30 and s["pnl"] < 0) else ""
        print(f"  {sym:<22} {s['pnl']:>+9,.0f}  {s['trades']:>6}  {wr:>5.0f}%  {avg_hold:>6.1f}d  {s['sector']}{flag}")

    # ── Summary ───────────────────────────────────────────────────────────────
    total = len(ALL_SYMBOLS)
    n_traded = len(traded)
    n_never  = len(never_traded)
    n_losers = len(losers)
    n_weak   = len(weak)
    print(f"\n  Universe : {total} stocks")
    print(f"  Traded   : {n_traded} ({n_traded/total*100:.0f}%)")
    print(f"  Never    : {n_never}  — consider replacing with active momentum stocks")
    print(f"  Losers   : {n_losers}  — 0% WR with 2+ trades, should be removed")
    print(f"  Weak     : {n_weak}  — negative P&L, WR < 30%, review")


if __name__ == "__main__":
    run_audit()
