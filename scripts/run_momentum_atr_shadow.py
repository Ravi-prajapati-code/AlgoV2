"""
Daily after-close runner for the momentum_atr shadow books (docs/82).
PAPER ONLY: no broker object is created, no orders, no writes to the live
momentum_atr DB. Reads the live universe + daily bars, advances the virtual
books (momentum_atr/shadow.py) over every completed session since their
last run, and writes:

  data/shadow/momentum_atr/<BOOK>.json         book state (atomic replace)
  data/shadow/momentum_atr/trades.csv          closed trades, ledger.py format
  data/shadow/momentum_atr/equity.csv          daily equity per book

Cron (after market close, never overlapping the 08:50/09:17 live jobs):
  45 16 * * 1-5 ... flock -n /tmp/algov2_momentum_atr_shadow.lock -c "timeout 1800 python scripts/run_momentum_atr_shadow.py"

Usage: python scripts/run_momentum_atr_shadow.py [--start YYYY-MM-DD]
"""
import argparse
import csv
import logging
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from momentum_atr.shadow import Book, FORMULAS, advance, score_frames  # noqa: E402

OUT = os.path.join(ROOT, "data", "shadow", "momentum_atr")
DEFAULT_START = "2026-10-13"  # first session after the SMA200 live deploy (f5a6e8f)
log = logging.getLogger("MomentumATRShadow")


def _append_csv(path, rows, fields):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=DEFAULT_START)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s -- %(message)s")
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()

    from data.fetcher import fetch_all
    from data.universe import get_all_symbols

    books = {n: Book.load(os.path.join(OUT, f"{n}.json"), n) for n in FORMULAS}
    before = {n: (len(b.trades), len(b.equity)) for n, b in books.items()}
    symbols = sorted(set(get_all_symbols()) | {s for b in books.values() for s in b.positions})
    data = fetch_all(symbols, live_mode=True)
    log.info("fetched %d/%d symbols in %.0fs", len(data), len(symbols), time.time() - t0)
    if len(data) < 20:
        log.error("only %d symbols fetched -- not advancing the books", len(data))
        return 1

    for line in advance(books, score_frames(data), a.start):
        log.info(line)

    for n, b in books.items():
        b.save(os.path.join(OUT, f"{n}.json"))
        nt, ne = before[n]
        _append_csv(os.path.join(OUT, "trades.csv"),
                    [dict(t, source=f"shadow_{n}", exit_date=t["date"],
                          hold_days=(__import__("pandas").Timestamp(t["date"]) - __import__("pandas").Timestamp(t["entry_date"])).days,
                          exit_reason=t["reason"])
                     for t in b.trades[nt:] if t["action"] == "SELL"],
                    ["source", "symbol", "entry_date", "exit_date", "net_pnl", "ret_pct", "hold_days", "exit_reason", "shares", "price"])
        _append_csv(os.path.join(OUT, "equity.csv"),
                    [{"book": n, "date": e["date"], "equity": e["equity"], "cash": e["cash"],
                      "positions": ";".join(f"{s}:{q}" for s, q in e["positions"].items())} for e in b.equity[ne:]],
                    ["book", "date", "equity", "cash", "positions"])
        log.info("%s: last %s equity %s, %d closed trades total", n, b.last_date,
                 b.equity[-1]["equity"] if b.equity else None, sum(t["action"] == "SELL" for t in b.trades))
    log.info("shadow run complete in %.0fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
