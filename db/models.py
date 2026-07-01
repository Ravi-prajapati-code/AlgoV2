"""Dataclasses for core domain objects."""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Dict, Any


@dataclass
class Position:
    symbol: str
    sector: str
    entry_date: date
    entry_price: float
    shares: int
    stop_loss: float
    take_profit: float
    trailing_stop: float
    peak_price: float
    status: str = "OPEN"
    days_below_ema50: int = 0
    id: Optional[int] = None

    @property
    def cost_value(self) -> float:
        """Original investment (entry price * shares)."""
        return self.entry_price * self.shares

    def market_value(self, current_price: float) -> float:
        """Current market value (current price * shares)."""
        return current_price * self.shares

    def unrealized_pnl(self, current_price: float) -> float:
        return (current_price - self.entry_price) * self.shares

    def unrealized_pct(self, current_price: float) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (current_price - self.entry_price) / self.entry_price


@dataclass
class Trade:
    symbol: str
    sector: str
    entry_date: date
    entry_price: float
    shares: int
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    gross_pnl: Optional[float] = None
    charges: Optional[float] = None
    net_pnl: Optional[float] = None
    exit_reason: Optional[str] = None
    hold_days: Optional[int] = None
    slippage_pct: Optional[float] = None
    id: Optional[int] = None


@dataclass
class Signal:
    date: date
    symbol: str
    action: str           # BUY | SELL | HOLD
    score: float = 0.0
    price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    reason: str = ""
    indicators: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None


@dataclass
class PortfolioSnapshot:
    date: date
    cash: float
    invested: float
    total_value: float
    open_positions: int
    daily_pnl: float = 0.0
    cumulative_pnl: float = 0.0
    regime: Optional[str] = None
    capital_injected: float = 0.0   # external deposits detected that day — excluded from P&L
    id: Optional[int] = None
