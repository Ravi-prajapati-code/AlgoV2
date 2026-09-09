# 68 — Kill-Switch Drawdown Threshold Raised 25% → 35% (2026-09-09)

## Change

`config/settings.py::MOMENTUM_ATR_DD_KILL_PCT` default changed from `0.25`
to `0.35`. No `.env` override existed for this on the live server, so this
code-level default change is what actually governs live behavior — a
deliberate choice over adding a silent `.env` override, so the threshold is
git-tracked and auditable rather than living only in server config.

`momentum_atr/risk.py::check_kill_switch()` itself is unchanged — it still
trips when `drawdown_pct >= MOMENTUM_ATR_DD_KILL_PCT * 100`, manual-clear
only, same alerting behavior. Only the threshold value moved.

## Why this was asked for

User explicitly requested raising the threshold after the 2026-09-09
kill-switch trip (see docs/67) blocked new BUYs. Before changing anything,
the user was shown the tradeoff directly and asked to choose the scope and
the exact number — this was not a default I picked.

## Context the user was given before choosing

`risk.py`'s own docstring states 25% was **deliberately conservative** —
chosen specifically because momentum_atr is a first live run of an unproven
strategy. Raising the threshold loosens that original conservative design
intent. This is noted here for the record, not as an objection being
re-litigated — the user made an informed choice after being shown this.

For scale: today's real drawdown that tripped the switch was 44.58%. 35%
would still have tripped on today's number — it is not a threshold picked
to avoid today's specific trip, it's a genuine (if looser) risk control.

## What was NOT changed

- The kill switch mechanism itself, its manual-clear-only behavior, or its
  Telegram alerting.
- `MOMENTUM_ATR_CAPITAL_ALLOCATION_PCT` (still 100%, unrelated).
- Nothing about the currently-cleared `kill_switch_tripped` state (docs/67)
  — this changes the threshold for future evaluations only.

## Verification

`tests/test_momentum_atr_execution.py`: 18 passed, same 2 pre-existing
unrelated failures (scoring-formula test fixture missing a `volume` column
— unrelated local in-progress experiment work, not caused by this change).
No test hardcoded the old 25% value.
