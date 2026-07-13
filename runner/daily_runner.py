"""
Daily runner — the full hybrid pipeline, run once after market close.
Supports both Paper and Live (Upstox) modes.
"""

import html
import logging
import sys
import os
import argparse
from datetime import date
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)

from db.repository import (
    init_db, load_positions, load_snapshots, save_signal,
    save_position, close_position_and_save_trade, get_last_position, was_sold_today,
    snapshot_exists_for_date, get_last_ohlcv_close, bear_swing_sold_within,
)
from data.fetcher import fetch_all, fetch_index
from data.universe import get_all_symbols, get_sector
from indicators.composite import compute_all
from strategy.signals import generate_signals
from strategy.regime import detect_regime, is_buy_allowed, MIN_INDEX_CANDLES, is_index_confirming
from strategy.relative_strength import compute_rs_for_all
from strategy.exit import initial_stops, check_exit_conditions
from strategy.defensive_portfolio import (
    REGIME_SWITCH_DAYS, BULL_RECOVERY_DAYS, REBAL_DAYS, MIN_DEFENSIVE_HOLD_DAYS,
    ALL_DEFENSIVE_SYMBOLS, BEAR_SWING_RS_THRESHOLD, BEAR_SWING_SLOTS, BEAR_SWING_COOLDOWN_DAYS, GOLD_ETF,
    LIQUIDBEES, LIQUIDBEES_ENABLED, ENTRY_CONFIRM_DAYS,
    is_defensive_symbol, get_defensive_entries, compute_rebalance,
)
from portfolio.manager import PortfolioManager
from portfolio.allocator import can_open_position
from portfolio.sizer import calculate_shares_for_value
from charges.calculator import buy_charges, net_pnl
from config.settings import round_to_tick
from runner.signal_output import write_signals, write_portfolio_state
from config.settings import INITIAL_CAPITAL, MARKET_INDEX_SYMBOL, MAX_STOCK_ALLOCATION_PCT, GOLDBEES_PROFIT_EXIT_ONLY, GOLDBEES_MAX_LOSS_PCT
from db.models import Position, Signal, Trade

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("DailyRunner")


def _alert_run_abort(today: date, live_mode: bool, reason: str) -> None:
    """Telegram-alert when the daily run aborts on a non-holiday day.

    A silently-skipped run leaves open positions unmanaged (stops not refreshed,
    exits not taken) until the next session — the operator must know. Best-effort."""
    mode = "LIVE" if live_mode else "PAPER"
    try:
        import html
        from notifications.telegram import send_message
        send_message(
            f"🛑 <b>Daily run ABORTED — {today} [{mode}]</b>\n"
            f"Reason: {html.escape(str(reason))}\n\n"
            f"No signals processed today. Open positions are UNMANAGED until the "
            f"next run — check Upstox and verify stops manually."
        )
    except Exception as e:
        logger.warning("[Alert] Failed to send run-abort alert: %s", e)


# ── Hybrid mode helpers ───────────────────────────────────────────────────────

def _get_regime_streak(snapshots: list, current_regime: str) -> int:
    """Count consecutive trailing days with the same regime (including today)."""
    streak = 1
    for snap in reversed(snapshots):
        snap_regime = (snap.regime or "").split("|")[0]
        if snap_regime == current_regime:
            streak += 1
        else:
            break
    return streak


def _detect_hybrid_mode(open_positions: list) -> str:
    """Infer current mode from open positions.

    LIQUIDBEES is the true defensive-mode marker (only bought to park bear-swing cash).
    GOLDBEES alone = carry-into-momentum state after BULL recovery; generate_signals
    handles EXIT_SAFE_HAVEN with GOLDBEES_PROFIT_EXIT_ONLY respected.
    GOLDBEES alone with LIQUIDBEES_ENABLED=0 = still defensive (no liquid ETF available).
    """
    if not open_positions:
        return "momentum"
    held = {p.symbol for p in open_positions}
    if LIQUIDBEES in held:
        return "defensive"
    if GOLD_ETF in held and LIQUIDBEES_ENABLED:
        return "momentum"   # carry state — GOLDBEES exits via generate_signals
    if GOLD_ETF in held:
        return "defensive"  # LIQUIDBEES_ENABLED=0: GOLDBEES is sole defensive marker
    return "momentum"


def _last_rebal_date(open_positions: list) -> date:
    """Return the GOLDBEES entry date as proxy for last rebalance / defensive-mode start."""
    gold_pos = next((p for p in open_positions if p.symbol == GOLD_ETF), None)
    return gold_pos.entry_date if gold_pos else None


