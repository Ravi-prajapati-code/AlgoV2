#!/usr/bin/env python3
"""
Pre-market indicator/regime precompute for the MAIN live strategy. Runs
once per weekday, well before the 14:55 IST execute cron:

  30 9 * * 1-5 cd /home/ubuntu/AlgoV2 && flock -n /tmp/algov2_main_precompute.lock \
    -c "timeout 2400 .venv/bin/python scripts/precompute_main_indicators.py" \
    >> logs/main_precompute.log 2>&1

Split out after the 2026-08-07 incident: the 14:55 execute cron hit 7
consecutive 45s fetch stalls across the ~full symbol universe and was
SIGTERM-killed by the cron's own `timeout 900` wrapper mid-fetch -- zero
orders placed, zero exit/trailing-stop checks that day. The provider-level
stall cause is fixed (data/providers/upstox_provider.py rebuilds its
session after a stall), but the structural risk remains: the slow,
stall-prone fetch/scoring phase and the time-critical order-placement
phase shared one 900s budget right up against market close.

Computes exactly what runner/daily_runner.py::run() computed inline at
lines 469-501 before this split: fetch_all over the full universe,
compute_rs_for_all, compute_all -- provably T-1-close data regardless of
what time this runs, since Upstox never returns a same-day candle
mid-session (confirmed via live DB query, not assumption). regime/
market_bullish/strong_bull are stored too, but as diagnostics only -- the
14:55 execute step always recomputes those fresh from its own cheap
single-symbol index fetch and only logs a drift warning against this row.

Does NOT touch the broker, sync positions, compute live cash, or generate
signals -- those depend on live broker state and stay in
runner/daily_runner.py::run() at 14:55, unchanged.

Staggered to 09:30, after momentum_atr's own 08:50 precompute (`timeout
1800`, worst-case ends ~09:20) so the two strategies' full-universe fetch
loops don't compete for the same shared Upstox account's connections/rate
limit at once.

Own lock file, /tmp/algov2_main_precompute.lock -- never
/tmp/algov2_runner.lock (the 14:55 execute cron's lock) or either
momentum_atr lock file (docs/56's flock-race incident is exactly the
failure mode this avoids).
"""
import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s -- %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("MainPrecompute")


def _env_prefix() -> str:
    from config.settings import IS_PRODUCTION
    return "" if IS_PRODUCTION else "[LOCAL TEST] "


def _alert_abort(today: date, reason: str) -> None:
    try:
        import html
        from notifications.telegram import send_message
        send_message(
            f"{_env_prefix()}\U0001F6D1 <b>main strategy PRECOMPUTE ABORTED -- {today}</b>\n"
            f"Reason: {html.escape(str(reason))}\n\n"
            f"The 14:55 EXECUTE cron will find no precomputed indicators and "
            f"abort too, unless manually recovered (re-run this script, or "
            f"'main.py run --live --force-live-fetch' as an emergency escape "
            f"hatch)."
        )
    except Exception as e:
        logger.warning("[main] Failed to send precompute abort alert: %s", e)


def main() -> int:
    today = date.today()

    from runner.daily_runner import _is_market_holiday
    if _is_market_holiday(today):
        logger.info("=== main precompute: market holiday on %s -- no run. ===", today)
        return 0

    from db.repository import init_db, load_precompute_indicators, save_precompute_indicators, record_sla_checkpoint
    init_db()

    if load_precompute_indicators(today) is not None:
        logger.info("[main] precompute indicators for %s already computed -- skipping.", today)
        return 0

    from data.fetcher import fetch_all, fetch_index, get_and_reset_stall_count
    from data.universe import get_all_symbols
    from indicators.composite import compute_all
    from strategy.regime import detect_regime, is_buy_allowed, is_strong_bull, MIN_INDEX_CANDLES
    from strategy.relative_strength import compute_rs_for_all
    from strategy.defensive_portfolio import ALL_DEFENSIVE_SYMBOLS
    from config.settings import MARKET_INDEX_SYMBOL

    get_and_reset_stall_count()  # clear any count left over from a prior run/import

    try:
        logger.info("[main] Fetching market index %s...", MARKET_INDEX_SYMBOL)
        index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=MIN_INDEX_CANDLES + 50, live_mode=True)
        index_candles = len(index_df) if index_df is not None and not index_df.empty else 0

        if index_candles < 20:
            logger.warning("[main] Insufficient market data (%d/20). Defaulting to BULL.", index_candles)
            regime, market_bullish, strong_bull = "BULL", True, False
        else:
            regime = detect_regime(index_df)
            market_bullish = is_buy_allowed(regime)
            strong_bull = regime == "BULL" and is_strong_bull(index_df)
        logger.info("[main] Regime: %s | BUY entries %s", regime, "ALLOWED" if market_bullish else "BLOCKED")

        symbols = list(dict.fromkeys(get_all_symbols() + ALL_DEFENSIVE_SYMBOLS))
        logger.info("[main] scoring %d universe symbols...", len(symbols))
        data = fetch_all(symbols, live_mode=True)
        stalls = get_and_reset_stall_count()
        if stalls:
            logger.warning("[main] %d symbol(s) hit the 45s fetch stall this run", stalls)

        if not data:
            detail = f"no stock data fetched (data provider failure), {stalls} stalls"
            record_sla_checkpoint(today, "PRECOMPUTE", "ABORTED", detail)
            _alert_abort(today, detail)
            logger.error("[main] %s -- aborting", detail)
            return 1

        min_required = max(10, len(symbols) // 2)  # need at least 50% of universe
        if len(data) < min_required:
            detail = f"only {len(data)}/{len(symbols)} symbols fetched (need >={min_required}), {stalls} stalls"
            record_sla_checkpoint(today, "PRECOMPUTE", "ABORTED", detail)
            _alert_abort(today, detail)
            logger.error("[main] %s -- aborting to avoid scoring on partial universe", detail)
            return 1

        MIN_WARMUP = 450
        thin_symbols = [sym for sym, df in data.items() if sym != MARKET_INDEX_SYMBOL and len(df) < MIN_WARMUP]
        if thin_symbols:
            logger.warning(
                "[Warmup] %d symbol(s) have < %d days of history — EMA(150)/regime may be inaccurate: %s",
                len(thin_symbols), MIN_WARMUP, thin_symbols[:10],
            )

        rs_data = compute_rs_for_all(data, index_df)
        indicators = compute_all(data, rs_data=rs_data)

        save_precompute_indicators(
            today, indicators, regime, market_bullish, strong_bull, index_candles, stalls=stalls,
        )
        logger.info("[main] precompute complete: %d scored, regime=%s", len(indicators), regime)
        record_sla_checkpoint(
            today, "PRECOMPUTE", "OK",
            f"{len(indicators)} scored, regime={regime}, {stalls} stalls",
        )
    except Exception as e:
        logger.exception("[main] precompute crashed")
        record_sla_checkpoint(today, "PRECOMPUTE", "CRASHED", str(e))
        _alert_abort(today, f"Unhandled exception: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
