"""
Pre-trade integrity gate (docs/60 M3, plan optimized-humming-crayon).

Fail-closed BUY gate consulted by portfolio/manager.py and
momentum_atr/execution.py before either strategy opens a new position.
Read-only against db/reporting.db (via db/reporting_repo.py) -- never
touches trading.db/momentum_atr.db, never places or blocks a SELL.

Blocks on the same tier reconciliation/classifier.py already marks
CRITICAL/never-auto-repaired (DUPLICATE_OWNERSHIP, OWNERSHIP_CONFLICT,
QUANTITY_MISMATCH, UNKNOWN_POSITION, RECONCILIATION_ERROR), or on stale
reconciliation data (scripts/observability_snapshot.py's cron either hasn't
run recently enough, or its most recent attempt failed to get a broker read
at all -- see the early-exit in that script's main() when broker_snap is
None: no new strategy_position_snapshot row is written that cycle, so a
failed broker read shows up here purely as an aging existing row, not a
distinct classification value).

Bootstrap exception: if reporting.db has never been initialized on this box
(strategy_registry has zero rows), default to allow with a loud WARNING --
the one deliberate exception to fail-closed, scoped to first-deploy only.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Tuple

from config.settings import (
    PRE_TRADE_INTEGRITY_GATE_ENABLED,
    RECONCILIATION_STALENESS_TRADING_SESSIONS,
)
from db import reporting_repo as rrepo
from reconciliation.classifier import BLOCKING_CLASSIFICATIONS

logger = logging.getLogger("reconciliation.gate")


def _parse_snapshot_date(ts: str) -> date:
    """strategy_position_snapshot.ts is either a full ISO datetime
    (scripts/observability_snapshot.py::_now_iso()) or a bare ISO date --
    handle both without guessing a format."""
    try:
        return datetime.fromisoformat(ts).date()
    except ValueError:
        return date.fromisoformat(ts[:10])


def _trading_sessions_since(snapshot_date: date, today: date) -> int:
    """Count weekdays strictly after snapshot_date up to and including
    today. Weekday-only, not a real NSE holiday calendar -- no known
    incident has required that precision yet (M3 scope), and undercounting
    a holiday as a "session" only ever makes the gate MORE conservative
    (blocks slightly sooner on a week with a midweek holiday), never less."""
    if today <= snapshot_date:
        return 0
    sessions = 0
    d = snapshot_date + timedelta(days=1)
    while d <= today:
        if d.weekday() < 5:  # Mon-Fri
            sessions += 1
        d += timedelta(days=1)
    return sessions


def pre_trade_check(symbol: str, strategy_id: str) -> Tuple[bool, str]:
    """Returns (allowed, reason). Never raises -- any internal failure here
    must fail closed for the caller to interpret, not crash the BUY loop."""
    if not PRE_TRADE_INTEGRITY_GATE_ENABLED:
        return True, "pre-trade integrity gate disabled (PRE_TRADE_INTEGRITY_GATE_ENABLED=False)"

    try:
        registry = rrepo.load_strategy_registry()
    except Exception as e:
        logger.error("pre_trade_check(%s, %s): reporting.db registry read failed: %s", symbol, strategy_id, e)
        return False, f"reconciliation gate error reading strategy_registry: {e}"

    if not registry:
        logger.warning(
            "pre_trade_check(%s, %s): reporting.db strategy_registry is empty -- "
            "observability_snapshot.py has never run on this box. Bootstrap "
            "exception: allowing (fail-closed does not apply until reconciliation "
            "has run at least once).",
            symbol, strategy_id,
        )
        return True, "bootstrap: reporting.db not yet initialized (allowed by design)"

    try:
        row = rrepo.load_latest_position_classification(symbol)
    except Exception as e:
        logger.error("pre_trade_check(%s, %s): classification read failed: %s", symbol, strategy_id, e)
        return False, f"reconciliation gate error reading classification: {e}"

    if row is None:
        return True, "no reconciliation history for symbol (genuinely new, not stale)"

    classification = row.get("classification")
    if classification in {c.value for c in BLOCKING_CLASSIFICATIONS}:
        return False, f"blocked: latest reconciliation classification is {classification}"

    try:
        snapshot_date = _parse_snapshot_date(row["ts"])
    except Exception as e:
        logger.error("pre_trade_check(%s, %s): could not parse snapshot ts %r: %s", symbol, strategy_id, row.get("ts"), e)
        return False, f"reconciliation gate error parsing snapshot timestamp: {e}"

    gap = _trading_sessions_since(snapshot_date, date.today())
    if gap > RECONCILIATION_STALENESS_TRADING_SESSIONS:
        return False, (
            f"blocked: latest reconciliation data for {symbol} is {gap} trading "
            f"sessions old (limit {RECONCILIATION_STALENESS_TRADING_SESSIONS}) -- "
            "cron may not be running or broker reads have been failing"
        )

    return True, f"allowed: latest classification {classification}, {gap} session(s) old"
