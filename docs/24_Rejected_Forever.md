# 24. Rejected Forever

Every lever in this document was tested empirically (backtest,
attribution analysis, or `robustness_gate.py`'s full-window/train/test/
stress standard) and failed to earn its place. Do not re-implement or
re-propose any of these without genuinely new data — a different
dataset, a different market regime playing out live, or a materially
different formulation of the idea (not just a re-run of the same test).

If a future session is tempted to try one of these again: read the
linked section first. Re-deriving a known-negative result wastes a
robustness-gate run and re-litigates a closed question.

## Entry / ranking

| Lever | Verdict | Evidence |
|---|---|---|
| Complex multi-factor ranking (freshness + institutional volume + sector-leadership + sector-tailwind) | REJECTED — null | `|rho| < 0.06`, n=8,633. No single factor explains win vs. loss. [[signal_mechanism_analysis_20260710]] |
| Freshness ranking | REJECTED — null | Same suite, no signal. |
| ADX ranking (as a sort key, not the existing threshold gate) | REJECTED — null | Same suite. Note: ADX as a pass/fail *threshold* gate is proven and kept — see §XXV in `docs/23_Assumption_Audit.md`. Ranking by ADX magnitude is the rejected idea, not the gate itself. |
| Turnover ranking | REJECTED — null | Same suite. Note: turnover as a pass/fail *floor* gate is a separate, still-live check (never fires, kept as a live safety net — §XXIV). |
| Extension ranking | REJECTED — null | Same suite. |
| Weighted composite scores | REJECTED — never beat plain RS-rank | `entry_attribution_suite_20260709`: REVERSE_RS (worst-first) beat RS-rank ranking; ranking itself adds no value at the point of entry. |
| Sector blacklist (`SECTOR_BLACKLIST`) | REJECTED — overfit | §XXIII, Experiment A. Code deleted 2026-07-10, commit `54afde3`. |
| Streak-priority buy ordering (`STREAK_PRIORITY_ENABLED`) | NOT DEPLOYED — ambiguous, not proven | §XXIII, Experiment B. Mechanical PASS but train regresses while test improves — a genuine trade-off, not a clean win. Code deleted 2026-07-10 alongside the sector blacklist (never reached "proven," so didn't meet the bar to keep either). If revisited, this is the one item here that isn't a hard reject — it's an open judgment call that was resolved by *not* shipping it, not by disproving it. |
| Dynamic replacement (RS/volume/sector rotation triggers for replacing a weak holding) | REJECTED — no live trigger works | [[rotation_logic_synthesis_20260710]]: replacing a weak holding is correct in hindsight, but RS-rank, volume, and sector strength all fail as a real-time trigger. Built, tested, and explained — not an open question. |

## What is NOT on this list (proven, keep)

- Relative Strength gate, Trend Alignment (EMA), SuperTrend, ADX
  (as a threshold gate), Breakout — the baseline entry gate. Frozen.
- Liquidity/turnover floor — never fires in backtest, kept anyway as a
  live operational safety net (protects against risk a backtest can't
  model, not a proven backtest edge). See §XXIV / §XXVII.
- Exit chain: trend break, momentum decay, regime-flip bear protection.
  No complex-exit variant has ever beaten this. Frozen.
- Drawdown-based position sizing, regime filter. Justified, keep.

## How to apply

Before proposing any new ranking scheme, replacement trigger, or
weighting formula for entries: check this table first. If it's a
variant of something here (e.g. "rank by ADX *and* turnover together"),
treat it as the same rejected idea unless there's a specific reason to
believe combining previously-null factors produces a non-null result —
that reason needs to be stated up front, not discovered after another
robustness-gate run.
