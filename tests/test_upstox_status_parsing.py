"""Regression test for docs/54 C3 / docs/55: Upstox's "partial" order status
was silently miscategorized as PENDING (no entry in the broker's status_map),
losing the distinction between "order not yet touched" and "order partially
filled" in OrderResult.status, logs, and alerts."""
import os

os.environ.setdefault("UPSTOX_ACCESS_TOKEN", "test-token")

from broker.upstox import UpstoxBroker
from broker.base import OrderStatus


def _broker():
    return UpstoxBroker()


def test_partial_status_maps_to_partial_not_pending():
    broker = _broker()
    result = broker._parse_order_response({
        "status": "partial",
        "order_id": "OID1",
        "transaction_type": "SELL",
        "tradingsymbol": "XYZ",
        "quantity": 100,
        "filled_quantity": 40,
        "average_price": 150.0,
    })
    assert result.status == OrderStatus.PARTIAL
    assert result.filled_qty == 40


def test_complete_status_still_maps_correctly():
    broker = _broker()
    result = broker._parse_order_response({
        "status": "complete",
        "order_id": "OID2",
        "transaction_type": "BUY",
        "tradingsymbol": "XYZ",
        "quantity": 100,
        "filled_quantity": 100,
        "average_price": 150.0,
    })
    assert result.status == OrderStatus.COMPLETE
