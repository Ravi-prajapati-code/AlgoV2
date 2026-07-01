"""
Hybrid Scorer — Alpha-Tuned sizing factors.
Prioritizes high-conviction momentum signals with larger allocations.
"""

from typing import Dict

# ── Score thresholds → size factors ───────────────────────────────────────
# RS Rank 95+ gets 120% size factor (Momentum Turbo)
# RS Rank 90+ gets 100% (High Conviction)
# RS Rank 70+ gets 60% (Trend Anchor)
SCORE_BUCKETS = [
    (95, 1.20),   # TURBO
    (90, 1.00),   # HIGH
    (80, 0.80),   # GOOD
    (70, 0.60),   # ANCHOR
    (0,  0.00),   # REJECT
]

def score_signal(ind: Dict) -> float:
    """
    Returns composite_rank (RS × ATR%) as the score (0-100 cross-sectional percentile).
    High score = high momentum leader with high volatility — the explosive movers.
    """
    return float(ind.get("composite_rank", 0) or 0)

def score_to_size_factor(score: float) -> float:
    """
    Returns a multiplier for position sizing based on signal conviction.
    """
    for threshold, factor in SCORE_BUCKETS:
        if score >= threshold:
            return factor
    return 0.0

def score_label(score: float) -> str:
    if score >= 95: return "TURBO"
    if score >= 90: return "HIGH"
    if score >= 80: return "GOOD"
    if score >= 70: return "ANCHOR"
    return "WEAK"
