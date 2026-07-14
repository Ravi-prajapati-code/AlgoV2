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

# Profit-lock (test-only, off by default): trade_attribution 2026-07-14 found MFE
# giveback rises monotonically with holding period (3.75% at 0-5d -> 10.56% at 60d+)
# -- long winners give back the most before MOMENTUM_DECAY fires. This tightens the
# RSI decay threshold, but only once a position is already up PROFIT_LOCK_GAIN_PCT,
# so it can't clip a trade early -- still a pure signal exit, no broker-side stop
# order, so it doesn't reintroduce the GTT-desync risk that got hard stops removed.
PROFIT_LOCK_ENABLED      = os.getenv("PROFIT_LOCK_ENABLED", "false").lower() in ("true", "1", "yes")
PROFIT_LOCK_GAIN_PCT     = float(os.getenv("PROFIT_LOCK_GAIN_PCT", "15")) / 100
PROFIT_LOCK_RSI_THRESHOLD = float(os.getenv("PROFIT_LOCK_RSI", "58"))

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

    rsi = indicators.get("rsi", 100) if indicators else 100

    # Profit-lock — tighter RSI decay threshold once a winner is already up
    # PROFIT_LOCK_GAIN_PCT, to cut giveback on long-running winners specifically.
    if PROFIT_LOCK_ENABLED and pos.entry_price > 0:
        gain = (current_price - pos.entry_price) / pos.entry_price
        if gain >= PROFIT_LOCK_GAIN_PCT and rsi < PROFIT_LOCK_RSI_THRESHOLD:
            return True, f"PROFIT_LOCK (gain {gain:.1%}, RSI < {PROFIT_LOCK_RSI_THRESHOLD:.0f})"

    # Momentum Decay (RSI) — soft
    if rsi < MOMENTUM_RSI_THRESHOLD:
        return True, f"MOMENTUM_DECAY (RSI < {MOMENTUM_RSI_THRESHOLD:.0f})"

    return False, ""
