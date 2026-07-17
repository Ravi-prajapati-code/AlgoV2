"""
Portfolio-level risk controls (circuit breakers).
Checked once per day before processing new BUY signals.
"""

from typing import List
from db.models import Position
from config.settings import (
    MAX_NEW_TRADES_PER_DAY,
    MAX_OPEN_POSITIONS,
    DRAWDOWN_KILL_SWITCH_PCT,
)

# Max portfolio drawdown before halting new buys
MAX_PORTFOLIO_DRAWDOWN = DRAWDOWN_KILL_SWITCH_PCT


def can_open_new_trades(
    new_trades_today: int,
    open_positions: List[Position],
    portfolio_value: float,
    peak_portfolio_value: float,
    bypass_drawdown: bool = False,
) -> tuple[bool, str]:
    """
    Returns (allowed, reason).
    Checks: daily trade limit, max open positions, drawdown circuit breaker.

    bypass_drawdown: skip the drawdown check for safe-haven hedge entries
    (e.g. GOLDBEES). A hedge is meant to go on precisely when drawdown is
    high, so gating it behind the same breaker that blocks fresh momentum
    risk defeats its purpose. Daily-limit and max-positions checks still
    apply even when bypassed.
    """
    if new_trades_today >= MAX_NEW_TRADES_PER_DAY:
        return False, f"Daily trade limit reached ({MAX_NEW_TRADES_PER_DAY} trades/day)"

    if len(open_positions) >= MAX_OPEN_POSITIONS:
        return False, f"Max open positions reached ({MAX_OPEN_POSITIONS})"

    if bypass_drawdown:
        return True, "OK"

    drawdown = (peak_portfolio_value - portfolio_value) / peak_portfolio_value
    if drawdown >= MAX_PORTFOLIO_DRAWDOWN:
        return False, (
            f"Portfolio drawdown circuit breaker: {drawdown:.1%} "
            f"(limit {MAX_PORTFOLIO_DRAWDOWN:.0%})"
        )

    return True, "OK"