def _defensive_days_held(open_positions: list, today: date) -> int:
    """Calendar days since defensive mode was entered (GOLDBEES entry date as anchor).
    Falls back to LIQUIDBEES when SAFE_HAVEN_ENABLED=false (no GOLDBEES bought).
    Returns MIN_DEFENSIVE_HOLD_DAYS when neither defensive symbol is held — allows
    immediate BULL re-entry in pure bear-swing mode (no gold/liquid ETF to exit).
    """
    for sym in [GOLD_ETF, LIQUIDBEES]:
        pos = next((p for p in open_positions if p.symbol == sym), None)
        if pos and pos.entry_date:
            entry = pos.entry_date.date() if hasattr(pos.entry_date, 'date') else pos.entry_date
            return (today - entry).days
    return MIN_DEFENSIVE_HOLD_DAYS  # no anchor found → allow immediate exit


def _build_defensive_signals(today: date, prices: dict, open_positions: list,
                              portfolio_val: float, action: str) -> list:
    """
    Generate synthetic Signal objects for defensive entries/exits.
    action = "enter_defensive" | "exit_defensive"
    """
    signals = []
    if action == "exit_defensive":
        for pos in open_positions:
            if is_defensive_symbol(pos.symbol):
                signals.append(Signal(
                    date=today, symbol=pos.symbol, action="SELL",
                    score=0.0, price=prices.get(pos.symbol, pos.entry_price),
                    reason="bull_regime_recovery",
                    indicators={"sector": pos.sector},
                ))
    elif action == "enter_defensive":
        held = {p.symbol for p in open_positions}
        entries = get_defensive_entries(portfolio_val, prices)
        for sym, shares, ep, _ in entries:
            if sym not in held:
                signals.append(Signal(
                    date=today, symbol=sym, action="BUY",
                    score=50.0, price=ep,
                    reason="bear_regime_defensive",
                    indicators={"sector": "Defensive", "shares_override": shares},
                ))
    return signals


def _broker_api_responsive(broker) -> bool:
    """Return True if the broker API is reachable (not a stale-token / network failure)."""
    try:
        cash = broker.get_available_cash()
        pv = broker.get_portfolio_value()
        return cash > 0 or pv > 0
    except Exception:
        return False


def _close_broker_ghost_position(broker, pos: Position, today: date, exit_reason: str = "BROKER_SYNC_CLOSE"):
    """DB shows OPEN but broker no longer holds the stock (GTT/manual sell detected)."""
    from portfolio.manager import cancel_stale_gtts

    cancel_stale_gtts(broker, pos.symbol, f"ghost close — {exit_reason}")

    exit_price = get_last_ohlcv_close(pos.symbol) or pos.entry_price
    exit_price = round_to_tick(exit_price)
    entry = pos.entry_date.date() if hasattr(pos.entry_date, "date") else pos.entry_date
    hold_days = (today - entry).days if entry else 0
    result = net_pnl(pos.entry_price, exit_price, pos.shares)
    trade = Trade(
        symbol=pos.symbol, sector=pos.sector,
        entry_date=pos.entry_date, exit_date=today,
        entry_price=pos.entry_price, exit_price=exit_price,
        shares=pos.shares, gross_pnl=result["gross_pnl"],
        charges=result["total_charges"], net_pnl=result["net_pnl"],
        exit_reason=exit_reason, hold_days=hold_days,
    )
    close_position_and_save_trade(pos.symbol, trade)
    logger.warning(
        "[Sync] %s closed in DB — broker no longer holds it (%s @ ₹%.2f, P&L %+.1f%%)",
        pos.symbol, exit_reason, exit_price, result["net_pct"],
    )


