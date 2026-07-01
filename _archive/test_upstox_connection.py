import os

def get_token_from_file():
    with open(".env", "r") as f:
        for line in f:
            if line.startswith("UPSTOX_ACCESS_TOKEN="):
                return line.split("=")[1].strip().strip('"')
    return None

def test_connection():
    try:
        token = get_token_from_file()
        if token:
            print(f"Token read from file: {token[:10]}...{token[-10:]}")
            os.environ["UPSTOX_ACCESS_TOKEN"] = token
        else:
            print("Token not found in .env file!")
            return
            
        # Import inside the function AFTER os.environ is set
        from broker.upstox import UpstoxBroker
        broker = UpstoxBroker()
        print("Attempting to fetch available cash...")
        cash = broker.get_available_cash()
        print(f"✅ SUCCESS! Available Cash: ₹{cash:,.2f}")
        
        print("\nAttempting to fetch positions...")
        positions = broker.get_positions()
        print(f"✅ SUCCESS! Found {len(positions)} positions.")
        for p in positions:
            print(f"  - {p.symbol}: {p.quantity} shares")
            
    except Exception as e:
        print(f"❌ FAILED: {e}")

if __name__ == "__main__":
    test_connection()
