# AlgoV2 / AlgoV3 — Full Chat Context Handoff

**Date:** 2026-07-08  
**Purpose:** Paste this into Claude (or any agent) to continue work with full context from this session.

---

## 1. User Goal Arc (what we were doing)

1. Run Q3 #1 top experiment — forward shadow ledger on AlgoV2
2. Explain paradox: RS signal is real but portfolio fails vs Midcap 150
3. Design Strategy V2 (Chief Quant Architect spec) — core-satellite, not standalone Midcap-beater
4. Build V2 as **AlgoV3** at `/home/ravi.prajapati@brainvire.com/Workspace/AlgoV3`
5. Run `main.py daily --live`
6. Diagnose why **zero qualified signals today** (2026-07-08)
7. Historical frequency of non-zero qualifiers
8. Qualifiers on monthly rebalance days
9. Which rebalance days had zero qualifiers
10. Queue depth before zero rebalance days
11. **What was in the inter-rebalance queue on the 8 BULL zero-rebalance days** ← main analytical focus
12. Gate-failure analysis: why queued names didn't re-qualify on rebalance morning

---

## 2. Repository Layout

| Repo | Path | Role |
|------|------|------|
| **AlgoV2 (V1 frozen)** | `/home/ravi.prajapati@brainvire.com/Workspace/AlgoV2` | Backtest engine, live runner, signal/exit logic, DB |
| **AlgoV3 (V2 platform)** | `/home/ravi.prajapati@brainvire.com/Workspace/AlgoV3` | Core-satellite deployment; bridges to V1 via `bridge/v1.py` |

**Important:** AlgoV3 uses `v3config/` (not `config/`) to avoid package collision with AlgoV2.  
**DB override:** `DB_PATH_OVERRIDE=/home/ravi.prajapati@brainvire.com/Workspace/AlgoV2/db/trading.db`

---

## 3. Central Research Conclusion (Phase 1 — accepted as correct)

**Paradox:** RS ranking carries genuine selection information but portfolio construction destroys it.

| Stage | CAGR | Notes |
|-------|------|-------|
| Nifty Midcap 150 ETF | +21.05% | Natural benchmark |
| Equal-weight own 100-symbol universe | +17.20% | -3.85pp universe composition |
| Strategy with **random** RS permutation (mean of 40) | +2.90% | Permutation p=0.024 |
| Actual strategy | +12.49% | ~+9.6pp/yr inside wrapper |
| vs Midcap 150 | -5.07pp | IR -0.26, factor t=0.71 |

**V2 mandate (frozen):**

- Core-satellite component (70/30 default), NOT standalone Midcap-beater
- N=4..20 rejected; no parameter optimization in V2
- Exits/stops **cleared and frozen** (doc 20 audit)
- Portfolio construction is the leak, not the signal

**Key docs in AlgoV2:**

- `docs/18_Alpha_Leakage_Report.md`
- `docs/20_Portfolio_Construction_Audit.md`
- `docs/19_Leakage_Experiments.md`
- `docs/21_Research_Priorities.md`

---

## 4. AlgoV3 Architecture (built this session)

**Config:** `AlgoV3/config/v2_config.yaml`

- 70% core ETF (`NIFTYMIDCAP150.NS`) / 30% satellite
- 3 satellite slots, monthly rebalance (first trading day of month)
- Max 1 entry + 1 exit per rebalance
- BEAR → cash (not GOLDBEES until E5 validates)
- IC governor: GREEN/AMBER/RED exposure scaling

**Planes:**

| Plane | Path | Role |
|-------|------|------|
| Evidence | `planes/evidence/` | Shadow ledger authorization tiers |
| Capital | `planes/capital/` | Core-satellite weights |
| Universe | `planes/universe/` | PIT universe snapshots |
| Signal | `planes/signal/` | Frozen V1 RS pipeline |
| Regime | `planes/regime/` | Frozen regime detection |
| Execution | `planes/execution/` | **Candidate queue + monthly rebalance** |
| Monitoring | `planes/monitoring/` | IC-state governor |

**Daily flow** (`runner/daily.py`):

1. Regime → signal pipeline → **queue upsert** (qualified only)
2. Monthly rebalance: `plan_rebalance()` reads persisted queue
3. Shadow ledger append (live mode)

**Candidate queue** (`planes/execution/queue.py`):

- Write-only daily; persists to `state/candidate_queue.json`
- Key fields: `symbol`, `rs_rank`, `first_qualified_date`, `last_seen_date`
- Ranked by RS; **no auto-expiry**
- Rebalance can use stale queue entries even when `qualified_count=0` today

**V1 vs V3 critical difference:**

- **V1 backtest** buys **same-day** qualifiers only (no persistent inter-rebalance queue for entries)
- **V3** can enter from **persisted queue** at monthly rebalance even if nothing qualifies that morning