def sync_portfolio_with_broker(broker, today: date):
    """
    Ensure DB 'OPEN' positions exactly match Broker live holdings.
    1. Close DB positions not found in Broker (records trade — fixes GTT ghost positions).
    2. Add Broker holdings not found in DB.
    3. Update share counts if they differ.
    4. Cancel any legacy stop-loss GTTs — exits are signal-only now.
    """
    if not broker:
        return

    logger.info("[Sync] Reconciling DB with Broker holdings...")
    try:
        from portfolio.manager import cancel_stale_gtts

        raw_live_positions = broker.get_positions()
        # Every broker position is tracked now, regardless of IGNORE_SYMBOLS — origin
        # classification below (strategy vs manual) replaces symbol-list filtering. docs/30.
        live_positions = list(raw_live_positions)
        db_positions = {p.symbol: p for p in load_positions(status="OPEN")}

        if not live_positions and db_positions:
            if not _broker_api_responsive(broker):
                logger.warning(
                    "[Sync] Broker returned 0 positions but API looks down — "
                    "skipping ghost close to avoid false wipe."
                )
                return
            logger.info(
                "[Sync] Broker holds 0 positions but DB has %d open — "
                "closing all as broker-exited (likely GTT/manual sell).",
                len(db_positions),
            )
            for pos in list(db_positions.values()):
                _close_broker_ghost_position(broker, pos, today)
            return

        if not live_positions:
            return

        live_symbols = {lp.symbol for lp in live_positions}

        # 1. Close positions found in DB but NOT in Broker
        for symbol, pos in list(db_positions.items()):
            if symbol not in live_symbols:
                _close_broker_ghost_position(broker, pos, today)

        # Refresh after ghost closes
        db_positions = {p.symbol: p for p in load_positions(status="OPEN")}

        # 2. Add or Update positions found in Broker
        for lp in live_positions:
            if lp.symbol not in db_positions:
                # Guard: skip T+1 settlement residue — CNC sells appear in holdings next day
                if was_sold_today(lp.symbol, today):
                    logger.info(
                        "[Sync] %s sold today — skipping re-add (T+1 settlement residue).",
                        lp.symbol,
                    )
                    continue

                # Recover original entry_date from any previous record (closed due to API error, etc.)
                prev = get_last_position(lp.symbol)
                recovered_date = prev.entry_date if prev else today
                # Upstox avg_price = 0 for T+1 holdings; use ltp then OHLCV as fallback
                broker_price = lp.avg_price if lp.avg_price > 0 else lp.ltp
                if broker_price <= 0:
                    broker_price = get_last_ohlcv_close(lp.symbol)
                    if broker_price > 0:
                        logger.info(
                            "[Sync] %s: broker returned price=0 — using OHLCV last close: ₹%.2f",
                            lp.symbol, broker_price,
                        )
                    else:
                        logger.warning(
                            "[Sync] %s: could not determine entry price from broker or OHLCV — recording 0",
                            lp.symbol,
                        )
                # Only recover prev entry_price if it was non-zero; otherwise trust broker price
                recovered_price = (prev.entry_price
                                   if (prev and prev.entry_price > 0)
                                   else broker_price)
                # No prior DB record at all (any status) means the strategy never opened
                # this position — it's a manual/imported broker holding. docs/30.
                origin = "strategy" if prev else "manual"
                if prev:
                    logger.info(
                        f"[Sync] Re-adding {lp.symbol} — recovering entry_date={recovered_date} "
                        f"entry_price=₹{recovered_price:.2f} from previous record."
                    )
                else:
                    logger.info(f"[Sync] New {origin} position in Broker: {lp.symbol}. Adding to DB.")
                stops = initial_stops(recovered_price)
                new_pos = Position(
                    symbol=lp.symbol,
                    sector=get_sector(lp.symbol),
                    entry_date=recovered_date,
                    entry_price=recovered_price,
                    shares=lp.quantity,
                    stop_loss=stops["stop_loss"],
                    take_profit=stops["take_profit"],
                    trailing_stop=stops["trailing_stop"],
                    peak_price=max(recovered_price, lp.ltp),
                    origin=origin,
                )
                save_position(new_pos)

            else:
                # Update share count if it changed manually
                db_pos = db_positions[lp.symbol]
                if db_pos.shares != lp.quantity:
                    logger.info(f"[Sync] Updating {lp.symbol} shares: {db_pos.shares} -> {lp.quantity}")
                    db_pos.shares = lp.quantity
                    save_position(db_pos)

        # 3. Remove legacy stop-loss GTTs — exits are signal-only.
        # Never touch a non-strategy position's GTTs — those are the user's own broker-side
        # protection on a manual/imported holding, not something this system manages. docs/30.
        for pos in load_positions(status="OPEN"):
            if pos.origin != "strategy":
                continue
            cancel_stale_gtts(broker, pos.symbol, "signal-only mode — legacy stop GTT cleanup")

    except Exception as e:
        logger.error(f"[Sync] Failed to reconcile portfolio: {e}")
        try:
            from notifications.telegram import send_message
            send_message(
                f"⚠️ <b>Sync Failed — {today}</b>\n"
                f"Broker reconciliation failed: {html.escape(str(e))}\n"
                f"Runner continuing with DB state — verify positions manually."
            )
        except Exception:
            pass

