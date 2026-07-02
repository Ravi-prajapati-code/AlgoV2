"""
Central configuration for the swing trading platform.
All tunable parameters live here — change these to adjust strategy behaviour.

Parameter hierarchy (highest to lowest priority):
  1. Environment variables (for secrets / per-deploy overrides)
  2. config/risk_config.yaml   (risk parameters)
  3. config/strategy_config.yaml (strategy parameters)
  4. Hardcoded defaults below  (fallback)
"""

import os
import yaml
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Path to config files
BASE_DIR = Path(__file__).parent.parent
RISK_CONFIG_PATH = BASE_DIR / "config" / "risk_config.yaml"
STRATEGY_CONFIG_PATH = BASE_DIR / "config" / "strategy_config.yaml"

def load_yaml(path):
    if not path.exists():
        return {}
    with open(path, 'r') as f:
        try:
            return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Error loading {path}: {e}")
            return {}

risk_cfg = load_yaml(RISK_CONFIG_PATH)
strat_cfg_raw = load_yaml(STRATEGY_CONFIG_PATH)
# Handle both flat and strategy-nested YAML structures
strat_cfg = strat_cfg_raw.get('strategy', strat_cfg_raw)

# ──────────────────────────────────────────────
# MARKET / BROKER
# ──────────────────────────────────────────────
MARKET    = "NSE"
BROKER    = "upstox"
CURRENCY  = "INR"

# ──────────────────────────────────────────────
# PORTFOLIO (from risk_cfg)
# ──────────────────────────────────────────────
portfolio_cfg = risk_cfg.get('portfolio', {})
INITIAL_CAPITAL         = float(os.getenv("INITIAL_CAPITAL") or portfolio_cfg.get('initial_capital', 100000))
MAX_OPEN_POSITIONS      = int(os.getenv("MAX_POSITIONS", portfolio_cfg.get('max_open_positions', 6)))
MAX_NEW_TRADES_PER_DAY  = portfolio_cfg.get('max_new_trades_per_day', 999)

# ── Allocation caps ────────────────────────────────────────────────────
alloc_cfg = risk_cfg.get('allocation', {})
MAX_STOCK_ALLOCATION_PCT    = 0.25  # hardcoded — 25% max per position
MAX_SECTOR_ALLOCATION_PCT   = alloc_cfg.get('max_sector_pct', 0.50)

# ── Per-trade risk limits ──────────────────────────────────────────────
per_trade_cfg = risk_cfg.get('per_trade', {})
MAX_RISK_PER_TRADE_PCT      = per_trade_cfg.get('max_risk_pct', 1.0)
CASH_RESERVE_PCT            = portfolio_cfg.get('cash_reserve_pct', 0.0)
SIZER_CASH_BUFFER_PCT       = portfolio_cfg.get('sizer_cash_buffer_pct', 0.05)

# ── Drawdown protection ────────────────────────────────────────────────
drawdown_cfg = risk_cfg.get('drawdown', {})
DRAWDOWN_KILL_SWITCH_PCT       = float(os.getenv("DD_KILL_PCT",   drawdown_cfg.get('kill_switch_pct', 0.10)))
DRAWDOWN_REDUCE_SIZE_PCT       = float(os.getenv("DD_REDUCE_PCT", drawdown_cfg.get('reduce_size_pct', 0.05)))
DRAWDOWN_REDUCE_TIER2_MULT     = float(os.getenv("DD_TIER2_MULT", drawdown_cfg.get('reduce_size_tier2_mult', 1.5)))

# GTT stop-loss orders execute as LIMIT on trigger (NSE GTT does not support true
# market-on-trigger) — with no buffer, a limit pinned at the exact trigger price is
# unfillable the instant price gaps/moves through it (2026-07-01 GOLDBEES incident:
# left naked all day). This buffer gives the resting order real room to fill.
GTT_LIMIT_BUFFER_PCT           = float(os.getenv("GTT_LIMIT_BUFFER_PCT", "0.015"))

