# Universe Verification Report

**Investment Committee Gate — "Is there actual look-ahead contamination?"**

Every claim below is labeled `[Fact / Opinion / Unknown, Confidence: High / Medium / Low]`. This
report exists to give a direct, decision-ready answer to the one question the reviewer flagged as
most consequential in `13_Independent_Institutional_Review.md`: does the universe-construction
issue require rebuilding historical results, or is it an architectural nit?

---

## 1. Is there contamination? Yes — confirmed by direct code trace, not inference.

**[Fact, High confidence]** Before 2026-07-07, `main.py::cmd_backtest()` called
`data/universe.py::get_all_symbols()`, which returns `config.watchlist_nse.ALL_SYMBOLS` — the
*current working-tree contents of that file* — with no date parameter anywhere in the call chain.
Every backtest, for every historical date, was evaluated against **today's** static watchlist.
`scripts/out_of_sample_validator.py` runs its TRAIN (2022-2024) and TEST (2025-present) windows
through this identical unconditional path — both windows see the same present-day list.

**[Fact, High confidence]** `config/watchlist_nse.py`'s own comments document a "Quality revision"
dated **2026-06-17** that removed symbols from the list using **cumulative trade-level win-rate and
P&L statistics** — i.e., using knowledge of how those symbols actually performed, including
performance recorded well after most of the backtest history begins. This is a textbook
survivorship-bias mechanism: known losers were identified using hindsight and then excluded from
every historical evaluation, including periods before the exclusion decision existed.

## 2. Quantifying the scope

**[Fact, High confidence]** The current watchlist (`ALL_SYMBOLS`, 100 symbols) excludes 33 symbols
that were removed using retrospective performance criteria, per the file's own documentation:

| Removal batch | Symbols | Count |
|---|---|---|
| "Governance risks & confirmed losers" | IIFL, RECLTD, BIOCON, DEEPAKNTR, NAVINFLUOR | 5 |
| "Quality revision" (2026-06-17) | INDUSTOWER, BHARTIARTL, BEL, KEI, SOLARINDS, SCHAEFFLER, ETERNAL, PERSISTENT, LUPIN, HEROMOTOCO, TIINDIA, ZYDUSLIFE, ESCORTS, SUPREMEIND, FORTIS, JBCHEPHARM, LAURUSLABS, THERMAX, COFORGE, DIXON | 20 |
| "Consumer Services losers" | INDHOTEL, IRCTC, DEVYANI, JUBLFOOD, NAUKRI | 5 |
| Realty | GODREJPROP, PHOENIXLTD, PRESTIGE | 3 |
| **Total removed using retrospective performance data** | | **33** |

All 33 have cached daily OHLCV data in the project's own DB back to **2021-12-02** (confirmed via
`db.repository.earliest_cached_date`), meaning a direct sensitivity test — restore these 33 symbols
and re-run the exact same backtest — was buildable without any new external data.

**[Fact, High confidence]** `scripts/universe_contamination_sensitivity.py` runs this test: current
100-symbol universe vs. a 133-symbol universe with all 33 hindsight-removed names restored, across
the same TRAIN (2022-2024) / TEST (2025-present) / FULL (2022-present) windows
`out_of_sample_validator.py` uses.

**Results** (`scripts/universe_contamination_sensitivity.py`, run 2026-07-07):

| Window | Baseline (100) CAGR | Restored (133) CAGR | Delta | Baseline N | Restored N |
|---|---|---|---|---|---|
| TRAIN (2022-2024) | +15.64% | +13.45% | **-2.19pp** | 97 | 108 |
| TEST (2025-present) | +10.73% | +17.66% | **+6.93pp** | 52 | 51 |
| FULL (2022-present) | +12.21% | +13.14% | **+0.93pp** | 153 | 164 |

**[Fact, High confidence]** This result is **not what the naive survivorship-bias hypothesis
predicts.** If removing 33 "confirmed losers" using hindsight had simply inflated reported
performance, the baseline (without them) should look better than the restored universe (with them)
on *every* window. Instead: baseline beats restored on TRAIN (as the naive hypothesis predicts), but
**restored beats baseline by a wide margin on TEST** — the very window the removal decision was
supposedly informed by.

**[Opinion, Medium confidence] — the honest reading of this**: the test has a real design confound
that this result surfaces. The strategy selects its top-N candidates each period from whatever
universe it's given; enlarging the universe by 33 names changes *which trades get selected*, not just
whether the 33 "loser" symbols themselves get traded well or badly. A bigger candidate pool can move
results in either direction for reasons that have nothing to do with those specific symbols'
standalone quality — it is not a clean isolation of "the hindsight-removal effect" the way a
same-size swap (remove 33 different names to compensate) would be. Combined with the small trade
counts involved (51-164 trades per window — the same order of magnitude that already produced the
19.3pp OOS win-rate swing noted in `13_Independent_Institutional_Review.md` §4.3), these deltas are
plausibly dominated by noise and selection-pool-size effects, not by a clean, isolable survivorship
bias number.

