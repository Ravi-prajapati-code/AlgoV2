"""
Sync Utility — imports existing Upstox holdings into the Shield database.
Standardized to capture ACTUAL average cost (invested amount).
"""

import os
import sys
import logging
from datetime import date
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(override=True)

from broker.upstox import UpstoxBroker
from db.repository import init_db, save_position, load_positions, save_snapshot
from db.models import Position, PortfolioSnapshot
from strategy.exit import initial_stops
from data.universe import get_sector
from config.settings import INITIAL_CAPITAL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SyncUtility")

def sync():
    logger.info("Initializing Shield Database...")
    init_db()
    
    try:
        broker = UpstoxBroker()
    except Exception as e:
        logger.error(f"Failed to connect to Upstox: {e}")
        return

    logger.info("Fetching real-time data from Upstox...")
    live_positions = broker.get_positions()
    live_cash = broker.get_available_cash()
    
    # We need to CLEAR existing positions before sync to ensure Invested Amount is accurate
    from db.repository import get_connection
    conn = get_connection()
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()

    total_invested_cost = 0
    total_market_value = 0
    
    from data.fetcher import fetch_symbol

    for lp in live_positions:
        # 1. Invested Cost (What you paid)
        invested_cost = lp.avg_price * lp.quantity
        total_invested_cost += invested_cost

        # 2. Market Value (Current worth)
        df = fetch_symbol(lp.symbol, lookback_days=5)
        current_price = df['close'].iloc[-1] if not df.empty else lp.ltp
        total_market_value += current_price * lp.quantity

        logger.info(f"Syncing {lp.symbol}: Qty {lp.quantity} @ Avg ₹{lp.avg_price:.2f} (Cost: ₹{invested_cost:,.2f})")
        
        stops = initial_stops(current_price)
        
        new_pos = Position(
            symbol=lp.symbol,
            sector=get_sector(lp.symbol),
            entry_date=date.today(), 
            entry_price=lp.avg_price, # This is your real invested price
            shares=lp.quantity,
            stop_loss=stops["stop_loss"],
            take_profit=stops["take_profit"],
            trailing_stop=stops["trailing_stop"],
            peak_price=max(lp.avg_price, current_price)
        )
        save_position(new_pos)

    # Save snapshot using the Invested Cost as the baseline
    total_value = live_cash + total_market_value
    snap = PortfolioSnapshot(
        date=date.today(),
        cash=live_cash,
        invested=total_invested_cost, # This will now match your 60k expectation
        total_value=total_value,
        open_positions=len(live_positions),
        daily_pnl=total_market_value - total_invested_cost,
        cumulative_pnl=total_value - INITIAL_CAPITAL
    )
    save_snapshot(snap)

    logger.info(f"Sync complete.")
    print(f"\n--- SYNC SUMMARY ---")
    print(f"Real Invested Cost:  ₹{total_invested_cost:,.2f}")
    print(f"Real Market Value:   ₹{total_market_value:,.2f}")
    print(f"Available Cash:      ₹{live_cash:,.2f}")

if __name__ == "__main__":
    sync()
