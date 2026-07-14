"""
Portfolio manager — the single source of truth for portfolio state.
Surgical Logging: Only actual BUY/SELL executions are logged.
"""

import json
import logging
import os
from datetime import date
from typing import List, Dict

from db.models import Position, Trade, Signal, PortfolioSnapshot
from db import repository as repo
from db.repository import total_capital_injected_ever
from portfolio.sizer import calculate_shares_for_value, position_value
from portfolio.allocator import can_open_position, portfolio_invested_value
from portfolio.risk import can_open_new_trades
from strategy.exit import initial_stops
from charges.calculator import net_pnl, buy_charges
from broker.base import BaseBroker
from config.settings import (
    INITIAL_CAPITAL, round_to_tick, MAX_OPEN_POSITIONS, SAFE_HAVEN_SYMBOL, SIZER_CASH_BUFFER_PCT,
    GOLD_EQUAL_SLOT_SIZING,
    MAX_STOCK_ALLOCATION_PCT, DRAWDOWN_REDUCE_SIZE_PCT, DRAWDOWN_REDUCE_TIER2_MULT, GTT_LIMIT_BUFFER_PCT,
    REPLACE_MIN_NEW_RS, REPLACE_MAX_HELD_RS, REPLACE_MIN_GAP, MIN_PROFIT_SOFT,
    MAX_NEW_TRADES_PER_DAY, DRAWDOWN_KILL_SWITCH_PCT, DD_THROTTLE_DISABLED_ENABLED,
    REGIME_SIZE_MULT_BEAR, REGIME_SIZE_MULT_BULL,
)
from strategy.defensive_portfolio import (
    ROTATION_ENABLED, ROTATE_EXIT_RS, ROTATE_INTO_RS, ROTATE_MIN_GAP,
    RIDE_WINNER_ENABLED, RIDE_WINNER_GAP_PCT,
    SCORE_DROP_EXIT_ENABLED, SCORE_DROP_DAYS,
    is_defensive_symbol, is_score_declining,
)

_SCORE_HISTORY_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "score_history.json")


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


def _alert_gtt_check_uncertain(symbol: str, context: str) -> None:
    """Telegram-alert when we could not confirm whether a stale GTT for `symbol`
    was cancelled (lookup failed or a cancel call itself failed). Best-effort."""
    try:
        import html
        from notifications.telegram import send_message
        send_message(
            f"⚠️ <b>GTT state unverified</b> — {html.escape(str(symbol))}\n"
            f"Context: {html.escape(str(context))}\n"
            f"Could not confirm old GTT(s) were cancelled. There may be a stale "
            f"or duplicate GTT on Upstox — check manually."
        )
    except Exception as e:
        logger.warning("[Alert] Failed to send GTT-uncertain alert for %s: %s", symbol, e)


