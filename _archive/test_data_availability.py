import os
import pandas as pd
import logging
from datetime import date, timedelta
from dotenv import load_dotenv
from data.providers.upstox_provider import UpstoxDataProvider
from data.instruments.mapper import InstrumentMapper

logging.basicConfig(level=logging.INFO)
load_dotenv()
token = os.getenv("UPSTOX_ACCESS_TOKEN")
provider = UpstoxDataProvider(token)
mapper = InstrumentMapper()

symbol = "RELIANCE.NS"
key = mapper.get_key(symbol)

# Test 7: July 2021
to_dt = date(2021, 7, 31)
from_dt = date(2021, 7, 1)
print(f"Testing 1-minute data for {symbol} from {from_dt} to {to_dt}...")
df_2021_july = provider.fetch_historical(key, interval="1minute", to_date=to_dt, from_date=from_dt)
print(f"July 2021 data count: {len(df_2021_july)}")
