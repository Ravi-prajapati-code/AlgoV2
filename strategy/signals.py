import logging
import random
from datetime import date
from typing import List, Tuple

from db.models import Signal, Position
from strategy.entry import check_entry
from strategy.exit import check_exit_conditions, initial_stops
from data.universe import get_sector
from config.settings import IGNORE_SYMBOLS, BLOCKED_SECTORS, BLOCKED_SYMBOLS, SAFE_HAVEN_SYMBOL, SAFE_HAVEN_ENABLED, GOLDBEES_PROFIT_EXIT_ONLY, GOLDBEES_MAX_LOSS_PCT, ENTRY_MODE, ENTRY_MODE_SEED, SECTOR_DURABILITY_WEIGHT, EXIT_TREND_EMA, EXIT_TREND_CONFIRM_DAYS, CRASH_PROTECTION_STOCK_OVERRIDE, CRASH_PROTECTION_CONFIRM_DAYS
from strategy.defensive_portfolio import MIN_GOLDBEES_HOLD_DAYS

logger = logging.getLogger(__name__)

MIN_DAILY_TURNOVER = 20_000_000

def generate_signals(
    today: date,
    indicators: dict,
    open_positions: List[Position],
    held_symbols: set,
    market_bullish: bool = True,
    regime: str = "BULL",
    portfolio_value: float = 100000.0,
    cash: float = 100000.0,
    initial_capital: float = 100000.0,
    index_confirming: bool = True,
    sector_durability: dict = None
) -> Tuple[List[Signal], List[Position]]:
    
    signals = []
    updated_positions = []

    # 1. EVALUATE EXITS
    for pos in open_positions:
        # Non-strategy positions (manual/imported broker holdings) are never exit-evaluated
        # or force-sold by strategy signals — but still reported as open. docs/30. Previously
        # this checked IGNORE_SYMBOLS and dropped the position with no append (a latent bug
        # masked by the fact this branch was unreachable before origin tracking existed —
        # ignored symbols never made it into open_positions in the first place).
        if pos.origin != "strategy":
            updated_positions.append(pos)
            continue
        
        # Safe Haven Exit: Sell if market returns to BULL AND held long enough
        # MIN_GOLDBEES_HOLD_DAYS prevents 1-day whipsaw (crash protect → immediate exit)
        if SAFE_HAVEN_ENABLED and pos.symbol == SAFE_HAVEN_SYMBOL:
            entry = pos.entry_date.date() if hasattr(pos.entry_date, 'date') else pos.entry_date
            days_held = (today - entry).days if entry else 999
            current_price = indicators.get(pos.symbol, {}).get('close', pos.entry_price)
            # Hard cut if loss exceeds GOLDBEES_MAX_LOSS_PCT regardless of regime/PROFIT_EXIT_ONLY/
            # hold-days -- this is a stop-loss, not a bull-rotation signal, so BEAR must not block it.
            max_loss_hit = (GOLDBEES_MAX_LOSS_PCT > 0
                            and current_price < pos.entry_price * (1 - GOLDBEES_MAX_LOSS_PCT))
            if max_loss_hit:
                signals.append(Signal(
                    date=today, symbol=pos.symbol, action="SELL",
                    price=current_price,
                    reason="GOLDBEES_MAX_LOSS",
                    indicators=indicators.get(pos.symbol, {}),
                ))
            elif regime == "BULL" and days_held >= MIN_GOLDBEES_HOLD_DAYS:
                # Defer exit when price below entry (profit-exit-only mode)
                defer = GOLDBEES_PROFIT_EXIT_ONLY and current_price < pos.entry_price
                if defer:
                    updated_positions.append(pos)
                else:
                    signals.append(Signal(
                        date=today, symbol=pos.symbol, action="SELL",
                        price=current_price,
                        reason="EXIT_SAFE_HAVEN (Market is BULL)",
                        indicators=indicators.get(pos.symbol, {}),
                    ))
            else:
                updated_positions.append(pos)
            continue

        ind = indicators.get(pos.symbol)
        if not ind:
            updated_positions.append(pos)
            continue

        current_price = ind['close']
        rs_rank = ind.get('rs_rank', 0)
        ema_50 = ind.get('ema_50', 0)
        # TREND_BREAK basis is separately configurable (docs/31 sweep) — falls
        # back to ema_50 if the generic key is absent, same pattern as entry.py.
        ema_exit_trend = ind.get('ema_exit_trend', ema_50) or ema_50
        atr = ind.get('atr', 0)

        exit_triggered, exit_reason = check_exit_conditions(pos, current_price, rs_rank, indicators=ind)

        if not exit_triggered:
            force_bear_exit = (regime == "BEAR")
            if force_bear_exit and CRASH_PROTECTION_STOCK_OVERRIDE:
                if ind.get('bear_trend_confirm_days', 0) >= CRASH_PROTECTION_CONFIRM_DAYS:
                    force_bear_exit = False

            if force_bear_exit:
                exit_triggered = True
                exit_reason = "MARKET_CRASH_PROTECTION (Index < 100 EMA)"
            elif current_price < ema_exit_trend:
                pos.days_below_ema50 += 1
                if pos.days_below_ema50 >= EXIT_TREND_CONFIRM_DAYS:
                    exit_triggered = True
                    exit_reason = f"TREND_BREAK (Price < {EXIT_TREND_EMA} EMA x{EXIT_TREND_CONFIRM_DAYS} days)"
            else:
                pos.days_below_ema50 = 0


        if exit_triggered:
            signals.append(Signal(
                date=today, symbol=pos.symbol, action="SELL",
                score=rs_rank,
                price=current_price, reason=exit_reason,
                indicators=ind
            ))
        else:
            updated_positions.append(pos)

    # 2. EVALUATE ENTRIES
    if regime == "BEAR":
        # Safe Haven Entry: Buy if enabled and not already held
        if SAFE_HAVEN_ENABLED and SAFE_HAVEN_SYMBOL not in held_symbols:
            sh_ind = indicators.get(SAFE_HAVEN_SYMBOL)
            price = sh_ind.get('close', 0) if sh_ind else 0
            if price > 0:
                signals.append(Signal(
                    date=today, symbol=SAFE_HAVEN_SYMBOL, action="BUY",
                    score=100.0, price=price,
                    reason="ENTER_SAFE_HAVEN (Market is BEAR)",
                    indicators=sh_ind or {}
                ))
        if not CRASH_PROTECTION_STOCK_OVERRIDE:
            return signals, updated_positions
        # else: fall through to the normal candidate-evaluation loop below —
        # check_entry()'s existing EMA/SuperTrend/ADX trend gate is the bar a
        # stock must clear to enter even while the market regime is BEAR.

    # Entry Attribution Suite (docs/23_Assumption_Audit.md §XIV): SHUFFLE_RS tests
    # whether the RS *value* matters or just which stock it's attached to — permute
    # rs_rank across today's eligible symbols before gating/ranking, seeded per-day
    # for reproducibility. All other modes use the real rs_rank.
    rs_override = {}
    if ENTRY_MODE == "SHUFFLE_RS":
        eligible = [s for s in indicators
                    if s not in held_symbols and s not in IGNORE_SYMBOLS and s not in BLOCKED_SYMBOLS
                    and not (SAFE_HAVEN_ENABLED and s == SAFE_HAVEN_SYMBOL)
                    and not (BLOCKED_SECTORS and get_sector(s) in BLOCKED_SECTORS)]
        rs_values = [indicators[s].get('rs_rank', 0) for s in eligible]
        rng = random.Random(f"{ENTRY_MODE_SEED}:{today.toordinal()}")
        rng.shuffle(rs_values)
        rs_override = dict(zip(eligible, rs_values))

    # NOTE: backtest/engine.py independently re-sorts BUY signals by `.score`
    # descending before filling slots (and live portfolio/manager.py does the
    # same) — a local sort/shuffle of `candidates` here has no effect on which
    # stocks actually get bought. The mode's intended ranking must therefore be
    # baked into the `rs_rank` value itself, since that becomes `Signal.score`.
    rank_rng = random.Random(f"{ENTRY_MODE_SEED}:{today.toordinal()}")

    candidates = []
    for symbol, ind in indicators.items():
        if symbol in held_symbols or symbol in IGNORE_SYMBOLS or symbol in BLOCKED_SYMBOLS:
            continue
        if SAFE_HAVEN_ENABLED and symbol == SAFE_HAVEN_SYMBOL:
            continue
        if BLOCKED_SECTORS and get_sector(symbol) in BLOCKED_SECTORS:
            continue

        check_ind = ind
        if symbol in rs_override:
            check_ind = dict(ind)
            check_ind['rs_rank'] = rs_override[symbol]

        qualified, gate_reason = check_entry(
            check_ind, symbol=symbol, regime=regime,
            index_confirming=index_confirming
        )

        if qualified and regime == "BEAR" and CRASH_PROTECTION_STOCK_OVERRIDE:
            if check_ind.get('bear_trend_confirm_days', 0) < CRASH_PROTECTION_CONFIRM_DAYS:
                qualified = False

        if qualified:
            if ENTRY_MODE == "REVERSE_RS":
                score = -check_ind.get('rs_rank', 0)
            elif ENTRY_MODE in ("RANDOM_ALL", "RANDOM_ELIGIBLE"):
                score = rank_rng.random()
            elif ENTRY_MODE == "PURE_ADX_BREAKOUT":
                score = ind.get('adx', 0)
            else:  # FULL, PURE_RS, SHUFFLE_RS
                score = check_ind.get('rs_rank', 0)
            if SECTOR_DURABILITY_WEIGHT and sector_durability:
                score += SECTOR_DURABILITY_WEIGHT * sector_durability.get(get_sector(symbol), 0.0)
            candidates.append({
                "symbol": symbol,
                "rs_rank": score,
                "reason": gate_reason,
                "indicators": ind
            })

    candidates.sort(key=lambda x: x["rs_rank"], reverse=True)

    for cand in candidates:
        price = cand["indicators"]['close']
        atr = cand["indicators"].get("atr", 0)
        stops = initial_stops(price, atr=atr)

        signals.append(Signal(
            date=today, symbol=cand["symbol"], action="BUY",
            score=cand["rs_rank"],
            price=price,
            stop_loss=stops["stop_loss"],
            take_profit=stops["take_profit"],
            reason=cand["reason"],
            indicators=cand["indicators"]
        ))

    return signals, updated_positions
