# 31. EMA50 Mislabel Fix

## What was wrong

`indicators/composite.py`'s `compute_indicators()` set the `ema_50` key
in the indicators dict to `trend["ema_slow"]` — a value computed by
`indicators/trend.py` at period `EMA_SLOW`, which defaults to **100**
(`config/settings.py:84`, `entry_cfg.get('ema_slow', 100)`) and was
never overridden in `config/strategy_config.yaml` or `.env`.

So `indicators['ema_50']` and `indicators['ema_100']` were both EMA(100)
of close — one rounded to 2dp (via `trend.py`'s `round(last_ema_s, 2)`),
one not. Verified on live cached data (RELIANCE.NS, 2026-07-13):
`ema_50 = 1355.85`, `ema_100 = 1355.8497793252834` — same value.

## Why it mattered

Two live mechanisms read `ema_50` expecting a genuine EMA(50):

- `strategy/entry.py`'s trend-alignment gate:
  `close > ema_50 AND ema_50 > ema_100`. With both sides at the same
  period, `ema_50 > ema_100` reduced to "does the rounded value happen
  to exceed the unrounded one" — rounding noise, not a real medium-vs-
  long-term trend signal.
- `strategy/signals.py`'s `TREND_BREAK` exit:
  `current_price < ema_50` for 2 consecutive days. This was actually
  checking price against EMA(100), a much slower-moving average than
  the name implies — exits fired later than intended.

`backtest/engine.py` was never affected — it computes
`ema50 = close.ewm(span=50, ...)` independently, a genuine EMA(50), and
has since backtest and live indicator pipelines were built separately
(same underlying pattern as `docs/28_Software_Truth_Audit.md`: the two
pipelines silently diverged). Every `robustness_gate.py` verdict this
project has produced was validated against the **correct** EMA50/EMA100
separation — it was live that had drifted from what was tested.

## The fix

`indicators/composite.py` now computes `ema_50` independently at
`close.ewm(span=50, adjust=False, min_periods=1).mean()`, matching
`backtest/engine.py`'s formula exactly — same as the existing
`ema_100`/`ema_150`/`ema_200` treatment. The legacy `ema_fast`/
`ema_slow` ML-feature keys are untouched (still `trend["ema_fast"]`/
`trend["ema_slow"]`) — `ML_ENABLED=False` already, with an existing
comment anticipating "retrain needed after indicator fixes"
(`config/settings.py:194`), so no live model is affected either way.

## Why no new robustness-gate run was needed for this fix

The fix makes live match backtest's already-gated logic — it doesn't
introduce new behavior, it removes a live-only divergence. Every past
"proven, keep" verdict for the EMA trend-alignment gate and
`TREND_BREAK` exit (`docs/24_Rejected_Forever.md`) was earned against
the correct EMA50/EMA100 separation in backtest; this fix is what makes
live finally run that same logic. Test suite: 90/90 passing after the
change.

## What's still open

This fix changes real live entry/exit trigger timing going forward
(the trend-alignment gate will now actually gate, and `TREND_BREAK`
will fire against a faster-moving average than before). Needs the same
live-server dry-run treatment as `docs/30`'s Step 2/5 before deploy —
see that doc's Phase 4 checklist, same blocker (no SSH access as of
2026-07-13).
