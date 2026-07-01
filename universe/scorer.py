"""
Universe scorer — 6-factor composite score for each candidate.

Factors (all percentile-ranked cross-sectionally):
  1. rs_momentum_6m       (25%) — 6M return relative to Nifty50
  2. rs_momentum_3m       (20%) — 3M return relative to Nifty50
  3. breakout_readiness   (20%) — close/52wk-high × 10d momentum (stage 2 proxy)
  4. momentum_consistency (15%) — Sharpe of 26 weekly returns (trend smoothness)
  5. volume_quality       (10%) — log(avg daily turnover Cr) + circuit-breaker penalty
  6. volatility_atr       (10%) — 1/(true ATR%) — lower vol = higher score

Why breakout_readiness > momentum_1m:
  - 1M return is noisy: one bad week erases 3 good ones
  - close/52wk-high captures the same information smoothly
  - Near-52wk-high stocks are in "Stage 2 uptrend" (Weinstein) — best entry zone

Why momentum_consistency > raw momentum only:
  - End-to-end 6M return misses trajectory
  - Sharpe of weekly returns rewards smooth, steady trends
  - A stock that rose 1%/week for 26 weeks is a better candidate than
    one that spiked 30% and gave back 10% (same 6M return, different risk)

Why true ATR > std(close):
  - std(close) ignores gaps and intraday range
  - True ATR = mean(max(H-L, |H-prev_C|, |L-prev_C|)) — captures all volatility sources

Does NOT touch strategy logic.
"""
import logging
from typing import List, Dict, Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_WEIGHTS = {
    "rs_momentum_6m":       0.25,
    "rs_momentum_3m":       0.20,
    "breakout_readiness":   0.20,
    "momentum_consistency": 0.15,
    "volume_quality":       0.10,
    "volatility_atr":       0.10,
}

_DEFAULT_LOOKBACK = {
    "momentum_6m_days":     126,
    "momentum_3m_days":      63,
    "breakout_days":        252,
    "momentum_10d_days":     10,
    "consistency_weeks":     26,
    "volume_avg_days":       90,
    "atr_period":            14,
    "circuit_breaker_atr_pct": 6.0,
}


