"""
Position Reconciler — compares DB open positions vs actual Upstox holdings.

Broker-only mismatches (broker holds a symbol DB doesn't know about) are
auto-fixed into main's trading.db ONLY while
config.settings.MAIN_STRATEGY_LIVE_TRADING_ENABLED is True -- inserted via
the same origin-recovery logic daily_runner.py's sync uses, reusing
add_or_update_broker_positions() so there's one vetted implementation
instead of two that can drift apart. This was previously alert-only, which
is how the CEMPRO.NS live-buy-not-yet-persisted incident (2026-07-21) sat
undetected until caught manually the same day — see
[[cempro_orphan_position_bug_20260722]] in memory.

While main is paper-only, that flag is False and broker-only mismatches
stay alert-only instead: auto-filing a real holding into a ledger that
can never place a real matching order again would silently strand it, as
happened with GOLDBEES.NS on 2026-09-02 (see
[[goldbees_orphan_position_fold_20260907]] in memory).

DB-only mismatches (DB thinks a position is open, broker doesn't have it)
stay alert-only — auto-closing on a possibly-stale/erroring broker read
risks masking a real failed-sell that needs a human look, so that side is
intentionally NOT auto-fixed.

Cron (server — all times IST):
  # 09:20 IST Mon-Fri — after token refresh (08:30 IST), checks yesterday's positions
  20 9 * * 1-5  cd /home/ubuntu/AlgoV2 && .venv/bin/python scripts/reconcile_positions.py >> logs/reconcile.log 2>&1
"""

import sys
import logging
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("reconciler")

import requests
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MAIN_STRATEGY_PAPER_SINCE
from reconciliation.classifier import Classification, classify


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


def get_broker_positions():
    """Fetch live CNC delivery positions from Upstox via the shared broker abstraction.

    Returns list[LivePosition] on success, or None if the broker returned
    nothing at all (auth error / network issue) — caller must skip the
    mismatch check in that case rather than treat it as "broker holds 0".
    """
    from broker.upstox import UpstoxBroker

    broker = UpstoxBroker()
    positions = broker.get_positions()
    if not positions:
        logger.warning("Broker returned 0 positions — token may be expired or API down. Skipping check.")
        return None
    # Keep parity with the old conservative filter: only CNC delivery counts here.
    return [p for p in positions if p.product == "CNC"]


