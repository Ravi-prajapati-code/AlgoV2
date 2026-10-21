#!/usr/bin/env python3
"""
Read-only audit: which universe/held symbols cannot get fresh daily bars.

For every symbol in the live universe plus open momentum_atr holdings it
reports
  - whether the LOCAL instrument cache (data/instruments/nse_instruments.json)
    has an Upstox instrument key,
  - whether Upstox's CURRENT instrument list has one (downloaded in memory,
    the local cache file is not touched),
  - the latest cached daily bar date in db/trading.db.

Added 2026-10-21 after HFCL/GUJGASLTD/HEG were found ranked on a frozen
2026-09-25 close for 4 weeks: the local instrument cache was never refreshed
after Upstox re-keyed those symbols, so the fetcher silently got nothing and
the scorer fell back to stale cached bars.

    .venv/bin/python scripts/audit_symbol_data_freshness.py
"""
import gzip
import json
import os
import sys
from datetime import date

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.instruments.mapper import InstrumentMapper  # noqa: E402
from db import repository as repo  # noqa: E402


def _upstox_current_keys() -> dict:
    resp = requests.get(InstrumentMapper.INSTRUMENTS_URL, timeout=60)
    resp.raise_for_status()
    data = json.loads(gzip.decompress(resp.content))
    return {i["trading_symbol"]: i["instrument_key"] for i in data
            if i.get("segment") == "NSE_EQ" and i.get("instrument_type") == "EQ"
            and i.get("trading_symbol") and i.get("instrument_key")}


def main() -> int:
    from data.universe import get_all_symbols
    from db import momentum_atr_repo as matr_repo

    symbols = list(dict.fromkeys(get_all_symbols() + [p.symbol for p in matr_repo.load_positions("OPEN")]))
    with open(InstrumentMapper.CACHE_FILE) as f:
        local = json.load(f)
    cache_age = date.today() - date.fromtimestamp(os.path.getmtime(InstrumentMapper.CACHE_FILE))
    current = _upstox_current_keys()

    rows = []
    for s in symbols:
        clean = s.replace(".NS", "").upper()
        latest = repo.latest_cached_date(s)
        rows.append((s, local.get(clean), current.get(clean), latest))
    dates = [r[3] for r in rows if r[3]]
    ref = max(set(dates), key=dates.count) if dates else None

    print(f"local instrument cache age: {cache_age.days} days ({len(local)} keys); "
          f"Upstox current list: {len(current)} keys")
    print(f"symbols checked: {len(rows)}; most common last bar: {ref}")
    bad = [r for r in rows if not r[1] or r[1] != r[2] or not r[3] or (ref and r[3] < ref)]
    print(f"\n{len(bad)} symbol(s) with a data problem:")
    print(f"{'symbol':18} {'local key':26} {'upstox key now':26} last bar")
    for s, lk, ck, lb in sorted(bad, key=lambda r: (r[3] or date.min)):
        print(f"{s:18} {str(lk):26} {str(ck):26} {lb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
