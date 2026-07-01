import os
import pandas as pd
import logging
from datetime import date, datetime, timedelta
import time
import sys
from dotenv import load_dotenv

# Import our existing modules
from data.providers.upstox_provider import UpstoxDataProvider
from data.instruments.mapper import InstrumentMapper
from data.universe import get_all_symbols

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("MinuteDataSaver")

# Load environment variables
load_dotenv()
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN")

# Configuration
PARQUET_DIR = "data/parquet"
START_DATE = date(2023, 1, 1)
END_DATE = date.today()
CHUNK_DAYS = 30  # Fetch 30 days of 1-minute data at a time (safer for API limits)

def save_symbol_minute_data(symbol: str, provider: UpstoxDataProvider, mapper: InstrumentMapper):
    """Fetch and save 1-minute data for a single symbol."""
    if symbol == "Nifty 50":
        instrument_key = "NSE_INDEX|Nifty 50"
    else:
        instrument_key = mapper.get_key(symbol)

    if not instrument_key:
        logger.warning(f"No instrument key found for {symbol}")
        return

    os.makedirs(PARQUET_DIR, exist_ok=True)
    file_path = os.path.join(PARQUET_DIR, f"{symbol}.parquet")
    
    existing_df = pd.DataFrame()
    last_fetched_date = None
    earliest_fetched_date = None
    
    if os.path.exists(file_path):
        try:
            existing_df = pd.read_parquet(file_path)
            if not existing_df.empty:
                last_fetched_date = existing_df.index.max().date()
                earliest_fetched_date = existing_df.index.min().date()
                logger.info(f"Existing data for {symbol}: {earliest_fetched_date} to {last_fetched_date}")
        except Exception as e:
            logger.error(f"Failed to read existing parquet for {symbol}: {e}")

    all_dfs = [existing_df] if not existing_df.empty else []

    # 1. Fetch missing historical data (Before existing range)
    if earliest_fetched_date is None or earliest_fetched_date > START_DATE:
        fetch_until = (earliest_fetched_date - timedelta(days=1)) if earliest_fetched_date else END_DATE
        fetch_from = START_DATE
        
        logger.info(f"Fetching historical gap for {symbol}: {fetch_from} to {fetch_until}")
        
        temp_start = fetch_from
        while temp_start <= fetch_until:
            temp_end = min(temp_start + timedelta(days=CHUNK_DAYS), fetch_until)
            logger.info(f"Fetching historical {symbol}: {temp_start} to {temp_end}")
            try:
                df = provider.fetch_historical(instrument_key, interval="1minute", from_date=temp_start, to_date=temp_end)
                if not df.empty:
                    df.index = pd.to_datetime(df.index).tz_localize(None)
                    all_dfs.append(df)
                    # Merge and save immediately to avoid memory bloat
                    combined_df = pd.concat(all_dfs).sort_index()
                    combined_df = combined_df[~combined_df.index.duplicated(keep='last')]
                    combined_df.to_parquet(file_path, compression='snappy')
                    all_dfs = [combined_df]
                else:
                    logger.info(f"No 1-min data for {symbol} in {temp_start} to {temp_end}")
            except Exception as e:
                logger.error(f"Error fetching {symbol}: {e}")
                time.sleep(2)
            
            temp_start = temp_end + timedelta(days=1)
            time.sleep(0.2)

    # 2. Fetch missing forward data (After existing range)
    if last_fetched_date and last_fetched_date < END_DATE:
        fetch_from = last_fetched_date + timedelta(days=1)
        fetch_until = END_DATE
        
        logger.info(f"Fetching forward gap for {symbol}: {fetch_from} to {fetch_until}")
        
        temp_start = fetch_from
        while temp_start <= fetch_until:
            temp_end = min(temp_start + timedelta(days=CHUNK_DAYS), fetch_until)
            logger.info(f"Fetching forward {symbol}: {temp_start} to {temp_end}")
            try:
                df = provider.fetch_historical(instrument_key, interval="1minute", from_date=temp_start, to_date=temp_end)
                if not df.empty:
                    df.index = pd.to_datetime(df.index).tz_localize(None)
                    all_dfs.append(df)
                    combined_df = pd.concat(all_dfs).sort_index()
                    combined_df = combined_df[~combined_df.index.duplicated(keep='last')]
                    combined_df.to_parquet(file_path, compression='snappy')
                    all_dfs = [combined_df]
                else:
                    logger.info(f"No 1-min data for {symbol} in {temp_start} to {temp_end}")
            except Exception as e:
                logger.error(f"Error fetching {symbol}: {e}")
                time.sleep(2)
            
            temp_start = temp_end + timedelta(days=1)
            time.sleep(0.2)

def main():
    if not UPSTOX_ACCESS_TOKEN:
        logger.error("UPSTOX_ACCESS_TOKEN not found in .env")
        return

    provider = UpstoxDataProvider(UPSTOX_ACCESS_TOKEN)
    mapper = InstrumentMapper()
    
    # Get symbols from command line if provided, else from universe
    if len(sys.argv) > 1:
        symbols = sys.argv[1:]
    else:
        symbols = get_all_symbols()
        # Ensure Nifty 50 is included by default
        if "Nifty 50" not in symbols:
            symbols = ["Nifty 50"] + symbols

    logger.info(f"Starting 1-minute data fetch for {len(symbols)} symbols...")
    
    for i, symbol in enumerate(symbols, 1):
        logger.info(f"--- Processing {i}/{len(symbols)}: {symbol} ---")
        save_symbol_minute_data(symbol, provider, mapper)

if __name__ == "__main__":
    main()
