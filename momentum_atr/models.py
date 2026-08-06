"""
Lean dataclasses for the standalone momentum x ATR live strategy ledger.
Physically separate from db/models.py -- this strategy never writes to the
main trading.db positions/trades tables (db/schema.sql's positions.symbol
TEXT NOT NULL UNIQUE would silently clobber the main strategy's row for
any symbol both strategies hold).
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class Position:
    symbol: str
    entry_date: date
    entry_price: float
    shares: int
    status: str = "OPEN"
    entry_order_id: str = ""
    id: Optional[int] = None

    @property
    def cost_value(self) -> float:
        return self.entry_price * self.shares

    def market_value(self, current_price: float) -> float:
        return current_price * self.shares


@dataclass
class Trade:
    symbol: str
    entry_date: date
    entry_price: float
    shares: int
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    gross_pnl: Optional[float] = None
    charges: Optional[float] = None
    net_pnl: Optional[float] = None
    exit_reason: Optional[str] = None
    exit_order_id: str = ""
    id: Optional[int] = None


@dataclass
class EquitySnapshot:
    date: date
    cash: float
    invested_value: float
    total_equity: float
    peak_equity: float
    drawdown_pct: float = 0.0
    kill_switch_tripped: bool = False
    id: Optional[int] = None


@dataclass
class StrategyState:
    cash: float
    peak_equity: float
    kill_switch_tripped: bool = False
    kill_switch_tripped_date: Optional[date] = None
