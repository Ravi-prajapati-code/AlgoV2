"""
Live score adapter for the momentum x ATR strategy -- ports
scripts/momentum_atr_experiment/engine.py:compute_scores() (FULL mode)
verbatim, substituting the backtest's static parquet read for a live
daily-bar fetch via data.fetcher.fetch_all(live_mode=True). Universe is
the real live universe (data.universe.get_all_symbols()), not the
backtest's 112-symbol parquet-coverage artifact.
"""
from typing import Dict, List, Optional, Tuple

import pandas as pd

MIN_HISTORY_DAYS = 50  # SMA50 warmup floor -- matches data.fetcher.fetch_all's own >=50 filter
MIN_AVG_VOLUME_20D = 300_000  # 20-day avg traded shares floor -- docs/61 addendum (2026-09-02),
# variant U_VOL30: TEST CAGR 91.2%->97.4%, Sharpe 1.61->1.68, MDD -36.7%->-33.9%, no stress-scenario
# breach beyond the +/-0.10 PF gate tolerance vs the unfiltered baseline. Uses the same rolling-
# mean-on-the-latest-complete-bar convention as the score itself (see compute_live_scores'
# docstring) -- no extra shift needed, live already only ever sees yesterday's close.
SMA200_WINDOW = 200  # docs/79 trend gate; see config.settings.MOMENTUM_ATR_SMA200_FILTER