def cancel_stale_gtts(broker, symbol: str, context: str) -> bool:
    """Cancel all pending GTT orders for `symbol` before placing a replacement or
    a new order. Returns True only if every pending GTT was confirmed cancelled
    (or none existed). Returns False — and raises a Telegram alert — if the
    pending-GTT lookup itself failed (ambiguous: API error vs. truly none) or if
    any individual cancel call failed, so callers can treat "unverified" the same
    as "still there" instead of silently assuming it's safe to proceed."""
    pending = broker.get_pending_gtt_orders(symbol)
    if pending is None:
        logger.warning("  [GTT] %s: pending-GTT lookup failed (API error) — %s", symbol, context)
        _alert_gtt_check_uncertain(symbol, context)
        return False
    all_cancelled = True
    for gtt_id in pending:
        logger.info("  [GTT] Cancelling stale GTT %s for %s (%s)", gtt_id, symbol, context)
        if not broker.cancel_gtt_order(gtt_id):
            all_cancelled = False
    if not all_cancelled:
        logger.warning("  [GTT] %s: one or more GTT cancels failed — %s", symbol, context)
        _alert_gtt_check_uncertain(symbol, context)
    return all_cancelled


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
                    # One-time bootstrap only (no snapshot history to derive a strategy-only
                    # baseline from yet) — uses broker-wide value same as before. Fine in
                    # practice since this path only fires once, before any non-strategy
                    # position could exist. See docs/30 §4.
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
            # peak_value feeds the drawdown-throttle comparison against strategy_value()
            # (via portfolio_value()'s alias) — must be sourced from the same strategy-only
            # basis, not account-wide total_value, or drawdown reads wrong once manual
            # positions exist. docs/30. strategy_value falls back to total_value for any
            # pre-migration snapshot row (repository.py load_snapshots), so this is exact
            # for existing history and correct going forward.
            self.peak_value = max(getattr(self, 'peak_value', 0), max(s.strategy_value for s in snapshots))
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

    def account_value(self, prices: dict) -> float:
        """Total broker account value — every position (any origin) + cash.
        Reporting/net-worth only, never a sizing or risk denominator — see
        strategy_value(). docs/30."""
        if self.broker:
            try:
                live_val = self.broker.get_portfolio_value()
                if live_val > 0: return live_val
            except:
                pass

        open_position_value = portfolio_invested_value(self.open_positions, prices)
        return self.cash + open_position_value

    def strategy_value(self, prices: dict) -> float:
        """Cash + strategy-origin positions only. The authoritative denominator
        for position sizing, MAX_STOCK_ALLOCATION_PCT, drawdown-throttle, and
        performance reporting — isolates strategy skill from manual/imported
        broker holdings that the strategy doesn't control. docs/30."""
        strategy_positions = [p for p in self.open_positions if p.origin == "strategy"]
        return self.cash + portfolio_invested_value(strategy_positions, prices)

    def portfolio_value(self, prices: dict) -> float:
        """Deprecated alias for strategy_value() — kept so any call site not yet
        migrated gets the conservative (strategy-only) basis rather than the
        broker-wide total. Prefer strategy_value()/account_value() explicitly.
        docs/30."""
        return self.strategy_value(prices)

    def process_signals(self, today: date, signals: List[Signal], prices: dict, indicators: dict = None, regime: str = None, fund_injection: float = 0.0):
        """Process today's signals: Exits first, then dynamic batch entries."""
        self.new_trades_today = 0
        # Stop-loss/trailing-stop GTTs removed — positions now exit only on the
        # system's own sell signals, executed as real market sells (see _execute_sell).
        if self.broker:
            for pos in self.open_positions:
                cancel_stale_gtts(self.broker, pos.symbol, "signal-only mode — legacy stop GTT cleanup")

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
                if not is_score_declining(pos.symbol, score_history, SCORE_DROP_DAYS):
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
                            if self.broker:
                                from broker.base import OrderRequest, OrderSide, OrderType, OrderStatus
                                req = OrderRequest(
                                    symbol=recv.symbol, side=OrderSide.BUY,
                                    quantity=add_shares, order_type=OrderType.MARKET,
                                )
                                res = self.broker.place_order_with_retry(req)
                                if res.order_id:
                                    res = self._await_order_completion(res.order_id)
                                if res.status != OrderStatus.COMPLETE:
                                    logger.error(f"  [Live] SCORE-DROP-ADD failed for {recv.symbol}: {res.status}")
                                    add_shares = 0
                                elif res.avg_price > 0:
                                    add_price = round_to_tick(res.avg_price)
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
        # Must run before sell signals so a soft exit (e.g. MOMENTUM_DECAY) can't steal the loser first.
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
                                    cancel_stale_gtts(self.broker, best.symbol, "ROTATE_ADD refresh — stop-loss/trail removed")

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
        # evict the weakest holder so the top candidate can enter this session. Mirrors
        # backtest/engine.py's replace-eviction gate: rs_rank (not composite_rank),
        # MIN_PROFIT_SOFT gate, is_defensive_symbol exclusion (not just SAFE_HAVEN_SYMBOL),
        # a risk guard (new-trades/drawdown), and the cash check pulled inside the eviction
        # condition so a low-cash day can't produce a pure eviction with no replacement buy.
        pre_replace_dd = (self.peak_value - portfolio_val) / self.peak_value if self.peak_value > 0 else 0.0
        replace_risk_ok = (self.new_trades_today < MAX_NEW_TRADES_PER_DAY) and (pre_replace_dd < DRAWDOWN_KILL_SWITCH_PCT)
        if buy_signals and available_slots == 0 and replace_risk_ok and self.cash > (portfolio_val * 0.005):
            best_cand = buy_signals[0]
            non_def = [p for p in self.open_positions if not is_defensive_symbol(p.symbol)]
            if non_def:
                weakest = min(non_def, key=lambda p: (indicators or {}).get(p.symbol, {}).get("rs_rank", 101))
                weakest_rs = float((indicators or {}).get(weakest.symbol, {}).get("rs_rank", 101))
                weakest_profit = ((prices.get(weakest.symbol, weakest.entry_price) / weakest.entry_price) - 1) if weakest.entry_price > 0 else 0.0
                if (best_cand.score >= REPLACE_MIN_NEW_RS
                        and weakest_rs <= REPLACE_MAX_HELD_RS
                        and (best_cand.score - weakest_rs) >= REPLACE_MIN_GAP
                        and weakest_profit >= MIN_PROFIT_SOFT):
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
                base_slot_cash *= REGIME_SIZE_MULT_BEAR if regime == "BEAR" else REGIME_SIZE_MULT_BULL
                # Graduated size reduction under drawdown — mirrors backtest engine
                current_dd = (self.peak_value - portfolio_val) / self.peak_value if self.peak_value > 0 else 0.0
                if not DD_THROTTLE_DISABLED_ENABLED:
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
                            and is_score_declining(sig.symbol, score_history, SCORE_DROP_DAYS)):
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
                    if sig.symbol == SAFE_HAVEN_SYMBOL and not GOLD_EQUAL_SLOT_SIZING:
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
                        if sig.symbol != SAFE_HAVEN_SYMBOL or GOLD_EQUAL_SLOT_SIZING:
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
                            cancel_stale_gtts(self.broker, sig.symbol, "pre-BUY cleanup")

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

                            # Stop-loss/trailing-stop GTT removed — exit is signal-only now.

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

                            if self.broker:
                                cancel_stale_gtts(self.broker, best_pos.symbol, "ADD (pyramiding) refresh — stop-loss/trail removed")

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

        # total_value/day_pnl/cum_pnl below stay account-wide for continuity with existing
        # history; strategy_value is the new, separate strategy-attributable column. docs/30.
        pv_after = self.account_value(prices)
        pv_strategy_after = self.strategy_value(prices)
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
            strategy_value=pv_strategy_after,
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
            cancel_stale_gtts(self.broker, pos.symbol, "pre-SELL cleanup")

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


