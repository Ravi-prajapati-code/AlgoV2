"""
CLI entry point for the Algo swing trading platform.

Usage
-----
  python main.py run                          # Run today's signal pipeline
  python main.py backtest                     # Backtest with default dates
  python main.py backtest --start 2022-01-01 --end 2024-12-31
  python main.py charges                      # Show charges example
  python main.py initdb                       # Initialise / migrate database
  python main.py train_ml                     # Train ML prediction model
  python main.py train_ml --start 2022-01-01 --end 2024-12-31
  python main.py risk_report                  # Show current portfolio risk
"""

import argparse
import sys
import logging
from datetime import date

from monitoring.logger import setup_logging
from dotenv import load_dotenv
load_dotenv(override=True)
setup_logging()


def _audit_abort(reason: str):
    """Telegram-alert + exit when the live session can't start. A skipped live
    run leaves open positions unmanaged for the day — the operator must know."""
    from datetime import date as _date
    try:
        import html
        from notifications.telegram import send_message
        send_message(
            f"🛑 <b>Live session BLOCKED — {_date.today()}</b>\n"
            f"Reason: {html.escape(str(reason))}\n\n"
            f"Daily run did NOT start. Open positions are UNMANAGED today — "
            f"refresh the Upstox token and verify stops manually."
        )
    except Exception:
        pass
    sys.exit(1)


