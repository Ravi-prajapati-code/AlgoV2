"""
Abstract Broker Interface.

All broker implementations must subclass BaseBroker and implement
the abstract methods. This ensures the rest of the system can switch
between brokers (Upstox, Zerodha, Paper) without changing strategy code.

Order lifecycle:
  place_order() → OrderResult (with order_id)
  get_order_status(order_id) → OrderStatus
  cancel_order(order_id) → bool

Position management:
  get_positions() → list of live Position objects
  get_portfolio_value() → float

Usage
-----
    from broker.paper import PaperBroker
    broker = PaperBroker(initial_capital=75000)
    result = broker.place_order("RELIANCE.NS", "BUY", shares=10, price=2500)
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class OrderSide(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT  = "LIMIT"
    SL     = "SL"        # Stop-loss market
    SL_M   = "SL-M"      # Stop-loss limit


class OrderStatus(str, Enum):
    PENDING    = "PENDING"
    OPEN       = "OPEN"
    COMPLETE   = "COMPLETE"
    REJECTED   = "REJECTED"
    CANCELLED  = "CANCELLED"
    PARTIAL    = "PARTIAL"


@dataclass
class OrderRequest:
    """Parameters for placing an order."""
    symbol:      str
    side:        OrderSide
    quantity:    int
    order_type:  OrderType = OrderType.MARKET
    price:       float = 0.0          # For LIMIT orders
    trigger_price: float = 0.0        # For SL orders
    product:     str = "CNC"          # CNC (delivery) | MIS (intraday)
    tag:         str = ""             # Optional metadata tag
    is_gtt:      bool = False         # Place as GTT order
    gtt_trigger_price: float = 0.0    # Trigger price for GTT
    is_amo:      bool = False         # After Market Order (place outside market hours)


@dataclass
class OrderResult:
    """Result of a placed order."""
    order_id:       str
    status:         OrderStatus
    symbol:         str
    side:           OrderSide
    requested_qty:  int
    filled_qty:     int = 0
    avg_price:      float = 0.0
    placed_at:      Optional[datetime] = None
    filled_at:      Optional[datetime] = None
    rejection_reason: str = ""
    raw_response:   dict = field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        return self.status == OrderStatus.COMPLETE

    @property
    def is_partial(self) -> bool:
        return self.status == OrderStatus.PARTIAL

    @property
    def fill_pct(self) -> float:
        return (self.filled_qty / self.requested_qty * 100) if self.requested_qty > 0 else 0.0


@dataclass
class LivePosition:
    """A live position from the broker's API."""
    symbol:      str
    quantity:    int
    avg_price:   float
    ltp:         float    # Last traded price
    pnl:         float
    product:     str      # CNC | MIS


class BaseBroker(ABC):
    """
    Abstract base class for all broker integrations.

    Concrete implementations:
      - PaperBroker (simulation — no real orders)
      - UpstoxBroker (Upstox v2 API)
    """

    MAX_RETRY_ATTEMPTS = 3
    RETRY_BACKOFF_SEC  = [2, 4, 8]   # Exponential backoff

    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResult:
        """Submit an order to the broker."""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending or open order. Returns True if successful."""
        ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderResult:
        """Fetch current status of an order by ID."""
        ...

    @abstractmethod
    def get_positions(self) -> List[LivePosition]:
        """Return all open positions from the broker."""
        ...

    @abstractmethod
    def get_portfolio_value(self) -> float:
        """Return total portfolio value (cash + invested)."""
        ...

    @abstractmethod
    def get_available_cash(self) -> float:
        """Return available cash balance for trading."""
        ...

    # ── Retry wrapper ──────────────────────────────────────────────────────

    def find_recent_order_by_tag(self, tag: str) -> Optional[OrderResult]:
        """Return today's order matching `tag` if one exists, else None.

        Used by place_order_with_retry to detect orders that the broker ACCEPTED
        but whose HTTP response was lost (timeout) — prevents placing a duplicate
        on retry. Default returns None (brokers with immediate, exception-free
        fills like PaperBroker never hit the timeout-retry path)."""
        return None

    def place_order_with_retry(self, request: OrderRequest) -> OrderResult:
        """
        Place an order with automatic retry on transient failures.
        Uses exponential backoff (2s, 4s, 8s).

        Idempotency: a unique tag is stamped on the request so that, if a retry
        is triggered by a lost response (timeout) after the broker already
        accepted the order, the duplicate can be detected and skipped.
        """
        import time
        import uuid

        # Stamp a unique idempotency tag (≤20 alphanumeric chars for Upstox).
        if not getattr(request, "tag", "") or not request.tag.startswith("algo"):
            request.tag = "algo" + uuid.uuid4().hex[:12]

        last_exc = None
        for attempt, delay in enumerate(self.RETRY_BACKOFF_SEC, start=1):
            try:
                result = self.place_order(request)
                if result.status not in (OrderStatus.REJECTED,):
                    if attempt > 1:
                        logger.info("[Broker] Order succeeded on attempt %d", attempt)
                    return result
            except Exception as exc:
                last_exc = exc
                from monitoring.logger import log_api_failure
                log_api_failure(
                    broker=self.__class__.__name__,
                    operation="place_order",
                    exc=exc,
                    attempt=attempt,
                )
                # Before retrying, check whether the broker actually accepted the
                # order despite the lost response — avoids a duplicate fill.
                try:
                    existing = self.find_recent_order_by_tag(request.tag)
                except Exception as lookup_exc:
                    existing = None
                    logger.warning("[Broker] Idempotency lookup failed: %s", lookup_exc)
                if existing is not None:
                    logger.warning(
                        "[Broker] Order for %s already landed at broker (tag=%s, id=%s) "
                        "despite send failure — skipping retry to avoid duplicate.",
                        request.symbol, request.tag, existing.order_id,
                    )
                    return existing
                if attempt < len(self.RETRY_BACKOFF_SEC):
                    logger.warning(
                        "[Broker] Attempt %d failed (%s) — retrying in %ds",
                        attempt, exc, delay,
                    )
                    time.sleep(delay)

        # All retries exhausted
        logger.error("[Broker] All %d attempts failed for %s", len(self.RETRY_BACKOFF_SEC), request.symbol)
        return OrderResult(
            order_id="", status=OrderStatus.REJECTED,
            symbol=request.symbol, side=request.side,
            requested_qty=request.quantity,
            rejection_reason=f"All retries exhausted: {last_exc}",
        )

    # ── Convenience helpers ────────────────────────────────────────────────

    def buy(self, symbol: str, quantity: int, price: float = 0.0) -> OrderResult:
        """Convenience: place a market BUY order."""
        req = OrderRequest(
            symbol=symbol, side=OrderSide.BUY,
            quantity=quantity, order_type=OrderType.MARKET,
            price=price, product="CNC",
        )
        return self.place_order_with_retry(req)

    def sell(self, symbol: str, quantity: int, price: float = 0.0) -> OrderResult:
        """Convenience: place a market SELL order."""
        req = OrderRequest(
            symbol=symbol, side=OrderSide.SELL,
            quantity=quantity, order_type=OrderType.MARKET,
            price=price, product="CNC",
        )
        return self.place_order_with_retry(req)
