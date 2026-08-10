"""Shared read-only metric helpers for dashboard views. Pure functions over
values already loaded from a DB — no I/O, no strategy-specific knowledge,
safe to reuse across strategies.
"""

import math
from typing import List


def sharpe(returns: List[float]) -> float:
    if len(returns) < 5:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / len(returns)
    std = math.sqrt(var)
    return (mean / std) * math.sqrt(252) if std > 0 else 0.0


def profit_factor(trades) -> float:
    wins = sum(t.net_pnl for t in trades if t.net_pnl and t.net_pnl > 0)
    losses = sum(abs(t.net_pnl) for t in trades if t.net_pnl and t.net_pnl < 0)
    return round(wins / losses, 2) if losses > 0 else float("inf")


def max_dd(values: List[float]) -> float:
    peak = 0.0
    dd_max = 0.0
    for v in values:
        peak = max(peak, v)
        dd = (peak - v) / peak if peak > 0 else 0.0
        dd_max = max(dd_max, dd)
    return dd_max


def cagr(start_value: float, end_value: float, days: int) -> float:
    """Annualized return from a start/end equity value over `days` calendar
    days. Returns 0.0 for degenerate inputs (non-positive start, <1 day)
    rather than raising or dividing by zero."""
    if start_value <= 0 or days < 1:
        return 0.0
    years = days / 365.25
    if years <= 0:
        return 0.0
    return (end_value / start_value) ** (1 / years) - 1