**[Opinion, Medium confidence] — what this does and doesn't establish**: this result does **not**
mean the look-ahead problem is smaller than feared, and it does **not** let anyone conclude the
existing baseline numbers are trustworthy. It means the *specific* sensitivity design tried here
can't cleanly separate "hindsight-informed removal inflated the numbers" from "a bigger candidate
pool changes which trades get picked, in a noisy, sample-size-limited way." A cleaner test — same
universe size, remove 33 names using only information available at the start of each window, compare
against removing today's specific 33 — would be needed to isolate the effect properly, and hasn't
been built. This is a genuine limitation of this report, not a reason to relax the caution in §6.

## 3. What can and cannot be reconstructed

**[Fact, High confidence]** `config/watchlist_nse.py` has exactly one commit in git history
(`a26a4df`, 2026-07-01, "Initial commit of AlgoV2 trading system") — the whole repository was
squashed into a single initial commit on that date. There is no per-revision commit history for this
file. The 2026-06-17 "Quality revision" predates the only commit that exists; it is known only from
a comment in the file, not from a dated record.

**[Fact, High confidence]** This means true point-in-time reconstruction of universe membership for
any date before tracking began is **structurally impossible** with data that currently exists in
this project. There is no git history, no changelog, no dated snapshot table, and no external
record of what the watchlist looked like on any past date.

## 4. What has been fixed, and what hasn't

**[Fact, High confidence]** As of 2026-07-07, the unconditional-current-list code path is closed
going forward:

- `data/universe.py::get_all_symbols_as_of(date)` reconstructs static-universe membership from
  dated snapshots logged in `db/universe_repo.py` (`universe_history` table,
  `operator='static_watchlist_sync'`) instead of always importing today's file contents.
- `main.py::cmd_backtest()` uses this function, and raises `UniverseHistoryUnavailable` — loudly,
  to stderr, with a fallback disclosure — for any date before the tracking baseline.
- `config/watchlist_nse.py` now requires `scripts/sync_static_universe.py` to be run after every
  edit; `tests/test_static_universe_sync.py` (7 tests) guards the mechanism.
- Point-in-time tracking baseline was seeded 2026-07-06/07.

**[Fact, High confidence] — the critical limitation**: because tracking only starts 2026-07-06/07,
`get_all_symbols_as_of()` will raise for **every date in the entire existing evidence base**
(2022-2025) and fall back to applying today's list — the exact same behavior as before, now with a
warning attached instead of silence. **Re-running the existing backtests today does not produce
different numbers than before the fix.** The fix prevents this specific bug from recurring for any
*future* universe change; it cannot retroactively clean the historical record, because §3 established
that the historical record needed for a clean reconstruction does not exist.

## 5. Known remaining gap — not addressed by this report or the fix

**[Fact, High confidence]** The *dynamic* universe "extras" mechanism
(`data/universe.py::_get_dynamic_symbols()`) is separate from the static watchlist covered above and
is **not** point-in-time filtered. It already has dated events in `universe_history` (used for its
own promotion/demotion state machine), so it is plausibly the easier of the two to fully fix — but
that work has not been done or scoped.

## 6. Bottom line for the Investment Committee

**[Opinion, High confidence]**: This is not an "architectural improvement" in the mild sense, but the
sensitivity test in §2 did not deliver a clean, one-directional "here's how much it was inflated"
number — it surfaced a design confound (candidate-pool size vs. specific-symbol effects) that makes
the true magnitude genuinely **unknown**, not merely "large but unmeasured." That is arguably a worse
epistemic position than a confirmed inflation number would have been: it means this project cannot
currently state, in either direction, how far off the existing evidence base is. **Every backtest
number produced before 2026-07-07 — including the 12.85% CAGR baseline, the 19.3pp OOS win-rate
divergence, and everything in `10_Quantitative_Research_Review.md` — should be treated as
unvalidated, not merely "optimistic."** A genuinely clean evaluation is only possible for periods
*after* the 2026-07-06/07 tracking baseline, going forward, per §3-4 — not retroactively. The
committee's own instruction (proceed with Gates 1-3 now, using existing data, with every downstream
number explicitly labeled "pending re-validation once enough clean forward data accumulates") is the
basis for the rest of this doc series — see `15_Economic_Hypothesis.md` onward.
