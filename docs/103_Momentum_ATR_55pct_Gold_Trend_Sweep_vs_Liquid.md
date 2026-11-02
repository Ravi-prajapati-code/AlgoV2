# 103 — 55% equity cap: GOLDBEES trend sweep vs liquid fund (2026-11-02)

**Verdict: REJECT as a replacement for the liquid/cash reserve.** Gold adds return only in windows
that contain the 2025 gold rally (+72%). On the 2016–22 windows it loses to the liquid fund, and its
drawdown is equal or worse. The trend filter costs more than it saves: always-on gold beats
trend-gold on CAGR in 4 of 5 windows.

## Proposal tested (user, 2026-11-02)
- Active equity is capped at 55% of total account equity × dd_scale (live config, 5 names).
- All idle cash is swept into GOLDBEES.NS while its close is above both SMA50 and SMA200.
- Below either SMA, gold is sold to cash (GOLD_TREND_EXIT).
- An equity buy that needs more than the free rupee cash sells gold first (GOLD_SWEEP_TRIM_FOR_EQUITY).
- GOLDBEES never takes one of the 5 equity slots.

## Setup
- Script: `~/Workspace/momentum_atr_standalone/research_gold.py`. It runs the real `matr` engine through the SimBroker and leaves live code unchanged.
- Raw output: `research_mission/gold/all.txt` and `table.txt`.
- Gold sits outside the ledger:
  - `get_holdings()` hides gold.
  - `get_available_cash()` reports cash plus gold value, so total equity and the 55% cap behave as live does.
  - A trim fires inside `place_order` when an equity buy is short of cash.
- Timing: the signal uses the prior close and fills at the open. A re-sweep happens only when idle cash is at least 2% of equity. A ₹500 buffer is kept.
- Gold starts only after the first strategy trade (this avoids bootstrap adoption).
- Charges use the engine's equity-delivery model. This **overstates** gold costs, since a gold ETF pays no STT. See the caveats.
- Arms:
  - `off`: 55% with cash.
  - `liquid`: 6%/yr on all idle cash, the same accrual as docs/100 addendum 2.
  - `trend`: the proposal.
  - `always`: gold held with no filter.
- GOLDBEES is excluded from the equity ranking in every arm.
- Capital ₹1L. FULL_OLD and FULL_ALL actually start 2021-05 because of the 50%-coverage day skip (docs/99).

**Data bug found:** GOLDBEES opens in `bt_data.db` are corrupt from 2019-12 to 2022-10. There are about 40 fake open gaps of ±10–15% while the closes are clean. The first run showed U2015_OLD trend at −58.6% MaxDD because of them. Fix: a gold fill uses the open only if it is within 3% of the prior close, and the prior close otherwise. All numbers below are after the fix.

## Results — CAGR / MaxDD (Sharpe)
| Window | 55% cash | 55% liquid 6% | 55% gold trend | 55% gold always |
|---|---|---|---|---|
| FULL_NEW 22-11..26-10 | 43.3 / −18.6 (1.84) | 44.0 / −18.0 (1.93) | **58.0 / −17.6 (2.18)** | 61.9 / −22.3 (2.23) |
| FULL_OLD 21-05..22-10 | 22.1 / −18.8 (1.42) | **29.8 / −17.8 (1.77)** | 23.7 / −18.1 (1.44) | 28.0 / −15.7 (1.57) |
| U2015_NEW | −3.2 / −23.1 | **1.5 / −16.5** | 10.7 / −21.9 | 15.2 / −21.0 |
| U2015_OLD 16..22-10 | −3.9 / −34.9 | **1.6 / −20.5** | −4.7 / −37.3 | 3.0 / −28.8 |
| FULL_ALL 21-05..26-10 | 33.4 / −18.8 (1.69) | 47.9 / −17.8 (2.02) | 54.2 / −18.1 (2.09) | 66.3 / −23.1 (2.22) |

Trend-arm activity:
| Window | Days in gold | Trend exits | Gold charges |
|---|---|---|---|
| FULL_NEW | 73% | 29 | ₹14.3k |
| U2015_OLD | 49% | 60 | ₹18.2k |
| FULL_ALL | 64% | 46 | ₹24.3k |

FULL_NEW annual returns, trend vs liquid:
| Year | Trend | Liquid |
|---|---|---|
| 2023 | 67.2 | 72.8 |
| 2024 | 48.9 | 51.1 |
| **2025** | **45.8** | **3.2** |
| 2026 | 51.6 | 45.0 |

GOLDBEES calendar-year returns:
| Year | Return |
|---|---|
| 2016 | +12.5 |
| 2017 | +2.3 |
| 2018 | +6.7 |
| 2019 | +23.0 |
| 2020 | +27.0 |
| 2021 | −5.2 |
| 2022 | +12.8 |
| 2023 | +14.7 |
| 2024 | +19.4 |
| 2025 | **+71.8** |
| 2026 YTD | +11.5 |

## Findings
1. **The FULL_NEW win is one year.** Almost all of trend's +14-point edge over liquid comes from 2025. That year gold returned +72% and equity was flat (liquid arm +3.2%). In 2023–24 liquid is ahead.
2. **The 2016–22 windows favour liquid.**
   - U2015_OLD: trend −4.7 / −37.3 against liquid +1.6 / −20.5.
   - FULL_OLD: trend 23.7 against liquid 29.8.
   - Gold rose 23–27% in 2019–20 there. The trend filter still whipsawed: 60 exits and ₹18k in charges on ₹1L.
3. **Gold does not cut drawdown.** MaxDD is no better than liquid in any window, and it is worse on both U2015 windows. Momentum drawdowns and gold drawdowns are not reliably offset. Gold is a second return source (gold beta), not protection.
4. **The trend filter fails on its own terms.** Always-on gold beats trend-gold on CAGR in 4 of 5 windows. Trend only shaves MaxDD on the windows that include 2025.
5. **This agrees with docs/100.** EXP-28's gold sweep was found to be return only, −1.2% in 2021–22, and v2 at 55% plus gold beat it. This test reaches the same conclusion with the user's exact rule set on the live engine.

## Caveats
- Gold charges are overstated (no STT on gold ETFs). Taking U2015_OLD trend charges to zero adds about 2.5%/yr, to roughly −2%, which is still below liquid. FULL_NEW trend would rise by about 2–3 points.
- 6% is an assumed liquid yield. docs/100 measured 4% and 7%, and 4% would cut about 0.5–1 point off the liquid arms.
- The engine is path-noisy: gaps under about 10 points are not meaningful (docs/100, finding 4).
- The GOLDBEES fill-price fix uses the prior close on bad-open days. It is fair to both sides, but it is still an approximation.

## Decision
Keep the reserve in LIQUIDBEES or cash, as deployed on 2026-11-02 (allocation 0.55). A gold reserve is a separate bet on the gold price, not a stability improvement. If wanted at all, plain always-on gold is the better form, and it raises drawdown. A live gold sleeve would also add order-sync complexity (trim before buy, a second position outside the ledger), the same failure class that caused the 2026-11-02 missed buys.
