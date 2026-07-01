"""
Daily P&L Summary — sends morning Telegram with portfolio snapshot.
Shows: open positions with unrealized P&L, yesterday's closed trades.

Cron (add to server):
  # 09:25 IST Mon-Fri — after market open prices settle
  55 3 * * 1-5  cd /home/ubuntu/AlgoV2 && .venv/bin/python scripts/daily_pnl_summary.py >> logs/pnl_summary.log 2>&1
"""

import os
import sys
import logging
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("pnl_summary")

import requests
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, INITIAL_CAPITAL


def _send(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(msg)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.error("Telegram send failed: %s", e)


def get_live_prices(symbols: list) -> dict:
    """Fetch last close prices via yfinance for open positions."""
    if not symbols:
        return {}
    try:
        import yfinance as yf
        tickers = yf.download(symbols, period="2d", progress=False, auto_adjust=True)
        prices = {}
        if "Close" in tickers.columns.get_level_values(0):
            close = tickers["Close"]
        else:
            close = tickers
        for sym in symbols:
            try:
                prices[sym] = float(close[sym].dropna().iloc[-1])
            except Exception:
                pass
        return prices
    except Exception as e:
        logger.warning("yfinance price fetch failed: %s", e)
        return {}


def run_summary():
    today = date.today()
    yesterday = today - timedelta(days=1)
    # Look back 3 days to catch Friday→Monday gap
    lookback = today - timedelta(days=3)

    from db.repository import load_positions, load_trades, load_baseline_capital

    open_positions = load_positions("OPEN")
    all_trades = load_trades()

    # Trades closed yesterday or over weekend
    recent_trades = [
        t for t in all_trades
        if t.exit_date and t.exit_date >= lookback
    ]

    # Fetch live prices for open positions
    open_symbols = [p.symbol for p in open_positions]
    prices = get_live_prices(open_symbols)

    # Compute unrealized P&L
    total_unrealized = 0.0
    for pos in open_positions:
        ltp = prices.get(pos.symbol, pos.entry_price)
        total_unrealized += (ltp - pos.entry_price) * pos.shares

    # Compute realized P&L from recent trades
    recent_realized = sum(t.net_pnl for t in recent_trades)

    # Overall portfolio P&L
    baseline = load_baseline_capital() or INITIAL_CAPITAL
    total_realized = sum(t.net_pnl for t in all_trades)
    overall_pct = (total_realized / baseline * 100) if baseline > 0 else 0.0

    regime = _get_regime()

    lines = [f"📊 <b>Algo Morning Summary — {today.strftime('%a %d %b %Y')}</b>"]
    lines.append(f"🌐 Regime: <b>{regime}</b>")

    # Portfolio overview
    pnl_icon = "🟢" if total_realized >= 0 else "🔴"
    lines.append(
        f"\n{pnl_icon} <b>Portfolio</b>\n"
        f"  Realized P&L (all-time): ₹{total_realized:+,.0f} ({overall_pct:+.1f}%)\n"
        f"  Unrealized (open pos):   ₹{total_unrealized:+,.0f}\n"
        f"  Baseline capital:        ₹{baseline:,.0f}"
    )

    # Open positions
    if open_positions:
        lines.append(f"\n💼 <b>Open Positions ({len(open_positions)})</b>")
        for pos in open_positions:
            ltp = prices.get(pos.symbol, pos.entry_price)
            unreal = (ltp - pos.entry_price) * pos.shares
            unreal_pct = ((ltp - pos.entry_price) / pos.entry_price * 100) if pos.entry_price > 0 else 0
            icon = "🟢" if unreal >= 0 else "🔴"
            lines.append(
                f"  {icon} <b>{pos.symbol.replace('.NS', '')}</b> × {pos.shares} qty\n"
                f"    Entry ₹{pos.entry_price:,.1f} → Now ₹{ltp:,.1f} | "
                f"P&L ₹{unreal:+,.0f} ({unreal_pct:+.1f}%)\n"
                f"    SL: ₹{pos.stop_loss:,.1f} | Trail: ₹{pos.trailing_stop:,.1f}"
            )
    else:
        lines.append("\n💼 <b>Open Positions:</b> None (cash / GOLDBEES)")

    # Recent closed trades
    if recent_trades:
        lines.append(f"\n📋 <b>Recent Closed Trades</b>")
        for t in recent_trades[:5]:
            icon = "✅" if t.net_pnl >= 0 else "❌"
            lines.append(
                f"  {icon} <b>{t.symbol.replace('.NS', '')}</b> "
                f"[{t.exit_date}] ₹{t.net_pnl:+,.0f} | {t.exit_reason}"
            )
    else:
        lines.append("\n📋 <b>Recent Trades:</b> None in last 3 days")

    _send("\n".join(lines))
    logger.info("P&L summary sent. Open=%d, Recent trades=%d", len(open_positions), len(recent_trades))


def _get_regime() -> str:
    """Read latest regime from DB or return UNKNOWN."""
    try:
        from db.repository import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT regime FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return row[0] if row else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


if __name__ == "__main__":
    run_summary()
