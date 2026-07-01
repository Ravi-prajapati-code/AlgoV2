import os
from typing import Tuple, Dict
from db.models import Position
from config.settings import (
    STOP_LOSS_PCT, TAKE_PROFIT_PCT, TRAILING_STOP_PCT,
    TRAIL_TIGHTEN_THRESHOLD, TRAIL_TIGHTEN_PCT,
    ATR_TRAIL_MULT_INITIAL, ATR_TRAIL_MULT_TIGHT,
    round_to_tick,
)

def initial_stops(price: float, atr: float = 0) -> dict:
    """
    Hard stop at STOP_LOSS_PCT below entry.
    Take profit is an emergency ceiling only (set high — strategy relies on trailing stop).
    Initial trailing stop: ATR-based if atr provided, else fixed %.
    ATR trail is floored at the hard stop so it is never set BELOW the hard stop.
    """
    hard_stop = round_to_tick(price * (1 - STOP_LOSS_PCT))
    if atr > 0:
        trail = max(round_to_tick(price - (ATR_TRAIL_MULT_INITIAL * atr)), hard_stop)
    else:
        trail = round_to_tick(price * (1 - TRAILING_STOP_PCT))
    return {
        "stop_loss":     hard_stop,
        "take_profit":   round_to_tick(price * (1 + TAKE_PROFIT_PCT)),
        "trailing_stop": trail,
        "peak_price":    price,
    }

def update_trailing_stop(pos: Position, current_price: float, atr: float = 0) -> Position:
    """
    Ratchet up trailing stop as price rises.
    Uses ATR-based distance when atr is provided (preferred).
    Tightens the multiplier/% once profit exceeds TRAIL_TIGHTEN_THRESHOLD.
    """
    if current_price > pos.peak_price:
        pos.peak_price = current_price
        profit_pct = ((current_price / pos.entry_price) - 1) if pos.entry_price > 0 else 0.0
        tightened = profit_pct >= TRAIL_TIGHTEN_THRESHOLD

        if atr > 0:
            mult = ATR_TRAIL_MULT_TIGHT if tightened else ATR_TRAIL_MULT_INITIAL
            new_trail = current_price - (mult * atr)
        else:
            trail_pct = TRAIL_TIGHTEN_PCT if tightened else TRAILING_STOP_PCT
            new_trail = current_price * (1 - trail_pct)

        if new_trail > pos.trailing_stop:
            pos.trailing_stop = round_to_tick(new_trail)

    return pos

MIN_PROFIT_FOR_SOFT_EXIT = float(os.getenv("MIN_PROFIT_SOFT", "0.25"))
LAGGARD_RS_THRESHOLD     = float(os.getenv("LAGGARD_RS",     "50"))
MOMENTUM_RSI_THRESHOLD   = float(os.getenv("MOMENTUM_RSI",   "50"))

def check_exit_conditions(pos: Position, current_price: float, rs_rank: float = 100, indicators: dict = None) -> Tuple[bool, str]:
    # 1. Hard Stop Loss — always applies
    if current_price < pos.stop_loss:
        return True, "STOP_LOSS"

    # 2. Take Profit — always applies
    if pos.take_profit > 0 and current_price >= pos.take_profit:
        return True, "PROFIT_TARGET"

    # 3. Trailing Stop Loss — always applies
    if pos.trailing_stop > 0 and current_price < pos.trailing_stop:
        return True, "TRAIL_EXIT"

    # Soft exits only fire once position has reached MIN_PROFIT_FOR_SOFT_EXIT
    profit_pct = ((current_price / pos.entry_price) - 1) if pos.entry_price > 0 else 0.0
    if profit_pct < MIN_PROFIT_FOR_SOFT_EXIT:
        return False, ""

    # 4. Laggard Exit (Momentum Loss) — soft
    if rs_rank < LAGGARD_RS_THRESHOLD:
        return True, f"LAGGARD_EXIT (RS Rank < {LAGGARD_RS_THRESHOLD:.0f})"

    # 5. Momentum Decay (RSI) — soft
    if indicators and indicators.get("rsi", 100) < MOMENTUM_RSI_THRESHOLD:
        return True, f"MOMENTUM_DECAY (RSI < {MOMENTUM_RSI_THRESHOLD:.0f})"

    return False, ""