class UniverseScorer:
    """
    Score a universe of stocks. All factors percentile-ranked cross-sectionally.

    Usage:
        scorer = UniverseScorer(config)
        results = scorer.score_all(price_data, index_data)
        # results: list sorted by composite_score desc, each item is a dict:
        #   {symbol, rs_momentum_6m, rs_momentum_3m, breakout_readiness,
        #    momentum_consistency, volume_quality, volatility_atr,
        #    composite_score, score_percentile}
    """

    def __init__(self, config: Dict):
        scoring_cfg = config.get("scoring", {})
        self.weights = {**_DEFAULT_WEIGHTS, **scoring_cfg.get("weights", {})}
        self.lookback = {**_DEFAULT_LOOKBACK, **scoring_cfg.get("lookback", {})}
        total = sum(self.weights.values())
        if total > 0:
            self.weights = {k: v / total for k, v in self.weights.items()}

    # ── Public API ──────────────────────────────────────────────────────────

    def score_all(self, price_data: Dict[str, pd.DataFrame],
                  index_data: pd.DataFrame,
                  ipo_symbols: Optional[set] = None,
                  ipo_young_symbols: Optional[set] = None) -> List[Dict]:
        """
        Compute composite scores.

        price_data: {symbol: DataFrame with columns: close, high, low, volume}
        index_data: DataFrame with 'close' column (Nifty50)
        ipo_symbols: symbols in IPO watch (no special treatment currently)
        ipo_young_symbols: symbols < lock_in_days old — exclude 3M/1M momentum
                           (listing gains inflate short-term scores)
        """
        ipo_young = ipo_young_symbols or set()
        raw_factors: Dict[str, Dict[str, float]] = {}

        for symbol, df in price_data.items():
            if df is None or df.empty or "close" not in df.columns:
                continue
            try:
                factors = self._compute_factors(df, index_data,
                                                is_young_ipo=(symbol in ipo_young))
                raw_factors[symbol] = factors
            except Exception as e:
                logger.debug("Score failed for %s: %s", symbol, e)

        if not raw_factors:
            logger.warning("[Scorer] No factors computed — empty price data?")
            return []

        return self._percentile_rank_and_combine(raw_factors)

    # ── Factor Computation ──────────────────────────────────────────────────

    def _compute_factors(self, df: pd.DataFrame, index_df: pd.DataFrame,
                          is_young_ipo: bool = False) -> Dict[str, float]:
        """Compute raw (unranked) factor values for one stock."""
        # Normalise column names
        df = self._normalise_columns(df)
        close  = df["close"]
        high   = df.get("high",   close)   # fallback to close if no OHLCV
        low    = df.get("low",    close)
        volume = df.get("volume", pd.Series(0.0, index=close.index))

        factors: Dict[str, float] = {}

        # 1. Relative momentum 6M
        factors["rs_momentum_6m"] = self._relative_momentum(
            close, index_df, self.lookback["momentum_6m_days"]
        )

        # 2. Relative momentum 3M
        #    Young IPOs: listing gains inflate 3M → use 0 (neutral, not penalised)
        if is_young_ipo:
            factors["rs_momentum_3m"] = 0.0
        else:
            factors["rs_momentum_3m"] = self._relative_momentum(
                close, index_df, self.lookback["momentum_3m_days"]
            )

        # 3. Breakout readiness (replaces momentum_1m)
        factors["breakout_readiness"] = self._breakout_readiness(close)

        # 4. Momentum consistency (Sharpe of weekly returns)
        factors["momentum_consistency"] = self._momentum_consistency(df)

        # 5. Volume quality (with circuit-breaker penalty)
        factors["volume_quality"] = self._volume_quality(
            close, high, low, volume
        )

        # 6. Volatility (true ATR-based, lower = higher score)
        factors["volatility_atr"] = self._volatility_atr(close, high, low)

        return factors

    def _normalise_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensure lowercase column names and DatetimeIndex."""
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        # If there's a 'date' column but no DatetimeIndex, set it as index
        if not isinstance(df.index, pd.DatetimeIndex):
            if "date" in df.columns:
                df = df.set_index(pd.to_datetime(df["date"]))
            elif "index" in df.columns:
                try:
                    df = df.set_index(pd.to_datetime(df["index"]))
                except Exception:
                    pass
        return df

    def _relative_momentum(self, close: pd.Series, index_df: pd.DataFrame,
                            days: int) -> float:
        """(stock return - index return) over `days` trading days."""
        if len(close) < days + 5:
            return 0.0
        stock_ret = (close.iloc[-1] / close.iloc[-(days + 1)] - 1) * 100

        if index_df is not None and not index_df.empty:
            idx_close = self._get_close(index_df)
            if idx_close is not None and len(idx_close) >= days + 5:
                idx_ret = (idx_close.iloc[-1] / idx_close.iloc[-(days + 1)] - 1) * 100
                return stock_ret - idx_ret
        return stock_ret

    def _breakout_readiness(self, close: pd.Series) -> float:
        """
        Proximity to 52-week high, scaled by recent momentum.
        Formula: (close / max_close_252d) * (1 + max(0, perf_10d/100))

        - Score = 1.0 means exactly at 52-week high with no 10d momentum
        - Score > 1.0 means new 52-week high with upward momentum (best)
        - Score = 0.5 means 50% below 52-week high (stage 1 or downtrend)
        """
        days_52w = min(self.lookback["breakout_days"], len(close))
        days_10d = min(self.lookback["momentum_10d_days"], len(close) - 1)

        if days_52w < 20 or days_10d < 5:
            return 0.0

        high_52w = close.tail(days_52w).max()
        if high_52w <= 0:
            return 0.0

        proximity = close.iloc[-1] / high_52w  # 0.0 to 1.0 (can slightly exceed 1.0 on new high)

        perf_10d = (close.iloc[-1] / close.iloc[-(days_10d + 1)] - 1) * 100
        momentum_mult = 1.0 + max(0.0, perf_10d / 100.0)  # only reward, never penalise

        return float(proximity * momentum_mult * 100)  # scale to ~0–120

    def _momentum_consistency(self, df: pd.DataFrame) -> float:
        """
        Sharpe ratio of weekly returns over 26 weeks.
        Measures TRAJECTORY quality, not just end-to-end return.
        A stock rising 1%/week for 26 weeks scores higher than
        one that spiked 30% and gave back 10%.

        Returned value clamped to [-3, 5] then shifted so 0 = neutral.
        """
        n_weeks = self.lookback["consistency_weeks"]
        close = df.get("close") if "close" in df.columns else df.iloc[:, 0]

        if not isinstance(df.index, pd.DatetimeIndex):
            return 0.0
        if len(close) < n_weeks * 3:  # need at least 3× period for resampling
            return 0.0

        try:
            weekly_close = close.resample("W-FRI").last().dropna()
            weekly_ret = weekly_close.pct_change().dropna().tail(n_weeks)
            if len(weekly_ret) < 8:
                return 0.0
            mean_ret = weekly_ret.mean()
            std_ret  = weekly_ret.std()
            if std_ret <= 0:
                return 0.0
            sharpe = mean_ret / std_ret * np.sqrt(52)
            return float(np.clip(sharpe, -3, 5) + 3)  # shift: [-3,5] → [0,8]
        except Exception:
            return 0.0

    def _volume_quality(self, close: pd.Series, high: pd.Series,
                         low: pd.Series, volume: pd.Series) -> float:
        """
        Log(avg daily turnover in Crore) over 90 days.
        Applies circuit-breaker penalty: if stock regularly hits intraday limits
        (H-L range > threshold), the apparent liquidity is misleading.
        """
        days = self.lookback["volume_avg_days"]
        cb_threshold = self.lookback["circuit_breaker_atr_pct"] / 100.0

        if volume is None or len(volume) < 20:
            return 0.0

        # Align series
        n = min(len(close), len(volume), days)
        close_t = close.tail(n)
        vol_t   = volume.tail(n)
        high_t  = high.tail(n) if high is not None else close_t
        low_t   = low.tail(n)  if low  is not None else close_t

        turnover = close_t * vol_t
        avg_turnover = turnover.mean()
        if avg_turnover <= 0:
            return 0.0

        base_score = float(np.log1p(avg_turnover / 1e7))  # Crore scale

        # Circuit-breaker penalty: measure avg intraday range as % of close
        intraday_range_pct = ((high_t - low_t) / close_t.clip(lower=0.01)).mean()
        if intraday_range_pct > cb_threshold:
            # Penalty proportional to excess above threshold
            excess = (intraday_range_pct - cb_threshold) / cb_threshold
            penalty = min(0.5, excess * 0.25)  # max 50% discount
            base_score *= (1 - penalty)

        return base_score

    def _volatility_atr(self, close: pd.Series, high: pd.Series,
                         low: pd.Series) -> float:
        """
        True ATR as % of price. Score = 1/ATR_pct.
        Uses max(H-L, |H-prev_C|, |L-prev_C|) — captures gaps and wicks.
        Lower volatility = higher score = better candidate.
        """
        period = self.lookback["atr_period"]
        if len(close) < period + 5:
            return 0.0

        n = min(len(close), len(high), len(low), period * 3)
        c = close.tail(n).values
        h = high.tail(n).values if high is not None else c
        l = low.tail(n).values  if low  is not None else c

        if len(c) < period + 2:
            return 0.0

        # True range for each bar
        tr = np.maximum(
            h[1:] - l[1:],
            np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1]))
        )
        atr = np.mean(tr[-period:])
        last_close = c[-1]
        if last_close <= 0:
            return 0.0

        atr_pct = (atr / last_close) * 100
        atr_pct = np.clip(atr_pct, 0.2, 10.0)  # prevent division extremes
        return float(1.0 / atr_pct * 100)  # normalise to ~10–500 range

    @staticmethod
    def _get_close(df: pd.DataFrame) -> Optional[pd.Series]:
        if df is None or df.empty:
            return None
        cols = [c.lower() for c in df.columns]
        if "close" in cols:
            return df[df.columns[cols.index("close")]]
        if "Close" in df.columns:
            return df["Close"]
        return None

    # ── Percentile Ranking ──────────────────────────────────────────────────

    def _percentile_rank_and_combine(self,
                                      raw: Dict[str, Dict[str, float]]) -> List[Dict]:
        """
        Cross-sectionally percentile-rank each factor, combine with weights.
        Output scores in [0, 100].
        """
        symbols = list(raw.keys())
        factor_names = list(self.weights.keys())

        # Build factor matrix — fill missing factors with NaN
        rows = {}
        for sym in symbols:
            row = raw[sym]
            rows[sym] = {f: row.get(f, np.nan) for f in factor_names}

        matrix = pd.DataFrame(rows).T  # (n_symbols, n_factors)

        # Percentile rank each factor column; NaN stocks get rank 0
        ranked = matrix.rank(pct=True, na_option="bottom") * 100.0

        # Weighted composite
        weights_s = pd.Series(self.weights)
        composite = (ranked * weights_s).sum(axis=1)

        # Percentile rank the composite
        composite_pct = composite.rank(pct=True) * 100.0

        results = []
        for sym in symbols:
            row = {
                "symbol":           sym,
                "composite_score":  round(float(composite.loc[sym]), 2),
                "score_percentile": round(float(composite_pct.loc[sym]), 2),
            }
            for f in factor_names:
                row[f] = round(float(ranked.loc[sym, f]), 2)
            results.append(row)

        results.sort(key=lambda r: r["composite_score"], reverse=True)
        return results
