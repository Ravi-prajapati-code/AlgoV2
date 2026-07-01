"""
Defensive portfolio — activated when regime = BEAR for N consecutive days.

Allocation (bear swing mode):
  50%  GOLDBEES.NS   (Gold ETF — core bear hedge)
  50%  Cash          (reserved for 1-2 active bear swing slots)

Bear swing: remaining cash is actively deployed into stocks with RS > BEAR_SWING_RS_THRESHOLD
(stocks outperforming the bear market — typically pharma, FMCG, IT, gold-adjacent).
Bear swing positions exit via normal trailing stops, NOT regime switching.

Switches:
  BULL → BEAR  : after REGIME_SWITCH_DAYS consecutive BEAR days
  BEAR → BULL  : after BULL_RECOVERY_DAYS consecutive BULL days
  While BEAR   : rebalance gold every REBAL_DAYS trading days
"""

import os
REGIME_SWITCH_DAYS      = int(os.getenv("REGIME_SWITCH_DAYS", "25"))  # consecutive BEAR days required before switching to defensive
BULL_RECOVERY_DAYS      = int(os.getenv("BULL_RECOVERY_DAYS", "3"))   # consecutive BULL days required before switching back to momentum
MIN_DEFENSIVE_HOLD_DAYS = int(os.getenv("MIN_DEFENSIVE_HOLD_DAYS", "45"))  # minimum calendar days in defensive before exit allowed
REBAL_DAYS              = 63  # ~quarterly rebalance while in defensive mode
ENTRY_CONFIRM_DAYS      = int(os.getenv("ENTRY_CONFIRM_DAYS", "0"))
                              # consecutive BULL days required before NEW momentum entries
                              # (anti-whipsaw: blocks rally-top buys during choppy bears)
DD_BRAKE_PCT            = float(os.getenv("DD_BRAKE", "0"))
                              # if >0: force defensive switch when regime=BEAR and portfolio
                              # drawdown from peak >= this (skips REGIME_SWITCH_DAYS wait)
ENTRY_CONFIRM_ADAPTIVE  = os.getenv("ENTRY_CONFIRM_ADAPTIVE", "0") == "1"
ENTRY_CONFIRM_DIVISOR   = int(os.getenv("ENTRY_CONFIRM_DIVISOR", "5"))
CASH_PARK               = os.getenv("CASH_PARK", "0") == "1"
                              # park idle momentum-mode cash in GOLDBEES during BULL;
                              # auto-liquidated whenever cash is needed for a real entry
                              # adaptive: required BULL days = min(ENTRY_CONFIRM_DAYS,
                              # preceding_bear_spell_days // DIVISOR). Long grinding bears
                              # get full confirmation wait; flash crashes re-enter fast.

MIN_GOLDBEES_HOLD_DAYS  = int(os.getenv("MIN_GOLDBEES_HOLD_DAYS", "3"))
                              # minimum trading days GOLDBEES must be held before EXIT_SAFE_HAVEN
                              # can fire — prevents 1-day whipsaw (crash protect → same-day exit)

BEAR_SWING_RS_THRESHOLD  = int(os.getenv("BEAR_SWING_RS_THRESHOLD", "60"))   # minimum RS rank; EMA50 filter is the real quality gate
BEAR_SWING_SLOTS         = int(os.getenv("BEAR_SWING_SLOTS", "2"))            # max active swing positions during bear
BEAR_SWING_COOLDOWN_DAYS = int(os.getenv("BEAR_SWING_COOLDOWN_DAYS", "0"))   # 0=disabled; set >0 to prevent re-entering same stock too soon after bear-swing exit (costs ~4pp CAGR)

QUALITY_STOCKS = []  # no longer used in defensive allocation

GOLD_ETF = "GOLDBEES.NS"
LIQUIDBEES = "LIQUIDBEES.NS"
LIQUIDBEES_ENABLED = os.getenv("LIQUIDBEES_ENABLED", "0") == "1"  # disabled — complexity > yield at current portfolio size

ALL_DEFENSIVE_SYMBOLS = [GOLD_ETF, LIQUIDBEES]


def build_target_weights() -> dict:
    """Return {symbol: target_weight} for the defensive portfolio.
    50% gold, 45% LIQUIDBEES (liquid cash park), 5% cash buffer.
    SAFE_HAVEN_ENABLED=false → empty dict (pure bear swing, no GOLDBEES).
    LIQUIDBEES_ENABLED=0 falls back to original 50% gold + 50% cash.
    """
    from config.settings import SAFE_HAVEN_ENABLED
    if not SAFE_HAVEN_ENABLED:
        return {}
    weights = {GOLD_ETF: 0.50}
    if LIQUIDBEES_ENABLED:
        weights[LIQUIDBEES] = LIQUIDBEES_TARGET_WEIGHT
    return weights


def is_defensive_symbol(symbol: str) -> bool:
    return symbol in ALL_DEFENSIVE_SYMBOLS


def get_defensive_entries(portfolio_val: float, prices: dict, slippage_pct: float = 0.001) -> list:
    """
    Build Position objects for the defensive allocation.
    Returns list of (symbol, shares, exec_price, weight).
    """
    from config.settings import round_to_tick
    from charges.calculator import buy_charges

    weights = build_target_weights()
    entries = []
    for sym, weight in weights.items():
        price = prices.get(sym)
        if not price or price <= 0:
            continue
        exec_price = round_to_tick(price * (1 + slippage_pct))
        budget = portfolio_val * weight
        cost_est = buy_charges(budget).total
        shares_budget = budget - cost_est
        shares = int(shares_budget / exec_price)
        if shares > 0:
            entries.append((sym, shares, exec_price, weight))
    return entries


def compute_rebalance(open_positions: list, portfolio_val: float, prices: dict,
                      slippage_pct: float = 0.001) -> tuple:
    """
    Compare current weights to target. Return (sells, buys) as lists of
    (symbol, shares, exec_price) tuples. Sells first, then buys.
    Only rebalances if drift > 5% from target.
    """
    from config.settings import round_to_tick

    weights = build_target_weights()
    held    = {p.symbol: p for p in open_positions if is_defensive_symbol(p.symbol)}
    sells, buys = [], []
    DRIFT_THRESHOLD = 0.05

    for sym, target_w in weights.items():
        target_val = portfolio_val * target_w
        price      = prices.get(sym)
        if not price or price <= 0:
            continue

        if sym in held:
            current_val = held[sym].shares * price
            drift = (current_val - target_val) / portfolio_val
            if drift > DRIFT_THRESHOLD:
                excess_shares = int((current_val - target_val) / price)
                if excess_shares > 0:
                    sells.append((sym, excess_shares, round_to_tick(price * (1 - slippage_pct))))
            elif drift < -DRIFT_THRESHOLD:
                gap_shares = int((target_val - current_val) / price)
                if gap_shares > 0:
                    buys.append((sym, gap_shares, round_to_tick(price * (1 + slippage_pct))))
        else:
            shares = int(target_val / (price * (1 + slippage_pct)))
            if shares > 0:
                buys.append((sym, shares, round_to_tick(price * (1 + slippage_pct))))

    return sells, buys
