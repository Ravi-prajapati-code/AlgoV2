"""
Drawdown risk controls for the momentum x ATR live strategy.

1. Exposure scaling (MOMENTUM_ATR_DD_SCALE_ENABLED, default on): once
   strategy drawdown from peak passes MOMENTUM_ATR_DD_SCALE_START_PCT (15%),
   the allocation cap shrinks by 3x the excess, floored at
   MOMENTUM_ATR_DD_SCALE_MIN_FACTOR (25%). Only new buys are sized smaller;
   nothing is sold because of it.
2. Kill switch (MOMENTUM_ATR_DD_KILL_PCT, default 0 = OFF): if set > 0, new
   BUYs are blocked at that drawdown; SELLs keep running. Re-evaluated every
   run. Because a blocked strategy drifts into cash and cash cannot recover
   a drawdown, a tripped switch also resets itself (peak rebased to current
   cash) once the book has been fully flat for
   MOMENTUM_ATR_KILL_COOLDOWN_DAYS -- in the 2022-2026 replay a plain 35%
   switch tripped in 2024 and blocked every buy for two years.
Own named constants, deliberately not DRAWDOWN_KILL_SWITCH_PCT (drifting
env/yaml values for that one).
"""
from datetime import date
from typing import Dict, List

from config.settings import (
    MOMENTUM_ATR_DD_KILL_PCT, MOMENTUM_ATR_KILL_COOLDOWN_DAYS,
    MOMENTUM_ATR_DD_SCALE_ENABLED, MOMENTUM_ATR_DD_SCALE_START_PCT, MOMENTUM_ATR_DD_SCALE_MIN_FACTOR,
)
from db import momentum_atr_repo as repo
from momentum_atr.models import Position
from notifications.telegram import send_message


def compute_equity(cash: float, positions: List[Position], prices: Dict[str, float]) -> float:
    """Falls back to entry_price for any symbol missing a live price --
    matches engine.py's close_by_date.get(day, entry_price) fallback."""
    invested = sum(p.shares * prices.get(p.symbol, p.entry_price) for p in positions)
    return cash + invested


def compute_drawdown_scale(cash: float, positions: List[Position], prices: Dict[str, float]) -> float:
    """Multiplier in [MOMENTUM_ATR_DD_SCALE_MIN_FACTOR, 1.0] for the allocation cap."""
    if not MOMENTUM_ATR_DD_SCALE_ENABLED:
        return 1.0
    equity = compute_equity(cash, positions, prices)
    peak = max(repo.get_state().peak_equity, equity)
    if peak <= 0:
        return 1.0
    dd = (peak - equity) / peak
    if dd <= MOMENTUM_ATR_DD_SCALE_START_PCT:
        return 1.0
    return max(MOMENTUM_ATR_DD_SCALE_MIN_FACTOR, 1.0 - (dd - MOMENTUM_ATR_DD_SCALE_START_PCT) * 3.0)


def check_kill_switch(today: date, cash: float, positions: List[Position],
                       prices: Dict[str, float], persist: bool = True) -> bool:
    """Update peak_equity, evaluate drawdown, alert on trip / still-halted /
    reset. Returns True if new BUYs should be blocked this run. persist=False
    (dry run) evaluates only and changes nothing."""
    state = repo.get_state()
    equity = compute_equity(cash, positions, prices)
    # NOTE: peak_equity is a persisted running max, not derived from
    # portfolio_snapshots -- a manual ownership correction to momentum_atr's
    # own ledger has no docs/64 value_change_reason marking here and would
    # permanently poison this peak the same way it did MAIN's. No known
    # incident yet; out of scope for this fix (plan optimized-humming-crayon M2).
    peak = max(state.peak_equity, equity)
    drawdown = (peak - equity) / peak if peak > 0 else 0.0
    was_tripped = state.kill_switch_tripped

    if (was_tripped and not positions and state.kill_switch_tripped_date is not None
            and (today - state.kill_switch_tripped_date).days >= MOMENTUM_ATR_KILL_COOLDOWN_DAYS
            and equity > 0):
        if persist:
            repo.update_state(peak_equity=equity, kill_switch_tripped=False, clear_tripped_date=True)
            send_message(
                "✅ <b>momentum_atr kill-switch RESET</b>\n"
                f"Fully in cash for {(today - state.kill_switch_tripped_date).days} days since the trip; "
                f"peak rebased to Rs.{equity:,.0f}, buying resumes."
            )
        return False

    tripped = MOMENTUM_ATR_DD_KILL_PCT > 0 and drawdown >= MOMENTUM_ATR_DD_KILL_PCT
    if not persist:
        return tripped

    repo.update_state(
        peak_equity=peak,
        kill_switch_tripped=tripped,
        kill_switch_tripped_date=today if (tripped and not was_tripped) else state.kill_switch_tripped_date,
        clear_tripped_date=(was_tripped and not tripped),
    )

    if tripped and not was_tripped:
        send_message(
            "⚠️ <b>momentum_atr kill-switch TRIPPED</b>\n"
            f"Drawdown {drawdown * 100:.1f}% from peak Rs.{peak:,.0f} (equity Rs.{equity:,.0f}).\n"
            f"New BUYs blocked. Existing positions still managed. Resets after "
            f"{MOMENTUM_ATR_KILL_COOLDOWN_DAYS} days fully in cash, or when drawdown recovers."
        )
    elif tripped and was_tripped:
        send_message(
            f"momentum_atr kill-switch still halted ({drawdown * 100:.1f}% drawdown, "
            f"equity Rs.{equity:,.0f})."
        )

    return tripped
