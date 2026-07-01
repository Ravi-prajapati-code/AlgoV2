
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db.repository import close_position, load_positions

print("Checking current open positions...")
pos = load_positions(status="OPEN")
for p in pos:
    print(f" - {p.symbol}: {p.shares} shares")

print("\nForcing close of WIPRO.NS in local database...")
close_position("WIPRO.NS")

print("Verification:")
pos = load_positions(status="OPEN")
found = False
for p in pos:
    if p.symbol == "WIPRO.NS":
        found = True
    print(f" - {p.symbol}: {p.shares} shares")

if not found:
    print("\n✅ SUCCESS: WIPRO.NS is now marked as CLOSED in your database.")
else:
    print("\n❌ FAILED: WIPRO.NS is still marked as OPEN.")
