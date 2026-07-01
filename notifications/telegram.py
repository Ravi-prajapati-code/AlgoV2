"""
Telegram Bot alert sender.
Sends a formatted daily signal summary to your Telegram chat.

Setup:
  1. Message @BotFather on Telegram → create bot → get BOT_TOKEN
  2. Message your bot once → get CHAT_ID from:
     https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
  3. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env or GitHub Secrets
"""

import logging
import requests
import html
from datetime import date
from typing import List, Optional

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from db.models import Signal, PortfolioSnapshot, Position

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"

_RECIPIENTS = [
    ("TELEGRAM_BOT_TOKEN", lambda: TELEGRAM_BOT_TOKEN, lambda: TELEGRAM_CHAT_ID),
]


def _escape(text: any) -> str:
    """Escape HTML special characters in string or numeric values."""
    if text is None:
        return "?"
    return html.escape(str(text))


def _send(text: str) -> bool:
    """Send message to all configured Telegram recipients. Returns True if at least one succeeds."""
    sent = False
    for label, get_token, get_chat in _RECIPIENTS:
        token, chat_id = get_token(), get_chat()
        if not token or not chat_id:
            continue
        try:
            url = _API_BASE.format(token=token)
            resp = requests.post(url, json={
                "chat_id":    chat_id,
                "text":       text,
                "parse_mode": "HTML",
            }, timeout=10)
            resp.raise_for_status()
            sent = True
        except Exception as e:
            logger.error(f"[Telegram] Failed to send via {label}: {e}")
    if not sent:
        logger.warning("[Telegram] No recipients configured or all sends failed")
    return sent


def send_daily_summary(
    today: date,
    buy_signals: List[Signal],
    sell_signals: List[Signal],
    snapshot: Optional[PortfolioSnapshot],
    open_positions: List[Position] = None,
    prices: dict = None,
):
    """Compose and send the end-of-day summary including current holdings."""
    from db.repository import load_baseline_capital
    from config.settings import INITIAL_CAPITAL

    lines = [f"<b>Algo Swing Trader — {today}</b>"]

    if snapshot:
        from db.repository import total_capital_injected_ever
        baseline        = load_baseline_capital() or INITIAL_CAPITAL
        total_injected  = total_capital_injected_ever()
        total_deployed  = baseline + total_injected
        total_return    = snapshot.cumulative_pnl
        return_pct      = (total_return / total_deployed * 100) if total_deployed > 0 else 0
        market_val      = snapshot.total_value - snapshot.cash

        pnl_emoji = "🚀" if total_return >= 0 else "📉"
        injected_line = (
            f"  Injected Capital:₹{total_injected:,.2f}\n"
            f"  Total Deployed:  ₹{total_deployed:,.2f}\n"
        ) if total_injected > 0 else ""
        lines.append(
            f"\n{pnl_emoji} <b>Portfolio Performance</b>\n"
            f"  Starting Capital:₹{baseline:,.2f}\n"
            f"{injected_line}"
            f"  Total Value:     ₹{snapshot.total_value:,.2f}\n"
            f"  Holdings Value:  ₹{market_val:,.2f}\n"
            f"  Available Cash:  ₹{snapshot.cash:,.2f}\n"
            f"  Total Return:    ₹{total_return:+,.2f} ({return_pct:+.1f}% on deployed)\n"
            f"  Today's P&L:     ₹{snapshot.daily_pnl:+,.2f}"
        )

    if open_positions:
        lines.append("\n💼 <b>Current Holdings</b>")
        for pos in open_positions:
            current_price = (prices or {}).get(pos.symbol, pos.entry_price)
            current_val   = pos.shares * current_price
            cost_val      = pos.shares * pos.entry_price
            unrealized    = current_val - cost_val
            unreal_pct    = (unrealized / cost_val * 100) if cost_val > 0 else 0
            lines.append(
                f"  • <b>{_escape(pos.symbol)}</b>: {pos.shares} qty\n"
                f"    Entry: ₹{pos.entry_price:,.2f} | Now: ₹{current_price:,.2f}\n"
                f"    P&L: ₹{unrealized:+,.2f} ({unreal_pct:+.1f}%)"
            )

    if buy_signals:
        lines.append("\n🟢 <b>BUY Signals</b>")
        for sig in buy_signals[:5]:   # Top 5
            lines.append(
                f"  • <b>{_escape(sig.symbol)}</b> @ ₹{sig.price:,.2f}\n"
                f"    SL: ₹{sig.stop_loss:,.2f} | Tgt: ₹{sig.take_profit:,.2f}\n"
                f"    RS Rank: {sig.score:.1f}"
            )

    if sell_signals:
        lines.append("\n🔴 <b>SELL Signals</b>")
        for sig in sell_signals:
            lines.append(
                f"  • <b>{_escape(sig.symbol)}</b> @ ₹{sig.price:,.2f}\n"
                f"    Reason: {_escape(sig.reason)}"
            )

    if not buy_signals and not sell_signals and not open_positions:
        lines.append("\n⏸ No activity today.")

    lines.append("\n<i>Paper trading — not financial advice</i>")
    _send("\n".join(lines))


def send_error_alert(message: str):
    """Send an error notification."""
    _send(f"⚠️ <b>Algo Error</b>\n\n{_escape(message)}")


def send_message(text: str) -> bool:
    """Generic message sender — used by universe reporter and other modules.
    Caller is responsible for HTML-safe content; variable values must be wrapped
    in _escape() individually. Do NOT escape the whole message here."""
    return _send(text)
