
import sqlite3
import pandas as pd
from datetime import date
from db.repository import save_position, save_snapshot, get_connection
from db.models import Position, PortfolioSnapshot
from config.settings import DB_PATH, INITIAL_CAPITAL

def clear_db():
    conn = get_connection()
    # Clear active data for a fresh paper trading start
    conn.execute("DELETE FROM positions")
    conn.execute("DELETE FROM trades")
    conn.execute("DELETE FROM portfolio_snapshots")
    conn.execute("DELETE FROM signals")
    conn.commit()
    conn.close()
    print("[Paper Trade] Cleared existing tables.")

def init_paper():
    # Final data from latest response
    # Total Invested: 98,440.40
    # Remaining Cash: 1,559.60
    positions_to_add = [
        {"symbol": "ASHOKLEY.NS",   "sector": "Capital Goods", "shares": 147, "price": 172.20},
        {"symbol": "BHARATFORG.NS", "sector": "Auto",          "shares": 13,  "price": 1801.00},
        {"symbol": "SBIN.NS",       "sector": "Finance",       "shares": 23,  "price": 1062.00},
        {"symbol": "INDUSTOWER.NS", "sector": "Telecom",       "shares": 58,  "price": 436.00},
    ]

    total_invested = 0
    today = date(2026, 4, 16)
    
    print(f"\n[Paper Trade] Initializing portfolio for {today}...")
    for p in positions_to_add:
        # Standard 7% stop and 60% profit for initialization
        stop_loss = round(p["price"] * 0.93, 2)
        take_profit = round(p["price"] * 1.60, 2)
        
        pos = Position(
            symbol=p["symbol"],
            sector=p["sector"],
            entry_date=today,
            entry_price=p["price"],
            shares=p["shares"],
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_stop=stop_loss,
            peak_price=p["price"],
            status="OPEN"
        )
        save_position(pos)
        val = p["price"] * p["shares"]
        total_invested += val
        print(f"  Added {p['symbol']}: {p['shares']} shares @ ₹{p['price']} (Value: ₹{val:,.2f})")

    cash = 100000 - total_invested
    total_value = total_invested + cash
    
    snap = PortfolioSnapshot(
        date=today,
        cash=cash,
        invested=total_invested,
        total_value=total_value,
        open_positions=len(positions_to_add),
        daily_pnl=0,
        cumulative_pnl=0
    )
    save_snapshot(snap)
    print(f"\n[Paper Trade] Portfolio Initialized:")
    print(f"  Cash        : ₹{cash:,.2f}")
    print(f"  Invested    : ₹{total_invested:,.2f}")
    print(f"  Total Value : ₹{total_value:,.2f}")

if __name__ == "__main__":
    clear_db()
    init_paper()
