"""
Intraday Runner — Executes trades at specific times (e.g., 09:20 and 15:20).
Supports both Paper and Live (Upstox) modes.
"""

import logging
import sys
import os
import argparse
import time
import schedule
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.repository import (
    init_db, load_positions, load_snapshots, save_signal,
)
from data.fetcher import fetch_all, fetch_index
from data.universe import get_all_symbols
from indicators.composite import compute_all
from strategy.signals import generate_signals
from strategy.regime import detect_regime, is_buy_allowed, MIN_INDEX_CANDLES, is_index_confirming
from strategy.relative_strength import compute_rs_for_all
from portfolio.manager import PortfolioManager
from runner.signal_output import write_signals, write_portfolio_state
from config.settings import INITIAL_CAPITAL, MARKET_INDEX_SYMBOL, EXECUTION_TIMES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("IntradayRunner")

def run_pipeline(live_mode: bool = False):
    now = datetime.now()
    today = now.date()
    
    # 0. Check if market is open (9:15 to 15:30)
    market_open = dt_time(9, 15)
    market_close = dt_time(15, 30)
    current_time = now.time()
    
    # In production, you might want to skip weekends here
    if today.weekday() >= 5:
        logger.info("Market is closed (Weekend). Skipping.")
        return

    mode_str = "LIVE (REAL MONEY)" if live_mode else "PAPER TRADING"
    logger.info("=== Intraday Pipeline started [%s]: %s ===", mode_str, now.strftime("%Y-%m-%d %H:%M"))

    # 1. Initialise DB
    init_db()

    # 2. Fetch index (1-minute data for latest regime)
    logger.info("Fetching market index %s (1min)...", MARKET_INDEX_SYMBOL)
    index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=2, interval="1minute")
    
    if index_df.empty:
        logger.warning("Could not fetch 1min index data. Falling back to daily.")
        index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=MIN_INDEX_CANDLES + 50)

    # 3. Detect regime
    regime = detect_regime(index_df)
    market_bullish = is_buy_allowed(regime)
    logger.info("Market regime: %s | BUY entries %s", regime, "ALLOWED" if market_bullish else "BLOCKED")

    # 4. Fetch stock data (1-minute)
    symbols = get_all_symbols()
    data_min = fetch_all(symbols, interval="1minute")
    if not data_min:
        logger.error("No stock data fetched — aborting")
        return

    # Resample to daily bars so strategy logic (EMA, RS) works same as before
    # The last bar will be the "partial" day bar up to the current minute
    data_daily = {}
    for symbol, df_min in data_min.items():
        df_daily = df_min.resample('D').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        if len(df_daily) >= 20:
            data_daily[symbol] = df_daily

    # Resample index as well
    index_daily = index_df.resample('D').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna()

    # 5. Relative strength
    rs_data = compute_rs_for_all(data_daily, index_daily) if market_bullish else {}

    # 6. Compute indicators
    indicators = compute_all(data_daily, rs_data=rs_data)

    # 7. Load open positions
    open_positions = load_positions(status="OPEN")
    held_symbols = {p.symbol for p in open_positions}

    # 8. Generate signals
    snapshots = load_snapshots()
    latest_snap = snapshots[-1] if snapshots else None
    idx_confirmed = is_index_confirming(index_df)

    pv = latest_snap.total_value if latest_snap else INITIAL_CAPITAL
    cash_bal = latest_snap.cash if latest_snap else INITIAL_CAPITAL

    signals, _ = generate_signals(
        today, indicators, open_positions, held_symbols,
        market_bullish=market_bullish, regime=regime,
        portfolio_value=pv, cash=cash_bal, initial_capital=INITIAL_CAPITAL,
        index_confirming=idx_confirmed
    )

    # 9. Process through portfolio manager
    prices = {sym: ind["close"] for sym, ind in indicators.items()}
    
    broker = None
    if live_mode:
        try:
            from broker.upstox import UpstoxBroker
            broker = UpstoxBroker()
            logger.info("[Live] Connected to Upstox")
        except Exception as e:
            logger.error(f"[Live] Failed to initialise Upstox broker: {e}")
            return

    mgr = PortfolioManager(INITIAL_CAPITAL, broker=broker)
    mgr.process_signals(today, signals, prices)

    # 10. Outputs
    snapshots = load_snapshots()
    latest_snap = snapshots[-1] if snapshots else None

    for sig in signals:
        save_signal(sig)
    
    write_signals(today, signals)
    if latest_snap:
        write_portfolio_state(today, latest_snap, mgr.open_positions, prices)

    try:
        from notifications.telegram import send_daily_summary
        send_daily_summary(today, [s for s in signals if s.action == "BUY"], [s for s in signals if s.action == "SELL"], latest_snap)
    except Exception as e:
        logger.warning("Telegram alert failed: %s", e)

    logger.info("=== Intraday Pipeline complete: %s ===", now.strftime("%H:%M"))

from datetime import time as dt_time

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Enable live trading mode")
    parser.add_argument("--now", action="store_true", help="Run once immediately")
    args = parser.parse_args()

    if args.now:
        run_pipeline(live_mode=args.live)
        return

    logger.info("Scheduling intraday runs at: %s", ", ".join(EXECUTION_TIMES))
    
    for t in EXECUTION_TIMES:
        schedule.every().day.at(t).do(run_pipeline, live_mode=args.live)

    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    main()
