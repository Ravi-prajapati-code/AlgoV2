#!/usr/bin/env python3
"""
Walk-Forward Validator (E) — detect strategy decay.

Runs a rolling 6-month backtest and compares CAGR / Sharpe / Win-Rate
against the all-time historical baseline.

Flags strategy decay when recent performance falls >25% below baseline.

Saves results to outputs/walk_forward/ for dashboard display.

Crontab (monthly, last Friday):
  15 13 * * 5  cd /home/ubuntu/AlgoV2 && python3 scripts/walk_forward.py
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("walk_forward")

OUTPUT_DIR = "outputs/walk_forward"
DECAY_THRESHOLD = 0.25   # 25% drop triggers alert
WINDOW_MONTHS   = 6


def _run_backtest_window(start: date, end: date) -> dict:
    """Run backtest for a date window and return key metrics."""
    from data.fetcher import fetch_all, fetch_index
    from data.universe import get_all_symbols
    from backtest.engine import BacktestEngine
    from config.settings import INITIAL_CAPITAL, MARKET_INDEX_SYMBOL, MAX_OPEN_POSITIONS
    from db.repository import init_db

    init_db()
    symbols = get_all_symbols()
    lookback = (end - start).days + 60
    warmup = start - timedelta(days=200)

    logger.info("[WalkForward] Fetching data %s → %s ...", start, end)
    data = fetch_all(symbols, lookback_days=lookback, start=warmup, end=end)

    index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=lookback,
                           start=warmup, end=end)
    if not index_df.empty:
        data[MARKET_INDEX_SYMBOL] = index_df

    if len(data) < 5:
        logger.warning("[WalkForward] Insufficient data for %s → %s", start, end)
        return {}

    engine = BacktestEngine(
        data, start, end, INITIAL_CAPITAL,
        slippage_model="fixed_pct",
        max_selected=MAX_OPEN_POSITIONS,
    )
    result = engine.run()

    trades = result.get("trades", [])
    snaps  = result.get("snapshots", [])

    if not snaps or not trades:
        return {}

    values = [s.total_value for s in snaps]
    years  = (end - start).days / 365.25
    final  = values[-1]
    cagr   = ((final / INITIAL_CAPITAL) ** (1.0 / years) - 1.0) * 100 if years > 0 else 0

    # Sharpe from daily returns
    daily_returns = []
    for i in range(1, len(values)):
        prev = values[i-1]
        daily_returns.append((values[i] - prev) / prev if prev > 0 else 0)

    import math, statistics
    sharpe = 0.0
    if len(daily_returns) >= 10:
        mean = statistics.mean(daily_returns)
        std  = statistics.stdev(daily_returns)
        sharpe = round((mean / std) * math.sqrt(252), 2) if std > 0 else 0

    # Max drawdown
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        dd = (peak - v) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    wins     = sum(1 for t in trades if (getattr(t, "net_pnl", 0) or 0) > 0)
    win_rate = wins / len(trades) * 100 if trades else 0

    return {
        "start":       str(start),
        "end":         str(end),
        "cagr_pct":    round(cagr, 2),
        "sharpe":      round(sharpe, 2),
        "win_rate_pct":round(win_rate, 1),
        "max_dd_pct":  round(max_dd * 100, 2),
        "n_trades":    len(trades),
        "final_value": round(final, 2),
    }


def run(force: bool = False) -> dict:
    today = date.today()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Skip if not last Friday of month (unless forced)
    if not force:
        from scripts.universe_scheduler import _is_last_friday_of_month
        if not _is_last_friday_of_month(today):
            logger.info("[WalkForward] Not last Friday — skipping (use --force to override).")
            return {}

    # ── Recent 6-month window ──────────────────────────────────────────────
    recent_end   = today
    recent_start = today - timedelta(days=WINDOW_MONTHS * 30)

    # ── Historical window (full backtest baseline 2022–) ──────────────────
    hist_start = date(2022, 1, 1)
    hist_end   = today

    logger.info("[WalkForward] Running recent 6M window: %s → %s", recent_start, recent_end)
    recent = _run_backtest_window(recent_start, recent_end)

    logger.info("[WalkForward] Running full historical window: %s → %s", hist_start, hist_end)
    historical = _run_backtest_window(hist_start, hist_end)

    if not recent or not historical:
        logger.error("[WalkForward] Backtest failed — no result.")
        return {}

    # ── Decay Detection ───────────────────────────────────────────────────
    decay_flags = []
    for metric, label in [("cagr_pct", "CAGR"), ("sharpe", "Sharpe"),
                           ("win_rate_pct", "Win Rate")]:
        hist_val = historical.get(metric, 0)
        rec_val  = recent.get(metric, 0)
        if hist_val > 0:
            drop = (hist_val - rec_val) / hist_val
            if drop >= DECAY_THRESHOLD:
                decay_flags.append({
                    "metric":   label,
                    "baseline": hist_val,
                    "recent":   rec_val,
                    "drop_pct": round(drop * 100, 1),
                })

    verdict = "DECAYING" if decay_flags else ("HEALTHY" if recent.get("cagr_pct", 0) > 0 else "WEAK")

    report = {
        "as_of":       str(today),
        "window_months": WINDOW_MONTHS,
        "recent":      recent,
        "historical":  historical,
        "decay_flags": decay_flags,
        "verdict":     verdict,
    }

    # Save
    out_path = os.path.join(OUTPUT_DIR, f"walk_forward_{today.isoformat()}.json")
    latest_path = os.path.join(OUTPUT_DIR, "latest.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    with open(latest_path, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("[WalkForward] Saved to %s", out_path)

    # Alert if decaying
    if decay_flags:
        msg_lines = [f"⚠️ Strategy DECAY detected ({today}):"]
        for flag in decay_flags:
            msg_lines.append(
                f"  {flag['metric']}: {flag['baseline']:.1f} → {flag['recent']:.1f} "
                f"(↓{flag['drop_pct']:.0f}%)"
            )
        try:
            from notifications.telegram import send_message
            send_message("\n".join(msg_lines))
        except Exception:
            pass
        logger.warning("[WalkForward] DECAY: %s", decay_flags)
    else:
        logger.info("[WalkForward] Verdict: %s | Recent CAGR: %.1f%% | Hist CAGR: %.1f%%",
                    verdict, recent.get("cagr_pct", 0), historical.get("cagr_pct", 0))

    return report


def main():
    parser = argparse.ArgumentParser(description="Walk-Forward Strategy Validator")
    parser.add_argument("--force", action="store_true",
                        help="Run even if not last Friday of month")
    args = parser.parse_args()
    run(force=args.force)


if __name__ == "__main__":
    main()
