# 06 — Validation Methodology

## The core rule

**A clean full-window backtest number is not sufficient evidence that a change is real.** Every
promising-looking result in this project's history has needed all three of the following before
being trusted, and results that skipped a stage were the ones that turned out to be false
positives (see `05_Research.md`'s extension-filter and staged-entry rejections):

1. Full-window backtest.
2. Out-of-sample train/test split.
3. All 4 synthetic stress-test scenarios.

## Out-of-sample validation (`scripts/out_of_sample_validator.py`)

Splits history into TRAIN (2022-01-01 → 2024-12-31) and TEST (2025-01-01 → present, never touched
by any parameter sweep), runs a config on both, and flags a metric as DIVERGENT if it swings more
than a threshold between windows (win-rate >15pp, PF >0.8, Sharpe >0.6).

**Finding on the current live config**: TRAIN win-rate 32.7% vs TEST win-rate 52.0% — a 19.3-point
swing, flagged DIVERGENT. CAGR/MDD differ across windows too (train 15.20%/23.67% vs test
13.50%/15.32%) though within threshold. A `MAX_POSITIONS=2` variant that looked like a near-gate-pass
on the TEST window alone was shown to be period luck once TRAIN was checked (TRAIN win-rate 33.3%,
PF 1.62 vs TEST's 52.5%/2.22) — not a real improvement.

**Conclusion carried forward**: at this strategy's trade-count scale (~100–150 trades per ~3-year
window), win-rate and profit-factor carry substantial period-dependent noise. A single-window PASS,
past or future, should always be read with this in mind.

## Robustness Gate (`scripts/robustness_gate.py`, built 2026-07-06)

Formalizes the full three-stage sequence as one command, run for BASELINE (no overrides) and
CANDIDATE (given `--env KEY=VALUE` overrides) side by side against identical data:

```
python3 scripts/robustness_gate.py --env EXTENSION_FILTER_EMA100_MAX_PCT=17.8
```

Runs: full-window backtest, out-of-sample train/test (reusing `out_of_sample_validator.run_window`),
and all 4 stress scenarios (reusing `stress_test_scenarios.py`, sharing one scratch DB per scenario
across both arms for a fair comparison). ~5–6 minutes total, 14 subprocess `main.py backtest` calls.
Zero engine.py changes, zero live DB writes; `UPSTOX_ACCESS_TOKEN` is stripped before every
subprocess call.

**Fixed, inspectable gate rules** (not a black box):
- Candidate TEST-window Sharpe/PF must not fall more than 0.10 (absolute) below baseline's.
- Candidate must not introduce **new** train/test instability beyond what the baseline already has
  — the baseline's own known WR divergence (above) is treated as informational, not an automatic
  candidate rejection, since it's a pre-existing condition unrelated to the candidate.
- Candidate must not flip any stress scenario's CAGR from non-negative to negative, and must not
  drop PF more than 0.10 below baseline's PF in any scenario.

**Verified**: smoke-tested with a no-op env override — baseline and candidate came back
byte-identical on every metric across all 3 stages, correctly reporting PASS, and correctly
reproduced the known baseline WR divergence without treating it as a false rejection.

**How to apply**: any future candidate lever should be wired in via the established off-by-default
env-var pattern (see `TREND_GATE_200_ENABLED` in `config/settings.py`), then run through this one
command instead of manually chaining the validator and stress-test scripts by hand. A PASS here is
**necessary but not sufficient** — still exercise judgment before a live-deployment conversation.

## Current honest baseline (post regime-signal-divergence fix, 2026-07-03)

CAGR +12.85%, Sharpe 0.83, MDD 23.67%. **This fails the system's own gates.** The previous
headline number (CAGR 32.04%) was confirmed to be an artifact of the pre-fix backtest bug (backtest
used a raw EMA100 crossover for regime instead of live's smoothed `detect_regime()`), not a real
edge. Extensive re-tuning after the fix found no safe configuration that both recovers CAGR and
survives all 4 stress scenarios (see `05_Research.md`). Live has been kept unchanged.

## Known validation-tooling inconsistency

`backtest/metrics.py::calculate_metrics()` computes Sharpe/Sortino using population variance
(÷N). `scripts/walk_forward.py`'s `_run_backtest_window()` instead uses `statistics.stdev()`
(sample variance, ÷N-1) for its own Sharpe. The two "Sharpe" numbers reported by these two tools
for the identical equity curve are not exactly reproducible against each other — a small but real
methodology drift worth fixing or at minimum being aware of before comparing a `main.py backtest`
Sharpe directly against a `walk_forward.py` decay-monitor Sharpe. See `09_Open_Questions.md`.