# NSE holiday calendar — extend this set each year before year-end
_NSE_HOLIDAYS = {
    # 2026
    date(2026,  1, 15), date(2026,  1, 26), date(2026,  2, 15),
    date(2026,  3,  3), date(2026,  3, 21), date(2026,  3, 26),
    date(2026,  3, 31), date(2026,  4,  3), date(2026,  4, 14),
    date(2026,  5,  1), date(2026,  5, 28), date(2026,  6, 26),
    date(2026,  8, 15), date(2026,  9, 14), date(2026, 10,  2),
    date(2026, 10, 20), date(2026, 11,  8), date(2026, 11, 10),
    date(2026, 11, 24), date(2026, 12, 25),
}


def _is_market_holiday(today: date, index_df=None) -> bool:
    """
    Return True if today is a weekend or NSE holiday.
    Primary check: known holiday list + weekday.
    Secondary check: no trading bar in index data — only reliable AFTER market close (15:30 IST).
    Running at 14:50 IST means today's candle is not yet complete; skip data-driven check pre-close.
    """
    from datetime import datetime, timezone, timedelta as td
    if today.weekday() >= 5:
        return True
    if today in _NSE_HOLIDAYS:
        logger.info("NSE holiday on %s — skipping.", today)
        return True
    if today.year > 2026:
        logger.warning(
            "Holiday list only covers up to 2026. %s may be an NSE holiday — "
            "relying on index data check only. Update _NSE_HOLIDAYS for %d.",
            today, today.year
        )
    # Data-driven check: only use "no bar = holiday" signal after 15:30 IST
    # (candle is not complete while market is open — would cause false holiday detection)
    IST = timezone(td(hours=5, minutes=30))
    now_ist = datetime.now(IST)
    market_closed = now_ist.hour > 15 or (now_ist.hour == 15 and now_ist.minute >= 30)
    if market_closed and index_df is not None and not index_df.empty:
        today_ts = pd.Timestamp(today)
        if not index_df.index.normalize().isin([today_ts]).any():
            logger.info("No index bar for %s (post-close) — treating as non-trading day.", today)
            return True
    return False


def _backup_db(today: date) -> None:
    """Pre-run SQLite backup using same naming as scripts/backup_db.py (trading_YYYYMMDD.db).
    Uses sqlite3.backup() API for a safe consistent copy even under active writes.
    Post-run cron backup (16:30 IST) overwrites this with the final post-trade state."""
    import sqlite3
    from pathlib import Path
    from config.settings import DB_PATH
    db = Path(DB_PATH)
    if not db.exists():
        return
    backup_dir = db.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"trading_{today.strftime('%Y%m%d')}.db"
    if not backup_path.exists():
        try:
            src = sqlite3.connect(str(db))
            dst = sqlite3.connect(str(backup_path))
            src.backup(dst)
            src.close()
            dst.close()
            logger.info("[Backup] Pre-run DB backed up → %s", backup_path.name)
        except Exception as e:
            logger.warning("[Backup] Pre-run backup failed: %s", e)


