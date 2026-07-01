"""
Dynamic Position Sizer — Zero Cash Allocation.
Allocates 100% of available cash into signals or winners.
"""

import math
import logging
from config.settings import MAX_RISK_PER_TRADE_PCT, MAX_STOCK_ALLOCATION_PCT, SIZER_CASH_BUFFER_PCT

logger = logging.getLogger(__name__)

def calculate_shares_for_value(target_value: float, price: float) -> int:
    """Simple share calculation based on a specific ₹ amount."""
    if price <= 0 or target_value <= 0:
        return 0
    return math.floor(target_value / price)

def calculate_shares(
    portfolio_value: float,
    entry_price: float,
    stop_loss_price: float,
    available_cash: float,
    **kwargs
) -> int:
    """
    ATR Risk Sizer — sizes by ₹-risk per trade, not equal weight.

    shares = (portfolio × risk_pct%) / stop_distance
    stop_distance = atr_mult × ATR when ATR available (preferred),
                    else entry_price − stop_loss_price (hard stop fallback).
    Result capped by allocation % cap and available cash.
    """
    if entry_price <= 0:
        return 0

    risk_pct  = kwargs.get("risk_pct",  MAX_RISK_PER_TRADE_PCT)
    alloc_pct = kwargs.get("alloc_pct", MAX_STOCK_ALLOCATION_PCT)
    atr       = float(kwargs.get("atr",      0) or 0)
    atr_mult  = float(kwargs.get("atr_mult", 0) or 0)

    # 1. Size by Risk — ATR stop distance preferred over hard stop
    risk_amount = portfolio_value * (risk_pct / 100.0)
    if atr > 0 and atr_mult > 0:
        risk_per_share = atr_mult * atr           # ₹ distance to ATR trailing stop
    else:
        risk_per_share = entry_price - stop_loss_price  # fallback: hard stop

    if risk_per_share > 0:
        shares_by_risk = math.floor(risk_amount / risk_per_share)
    else:
        shares_by_risk = 0

    # 2. Size by Allocation Cap
    max_stock_value = portfolio_value * alloc_pct
    shares_by_allocation = math.floor(max_stock_value / entry_price)

    # 3. Size by Cash
    useable_cash = available_cash * (1.0 - SIZER_CASH_BUFFER_PCT)
    shares_by_cash = math.floor(useable_cash / entry_price)

    # Final result is the minimum of all constraints
    final_shares = max(0, min(shares_by_risk, shares_by_allocation, shares_by_cash))
    
    logger.info(
        f"[Sizer] Portfolio: {portfolio_value:.0f} | Risk Amt: {risk_amount:.0f} | "
        f"ByRisk: {shares_by_risk} | ByAlloc: {shares_by_allocation} | "
        f"ByCash: {shares_by_cash} | Final: {final_shares}"
    )

    return final_shares

def position_value(shares: int, price: float) -> float:
    return round(shares * price, 2)
