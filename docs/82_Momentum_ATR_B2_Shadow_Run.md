# 82 — momentum_atr: B2 Shadow (Paper) Run

Date: 2026-10-12 · Status: built and deployed · Code:
`momentum_atr/shadow.py`, `scripts/run_momentum_atr_shadow.py` · Tests:
`tests/test_momentum_atr_shadow.py`.

## Purpose

docs/80's B2 score (mom × Close/SMA50 × ATR%) beat the live score on the
2022-25 data but was slightly behind it in TEST (113% vs 119%). The open
question is whether it is better on data neither score has seen. The
shadow run answers it without risking money.

## What it does

- Two virtual books, ₹1,00,000 each, 0.15%/side cost:
  - **LIVE**: the live score (momentum × ATR%, with SMA50 + SMA200
    eligibility).
  - **B2**: momentum × (Close/SMA50) × ATR%, with the same eligibility.
- Both use identical, live-equivalent rules: top 3, rank exit (one per
  day), ±3% swap, top-up, re-entry, and an equal split with a leftover
  cascade.
- Decisions on close d fill at the open of d+1.
- **Paper only.** No broker object, no orders, no writes to the live
  momentum_atr DB. A test asserts that neither file imports broker, db,
  portfolio, notifications, or momentum_atr.execution.
- Runs after market close (cron 16:45 IST, own lock, 30-minute timeout),
  so it can never delay the 08:50 precompute or the 09:17 live run.
- **Data gaps:** if a held symbol has no bar for a session (Upstox fetch
  stall), that session is deferred and retried next run. It is never
  treated as an exit.
- Re-running over the same days changes nothing. State is written
  atomically.
- Output in `data/shadow/momentum_atr/` (gitignored):
  - `LIVE.json`, `B2.json`: book state
  - `trades.csv`: closed trades in `ledger.py` format, sources
    `shadow_LIVE` and `shadow_B2`
  - `equity.csv`: daily equity per book
- First session: **2026-10-13**.

## Verification

- The LIVE book's score equals `momentum_atr/scoring.py` exactly:
  same values, same ranking.
- Both books reproduce the research engine (`audit_sim`) **trade for
  trade** over the full 2022-11 → 2026-05 backtest (dates, symbols,
  shares, reasons), and final equity to the paisa.
- Further tests: next-open timing, the rank-exit date, deferral on
  missing bars, and an idempotent re-run.

## How to judge it (fixed now, before any shadow data exists)

Compare the two books only after **both have ≥ 30 closed trades**. Use
`research/momentum_atr_capital_protection/ledger.py` to load `trades.csv`
and compare equity curves. B2 would justify a live switch only if it
beats LIVE on return **and** max drawdown over that period. A
difference within normal noise (the ledger's bootstrap CI includes 0)
means "no evidence"; in that case keep LIVE. At ~1–2 closed trades per
book per week, expect ~3–6 months to reach 30.