---

## 5. Entry Gate Logic (frozen V1 — `AlgoV2/strategy/entry.py`)

All must pass for `signal=YES`:

| Gate | Threshold |
|------|-----------|
| RS rank | ≥ 72 (`RS_THRESHOLD`) |
| RSI | 50–85 (config: `rsi_buy_min/max`; doc context often cites 55–70) |
| Breakout | Within 5% of 20d high OR VCP pivot + RVOL ≥ 1.5 |
| 10d momentum | ≥ +2% |
| Overextension | Not >15% above EMA50 |
| Volume | `vol_ratio` ≥ 1.5 (`MIN_VOLUME_RATIO`) |
| Trend | Price > EMA50 > EMA100, SuperTrend up, ADX ≥ 20 |
| Liquidity | Turnover ≥ ₹2 Cr/day |

**Today (2026-07-08):** BULL, T0 auth, AMBER IC → 85/15 allocation, **0 qualified**.  
29 symbols passed RS≥72 but **all failed secondary gates** (overextended, RSI, weak volume, not at breakout). Normal on ~32% of BULL days.

---

## 6. Qualified Signal Statistics (2022-01-01 → 2026-07-07)

Source: `AlgoV3/outputs/qualified_signal_history.json`  
Script: `AlgoV3/scripts/qualified_signal_history.py`

| Slice | Trading days | % with ≥1 qualifier | Median | Mean |
|-------|-------------|---------------------|--------|------|
| All regimes | 1117 | 48.7% | 0 | 0.90 |
| BULL only | 709 | **68.4%** | 1 | 1.29 |
| Rebalance days (all) | 55 | 54.5% | 1 | 0.95 |
| **Rebalance days (BULL)** | 33 | **75.8%** | 1 | 1.39 |
| Non-rebalance BULL | 676 | 68.0% | 1 | 1.29 |

**Rebalance definition:** First trading day of each calendar month.

---

## 7. The 8 BULL Zero-Rebalance Days

Days that are (a) first trading day of month, (b) BULL regime, (c) zero same-day qualifiers:

```
2023-01-02, 2023-07-03, 2024-04-01, 2024-06-03,
2024-07-01, 2024-08-01, 2024-10-01, 2025-11-03
```

Out of 33 BULL rebalance days → **8 zero (24.2%)**, 25 non-zero (75.8%).

---

## 8. Inter-Rebalance Queue — Correct Definition & Results

**Definition (CORRECT):**  
Unique symbols that qualified (`signal=YES`) on **BULL days** strictly after the **previous month's rebalance** and strictly before **this rebalance**, ranked by RS on rebalance day.

**Correct output:** `AlgoV3/outputs/bull_zero_rebalance_queues.json`

| Date | Prev rebalance | BULL days in window | Queue size | Top 3 by RS |
|------|----------------|---------------------|------------|-------------|
| 2023-01-02 | 2022-12-01 | 21 | 11 | CRAFTSMAN 95.7, HINDALCO 92.6, DALBHARAT 91.5 |
| 2023-07-03 | 2023-06-01 | 20 | 23 | LTF 99.0, CHOLAFIN 96.9, AUROPHARMA 95.8 |
| 2024-04-01 | 2024-03-01 | 18 | 13 | CUMMINSIND 99.0, SIEMENS 94.9, BAJAJ-AUTO 93.9 |
| 2024-06-03 | 2024-05-02 | 21 | 11 | ABB 98.0, ASHOKLEY 91.9, POLYCAB 90.9 |
| 2024-07-01 | 2024-06-03 | 18 | **4** | M&M 93.9, OFSS 92.9, POLYCAB 75.8, VOLTAS 70.7 |
| 2024-08-01 | 2024-07-01 | 21 | 13 | NUVAMA 99.0, CROMPTON 94.9, TRENT 91.9 |
| 2024-10-01 | 2024-09-02 | 20 | 14 | CRAFTSMAN 96.0, MOTHERSON 93.9, KEC 92.9 |
| 2025-11-03 | 2025-10-01 | 20 | 18 | CUMMINSIND 99.0, MUTHOOTFIN 97.0, M&MFIN 96.0 |

**Full symbol lists with `first_qualified` dates** are in the JSON file.

### Full queues (all symbols)

**2023-01-02 (11):** CRAFTSMAN, HINDALCO, DALBHARAT, BHARATFORG, LTF, KEC, CGPOWER, INDIGO, TVSMOTOR, LT, JYOTHYLAB

