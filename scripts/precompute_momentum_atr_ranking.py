#!/usr/bin/env python3
"""
Pre-market ranking cron for the standalone momentum x ATR live strategy
(docs/57, docs/58; plan velvet-cooking-minsky). Runs once per weekday well
before market open (~08:00 IST):

  0 8 * * 1-5 cd /home/ubuntu/AlgoV2 && flock -n /tmp/algov2_momentum_atr_precompute.lock \
    -c "timeout 1800 .venv/bin/python scripts/precompute_momentum_atr_ranking.py" \
    >> logs/momentum_atr_precompute.log 2>&1

Split out from scripts/run_momentum_atr_live.py (the 9:17 execution cron)
after the 2026-08-07 first live run took ~10 minutes to score the full
~509-symbol universe (cold OHLCV cache, per-symbol fetch stalls) --
correctness-wise harmless (it finished inside the 900s budget that day),
but it meant order placement didn't start until minutes after 9:17, and a
worse cache day could blow the timeout and silently skip the run. Scoring
uses only the prior COMPLETE daily bar (see momentum_atr/scoring.py) which
is already fixed the moment this runs, however early -- computing it at
08:00 instead of 09:17 changes nothing about which bar gets used, only
gives the slow part a large buffer before the fast part (order placement)
needs to start exactly on time.

Own lock file and own log, never shared with run_momentum_atr_live.py's
-- a stuck precompute must never block or race the execution cron.
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
logger = logging.getLogger("MomentumATRPrecompute")


def _env_prefix() -> str:
    from config.settings import IS_PRODUCTION
    return "" if IS_PRODUCTION else "[LOCAL TEST] "


def _alert_abort(today: date, reason: str) -> None:
    try:
        from notifications.telegram import send_message
        import html
        send_message(
            f"{_env_prefix()}\U0001F6D1 <b>momentum_atr PRECOMPUTE ABORTED -- {today}</b>\n"
            f"Reason: {html.escape(str(reason))}\n\n"
            f"The 9:17 execution cron will find no ranking for today and abort too."
        )
    except Exception as e:
        logger.warning("[momentum_atr] Failed to send precompute abort alert: %s", e)


def main() -> int:
    today = date.today()

    from runner.daily_runner import _is_market_holiday
    if _is_market_holiday(today):
        logger.info("=== momentum_atr precompute: market holiday on %s -- no run. ===", today)
        return 0

    from db import momentum_atr_repo as repo
    if repo.load_daily_ranking(today) is not None:
        logger.info("[momentum_atr] ranking for %s already computed -- skipping.", today)
        return 0

    try:
        from data.universe import get_all_symbols
        from momentum_atr.scoring import compute_live_scores, rank_symbols

        symbols = get_all_symbols()
        logger.info("[momentum_atr] scoring %d universe symbols...", len(symbols))
        scores, closes = compute_live_scores(symbols)
        if len(closes) < 20:
            _alert_abort(today, f"only {len(closes)} symbols scored (need a real universe)")
            logger.error("[momentum_atr] only %d symbols scored -- aborting", len(closes))
            return 1

        ranked = rank_symbols(scores)
        repo.save_daily_ranking(today, ranked, closes)
        logger.info(
            "[momentum_atr] precompute complete: %d scored, top-5 ranked: %s",
            len(closes), ranked[:5],
        )
    except Exception as e:
        logger.exception("[momentum_atr] precompute crashed")
        _alert_abort(today, f"Unhandled exception: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
