"""
Daily decision + order-placement logic for the momentum x ATR live
strategy. Baseline config only (portfolio_size=3, exit_mode=immediate,
enable_swap=True, enable_cascade=True) -- the docs/58 gate-preferred
config, not TOP1/TOP2.

Timing note (why one cron run needs no cross-run "pending action" state):
scripts/momentum_atr_experiment/engine.py:run_sim() decides an action from
day i's CLOSE-based score and executes it at day i+1's OPEN. A daily
pre-market cron naturally reproduces that lag on its own: the most recent
COMPLETE daily bar available at a 9:17 IST run is necessarily yesterday's
close (today's own bar hasn't formed yet), and the broker MARKET order
placed now fills at ~today's open. So "decide from the latest available
close, execute now" IS the backtest's decide-on-close-N/execute-on-open-N+1
rule -- no pending_swap/pending_rank_exit needs to survive between cron
invocations, only the actual open positions (which live in the DB anyway).

Order placement funnel: broker.place_order_with_retry() (via buy()/sell())
-> poll to terminal status (portfolio/manager.py's 2s/30s pattern) ->
ledger is mutated ONLY on confirmed COMPLETE with filled_qty == requested
qty. Reject/timeout/partial -> alert, ledger untouched (partial-fill gap
matches an existing one in portfolio/manager.py, not a new regression --
see plan velvet-cooking-minsky, open risk #3).
"""
import time
from datetime import date as _date
from typing import Dict, List, Optional, Tuple

from broker.base import BaseBroker, OrderResult, OrderStatus
from charges.calculator import buy_charges, sell_charges, net_pnl
from config.settings import MOMENTUM_ATR_DD_KILL_PCT, MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT
from db import momentum_atr_repo as repo
from momentum_atr.models import Position, Trade
from momentum_atr.risk import check_kill_switch
from momentum_atr.scoring import compute_live_scores, rank_symbols
from notifications.telegram import send_error_alert, send_message

PORTFOLIO_SIZE = 3
SWAP_LOSS_PCT = -3.0
SWAP_GAIN_PCT = 3.0
AWAIT_TIMEOUT_SEC = 30
AWAIT_POLL_SEC = 2
MIN_REAL_CASH_RATIO = 0.5  # alert if broker cash < 50% of internal ledger cash


def _await_order_completion(broker: BaseBroker, order_id: str) -> Optional[OrderResult]:
    elapsed = 0
    res = None
    while elapsed < AWAIT_TIMEOUT_SEC:
        res = broker.get_order_status(order_id)
        if res.status in (OrderStatus.COMPLETE, OrderStatus.REJECTED, OrderStatus.CANCELLED):
            return res
        time.sleep(AWAIT_POLL_SEC)
        elapsed += AWAIT_POLL_SEC
    return res


def _confirmed_fill(broker: BaseBroker, side: str, symbol: str, qty: int,
                     dry_run: bool, plan: list) -> Optional[OrderResult]:
    """Place a MARKET order, wait for terminal status. Returns the
    OrderResult only when status==COMPLETE and filled_qty==qty; None
    otherwise (caller must not touch the ledger). dry_run logs intent and
    returns None without calling the broker."""
    if dry_run:
        plan.append({"side": side, "symbol": symbol, "qty": qty})
        return None
    res = broker.buy(symbol, qty) if side == "BUY" else broker.sell(symbol, qty)
    if not res.order_id:
        send_error_alert(f"momentum_atr: {side} {symbol} x{qty} -- no order_id, not retried further this run")
        return None
    final = _await_order_completion(broker, res.order_id)
    if final is None or final.status != OrderStatus.COMPLETE:
        send_error_alert(f"momentum_atr: {side} {symbol} x{qty} did not complete "
                          f"(status={final.status if final else 'unknown'}) -- ledger left unchanged")
        return None
    if final.filled_qty != qty:
        send_error_alert(f"momentum_atr: {side} {symbol} partial fill {final.filled_qty}/{qty} "
                          "-- treated as unresolved, ledger left unchanged")
        return None
    return final