# ──────────────────────────────────────────────
# STRATEGY — ENTRY SIGNALS (from strat_cfg)
# ──────────────────────────────────────────────
entry_cfg = strat_cfg.get('entry', {})
EMA_FAST                = entry_cfg.get('ema_fast', 20)
EMA_SLOW                = entry_cfg.get('ema_slow', 100)
EMA_CROSSOVER_LOOKBACK  = entry_cfg.get('ema_crossover_lookback', 5)
RSI_PERIOD              = entry_cfg.get('rsi_period', 14)
RSI_BUY_MIN             = entry_cfg.get('rsi_buy_min', 55)
RSI_BUY_MAX             = entry_cfg.get('rsi_buy_max', 85)
MACD_FAST               = entry_cfg.get('macd_fast', 12)
MACD_SLOW               = entry_cfg.get('macd_slow', 26)
MACD_SIGNAL             = entry_cfg.get('macd_signal', 9)
VOLUME_SPIKE_MULTIPLIER = entry_cfg.get('volume_spike_multiplier', 1.2)
MIN_SIGNAL_SCORE        = entry_cfg.get('min_signal_score', 0)
MIN_VOLUME_RATIO        = entry_cfg.get('min_volume_ratio', 1.5)
BLOCKED_SECTORS         = set(strat_cfg.get('blocked_sectors', []))

# ──────────────────────────────────────────────
# MARKET REGIME FILTER
# ──────────────────────────────────────────────
MARKET_INDEX_SYMBOL     = "Nifty 50"
MARKET_FILTER_SMA       = 200
MARKET_FILTER_ENABLED   = True

# ──────────────────────────────────────────────
# STRATEGY — EXIT / RISK
# ──────────────────────────────────────────────
exit_cfg = strat_cfg.get('exit', {})
STOP_LOSS_PCT           = exit_cfg.get('stop_loss_pct', 0.07)
TAKE_PROFIT_PCT         = exit_cfg.get('take_profit_pct', 0.50)   # Emergency ceiling only — let winners run
TRAILING_STOP_PCT       = exit_cfg.get('trailing_stop_pct', 0.12) # Wider initial trail (was 0.09)
TRAIL_TIGHTEN_THRESHOLD = exit_cfg.get('trail_tighten_threshold', 0.15) # Tighten earlier at 15% profit (was 0.20)
TRAIL_TIGHTEN_PCT       = exit_cfg.get('trail_tighten_pct', 0.06)       # 6% tightened trail (was 0.05)
ATR_STOP_MULTIPLIER     = per_trade_cfg.get('atr_stop_multiplier', 2.5)
ATR_TRAIL_MULT_INITIAL  = exit_cfg.get('atr_trail_mult_initial', 2.5)   # ATR × 2.5 for initial trail
ATR_TRAIL_MULT_TIGHT    = exit_cfg.get('atr_trail_mult_tight', 1.5)     # ATR × 1.5 after tightening
MAX_HOLD_DAYS           = exit_cfg.get('max_hold_days', 120)

# ──────────────────────────────────────────────
RS_THRESHOLD         = float(os.getenv("RS_THRESHOLD", "72.0"))  # min composite_rank to qualify for buy
ADX_TREND_THRESHOLD  = entry_cfg.get('adx_trend_threshold', 20.0)
# 200-day EMA trend gate (off by default — live unaffected). Test-only refinement lever.
TREND_GATE_200_ENABLED = os.getenv("TREND_GATE_200", "false").lower() in ("true", "1", "yes")

# Backtest-only realism fix: entries fill at the NEXT trading day's open, matching
# live's actual timing (signal from yesterday's close, fill at today's 09:17 open).
# On by default as of 2026-07-02 — A/B validated against the prior same-day-close
# fill (CAGR +40.89%→+32.28%, Sharpe 2.10→1.72, MDD 13.96%→18.68% on 2022-01-01→
# 2026-06-30, same trade count) and adopted as the realistic baseline. Set
# NEXT_DAY_OPEN_FILL=false to reproduce the old (optimistic) same-day-close numbers.
NEXT_DAY_OPEN_FILL_ENABLED = os.getenv("NEXT_DAY_OPEN_FILL", "true").lower() in ("true", "1", "yes")

# Backtest-only correctness fix: live (runner/daily_runner.py) computes regime via
# strategy.regime.detect_regime() — a 3-day-confirm + 65%-of-20-days-hysteresis
# whipsaw filter. backtest/engine.py's _precompute_all() used to independently
# compute its own raw day-by-day (close > EMA100) regime signal instead of calling
# the detect_regime() it already imports — a genuine live/backtest divergence, not
# a deliberate design choice. On by default as of 2026-07-02: A/B validated
# against the old raw signal (CAGR +32.04%→+12.63%, Sharpe 1.71→0.82, MDD
# 18.68%→23.67% on 2022-01-01→2026-06-30 under the then-current params — the old
# raw-signal numbers were never representative of what live actually gates on).
# Set REGIME_SMOOTHING=false to reproduce the old (incorrect) raw-signal numbers.
REGIME_SMOOTHING_ENABLED = os.getenv("REGIME_SMOOTHING", "true").lower() in ("true", "1", "yes")

