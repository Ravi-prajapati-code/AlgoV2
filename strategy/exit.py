import os
from typing import Tuple
from db.models import Position

# Hard stop-loss: tried 2026-07-21 as a pure signal exit (checked once daily
# against the close, in check_exit_conditions) -- NOT a broker-side GTT/resting
# stop order, so it doesn't reintroduce the GTT-desync risk that got the old
# hard stop removed (see live_incident_20260701_gtt_cancel,
# live_bug_trailing_stop_not_persisted). REJECTED via robustness_gate at 15%,
# see docs/24_Rejected_Forever.md -- TRAIN CAGR +27.47%->+17.63%, FULL
# +22.77%->+11.45%, new train/test instability (Sharpe gap widens). TEST window
# and all 4 stress scenarios were byte-identical baseline vs candidate (stop
# never fired there) -- the damage is concentrated entirely in TRAIN, where
# positions that breach -15% intraday apparently recover before MOMENTUM_DECAY
# would have exited them; the hard stop locks in losses the existing
# signal-based exit would have ridden out. Off by default; kept in case a wider
# threshold is worth testing later.
HARD_STOP_LOSS_ENABLED = os.getenv("HARD_STOP_LOSS_ENABLED", "false").lower() in ("true", "1", "yes")
HARD_STOP_LOSS_PCT     = float(os.getenv("HARD_STOP_LOSS_PCT", "15")) / 100

def initial_stops(price: float, atr: float = 0) -> dict:
    """
    Trailing-stop and profit-ceiling are not used for exits. Hard stop-loss
    (see HARD_STOP_LOSS_ENABLED) is off by default -- REJECTED, docs/24.
    """
    stop_loss = price * (1 - HARD_STOP_LOSS_PCT) if HARD_STOP_LOSS_ENABLED else 0.0
    return {
        "stop_loss":     stop_loss,
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

    # Hard stop-loss — tail protection, checked ahead of everything else.
    # EOD-only (no resting broker order): a position can still gap past this
    # intraday and only exit at the next close that confirms the breach.
    if HARD_STOP_LOSS_ENABLED and pos.entry_price > 0:
        loss = (pos.entry_price - current_price) / pos.entry_price
        if loss >= HARD_STOP_LOSS_PCT:
            return True, f"HARD_STOP_LOSS ({loss:.1%} below entry)"

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