def _greedy_fill(cash: float, priority_syms: List[str], closes: Dict[str, float]) -> Tuple[float, Dict[str, int]]:
    """Ports engine.py's _greedy_fill verbatim: repeatedly sweep the
    priority list buying 1+ more shares wherever leftover cash allows,
    until nothing changes."""
    bought: Dict[str, int] = {}
    changed = True
    while changed:
        changed = False
        for sym in priority_syms:
            price = closes.get(sym)
            if not price or price <= 0:
                continue
            n = int(cash // price)
            if n > 0:
                cash -= n * price
                bought[sym] = bought.get(sym, 0) + n
                changed = True
    return cash, bought


def _buy_split(names: List[str], cash_in: float, closes: Dict[str, float]) -> Tuple[float, Dict[str, int]]:
    """Ports engine.py's buy_split verbatim: equal-split across `names`,
    then cascade any leftover cash."""
    if not names:
        return cash_in, {}
    each = cash_in / len(names)
    remaining = cash_in
    bought: Dict[str, int] = {}
    for sym in names:
        price = closes.get(sym)
        if not price or price <= 0:
            continue
        n = int(each // price)
        if n > 0:
            bought[sym] = bought.get(sym, 0) + n
            remaining -= n * price
    remaining, extra = _greedy_fill(remaining, names, closes)
    for sym, n in extra.items():
        bought[sym] = bought.get(sym, 0) + n
    return remaining, bought


def _real_total_account_equity(broker: BaseBroker) -> float:
    """Real cash + market value of EVERY holding on the shared Upstox account
    -- both this strategy's and the main strategy's positions together, since
    the broker has no concept of per-strategy attribution."""
    cash = broker.get_available_cash()
    holdings = broker.get_holdings()
    invested = sum(h.quantity * h.ltp for h in holdings if h.quantity > 0)
    return cash + invested


def _atr_invested_value(closes: Dict[str, float]) -> float:
    """This strategy's own open-position value right now, from its own DB --
    the slice of the shared account already counted toward its allocation."""
    return sum(p.shares * closes.get(p.symbol, p.entry_price)
               for p in repo.load_positions("OPEN"))


def _get_effective_cash(broker: BaseBroker, internal_cash: float, atr_invested_value: float) -> float:
    real_cash = broker.get_available_cash()
    if internal_cash > 0 and real_cash < internal_cash * MIN_REAL_CASH_RATIO:
        send_error_alert(
            f"momentum_atr: broker cash Rs.{real_cash:,.0f} is <50% of internal ledger cash "
            f"Rs.{internal_cash:,.0f} -- possible capital collision with the other live strategy."
        )
    total_equity = _real_total_account_equity(broker)
    allocation_cap = total_equity * MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT
    headroom = max(0.0, allocation_cap - atr_invested_value)
    return min(internal_cash, real_cash, headroom)


def _execute_buys(broker: BaseBroker, bought: Dict[str, int], reason: str,
                   closes: Dict[str, float], dry_run: bool, plan: list) -> float:
    """Places confirmed BUYs, writes/updates positions, returns total cash
    actually spent (only for confirmed fills)."""
    spent = 0.0
    for sym, qty in bought.items():
        if qty <= 0:
            continue
        res = _confirmed_fill(broker, "BUY", sym, qty, dry_run, plan)
        if res is None:
            continue
        fill_price = res.avg_price if res.avg_price > 0 else closes.get(sym, 0.0)
        value = fill_price * res.filled_qty
        spent += value + buy_charges(value).total

        existing = repo.get_position(sym)
        if existing:
            total_shares = existing.shares + res.filled_qty
            new_entry = (existing.shares * existing.entry_price + res.filled_qty * fill_price) / total_shares
            repo.save_position(Position(
                symbol=sym, entry_date=existing.entry_date, entry_price=new_entry,
                shares=total_shares, status="OPEN", entry_order_id=existing.entry_order_id,
                id=existing.id,
            ))
        else:
            repo.save_position(Position(
                symbol=sym, entry_date=_date.today(), entry_price=fill_price,
                shares=res.filled_qty, status="OPEN", entry_order_id=res.order_id or "",
            ))
    return spent


def _execute_sell(broker: BaseBroker, pos: Position, reason: str, closes: Dict[str, float],
                   dry_run: bool, plan: list) -> Optional[float]:
    """Sells a full position, records the trade with real charges applied.
    Returns proceeds actually credited (only on confirmed full fill)."""
    res = _confirmed_fill(broker, "SELL", pos.symbol, pos.shares, dry_run, plan)
    if res is None:
        return None
    exit_price = res.avg_price if res.avg_price > 0 else closes.get(pos.symbol, pos.entry_price)
    # net_pnl() reports the full round-trip (buy-leg + sell-leg) cost for
    # the trade record -- correct for P&L reporting. But the buy-leg
    # charge was already deducted from cash at entry time (_execute_buys),
    # so the cash credited back here must use the sell-leg charge only, or
    # the buy-leg charge would be subtracted twice.
    pnl = net_pnl(pos.entry_price, exit_price, res.filled_qty)
    sell_value = exit_price * res.filled_qty
    sell_leg_charge = sell_charges(sell_value).total

    repo.close_position_and_save_trade(pos.symbol, Trade(
        symbol=pos.symbol, entry_date=pos.entry_date, entry_price=pos.entry_price,
        shares=res.filled_qty, exit_date=_date.today(), exit_price=exit_price,
        gross_pnl=pnl["gross_pnl"], charges=pnl["total_charges"], net_pnl=pnl["net_pnl"],
        exit_reason=reason, exit_order_id=res.order_id or "",
    ))
    return sell_value - sell_leg_charge


def run_daily(broker: BaseBroker, today: _date, dry_run: bool = False) -> dict:
    """One cron invocation. Returns a summary dict for logging/Telegram."""
    from data.universe import get_all_symbols

    plan: list = []
    state = repo.get_state()
    positions = sorted(repo.load_positions("OPEN"), key=lambda p: p.id or 0)

    if not dry_run and not positions and not repo.load_trades():
        # Never traded yet -- the state row's cash/peak_equity is still the
        # flat deploy-time placeholder (MOMENTUM_ATR_INITIAL_CAPITAL), not
        # real money. Bootstrap it to today's real 40%-of-total-account
        # split before anything else runs, so the kill-switch's peak_equity
        # baseline and this run's own cash reflect the real shared account,
        # not a guess. Safe to do exactly once, only while fully flat --
        # never touches state once a trade exists.
        bootstrap_cap = _real_total_account_equity(broker) * MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT
        repo.update_state(cash=bootstrap_cap, peak_equity=bootstrap_cap)
        state = repo.get_state()

    scores, closes = compute_live_scores(get_all_symbols())
    if len(closes) < 20:
        send_error_alert(f"momentum_atr: only {len(closes)} symbols scored today (need a real universe) -- aborting run")
        return {"aborted": True, "reason": "insufficient_universe_coverage", "scored": len(closes)}

    ranked = rank_symbols(scores)
    rank_of = {sym: i + 1 for i, sym in enumerate(ranked)}

    tripped = check_kill_switch(today, state.cash, positions, closes)
    cash = state.cash
    summary = {"date": str(today), "dry_run": dry_run, "kill_switch_tripped": tripped, "actions": []}

    if not positions:
        # Establish (or re-establish) the top-N portfolio.
        names = ranked[:PORTFOLIO_SIZE]
        if len(names) < PORTFOLIO_SIZE or tripped:
            summary["actions"].append({"type": "SKIP_INITIAL", "reason": "kill_switch" if tripped else "insufficient_ranked_symbols"})
        else:
            effective_cash = _get_effective_cash(broker, cash, 0.0) if not dry_run else cash
            remaining, bought = _buy_split(names, effective_cash, closes)
            spent = _execute_buys(broker, bought, "INITIAL_TOPN_SPLIT", closes, dry_run, plan)
            cash = cash - spent if not dry_run else cash
            summary["actions"].append({"type": "INITIAL_TOPN_SPLIT", "bought": bought})
    else:
        returns = {p.symbol: (closes[p.symbol] - p.entry_price) / p.entry_price * 100
                   for p in positions if p.symbol in closes}

        swap_loser = swap_winner = None
        if returns:
            worst_sym = min(returns, key=returns.get)
            best_sym = max(returns, key=returns.get)
            if worst_sym != best_sym and returns[worst_sym] <= SWAP_LOSS_PCT and returns[best_sym] >= SWAP_GAIN_PCT:
                swap_loser, swap_winner = worst_sym, best_sym

        rank_exit_sym = None
        for p in positions:
            if p.symbol == swap_loser:
                continue
            r = rank_of.get(p.symbol, 10 ** 9)
            if r > PORTFOLIO_SIZE:
                rank_exit_sym = p.symbol
                break

        if swap_loser:
            loser_pos = next(p for p in positions if p.symbol == swap_loser)
            proceeds = _execute_sell(broker, loser_pos, "STOP_LOSS_-3%_SWAP", closes, dry_run, plan)
            if proceeds is not None:
                cash += proceeds
                summary["actions"].append({"type": "SWAP_SELL", "symbol": swap_loser})
                if not tripped:
                    price = closes.get(swap_winner)
                    invested_now = _atr_invested_value(closes) if not dry_run else 0.0
                    effective_cash = _get_effective_cash(broker, cash, invested_now) if not dry_run else cash
                    if price and price > 0:
                        n = int(min(cash, effective_cash) // price)
                        if n > 0:
                            spent = _execute_buys(broker, {swap_winner: n}, "TAKE_PROFIT_+3%_SWAP", closes, dry_run, plan)
                            cash = cash - spent if not dry_run else cash
                            summary["actions"].append({"type": "SWAP_BUY", "symbol": swap_winner, "qty": n})
                else:
                    summary["actions"].append({"type": "SKIP_SWAP_BUY", "reason": "kill_switch"})

        if rank_exit_sym:
            exit_pos = next(p for p in positions if p.symbol == rank_exit_sym)
            proceeds = _execute_sell(broker, exit_pos, "RANK_RULE_EXIT", closes, dry_run, plan)
            if proceeds is not None:
                cash += proceeds
                summary["actions"].append({"type": "RANK_EXIT_SELL", "symbol": rank_exit_sym})
                if not tripped:
                    names = ranked[:PORTFOLIO_SIZE]
                    invested_now = _atr_invested_value(closes) if not dry_run else 0.0
                    effective_cash = _get_effective_cash(broker, cash, invested_now) if not dry_run else cash
                    remaining, bought = _buy_split(names, min(cash, effective_cash), closes)
                    if bought:
                        spent = _execute_buys(broker, bought, "RANK_EXIT_REALLOC_TOPN", closes, dry_run, plan)
                        cash = cash - spent if not dry_run else cash
                        summary["actions"].append({"type": "RANK_EXIT_REALLOC", "bought": bought})
                else:
                    summary["actions"].append({"type": "SKIP_RANK_EXIT_REALLOC", "reason": "kill_switch"})

    if dry_run:
        summary["planned_orders"] = plan
        return summary

    repo.update_state(cash=cash)
    final_positions = repo.load_positions("OPEN")
    invested = sum(p.shares * closes.get(p.symbol, p.entry_price) for p in final_positions)
    total_equity = cash + invested
    new_state = repo.get_state()
    drawdown = (new_state.peak_equity - total_equity) / new_state.peak_equity if new_state.peak_equity > 0 else 0.0

    from momentum_atr.models import EquitySnapshot
    repo.save_equity_snapshot(EquitySnapshot(
        date=today, cash=cash, invested_value=invested, total_equity=total_equity,
        peak_equity=new_state.peak_equity, drawdown_pct=drawdown, kill_switch_tripped=tripped,
    ))
    summary["cash"] = cash
    summary["invested_value"] = invested
    summary["total_equity"] = total_equity
    summary["open_positions"] = len(final_positions)
    return summary
