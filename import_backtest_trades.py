import pandas as pd
from db.models import Trade
from db import repository as repo
from datetime import datetime

def import_trades():
    df = pd.read_csv("outputs/backtest_trades.csv")
    repo.init_db()
    
    # Clear old trades to avoid duplicates
    conn = repo.get_connection()
    conn.execute("DELETE FROM trades")
    conn.commit()
    
    for _, row in df.iterrows():
        t = Trade(
            symbol=row['symbol'],
            sector=row['sector'],
            entry_date=datetime.strptime(row['entry_date'], "%Y-%m-%d").date(),
            exit_date=datetime.strptime(row['exit_date'], "%Y-%m-%d").date(),
            entry_price=row['entry_price'],
            exit_price=row['exit_price'],
            shares=row['shares'],
            gross_pnl=row['gross_pnl'],
            charges=row['charges'],
            net_pnl=row['net_pnl'],
            exit_reason=row['exit_reason'],
            hold_days=row['hold_days']
        )
        repo.save_trade(t)
    print(f"Imported {len(df)} trades into DB.")

if __name__ == "__main__":
    import_trades()
