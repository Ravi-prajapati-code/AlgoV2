"""
Performance metrics calculator for backtest results.
"""

import math
from typing import Optional
from db.models import Trade
from backtest.engine import BacktestResult
from config.settings import INITIAL_CAPITAL


def calculate_monthly_returns(equity: dict, initial_capital: float) -> dict:
    """Group daily equity into monthly returns: {year: {month: return_pct}}.
    Missing months (data gaps) are filled by carrying forward the last known value,
    showing 0.0% return rather than a blank that distorts surrounding months.
    """
    if not equity:
        return {}

    dates = sorted(equity.keys())
    month_ends = {}
    for d in dates:
        month_ends[(d.year, d.month)] = equity[d]

    # Build complete month range from first to last equity date — no gaps
    first, last = dates[0], dates[-1]
    all_months = []
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        all_months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1

    returns = {}
    prev_value = initial_capital

    for year, month in all_months:
        if year not in returns:
            returns[year] = {}
        if (year, month) in month_ends:
            current_val = month_ends[(year, month)]
            returns[year][month] = (current_val / prev_value) - 1
            prev_value = current_val
        else:
            # Data gap — carry forward prev_value, show 0.0%
            returns[year][month] = 0.0

    return returns


def calculate_metrics(result: BacktestResult, initial_capital: float = INITIAL_CAPITAL) -> dict:
    trades = result.trades
    equity = result.equity_curve
    total_injected = getattr(result, "total_injected", 0.0)
    total_deployed = initial_capital + total_injected

    if not equity:
        return {
            "initial_capital":          round(initial_capital, 2),
            "total_injected":           round(total_injected, 2),
            "total_deployed":           round(total_deployed, 2),
            "final_value":              round(initial_capital, 2),
            "final_cash":               round(initial_capital, 2),
            "total_return_pct":         0.0,
            "cagr_pct":                 0.0,
            "max_drawdown_pct":         0.0,
            "sharpe_ratio":             0.0,
            "total_trades":             0,
            "win_rate_pct":             0.0,
            "avg_win_inr":              0.0,
            "avg_loss_inr":             0.0,
            "avg_hold_days":            0.0,
            "profit_factor":            0.0,
            "total_charges_inr":        0.0,
            "annual_charges_drag_pct":  0.0,
            "passes_15pct_target":      False,
            "passes_sharpe":            False,
            "passes_drawdown":          True,
            "passes_win_rate":          False,
            "passes_profit_factor":     False,
            "all_criteria_met":         False,
        }

    dates = sorted(equity.keys())
    values = [equity[d] for d in dates]
    final_value = values[-1]
    # Critical Fix: Ensure final_cash is always retrieved
    final_cash = result.cash_curve.get(dates[-1], final_value)

    total_return = (final_value - total_deployed) / total_deployed
    years = max((dates[-1] - dates[0]).days / 365.25, 1/365)
    cagr = (final_value / total_deployed) ** (1 / years) - 1

    # Peak starts at initial_capital — injections bump equity curve on their date,
    # so the rolling peak naturally captures them as portfolio grows.
    peak = initial_capital
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        dd = (peak - v) / peak
        max_dd = max(max_dd, dd)

    daily_returns = []
    for i in range(1, len(values)):
        r = (values[i] - values[i - 1]) / values[i - 1]
        daily_returns.append(r)

    if daily_returns:
        mean_r = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)
        std_r = math.sqrt(variance)
        sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    completed = [t for t in trades if t.net_pnl is not None]
    winners = [t for t in completed if (t.net_pnl or 0) > 0]
    losers  = [t for t in completed if (t.net_pnl or 0) <= 0]

    win_rate = len(winners) / len(completed) if completed else 0.0
    avg_win  = sum(t.net_pnl for t in winners) / len(winners) if winners else 0.0
    avg_loss = sum(t.net_pnl for t in losers)  / len(losers)  if losers  else 0.0
    avg_hold = sum(t.hold_days for t in completed if t.hold_days) / len(completed) if completed else 0.0
    total_charges = sum(t.charges for t in completed if t.charges)

    gross_profit = sum(t.gross_pnl for t in winners if t.gross_pnl)
    gross_loss   = abs(sum(t.gross_pnl for t in losers if t.gross_pnl))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

    monthly_returns = calculate_monthly_returns(equity, initial_capital)

    from config.settings import (
        BACKTEST_MIN_CAGR, BACKTEST_MIN_SHARPE,
        BACKTEST_MAX_DRAWDOWN, BACKTEST_MIN_WIN_RATE,
        BACKTEST_MIN_PROFIT_FACTOR
    )

    return {
        "initial_capital":   round(initial_capital, 2),
        "total_injected":    round(total_injected, 2),
        "total_deployed":    round(total_deployed, 2),
        "final_value":       round(final_value, 2),
        "final_cash":        round(final_cash, 2),
        "total_return_pct":  round(total_return * 100, 2),
        "cagr_pct":          round(cagr * 100, 2),
        "max_drawdown_pct":  round(max_dd * 100, 2),
        "sharpe_ratio":      round(sharpe, 2),
        "monthly_returns":   monthly_returns,
        "total_trades":      len(completed),
        "win_rate_pct":      round(win_rate * 100, 2),
        "avg_win_inr":       round(avg_win, 2),
        "avg_loss_inr":      round(avg_loss, 2),
        "avg_hold_days":     round(avg_hold, 1),
        "profit_factor":     round(profit_factor, 2),
        "total_charges_inr":      round(total_charges, 2),
        "annual_charges_drag_pct": round((total_charges / total_deployed * 100) / years, 2),
        "passes_15pct_target":  cagr >= BACKTEST_MIN_CAGR,
        "passes_sharpe":        sharpe >= BACKTEST_MIN_SHARPE,
        "passes_drawdown":      max_dd <= BACKTEST_MAX_DRAWDOWN,
        "passes_win_rate":      win_rate >= BACKTEST_MIN_WIN_RATE,
        "passes_profit_factor": profit_factor >= BACKTEST_MIN_PROFIT_FACTOR,
        "all_criteria_met": bool(
            cagr >= BACKTEST_MIN_CAGR and
            sharpe >= BACKTEST_MIN_SHARPE and
            max_dd <= BACKTEST_MAX_DRAWDOWN and
            win_rate >= BACKTEST_MIN_WIN_RATE and
            profit_factor >= BACKTEST_MIN_PROFIT_FACTOR
        ),
    }