def _audit_live_session():
    """Verify connectivity and credentials before starting a live session."""
    import os
    import requests as _requests
    from config.settings import UPSTOX_ACCESS_TOKEN

    if not UPSTOX_ACCESS_TOKEN:
        print("\n❌ ERROR: UPSTOX_ACCESS_TOKEN missing from .env")
        print("Live trading requires a fresh token every 24 hours.")
        _audit_abort("UPSTOX_ACCESS_TOKEN missing from .env")

    # Direct API call — catches 401 that get_available_cash() silently swallows
    try:
        resp = _requests.get(
            "https://api.upstox.com/v2/user/get-funds-and-margin",
            headers={"Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}", "Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == 401:
            print("\n❌ ERROR: UPSTOX_ACCESS_TOKEN is expired or invalid (401).")
            print("Run: python3 scripts/auto_token_playwright.py")
            _audit_abort("UPSTOX_ACCESS_TOKEN expired or invalid (401)")
        resp.raise_for_status()
        data = resp.json().get("data", {})
        cash = float(data.get("equity", {}).get("available_margin", 0))
        print(f"✅ Live Broker Connected | Available Cash: ₹{cash:,.2f}")
    except SystemExit:
        raise
    except Exception as e:
        print(f"\n❌ ERROR: Failed to connect to Upstox API: {e}")
        _audit_abort(f"Failed to connect to Upstox API: {e}")


def cmd_run(args):
    from runner.daily_runner import run
    from datetime import datetime
    
    if args.live:
        _audit_live_session()

    today = None
    if args.date:
        today = datetime.strptime(args.date, "%Y-%m-%d").date()
    run(today=today, live_mode=args.live, fund_injection=args.inject)



def cmd_update_parquet():
    """Incrementally update all parquet files with latest 1-min data from Upstox."""
    import os, time
    from dotenv import load_dotenv
    from data.providers.upstox_provider import UpstoxDataProvider
    from data.instruments.mapper import InstrumentMapper
    from data.universe import get_all_symbols
    from save_minute_data import save_symbol_minute_data

    load_dotenv()
    token = os.getenv("UPSTOX_ACCESS_TOKEN")
    if not token:
        print("[Update] ERROR: UPSTOX_ACCESS_TOKEN not set in .env — skipping data update.")
        return

    provider = UpstoxDataProvider(token)
    mapper   = InstrumentMapper()
    symbols  = ["Nifty 50"] + get_all_symbols()

    print(f"[Update] Refreshing parquet for {len(symbols)} symbols (incremental)…")
    for i, symbol in enumerate(symbols, 1):
        print(f"[Update] {i}/{len(symbols)} {symbol}", end="\r")
        save_symbol_minute_data(symbol, provider, mapper)
    print(f"\n[Update] Parquet refresh complete.")


def cmd_backtest(args):
    from datetime import datetime
    from data.fetcher import fetch_all, fetch_index
    from data.universe import get_all_symbols, get_all_symbols_as_of, UniverseHistoryUnavailable
    from backtest.engine import BacktestEngine
    from backtest.reporter import run_and_report
    from db.repository import init_db
    from config.settings import INITIAL_CAPITAL, MARKET_INDEX_SYMBOL, MAX_OPEN_POSITIONS

    if getattr(args, "update_data", False):
        cmd_update_parquet()

    init_db()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end   = datetime.strptime(args.end,   "%Y-%m-%d").date()

    print(f"Fetching historical data ({start} → {end})…")
    try:
        symbols = get_all_symbols_as_of(start)
    except UniverseHistoryUnavailable as e:
        print(f"WARNING: {e}", file=sys.stderr)
        print(
            "WARNING: falling back to TODAY's static watchlist applied retroactively to "
            f"{start} — this backtest's symbol selection may reflect look-ahead/survivorship "
            "bias. See docs/13_Independent_Institutional_Review.md §2/§4/§10.",
            file=sys.stderr,
        )
        symbols = get_all_symbols()
    lookback = (end - start).days + 60
    # 500-day warmup ensures EMA(150) is fully converged at backtest day 1 (needs ~450 trading days)
    from datetime import timedelta
    warmup_start = start - timedelta(days=500)
    data     = fetch_all(symbols, lookback_days=lookback, start=warmup_start, end=end)

    print(f"Fetching market index {MARKET_INDEX_SYMBOL}…")
    index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=lookback, start=warmup_start, end=end)
    if not index_df.empty:
        data[MARKET_INDEX_SYMBOL] = index_df

    slippage = getattr(args, "slippage", "fixed_pct")

    fund_injections = {}
    for item in (getattr(args, "inject_funds", None) or []):
        try:
            d_str, amt_str = item.split(":")
            fund_injections[datetime.strptime(d_str.strip(), "%Y-%m-%d").date()] = float(amt_str.strip())
        except ValueError:
            print(f"[ERROR] Invalid --inject-funds format: '{item}' — expected DATE:AMOUNT e.g. 2023-06-01:50000")
            sys.exit(1)

    if fund_injections:
        print(f"Fund injections scheduled: {', '.join(f'{d}:₹{a:,.0f}' for d, a in sorted(fund_injections.items()))}")

    print(f"Running backtest on {len(data)-1} symbols + index  [slippage={slippage}]…")
    engine = BacktestEngine(
        data, start, end, INITIAL_CAPITAL,
        slippage_model=slippage, max_selected=MAX_OPEN_POSITIONS,
        fund_injections=fund_injections,
    )
    result = engine.run()
    run_and_report(result, INITIAL_CAPITAL)


def cmd_charges(_args):
    from charges.calculator import net_pnl
    print("\n=== Upstox Delivery Charges Example ===")
    print("Trade: Buy 10 shares @ ₹1,000, Sell @ ₹1,130 (13% gain)\n")
    result = net_pnl(1000, 1130, 10)
    print(f"  Buy value:       ₹{result['buy_value']:,.2f}")
    print(f"  Sell value:      ₹{result['sell_value']:,.2f}")
    print(f"  Gross P&L:       ₹{result['gross_pnl']:+,.2f}  ({result['gross_pct']:+.2f}%)")
    print(f"  Total Charges:   ₹{result['total_charges']:,.2f}")
    print(f"  Net P&L:         ₹{result['net_pnl']:+,.2f}  ({result['net_pct']:+.2f}%)")
    print("\n  Buy charges breakdown:")
    for k, v in result["buy_charges"].items():
        print(f"    {k:<15}: ₹{v:.2f}")
    print("  Sell charges breakdown:")
    for k, v in result["sell_charges"].items():
        print(f"    {k:<15}: ₹{v:.2f}")


def cmd_initdb(_args):
    from db.repository import init_db
    init_db()


def cmd_train_ml(args):
    """Train the ML prediction model on historical trade data."""
    from datetime import datetime
    from ml.trainer import train, print_feature_importance
    from ml.model import get_model_handler

    start = None
    end   = None
    if hasattr(args, "start") and args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    if hasattr(args, "end") and args.end:
        end = datetime.strptime(args.end, "%Y-%m-%d").date()

    print("Training ML model on historical trade data…")
    success = train(start_date=start, end_date=end)
    if success:
        handler = get_model_handler()
        meta = handler.metadata
        print(f"\nML Model trained successfully!")
        print(f"  Trades used   : {meta.get('n_trades', 'N/A')}")
        print(f"  Win rate      : {meta.get('win_rate', 0)*100:.1f}%")
        print(f"  Train period  : {meta.get('train_start')} → {meta.get('train_end')}")
        print(f"\nEnable ML in live trading by setting: ML_ENABLED=true")
    else:
        print("Training failed. Run a backtest first to generate trade history.")
        sys.exit(1)


def cmd_risk_report(_args):
    """Print current portfolio risk metrics."""
    from db import repository as repo
    from portfolio.allocator import portfolio_invested_value
    from risk.manager import RiskManager
    from config.settings import INITIAL_CAPITAL
    from data.fetcher import fetch_symbol

    positions  = repo.load_positions(status="OPEN")
    snapshots  = repo.load_snapshots()

    # Get latest prices for current valuation
    prices = {}
    for pos in positions:
        df = fetch_symbol(pos.symbol, lookback_days=5)
        if not df.empty:
            prices[pos.symbol] = df['close'].iloc[-1]
        else:
            prices[pos.symbol] = pos.entry_price

    if snapshots:
        cash = snapshots[-1].cash
        peak_val = max(s.total_value for s in snapshots)
    else:
        cash = INITIAL_CAPITAL
        peak_val = INITIAL_CAPITAL

    invested_cost = sum(p.entry_price * p.shares for p in positions)
    market_val    = portfolio_invested_value(positions, prices)
    total_val     = cash + market_val
    peak_val      = max(peak_val, total_val)

    rm = RiskManager(total_val, peak_val, INITIAL_CAPITAL)
    report = rm.health_report()

    print("\n=== Portfolio Risk Report (LIVE VALUATION) ===")
    print(f"  Current Market Value: ₹{market_val:,.2f}")
    print(f"  Total Invested Cost:  ₹{invested_cost:,.2f}")
    print(f"  Unrealized P&L:       ₹{market_val - invested_cost:+,.2f} ({(market_val/invested_cost - 1)*100 if invested_cost else 0:+.1f}%)")
    print(f"  Available Cash:       ₹{cash:,.2f}")
    print(f"  Total Account Value:  ₹{total_val:,.2f}")
    print(f"  Peak Value:           ₹{report['peak_value']:,.2f}")
    print(f"  Drawdown           : {report['drawdown_pct']:.2f}%  (halt at {report['drawdown_halt_pct']:.0f}%)")
    print(f"  Kill Switch        : {'ACTIVE ⚠️' if report['kill_switch_active'] else 'OFF ✓'}")
    print(f"  Size Reduction     : {report['size_reduction_factor']:.0%}")
    print(f"  Return from Start  : {report['return_from_start_pct']:+.2f}%")
    print(f"\n  Open Positions     : {len(positions)}")

    if positions:
        print("\n  Positions:")
        for pos in positions:
            print(f"    {pos.symbol:<20} {pos.shares:>4} shares @ ₹{pos.entry_price:.2f}")


def main():
    parser = argparse.ArgumentParser(
        description="Algo Swing Trading Platform — Production-Grade",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Run today's signal pipeline")
    run_parser.add_argument("--live", action="store_true", help="Enable live trading mode")
    run_parser.add_argument("--date", help="Simulate a specific date (YYYY-MM-DD)")
    run_parser.add_argument("--inject", type=float, default=0.0,
                            metavar="AMOUNT",
                            help="Record a fund injection today in ₹ (paper mode). "
                                 "e.g. --inject 7500")
    
    sub.add_parser("initdb",      help="Initialise / migrate the SQLite database")
    sub.add_parser("charges",     help="Show charges calculation example")
    sub.add_parser("risk_report", help="Print current portfolio risk metrics")

    univ = sub.add_parser("universe", help="Dynamic universe management")
    univ.add_argument(
        "--mode",
        choices=["seed", "status", "daily", "weekly", "monthly", "quarterly", "audit"],
        required=True,
        help="Universe management mode",
    )

    bt = sub.add_parser("backtest", help="Run backtesting engine")
    bt.add_argument("--start",       default="2022-01-01",   help="Start date YYYY-MM-DD")
    bt.add_argument("--end",         default=str(date.today()), help="End date YYYY-MM-DD")
    bt.add_argument("--slippage",    default="fixed_pct",    choices=["none", "fixed_pct", "volatility"],
                    help="Slippage model (default: fixed_pct)")
    bt.add_argument("--update-data", action="store_true",
                    help="Refresh parquet files with latest data before backtesting")
    bt.add_argument("--inject-funds", nargs="*", metavar="DATE:AMOUNT",
                    help="Mid-run fund injections e.g. 2023-06-01:50000 2024-01-01:25000")

    ml = sub.add_parser("train_ml", help="Train ML prediction model on trade history")
    ml.add_argument("--start", default=None, help="Training start date YYYY-MM-DD")
    ml.add_argument("--end",   default=None, help="Training end date YYYY-MM-DD")

    def cmd_universe(args):
        import importlib, sys
        sys.argv = ["universe_scheduler", "--mode", args.mode]
        mod = importlib.import_module("scripts.universe_scheduler")
        mod.main()

    commands = {
        "run":         cmd_run,
        "backtest":    cmd_backtest,
        "charges":     cmd_charges,
        "initdb":      cmd_initdb,
        "train_ml":    cmd_train_ml,
        "risk_report": cmd_risk_report,
        "universe":    cmd_universe,
    }

    args = parser.parse_args()
    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
