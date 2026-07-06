"""
Upstox v2 API Broker Integration.

Implements the BaseBroker interface for Upstox delivery (CNC) trading.

Prerequisites
-------------
1. Set environment variables:
     UPSTOX_API_KEY, UPSTOX_API_SECRET, UPSTOX_ACCESS_TOKEN
2. Generate access token via Upstox OAuth2 flow (done once per day).
3. Use NSE instrument keys (e.g. "NSE_EQ|INE002A01018" for RELIANCE).

Symbol mapping
--------------
Trading symbols (e.g. "RELIANCE.NS") are mapped to Upstox instrument keys. 
The mapping file is loaded from data/instruments/nse_instruments.json.

Order types supported:
  - MARKET (immediate fill at best available price)
  - LIMIT  (fill at specified price or better)
  - SL-M   (stop-loss market: triggers at trigger_price, fills at market)

⚠️ WARNING: This module places REAL orders. Only use in production
    when you have thoroughly tested with PaperBroker first.
"""

import logging
import os
import time
from datetime import datetime, date
from typing import List, Optional

import socket
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.connection import allowed_gai_family
import urllib3

from broker.base import (
    BaseBroker, OrderRequest, OrderResult, OrderStatus,
    OrderSide, OrderType, LivePosition,
)
from config.settings import UPSTOX_API_KEY, UPSTOX_API_SECRET, UPSTOX_ACCESS_TOKEN
from monitoring.logger import log_api_failure

logger = logging.getLogger(__name__)

UPSTOX_BASE_URL = "https://api.upstox.com/v2"
UPSTOX_SANDBOX  = "https://api-hft.upstox.com/v2"   # Sandbox for testing


class _IPv4HTTPAdapter(HTTPAdapter):
    """Force all requests through IPv4 to match Upstox static IP whitelist."""
    def send(self, request, **kwargs):
        _orig = allowed_gai_family
        urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET
        try:
            return super().send(request, **kwargs)
        finally:
            urllib3.util.connection.allowed_gai_family = _orig


