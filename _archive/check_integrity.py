import os
import pandas as pd
import numpy as np

PARQUET_DIR = "data/parquet"
files = [f for f in os.listdir(PARQUET_DIR) if f.endswith(".parquet")]

results = []
for file in sorted(files):
    path = os.path.join(PARQUET_DIR, file)
    try:
        df = pd.read_parquet(path)
        if df.empty:
            results.append((file, "EMPTY", None, None, 0))
            continue
            
        start_dt = df.index.min()
        end_dt = df.index.max()
        row_count = len(df)
        
        # Simple dummy check: if rows < 10 or price is suspiciously constant/round
        is_dummy = False
        if row_count < 10:
            is_dummy = True
        elif df['close'].nunique() == 1 and row_count > 1:
            is_dummy = True
            
        status = "DUMMY" if is_dummy else "OK"
        results.append((file, status, start_dt, end_dt, row_count))
    except Exception as e:
        results.append((file, f"ERROR: {str(e)}", None, None, 0))

print(f"{'File':<25} | {'Status':<10} | {'Start':<20} | {'End':<20} | {'Rows':<10}")
print("-" * 100)
for r in results:
    print(f"{r[0]:<25} | {r[1]:<10} | {str(r[2]):<20} | {str(r[3]):<20} | {r[4]:<10}")
