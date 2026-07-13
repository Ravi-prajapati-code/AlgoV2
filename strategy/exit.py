import os
from typing import Tuple
from db.models import Position

def initial_stops(price: float, atr: float = 0) -> dict:
    """
    Stop-loss, trailing-stop, and profit-ceiling are no longer used for exits —
    positions close only on system sell signals (see check_exit_conditions).
    """
    return {
        "stop_loss":     0.0,
        "take_profit":   0.0,
        "trailing_stop": 0.0,
        "peak_price":    price,
    }

def update_trailing_stop(pos: Position, current_price: float, atr: float = 0, regime: str = None) -> Position:
    """No-op — trailing stops removed; peak_price tracked for display only."""
    if current_price > pos.peak_price:
        pos.peak_price = current_price
    return pos

MOMENTUM_RSI_THRESHOLD   = float(os.getenv("MOMENTUM_RSI",   "50"))

def check_exit_conditions(pos: Position, current_price: float, rs_rank: float = 100, indicators: dict = None) -> Tuple[bool, str]:
    # Stop-loss, trailing-stop, and profit-ceiling removed — exits fire only on the
    # system's own sell signals (momentum decay, regime/crash protection, score-drop,
    # rotation). See strategy/signals.py for the rest of the chain.
    # No profit gate — a deteriorating position exits regardless of P&L; the prior
    # gate left underwater laggards with no exit path once price-based stops were
    # removed (docs/23_Assumption_Audit.md #24).
    #
    # RS-decay exit removed (docs/23_Assumption_Audit.md #23): robustness_gate
    # bracket test (LAGGARD_RS 30/40/50/65) showed it never independently fires —
    # MOMENTUM_DECAY (RSI) always triggers first across the real backtest history —
    # and the one point where it did bind independently (65) hurt crash-recovery
    # capture with no offsetting benefit. rs_rank kept in the signature for caller
    # compatibility; unused here now.

    # Momentum Decay (RSI) — soft
    if indicators and indicators.get("rsi", 100) < MOMENTUM_RSI_THRESHOLD:
        return True, f"MOMENTUM_DECAY (RSI < {MOMENTUM_RSI_THRESHOLD:.0f})"

    return False, ""
