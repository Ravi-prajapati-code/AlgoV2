"""
Backtest reporter — generates human-readable summary and CSV logs.

Outputs:
  backtest_trades.csv    — Detailed trade log (every completed trade)
  backtest_rejected.csv  — Rejected trade log (stocks scanned but not traded)
  backtest_decisions.csv — Per-stock decision log (RS, Signal, Rank, Selected)
  backtest_daily_scan.csv— Daily scan summary (scanned/passed/selected counts)
  backtest_equity.csv    — Equity curve

Trade log fields:
  symbol, sector, entry_date, exit_date, hold_days,
  entry_price, exit_price, shares,
  position_size_inr, position_size_pct,
  stop_loss, pnl_pct,
  gross_pnl, charges, net_pnl, exit_reason
"""

import os
import csv
from datetime import date
from collections import defaultdict

from backtest.engine import BacktestResult
from backtest.metrics import calculate_metrics
from config.settings import INITIAL_CAPITAL, OUTPUTS_DIR


def print_summary(metrics: dict, result: BacktestResult = None):
    """Print a formatted backtest summary to stdout."""
    print("\n" + "=" * 60)
    print("  BACKTEST RESULTS")
    print("=" * 60)
    print(f"  Starting Capital :  ₹{metrics['initial_capital']:>12,.2f}")
    if metrics.get("total_injected", 0) > 0:
        n = len(result.fund_injections_log) if result else 0
        print(f"  Funds Injected   :  ₹{metrics['total_injected']:>12,.2f}  ({n} injection(s))")
        print(f"  Total Deployed   :  ₹{metrics['total_deployed']:>12,.2f}")

    # Total Account Value breakdown
    final_val = metrics['final_value']
    final_cash = metrics['final_cash']
    invested = round(final_val - final_cash, 2)
    
    print(f"  Final Value      :  ₹{final_val:>12,.2f}  (Cash + Portfolio + Open Positions)")
    print(f"  └─ Cash (Onhand) :  ₹{final_cash:>12,.2f}")
    if invested > 0:
        print(f"  └─ Portfolio/Open:  ₹{invested:>12,.2f}")
    else:
        print(f"  └─ Portfolio/Open:  ₹{0.0:>12.2f}  (All positions closed at end)")

    print(f"  Total Return     :  {metrics['total_return_pct']:>+.2f}%")
    print(f"  CAGR             :  {metrics['cagr_pct']:>+.2f}%  {'✅' if metrics['passes_15pct_target'] else '❌'}")
    print(f"  Sharpe Ratio     :  {metrics['sharpe_ratio']:>.2f}  {'✅' if metrics['passes_sharpe'] else '❌'}")
    print(f"  Max Drawdown     :  {metrics['max_drawdown_pct']:>.2f}%  {'✅' if metrics['passes_drawdown'] else '❌'}")
    print("-" * 60)
    print(f"  Total Trades     :  {metrics['total_trades']}")
    print(f"  Win Rate         :  {metrics['win_rate_pct']:.1f}%  {'✅' if metrics['passes_win_rate'] else '❌'}")
    print(f"  Avg Win          :  ₹{metrics['avg_win_inr']:>+,.2f}")
    print(f"  Avg Loss         :  ₹{metrics['avg_loss_inr']:>+,.2f}")
    print(f"  Profit Factor    :  {metrics['profit_factor']:.2f}  {'✅' if metrics['passes_profit_factor'] else '❌'}")
    print(f"  Avg Hold Days    :  {metrics['avg_hold_days']:.1f}")
    print(f"  Total Charges    :  ₹{metrics['total_charges_inr']:>,.2f} ({metrics['annual_charges_drag_pct']:.2f}%/yr drag)")
    
    if result and result.final_open_positions:
        print("-" * 60)
        print(f"  OPEN POSITIONS AT END ({len(result.final_open_positions)})")
        print("-" * 60)
        for pos in result.final_open_positions:
            print(f"    {pos.symbol:<12} {pos.shares:>5} qty @ ₹{pos.entry_price:,.2f}")

    if "monthly_returns" in metrics:
        print_monthly_returns(metrics["monthly_returns"])

    print("-" * 60)
    print_sector_performance(result.trades)
    print_stock_performance(result.trades)

    print("=" * 60)
    verdict = "PASS — Ready for paper trading" if metrics["all_criteria_met"] else "FAIL — Needs tuning"
    print(f"  Overall: {verdict}")
    print("=" * 60 + "\n")


