"""
Signal scoring — composite rank used for entry ranking/selection.
"""

from typing import Dict


def score_signal(ind: Dict) -> float:
    """
    Returns composite_rank (RS x ATR%) as the score (0-100 cross-sectional percentile).
    High score = high momentum leader with high volatility - the explosive movers.
    """
    return float(ind.get("composite_rank", 0) or 0)
