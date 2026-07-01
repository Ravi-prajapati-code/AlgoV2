"""
GTT price-consistency audit.

Complements gtt_coverage.py (which only checks a GTT *exists*). This checks
that each open position's live broker GTT trigger actually matches what the
system believes the stop should be — catching the class of bug found on
2026-07-01 where update_trailing_stop() ratcheted a position's stop and
correctly updated the broker GTT, but never saved the new value to the DB
(fixed in portfolio/manager.py commit c5460f6). If that kind of persistence
gap ever recurs, or a manual/out-of-band GTT change drifts from the DB, this
catches it.

Also flags:
  - NAKED    — no active GTT at all (same check as gtt_coverage.py, repeated
               here for a single combined report)
  - DUPLICATE — more than one active GTT for the same symbol (the CGPOWER
               cancel-endpoint incident from 2026-07-01)
  - MISMATCH — exactly one active GTT, but its trigger price doesn't match
               the expected floor (DB trailing_stop/stop_loss for normal
               positions, or the static 7%-from-entry floor for GOLDBEES)

Run standalone, ad hoc:
  .venv/bin/python monitoring/gtt_price_audit.py

Exit codes: 0 = all consistent, 2 = one or more issues found,
1 = could not audit (API/token failure — no alert raised, just logged).
"""

import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("GTTPriceAudit")

TOLERANCE = 0.10  # rupees — allows for tick rounding drift


def _active_gtts_by_key(broker) -> dict:
    """instrument_key -> list of (gtt_id, trigger_price) for currently active GTTs."""
    resp = broker._session.get(
        f"{broker._base_url.replace('/v2', '/v3')}/order/gtt",
        headers=broker._headers,
        timeout=10,
    )
    resp.raise_for_status()
    orders = resp.json().get("data", []) or []
    by_key = {}
    for o in orders:
        key = o.get("instrument_token") or o.get("instrument_key", "")
        if not key:
            continue
        for r in o.get("rules", []):
            status = (r.get("status") or "").upper()
            if status in {"PENDING", "OPEN", "ACTIVE", "CREATED", "SCHEDULED"}:
                oid = str(o.get("id") or o.get("gtt_order_id", ""))
                trigger = r.get("trigger_price")
                by_key.setdefault(key, []).append((oid, trigger))
    return by_key


def _expected_price(pos, entry_price: float) -> float:
    from config.settings import SAFE_HAVEN_SYMBOL, GOLDBEES_MAX_LOSS_PCT, round_to_tick
    if pos.symbol == SAFE_HAVEN_SYMBOL:
        return round_to_tick(entry_price * (1 - GOLDBEES_MAX_LOSS_PCT))
    return pos.trailing_stop if pos.trailing_stop and pos.trailing_stop > 0 else pos.stop_loss


def check() -> int:
    from broker.upstox import UpstoxBroker
    from db.repository import load_positions
    from monitoring.gtt_coverage import _excluded_symbols
    from config.settings import SAFE_HAVEN_SYMBOL

    try:
        broker = UpstoxBroker()
    except Exception as e:
        logger.error("Could not init Upstox broker (token?): %s — skipping audit.", e)
        return 1

    positions = load_positions(status="OPEN")
    if not positions:
        logger.info("No open positions — nothing to audit.")
        return 0

    try:
        gtts_by_key = _active_gtts_by_key(broker)
    except Exception as e:
        logger.error("Could not fetch GTT list — skipping audit to avoid false alerts: %s", e)
        return 1

    # GOLDBEES DOES carry a GTT (a static max-loss floor, see _expected_price) — unlike
    # gtt_coverage.py's naked-position check, don't skip it here just because it's "defensive".
    excluded = _excluded_symbols() - {SAFE_HAVEN_SYMBOL}
    issues = []
    checked = 0

    for pos in positions:
        if pos.symbol in excluded or pos.shares <= 0:
            continue
        checked += 1
        key = broker._resolve_instrument(pos.symbol)
        gtts = gtts_by_key.get(key, [])

        if len(gtts) == 0:
            issues.append(("NAKED", pos.symbol, None, None))
            continue
        if len(gtts) > 1:
            triggers = ", ".join(f"{gid}@{tp}" for gid, tp in gtts)
            issues.append(("DUPLICATE", pos.symbol, None, triggers))
            continue

        gtt_id, actual = gtts[0]
        expected = _expected_price(pos, pos.entry_price)
        if actual is None or expected is None:
            continue
        if abs(actual - expected) > TOLERANCE:
            issues.append(("MISMATCH", pos.symbol, expected, actual))

    if not issues:
        logger.info("GTT price audit OK — %d position(s), all consistent with DB.", checked)
        return 0

    lines = []
    for kind, symbol, expected, actual in issues:
        if kind == "NAKED":
            lines.append(f"  NAKED     {symbol} — no active GTT")
        elif kind == "DUPLICATE":
            lines.append(f"  DUPLICATE {symbol} — multiple active GTTs: {actual}")
        else:
            lines.append(f"  MISMATCH  {symbol} — DB expects ₹{expected:.2f}, broker has ₹{actual:.2f}")

    msg = "GTT price audit found issues:\n" + "\n".join(lines)
    logger.warning(msg)
    try:
        from notifications.telegram import send_message
        send_message(f"⚠️ *GTT Price Audit*\n" + "\n".join(lines))
    except Exception as e:
        logger.error("Failed to send audit alert: %s", e)
    return 2


if __name__ == "__main__":
    sys.exit(check())
