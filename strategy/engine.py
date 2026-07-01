"""
Event-driven backtesting engine.
Standardized for high-fidelity analysis logs and robustness.
Optimized for speed while supporting intraday execution times.
"""

import logging
import os
from datetime import date, datetime, time as dt_time
from typing import List, Dict, Optional

import pandas as pd
import numpy as np

from db.models import Position, Trade, Signal
from strategy.entry import check_entry
from indicators.composite import compute_indicators
from strategy.relative_strength import compute_rs_for_all
from strategy.regime import detect_regime, is_buy_allowed, regime_position_factor, regime_min_score, is_index_confirming
from strategy.defensive_portfolio import (
    REGIME_SWITCH_DAYS, BULL_RECOVERY_DAYS, REBAL_DAYS, MIN_DEFENSIVE_HOLD_DAYS,
    BEAR_SWING_RS_THRESHOLD, BEAR_SWING_SLOTS, ENTRY_CONFIRM_DAYS,
    is_defensive_symbol, get_defensive_entries, compute_rebalance, GOLD_ETF,
)

# Nifty pullback guard: pause new BULL entries when Nifty drops >X% from 10-day high
NIFTY_PULLBACK_GUARD_PCT = float(os.getenv("NIFTY_PULLBACK_PCT", "0"))
# Portfolio-level stop: close ALL positions if portfolio DD from peak >= this (0 = disabled)
PORTFOLIO_STOP_PCT = float(os.getenv("PORTFOLIO_STOP_PCT", "0"))
from strategy.signals import generate_signals, MIN_DAILY_TURNOVER
from strategy.exit import initial_stops, update_trailing_stop, check_exit_conditions
from portfolio.sizer import calculate_shares_for_value, position_value
from portfolio.allocator import can_open_position, portfolio_invested_value
from portfolio.risk import can_open_new_trades
from strategy.scoring import score_to_size_factor
from charges.calculator import net_pnl, buy_charges
from backtest.slippage import simulate_partial_fill
from data.universe import get_sector
from config.settings import (
    MARKET_INDEX_SYMBOL, INITIAL_CAPITAL,
    SLIPPAGE_FIXED_PCT, SLIPPAGE_MODEL, round_to_tick,
    MAX_OPEN_POSITIONS, EXECUTION_TIMES, EMA_FAST, EMA_SLOW,
    RS_THRESHOLD, DRAWDOWN_KILL_SWITCH_PCT, DRAWDOWN_REDUCE_SIZE_PCT, DRAWDOWN_REDUCE_TIER2_MULT,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL, SAFE_HAVEN_SYMBOL,
    MAX_STOCK_ALLOCATION_PCT, MAX_NEW_TRADES_PER_DAY,
    ATR_TRAIL_MULT_INITIAL, GOLDBEES_PROFIT_EXIT_ONLY, GOLDBEES_MAX_LOSS_PCT,
)

logger = logging.getLogger(__name__)

class BacktestResult:
    def __init__(self):
        self.trades: List[Trade] = []
        self.equity_curve: Dict[date, float] = {}
        self.cash_curve: Dict[date, float] = {}
        self.regime_log: Dict[date, str] = {}
        self.daily_scan_log: List[dict] = []
        self.decision_log: List[dict] = []
        self.final_open_positions: List[Position] = []
        self.transaction_log: List[dict] = []
        self.fund_injections_log: List[dict] = []
        self.total_injected: float = 0.0

