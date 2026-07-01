"""
Indicator Verification Script
-------------------------------
Prints our computed indicator values for a symbol so you can compare
against TradingView on the same date.

Usage:
    python scripts/verify_indicators.py RELIANCE
    python scripts/verify_indicators.py HDFCBANK --date 2026-06-10
"""

import sys
import argparse
import pandas as pd
import numpy as np
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

from monitoring.logger import setup_logging
setup_logging()

from data.fetcher import fetch_symbol
from config.settings import MARKET_INDEX_SYMBOL


def wilder_ema(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing — alpha=1/period. Used by TradingView RSI and ATR."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def compute_rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = wilder_ema(gain, period)
    avg_loss = wilder_ema(loss, period)
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return (100 - (100 / (1 + rs))).fillna(100)


def compute_rsi_cutler(close: pd.Series, period: int = 14) -> pd.Series:
    """Cutler's RSI — simple rolling mean. This is what composite.py currently uses."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, float("nan"))
    return (100 - (100 / (1 + rs))).fillna(100)


def compute_atr_simple(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Simple rolling mean ATR — what composite.py currently uses."""
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def compute_atr_wilder(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR (EMA alpha=1/period) — TradingView standard."""
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return wilder_ema(tr, period)


def verify(symbol: str, as_of: date):
    print(f"\n{'='*60}")
    print(f"  Symbol : {symbol}")
    print(f"  Date   : {as_of}")
    print(f"{'='*60}")

    df = fetch_symbol(symbol, lookback_days=600, end=as_of)
    if df is None or df.empty:
        print("ERROR: No data fetched — check token and symbol name")
        return

    # Trim to on-or-before as_of
    df = df[df.index.date <= as_of]
    if df.empty:
        print(f"ERROR: No data on or before {as_of}")
        return

    last_date = df.index[-1].date()
    close = df['close']
    last_price = close.iloc[-1]

    print(f"\n  Data points : {len(df)}")
    print(f"  Last bar    : {last_date}  (Close = {last_price:.2f})")
    print(f"\n{'─'*60}")

    # ── EMA ──────────────────────────────────────────────────────
    ema20  = close.ewm(span=20,  adjust=False).mean().iloc[-1]
    ema50  = close.ewm(span=50,  adjust=False).mean().iloc[-1]
    ema100 = close.ewm(span=100, adjust=False).mean().iloc[-1]
    ema150 = close.ewm(span=150, adjust=False).mean().iloc[-1]

    print(f"\n  EMA (adjust=False — matches TradingView):")
    print(f"    EMA(20)  = {ema20:.2f}   {'✓ above' if last_price > ema20 else '✗ below'}")
    print(f"    EMA(50)  = {ema50:.2f}   {'✓ above' if last_price > ema50 else '✗ below'}")
    print(f"    EMA(100) = {ema100:.2f}  {'✓ above' if last_price > ema100 else '✗ below'}")
    print(f"    EMA(150) = {ema150:.2f}  {'✓ above' if last_price > ema150 else '✗ below'}")

    # ── RSI ──────────────────────────────────────────────────────
    rsi_wilder = compute_rsi_wilder(close).iloc[-1]
    rsi_cutler = compute_rsi_cutler(close).iloc[-1]

    print(f"\n  RSI(14):")
    print(f"    Wilder's (TradingView standard) = {rsi_wilder:.2f}")
    print(f"    Cutler's (what composite.py uses)= {rsi_cutler:.2f}")
    print(f"    Difference                       = {abs(rsi_wilder - rsi_cutler):.2f} pts  {'⚠️ Gap!' if abs(rsi_wilder - rsi_cutler) > 2 else '✓ OK'}")

    # ── ATR ──────────────────────────────────────────────────────
    atr_wilder = compute_atr_wilder(df).iloc[-1]
    atr_simple = compute_atr_simple(df).iloc[-1]
    atr_pct_diff = abs(atr_wilder - atr_simple) / atr_simple * 100 if atr_simple > 0 else 0

    print(f"\n  ATR(14):")
    print(f"    Wilder's (TradingView standard) = {atr_wilder:.2f}  ({atr_wilder/last_price*100:.2f}% of price)")
    print(f"    Simple   (what composite.py uses)= {atr_simple:.2f}  ({atr_simple/last_price*100:.2f}% of price)")
    print(f"    Difference                       = {atr_pct_diff:.1f}%  {'⚠️ Gap!' if atr_pct_diff > 5 else '✓ OK'}")

    # ── MACD ─────────────────────────────────────────────────────
    exp1   = close.ewm(span=12, adjust=False).mean()
    exp2   = close.ewm(span=26, adjust=False).mean()
    macd   = exp1 - exp2
    signal = macd.ewm(span=9, adjust=False).mean()
    hist   = macd - signal

    print(f"\n  MACD(12,26,9) — matches TradingView:")
    print(f"    MACD Line   = {macd.iloc[-1]:.4f}")
    print(f"    Signal Line = {signal.iloc[-1]:.4f}")
    print(f"    Histogram   = {hist.iloc[-1]:.4f}  {'▲ bullish' if hist.iloc[-1] > 0 else '▼ bearish'}")

    # ── Summary ──────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  VERIFY ON TRADINGVIEW:")
    print(f"    Open TV → {symbol} → Daily chart")
    print(f"    Check on bar: {last_date}")
    print(f"    EMA(20)={ema20:.0f}  EMA(50)={ema50:.0f}  EMA(100)={ema100:.0f}")
    print(f"    RSI(14) should be ≈ {rsi_wilder:.1f}  (Wilder's)")
    print(f"    ATR(14) should be ≈ {atr_wilder:.2f}  (Wilder's)")
    print(f"    MACD Hist = {hist.iloc[-1]:.4f}")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Verify indicator values vs TradingView")
    parser.add_argument("symbol", help="NSE symbol e.g. RELIANCE")
    parser.add_argument("--date", default=None, help="As-of date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.date) if args.date else date.today()
    verify(args.symbol, as_of)


if __name__ == "__main__":
    main()