def print_sector_performance(trades: list):
    """Print P&L and win-rate breakdown by sector."""
    if not trades: return

    stats = {}
    for t in trades:
        sec = getattr(t, "sector", None) or "Unknown"
        if sec not in stats:
            stats[sec] = {"pnl": 0.0, "trades": 0, "wins": 0, "hold_days": 0}
        stats[sec]["pnl"]       += t.net_pnl or 0
        stats[sec]["trades"]    += 1
        stats[sec]["hold_days"] += t.hold_days or 0
        if (t.net_pnl or 0) > 0:
            stats[sec]["wins"] += 1

    total_pnl = sum(s["pnl"] for s in stats.values()) or 1
    sorted_sectors = sorted(stats.items(), key=lambda x: x[1]["pnl"], reverse=True)

    print("\n  SECTOR-WISE PERFORMANCE")
    print("-" * 80)
    print(f"  {'Sector':<32} | {'P&L (₹)':>10} | {'Contrib%':>8} | {'Trades':>6} | {'Win%':>6} | {'AvgHold':>7}")
    print("-" * 80)
    for sec, s in sorted_sectors:
        win_pct  = (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0
        contrib  = (s["pnl"] / total_pnl * 100)
        avg_hold = (s["hold_days"] / s["trades"]) if s["trades"] > 0 else 0
        emoji    = "🟢" if s["pnl"] >= 0 else "🔴"
        print(f"  {emoji} {sec:<30} | {s['pnl']:>10,.2f} | {contrib:>+7.1f}% | {s['trades']:>6} | {win_pct:>5.1f}% | {avg_hold:>6.1f}d")
    print("-" * 80)


def print_stock_performance(trades: list):
    """Print P&L breakdown for each stock traded."""
    if not trades: return

    stats = {}
    for t in trades:
        if t.symbol not in stats:
            stats[t.symbol] = {"pnl": 0.0, "trades": 0, "wins": 0, "sector": getattr(t, "sector", "")}
        stats[t.symbol]["pnl"] += t.net_pnl or 0
        stats[t.symbol]["trades"] += 1
        if (t.net_pnl or 0) > 0:
            stats[t.symbol]["wins"] += 1

    # Sort by total P&L descending
    sorted_stats = sorted(stats.items(), key=lambda x: x[1]["pnl"], reverse=True)

    print("\n  STOCK-WISE PERFORMANCE (Net P&L)")
    print("-" * 60)
    print(f"  {'Symbol':<15} | {'P&L (₹)':>10} | {'Trades':>6} | {'Win%':>6}")
    print("-" * 60)

    # Show top 15 and bottom 5 performers
    top_n = sorted_stats[:15]
    bottom_n = sorted_stats[-5:] if len(sorted_stats) > 20 else []

    for sym, s in top_n:
        win_pct = (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0
        print(f"  {sym:<15} | {s['pnl']:>10,.2f} | {s['trades']:>6} | {win_pct:>5.1f}%")

    if bottom_n:
        print(f"  ...")
        for sym, s in bottom_n:
            win_pct = (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0
            print(f"  {sym:<15} | {s['pnl']:>10,.2f} | {s['trades']:>6} | {win_pct:>5.1f}%")


def print_monthly_returns(monthly_returns: dict):
    """Print a grid of monthly returns."""
    print("-" * 60)
    print("  MONTHLY RETURNS (%)")
    print("-" * 60)
    
    years = sorted(monthly_returns.keys())
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    header = "Year | " + " | ".join(months) + " | Year"
    print(header)
    print("-" * len(header))
    
    for year in years:
        row = [f"{year}"]
        year_total = 1.0
        for m in range(1, 13):
            ret = monthly_returns[year].get(m)
            if ret is not None:
                row.append(f"{ret*100:>+5.1f}")
                year_total *= (1 + ret)
            else:
                row.append("     ")
        
        row.append(f"{(year_total - 1)*100:>+5.1f}")
        print(" | ".join(row))


def _portfolio_value_at(result: BacktestResult, ts, fallback: float) -> float:
    """Look up portfolio value at or just before a timestamp from equity_curve."""
    v = result.equity_curve.get(ts)
    if v is not None:
        return v
    candidates = sorted(k for k in result.equity_curve if k <= ts)
    return result.equity_curve[candidates[-1]] if candidates else fallback


def save_trade_log(result: BacktestResult, filepath: str = None, initial_capital: float = INITIAL_CAPITAL):
    if filepath is None:
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        filepath = os.path.join(OUTPUTS_DIR, "backtest_trades.csv")

    fieldnames = [
        "symbol", "sector",
        "entry_date", "exit_date", "hold_days",
        "entry_price", "exit_price", "shares",
        "position_size_inr", "position_size_pct",
        "stop_loss_2pct_est", "exit_reason",
        "gross_pnl", "net_pnl", "pnl_pct", "charges",
    ]
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in result.trades:
            position_size_inr = round(t.entry_price * t.shares, 2)
            portfolio_at_entry = _portfolio_value_at(result, t.entry_date, initial_capital)
            position_size_pct = round(position_size_inr / portfolio_at_entry * 100, 2) if portfolio_at_entry > 0 else 0.0
            pnl_pct = round((t.exit_price - t.entry_price) / t.entry_price * 100, 2) if t.entry_price > 0 else 0.0
            stop_loss_2pct_est = round(t.entry_price * 0.98, 2)  # ATR stop not stored in Trade; this is a fixed 2% estimate
            writer.writerow({
                "symbol":            t.symbol,
                "sector":            t.sector,
                "entry_date":        str(t.entry_date),
                "exit_date":         str(t.exit_date),
                "hold_days":         t.hold_days,
                "entry_price":       t.entry_price,
                "exit_price":        t.exit_price,
                "shares":            t.shares,
                "position_size_inr": position_size_inr,
                "position_size_pct": position_size_pct,
                "stop_loss_2pct_est": stop_loss_2pct_est,
                "exit_reason":       t.exit_reason,
                "gross_pnl":         t.gross_pnl,
                "net_pnl":           t.net_pnl,
                "pnl_pct":           pnl_pct,
                "charges":           t.charges,
            })
    print(f"[Reporter] Trade log saved: {filepath}  ({len(result.trades)} trades)")


def save_rejected_log(result: BacktestResult, filepath: str = None):
    """Save per-day rejected trade reasons to CSV."""
    if not result.daily_scan_log:
        return

    if filepath is None:
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        filepath = os.path.join(OUTPUTS_DIR, "backtest_rejected.csv")

    # Extract rejected decisions (signal=NO or rs_pass=FAIL)
    fieldnames = ["date", "symbol", "rs_pass", "signal", "reason", "rank_score", "rs_rank", "selected"]
    rejected = [d for d in result.decision_log if d.get("selected") == "NO"]

    if not rejected:
        return

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rejected)
    print(f"[Reporter] Rejected log saved: {filepath}  ({len(rejected)} entries)")


def save_decision_log(result: BacktestResult, filepath: str = None):
    """
    Save per-stock per-day decision table to CSV.

    Shows for each stock scanned:
      Symbol | RS | Signal | Rank | Selected

    Example:
      RELIANCE  PASS  YES  87.2  YES
      TCS       PASS  YES  76.1  YES
      WIPRO     PASS  YES  65.4  NO (not in top 5)
      HDFCBANK  FAIL  NO   12.0  NO (RS failed)
    """
    if not result.decision_log:
        return

    if filepath is None:
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        filepath = os.path.join(OUTPUTS_DIR, "backtest_decisions.csv")

    fieldnames = ["date", "symbol", "rs_pass", "signal", "reason", "rank_score", "rs_rank", "selected"]
    # Add 'date' field from daily scan if not already in decision_log entries
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result.decision_log)
    print(f"[Reporter] Decision log saved: {filepath}  ({len(result.decision_log)} entries)")


def save_daily_scan_log(result: BacktestResult, filepath: str = None):
    """Save daily scan summary (scanned/passed/selected/regime) to CSV."""
    if not result.daily_scan_log:
        return

    if filepath is None:
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        filepath = os.path.join(OUTPUTS_DIR, "backtest_daily_scan.csv")

    fieldnames = [
        "date", "regime", "portfolio_value",
        "total_scanned", "rs_passed", "signals", "selected",
        "open_positions", "relaxed_filters",
    ]
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result.daily_scan_log)
    print(f"[Reporter] Daily scan log saved: {filepath}  ({len(result.daily_scan_log)} days)")