# ──────────────────────────────────────────────
# BACKTESTING — SLIPPAGE (Phase 2)
# ──────────────────────────────────────────────
SLIPPAGE_MODEL          = "fixed_pct"  # 'none' | 'fixed_pct' | 'volatility'
SLIPPAGE_FIXED_PCT      = 0.001        # 0.1 % per side (fixed model)
SLIPPAGE_ATR_MULT       = 0.10         # ATR × 0.10 → slippage % (volatility model)
PARTIAL_FILL_ENABLED    = True         # Simulate partial fills for large orders
PARTIAL_FILL_RATE       = 0.10         # Max 10 % of ADV per order

# ──────────────────────────────────────────────
# SAFE HAVEN (Phase 2 Add-on)
# ──────────────────────────────────────────────
SAFE_HAVEN_ENABLED          = os.getenv("SAFE_HAVEN_ENABLED", "true").lower() in ("true", "1", "yes")
SAFE_HAVEN_SYMBOL           = "GOLDBEES.NS"
SAFE_HAVEN_YIELD_ANNUAL     = 0.06         # Fallback 6% annual return if data missing
GOLDBEES_PROFIT_EXIT_ONLY   = os.getenv("GOLDBEES_PROFIT_EXIT_ONLY", "false").lower() in ("true", "1", "yes")
GOLDBEES_MAX_LOSS_PCT       = float(os.getenv("GOLDBEES_MAX_LOSS_PCT", "0.07"))  # cut GOLDBEES if loss exceeds this
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# ML PREDICTION LAYER (Disabled for Simplicity)
# ──────────────────────────────────────────────
ML_ENABLED              = False  # Disabled: model win_rate=26% (harmful), retrain needed after indicator fixes
ML_MIN_CONFIDENCE       = 0.65
ML_MODEL_DIR            = "ml/models"

# ──────────────────────────────────────────────
# DATA
# ──────────────────────────────────────────────
PARTIAL_REGIME_MIN_CANDLES = 50       # Minimum candles for PARTIAL regime trading
LOOKBACK_DAYS           = 500         # Days of OHLCV history for indicators; matches EMA_WARMUP_DAYS for EMA(150) convergence
EMA_WARMUP_DAYS         = 500         # Days needed for EMA(150) to converge (3× span); used on first fetch
# DB_PATH_OVERRIDE lets isolated tooling (e.g. scripts/stress_test_scenarios.py)
# point the whole app at a scratch DB via subprocess env, without ever touching
# the real db/trading.db — same per-deploy-override pattern as everything else here.
DATA_CACHE_DB           = os.getenv("DB_PATH_OVERRIDE", "db/trading.db")
DB_PATH                 = DATA_CACHE_DB
OUTPUTS_DIR             = "outputs"

# ──────────────────────────────────────────────
# BACKTEST ACCEPTANCE CRITERIA
# ──────────────────────────────────────────────
BACKTEST_MIN_CAGR           = 0.22  # Revised: Wilder's indicator correction reduced realistic CAGR from ~26% to ~23%
BACKTEST_MIN_SHARPE         = 1.00
BACKTEST_MAX_DRAWDOWN       = 0.20
BACKTEST_MIN_WIN_RATE       = 0.40   # with sector caps active; 40% @ 4:1 R:R still profitable
BACKTEST_MIN_PROFIT_FACTOR  = 1.80

# ──────────────────────────────────────────────
# NOTIFICATIONS
# ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ──────────────────────────────────────────────
# UPSTOX API (live trading — optional)
# ──────────────────────────────────────────────
UPSTOX_API_KEY      = os.getenv("UPSTOX_API_KEY", "")
UPSTOX_API_SECRET   = os.getenv("UPSTOX_API_SECRET", "")
# Re-read token to ensure we have the latest from .env
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "")

# ──────────────────────────────────────────────
# INTRADAY EXECUTION
# ──────────────────────────────────────────────
EXECUTION_TIMES = ["09:17"]
def round_to_tick(price: float, tick: float = 0.05) -> float:
    """Round price to the nearest tick size (e.g., 0.05 for NSE)."""
    return round(round(price / tick) * tick, 2)

# ──────────────────────────────────────────────
# IGNORE MANUAL HOLDINGS
# ──────────────────────────────────────────────
IGNORE_SYMBOLS = ["LT.NS", "HCLTECH.NS", "IRFC.NS", "CAMS.NS"]  # CAMS: 3 trades 0% WR structural loser
