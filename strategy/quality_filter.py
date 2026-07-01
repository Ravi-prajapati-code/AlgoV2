"""
Institutional Quality Filter — filters for high-quality, liquid companies.
Uses F&O and Nifty 100 membership as proxies for institutional safety.
"""

from typing import Tuple, Dict

# Institutional/F&O Proxy list (High quality, liquid)
_INSTITUTIONAL_UNIVERSE = {
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "BHARTIARTL.NS",
    "HINDUNILVR.NS", "ITC.NS", "LT.NS", "SBIN.NS", "KOTAKBANK.NS", "AXISBANK.NS",
    "BAJFINANCE.NS", "ASIANPAINT.NS", "MARUTI.NS", "TITAN.NS", "SUNPHARMA.NS",
    "ULTRACEMCO.NS", "ADANIENT.NS", "JSWSTEEL.NS", "TATASTEEL.NS", "NTPC.NS",
    "POWERGRID.NS", "ONGC.NS", "M&M.NS", "TATAMOTORS.NS", "HCLTECH.NS", "COALINDIA.NS",
    "BAJAJFINSV.NS", "LTIM.NS", "HINDALCO.NS", "GRASIM.NS", "ADANIPORTS.NS", "NESTLEIND.NS",
    "DRREDDY.NS", "JSWENERGY.NS", "BEL.NS", "HAL.NS", "ABB.NS", "SIEMENS.NS", "TRENT.NS",
    "VBL.NS", "DLF.NS", "CHOLAFIN.NS", "POLYCAB.NS", "PERSISTENT.NS", "CUMMINSIND.NS"
}

def passes_quality_filter(symbol: str, ind: dict) -> Tuple[bool, str]:
    """
    Institutional Quality Check:
    1. Membership in Institutional Universe (Optional but preferred)
    2. Liquidity: Turnover > 5 Cr (Increased for higher quality)
    3. Price Stability: Avoid stocks below ₹100 (Penny stock protection)
    """
    # 1. Liquidity Floor (Absolute requirement)
    # Increased to 5 Crore for the 'Plus' strategy
    if ind.get('turnover', 0) < 50_000_000:
        return False, "Low Liquidity (<5 Cr Turnover)"

    # 2. Penny Stock Protection
    if ind.get('close', 0) < 100:
        return False, "Penny Stock Filter (< ₹100)"

    # 3. Quality Proxy
    # We allow stocks outside the universe, but we flag them as 'Tier 2'
    is_tier1 = symbol in _INSTITUTIONAL_UNIVERSE
    
    return True, "Tier-1 Institutional" if is_tier1 else "Tier-2 Midcap"

def get_event_blackout(symbol: str) -> bool:
    """
    Placeholder for Earnings/Event check.
    In production, this would query an earnings calendar API.
    Returns True if stock is in a blackout window (e.g., earnings in < 3 days).
    """
    # Currently default to False (no blackout) until data source is integrated
    return False
