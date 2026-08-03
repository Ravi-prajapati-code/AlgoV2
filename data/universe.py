"""
Symbol universe and sector metadata.

Priority:
  1. Dynamic universe DB (universe_active table) — when populated by universe manager
  2. Static watchlist (config/watchlist_nse.py) — fallback, always available
"""
import logging
import os
from datetime import date as _date
from typing import List, Dict

from config.watchlist_nse import WATCHLIST, SYMBOL_TO_SECTOR, SYMBOL_TO_NAME, ALL_SYMBOLS, ALL_SECTORS

logger = logging.getLogger(__name__)


class UniverseHistoryUnavailable(Exception):
    """
    Raised by get_all_symbols_as_of() when `as_of` predates the earliest dated
    static-watchlist snapshot (see db.universe_repo.sync_static_universe_snapshot).

    config/watchlist_nse.py's ALL_SYMBOLS has been revised multiple times
    (see its docstring) with no dated, machine-readable record of when each
    change happened — and git holds only a single squashed commit for the
    file, so history can't be reconstructed from git either. Silently
    substituting today's list for an unknown historical date reproduces the
    look-ahead/survivorship bias documented in
    docs/13_Independent_Institutional_Review.md (§2, §4.3, §10) — so this is
    raised instead of guessed at.
    """

    def __init__(self, as_of, tracking_start):
        self.as_of = as_of
        self.tracking_start = tracking_start
        super().__init__(
            f"Point-in-time universe membership unknown for {as_of}: static-watchlist "
            f"tracking begins {tracking_start or '(never seeded)'}. Backtests before that "
            "date cannot be verified free of look-ahead bias."
        )


def _get_dynamic_symbols() -> List[str]:
    """Try to read CORE symbols from DB. Returns [] on any error."""
    try:
        from db.universe_repo import get_active_symbols, active_universe_count
        count = active_universe_count()
        if count > 0:
            return get_active_symbols()
    except Exception:
        pass
    return []


def get_all_symbols() -> List[str]:
    """
    Return static watchlist (119 stocks) PLUS any dynamic universe symbols
    not already in the static list. Static list is always the floor —
    dynamic universe adds new stocks discovered by the scanner.

    UNIVERSE_OVERRIDE_FILE env var (unset by default) replaces the static
    list wholesale with a newline-delimited symbol file — used for the
    out-of-domain validation run (docs/39) so PURE_RS can be tested on
    symbols that were never part of the tuning universe, without touching
    the tuning universe itself.
    """
    override_file = os.getenv("UNIVERSE_OVERRIDE_FILE")
    if override_file:
        with open(override_file) as f:
            return [line.strip() for line in f if line.strip()]

    dynamic = _get_dynamic_symbols()
    if dynamic:
        static_set = set(ALL_SYMBOLS)
        extras = [s for s in dynamic if s not in static_set]
        if extras:
            logger.debug("[Universe] Dynamic universe adds %d new symbols beyond static list.", len(extras))
        return ALL_SYMBOLS + extras
    return ALL_SYMBOLS


def get_all_symbols_as_of(as_of: _date) -> List[str]:
    """
    Point-in-time-correct symbol list for backtesting a specific historical
    date, as opposed to get_all_symbols() which always reflects TODAY's
    static+CORE list — correct for live trading, but a look-ahead/
    survivorship bias source when used (as the backtest previously did
    unconditionally) to evaluate historical dates that predate the static
    list's most recent revision or the CORE engine's discovery history.

    Returns static-as-of UNION core-as-of, matching get_all_symbols()'s
    static-floor-plus-dynamic-extras shape. Raises UniverseHistoryUnavailable
    if `as_of` predates the earliest dated static-watchlist snapshot rather
    than silently falling back to today's list — see that exception's
    docstring for why. CORE-layer history has no equivalent raise: a date
    before the CORE engine's first logged event legitimately has zero CORE
    members, see get_core_symbols_as_of().
    """
    from db.universe_repo import (
        get_static_symbols_as_of,
        get_static_universe_tracking_start,
        get_core_symbols_as_of,
    )
    static_symbols = get_static_symbols_as_of(as_of)
    if static_symbols is None:
        raise UniverseHistoryUnavailable(as_of, get_static_universe_tracking_start())
    core_symbols = get_core_symbols_as_of(as_of)
    if not core_symbols:
        return static_symbols
    static_set = set(static_symbols)
    extras = [s for s in core_symbols if s not in static_set]
    return static_symbols + extras


def get_sector(symbol: str) -> str:
    """Sector lookup — static map first, then DB candidate table."""
    if symbol in SYMBOL_TO_SECTOR:
        return SYMBOL_TO_SECTOR[symbol]
    try:
        from db.universe_repo import get_candidate
        cand = get_candidate(symbol)
        if cand and cand.get("sector"):
            return cand["sector"]
    except Exception:
        pass
    return "Unknown"


def get_name(symbol: str) -> str:
    if symbol in SYMBOL_TO_NAME:
        return SYMBOL_TO_NAME[symbol]
    try:
        from db.universe_repo import get_candidate
        cand = get_candidate(symbol)
        if cand and cand.get("name"):
            return cand["name"]
    except Exception:
        pass
    return symbol


def get_symbols_by_sector(sector: str) -> List[str]:
    return [sym for sym, sec, _ in WATCHLIST if sec == sector]


def get_all_sectors() -> List[str]:
    return ALL_SECTORS


def get_sector_map() -> Dict[str, str]:
    return SYMBOL_TO_SECTOR.copy()
