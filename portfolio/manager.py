"""
Portfolio manager — the single source of truth for portfolio state.
Surgical Logging: Only actual BUY/SELL executions are logged.
"""

import json
import logging
import os
from collections import deque
from datetime import date
from typing import List, Dict

from db.models import Position, Trade, Signal, PortfolioSnapshot
from db import repository as repo
from db.repository import total_capital_injected_ever
from portfolio.sizer import calculate_shares_for_value, position_value
from portfolio.allocator import can_open_position, portfolio_invested_value
from portfolio.risk import can_open_new_trades
from strategy.scoring import score_to_size_factor
from strategy.exit import initial_stops, update_trailing_stop
from charges.calculator import net_pnl, buy_charges
from broker.base import BaseBroker, OrderSide, OrderType, OrderRequest
from config.settings import INITIAL_CAPITAL, round_to_tick, MAX_OPEN_POSITIONS, SAFE_HAVEN_SYMBOL, SIZER_CASH_BUFFER_PCT, MAX_STOCK_ALLOCATION_PCT, DRAWDOWN_REDUCE_SIZE_PCT, DRAWDOWN_REDUCE_TIER2_MULT, GTT_LIMIT_BUFFER_PCT
from strategy.defensive_portfolio import (
    ROTATION_ENABLED, ROTATE_EXIT_RS, ROTATE_INTO_RS, ROTATE_MIN_GAP,
    RIDE_WINNER_ENABLED, RIDE_WINNER_GAP_PCT,
    SCORE_DROP_EXIT_ENABLED, SCORE_DROP_DAYS,
    is_defensive_symbol,
)

_SCORE_HISTORY_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "score_history.json")


def _is_score_declining(symbol: str, score_history: dict, min_days: int) -> bool:
    """
    Return True if symbol's composite momentum score (rs_ratio × rs_ratio_1m) has
    declined every single day for min_days consecutive days.
    Uses absolute RS ratio values — not the cross-sectional percentile rank — so the
    signal reflects genuine momentum decay, not just other stocks improving.
    """
    h = score_history.get(symbol)
    if h is None or len(h) < min_days:
        return False
    recent = list(h)[-min_days:]
    return all(recent[i] > recent[i + 1] for i in range(len(recent) - 1))


