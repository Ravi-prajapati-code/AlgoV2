"""
Drawdown kill-switch for the momentum x ATR live strategy. Own named
constant (MOMENTUM_ATR_DD_KILL_PCT), deliberately not
DRAWDOWN_KILL_SWITCH_PCT -- this codebase has drifting env/yaml values for
that one (see plan velvet-cooking-minsky). Blocks new BUYs only; SELLs
(including swap-loser sells and rank-exits) keep running. Recovery is
manual-only by design: no auto-clear when drawdown recovers below
threshold, a deliberately conservative default for a first live run of an
unproven strategy.
"""
from datetime import date
from typing import Dict, List

from config.settings import MOMENTUM_ATR_DD_KILL_PCT
from db import momentum_atr_repo as repo
from momentum_atr.models import Position
from notifications.telegram import send_message


def compute_equity(cash: float, positions: List[Position], prices: Dict[str, float]) -> float:
    """Falls back to entry_price for any symbol missing a live price --
    matches engine.py's close_by_date.get(day, entry_price) fallback."""
    invested = sum(p.shares * prices.get(p.symbol, p.entry_price) for p in positions)
    return cash + invested


def check_kill_switch(today: date, cash: float, positions: List[Position],
                       prices: Dict[str, float]) -> bool:
    """Update peak_equity, evaluate drawdown, alert on trip / still-halted.
    Returns True if new BUYs should be blocked this run."""
    state = repo.get_state()
    equity = compute_equity(cash, positions, prices)
    peak = max(state.peak_equity, equity)
    drawdown = (peak - equity) / peak if peak > 0 else 0.0
    tripped = drawdown >= MOMENTUM_ATR_DD_KILL_PCT
    was_tripped = state.kill_switch_tripped

    repo.update_state(
        peak_equity=peak,
        kill_switch_tripped=tripped,
        kill_switch_tripped_date=today if (tripped and not was_tripped) else state.kill_switch_tripped_date,
    )

    if tripped and not was_tripped:
        send_message(
            "⚠️ <b>momentum_atr kill-switch TRIPPED</b>\n"
            f"Drawdown {drawdown * 100:.1f}% from peak Rs.{peak:,.0f} (equity Rs.{equity:,.0f}).\n"
            "New BUYs blocked. Existing positions still managed. Recovery is manual-only."
        )
    elif tripped and was_tripped:
        send_message(
            f"momentum_atr kill-switch still halted ({drawdown * 100:.1f}% drawdown, "
            f"equity Rs.{equity:,.0f})."
        )

    return tripped