def save_equity_curve(result: BacktestResult, filepath: str = None):
    """Save equity curve to CSV."""
    if filepath is None:
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        filepath = os.path.join(OUTPUTS_DIR, "backtest_equity.csv")

    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "portfolio_value", "cash"])
        for d in sorted(result.equity_curve.keys()):
            writer.writerow([str(d), result.equity_curve[d], result.cash_curve.get(d, "")])
    print(f"[Reporter] Equity curve saved: {filepath}")


def save_transaction_log(result: BacktestResult, filepath: str = None, initial_capital: float = INITIAL_CAPITAL):
    """Save every BUY/SELL/ADD action to CSV and an extended Excel file as requested by user."""
    if not result.transaction_log:
        return

    import pandas as pd

    if filepath is None:
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        filepath = os.path.join(OUTPUTS_DIR, "backtest_transactions.csv")

    fieldnames = ["Time", "Action", "Stock", "Price", "Qty", "Balance", "Holdings"]
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(result.transaction_log)
    print(f"[Reporter] Transaction log saved: {filepath} ({len(result.transaction_log)} rows)")

    try:
        excel_filepath = filepath.replace(".csv", ".xlsx")
        
        # Sheet 1: Transactions
        df_trans = pd.DataFrame(result.transaction_log)
        
        # Sheet 2: Monthly P&L
        if result.equity_curve:
            df_eq = pd.DataFrame(list(result.equity_curve.items()), columns=["Date", "Portfolio Value"])
            df_eq["Date"] = pd.to_datetime(df_eq["Date"])
            df_eq.set_index("Date", inplace=True)
            df_monthly = df_eq.resample("ME").last()
            df_monthly["Monthly P&L (₹)"] = df_monthly["Portfolio Value"].diff().fillna(df_monthly["Portfolio Value"] - initial_capital)
            df_monthly["Monthly P&L (%)"] = (df_monthly["Monthly P&L (₹)"] / df_monthly["Portfolio Value"].shift(1).fillna(initial_capital)) * 100
            df_monthly.reset_index(inplace=True)
            df_monthly["Date"] = df_monthly["Date"].dt.strftime("%Y-%m")
        else:
            df_monthly = pd.DataFrame(columns=["Date", "Portfolio Value", "Monthly P&L (₹)", "Monthly P&L (%)"])

        # Sheet 3: Stock Summary
        stock_stats = {}
        for row in result.transaction_log:
            sym = row["Stock"]
            if sym not in stock_stats:
                stock_stats[sym] = {"bought_qty": 0, "total_buy_val": 0.0, "sold_qty": 0, "total_sell_val": 0.0}
            qty = row["Qty"]
            price = row["Price"]
            if row["Action"] in ["BUY", "ADD_TO_WINNER"]:
                stock_stats[sym]["bought_qty"] += qty
                stock_stats[sym]["total_buy_val"] += qty * price
            elif row["Action"] == "SELL":
                stock_stats[sym]["sold_qty"] += qty
                stock_stats[sym]["total_sell_val"] += qty * price

        summary_rows = []
        for sym, stats in stock_stats.items():
            b_qty = stats["bought_qty"]
            s_qty = stats["sold_qty"]
            o_qty = b_qty - s_qty
            avg_buy = stats["total_buy_val"] / b_qty if b_qty > 0 else 0
            avg_sell = stats["total_sell_val"] / s_qty if s_qty > 0 else 0
            realized_pnl = sum([t.net_pnl for t in result.trades if t.symbol == sym])
            
            summary_rows.append({
                "Stock": sym,
                "Qty Bought": b_qty,
                "Qty Sold": s_qty,
                "Qty Open": o_qty,
                "Avg Buying Value": round(avg_buy, 2),
                "Avg Selling Value": round(avg_sell, 2),
                "Net P&L": round(realized_pnl, 2)
            })
            
        df_summary = pd.DataFrame(summary_rows)

        with pd.ExcelWriter(excel_filepath, engine='openpyxl') as writer:
            df_trans.to_excel(writer, sheet_name="Transactions", index=False)
            df_monthly.to_excel(writer, sheet_name="Monthly P&L", index=False)
            df_summary.to_excel(writer, sheet_name="Stock Summary", index=False)
            
        print(f"[Reporter] Extended Excel log saved: {excel_filepath} with 3 sheets")
    except Exception as e:
        print(f"[Reporter] Failed to create Excel log: {e}")