def compute_live_scores(symbols: List[str], diagnostics: Optional[Dict[str, dict]] = None
                        ) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Returns (scores, closes): {symbol: latest score}, {symbol: latest close}.
    Only the most recent COMPLETE daily bar is used -- at a pre-market cron
    run that is necessarily yesterday's close, which is what makes the
    live daily decision naturally reproduce the backtest's
    decide-on-close-N/execute-on-open-N+1 lag (see momentum_atr/execution.py).
    Symbols with insufficient history, below the 20d volume floor, or a NaN
    latest score are omitted.

    When MOMENTUM_ATR_SMA200_FILTER is on (docs/79), the score is also forced
    to 0 unless the latest close is above the symbol's own 200-session SMA.
    A symbol with fewer than 200 bars has no SMA200 and therefore scores 0
    (same as the backtest). A score of 0 is still returned (not omitted), so
    a held position that drops below SMA200 is rank-exited exactly like one
    that drops below SMA50.

    `diagnostics` (optional, default None = old behaviour): if a dict is
    passed, it is filled with one entry per requested symbol --
    {"status": "scored" | "no_data" | "short_history" | "volume_floor" |
    "nan_score", "last_date": date of the latest bar or None, "close":
    latest close or None}. It never changes the returned scores/closes; it
    only lets a caller tell "genuinely ineligible" (volume_floor) apart from
    "no usable data" (no_data/short_history/nan_score)."""
    from config.settings import MOMENTUM_ATR_SMA200_FILTER
    from data.fetcher import fetch_all

    data = fetch_all(symbols, live_mode=True)
    scores: Dict[str, float] = {}
    closes: Dict[str, float] = {}

    def _note(sym, status, df=None):
        if diagnostics is None:
            return
        last_date = close_px = None
        if df is not None and len(df):
            last_date = pd.Timestamp(df.index[-1]).date()
            close_px = float(df["close"].iloc[-1])
        diagnostics[sym] = {"status": status, "last_date": last_date, "close": close_px}

    if diagnostics is not None:
        for s in symbols:
            if s not in data or data[s] is None or len(data[s]) == 0:
                _note(s, "no_data")
    for s, df in data.items():
        if df is None or len(df) == 0:
            continue
        if len(df) < MIN_HISTORY_DAYS:
            _note(s, "short_history", df.sort_index())
            continue
        df = df.sort_index()
        avg_vol_20d = df["volume"].rolling(20).mean().iloc[-1]
        if pd.isna(avg_vol_20d) or avg_vol_20d < MIN_AVG_VOLUME_20D:
            _note(s, "volume_floor", df)
            continue
        close = df["close"]
        sma50 = close.rolling(50).mean()
        momentum = (close - sma50) / sma50 * 100

        high_low = df["high"] - df["low"]
        high_close = (df["high"] - close.shift()).abs()
        low_close = (df["low"] - close.shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0 / 14, adjust=False).mean()
        atr_pct = atr / close * 100

        eligible = momentum > 0
        if MOMENTUM_ATR_SMA200_FILTER:
            sma200 = close.rolling(SMA200_WINDOW).mean()
            eligible = eligible & (close > sma200)  # NaN SMA200 (<200 bars) compares False
        score = (momentum * atr_pct).where(eligible, 0.0)
        last_score = score.iloc[-1]
        if pd.isna(last_score):
            _note(s, "nan_score", df)
            continue
        scores[s] = float(last_score)
        closes[s] = float(close.iloc[-1])
        _note(s, "scored", df)
    return scores, closes


def rank_symbols(scores: Dict[str, float]) -> List[str]:
    """Descending rank by score, score>0 only -- mirrors engine.py's
    default_rank(): s = s[s > 0].sort_values(ascending=False)."""
    eligible = {s: v for s, v in scores.items() if v > 0}
    return [s for s, _ in sorted(eligible.items(), key=lambda kv: kv[1], reverse=True)]


def build_ranking_inputs(universe: List[str], held: List[str]
                         ) -> Tuple[List[str], Dict[str, float], Dict[str, object]]:
    """Ranking input for the 08:50 precompute (docs/83).

    Current holdings are ALWAYS scored, even if the universe manager has
    removed them from `universe` -- universe membership is a new-entry
    concern, not an exit signal. A held symbol then lands in one of:

      scored normally              -> ranked like any other symbol; the
                                      09:17 rank rule decides (unchanged)
      volume_floor (real data)     -> close kept in `closes`, no score: a
                                      genuine strategy-rule ineligibility,
                                      rank-exited exactly as before
      no_data / short_history /    -> left OUT of `closes`: 09:17 treats a
      nan_score / stale last bar      held symbol missing from closes as
                                      DATA_MISSING and holds (alerted)
                                      instead of reading it as rank 10**9

    "Stale" = the symbol's last bar is older than the most common last-bar
    date among all scored symbols (i.e. it did not get today's input bar).
    Returns (ranked, closes, report)."""
    from collections import Counter

    uni = list(universe)
    uni_set = set(uni)
    outside = [h for h in held if h not in uni_set]
    diag: Dict[str, dict] = {}
    scores, closes = compute_live_scores(uni + outside, diagnostics=diag)

    dates = Counter(d["last_date"] for d in diag.values() if d.get("status") == "scored" and d.get("last_date"))
    ref_date = dates.most_common(1)[0][0] if dates else None

    missing: Dict[str, str] = {}
    volume_floor: List[str] = []
    for h in held:
        d = diag.get(h, {"status": "no_data", "last_date": None, "close": None})
        st = d.get("status")
        if st == "volume_floor" and d.get("close"):
            closes[h] = d["close"]
            volume_floor.append(h)
        elif st in ("no_data", "short_history", "nan_score"):
            missing[h] = st
        elif st == "scored" and ref_date and d.get("last_date") and d["last_date"] < ref_date:
            scores.pop(h, None)
            closes.pop(h, None)
            missing[h] = f"stale_last_bar_{d['last_date']}_vs_{ref_date}"

    # Non-held symbols: a stale last bar must never be ranked -- its frozen
    # close would compete against fresh ones (2026-10-21: HFCL scored on a
    # 09-25 close for 4 weeks). no_data symbols are already unranked; both
    # are reported so a broken feed is visible instead of silent.
    held_set = set(held)
    stale_unheld: Dict[str, str] = {}
    no_data_unheld: List[str] = []
    for s, d in diag.items():
        if s in held_set:
            continue
        if d.get("status") == "no_data":
            no_data_unheld.append(s)
        elif d.get("status") == "scored" and ref_date and d.get("last_date") and d["last_date"] < ref_date:
            scores.pop(s, None)
            closes.pop(s, None)
            stale_unheld[s] = str(d["last_date"])
    report = {"held_outside_universe": outside, "held_data_missing": missing,
              "held_volume_floor": volume_floor, "reference_bar_date": ref_date,
              "stale_unheld": stale_unheld, "no_data_unheld": sorted(no_data_unheld)}
    return rank_symbols(scores), closes, report