class BacktestEngine:
    def __init__(
        self,
        data: Dict[str, pd.DataFrame],
        start_date: date,
        end_date: date,
        initial_capital: float = INITIAL_CAPITAL,
        max_selected: int = 4,
        use_partial_fills: bool = True,
        slippage_model: str = SLIPPAGE_MODEL,
        fund_injections: Optional[Dict[date, float]] = None,
    ):
        self.data = data
        self.start = start_date
        self.end = end_date
        self.initial_capital = initial_capital
        self.max_selected = max_selected
        self.use_partial_fills = use_partial_fills
        self.slippage_model = slippage_model
        self.fund_injections: Dict[date, float] = fund_injections or {}

    def run(self) -> BacktestResult:
        result = BacktestResult()
        cash = self.initial_capital
        open_positions: List[Position] = []
        peak_value = self.initial_capital

        # ── Hybrid mode state ──────────────────────────────────────────
        hybrid_mode           = "momentum"   # "momentum" | "defensive"
        prev_regime           = "UNKNOWN"
        regime_streak         = 0
        last_rebal_date: Optional[date] = None
        defensive_start_date: Optional[date] = None

        all_dates = self._get_trading_dates()
        if not all_dates:
            logger.warning(f"No trading dates found between {self.start} and {self.end}")
            return result

        # Forward-roll injection dates to next available trading day
        trading_date_set = set(all_dates)
        effective_injections: Dict[date, float] = {}
        for inj_date, amount in self.fund_injections.items():
            rolled = next((d for d in sorted(all_dates) if d >= inj_date), None)
            if rolled is None:
                logger.warning(f"[FUND INJECTION] {inj_date} beyond backtest range — skipped")
                continue
            if rolled != inj_date:
                logger.info(f"[FUND INJECTION] {inj_date} is non-trading day → rolled to {rolled}")
            effective_injections[rolled] = effective_injections.get(rolled, 0.0) + amount

        # ── 1. Precompute ALL indicators for ALL execution times ──
        logger.info(f"Precomputing indicators for {len(self.data)-1} symbols @ {EXECUTION_TIMES}...")
        all_indicators, idx_confirmed_by_time, nifty_pullback_ok_by_time = self._precompute_all(all_dates)

        logger.info(f"Running backtest loop [slippage={self.slippage_model}]…")

        for i, today_date in enumerate(all_dates):
            new_trades_today = 0  # reset daily trade counter

            # ── Fund injection ────────────────────────────────────────────
            if today_date in effective_injections:
                amount = effective_injections[today_date]
                cash += amount
                result.total_injected += amount
                result.fund_injections_log.append({
                    "date": str(today_date), "amount": amount, "cash_after": round(cash, 2)
                })
                result.transaction_log.append({
                    "Time": str(today_date), "Action": "FUND_INJECTION", "Stock": "-",
                    "Price": 0, "Qty": 0, "Balance": round(cash, 2),
                    "Holdings": ", ".join([p.symbol for p in open_positions])
                })
                logger.info(
                    f"[FUND INJECTION] +₹{amount:,.0f} on {today_date} | Cash now: ₹{cash:,.0f}"
                )

            for exec_time_str in EXECUTION_TIMES:
                exec_time = datetime.strptime(exec_time_str, "%H:%M").time()
                today_ts = pd.Timestamp(datetime.combine(today_date, exec_time))
                
                # Get precomputed indicators for this specific time
                indicators = all_indicators.get(today_ts, {})
                if not indicators: continue

                # ── 2. Regime & Context ───────────────────────────────────
                first_sym = next(iter(indicators))
                regime = indicators[first_sym].get("regime", "UNKNOWN")
                market_bullish = is_buy_allowed(regime)

                prices = {sym: ind["close"] for sym, ind in indicators.items()}

                # ── 2b. Hybrid Mode Switching ─────────────────────────────
                if regime == prev_regime:
                    regime_streak += 1
                else:
                    regime_streak  = 1
                    prev_regime    = regime

                # Anti-whipsaw: after a regime flip to BULL, wait ENTRY_CONFIRM_DAYS
                # before allowing new momentum entries (exits unaffected)
                entry_confirmed = regime != "BULL" or regime_streak >= ENTRY_CONFIRM_DAYS

                def _close_all(positions, reason="regime_switch"):
                    nonlocal cash
                    closed = []
                    for pos in list(positions):
                        ep = round_to_tick(
                            prices.get(pos.symbol, pos.entry_price) * (1 - SLIPPAGE_FIXED_PCT)
                        )
                        pnl = net_pnl(pos.entry_price, ep, pos.shares)
                        result.trades.append(Trade(
                            symbol=pos.symbol, sector=pos.sector,
                            entry_date=pos.entry_date, exit_date=today_ts,
                            entry_price=pos.entry_price, exit_price=ep,
                            shares=pos.shares, gross_pnl=pnl["gross_pnl"],
                            charges=pnl["total_charges"], net_pnl=pnl["net_pnl"],
                            exit_reason=reason,
                            hold_days=(today_ts - pos.entry_date).days,
                        ))
                        cash += ep * pos.shares - pnl["sell_charges"]["total"]
                        closed.append(pos.symbol)
                    return closed

                # BULL→BEAR: switch to defensive after N consecutive BEAR days
                defensive_days_held = (today_date - defensive_start_date).days if defensive_start_date else 999
                if (regime == "BEAR" and hybrid_mode == "momentum"
                        and regime_streak >= REGIME_SWITCH_DAYS):
                    portfolio_val_now = cash + portfolio_invested_value(open_positions, prices)
                    logger.info(
                        f"[Hybrid] Regime BEAR x{regime_streak}d — switching to DEFENSIVE "
                        f"(₹{portfolio_val_now:,.0f}) on {today_date}"
                    )
                    # Close momentum positions only — GOLDBEES may already be held from
                    # MARKET_CRASH_PROTECTION on bear day 1; keep it and top-up to 50% target
                    # rather than sell/rebuy (avoids double transaction costs)
                    gold_pos_existing = next((p for p in open_positions if p.symbol == GOLD_ETF), None)
                    momentum_positions = [p for p in open_positions if not is_defensive_symbol(p.symbol)]
                    _close_all(momentum_positions, reason="bear_regime_exit")
                    open_positions = [p for p in open_positions if is_defensive_symbol(p.symbol)]
                    # Target 50% of total portfolio in GOLDBEES; top-up if already held
                    portfolio_val_now = cash + portfolio_invested_value(open_positions, prices)
                    entries = get_defensive_entries(portfolio_val_now, prices, SLIPPAGE_FIXED_PCT)
                    for sym, shares_target, ep, weight in entries:
                        existing_pos = next((p for p in open_positions if p.symbol == sym), None)
                        if existing_pos:
                            # Already held — compute how many additional shares to reach target
                            shares_held = existing_pos.shares
                            add_shares = max(0, shares_target - shares_held)
                            if add_shares > 0:
                                cost = buy_charges(add_shares * ep).total
                                if cash >= add_shares * ep + cost:
                                    old_val = existing_pos.shares * existing_pos.entry_price
                                    new_val = add_shares * ep
                                    existing_pos.entry_price = round_to_tick(
                                        (old_val + new_val) / (existing_pos.shares + add_shares)
                                    )
                                    existing_pos.shares += add_shares
                                    cash -= add_shares * ep + cost
                                    logger.info(f"  [DEF-ADD]  {sym:<16} +{add_shares} @ ₹{ep:.2f} (no churn)")
                            else:
                                logger.info(f"  [DEF-OK]   {sym:<16} already at target ({shares_held} shares)")
                        else:
                            cost = buy_charges(shares_target * ep).total
                            if cash >= shares_target * ep + cost:
                                open_positions.append(Position(
                                    symbol=sym, sector=get_sector(sym),
                                    entry_date=today_ts, entry_price=ep, shares=shares_target,
                                    stop_loss=0.0, take_profit=0.0,
                                    trailing_stop=0.0, peak_price=ep,
                                ))
                                cash -= shares_target * ep + cost
                                logger.info(f"  [DEF-BUY]  {sym:<16} {shares_target} @ ₹{ep:.2f}")
                    hybrid_mode          = "defensive"
                    last_rebal_date      = today_date
                    defensive_start_date = today_date

                # BEAR→BULL: switch back after BULL_RECOVERY_DAYS + MIN_DEFENSIVE_HOLD_DAYS enforced
                elif (regime == "BULL" and hybrid_mode == "defensive"
                        and regime_streak >= BULL_RECOVERY_DAYS
                        and defensive_days_held >= MIN_DEFENSIVE_HOLD_DAYS):
                    logger.info(
                        f"[Hybrid] Regime BULL x{regime_streak}d — switching to MOMENTUM on {today_date}"
                    )
                    # Close all defensive positions EXCEPT GOLDBEES —
                    # carry gold into momentum and let normal exit logic close it
                    gold_pos  = next((p for p in open_positions if p.symbol == GOLD_ETF), None)
                    non_gold  = [p for p in open_positions if p.symbol != GOLD_ETF]
                    _close_all(non_gold, reason="bull_regime_recovery")

                    if gold_pos:
                        gold_price = prices.get(GOLD_ETF, gold_pos.entry_price)
                        gold_atr   = indicators.get(GOLD_ETF, {}).get("atr", 0)
                        stops      = initial_stops(gold_price, atr=gold_atr)
                        gold_pos.stop_loss     = stops["stop_loss"]
                        gold_pos.trailing_stop = stops["trailing_stop"]
                        gold_pos.peak_price    = stops["peak_price"]
                        open_positions = [gold_pos]
                        logger.info(
                            f"  [GOLD-CARRY] {GOLD_ETF} held into momentum "
                            f"@ ₹{gold_price:.2f} | Trail stop: ₹{stops['trailing_stop']:.2f}"
                        )
                    else:
                        open_positions = []

                    hybrid_mode          = "momentum"
                    last_rebal_date      = None
                    defensive_start_date = None
                    peak_value           = cash + portfolio_invested_value(open_positions, prices)

                # Quarterly rebalance while in defensive mode
                elif (hybrid_mode == "defensive" and last_rebal_date is not None
                        and (today_date - last_rebal_date).days >= REBAL_DAYS):
                    portfolio_val_now = cash + portfolio_invested_value(open_positions, prices)
                    logger.info(f"[Hybrid] Defensive quarterly rebalance on {today_date}")
                    sells, buys = compute_rebalance(open_positions, portfolio_val_now, prices, SLIPPAGE_FIXED_PCT)
                    for sym, shares, ep in sells:
                        pos = next((p for p in open_positions if p.symbol == sym), None)
                        if not pos: continue
                        pnl = net_pnl(pos.entry_price, ep, shares)
                        result.trades.append(Trade(
                            symbol=sym, sector=pos.sector,
                            entry_date=pos.entry_date, exit_date=today_ts,
                            entry_price=pos.entry_price, exit_price=ep,
                            shares=shares, gross_pnl=pnl["gross_pnl"],
                            charges=pnl["total_charges"], net_pnl=pnl["net_pnl"],
                            exit_reason="rebalance_trim",
                            hold_days=(today_ts - pos.entry_date).days,
                        ))
                        cash += ep * shares - pnl["sell_charges"]["total"]
                        pos.shares -= shares
                        if pos.shares <= 0:
                            open_positions = [p for p in open_positions if p.symbol != sym]
                    for sym, shares, ep in buys:
                        cost = buy_charges(shares * ep).total
                        if cash >= shares * ep + cost:
                            existing = next((p for p in open_positions if p.symbol == sym), None)
                            if existing:
                                old_val = existing.shares * existing.entry_price
                                existing.entry_price = round_to_tick(
                                    (old_val + shares * ep) / (existing.shares + shares)
                                )
                                existing.shares += shares
                            else:
                                open_positions.append(Position(
                                    symbol=sym, sector=get_sector(sym),
                                    entry_date=today_ts, entry_price=ep, shares=shares,
                                    stop_loss=0.0, take_profit=0.0,
                                    trailing_stop=0.0, peak_price=ep,
                                ))
                            cash -= shares * ep + cost
                    last_rebal_date = today_date

                # ── Bear swing mode: active trading during BEAR regime ────────
                if hybrid_mode == "defensive":
                    # Update trailing stops for ALL positions (gold + bear swing)
                    for pos in open_positions:
                        cp  = prices.get(pos.symbol)
                        atr = indicators.get(pos.symbol, {}).get("atr", 0)
                        if cp: update_trailing_stop(pos, cp, atr=atr)

                    portfolio_val = cash + portfolio_invested_value(open_positions, prices)
                    peak_value    = max(peak_value, portfolio_val)

                    # ── Bear swing exits (custom — bypasses generate_signals BEAR gate)
                    # GOLDBEES never force-sold here; exits only on regime transition
                    for pos in [p for p in open_positions if p.symbol != GOLD_ETF]:
                        ind = indicators.get(pos.symbol, {})
                        cp  = ind.get("close", pos.entry_price)
                        rs  = ind.get("rs_rank", 0)
                        exit_triggered, exit_reason = check_exit_conditions(
                            pos, cp, rs, indicators=ind
                        )
                        if not exit_triggered:
                            continue
                        ep = round_to_tick(cp * (1 - SLIPPAGE_FIXED_PCT))
                        pnl_data = net_pnl(pos.entry_price, ep, pos.shares)
                        result.trades.append(Trade(
                            symbol=pos.symbol, sector=pos.sector,
                            entry_date=pos.entry_date, exit_date=today_ts,
                            entry_price=pos.entry_price, exit_price=ep,
                            shares=pos.shares, gross_pnl=pnl_data["gross_pnl"],
                            charges=pnl_data["total_charges"], net_pnl=pnl_data["net_pnl"],
                            exit_reason=f"BEAR_SWING|{exit_reason}",
                            hold_days=(today_ts - pos.entry_date).days,
                        ))
                        cash += ep * pos.shares - pnl_data["sell_charges"]["total"]
                        open_positions = [p for p in open_positions if p.symbol != pos.symbol]
                        result.transaction_log.append({
                            "Time": str(today_ts), "Action": "SELL", "Stock": pos.symbol,
                            "Price": ep, "Qty": pos.shares, "Balance": round(cash, 2),
                            "Holdings": ", ".join([p.symbol for p in open_positions]),
                        })
                        logger.info(
                            f"  [BEAR-SELL] {pos.symbol:<12} @ ₹{ep:>9,.2f} | {exit_reason}"
                        )

                    # ── Bear swing entries: RS > threshold AND stock above own EMA50
                    # (individually trending up despite the broader bear market)
                    bear_swing_now = [p for p in open_positions if p.symbol != GOLD_ETF]
                    bear_slots_free = BEAR_SWING_SLOTS - len(bear_swing_now)
                    held_symbols    = {pos.symbol for pos in open_positions}
                    portfolio_val   = cash + portfolio_invested_value(open_positions, prices)

                    if bear_slots_free > 0 and cash > portfolio_val * 0.005:
                        candidates = []
                        for symbol, ind in indicators.items():
                            if symbol in held_symbols or symbol == GOLD_ETF:
                                continue
                            rs_rank  = ind.get("rs_rank", 0)
                            close    = ind.get("close", 0)
                            ema50    = ind.get("ema_50", 0)
                            turnover = ind.get("turnover", 0)
                            # Filter: outperforming bear AND stock individually above EMA50
                            if (rs_rank >= BEAR_SWING_RS_THRESHOLD
                                    and ema50 > 0 and close > ema50
                                    and turnover >= 20_000_000):
                                candidates.append((symbol, rs_rank, ind))
                        candidates.sort(key=lambda x: x[1], reverse=True)

                        slot_cash = cash / BEAR_SWING_SLOTS
                        for symbol, rs_rank, ind in candidates[:bear_slots_free]:
                            ep = round_to_tick(ind["close"] * (1 + SLIPPAGE_FIXED_PCT))
                            slot_cash_capped = min(slot_cash, portfolio_val * MAX_STOCK_ALLOCATION_PCT)
                            alloc_ok, alloc_reason = can_open_position(
                                symbol, slot_cash_capped, portfolio_val, open_positions, prices
                            )
                            if not alloc_ok:
                                logger.info(f"  [BEAR-SKIP] {symbol:<12} — {alloc_reason}")
                                continue
                            target_value = slot_cash_capped - buy_charges(slot_cash_capped).total
                            shares = calculate_shares_for_value(target_value, ep)
                            if shares > 0:
                                stops = initial_stops(ep, atr=float(ind.get("atr", 0) or 0))
                                open_positions.append(Position(
                                    symbol=symbol, sector=get_sector(symbol),
                                    entry_date=today_ts, entry_price=ep, shares=shares,
                                    stop_loss=stops["stop_loss"], take_profit=stops["take_profit"],
                                    trailing_stop=stops["trailing_stop"], peak_price=stops["peak_price"],
                                ))
                                cash -= (shares * ep) + buy_charges(shares * ep).total
                                logger.info(
                                    f"  [BEAR-BUY] {symbol:<12} @ ₹{ep:>9,.2f} "
                                    f"| RS: {rs_rank:.1f} | EMA50 ✓ | {today_ts}"
                                )
                                result.transaction_log.append({
                                    "Time": str(today_ts), "Action": "BEAR_SWING_BUY",
                                    "Stock": symbol, "Price": ep, "Qty": shares,
                                    "Balance": round(cash, 2),
                                    "Holdings": ", ".join([p.symbol for p in open_positions]),
                                })

                    portfolio_val = cash + portfolio_invested_value(open_positions, prices)
                    result.equity_curve[today_ts] = round(portfolio_val, 2)
                    result.cash_curve[today_ts]   = round(cash, 2)
                    result.regime_log[today_date] = f"{regime}|defensive"
                    result.daily_scan_log.append({
                        "date": str(today_ts), "regime": f"{regime}|defensive",
                        "portfolio_value": round(portfolio_val, 2),
                        "cash": round(cash, 2), "open_positions": len(open_positions),
                    })
                    continue

                # ── 3. Update Trailing Stops ──
                for pos in open_positions:
                    cp = prices.get(pos.symbol)
                    atr = indicators.get(pos.symbol, {}).get("atr", 0)
                    if cp: update_trailing_stop(pos, cp, atr=atr)

                held = {pos.symbol for pos in open_positions}
                portfolio_val = cash + portfolio_invested_value(open_positions, prices)
                peak_value = max(peak_value, portfolio_val)

                # ── 3b. Portfolio-level stop: close all if DD > PORTFOLIO_STOP_PCT ──
                if PORTFOLIO_STOP_PCT > 0 and open_positions:
                    port_dd = (peak_value - portfolio_val) / peak_value if peak_value > 0 else 0.0
                    if port_dd >= PORTFOLIO_STOP_PCT:
                        logger.info(
                            f"[PORT-STOP] DD={port_dd:.1%} >= {PORTFOLIO_STOP_PCT:.1%} "
                            f"— closing all {len(open_positions)} positions on {today_date}"
                        )
                        for pos in list(open_positions):
                            ep = round_to_tick(
                                prices.get(pos.symbol, pos.entry_price) * (1 - SLIPPAGE_FIXED_PCT)
                            )
                            pnl = net_pnl(pos.entry_price, ep, pos.shares)
                            result.trades.append(Trade(
                                symbol=pos.symbol, sector=pos.sector,
                                entry_date=pos.entry_date, exit_date=today_ts,
                                entry_price=pos.entry_price, exit_price=ep,
                                shares=pos.shares, gross_pnl=pnl["gross_pnl"],
                                charges=pnl["total_charges"], net_pnl=pnl["net_pnl"],
                                exit_reason="PORTFOLIO_STOP",
                                hold_days=(today_ts - pos.entry_date).days,
                            ))
                            cash += ep * pos.shares - pnl["sell_charges"]["total"]
                        open_positions = []
                        held = set()
                idx_confirmed = idx_confirmed_by_time.get(exec_time, {}).get(today_date, True)
                nifty_pb_ok = nifty_pullback_ok_by_time.get(exec_time, {}).get(today_date, True)
                num_to_buy = 0

                # ── 4. Signal Generation ──────────────────────────────────
                signals, _ = generate_signals(
                    today_date, indicators, open_positions, held,
                    market_bullish=market_bullish, regime=regime,
                    portfolio_value=portfolio_val, cash=cash,
                    initial_capital=self.initial_capital,
                    index_confirming=idx_confirmed and entry_confirmed and nifty_pb_ok
                )

                # ── 6. Execute Sells ──────────────────────────────────────
                sell_signals = [s for s in signals if s.action == "SELL"]
                for sig in sell_signals:
                    pos = next((p for p in open_positions if p.symbol == sig.symbol), None)
                    if not pos: continue
                    
                    # Execute at the signal price (the minute price) minus slippage
                    exec_price = round_to_tick(sig.price * (1 - SLIPPAGE_FIXED_PCT))
                    exec_date = today_ts

                    # Profit-only exit for GOLDBEES: defer sell until price ≥ entry
                    # BUT cut immediately if loss exceeds GOLDBEES_MAX_LOSS_PCT
                    if GOLDBEES_PROFIT_EXIT_ONLY and sig.symbol == GOLD_ETF and exec_price < pos.entry_price:
                        loss_pct = (pos.entry_price - exec_price) / pos.entry_price
                        if loss_pct < GOLDBEES_MAX_LOSS_PCT:
                            logger.info(
                                f"  [GOLD-HOLD] {GOLD_ETF} loss {loss_pct:.1%} < {GOLDBEES_MAX_LOSS_PCT:.0%} cap "
                                f"(₹{exec_price:.2f} < ₹{pos.entry_price:.2f}) — holding"
                            )
                            continue
                        logger.info(
                            f"  [GOLD-CUT]  {GOLD_ETF} loss {loss_pct:.1%} >= {GOLDBEES_MAX_LOSS_PCT:.0%} cap "
                            f"(₹{exec_price:.2f} < ₹{pos.entry_price:.2f}) — cutting loss"
                        )

                    pnl_data = net_pnl(pos.entry_price, exec_price, pos.shares)
                    
                    result.trades.append(Trade(
                        symbol=pos.symbol, sector=pos.sector,
                        entry_date=pos.entry_date, exit_date=exec_date,
                        entry_price=pos.entry_price, exit_price=exec_price,
                        shares=pos.shares, gross_pnl=pnl_data["gross_pnl"],
                        charges=pnl_data["total_charges"], net_pnl=pnl_data["net_pnl"],
                        exit_reason=f"{sig.reason}|intraday_slip",
                        hold_days=(exec_date - pos.entry_date).days,
                    ))
                    
                    pnl_pct = (exec_price / pos.entry_price - 1) * 100
                    logger.info(f"  [SELL] {pos.symbol:<12} @ ₹{exec_price:>9,.2f} | P&L: {pnl_pct:>+5.1f}% | {sig.reason} on {exec_date}")
                    
                    cash += exec_price * pos.shares - pnl_data["sell_charges"]["total"]
                    open_positions = [p for p in open_positions if p.symbol != pos.symbol]
                    
                    result.transaction_log.append({
                        "Time": str(exec_date), "Action": "SELL", "Stock": sig.symbol,
                        "Price": exec_price, "Qty": pos.shares, "Balance": round(cash, 2),
                        "Holdings": ", ".join([p.symbol for p in open_positions])
                    })

                held = {pos.symbol for pos in open_positions}
                portfolio_val = cash + portfolio_invested_value(open_positions, prices)

                # ── 7. Execute Buys ──────────────────────────────────────
                buy_signals = sorted([s for s in signals if s.action == "BUY"], key=lambda x: x.score, reverse=True)
                available_slots = MAX_OPEN_POSITIONS - len(open_positions)

                current_dd = (peak_value - portfolio_val) / peak_value if peak_value > 0 else 0.0

                trades_ok, trades_reason = can_open_new_trades(
                    new_trades_today, open_positions, portfolio_val, peak_value
                )

                # ── Rank replacement: evict weakest if a stronger candidate is waiting ──
                _REPLACE_MIN_NEW_RS  = 85.0
                _REPLACE_MAX_HELD_RS = 55.0
                _REPLACE_MIN_GAP     = 25.0
                if buy_signals and available_slots == 0 and trades_ok and cash > (portfolio_val * 0.005):
                    best_cand = buy_signals[0]
                    non_def = [p for p in open_positions if not is_defensive_symbol(p.symbol)]
                    if non_def:
                        weakest = min(non_def, key=lambda p: indicators.get(p.symbol, {}).get("rs_rank", 101))
                        weakest_rs = float(indicators.get(weakest.symbol, {}).get("rs_rank", 101))
                        if (best_cand.score >= _REPLACE_MIN_NEW_RS
                                and weakest_rs <= _REPLACE_MAX_HELD_RS
                                and (best_cand.score - weakest_rs) >= _REPLACE_MIN_GAP):
                            ep = round_to_tick(prices.get(weakest.symbol, weakest.entry_price) * (1 - SLIPPAGE_FIXED_PCT))
                            pnl_r = net_pnl(weakest.entry_price, ep, weakest.shares)
                            result.trades.append(Trade(
                                symbol=weakest.symbol, sector=weakest.sector,
                                entry_date=weakest.entry_date, exit_date=today_ts,
                                entry_price=weakest.entry_price, exit_price=ep,
                                shares=weakest.shares, gross_pnl=pnl_r["gross_pnl"],
                                charges=pnl_r["total_charges"], net_pnl=pnl_r["net_pnl"],
                                exit_reason="RANK_REPLACED",
                                hold_days=(today_ts - weakest.entry_date).days,
                            ))
                            cash += ep * weakest.shares - pnl_r["sell_charges"]["total"]
                            open_positions = [p for p in open_positions if p.symbol != weakest.symbol]
                            available_slots = MAX_OPEN_POSITIONS - len(open_positions)
                            logger.info(
                                f"  [REPLACE] {weakest.symbol} RS={weakest_rs:.0f} → "
                                f"{best_cand.symbol} RS={best_cand.score:.0f}"
                            )

                if available_slots > 0 and cash > (portfolio_val * 0.005) and current_dd < DRAWDOWN_KILL_SWITCH_PCT and trades_ok:
                    num_to_buy = min(len(buy_signals), available_slots)

                    if num_to_buy > 0:
                        base_slot_cash = cash / available_slots
                        # Graduated size reduction under drawdown
                        if current_dd >= DRAWDOWN_REDUCE_SIZE_PCT * DRAWDOWN_REDUCE_TIER2_MULT:
                            base_slot_cash *= 0.25
                        elif current_dd >= DRAWDOWN_REDUCE_SIZE_PCT:
                            base_slot_cash *= 0.50
                        for j in range(num_to_buy):
                            if new_trades_today >= MAX_NEW_TRADES_PER_DAY:
                                logger.info(f"  [SKIP] Daily trade limit reached ({MAX_NEW_TRADES_PER_DAY})")
                                break
                            sig = buy_signals[j]
                            exec_price = round_to_tick(sig.price * (1 + SLIPPAGE_FIXED_PCT))
                            exec_date = today_ts

                            atr = float(sig.indicators.get("atr", 0) or 0)
                            stops = initial_stops(exec_price, atr=atr)

                            if sig.symbol == SAFE_HAVEN_SYMBOL:
                                slot_cash = min(cash, portfolio_val * 0.50)
                            else:
                                size_factor = score_to_size_factor(sig.score)
                                slot_cash = min(base_slot_cash * size_factor, portfolio_val * MAX_STOCK_ALLOCATION_PCT)
                                alloc_ok, alloc_reason = can_open_position(
                                    sig.symbol, slot_cash, portfolio_val, open_positions, prices
                                )
                                if not alloc_ok:
                                    logger.info(f"  [SKIP] {sig.symbol:<12} — {alloc_reason}")
                                    continue

                            target_value = slot_cash - buy_charges(slot_cash).total
                            shares = calculate_shares_for_value(target_value, exec_price)

                            if shares > 0:
                                logger.info(f"  [BUY]  {sig.symbol:<12} @ ₹{exec_price:>9,.2f} | RS Rank: {sig.score:.1f} | Exec Date: {exec_date}")
                                open_positions.append(Position(
                                    symbol=sig.symbol, sector=get_sector(sig.symbol),
                                    entry_date=exec_date, entry_price=exec_price, shares=shares,
                                    stop_loss=stops["stop_loss"], take_profit=stops["take_profit"],
                                    trailing_stop=stops["trailing_stop"], peak_price=stops["peak_price"],
                                ))
                                cash -= (shares * exec_price) + buy_charges(shares * exec_price).total
                                new_trades_today += 1

                                result.transaction_log.append({
                                    "Time": str(exec_date), "Action": "BUY", "Stock": sig.symbol,
                                    "Price": exec_price, "Qty": shares, "Balance": round(cash, 2),
                                    "Holdings": ", ".join([p.symbol for p in open_positions])
                                })

                    elif len(open_positions) > 0:
                        best_pos = None
                        highest_rs = -1.0
                        for pos in open_positions:
                            rs = indicators.get(pos.symbol, {}).get("rs_rank", 0)
                            if rs > highest_rs:
                                highest_rs = rs
                                best_pos = pos
                        
                        if best_pos and best_pos.symbol in prices:
                            price_at_add = round_to_tick(prices[best_pos.symbol])
                            current_pos_value = best_pos.shares * price_at_add
                            max_pos_value = portfolio_val * 0.30
                            allowed_add_cash = min(cash, max(0, max_pos_value - current_pos_value))
                            target_value = allowed_add_cash - buy_charges(allowed_add_cash).total
                            add_shares = calculate_shares_for_value(target_value, price_at_add)
                            if add_shares > 0:
                                logger.info(f"  [ADD]  {best_pos.symbol:<12} | Re-investing idle cash into leader (RS: {highest_rs:.1f})")
                                old_val = best_pos.shares * best_pos.entry_price
                                new_val = add_shares * price_at_add
                                best_pos.entry_price = round_to_tick((old_val + new_val) / (best_pos.shares + add_shares))
                                best_pos.shares += add_shares
                                cash -= (add_shares * price_at_add) + buy_charges(add_shares * price_at_add).total
                                
                                result.transaction_log.append({
                                    "Time": str(today_ts), "Action": "ADD_TO_WINNER", "Stock": best_pos.symbol,
                                    "Price": price_at_add, "Qty": add_shares, "Balance": round(cash, 2),
                                    "Holdings": ", ".join([p.symbol for p in open_positions])
                                })

                # ── 7.5. Decision Logging ─────────────────────────────
                for symbol, ind in indicators.items():
                    if symbol in held: continue
                    qualified, gate_reason = check_entry(ind, symbol=symbol, regime=regime)
                    
                    rs_rank = ind.get('rs_rank', 0)
                    rs_pass = "PASS" if rs_rank >= RS_THRESHOLD else "FAIL"
                    signal = "YES" if qualified else "NO"
                    
                    selected_val = "NO"
                    if qualified and market_bullish:
                        # Find if it was in the symbols actually bought today
                        was_bought = any(p.symbol == symbol and p.entry_date == today_ts for p in open_positions)
                        if was_bought:
                            selected_val = "YES"
                    
                    result.decision_log.append({
                        "date": today_ts, "symbol": symbol, 
                        "rs_pass": rs_pass, "signal": signal, 
                        "reason": gate_reason,
                        "rank_score": round(rs_rank, 1),
                        "rs_rank": round(rs_rank, 1),
                        "selected": selected_val
                    })

                # ── 8. Log & State Management ─────────────────────────────
                portfolio_val = cash + portfolio_invested_value(open_positions, prices)
                result.equity_curve[today_ts] = round(portfolio_val, 2)
                result.cash_curve[today_ts] = round(cash, 2)
                
                result.daily_scan_log.append({
                    "date": str(today_ts), "regime": regime, 
                    "portfolio_value": round(portfolio_val, 2),
                    "cash": round(cash, 2), "open_positions": len(open_positions),
                })
                
                if signals:
                    logger.info(f"[Time] {today_ts} | Regime: {regime:<10} | Total: ₹{portfolio_val:,.0f} | Pos: {len(open_positions)}")

        result.final_open_positions = open_positions
        logger.info(f"[Backtest] Done. {len(result.trades)} trades over {len(all_dates)} trading days")
        return result

    def _precompute_all(self, all_dates: List[date]):
        """
        Blazing fast vectorized pre-calculation of indicators for all execution times.
        Returns (all_indicators, idx_confirmed_by_time).
        """
        all_indicators = {}
        times = [datetime.strptime(t, "%H:%M").time() for t in EXECUTION_TIMES]

        # 1. Prepare Index data for each time
        index_full = self.data.get(MARKET_INDEX_SYMBOL, pd.DataFrame())
        index_by_time = {}
        regime_by_time = {}
        idx_confirmed_by_time = {}
        nifty_pullback_ok_by_time = {}
        for t in times:
            idx_time = index_full[index_full.index.time <= t].resample('D').agg({
                'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
            }).dropna()
            index_by_time[t] = idx_time
            # Precompute regime (100 EMA)
            ema100_idx = idx_time['close'].ewm(span=100, adjust=False).mean()
            regime_by_time[t] = {d.date(): ("BULL" if idx_time['close'].loc[d] > ema100_idx.loc[d] else "BEAR") for d in idx_time.index}
            # Precompute short-term index confirmation (20 EMA)
            ema20_idx = idx_time['close'].ewm(span=20, adjust=False).mean()
            idx_confirmed_by_time[t] = {d.date(): bool(idx_time['close'].loc[d] > ema20_idx.loc[d]) for d in idx_time.index}
            # Precompute Nifty pullback guard (10-day rolling high)
            if NIFTY_PULLBACK_GUARD_PCT > 0:
                rolling_high = idx_time['close'].rolling(window=10, min_periods=1).max()
                pullback = (rolling_high - idx_time['close']) / rolling_high
                nifty_pullback_ok_by_time[t] = {d.date(): bool(pullback.loc[d] <= NIFTY_PULLBACK_GUARD_PCT) for d in idx_time.index}
            else:
                nifty_pullback_ok_by_time[t] = {}

        # 2. Compute RS Ratios cross-sectionally for each time
        rs_ratios_by_time = {}
        symbols = [s for s in self.data.keys() if s != MARKET_INDEX_SYMBOL]

        for t in times:
            idx_close = index_by_time[t]['close']
            # Build all columns in a dict first, then construct DataFrame once (avoids fragmentation)
            ratios_dict = {}
            for symbol in symbols:
                df_min = self.data[symbol]
                s_daily = df_min[df_min.index.time <= t].resample('D').agg({'close': 'last'}).dropna()
                s_close = s_daily['close'].reindex(idx_close.index)
                rs_line = s_close / idx_close
                ratios_dict[symbol] = (rs_line / rs_line.rolling(window=126, min_periods=20).mean()) * 100

            ratios_df = pd.DataFrame(ratios_dict, index=idx_close.index)
            rs_ratios_by_time[t] = ratios_df.rank(axis=1, pct=True) * 100

        # 3. Compute Technical Indicators for each symbol
        for symbol in symbols:
            df_min = self.data[symbol]
            for t in times:
                # Resample once per time per stock
                df = df_min[df_min.index.time <= t].resample('D').agg({
                    'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
                }).dropna()
                if len(df) < 20: continue
                
                # Vectorized indicators
                close = df['close']
                ema20 = close.ewm(span=20, adjust=False).mean()
                ema50 = close.ewm(span=50, adjust=False).mean()
                ema100 = close.ewm(span=100, adjust=False).mean()
                ema150 = close.ewm(span=150, adjust=False).mean()
                
                # ATR
                tr = pd.concat([df['high'] - df['low'], (df['high'] - close.shift()).abs(), (df['low'] - close.shift()).abs()], axis=1).max(axis=1)
                atr = tr.rolling(window=14).mean()

                # ADX (Wilder's — trend strength; <20 = sideways/chop)
                h_diff   = df['high'] - df['high'].shift(1)
                l_diff   = df['low'].shift(1) - df['low']
                plus_dm  = pd.Series(np.where((h_diff > l_diff) & (h_diff > 0), h_diff, 0.0), index=df.index)
                minus_dm = pd.Series(np.where((l_diff > h_diff) & (l_diff > 0), l_diff, 0.0), index=df.index)
                atr14    = tr.ewm(alpha=1/14, adjust=False).mean()
                plus_di  = 100 * plus_dm.ewm(alpha=1/14, adjust=False).mean() / atr14.replace(0, np.nan)
                minus_di = 100 * minus_dm.ewm(alpha=1/14, adjust=False).mean() / atr14.replace(0, np.nan)
                dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
                adx_s    = dx.ewm(alpha=1/14, adjust=False).mean()
                
                # RSI
                delta = close.diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rsi = 100 - (100 / (1 + gain / loss.replace(0, float("nan"))))
                
                # Turnover and volume ratio
                vol_avg = df['volume'].rolling(window=20, min_periods=5).mean()
                vol_ratio = (df['volume'] / vol_avg.replace(0, np.nan)).fillna(1.0)
                turnover = (close * df['volume']).rolling(window=20).mean()

                # MACD histogram
                macd_line = close.ewm(span=MACD_FAST, adjust=False).mean() - close.ewm(span=MACD_SLOW, adjust=False).mean()
                macd_sig = macd_line.ewm(span=MACD_SIGNAL, adjust=False).mean()
                macd_hist = macd_line - macd_sig

                # 10-day performance and 20-day high (from previous day)
                perf_10d = (close / close.shift(10) - 1) * 100
                high_20d = df['high'].shift(1).rolling(window=20, min_periods=1).max()

                # Align rs_ranks to stock dates — index dates may differ (holiday mismatch)
                rs_ranks = rs_ratios_by_time[t][symbol].reindex(df.index).fillna(0.0)

                for dt in df.index:
                    ts = pd.Timestamp(datetime.combine(dt.date(), t))
                    if ts not in all_indicators: all_indicators[ts] = {}

                    last_close = close.loc[dt]
                    last_atr = atr.loc[dt]

                    h = float(macd_hist.loc[dt]) if not pd.isna(macd_hist.loc[dt]) else 0.0
                    h_prev_idx = macd_hist.index.get_loc(dt)
                    h_prev = float(macd_hist.iloc[h_prev_idx - 1]) if h_prev_idx > 0 and not pd.isna(macd_hist.iloc[h_prev_idx - 1]) else 0.0
                    all_indicators[ts][symbol] = {
                        "symbol": symbol, "close": last_close,
                        "ema_20": ema20.loc[dt], "ema_50": ema50.loc[dt],
                        "ema_100": ema100.loc[dt], "ema_150": ema150.loc[dt],
                        "atr": last_atr,
                        "atr_pct": (last_atr / last_close * 100) if last_close > 0 else 0,
                        "rsi": rsi.loc[dt], "turnover": turnover.loc[dt],
                        "vol_ratio": round(float(vol_ratio.loc[dt]), 2),
                        "macd_hist": round(h, 4), "macd_hist_prev": round(h_prev, 4),
                        "macd_bullish": h > 0,
                        "perf_10d": perf_10d.loc[dt], "high_20d": high_20d.loc[dt],
                        "rs_rank": rs_ranks.loc[dt], "regime": regime_by_time[t].get(dt.date(), "UNKNOWN"),
                        "adx": round(float(adx_s.loc[dt]) if not pd.isna(adx_s.loc[dt]) else 0.0, 1)
                    }

        return all_indicators, idx_confirmed_by_time, nifty_pullback_ok_by_time

    def _get_trading_dates(self) -> List[date]:
        if MARKET_INDEX_SYMBOL not in self.data: return []
        df = self.data[MARKET_INDEX_SYMBOL]
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        mask = (df.index >= pd.Timestamp(self.start)) & (df.index <= pd.Timestamp(self.end))
        return sorted(list(set([d.date() for d in df[mask].index])))

    def _get_execution_price(self, symbol, today, all_dates, current_idx, side, ind):
        """Price is already set to the intraday bar close, just apply slippage."""
        if side == "buy":
            price = ind["close"] * (1 + SLIPPAGE_FIXED_PCT)
        else:
            price = ind["close"] * (1 - SLIPPAGE_FIXED_PCT)
        return round_to_tick(price), "intraday_slip"