def save_sector_analysis(result: BacktestResult, filepath: str = None):
    """Save sector-wise performance summary to CSV."""
    if not result.trades:
        return
    if filepath is None:
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        filepath = os.path.join(OUTPUTS_DIR, "backtest_sector_analysis.csv")

    stats = {}
    for t in result.trades:
        sec = getattr(t, "sector", None) or "Unknown"
        if sec not in stats:
            stats[sec] = {"pnl": 0.0, "trades": 0, "wins": 0, "hold_days": 0, "symbols": set()}
        stats[sec]["pnl"]       += t.net_pnl or 0
        stats[sec]["trades"]    += 1
        stats[sec]["hold_days"] += t.hold_days or 0
        stats[sec]["symbols"].add(t.symbol)
        if (t.net_pnl or 0) > 0:
            stats[sec]["wins"] += 1

    total_pnl = sum(s["pnl"] for s in stats.values()) or 1
    rows = []
    for sec, s in sorted(stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
        rows.append({
            "sector":        sec,
            "net_pnl":       round(s["pnl"], 2),
            "pnl_contrib_pct": round(s["pnl"] / total_pnl * 100, 1),
            "trades":        s["trades"],
            "win_rate_pct":  round(s["wins"] / s["trades"] * 100, 1) if s["trades"] else 0,
            "avg_hold_days": round(s["hold_days"] / s["trades"], 1) if s["trades"] else 0,
            "unique_stocks": len(s["symbols"]),
            "symbols":       ",".join(sorted(s["symbols"])),
        })

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"[Reporter] Sector analysis saved: {filepath}")


def run_and_report(result: BacktestResult, initial_capital: float = INITIAL_CAPITAL):
    """Compute metrics, print summary, save all CSVs."""
    metrics = calculate_metrics(result, initial_capital)
    print_summary(metrics, result)
    save_trade_log(result, initial_capital=initial_capital)
    save_transaction_log(result, initial_capital=initial_capital)
    save_rejected_log(result)
    save_decision_log(result)
    save_daily_scan_log(result)
    save_equity_curve(result)
    save_sector_analysis(result)
    return metrics
