# 88 — momentum_atr v2: 5 names / keep-rank 10, kill switch off, bug fixes

Date: 2026-10-14. Status: **deployed live at the user's explicit request**
("Bug fixes + improved strategy", kill switch "Off", "Commit + deploy").
Source of the changes: an externally "improved" copy of the standalone
package (`~/Workspace/momentum_atr_standalone_improved`), reviewed and
re-tested here before porting.

## What changed

| | v1 (until 2026-10-14) | v2 |
|---|---|---|
| Names held | 3 | `MOMENTUM_ATR_PORTFOLIO_SIZE=5` |
| Rank exit | rank > 3 | rank > `MOMENTUM_ATR_KEEP_RANK=10` (still one exit per day) |
| Cash after a rank exit | split across current top 3 | new names into empty slots only (`MOMENTUM_ATR_REALLOC_NEW_SLOTS_ONLY`) |
| −3%/+3% swap | on | off (`MOMENTUM_ATR_USE_SWAP_RULE=false`); if on, never into a winner outside keep-rank |
| Drawdown | kill switch 35%, never self-clears in practice | exposure scaling: cap × (1 − 3×(DD − 15%)), floor 25%; kill switch **off** (`MOMENTUM_ATR_DD_KILL_PCT=0`), if set it resets after 20 days fully in cash |

All settings are env-overridable in `config/settings.py`; v1 =
`PORTFOLIO_SIZE=3 KEEP_RANK=3 USE_SWAP_RULE=true REALLOC_NEW_SLOTS_ONLY=false
DD_SCALE_ENABLED=false DD_KILL_PCT=0.35`.

## Bug fixes

1. **Same-day trades invisible in Upstox holdings.** `long-term-holdings`
   changes only at settlement. Verified live 2026-10-14 after the 09:17 run
   (sold 7 MOREPENLAB, bought 2 SHANTIGOLD): holdings read MOREPENLAB 7 /
   SHANTIGOLD 166, `short-term-positions` read −7 / +2. Effects: a stock
   bought today could not be sold today (pre-sell check skipped it), fill
   self-heal could never confirm a buy, account equity was understated on
   buy days. New `UpstoxBroker.get_net_holdings_or_none()` nets both, both
   signs; used only by momentum_atr. MAIN/reconciler still use
   `get_holdings()` unchanged. The improved copy netted only positive
   quantities and would have kept a same-day sale looking held — fixed here.
2. **Failed holdings read counted as zero.** `get_holdings()` returns `[]`
   on API failure; self-heal could read that as "0 left" and confirm a SELL
   that never happened. Now `None` → no self-heal, sell pre-check fails closed.
3. **Top-up gave a position a new id.** `INSERT OR REPLACE` re-created the
   row, changing which holding the one-per-day rank exit sells first. Now an
   in-place `UPDATE`; a re-bought symbol still gets a fresh id.
4. **Trade P&L buy charges** were recomputed as one buy at the averaged
   price; each add actually pays its own Rs.30 minimum. Now `buy_charges_paid`
   is accumulated per buy (schema column added, migrated by `init_db`).
   Reporting only — cash was already correct.
5. **Swap winner = same-day rank exit** sold the stale pre-swap quantity,
   leaving the added shares untracked (found by the standalone replay: 45
   broker/ledger mismatch days). Now re-read from the DB, and the swap no
   longer buys a winner outside keep-rank. Never occurred live.
6. **Peak equity** also updated at end of run; **dry run** no longer
   writes the kill-switch peak.

## Evidence (standalone replay of the package's own code, 153 list, ₹1L)

| | CAGR | MDD | Calmar |
|---|---|---|---|
| v1, kill off, 2022-11-01 → 2026-10-09 | 78.0% | −39.2% | 1.99 |
| v1, kill 35% | 10.7% | −36.0% | 0.30 (tripped in 2024, 0% in 2025-26) |
| **v2** | **88.5%** | **−27.2%** | 3.26 |
| v2 N=4 / N=6 (keep 10) | 58.9% / 54.7% | −29.2% / −27.9% | 2.01 / 1.96 |
| v2 keep 8 / keep 12 (N=5) | 44.3% / 54.2% | −28.7% / −30.6% | 1.55 / 1.77 |
| v2 without DD scaling | 93.6% | −30.4% | 3.08 |
| TRAIN → 2025-04-15: v1 / v2 | 49.1% / 84.4% | −39.2% / −27.2% | |
| TEST 2025-04-16 →: v1 / v2 | 118.1% / 81.0% | −34.3% / −20.3% | 3.44 / 4.00 |

Broker/ledger mismatch days: 0 in every run. The improved package's fast
ranking matched `build_ranking_inputs` top-10 on 34/34 sampled dates.

## Honest read

- **88.5% is a parameter spike**, the same jagged profile docs/74 found
  (best in-sample arm lost walk-forward 5/5). One-step neighbours give
  44–59% — *below* v1's 78%. Expect **~45–60% CAGR**, not 88%.
- **The shallower drawdown is the robust part:** −28…−31% in every
  neighbour vs −39% for v1.
- Each v2 ingredient alone was previously rejected (C_TOP5 docs/73,
  X_RANK10 docs/74, B_SWAP_OFF docs/73) under the research engine; the
  combination is new and was not pre-registered.
- In TEST, v2 earned less than v1 (81% vs 118%) with less drawdown. Whether
  the improved copy's author saw that period when picking 5/10 is unknown.
- All numbers use the hindsight-biased 153 list (docs/86: rule-based
  universe ≈ 43% / −43%).
- The live account is shared with MAIN via the allocation cap; the replay
  models a dedicated account.

Rejection criteria for v2 live: judge after ≥30 closed trades against the
v1 B2/LIVE shadow books (docs/82); revert if live drawdown exceeds −35%
or realized PF < 1.0.

## Revert

Server `.env`: `MOMENTUM_ATR_PORTFOLIO_SIZE=3 MOMENTUM_ATR_KEEP_RANK=3
MOMENTUM_ATR_USE_SWAP_RULE=true MOMENTUM_ATR_REALLOC_NEW_SLOTS_ONLY=false
MOMENTUM_ATR_DD_SCALE_ENABLED=false MOMENTUM_ATR_DD_KILL_PCT=0.35`
(bug fixes stay), or `git revert` the v2 commit.