**2023-07-03 (23):** LTF, CHOLAFIN, AUROPHARMA, ABB, MAXHEALTH, INDIGO, HDFCLIFE, MANKIND, INDUSINDBK, PIIND, CUMMINSIND, TORNTPHARM, TITAN, ICICIGI, ENDURANCE, BAJFINANCE, TVSMOTOR, OFSS, GRINDWELL, KEC, TATAELXSI, DALBHARAT, APLAPOLLO

**2024-04-01 (13):** CUMMINSIND, SIEMENS, BAJAJ-AUTO, TATAPOWER, KALYANKJIL, CGPOWER, MARUTI, PIDILITIND, MOTHERSON, DMART, CIPLA, KEC, TVSMOTOR

**2024-06-03 (11):** ABB, ASHOKLEY, POLYCAB, MOTHERSON, INDIGO, HAVELLS, SBIN, HINDALCO, ASTRAL, CAMS, CDSL

**2024-07-01 (4):** M&M, OFSS, POLYCAB, VOLTAS

**2024-08-01 (13):** NUVAMA, CROMPTON, TRENT, KFINTECH, NCC, ASHOKLEY, AUROPHARMA, CDSL, CRAFTSMAN, KEC, PIIND, TORNTPHARM, ICICIGI

**2024-10-01 (14):** CRAFTSMAN, MOTHERSON, KEC, BAJAJ-AUTO, OFSS, DIVISLAB, TVSMOTOR, PIIND, SUNPHARMA, CHOLAFIN, CGPOWER, JYOTHYLAB, AUBANK, JSWENERGY

**2025-11-03 (18):** CUMMINSIND, MUTHOOTFIN, M&MFIN, TVSMOTOR, GOLDBEES, SBIN, AUBANK, HINDALCO, RADICO, UNOMINDA, BAJFINANCE, ASHOKLEY, CHOLAFIN, LT, SBILIFE, AXISBANK, KAYNES, CIPLA

---

## 9. Gate-Failure Analysis on Rebalance Morning

**Question:** If queue had 4–23 names, why zero qualifiers same day?

Ran backtest + inspected `decision_log` for queued symbols on each rebalance morning.

| Date | Queue | Still RS≥72 | Last-qualified >10d ago | Dominant failure |
|------|-------|-------------|-------------------------|------------------|
| 2023-01-02 | 11 | 9 | 8 | RSI out of bounds (8) |
| 2023-07-03 | 23 | 13 | 12 | Low RS (10), weak volume (8) |
| 2024-04-01 | 13 | 7 | 6 | Weak volume (4), Low RS (4) |
| 2024-06-03 | 11 | 9 | 4 | Overextended (4) |
| 2024-07-01 | 4 | 2 | 2 | Thin queue + mixed gates |
| 2024-08-01 | 13 | 9 | 0 | Weak volume (5), not at breakout (2) |
| 2024-10-01 | 14 | 9 | 9 | Low RS (4), weak momentum (4) |
| 2025-11-03 | 18 | 14 | 9 | Weak volume (9) |

**Interpretation:** These are **setup-decay days**, not empty-pipeline days. RS often stays high but secondary gates fail (RSI overbought, overextension, lost breakout proximity, volume, momentum fade). Connects to doc 20 Leak 3: selected entries underperform passed-over qualifiers by ~1.7pp — timing at purchase matters.

**V3 implication:** Persisted queue could still trigger rebalance entries on these days, but entering stale setups is exactly the timing risk the audit flagged.

---

## 10. Known Bug (NOT YET FIXED)

**File:** `AlgoV3/scripts/zero_rebalance_queue_depth.py`

**Bug:** Line 96 uses `prev_rb = rebalance_days[i - 1]` where `i` is the index in `zero_rebalances` list, NOT the index in full `rebalance_days` list.

**Effect:** Inflates inter-rebalance counts to 56–87 instead of correct 4–23.

**Stale wrong output:** `AlgoV3/outputs/zero_rebalance_queue_depth.json` — do NOT use for the 8 BULL days.

**Correct output:** `AlgoV3/outputs/bull_zero_rebalance_queues.json` (ad-hoc script with proper `rb_index` lookup).

**Fix needed:** Use `rb_index = {d: i for i, d in enumerate(rebalance_days)}` then `prev = rebalance_days[rb_index[rb] - 1]`. Optionally add gate-failure breakdown.

---

## 11. AlgoV2 Live / Shadow Ledger State

- Shadow ledger running: `AlgoV2/outputs/shadow_ledger.jsonl` (2026-07-08, BULL, 100 symbols)
- Shadow ledger score: 0 scorable entries until ~2026-08-12
- Cron added: daily 15:52 IST ledger, Saturday 16:05 IST scoring
- Signal-only exits, LAURUSLABS ghost-position fix (partially in prior session)
- **Uncommitted AlgoV2 changes** (git status): `backtest/engine.py`, `portfolio/manager.py`, `runner/daily_runner.py`, `strategy/entry.py`, `strategy/exit.py`, tests, scripts — user may want review before merge