class UpstoxBroker(BaseBroker):
    """
    Upstox v2 REST API broker.

    Only enabled when UPSTOX_ACCESS_TOKEN is set in environment.
    Raises UpstoxAuthError on startup if token is missing.

    Usage
    -----
        broker = UpstoxBroker()
        result = broker.buy("RELIANCE.NS", quantity=5, price=0)  # market order
    """

    def __init__(self, sandbox: bool = False):
        # Use os.getenv directly to avoid stale cached values from settings.py
        token = os.getenv("UPSTOX_ACCESS_TOKEN")
        if not token:
            raise UpstoxAuthError(
                "UPSTOX_ACCESS_TOKEN not set. Generate token via Upstox OAuth2 flow."
            )
        self._base_url = UPSTOX_SANDBOX if sandbox else UPSTOX_BASE_URL
        self._headers  = {
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }
        self._instrument_cache: dict = {}
        # Force IPv4 to match Upstox static IP whitelist (avoids IPv6 mismatch)
        self._session = requests.Session()
        self._session.mount("https://", _IPv4HTTPAdapter())
        self._session.mount("http://",  _IPv4HTTPAdapter())
        logger.info("[Upstox] Initialised (sandbox=%s)", sandbox)

    # ── BaseBroker implementation ──────────────────────────────────────────

    def place_order(self, request: OrderRequest) -> OrderResult:
        """Place order via Upstox v2 /order/place endpoint."""
        instrument_key = self._resolve_instrument(request.symbol)
        if not instrument_key:
            return OrderResult(
                order_id="", status=OrderStatus.REJECTED,
                symbol=request.symbol, side=request.side,
                requested_qty=request.quantity,
                rejection_reason=f"Cannot resolve instrument key for {request.symbol}",
            )

        # Map internal product names to Upstox API names
        # CNC (internal) -> D (Delivery)
        # MIS (internal) -> I (Intraday)
        upstox_product = "D" if request.product == "CNC" else "I"

        if getattr(request, 'is_gtt', False):
            # Place GTT Order via V3 API
            # Upstox V3: top-level "type" (SINGLE/MULTIPLE), "strategy" (ENTRY/TARGET/
            # STOPLOSS) lives per-rule, not top-level. Response is {"gtt_order_ids": [...]}.
            trigger_type = "ABOVE" if request.side.value == "BUY" else "BELOW"
            # NSE GTT orders execute as LIMIT on trigger — there is no true market-on-
            # trigger at the exchange level, whatever "order_type" is requested here.
            # Without an explicit price, Upstox pegs the limit to trigger_price itself,
            # which is unfillable the instant price gaps/moves through it (2026-07-01
            # GOLDBEES incident: left naked all day). Callers must pass a buffered
            # request.price — see portfolio.manager.gtt_stop_limit_price().
            if request.price <= 0:
                logger.warning(
                    "[Upstox] GTT for %s has no explicit price — Upstox will peg the "
                    "LIMIT to trigger_price (%.2f), leaving zero fill buffer on a gap.",
                    request.symbol, request.gtt_trigger_price,
                )
            payload = {
                "type": "SINGLE",
                "quantity": request.quantity,
                "product": upstox_product,
                "rules": [
                    {
                        "strategy": "ENTRY",
                        "trigger_type": trigger_type,
                        "trigger_price": request.gtt_trigger_price,
                        "order_type": "LIMIT",
                    }
                ],
                "instrument_token": instrument_key,
                "transaction_type": request.side.value
            }
            if request.price > 0:
                payload["price"] = request.price

            try:
                resp = self._session.post(
                    f"{self._base_url.replace('/v2', '/v3')}/order/gtt/place",
                    json=payload,
                    headers=self._headers,
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()

                order_id = (data.get("data", {}).get("gtt_order_ids") or [""])[0]
                logger.info("[Upstox] GTT Order placed: %s %s×%s → gtt_order_id=%s (Trigger: %s)",
                            request.side.value, request.quantity, request.symbol, order_id, request.gtt_trigger_price)

                return OrderResult(
                    order_id=order_id,
                    status=OrderStatus.OPEN,
                    symbol=request.symbol,
                    side=request.side,
                    requested_qty=request.quantity,
                    placed_at=datetime.now(),
                    raw_response=data,
                )
            except requests.HTTPError as e:
                reason = self._parse_error(e.response)
                logger.error("[Upstox] HTTP error placing GTT order for %s: %s", request.symbol, reason)
                return OrderResult(
                    order_id="", status=OrderStatus.REJECTED,
                    symbol=request.symbol, side=request.side,
                    requested_qty=request.quantity,
                    rejection_reason=reason,
                )

        payload = {
            "quantity":        request.quantity,
            "product":         upstox_product,
            "validity":        "DAY",
            "price":           request.price,
            "tag":             request.tag or "algo_swing",
            "instrument_token": instrument_key,
            "order_type":      request.order_type.value,
            "transaction_type": request.side.value,
            "disclosed_quantity": 0,
            "trigger_price":   request.trigger_price,
            "is_amo":          getattr(request, 'is_amo', False),
        }

        try:
            resp = self._session.post(
                f"{self._base_url}/order/place",
                json=payload,
                headers=self._headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            order_id = data.get("data", {}).get("order_id", "")
            logger.info("[Upstox] Order placed: %s %s×%s → order_id=%s",
                        request.side.value, request.quantity, request.symbol, order_id)

            return OrderResult(
                order_id=order_id,
                status=OrderStatus.OPEN,
                symbol=request.symbol,
                side=request.side,
                requested_qty=request.quantity,
                placed_at=datetime.now(),
                raw_response=data,
            )

        except requests.HTTPError as e:
            reason = self._parse_error(e.response)
            logger.error("[Upstox] HTTP error placing order for %s: %s", request.symbol, reason)
            return OrderResult(
                order_id="", status=OrderStatus.REJECTED,
                symbol=request.symbol, side=request.side,
                requested_qty=request.quantity,
                rejection_reason=reason,
            )

    def get_pending_gtt_orders(self, symbol: str):
        """Return list of pending GTT order IDs for a given symbol.

        Returns None on API failure (distinct from an empty list, which means the
        call succeeded but this symbol has no pending GTTs) so callers never
        mistake an API error for 'nothing to cancel'. Mirrors
        list_active_gtt_instrument_keys()."""
        try:
            resp = self._session.get(
                f"{self._base_url.replace('/v2', '/v3')}/order/gtt",
                headers=self._headers,
                timeout=10,
            )
            resp.raise_for_status()
            orders = resp.json().get("data", [])
            instrument_key = self._resolve_instrument(symbol)
            pending = []
            for o in orders:
                # Upstox may use either "instrument_token" or "instrument_key" in response
                o_token = o.get("instrument_token") or o.get("instrument_key", "")
                if o_token != instrument_key:
                    continue
                # V3: status lives per-rule now, not on the order itself.
                rule_statuses = {(r.get("status") or "").upper() for r in o.get("rules", [])}
                if rule_statuses & {"PENDING", "OPEN", "ACTIVE", "CREATED", "SCHEDULED"}:
                    oid = str(o.get("id") or o.get("gtt_order_id", ""))
                    pending.append(oid)
            return [oid for oid in pending if oid]
        except Exception as e:
            logger.error("[Upstox] get_pending_gtt_orders(%s) failed: %s", symbol, e)
            return None

    def list_active_gtt_instrument_keys(self):
        """Return the set of instrument_keys that currently have an ACTIVE GTT order.

        Returns None on API failure (distinct from an empty set, which means the
        call succeeded but no GTTs exist) so callers never mistake an API error
        for 'all positions naked'."""
        try:
            resp = self._session.get(
                f"{self._base_url.replace('/v2', '/v3')}/order/gtt",
                headers=self._headers,
                timeout=10,
            )
            resp.raise_for_status()
            orders = resp.json().get("data", []) or []
            keys = set()
            for o in orders:
                # V3: status lives per-rule now, not on the order itself.
                rule_statuses = {(r.get("status") or "").upper() for r in o.get("rules", [])}
                if rule_statuses & {"PENDING", "OPEN", "ACTIVE", "CREATED", "SCHEDULED"}:
                    k = o.get("instrument_token") or o.get("instrument_key", "")
                    if k:
                        keys.add(k)
            return keys
        except Exception as e:
            logger.error("[Upstox] list_active_gtt_instrument_keys failed: %s", e)
            return None

    def cancel_gtt_order(self, gtt_order_id: str) -> bool:
        """Cancel a GTT order by ID."""
        try:
            resp = self._session.delete(
                f"{self._base_url.replace('/v2', '/v3')}/order/gtt/cancel",
                json={"gtt_order_id": gtt_order_id},
                headers=self._headers,
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("[Upstox] Cancelled GTT order %s", gtt_order_id)
            return True
        except Exception as e:
            logger.error("[Upstox] Failed to cancel GTT order %s: %s", gtt_order_id, e)
            return False

    def cancel_order(self, order_id: str) -> bool:
        try:
            resp = self._session.delete(
                f"{self._base_url}/order/cancel",
                params={"order_id": order_id},
                headers=self._headers,
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("[Upstox] Cancelled order %s", order_id)
            return True
        except Exception as e:
            logger.error("[Upstox] Failed to cancel order %s: %s", order_id, e)
            return False

    def find_recent_order_by_tag(self, tag: str) -> Optional["OrderResult"]:
        """Look up today's order book for an order carrying `tag`.

        Returns the matching OrderResult if found (idempotency guard against
        duplicate placement on timeout-retry), else None. Ignores cancelled/
        rejected orders — those did not consume the intent and may be retried."""
        if not tag:
            return None
        try:
            resp = self._session.get(
                f"{self._base_url}/order/retrieve-all",
                headers=self._headers,
                timeout=10,
            )
            resp.raise_for_status()
            orders = resp.json().get("data", []) or []
            for o in orders:
                if o.get("tag") != tag:
                    continue
                status = (o.get("status") or "").lower()
                if status in ("cancelled", "rejected"):
                    continue
                return self._parse_order_response(o)
            return None
        except Exception as e:
            logger.warning("[Upstox] find_recent_order_by_tag(%s) failed: %s", tag, e)
            return None

    def get_order_status(self, order_id: str) -> OrderResult:
        try:
            resp = self._session.get(
                f"{self._base_url}/order/details",
                params={"order_id": order_id},
                headers=self._headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return self._parse_order_response(data)
        except Exception as e:
            logger.error("[Upstox] get_order_status(%s) failed: %s", order_id, e)
            return OrderResult(
                order_id=order_id, status=OrderStatus.PENDING,
                symbol="", side=OrderSide.BUY, requested_qty=0,
            )

    def get_positions(self) -> List[LivePosition]:
        """Combine short-term positions and long-term holdings."""
        positions = []
        try:
            # 1. Fetch short-term (intraday or T1)
            resp = self._session.get(
                f"{self._base_url}/portfolio/short-term-positions",
                headers=self._headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            for pos in data:
                qty = int(pos.get("quantity", 0))
                if qty > 0:
                    positions.append(LivePosition(
                        symbol=pos["tradingsymbol"] + ".NS",
                        quantity=qty,
                        avg_price=float(pos.get("average_price", 0)),
                        ltp=float(pos.get("last_price", 0)),
                        pnl=float(pos.get("realised_profit", 0)),
                        product=pos.get("product", "CNC"),
                    ))
            
            # 2. Fetch long-term holdings
            holdings = self.get_holdings()
            positions.extend(holdings)
            
        except Exception as e:
            logger.error("[Upstox] get_positions() combined failed: %s", e)
            
        return positions

    def get_holdings(self) -> List[LivePosition]:
        """Fetch long-term delivery holdings from Upstox."""
        try:
            resp = self._session.get(
                f"{self._base_url}/portfolio/long-term-holdings",
                headers=self._headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return [
                LivePosition(
                    symbol=pos["tradingsymbol"] + ".NS",
                    quantity=int(pos.get("quantity", 0)),
                    avg_price=float(pos.get("average_price", 0)),
                    ltp=float(pos.get("last_price", 0)),
                    pnl=float(pos.get("pnl", 0)),
                    product="CNC",
                )
                for pos in data
                if int(pos.get("quantity", 0)) > 0
            ]
        except Exception as e:
            logger.error("[Upstox] get_holdings() failed: %s", e)
            return []

    def get_portfolio_value(self) -> float:
        try:
            resp = self._session.get(
                f"{self._base_url}/user/get-funds-and-margin",
                headers=self._headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            equity = data.get("equity", {})
            return float(equity.get("net", 0))
        except Exception as e:
            logger.error("[Upstox] get_portfolio_value() failed: %s", e)
            return 0.0

    def get_available_cash(self) -> float:
        try:
            resp = self._session.get(
                f"{self._base_url}/user/get-funds-and-margin",
                headers=self._headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            equity = data.get("equity", {})
            return float(equity.get("available_margin", 0))
        except Exception as e:
            logger.error("[Upstox] get_available_cash() failed: %s", e)
            return 0.0

    # ── Instrument key resolution ──────────────────────────────────────────

    def _resolve_instrument(self, symbol: str) -> Optional[str]:
        """
        Map trading symbol (e.g. "RELIANCE.NS") to Upstox instrument key.
        Uses data/instruments/nse_instruments.json.
        """
        if symbol == "Nifty 50":
            return "NSE_INDEX|Nifty 50"

        # Strip .NS suffix
        nse_symbol = symbol.replace(".NS", "").replace(".BO", "").upper()

        # Check in-memory cache first
        if nse_symbol in self._instrument_cache:
            return self._instrument_cache[nse_symbol]

        # Use InstrumentMapper to resolve
        try:
            from data.instruments.mapper import InstrumentMapper
            mapper = InstrumentMapper()
            key = mapper.get_key(nse_symbol)
            if key:
                self._instrument_cache[nse_symbol] = key
                return key
        except Exception as e:
            logger.warning("[Upstox] Failed to resolve instrument via mapper: %s", e)

        # Last resort: construct key using standard NSE format
        constructed = f"NSE_EQ|{nse_symbol}"
        logger.debug("[Upstox] Using constructed key for %s: %s", symbol, constructed)
        return constructed

    def _parse_order_response(self, data: dict) -> OrderResult:
        status_map = {
            "complete":   OrderStatus.COMPLETE,
            "rejected":   OrderStatus.REJECTED,
            "cancelled":  OrderStatus.CANCELLED,
            "open":       OrderStatus.OPEN,
            "pending":    OrderStatus.PENDING,
        }
        raw_status = data.get("status", "").lower()
        status = status_map.get(raw_status, OrderStatus.PENDING)
        side = OrderSide.BUY if data.get("transaction_type") == "BUY" else OrderSide.SELL
        return OrderResult(
            order_id=data.get("order_id", ""),
            status=status,
            symbol=data.get("tradingsymbol", "") + ".NS",
            side=side,
            requested_qty=int(data.get("quantity", 0)),
            filled_qty=int(data.get("filled_quantity", 0)),
            avg_price=float(data.get("average_price", 0)),
            rejection_reason=data.get("status_message", ""),
            raw_response=data,
        )

    @staticmethod
    def _parse_error(response) -> str:
        try:
            return response.json().get("errors", [{}])[0].get("message", response.text)
        except Exception:
            return response.text[:200]


class UpstoxAuthError(Exception):
    """Raised when Upstox credentials are missing or invalid."""
    pass