def _load_score_history() -> Dict[str, list]:
    try:
        with open(_SCORE_HISTORY_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_score_history(history: Dict[str, list]) -> None:
    try:
        with open(_SCORE_HISTORY_PATH, "w") as f:
            json.dump(history, f)
    except Exception as e:
        logger.warning(f"[score_history] Could not save: {e}")

logger = logging.getLogger(__name__)


def gtt_stop_limit_price(trigger_price: float) -> float:
    """Limit price for a SELL-side stop-loss GTT: buffered below the trigger so the
    resting LIMIT order (NSE GTTs cannot execute as true MARKET) has real room to
    fill on a gap, instead of sitting unfillable at the exact trigger price."""
    return round_to_tick(trigger_price * (1 - GTT_LIMIT_BUFFER_PCT))


def _alert_naked_stop(symbol: str, shares, trigger, reason: str) -> None:
    """Telegram-alert when a GTT stop-loss could not be placed, leaving the
    position unprotected (naked) until the next daily run. Best-effort."""
    try:
        import html
        from notifications.telegram import send_message
        send_message(
            f"🚨 <b>NAKED POSITION — stop-loss NOT set</b>\n"
            f"Symbol: <b>{html.escape(str(symbol))}</b> ({html.escape(str(shares))} sh)\n"
            f"Intended stop: ₹{trigger:,.2f}\n"
            f"Reason: {html.escape(str(reason))}\n\n"
            f"Position has NO downside protection. Set a stop on Upstox manually."
        )
    except Exception as e:
        logger.warning("[Alert] Failed to send naked-position alert for %s: %s", symbol, e)


class PortfolioManager:
    """Manages paper portfolio state: cash, positions, trade log."""

    def __init__(self, initial_capital: float = INITIAL_CAPITAL, broker: BaseBroker = None):
        self.initial_capital = initial_capital
        self.broker = broker
        self._load_state()

    def _load_state(self):
        self.open_positions: List[Position] = repo.load_positions(status="OPEN")
        
        # If live broker is connected, sync cash from the broker
        if self.broker:
            try:
                self.cash = self.broker.get_available_cash()
                live_pv = self.broker.get_portfolio_value()

                # Baseline Detection: If no snapshots exist, we use the current live value
                # as our 'initial_capital' so P&L starts from zero for this session.
                # Also reset peak_value to avoid false drawdown triggers on first run.
                snapshots = repo.load_snapshots(limit=1)
                if not snapshots and live_pv > 0:
                    logger.info(f"[Live] Baseline established from Upstox: ₹{live_pv:,.2f}")
                    self.initial_capital = live_pv
                    self.peak_value = live_pv

                # Broker swallows 401/423 errors and returns 0.0 — fall back to DB
                # when broker reports ₹0 but DB has recorded non-zero cash.
                if self.cash == 0:
                    db_cash = self._load_cash_from_db()
                    if db_cash > 0:
                        logger.warning(
                            f"[Live] Broker returned ₹0 cash (possible API error). "
                            f"Falling back to DB snapshot: ₹{db_cash:,.2f}"
                        )
                        self.cash = db_cash

                logger.info(f"[Live] Synced actual cash from broker: ₹{self.cash:,.2f}")
            except Exception as e:
                logger.error(f"[Live] Failed to sync live data from broker: {e}")
                self.cash = self._load_cash_from_db()
                self.peak_value = self.initial_capital
        else:
            self.cash = self._load_cash_from_db()
            self.peak_value = self.initial_capital
            
        snapshots = repo.load_snapshots()
        if snapshots:
            # Only update peak_value if it's higher than what we have
            # In live mode, if snapshots were empty, peak_value was set to live_pv above
            self.peak_value = max(getattr(self, 'peak_value', 0), max(s.total_value for s in snapshots))
        elif not hasattr(self, 'peak_value'):
            self.peak_value = self.initial_capital
        self.new_trades_today = 0

    def _load_cash_from_db(self) -> float:
        snapshots = repo.load_snapshots()
        if snapshots:
            # Skip snapshots written with 0 cash (broker API failures)
            for snap in reversed(snapshots):
                if snap.cash > 0:
                    return snap.cash
            return snapshots[-1].cash
        return self.initial_capital

    def _await_order_completion(self, order_id: str, timeout: int = 30):
        """Poll the broker until the order is COMPLETE, REJECTED, or CANCELLED."""
        import time
        from broker.base import OrderStatus
        
        start_time = time.time()
        res = None
        while time.time() - start_time < timeout:
            res = self.broker.get_order_status(order_id)
            if res.status == OrderStatus.COMPLETE:
                return res
            if res.status in (OrderStatus.REJECTED, OrderStatus.CANCELLED):
                return res
            
            logger.info(f"  [Live] Order {order_id} is {res.status}... waiting")
            time.sleep(2)
            
        if res:
            logger.warning(f"  [Live] Timeout waiting for order {order_id}. Current status: {res.status}")
        return res

    def portfolio_value(self, prices: dict) -> float:
        """Total Account Value = Cash + Portfolio Value."""
        if self.broker:
            try:
                live_val = self.broker.get_portfolio_value()
                if live_val > 0: return live_val
            except:
                pass
                
        open_position_value = portfolio_invested_value(self.open_positions, prices)
        return self.cash + open_position_value

    def process_signals(self, today: date, signals: List[Signal], prices: dict, indicators: dict = None, regime: str = None, fund_injection: float = 0.0):
        """Process today's signals: Exits first, then dynamic batch entries."""
        self.new_trades_today = 0
        # Symbols whose GTT was (re)placed during this run (BUY/ADD/ROTATE) — the
        # end-of-run GTT reconcile skips these to avoid redundant cancel/replace.
        self._gtt_synced_this_run = set()
        # Symbols whose trailing stop ratcheted UP this run — broker GTT must follow.
        self._gtt_needs_refresh = set()

        # ── A. Update Trailing Stops for existing positions (ATR-aware) ──
        # GOLDBEES (SAFE_HAVEN_SYMBOL) is excluded: it's a static-floor hedge position
        # (exited only via GOLDBEES_MAX_LOSS_PCT or a BULL regime flip, in strategy/signals.py),
        # not a momentum stock — it must not be ratcheted/tightened by this ATR trail logic.
        for pos in list(self.open_positions):
            if pos.symbol == SAFE_HAVEN_SYMBOL:
                continue
            cp = prices.get(pos.symbol)
            if cp:
                atr = (indicators or {}).get(pos.symbol, {}).get('atr', 0)
                old_trail = pos.trailing_stop
                old_peak = pos.peak_price
                update_trailing_stop(pos, cp, atr=atr, regime=regime)
                if pos.trailing_stop >= cp:
                    # A regime-aware ratchet can jump the stop by more than the day's
                    # move, landing the new trigger at/above current price. A GTT placed
                    # there would fire immediately — but Upstox executes GTT-SINGLE as a
                    # LIMIT sell at the exact trigger price (ignores our MARKET order_type),
                    # which is unfillable once price is already below it, so it gets
                    # auto-cancelled and leaves the position naked (2026-07-01 GOLDBEES
                    # incident). Exit now via a real market sell instead of racing a GTT.
                    logger.warning(
                        f"  [TRAIL-BREACH] {pos.symbol} new stop ₹{pos.trailing_stop:.2f} "
                        f"already ≥ price ₹{cp:.2f} — exiting immediately instead of GTT"
                    )
                    breach_sig = Signal(
                        date=today, symbol=pos.symbol, action="SELL",
                        score=0, price=cp, reason="TRAIL_BREACH_IMMEDIATE",
                    )
                    self._execute_sell(today, pos, breach_sig, prices)
                    continue
                if pos.trailing_stop > old_trail:
                    self._gtt_needs_refresh.add(pos.symbol)
                # Persist the ratchet immediately — otherwise trailing_stop/peak_price only
                # exist in memory for this run and reset to stale values on next load, even
                # though the broker-side GTT was already updated to the new (correct) level.
                if pos.trailing_stop != old_trail or pos.peak_price != old_peak:
                    repo.save_position(pos)

        # ── A.1 Update composite score history (load → append today → save) ──
        score_history = _load_score_history()
        if indicators:
            for sym, ind in indicators.items():
                cr = float(ind.get("composite_rank", 0) or 0)
                if cr > 0:
                    hist = score_history.get(sym, [])
                    hist.append(cr)
                    score_history[sym] = hist[-9:]  # keep last 9 entries
            _save_score_history(score_history)

        # ── A.2 Score Drop Exit: RS declining N days → exit → 50/50 to others ──
        if SCORE_DROP_EXIT_ENABLED:
            non_def = [p for p in self.open_positions
                       if not is_defensive_symbol(p.symbol)
                       and p.symbol != SAFE_HAVEN_SYMBOL]
            for pos in list(non_def):
                if not _is_score_declining(pos.symbol, score_history, SCORE_DROP_DAYS):
                    continue
                logger.info(f"  [SCORE-DROP] {pos.symbol} RS declining {SCORE_DROP_DAYS} days — exiting")
                evict_sig = Signal(
                    date=today, symbol=pos.symbol, action="SELL",
                    score=0, price=prices.get(pos.symbol, pos.entry_price),
                    reason="SCORE_DROP_EXIT",
                )
                self._execute_sell(today, pos, evict_sig, prices)
                pv = self.portfolio_value(prices)
                remaining = [p for p in self.open_positions
                             if not is_defensive_symbol(p.symbol)
                             and p.symbol != SAFE_HAVEN_SYMBOL]
                if remaining:
                    each_cash = self.cash / len(remaining)
                    for recv in remaining:
                        recv_price = prices.get(recv.symbol, recv.entry_price)
                        recv_ind   = (indicators or {}).get(recv.symbol, {})
                        recv_ema20 = recv_ind.get("ema_20") or recv_ind.get("ema_fast") or 0
                        if recv_ema20 > 0 and (recv_price - recv_ema20) / recv_ema20 > 0.05:
                            continue
                        add_price = round_to_tick(recv_price)
                        cur_val   = recv.shares * add_price
                        max_add   = max(0, pv * MAX_STOCK_ALLOCATION_PCT - cur_val)
                        add_budget = min(each_cash * (1.0 - SIZER_CASH_BUFFER_PCT), max_add)
                        add_val    = add_budget - buy_charges(add_budget).total
                        add_shares = calculate_shares_for_value(add_val, add_price)
                        if add_shares > 0:
                            old_val = recv.shares * recv.entry_price
                            new_val = add_shares * add_price
                            recv.entry_price = round_to_tick((old_val + new_val) / (recv.shares + add_shares))
                            recv.shares += add_shares
                            self.cash -= (add_shares * add_price) + buy_charges(add_shares * add_price).total
                            repo.save_position(recv)
                            logger.info(f"  [SCORE-DROP-ADD] {recv.symbol:<12} +{add_shares} shares (50% split)")
                break  # one score-drop exit per session

        # ── A.3 Ride the Winner: P&L gap ≥ N% → sell worst, ALL to best ──
        ride_winner_fired = False
        if RIDE_WINNER_ENABLED:
            non_def = [p for p in self.open_positions
                       if not is_defensive_symbol(p.symbol)
                       and p.symbol != SAFE_HAVEN_SYMBOL]
            if len(non_def) >= 2:
                def _pnl_pct(p):
                    cp = prices.get(p.symbol, p.entry_price)
                    return (cp - p.entry_price) / p.entry_price if p.entry_price > 0 else 0.0
                worst = min(non_def, key=_pnl_pct)
                best  = max(non_def, key=_pnl_pct)
                worst_pct  = _pnl_pct(worst)
                best_pct   = _pnl_pct(best)
                best_price = prices.get(best.symbol, best.entry_price)
                best_val   = best.shares * best_price
                best_ind   = (indicators or {}).get(best.symbol, {})
                ema20      = best_ind.get("ema_20") or best_ind.get("ema_fast") or 0
                pv         = self.portfolio_value(prices)
                not_extended = (ema20 == 0 or (best_price - ema20) / ema20 <= 0.05)
                has_room     = best_val < pv * MAX_STOCK_ALLOCATION_PCT

                if ((best_pct - worst_pct) >= RIDE_WINNER_GAP_PCT
                        and has_room and not_extended
                        and worst.symbol != best.symbol):
                    logger.info(
                        f"  [RIDE] {worst.symbol} {worst_pct:+.1%} → {best.symbol} {best_pct:+.1%}"
                    )
                    evict_sig = Signal(
                        date=today, symbol=worst.symbol, action="SELL",
                        score=0, price=prices.get(worst.symbol, worst.entry_price),
                        reason="RIDE_WINNER_OUT",
                    )
                    self._execute_sell(today, worst, evict_sig, prices)
                    # Add ALL freed cash to winner
                    pv = self.portfolio_value(prices)
                    raw_add = prices.get(best.symbol)
                    if raw_add:
                        add_price     = round_to_tick(raw_add)
                        best_val_now  = best.shares * add_price
                        max_add       = max(0, pv * MAX_STOCK_ALLOCATION_PCT - best_val_now)
                        add_budget    = min(self.cash * (1.0 - SIZER_CASH_BUFFER_PCT), max_add)
                        add_val       = add_budget - buy_charges(add_budget).total
                        add_shares    = calculate_shares_for_value(add_val, add_price)
                        if add_shares > 0:
                            if self.broker:
                                from broker.base import OrderRequest, OrderSide, OrderType, OrderStatus
                                req = OrderRequest(
                                    symbol=best.symbol, side=OrderSide.BUY,
                                    quantity=add_shares, order_type=OrderType.MARKET,
                                )
                                res = self.broker.place_order_with_retry(req)
                                if res.order_id:
                                    res = self._await_order_completion(res.order_id)
                                if res.status != OrderStatus.COMPLETE:
                                    logger.error(f"  [Live] RIDE_ADD failed for {best.symbol}: {res.status}")
                                    add_shares = 0
                                elif res.avg_price > 0:
                                    add_price = round_to_tick(res.avg_price)
                            if add_shares > 0:
                                old_val = best.shares * best.entry_price
                                new_val = add_shares * add_price
                                best.entry_price = round_to_tick((old_val + new_val) / (best.shares + add_shares))
                                best.shares += add_shares
                                self.cash -= (add_shares * add_price) + buy_charges(add_shares * add_price).total
                                repo.save_position(best)
                                logger.info(f"  [RIDE-ADD] {best.symbol:<12} +{add_shares} shares @ ₹{add_price:.2f}")
                    ride_winner_fired = True

        # 0.5 Rotation: exit laggard → add to winner (before signal execution)
        # Must run before sell signals so LAGGARD_EXIT can't steal the loser first.
        # Skip if ride-winner already acted this session.
        if ROTATION_ENABLED and not ride_winner_fired:
            non_def = [p for p in self.open_positions
                       if not is_defensive_symbol(p.symbol)
                       and p.symbol != SAFE_HAVEN_SYMBOL]
            if len(non_def) >= 2:
                _portfolio_val = self.portfolio_value(prices)
                worst = min(non_def, key=lambda p: (indicators or {}).get(p.symbol, {}).get("composite_rank", 101))
                best  = max(non_def, key=lambda p: (indicators or {}).get(p.symbol, {}).get("composite_rank", 0))
                worst_rs   = float((indicators or {}).get(worst.symbol, {}).get("composite_rank", 101))
                best_rs    = float((indicators or {}).get(best.symbol,  {}).get("composite_rank", 0))
                best_price = prices.get(best.symbol, best.entry_price)
                best_val   = best.shares * best_price
                best_ind   = (indicators or {}).get(best.symbol, {})
                ema20      = best_ind.get("ema_20") or best_ind.get("ema_fast") or 0
                not_extended = (ema20 == 0 or (best_price - ema20) / ema20 <= 0.05)
                has_room     = best_val < _portfolio_val * MAX_STOCK_ALLOCATION_PCT

                if (worst_rs < ROTATE_EXIT_RS
                        and best_rs >= ROTATE_INTO_RS
                        and (best_rs - worst_rs) >= ROTATE_MIN_GAP
                        and has_room and not_extended
                        and worst.symbol != best.symbol):
                    logger.info(
                        f"  [ROTATE] {worst.symbol} RS={worst_rs:.0f} → {best.symbol} RS={best_rs:.0f}"
                    )
                    evict_sig = Signal(
                        date=today, symbol=worst.symbol, action="SELL",
                        score=0, price=prices.get(worst.symbol, worst.entry_price),
                        reason="ROTATE_OUT",
                    )
                    self._execute_sell(today, worst, evict_sig, prices)
                    # Add freed cash to the winner (mirror pyramid logic)
                    _portfolio_val = self.portfolio_value(prices)
                    raw_add = prices.get(best.symbol)
                    if raw_add and best_ind:
                        add_price = round_to_tick(raw_add)
                        best_val_now = best.shares * add_price
                        max_add = max(0, _portfolio_val * MAX_STOCK_ALLOCATION_PCT - best_val_now)
                        add_budget = min(self.cash * (1.0 - SIZER_CASH_BUFFER_PCT), max_add)
                        target_value = add_budget - buy_charges(add_budget).total
                        add_shares = calculate_shares_for_value(target_value, add_price)
                        if add_shares > 0:
                            if self.broker:
                                from broker.base import OrderRequest, OrderSide, OrderType, OrderStatus
                                req = OrderRequest(
                                    symbol=best.symbol, side=OrderSide.BUY,
                                    quantity=add_shares, order_type=OrderType.MARKET,
                                )
                                res = self.broker.place_order_with_retry(req)
                                if res.order_id:
                                    res = self._await_order_completion(res.order_id)
                                if res.status != OrderStatus.COMPLETE:
                                    logger.error(f"  [Live] ROTATE_ADD failed for {best.symbol}: {res.status}")
                                    add_shares = 0
                                elif res.avg_price > 0:
                                    add_price = round_to_tick(res.avg_price)
                            if add_shares > 0:
                                old_val = best.shares * best.entry_price
                                new_val = add_shares * add_price
                                best.entry_price = round_to_tick((old_val + new_val) / (best.shares + add_shares))
                                best.shares += add_shares
                                self.cash -= (add_shares * add_price) + buy_charges(add_shares * add_price).total
                                repo.save_position(best)
                                logger.info(f"  [ROTATE-ADD] {best.symbol:<12} RS={best_rs:.0f} +{add_shares} shares @ ₹{add_price:.2f}")
                                if self.broker:
                                    from broker.base import OrderRequest, OrderSide, OrderType, OrderStatus
                                    for gtt_id in self.broker.get_pending_gtt_orders(best.symbol):
                                        self.broker.cancel_gtt_order(gtt_id)
                                    gtt_req = OrderRequest(
                                        symbol=best.symbol, side=OrderSide.SELL,
                                        quantity=best.shares, order_type=OrderType.MARKET,
                                        price=gtt_stop_limit_price(best.trailing_stop),
                                        is_gtt=True, gtt_trigger_price=best.trailing_stop,
                                    )
                                    gtt_res = self.broker.place_order_with_retry(gtt_req)
                                    if gtt_res.status in (OrderStatus.OPEN, OrderStatus.COMPLETE, OrderStatus.PENDING):
                                        logger.info(f"  [Live] GTT updated after ROTATE_ADD: {best.symbol}")
                                        self._gtt_synced_this_run.add(best.symbol)
                                    else:
                                        logger.warning(f"  [Live] GTT update FAILED after ROTATE_ADD for {best.symbol}")
                                        _alert_naked_stop(best.symbol, best.shares, best.trailing_stop,
                                                          f"GTT update failed after ROTATE_ADD: {gtt_res.rejection_reason}")

        # 1. Execute SELLS
        sell_signals = [s for s in signals if s.action == "SELL"]
        for sig in sell_signals:
            pos = next((p for p in self.open_positions if p.symbol == sig.symbol), None)
            if pos:
                self._execute_sell(today, pos, sig, prices)

        # 2. Execute BUYS (Zero Cash / Dynamic Slot Allocation)
        buy_signals = sorted([s for s in signals if s.action == "BUY"], key=lambda x: x.score, reverse=True)
        available_slots = MAX_OPEN_POSITIONS - len(self.open_positions)
        portfolio_val = self.portfolio_value(prices)

        # ── Rank replacement: if slots full and a meaningfully stronger stock is waiting,
        # evict the weakest holder so the top candidate can enter this session ──────────
        REPLACE_MIN_NEW_RS  = 85.0
        REPLACE_MAX_HELD_RS = 55.0
        REPLACE_MIN_GAP     = 25.0
        if buy_signals and available_slots == 0:
            best_cand = buy_signals[0]
            non_haven = [p for p in self.open_positions if p.symbol != SAFE_HAVEN_SYMBOL]
            if non_haven:
                weakest = min(non_haven, key=lambda p: (indicators or {}).get(p.symbol, {}).get("composite_rank", 101))
                weakest_rs = float((indicators or {}).get(weakest.symbol, {}).get("composite_rank", 101))
                if (best_cand.score >= REPLACE_MIN_NEW_RS
                        and weakest_rs <= REPLACE_MAX_HELD_RS
                        and (best_cand.score - weakest_rs) >= REPLACE_MIN_GAP):
                    logger.info(
                        f"  [REPLACE] {weakest.symbol} RS={weakest_rs:.0f} evicted "
                        f"→ {best_cand.symbol} RS={best_cand.score:.0f}"
                    )
                    evict_sig = Signal(
                        date=today, symbol=weakest.symbol, action="SELL",
                        score=0, price=prices.get(weakest.symbol, weakest.entry_price),
                        reason="RANK_REPLACED",
                    )
                    self._execute_sell(today, weakest, evict_sig, prices)
                    available_slots = MAX_OPEN_POSITIONS - len(self.open_positions)
                    portfolio_val = self.portfolio_value(prices)

        if self.cash > (portfolio_val * 0.005): # Only invest if we have at least 0.5% cash
            num_to_buy = min(len(buy_signals), available_slots)

            if num_to_buy > 0:
                # Case 1: Fill empty slots with new leaders
                # Reserve 5% buffer so charges don't cause order rejection
                spendable = self.cash * (1.0 - SIZER_CASH_BUFFER_PCT)
                base_slot_cash = spendable / max(available_slots, 1)
                # Graduated size reduction under drawdown — mirrors backtest engine
                current_dd = (self.peak_value - portfolio_val) / self.peak_value if self.peak_value > 0 else 0.0
                if current_dd >= DRAWDOWN_REDUCE_SIZE_PCT * DRAWDOWN_REDUCE_TIER2_MULT:
                    base_slot_cash *= 0.25
                    logger.info("[Risk] DD %.1f%% — slot size cut to 25%%", current_dd * 100)
                elif current_dd >= DRAWDOWN_REDUCE_SIZE_PCT:
                    base_slot_cash *= 0.50
                    logger.info("[Risk] DD %.1f%% — slot size cut to 50%%", current_dd * 100)
                for i in range(num_to_buy):
                    # Check overall risk limits first
                    allowed, reason = can_open_new_trades(
                        self.new_trades_today, self.open_positions, 
                        portfolio_val, self.peak_value
                    )
                    if not allowed:
                        logger.warning(f"  [Risk] Skip buy: {reason}")
                        break

                    sig = buy_signals[i]

                    # Feature 3: skip if RS rank has been falling for N consecutive days
                    if (SCORE_DROP_EXIT_ENABLED
                            and _is_score_declining(sig.symbol, score_history, SCORE_DROP_DAYS)):
                        logger.info(
                            f"  [SKIP_BUY] {sig.symbol} RS declining {SCORE_DROP_DAYS} days — skip"
                        )
                        continue

                    raw_price = prices.get(sig.symbol)
                    if not raw_price: continue
                    price = round_to_tick(raw_price)

                    # ── ML Veto Filter ──
                    from config.settings import ML_ENABLED, ML_MIN_CONFIDENCE
                    if ML_ENABLED:
                        from ml.model import get_model_handler
                        ml_handler = get_model_handler()
                        if ml_handler.is_ready:
                            ml_conf, _ = ml_handler.predict(
                                sig.indicators,
                                regime=sig.indicators.get("regime", "BULL"),
                            )
                            if ml_conf < ML_MIN_CONFIDENCE:
                                logger.info(f"  [ML] Veto {sig.symbol}: Confidence {ml_conf:.2f} < {ML_MIN_CONFIDENCE}")
                                continue

                    atr = sig.indicators.get("atr", 0)
                    stops = initial_stops(price, atr=atr)

                    # Safe haven: deploy up to 50% of portfolio value — limits drawdown from gold volatility
                    if sig.symbol == SAFE_HAVEN_SYMBOL:
                        max_safe_haven = portfolio_val * 0.50
                        useable_cash = min(self.cash * (1.0 - SIZER_CASH_BUFFER_PCT), max_safe_haven)
                        target_val = useable_cash - buy_charges(useable_cash).total
                        shares = calculate_shares_for_value(target_val, price)
                        logger.info(f"  [SafeHaven] 50% cap allocation: ₹{useable_cash:,.0f} → {shares} shares @ ₹{price}")
                    else:
                        # Equal-weight sizing: cash / available slots, capped at stock cap
                        slot_cash = min(base_slot_cash, portfolio_val * MAX_STOCK_ALLOCATION_PCT)
                        target_val = slot_cash - buy_charges(slot_cash).total
                        shares = calculate_shares_for_value(target_val, price)

                    if shares > 0:
                        # Safe haven bypasses stock/sector caps — it's a cash parking mechanism
                        if sig.symbol != SAFE_HAVEN_SYMBOL:
                            val = position_value(shares, price)
                            ok, reason = can_open_position(
                                sig.symbol, val, portfolio_val,
                                self.open_positions, prices
                            )
                            if not ok:
                                logger.warning(f"  [Risk] Skip {sig.symbol}: {reason}")
                                continue

                        # ── 1. Live Broker Execution ──
                        # Run is always at 14:50 IST (market hours) — direct MARKET order, no AMO
                        if self.broker:
                            from broker.base import OrderRequest, OrderSide, OrderType, OrderStatus

                            # Cancel any pending GTT orders for this symbol before placing new order
                            pending_gtts = self.broker.get_pending_gtt_orders(sig.symbol)
                            for gtt_id in pending_gtts:
                                logger.info(f"  [Live] Cancelling stale GTT {gtt_id} for {sig.symbol}")
                                self.broker.cancel_gtt_order(gtt_id)

                            logger.info(f"  [Live] Placing MARKET BUY for {sig.symbol} (Qty: {shares})")
                            req = OrderRequest(
                                symbol=sig.symbol, side=OrderSide.BUY, quantity=shares,
                                order_type=OrderType.MARKET,
                            )
                            res = self.broker.place_order_with_retry(req)
                            if res.status not in (OrderStatus.OPEN, OrderStatus.COMPLETE, OrderStatus.PENDING):
                                logger.error(f"  [Live] BUY failed for {sig.symbol}: {res.status} | {res.rejection_reason}")
                                continue

                            # Await fill to capture the actual entry price (mirrors ADD/SELL).
                            # If still PENDING/timed-out, fall back to estimate and let the
                            # next-run broker sync reconcile shares/price from holdings.
                            if res.order_id:
                                res = self._await_order_completion(res.order_id)
                            if res.status == OrderStatus.REJECTED:
                                logger.error(f"  [Live] BUY rejected after await for {sig.symbol}: {res.rejection_reason}")
                                continue
                            if res.status == OrderStatus.COMPLETE and res.avg_price > 0:
                                price = round_to_tick(res.avg_price)
                                stops = initial_stops(price, atr=atr)  # recompute stops off real fill
                                logger.info(f"  [Live] MARKET BUY filled: {sig.symbol} @ ₹{price:.2f} (Qty: {shares})")
                            else:
                                logger.warning(
                                    f"  [Live] BUY for {sig.symbol} not confirmed (status={res.status}); "
                                    f"using estimate ₹{price:.2f} — sync will reconcile."
                                )

                            # Place GTT stop-loss: auto-SELL if price drops to stop_loss (best-effort)
                            gtt_req = OrderRequest(
                                symbol=sig.symbol, side=OrderSide.SELL, quantity=shares,
                                order_type=OrderType.MARKET,
                                price=gtt_stop_limit_price(stops["stop_loss"]),
                                is_gtt=True, gtt_trigger_price=stops["stop_loss"],
                            )
                            gtt_res = self.broker.place_order_with_retry(gtt_req)
                            if gtt_res.status in (OrderStatus.OPEN, OrderStatus.COMPLETE, OrderStatus.PENDING):
                                logger.info(f"  [Live] GTT stop-loss placed: {sig.symbol} trigger=₹{stops['stop_loss']:,.2f} (gtt_id={gtt_res.order_id})")
                                self._gtt_synced_this_run.add(sig.symbol)
                            else:
                                logger.warning(f"  [Live] GTT stop-loss FAILED for {sig.symbol}: {gtt_res.rejection_reason} — monitor manually")
                                _alert_naked_stop(sig.symbol, shares, stops["stop_loss"], gtt_res.rejection_reason)

                            # Add position locally so subsequent signals see correct slot count
                            # (broker sync will reconcile shares/price on next run)
                            provisional_pos = Position(
                                symbol=sig.symbol, sector=sig.indicators.get("sector", "Unknown"),
                                entry_date=today, entry_price=price, shares=shares,
                                stop_loss=stops["stop_loss"], take_profit=stops["take_profit"],
                                trailing_stop=stops["trailing_stop"], peak_price=stops["peak_price"],
                                atr_at_entry=atr,
                            )
                            self.cash -= (shares * price) + buy_charges(shares * price).total
                            self.open_positions.append(provisional_pos)
                            self.new_trades_today += 1
                            continue  # DB position saved by broker sync on next run

                        # ── 2. Local State Update (Paper Trading Only) ──
                        alloc_display = useable_cash if sig.symbol == SAFE_HAVEN_SYMBOL else slot_cash
                        logger.info(f"  [BUY]  {sig.symbol:<12} @ ₹{price:>9,.2f} | RS Rank: {sig.score:.1f} | Allocation: ₹{alloc_display:,.0f}")
                        new_pos = Position(
                            symbol=sig.symbol, sector=sig.indicators.get("sector", "Unknown"),
                            entry_date=today, entry_price=price, shares=shares,
                            stop_loss=stops["stop_loss"], take_profit=stops["take_profit"],
                            trailing_stop=stops["trailing_stop"], peak_price=stops["peak_price"],
                            atr_at_entry=atr,
                        )
                        self.cash -= (shares * price) + buy_charges(shares * price).total
                        self.open_positions.append(new_pos)
                        repo.save_position(new_pos)
                        self.new_trades_today += 1

            elif len(self.open_positions) > 0:
                # Case 2: No new buy signals — pyramid into the strongest winner
                # ONLY add if the position is pulling back toward EMA20 (not chasing extended price)
                best_pos = None
                highest_rs = -1.0
                for pos in self.open_positions:
                    rs = (indicators or {}).get(pos.symbol, {}).get("composite_rank", 0)
                    if rs > highest_rs:
                        highest_rs = rs
                        best_pos = pos

                if best_pos:
                    raw_price = prices.get(best_pos.symbol)
                    ind_best = (indicators or {}).get(best_pos.symbol, {})
                    ema20 = ind_best.get('ema_20') or ind_best.get('ema_fast') or 0
                    if raw_price and ema20 > 0:
                        dist_from_ema = (raw_price - ema20) / ema20
                        if dist_from_ema > 0.05:
                            logger.info(
                                f"  [SKIP_ADD] {best_pos.symbol} extended "
                                f"{dist_from_ema:.1%} above EMA20 — waiting for pullback"
                            )
                            raw_price = None  # block the add

                if best_pos and best_pos.symbol == SAFE_HAVEN_SYMBOL:
                    best_pos = None  # Never pyramid safe haven — 5% cash reserve is intentional

                if best_pos and raw_price:
                    price = round_to_tick(raw_price)
                    # Cap ADD: don't push any position past MAX_STOCK_ALLOCATION_PCT
                    max_add_value = max(0, portfolio_val * MAX_STOCK_ALLOCATION_PCT
                                       - best_pos.shares * price)
                    add_budget = min(self.cash * (1.0 - SIZER_CASH_BUFFER_PCT), max_add_value)
                    target_value = add_budget - buy_charges(add_budget).total
                    add_shares = calculate_shares_for_value(target_value, price)
                    if add_shares > 0:
                        # ── 1. Live Broker Execution ──
                        # Run at 14:50 IST — direct MARKET ADD, no AMO
                        if self.broker:
                            from broker.base import OrderRequest, OrderSide, OrderType, OrderStatus
                            logger.info(f"  [Live] Placing MARKET ADD for {best_pos.symbol} (Qty: {add_shares})")
                            req = OrderRequest(
                                symbol=best_pos.symbol, side=OrderSide.BUY,
                                quantity=add_shares, order_type=OrderType.MARKET,
                            )
                            res = self.broker.place_order_with_retry(req)
                            if res.order_id:
                                res = self._await_order_completion(res.order_id)
                            if res.status != OrderStatus.COMPLETE:
                                logger.error(f"  [Live] ADD failed for {best_pos.symbol}: {res.status} | {res.rejection_reason}")
                                add_shares = 0  # skip local state update; fall through to snapshot
                            elif res.avg_price > 0:
                                price = round_to_tick(res.avg_price)

                        # ── 2. Local State Update ──
                        if add_shares > 0:
                            logger.info(f"  [ADD]  {best_pos.symbol:<12} | Pyramiding near EMA20 (₹{target_value:,.0f}) | RS: {highest_rs:.1f}")
                            old_val = best_pos.shares * best_pos.entry_price
                            new_val = add_shares * price
                            best_pos.entry_price = round_to_tick((old_val + new_val) / (best_pos.shares + add_shares))
                            best_pos.shares += add_shares
                            self.cash -= (add_shares * price) + buy_charges(add_shares * price).total
                            repo.save_position(best_pos)

                            # Update GTT: cancel old (wrong quantity) and place new for full position
                            if self.broker:
                                from broker.base import OrderRequest, OrderSide, OrderType, OrderStatus
                                for gtt_id in self.broker.get_pending_gtt_orders(best_pos.symbol):
                                    self.broker.cancel_gtt_order(gtt_id)
                                gtt_req = OrderRequest(
                                    symbol=best_pos.symbol, side=OrderSide.SELL,
                                    quantity=best_pos.shares, order_type=OrderType.MARKET,
                                    price=gtt_stop_limit_price(best_pos.trailing_stop),
                                    is_gtt=True, gtt_trigger_price=best_pos.trailing_stop,
                                )
                                gtt_res = self.broker.place_order_with_retry(gtt_req)
                                if gtt_res.status in (OrderStatus.OPEN, OrderStatus.COMPLETE, OrderStatus.PENDING):
                                    logger.info(f"  [Live] GTT updated after ADD: {best_pos.symbol} trigger=₹{best_pos.trailing_stop:,.2f} qty={best_pos.shares}")
                                    self._gtt_synced_this_run.add(best_pos.symbol)
                                else:
                                    logger.warning(f"  [Live] GTT update FAILED after ADD for {best_pos.symbol} — monitor manually")
                                    _alert_naked_stop(best_pos.symbol, best_pos.shares, best_pos.trailing_stop,
                                                      f"GTT update failed after ADD: {gtt_res.rejection_reason}")

        # 2.5 Ratchet broker GTT stops up to match climbed trailing stops
        self._reconcile_gtt_stops()

        # 3. Save Daily Snapshot
        # Re-sync cash from broker — detect any external deposits before updating self.cash
        injection_today = 0.0
        if self.broker:
            try:
                broker_cash = self.broker.get_available_cash()
                if broker_cash > 0:
                    # Cash the broker has beyond what our trade accounting expects = external deposit
                    injection_today = max(0.0, round(broker_cash - self.cash, 2))
                    if injection_today > 500:  # ₹500 threshold avoids float/settlement noise
                        logger.info(
                            "[Capital] External cash deposit detected: ₹%.2f — excluded from P&L",
                            injection_today,
                        )
                    self.cash = broker_cash
                # else: broker returned ₹0 (API error) — keep our tracked cash
            except Exception as e:
                logger.warning(f"[Live] Failed to re-sync cash for snapshot: {e}")
        elif fund_injection > 0:
            # Paper mode: caller passed explicit injection amount (via --inject CLI arg)
            injection_today = fund_injection
            self.cash += injection_today
            logger.info(
                "[Capital] Paper-mode fund injection: ₹%.2f added to tracked cash (now ₹%.2f)",
                injection_today, self.cash,
            )

        pv_after = self.portfolio_value(prices)
        invested_cost = sum(p.entry_price * p.shares for p in self.open_positions)
        market_val = portfolio_invested_value(self.open_positions, prices)

        # Load recent snapshots for prev_total; use SQL sum for all-time injections (no 100-row limit)
        all_snaps = repo.load_snapshots()
        prev_total      = all_snaps[-1].total_value if all_snaps else self.initial_capital
        past_injected   = total_capital_injected_ever()
        total_injected  = past_injected + injection_today

        # Day P&L: market movement only — strip out any external deposit made today
        day_pnl = pv_after - injection_today - prev_total

        # Cumulative P&L: true investment return = current value minus all capital ever deployed
        # baseline = organic starting capital (first snapshot minus any day-1 injection)
        db_baseline = repo.load_baseline_capital()
        # On first ever run: no prior snapshots → baseline = organic capital today (before injection)
        baseline = db_baseline if db_baseline is not None else (pv_after - injection_today)
        cum_pnl  = pv_after - baseline - total_injected

        snap = PortfolioSnapshot(
            date=today, cash=self.cash, invested=invested_cost,
            total_value=pv_after, open_positions=len(self.open_positions),
            daily_pnl=day_pnl, cumulative_pnl=cum_pnl, regime=regime,
            capital_injected=injection_today,
        )
        repo.save_snapshot(snap)
        
        logger.info(
            f"[Capital] {today} | Total: ₹{pv_after:,.0f} | Cash: ₹{self.cash:,.0f} | "
            f"Invested Cost: ₹{invested_cost:,.0f} | Market Val: ₹{market_val:,.0f} | Day P&L: ₹{day_pnl:+,.0f}"
        )

    def _execute_sell(self, today: date, pos: Position, sig: Signal, prices: dict):
        """Execute a sell order."""
        raw_price = prices.get(sig.symbol, sig.price)
        price = round_to_tick(raw_price)

        # ── 1. Live Broker Execution ──
        # Run at 14:50 IST — immediate MARKET sell, await fill, then close in DB
        if self.broker:
            from broker.base import OrderRequest, OrderSide, OrderType, OrderStatus

            # Cancel any open GTT stop-loss orders before selling — prevents double-sell
            pending_gtts = self.broker.get_pending_gtt_orders(pos.symbol)
            for gtt_id in pending_gtts:
                logger.info(f"  [Live] Cancelling GTT {gtt_id} before SELL of {pos.symbol}")
                self.broker.cancel_gtt_order(gtt_id)

            logger.info(f"  [Live] Placing MARKET SELL for {pos.symbol} (Qty: {pos.shares})")
            req = OrderRequest(
                symbol=pos.symbol, side=OrderSide.SELL, quantity=pos.shares,
                order_type=OrderType.MARKET,
            )
            res = self.broker.place_order_with_retry(req)
            if res.order_id:
                res = self._await_order_completion(res.order_id)
            if res.status not in (OrderStatus.COMPLETE, OrderStatus.OPEN, OrderStatus.PENDING):
                logger.error(f"  [Live] SELL failed for {pos.symbol}: {res.status} | {res.rejection_reason}")
                return
            if res.status != OrderStatus.COMPLETE:
                # Timed-out or still PENDING — do NOT close in DB; let broker sync reconcile.
                # Closing here with unconfirmed fill would corrupt cash and block next-day sync.
                logger.warning(
                    f"  [Live] SELL for {pos.symbol} timed out (status={res.status}). "
                    "Position left OPEN — broker sync will close it when fill is confirmed."
                )
                try:
                    from notifications.telegram import send_message
                    send_message(
                        f"⚠️ <b>SELL Timeout</b> — {pos.symbol}\n"
                        f"Order {res.order_id} status={res.status}. Position left open. "
                        f"Check Upstox and verify manually."
                    )
                except Exception:
                    pass
                return  # abort — do not close position or update cash
            elif res.avg_price > 0:
                price = round_to_tick(res.avg_price)  # use actual fill price for P&L
            logger.info(f"  [Live] MARKET SELL confirmed: {pos.symbol} @ ₹{price:.2f}")

        # ── 2. Local State Update (Paper Trading Only) ──
        result = net_pnl(pos.entry_price, price, pos.shares)        
        # Log actual execution
        logger.info(f"  [SELL] {pos.symbol:<12} @ ₹{price:>9,.2f} | P&L: {result['net_pct']:>+5.1f}% | {sig.reason}")

        trade = Trade(
            symbol=pos.symbol, sector=pos.sector,
            entry_date=pos.entry_date, exit_date=today,
            entry_price=pos.entry_price, exit_price=price,
            shares=pos.shares, gross_pnl=result["gross_pnl"],
            charges=result["total_charges"], net_pnl=result["net_pnl"],
            exit_reason=sig.reason, hold_days=(today - (pos.entry_date.date() if hasattr(pos.entry_date, 'date') else pos.entry_date)).days
        )
        
        # Atomic DB write first — if it raises, in-memory state stays consistent
        repo.close_position_and_save_trade(pos.symbol, trade)
        self.cash += (price * pos.shares) - result["sell_charges"]["total"]
        self.open_positions = [p for p in self.open_positions if p.symbol != pos.symbol]

    def _reconcile_gtt_stops(self):
        """Ratchet broker-side GTT stop-loss up to each position's current trailing
        stop. Without this, the GTT placed at entry sits at the original hard stop
        while the daily-updated trailing stop climbs — leaving the broker safety net
        stale. Only refreshes positions whose trailing stop moved UP this run and
        that were not already (re)placed by a BUY/ADD/ROTATE this session, to avoid
        needless cancel/replace churn."""
        if not self.broker:
            return
        from broker.base import OrderRequest, OrderSide, OrderType, OrderStatus

        targets = self._gtt_needs_refresh - self._gtt_synced_this_run
        for pos in list(self.open_positions):
            if pos.symbol not in targets:
                continue
            trigger = pos.trailing_stop if (pos.trailing_stop and pos.trailing_stop > 0) else pos.stop_loss
            if trigger <= 0 or pos.shares <= 0:
                continue
            try:
                # Cancel-first to avoid duplicate GTTs, then place at the new trigger.
                # If any cancel fails, do NOT place a replacement — a single stale GTT
                # is safer than a duplicate live sell order (the 2026-07-01 CGPOWER
                # incident: a failed cancel + unconditional replace left two GTTs on
                # the same position). The old GTT stays active at its old, lower
                # trigger; gtt_price_audit.py's daily cron will flag the mismatch.
                pending = self.broker.get_pending_gtt_orders(pos.symbol)
                cancel_failed = False
                for gtt_id in pending:
                    if not self.broker.cancel_gtt_order(gtt_id):
                        cancel_failed = True
                if cancel_failed:
                    logger.warning(
                        "  [GTT-Sync] %s cancel of stale GTT failed — skipping "
                        "refresh to avoid a duplicate GTT", pos.symbol
                    )
                    try:
                        import html
                        from notifications.telegram import send_message
                        send_message(
                            f"⚠️ <b>GTT ratchet skipped</b> — {html.escape(pos.symbol)}\n"
                            f"Cancel of old GTT failed; new stop ₹{trigger:,.2f} was NOT "
                            f"placed to avoid a duplicate.\n"
                            f"Old GTT (stale, lower trigger) is still active. Check Upstox manually."
                        )
                    except Exception as e:
                        logger.warning("[Alert] Failed to send GTT-skip alert for %s: %s", pos.symbol, e)
                    continue
                gtt_req = OrderRequest(
                    symbol=pos.symbol, side=OrderSide.SELL, quantity=pos.shares,
                    order_type=OrderType.MARKET, price=gtt_stop_limit_price(trigger),
                    is_gtt=True, gtt_trigger_price=trigger,
                )
                gtt_res = self.broker.place_order_with_retry(gtt_req)
                if gtt_res.status in (OrderStatus.OPEN, OrderStatus.COMPLETE, OrderStatus.PENDING):
                    logger.info("  [GTT-Sync] %s stop ratcheted → ₹%.2f", pos.symbol, trigger)
                else:
                    logger.warning("  [GTT-Sync] %s GTT refresh FAILED: %s", pos.symbol, gtt_res.rejection_reason)
                    _alert_naked_stop(pos.symbol, pos.shares, trigger,
                                      f"GTT ratchet refresh failed: {gtt_res.rejection_reason}")
            except Exception as e:
                logger.warning("  [GTT-Sync] %s reconcile error: %s", pos.symbol, e)