---

## 12. AlgoV3 Runtime State (2026-07-08)

From `AlgoV3/outputs/daily_cycle_2026-07-08.json`:

```json
{
  "date": "2026-07-08",
  "regime": "BULL",
  "authorization_tier": "T0",
  "ic_state": "AMBER",
  "allocation": { "core_weight": 0.85, "satellite_weight": 0.15 },
  "qualified_count": 0,
  "queue_size": 0,
  "rebalance_plan": null,
  "notes": ["not_rebalance_day"]
}
```

- `daily --live` ran successfully; shadow ledger recorded to `AlgoV3/outputs/shadow_ledger.jsonl`
- 8 unit tests pass (`tests/test_v2_planes.py`)

---

## 13. Key Scripts & How to Reproduce

```bash
# Qualified signal history
cd /home/ravi.prajapati@brainvire.com/Workspace/AlgoV3
python3 scripts/qualified_signal_history.py

# Queue depth (BUGGY — needs fix)
python3 scripts/zero_rebalance_queue_depth.py

# V3 daily cycle
python main.py daily --dry-run
python main.py daily --live
python main.py status

# Correct inter-rebalance queue analysis (inline backtest ~45s)
# See bull_zero_rebalance_queues.json generation logic in chat / terminal 22.txt
```

**Backtest for analysis:** `BacktestEngine` from `AlgoV2/backtest/engine.py`, window 2022-01-01 → 2026-07-07, 100 symbols, decision_log has `date`, `symbol`, `signal`, `reason`, `rs_rank`.

---

## 14. Open Items / Next Steps

1. **Fix** `zero_rebalance_queue_depth.py` prev_rebalance indexing bug
2. **Add** gate-failure breakdown to analysis script (persist to JSON)
3. **Decide V3 policy:** Should rebalance enter from stale queue when same-day `qualified_count=0`? (Timing risk vs opportunity)
4. **AlgoV3 not built yet:** Broker execution, core ETF leg, PIT universe rebuild, V2 backtest engine, AlgoV3 cron
5. **V2 design spec** delivered in chat only — not saved to file unless user wants
6. **Consider queue expiry rule** — e.g. drop candidates not re-qualified within N days

---

## 15. Three Concepts — Do Not Conflate

| Concept | Meaning |
|---------|---------|
| **Same-day qualifiers** | Symbols passing ALL entry gates this morning |
| **Inter-rebalance BULL queue** | Symbols that qualified on prior BULL days since last rebalance |
| **V3 CandidateQueue file** | Persisted state used at monthly rebalance; survives zero-qualifier days |

**Zero rebalance day** = zero same-day qualifiers. Queue is usually **NOT** empty (median ~13 names).

---

## 16. Portfolio Construction Facts (for any V2 work)

From `docs/20_Portfolio_Construction_Audit.md`:

- 3 slots (`MAX_OPEN_POSITIONS=3`); slots full 90.2% of BULL days
- Stranded capital ~27% idle (Leak 1); `score_to_size_factor` is **dead code**
- Friction ~3.2pp/yr (Leak 2); 61% of trades <31 days hold
- Exits/stops **cleared** — do not optimize
- Rank replacement shows no material leak

---

## 17. Constraints for Any Agent Continuing This Work

- **Do NOT optimize parameters in V2** — N-sweep rejected
- **Do NOT modify AlgoV2 from AlgoV3** — use `bridge/v1.py`
- **Freeze exits/stops** — audit cleared them
- **V2 is core-satellite deployment**, not Midcap-beater mandate
- **OOS window statistically spent** — forward shadow ledger is the validation path
- **Run commands yourself** — user expects execution, not instructions
- **Date is 2026** (not 2025)

---

## 18. Suggested Prompt for Claude

```
I'm continuing AlgoV2/V3 quant work. Read the handoff context in docs/22_Chat_Handoff_2026-07-08.md.

Current priority: [pick one]
- Fix zero_rebalance_queue_depth.py bug and add gate-failure breakdown
- Decide V3 rebalance policy for stale queue entries on zero-qualifier days
- Build AlgoV3 broker execution / core ETF leg
- Analyze forward shadow ledger

Repos:
- AlgoV2: /home/ravi.prajapati@brainvire.com/Workspace/AlgoV2
- AlgoV3: /home/ravi.prajapati@brainvire.com/Workspace/AlgoV3

Key artifacts:
- AlgoV3/outputs/bull_zero_rebalance_queues.json (CORRECT queue data)
- AlgoV3/outputs/qualified_signal_history.json
- AlgoV3/outputs/zero_rebalance_queue_depth.json (STALE/WRONG counts)
```