def run(today: date = None, live_mode: bool = False, fund_injection: float = 0.0):
    if today is None:
        today = date.today()

    mode_str = "LIVE (REAL MONEY)" if live_mode else "PAPER TRADING"
    logger.info("=== Daily Runner started [%s]: %s ===", mode_str, today)

    # 1. Initialise DB
    init_db()

    # 2. Fetch index
    logger.info("Fetching market index %s...", MARKET_INDEX_SYMBOL)
    index_df = fetch_index(MARKET_INDEX_SYMBOL, lookback_days=MIN_INDEX_CANDLES + 50, live_mode=live_mode)
    index_candles = len(index_df) if index_df is not None and not index_df.empty else 0

    # 3. Holiday check — skip if market didn't trade today
    if _is_market_holiday(today, index_df):
        logger.info("=== Market holiday on %s — no run. ===", today)
        return

    # Idempotency guard — prevent double-execution on same day
    if snapshot_exists_for_date(today):
        logger.warning(
            "[Runner] Snapshot for %s already exists — runner already completed today. "
            "Aborting to prevent double execution. Delete today's snapshot to force re-run.",
            today,
        )
        return

    # Backup DB at start of each run (before any writes)
    _backup_db(today)

    # 4. Detect regime
    if index_candles < 20:
        logger.warning("Insufficient market data (%d/20). Defaulting to BULL.", index_candles)
        regime, market_bullish = "BULL", True
    else:
        regime = detect_regime(index_df)
        market_bullish = is_buy_allowed(regime)
        logger.info("Market regime: %s | BUY entries %s", regime, "ALLOWED" if market_bullish else "BLOCKED")

    # 5. Fetch stock data (include defensive symbols so they're available for prices)
    symbols = list(dict.fromkeys(get_all_symbols() + ALL_DEFENSIVE_SYMBOLS))
    data = fetch_all(symbols, live_mode=live_mode)
    if not data:
        logger.error("No stock data fetched — aborting")
        _alert_run_abort(today, live_mode, "No stock data fetched (data provider failure).")
        return
    min_required = max(10, len(symbols) // 2)  # need at least 50% of universe
    if len(data) < min_required:
        logger.error(
            "[Data] Only %d/%d symbols fetched (need ≥%d) — aborting to avoid trading on partial universe.",
            len(data), len(symbols), min_required,
        )
        _alert_run_abort(
            today, live_mode,
            f"Only {len(data)}/{len(symbols)} symbols fetched (need ≥{min_required}) — partial universe.",
        )
        return

    # 5b. Warmup adequacy check — warn if any symbol has < 450 days (EMA150 needs ~450 to converge)
    MIN_WARMUP = 450
    thin_symbols = [sym for sym, df in data.items() if sym != MARKET_INDEX_SYMBOL and len(df) < MIN_WARMUP]
    if thin_symbols:
        logger.warning(
            "[Warmup] %d symbol(s) have < %d days of history — EMA(150)/regime may be inaccurate: %s",
            len(thin_symbols), MIN_WARMUP, thin_symbols[:10],
        )

    # 6. Relative strength — always compute (needed for bear swing even in BEAR regime)
    rs_data = compute_rs_for_all(data, index_df)

    # 7. Compute indicators
    indicators = compute_all(data, rs_data=rs_data)

    # 8. Initialise Broker & Sync
    broker = None
    if live_mode:
        try:
            from broker.upstox import UpstoxBroker
            broker = UpstoxBroker()
            logger.info("[Live] Connected to Upstox")
        except Exception as e:
            logger.error(f"[Live] Failed to initialise Upstox broker: {e}")
            logger.error("Aborting live run to prevent state desync.")
            _alert_run_abort(today, live_mode, f"Upstox broker init failed: {e}")
            return

    # Sync reality with database before generating new signals
    sync_portfolio_with_broker(broker, today)

    # 8. Load open positions (Post-sync)
    open_positions = load_positions(status="OPEN")
    held_symbols   = {p.symbol for p in open_positions}

    # 9. Hybrid mode detection & signal generation
    snapshots   = load_snapshots()
    latest_snap = snapshots[-1] if snapshots else None
    prices      = {sym: ind["close"] for sym, ind in indicators.items()}

    pv       = latest_snap.total_value if latest_snap else INITIAL_CAPITAL
    cash_bal = latest_snap.cash        if latest_snap else INITIAL_CAPITAL

    # Override cash_bal with live broker cash before signal generation
    # so bear swing sizing and slot checks use today's actual available funds
    if broker:
        try:
            live_cash = broker.get_available_cash()
            if live_cash >= 0:
                logger.info("[Cash] Live broker cash: ₹%.2f (snapshot was ₹%.2f)", live_cash, cash_bal)
                cash_bal = live_cash
        except Exception as e:
            logger.warning("[Cash] Could not fetch live cash — using snapshot: %s", e)

    # Recompute pv using live cash + current market prices (snapshot value is from yesterday)
    invested_live = sum(prices.get(p.symbol, p.entry_price) * p.shares for p in open_positions)
    pv = cash_bal + invested_live
    logger.info("[PV] Live portfolio value: ₹%.2f (cash ₹%.2f + invested ₹%.2f)", pv, cash_bal, invested_live)

    hybrid_mode    = _detect_hybrid_mode(open_positions)
    regime_streak  = _get_regime_streak(snapshots, regime)
    rebal_date     = _last_rebal_date(open_positions)

    logger.info(
        "Hybrid mode: %s | Regime: %s (streak=%d) | Positions: %d",
        hybrid_mode, regime, regime_streak, len(open_positions)
    )

    # ── Regime transition: BULL → BEAR ──────────────────────────────────
    if regime == "BEAR" and hybrid_mode == "momentum" and regime_streak >= REGIME_SWITCH_DAYS:
        logger.info("[Hybrid] Switching to DEFENSIVE portfolio (BEAR x%d days)", regime_streak)
        # Exit all momentum positions
        exit_signals = [Signal(
            date=today, symbol=p.symbol, action="SELL",
            score=0.0, price=prices.get(p.symbol, p.entry_price),
            reason="bear_regime_exit", indicators={"sector": p.sector},
        ) for p in open_positions if not is_defensive_symbol(p.symbol)]
        # Enter defensive basket
        entry_signals = _build_defensive_signals(today, prices, open_positions, pv, "enter_defensive")
        signals = exit_signals + entry_signals

    # ── Regime transition: BEAR → BULL ──────────────────────────────────
    elif (regime == "BULL" and hybrid_mode == "defensive"
          and regime_streak >= BULL_RECOVERY_DAYS
          and _defensive_days_held(open_positions, today) >= MIN_DEFENSIVE_HOLD_DAYS):
        logger.info("[Hybrid] Switching back to MOMENTUM (BULL x%d days)", regime_streak)
        # Exit LIQUIDBEES and other defensives immediately.
        # GOLDBEES: exit now if profitable; defer (carry into momentum) if PROFIT_EXIT_ONLY
        # and price still below entry. _detect_hybrid_mode treats GOLDBEES-only as "momentum"
        # so generate_signals handles EXIT_SAFE_HAVEN on subsequent days.
        signals = []
        for pos in open_positions:
            if not is_defensive_symbol(pos.symbol):
                continue
            if pos.symbol == GOLD_ETF:
                gold_price = prices.get(GOLD_ETF, pos.entry_price)
                max_loss_hit = (GOLDBEES_MAX_LOSS_PCT > 0
                                and gold_price < pos.entry_price * (1 - GOLDBEES_MAX_LOSS_PCT))
                defer = GOLDBEES_PROFIT_EXIT_ONLY and gold_price < pos.entry_price and not max_loss_hit
                if defer:
                    logger.info(
                        "[Hybrid] Carrying GOLDBEES into momentum (₹%.2f < entry ₹%.2f, PROFIT_EXIT_ONLY)",
                        gold_price, pos.entry_price,
                    )
                    continue  # exits via generate_signals when price recovers
            signals.append(Signal(
                date=today, symbol=pos.symbol, action="SELL",
                score=0.0, price=prices.get(pos.symbol, pos.entry_price),
                reason="bull_regime_recovery",
                indicators={"sector": pos.sector},
            ))

    # ── Defensive quarterly rebalance ────────────────────────────────────
    elif hybrid_mode == "defensive" and rebal_date and (today - rebal_date).days >= REBAL_DAYS:
        logger.info("[Hybrid] Defensive quarterly rebalance")
        sells, buys = compute_rebalance(open_positions, pv, prices)
        signals = []
        for sym, shares, ep in sells:
            pos = next((p for p in open_positions if p.symbol == sym), None)
            if pos:
                signals.append(Signal(
                    date=today, symbol=sym, action="SELL",
                    score=0.0, price=ep, reason="rebalance_trim",
                    indicators={"sector": pos.sector},
                ))
        for sym, shares, ep in buys:
            signals.append(Signal(
                date=today, symbol=sym, action="BUY",
                score=50.0, price=ep, reason="rebalance_add",
                indicators={"sector": "Defensive", "shares_override": shares},
            ))

    # ── Defensive mode: bear swing — active trading alongside GOLDBEES ───
    elif hybrid_mode == "defensive":
        signals = []

        # 1. Exit bear swing positions that triggered stop/trail/rs exit
        bear_swing_positions = [p for p in open_positions if not is_defensive_symbol(p.symbol)]
        for pos in bear_swing_positions:
            ind = indicators.get(pos.symbol, {})
            cp  = ind.get("close", pos.entry_price)
            rs  = ind.get("rs_rank", 0)
            exit_triggered, exit_reason = check_exit_conditions(pos, cp, rs, indicators=ind)

            if not exit_triggered:
                ema50 = ind.get("ema_50", 0)
                if ema50 > 0 and cp < ema50:
                    pos.days_below_ema50 += 1
                    if pos.days_below_ema50 >= 2:
                        exit_triggered = True
                        exit_reason = "TREND_BREAK (Price < 50 EMA x2 days)"
                else:
                    pos.days_below_ema50 = 0

            if exit_triggered:
                signals.append(Signal(
                    date=today, symbol=pos.symbol, action="SELL",
                    score=0.0, price=cp,
                    reason=f"bear_swing|{exit_reason}",
                    indicators={"sector": pos.sector},
                ))
                logger.info("[Bear-Swing] EXIT %s — %s @ ₹%.2f", pos.symbol, exit_reason, cp)
            else:
                save_position(pos)  # persist days_below_ema50 counter

        # 2. Bear swing entries: RS > threshold AND stock above own EMA50
        exiting = {s.symbol for s in signals if s.action == "SELL"}
        active_swing = len(bear_swing_positions) - len(exiting)
        bear_slots_free = BEAR_SWING_SLOTS - active_swing
        held_set = {p.symbol for p in open_positions}

        # LIQUIDBEES: counts as liquid cash for bear swing sizing
        liq_pos = next((p for p in open_positions if p.symbol == LIQUIDBEES), None) if LIQUIDBEES_ENABLED else None
        liq_price = prices.get(LIQUIDBEES, 0) if liq_pos else 0
        liq_value = liq_pos.shares * liq_price if (liq_pos and liq_price > 0) else 0
        bear_capital = cash_bal + liq_value  # total deployable capital (cash + liquid ETF)

        if bear_slots_free > 0 and bear_capital > pv * 0.005:
            candidates = []
            for sym, ind in indicators.items():
                if sym in held_set or sym == GOLD_ETF or sym == LIQUIDBEES:
                    continue
                if bear_swing_sold_within(sym, today, BEAR_SWING_COOLDOWN_DAYS):
                    continue
                rs_rank  = ind.get("rs_rank", 0)
                close    = ind.get("close", 0)
                ema50    = ind.get("ema_50", 0)
                turnover = ind.get("turnover", 0)
                if (rs_rank >= BEAR_SWING_RS_THRESHOLD
                        and ema50 > 0 and close > ema50
                        and turnover >= 20_000_000):
                    candidates.append((sym, rs_rank, ind))
            candidates.sort(key=lambda x: x[1], reverse=True)

            liq_remaining = liq_value  # track how much LIQUIDBEES is still available this loop
            for sym, rs_rank, ind in candidates[:bear_slots_free]:
                ep = ind["close"] * 1.001
                slot_cash = bear_capital / BEAR_SWING_SLOTS
                slot_cash_capped = min(slot_cash, pv * MAX_STOCK_ALLOCATION_PCT)
                alloc_ok, alloc_reason = can_open_position(sym, slot_cash_capped, pv, open_positions, prices)
                if not alloc_ok:
                    logger.info("[Bear-Swing] SKIP %s — %s", sym, alloc_reason)
                    continue

                # Compute shares FIRST — skip entirely if price exceeds slot capital
                # (prevents LIQUIDBEES being sold with no corresponding BUY)
                target_value = slot_cash_capped - buy_charges(slot_cash_capped).total
                shares = calculate_shares_for_value(target_value, ep)
                if shares == 0:
                    logger.info(
                        "[Bear-Swing] SKIP %s — price ₹%.2f exceeds slot capital ₹%.2f",
                        sym, ep, slot_cash_capped,
                    )
                    continue

                # Safe to sell LIQUIDBEES now — BUY is guaranteed to follow
                if cash_bal < slot_cash_capped and liq_pos and liq_remaining > 0 and liq_price > 0:
                    needed = min(slot_cash_capped - cash_bal, liq_remaining)
                    liq_sell_shares = min(liq_pos.shares, int(needed / liq_price) + 1)
                    if liq_sell_shares > 0:
                        signals.append(Signal(
                            date=today, symbol=LIQUIDBEES, action="SELL",
                            score=0.0, price=liq_price,
                            reason="liquidbees_fund_swing",
                            indicators={"sector": "Defensive", "shares_override": liq_sell_shares},
                        ))
                        liq_remaining -= liq_sell_shares * liq_price
                        logger.info("[LIQUIDBEES] Selling %d shares @ ₹%.2f to fund %s swing entry",
                                    liq_sell_shares, liq_price, sym)

                signals.append(Signal(
                    date=today, symbol=sym, action="BUY",
                    score=rs_rank, price=ep,
                    reason="bear_swing_entry",
                    indicators={
                        "sector": get_sector(sym),
                        "rs_rank": rs_rank,
                        "atr": ind.get("atr", 0),
                        "ema_50": ind.get("ema_50", 0),
                        "shares_override": shares,
                    },
                ))
                logger.info(
                    "[Bear-Swing] BUY %s | RS %.1f | EMA50 ✓ | ₹%.2f x %d shares",
                    sym, rs_rank, ep, shares,
                )

        # Park idle bear-swing cash in LIQUIDBEES if not yet held
        if (LIQUIDBEES_ENABLED and LIQUIDBEES not in held_set
                and LIQUIDBEES not in {s.symbol for s in signals}):
            liq_price_now = prices.get(LIQUIDBEES, 0)
            _LIQUIDBEES_MIN_BUY = 5000
            if liq_price_now > 0 and cash_bal > _LIQUIDBEES_MIN_BUY:
                liq_budget = cash_bal * 0.90
                liq_shares = int(liq_budget / liq_price_now)
                if liq_shares > 0:
                    signals.append(Signal(
                        date=today, symbol=LIQUIDBEES, action="BUY",
                        score=0.0, price=liq_price_now,
                        reason="liquidbees_park_cash",
                        indicators={"sector": "Defensive", "shares_override": liq_shares},
                    ))
                    logger.info("[LIQUIDBEES] Parking ₹%.0f idle cash → %d shares @ ₹%.2f",
                                liq_budget, liq_shares, liq_price_now)

        if not signals:
            logger.info(
                "[Hybrid] Defensive — GOLDBEES held, %d bear swing slot(s) active, no new signals",
                active_swing,
            )

    # ── Normal BULL momentum mode ─────────────────────────────────────────
    else:
        idx_confirmed = is_index_confirming(index_df)
        # Anti-whipsaw: after regime flips to BULL, wait ENTRY_CONFIRM_DAYS before new entries
        # (mirrors backtest engine; default=0 means no delay)
        entry_confirmed = regime != "BULL" or regime_streak >= ENTRY_CONFIRM_DAYS
        signals, surviving_positions = generate_signals(
            today, indicators, open_positions, held_symbols,
            market_bullish=market_bullish, regime=regime,
            portfolio_value=pv, cash=cash_bal, initial_capital=INITIAL_CAPITAL,
            index_confirming=idx_confirmed and entry_confirmed
        )
        # Persist days_below_ema50 counter so TREND_BREAK accumulates across days
        for pos in surviving_positions:
            save_position(pos)


    # 10. Process through portfolio manager (The execution gate)
    # prices already built above in hybrid section

    # In live mode: override indicator close with actual LTP from broker for held symbols
    # Ensures Telegram "Now" prices and portfolio valuation match Upstox exactly
    if broker:
        try:
            live_positions = broker.get_positions()
            for lp in live_positions:
                if lp.ltp > 0:
                    prices[lp.symbol] = lp.ltp
        except Exception as e:
            logger.warning("[Live] Could not fetch LTP for price override: %s", e)

    mgr = PortfolioManager(INITIAL_CAPITAL, broker=broker)
    mgr.process_signals(today, signals, prices, indicators=indicators, regime=regime,
                        fund_injection=fund_injection)

    # 11. Outputs & Notifications
    # Re-load snapshots to get the one JUST saved by mgr.process_signals
    snapshots = load_snapshots()
    latest_snap = snapshots[-1] if snapshots else None

    for sig in signals:
        save_signal(sig)
    write_signals(today, signals)
    
    if latest_snap:
        # Pass the latest snapshot to the state writer
        write_portfolio_state(today, latest_snap, mgr.open_positions, prices)

    try:
        from notifications.telegram import send_daily_summary
        send_daily_summary(
            today,
            [s for s in signals if s.action == "BUY"],
            [s for s in signals if s.action == "SELL"],
            latest_snap,
            open_positions=mgr.open_positions,
            prices=prices,
        )
    except Exception as e:
        logger.warning("Telegram alert failed: %s", e)

    # Post-run drift check — alert if live execution slippage > 0.5% threshold
    try:
        from monitoring.drift_monitor import compute_drift
        from notifications.telegram import send_message
        drift = compute_drift()
        avg_drift = drift.get("avg_drift_pct", 0)
        pairs = drift.get("pairs", 0)
        if pairs > 0 and abs(avg_drift) > 0.5:
            send_message(
                f"⚠️ <b>Execution Drift Alert — {today}</b>\n"
                f"Avg drift: <b>{avg_drift:+.3f}%</b> (threshold ±0.5%)\n"
                f"Entry: {drift.get('entry_drift_pct', 0):+.3f}%  |  "
                f"Exit: {drift.get('exit_drift_pct', 0):+.3f}%\n"
                f"Trades analysed: {pairs}\n\n"
                f"Live fills diverging from backtest — review order execution."
            )
            logger.warning("[Drift] Avg drift %.3f%% exceeds 0.5%% — Telegram alert sent.", avg_drift)
        else:
            logger.info("[Drift] Execution drift %.3f%% within threshold (%d pairs).", avg_drift, pairs)
    except Exception as e:
        logger.debug("[Drift] Post-run drift check skipped: %s", e)

    logger.info("=== Daily Runner complete: %s ===", today)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Enable live trading mode")
    args = parser.parse_args()
    run(today=date.today(), live_mode=args.live)