def _manual_evidence_for_symbol(symbol: str) -> bool:
    """True if trading.db has a trade record for `symbol` whose exit_reason
    names a manual broker-side action (e.g. MANUAL_LIQUIDATION_PRE_PAPER_SWITCH)
    -- the evidence gate that separates MANUAL_BROKER_POSITION (explained,
    UNSAFE-tier not auto-repaired) from QUANTITY_MISMATCH (unexplained)."""
    from db.repository import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM trades WHERE symbol = ? AND exit_reason LIKE '%MANUAL%' LIMIT 1",
            (symbol,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _prior_record_exists_for_symbol(symbol: str) -> bool:
    """True if trading.db has ANY record (any status) for `symbol` -- lets a
    broker-only symbol classify as BROKER_ONLY_POSITION (resolvable, mirrors
    daily_runner's origin-recovery heuristic) instead of UNKNOWN_POSITION."""
    from db.repository import get_connection
    conn = get_connection()
    try:
        row = conn.execute("SELECT 1 FROM positions WHERE symbol = ? LIMIT 1", (symbol,)).fetchone()
        return row is not None
    finally:
        conn.close()


def log_classifications(now_str: str, broker_positions, db_positions, atr_positions) -> None:
    """Read-only, alert-log-only -- never touches trading.db/momentum_atr.db.
    Shares reconciliation/classifier.py with scripts/observability_snapshot.py
    so both scripts agree on what a mismatch means. Failure here must never
    block the reconciler's real ghost/unknown handling above, so callers
    catch broadly and treat this as best-effort."""
    from db import reporting_repo as rrepo

    # MAIN positions opened on/after MAIN_STRATEGY_PAPER_SINCE are simulated
    # -- never reached the broker -- and must not count toward ownership
    # math, or a paper entry falsely reads as DUPLICATE_OWNERSHIP against a
    # real momentum_atr holding that already matches the broker exactly
    # (GOLDBEES.NS). A position dated before the cutover stays counted
    # regardless of MAIN's current live-trading flag (ASIANENE.NS, manual
    # entry pre-cutover, still real) -- per-position date filter, not a
    # flag check. See the identical filter + rationale in
    # scripts/observability_snapshot.py::snapshot_positions().
    main_qty, main_paper_qty, atr_qty, broker_qty = {}, {}, {}, {}
    for p in db_positions:
        if p.entry_date >= MAIN_STRATEGY_PAPER_SINCE:
            main_paper_qty[p.symbol] = main_paper_qty.get(p.symbol, 0) + p.shares
        else:
            main_qty[p.symbol] = main_qty.get(p.symbol, 0) + p.shares
    for p in atr_positions:
        atr_qty[p.symbol] = atr_qty.get(p.symbol, 0) + p.shares
    for p in broker_positions:
        broker_qty[p.symbol] = broker_qty.get(p.symbol, 0) + p.quantity

    all_symbols = set(main_qty) | set(atr_qty) | set(broker_qty)
    rrepo.init_db()
    ts = date.today().isoformat()
    non_match = []
    for sym in sorted(all_symbols):
        m, a, b = main_qty.get(sym, 0), atr_qty.get(sym, 0), broker_qty.get(sym, 0)
        evidence = {
            "manual_evidence": _manual_evidence_for_symbol(sym),
            "prior_record_exists": _prior_record_exists_for_symbol(sym),
        }
        if sym in main_paper_qty:
            evidence["main_paper_qty"] = main_paper_qty[sym]
        cls, ev = classify(sym, m, a, b, evidence)
        if cls != Classification.MATCH:
            non_match.append((sym, m, a, b, cls, ev))

    if non_match:
        detail = "; ".join(f"{s}: main={m} atr={a} broker={b} -> {c.value}" for s, m, a, b, c, _ in non_match)
        rrepo.record_reconciliation(ts, "position_classification", "FAIL", detail=detail)
    else:
        rrepo.record_reconciliation(ts, "position_classification", "PASS")


def run_reconcile():
    now_str = date.today().strftime("%d %b %Y")
    logger.info("Starting reconciliation — %s", now_str)

    try:
        broker_positions = get_broker_positions()
    except Exception as e:
        _send(f"⚠️ <b>Reconciler Error</b>\n{e}\n<i>{now_str}</i>")
        sys.exit(1)

    if broker_positions is None:
        print(f"[{now_str}] SKIP — API unavailable, token may be expired.")
        return

    from db.repository import load_positions

    db_positions = load_positions("OPEN")
    from db import momentum_atr_repo
    try:
        atr_positions = momentum_atr_repo.load_positions("OPEN")
    except Exception as e:
        logger.warning("Could not load momentum_atr positions for classification: %s", e)
        atr_positions = []

    try:
        log_classifications(now_str, broker_positions, db_positions, atr_positions)
    except Exception as e:
        logger.error("Classification logging failed (non-fatal, does not affect reconciliation): %s", e)

    broker_syms = {p.symbol for p in broker_positions}
    db_syms = {p.symbol for p in db_positions}
    momentum_atr_syms = {p.symbol for p in atr_positions}

    ghost = db_syms - broker_syms                            # DB open, broker doesn't have it — alert only
    # momentum_atr_syms excluded so a symbol only momentum_atr bought never
    # looks like a "broker-only unknown position" and gets auto-inserted into
    # MAIN's trading.db -- corrupting it, not just false-alerting (shared-
    # broker fungibility, see CYIENT incident 2026-09).
    unknown = broker_syms - db_syms - momentum_atr_syms       # Broker holds, neither ledger knows — auto-fixed below

    logger.info("DB open: %s", db_syms)
    logger.info("momentum_atr open: %s", momentum_atr_syms)
    logger.info("Broker holds: %s", broker_syms)
    logger.info("Ghost (DB-only): %s", ghost)
    logger.info("Unknown (broker-only): %s", unknown)

    if not ghost and not unknown:
        logger.info("Reconciliation OK — DB and broker match.")
        print(f"[{now_str}] OK — {len(db_syms)} positions match.")
        return

    fixed = []
    fix_failed = []
    if unknown:
        from config.settings import MAIN_STRATEGY_LIVE_TRADING_ENABLED

        if not MAIN_STRATEGY_LIVE_TRADING_ENABLED:
            # Main strategy is paper-only -- its trading.db ledger no longer
            # corresponds to a strategy that places real orders. Auto-filing
            # a broker-only holding there would silently strand it exactly
            # like the 2026-09-02 GOLDBEES.NS incident (main bought it live,
            # then flipped to paper the next day; the real position sat
            # untracked by any live strategy for 5 days before manual fold-in
            # into momentum_atr). Alert-only until a human assigns ownership.
            fix_failed = sorted(unknown)
        else:
            from runner.daily_runner import add_or_update_broker_positions

            db_positions = {p.symbol: p for p in load_positions(status="OPEN")}
            unknown_positions = [p for p in broker_positions if p.symbol in unknown]
            try:
                add_or_update_broker_positions(date.today(), unknown_positions, db_positions)
                # Verify it actually landed before calling it fixed.
                still_missing = unknown - {p.symbol for p in load_positions(status="OPEN")}
                fixed = sorted(unknown - still_missing)
                fix_failed = sorted(still_missing)
            except Exception as e:
                logger.error("Auto-fix failed: %s", e)
                fix_failed = sorted(unknown)

    lines = [f"⚠️ <b>Position Mismatch — {now_str}</b>"]

    if ghost:
        lines.append(
            "\n🔴 <b>DB OPEN but broker has NO position:</b>\n"
            + "\n".join(f"  • {s}" for s in sorted(ghost))
            + "\n<i>Sell order may have failed — check manually.</i>"
        )

    if fixed:
        lines.append(
            "\n🟢 <b>Broker-only positions auto-recorded to DB:</b>\n"
            + "\n".join(f"  • {s}" for s in fixed)
            + "\n<i>Origin classified strategy/manual per prior-record heuristic — verify.</i>"
        )

    if fix_failed:
        lines.append(
            "\n🟡 <b>Broker holds but auto-fix failed, DB still has no record:</b>\n"
            + "\n".join(f"  • {s}" for s in fix_failed)
            + "\n<i>Needs manual insert — check logs/reconcile.log.</i>"
        )

    _send("\n".join(lines))
    logger.warning("Mismatch detected — %d auto-fixed, %d ghost, %d fix-failed.",
                    len(fixed), len(ghost), len(fix_failed))
    if ghost or fix_failed:
        sys.exit(2)


if __name__ == "__main__":
    run_reconcile()
