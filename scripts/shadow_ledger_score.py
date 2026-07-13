"""
Score forward shadow ledger entries (companion to shadow_ledger.py).

Reads append-only outputs/shadow_ledger.jsonl and measures whether the recorded
RS cross-section predicted forward returns — the clean out-of-sample test that
cannot be manufactured from spent backtest data.

Run after market close once per week (or daily — cheap):
    python3 scripts/shadow_ledger_score.py
    python3 scripts/shadow_ledger_score.py --forward-days 20

Writes outputs/shadow_ledger_score.json and prints a human-readable report.
Failure criteria (Q3 #1): quintile spread <= 0 for 6 consecutive scored months.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

import numpy as np
from scipy.stats import spearmanr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LEDGER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "outputs", "shadow_ledger.jsonl",
)
OUTPUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "outputs", "shadow_ledger_score.json",
)

TOP_Q = 80.0
BOT_Q = 20.0
DEFAULT_FORWARD = 20


def load_ledger() -> list[dict]:
    if not os.path.exists(LEDGER):
        return []
    records = []
    with open(LEDGER) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return sorted(records, key=lambda r: r["date"])


def _parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _forward_close(symbol: str, entry_date: date, forward_days: int) -> float | None:
    """Fetch close at entry and at entry+forward_days trading sessions."""
    from data.fetcher import fetch_symbol

    start = entry_date - timedelta(days=10)
    end = entry_date + timedelta(days=forward_days * 2 + 30)
    df = fetch_symbol(symbol, start=start, end=end)
    if df is None or df.empty:
        return None
    if not hasattr(df.index, "date"):
        import pandas as pd
        df.index = pd.to_datetime(df.index)
    dates = sorted({d.date() if hasattr(d, "date") else d for d in df.index})
    try:
        idx = dates.index(entry_date)
    except ValueError:
        # nearest prior trading day
        prior = [d for d in dates if d <= entry_date]
        if not prior:
            return None
        idx = dates.index(prior[-1])
    target_idx = idx + forward_days
    if target_idx >= len(dates):
        return None
    target_date = dates[target_idx]
    ts = datetime.combine(target_date, datetime.min.time())
    import pandas as pd
    row = df.loc[df.index.normalize() == pd.Timestamp(target_date)]
    if row.empty:
        return None
    return float(row["close"].iloc[-1])


def score_record(record: dict, forward_days: int) -> dict | None:
    entry_date = _parse_date(record["date"])
    closes = record.get("closes", {})
    rs_ranks = record.get("rs_ranks", {})
    if not closes or not rs_ranks:
        return None

    rows = []
    for sym, metrics in rs_ranks.items():
        entry_px = closes.get(sym)
        if not entry_px or entry_px <= 0:
            continue
        rs = metrics.get("rs_rank")
        if rs is None:
            continue
        fwd_px = _forward_close(sym, entry_date, forward_days)
        if fwd_px is None or fwd_px <= 0:
            continue
        fwd_ret = (fwd_px / entry_px) - 1.0
        rows.append({"symbol": sym, "rs_rank": float(rs), "fwd_ret": fwd_ret})

    if len(rows) < 20:
        return None

    rs_arr = np.array([r["rs_rank"] for r in rows])
    ret_arr = np.array([r["fwd_ret"] for r in rows])
    rho, pval = spearmanr(rs_arr, ret_arr)

    top = [r["fwd_ret"] for r in rows if r["rs_rank"] >= TOP_Q]
    bot = [r["fwd_ret"] for r in rows if r["rs_rank"] <= BOT_Q]
    q5_q1 = (np.mean(top) - np.mean(bot)) if top and bot else np.nan

    return {
        "entry_date": record["date"],
        "regime": record.get("regime"),
        "n_scored": len(rows),
        "mean_ic": round(float(rho), 4) if not np.isnan(rho) else None,
        "ic_pvalue": round(float(pval), 4) if not np.isnan(pval) else None,
        "q5_q1_spread_pct": round(float(q5_q1) * 100, 3) if not np.isnan(q5_q1) else None,
        "top_quintile_mean_pct": round(float(np.mean(top)) * 100, 3) if top else None,
        "bottom_quintile_mean_pct": round(float(np.mean(bot)) * 100, 3) if bot else None,
        "top_quintile_positive_pct": round(100 * sum(1 for x in top if x > 0) / len(top), 1) if top else None,
        "forward_days": forward_days,
    }


def monthly_failure_check(scored: list[dict]) -> dict:
    """Q3 #1 failure: quintile spread <= 0 for 6 consecutive scored months."""
    by_month: dict[str, list[float]] = {}
    for s in scored:
        if s.get("q5_q1_spread_pct") is None:
            continue
        m = s["entry_date"][:7]
        by_month.setdefault(m, []).append(s["q5_q1_spread_pct"])

    months = sorted(by_month.keys())
    month_means = {m: float(np.mean(by_month[m])) for m in months}

    worst_streak = 0
    current_streak = 0
    for m in months:
        if month_means[m] <= 0:
            current_streak += 1
            worst_streak = max(worst_streak, current_streak)
        else:
            current_streak = 0

    return {
        "months_scored": len(months),
        "monthly_q5_q1_mean": {m: round(v, 3) for m, v in month_means.items()},
        "max_consecutive_nonpositive_months": worst_streak,
        "failure_triggered": worst_streak >= 6,
    }


def main():
    parser = argparse.ArgumentParser(description="Score forward shadow ledger entries")
    parser.add_argument("--forward-days", type=int, default=DEFAULT_FORWARD)
    args = parser.parse_args()

    records = load_ledger()
    today = date.today()
    min_age = timedelta(days=int(args.forward_days * 1.5) + 5)

    print(f"Shadow ledger: {len(records)} entries in {LEDGER}")
    if not records:
        print("No entries yet. Run: python3 scripts/shadow_ledger.py")
        return 1

    scored = []
    pending = []
    for rec in records:
        entry = _parse_date(rec["date"])
        age = today - entry
        if age < min_age:
            pending.append(rec["date"])
            continue
        result = score_record(rec, args.forward_days)
        if result:
            scored.append(result)
        else:
            pending.append(f"{rec['date']} (fetch failed or n<20)")

    report = {
        "scored_at": str(today),
        "forward_days": args.forward_days,
        "ledger_entries": len(records),
        "scored_entries": len(scored),
        "pending_entries": pending,
        "scores": scored,
        "failure_check": monthly_failure_check(scored) if scored else None,
    }

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nScored: {len(scored)} | Pending (too recent or incomplete): {len(pending)}")
    for s in scored:
        print(
            f"  {s['entry_date']}  regime={s['regime']}  n={s['n_scored']}  "
            f"IC={s['mean_ic']:+.4f}  Q5-Q1={s['q5_q1_spread_pct']:+.2f}%  "
            f"top+={s['top_quintile_positive_pct']:.0f}%"
        )

    if not scored:
        earliest = _parse_date(records[0]["date"])
        ready_by = earliest + min_age
        print(f"\nFirst scoreable date: ~{ready_by} ({args.forward_days} trading days after {earliest})")
    elif report["failure_check"]:
        fc = report["failure_check"]
        print(f"\nFailure check: max non-positive month streak = {fc['max_consecutive_nonpositive_months']}")
        if fc["failure_triggered"]:
            print("  *** FAILURE CRITERIA MET — 6+ consecutive months with Q5-Q1 <= 0 ***")
        else:
            print("  Failure criteria NOT triggered (need 6 consecutive non-positive months)")

    print(f"\nWrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())