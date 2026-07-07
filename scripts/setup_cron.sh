#!/usr/bin/env bash
# Setup cron jobs for Algo trading system (IST timezone, system already Asia/Kolkata)
#
# Cron jobs installed:
#   08:30 IST Mon-Fri  — Auto token refresh (no human needed)
#   08:45 IST Mon-Fri  — Telegram reminder to refresh Upstox token
#   09:20 IST Mon-Fri  — Position reconciler (DB vs Upstox)
#   09:22 IST Mon-Fri  — GTT price-consistency audit (DB stop vs broker GTT trigger)
#   09:25 IST Mon-Fri  — Morning P&L summary via Telegram
#   15:40 IST Mon-Fri  — GTT coverage audit: alert on any naked (unprotected) position
#   15:45 IST Mon-Fri  — Run paper/live daily strategy after market close
#   15:55 IST Mon-Fri  — Health check: alert if run failed
#   16:30 IST Mon-Fri  — Nightly DB backup (30-day retention)
#   13:00 IST Friday   — Weekly universe re-rank

set -euo pipefail

ALGO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$(which python3)"
LOG_DIR="$ALGO_DIR/logs"
mkdir -p "$LOG_DIR"

# ── Detect live vs paper mode ──────────────────────────────────────────────
MODE="${1:-paper}"   # pass "live" as first arg to enable live trading cron
if [[ "$MODE" == "live" ]]; then
    RUN_CMD="$PYTHON $ALGO_DIR/main.py run --live"
    MODE_LABEL="LIVE"
else
    RUN_CMD="$PYTHON $ALGO_DIR/main.py run"
    MODE_LABEL="PAPER"
fi

echo ""
echo "=== Algo Trading Cron Setup ==="
echo "Project dir : $ALGO_DIR"
echo "Python      : $PYTHON"
echo "Mode        : $MODE_LABEL"
echo ""

# ── Build cron lines ───────────────────────────────────────────────────────

PYTHON_VENV="$ALGO_DIR/.venv/bin/python"
# Fall back to system python3 if venv not present
[[ -x "$PYTHON_VENV" ]] && PYTHON="$PYTHON_VENV"

# 1. Auto token refresh (8:30 AM IST)
AUTO_TOKEN_CRON="30 8 * * 1-5 cd $ALGO_DIR && $PYTHON $ALGO_DIR/scripts/auto_token.py >> $LOG_DIR/token_refresh.log 2>&1"

# 2. Morning reminder (8:45 AM IST): fallback Telegram nudge if auto_token fails
REMINDER_CRON="45 8 * * 1-5 cd $ALGO_DIR && $PYTHON $ALGO_DIR/scripts/send_token_reminder.py >> $LOG_DIR/token_reminder.log 2>&1"

# 3. Position reconciler (9:20 AM IST): DB vs Upstox holdings check
RECONCILE_CRON="20 9 * * 1-5 cd $ALGO_DIR && $PYTHON $ALGO_DIR/scripts/reconcile_positions.py >> $LOG_DIR/reconcile.log 2>&1"

# 4. Morning P&L summary (9:25 AM IST)
PNL_CRON="25 9 * * 1-5 cd $ALGO_DIR && $PYTHON $ALGO_DIR/scripts/daily_pnl_summary.py >> $LOG_DIR/pnl_summary.log 2>&1"

# 4b. GTT price-consistency audit (9:22 AM IST): DB stop vs broker GTT trigger,
#     also flags naked/duplicate GTTs. Read-only, Telegram-alerts only.
GTT_AUDIT_CRON="22 9 * * 1-5 cd $ALGO_DIR && $PYTHON $ALGO_DIR/monitoring/gtt_price_audit.py >> $LOG_DIR/gtt_price_audit.log 2>&1"

# 5. Market close runner (3:45 PM IST Mon-Fri)
RUNNER_CRON="45 15 * * 1-5 cd $ALGO_DIR && $RUN_CMD >> $LOG_DIR/daily_run_\$(date +\%Y\%m\%d).log 2>&1"

# 4c. GTT coverage audit (3:40 PM IST): alert on any naked (unprotected) position,
#     just before the daily runner. Schedule per monitoring/gtt_coverage.py's own docstring.
GTT_COVERAGE_CRON="40 15 * * 1-5 cd $ALGO_DIR && $PYTHON $ALGO_DIR/monitoring/gtt_coverage.py >> $LOG_DIR/gtt_coverage.log 2>&1"

# 6. Health check (3:55 PM IST): alert if today's log is empty/missing/errored
HEALTH_CRON="55 15 * * 1-5 cd $ALGO_DIR && $PYTHON $ALGO_DIR/scripts/health_check.py >> $LOG_DIR/health.log 2>&1"

# 7. Nightly DB backup (4:30 PM IST): 30-day rolling backup
BACKUP_CRON="30 16 * * 1-5 cd $ALGO_DIR && $PYTHON $ALGO_DIR/scripts/backup_db.py >> $LOG_DIR/backup.log 2>&1"

# 8. Weekly universe re-rank (1:00 PM IST Friday)
UNIVERSE_CRON="0 13 * * 5 cd $ALGO_DIR && $PYTHON $ALGO_DIR/scripts/universe_scheduler.py --mode weekly >> $LOG_DIR/universe.log 2>&1"

# 8b. Daily universe safety net (12:00 PM IST Mon-Fri)
UNIVERSE_DAILY_CRON="0 12 * * 1-5 cd $ALGO_DIR && $PYTHON $ALGO_DIR/scripts/universe_scheduler.py --mode daily >> $LOG_DIR/universe_daily.log 2>&1"

# ── Install into crontab ───────────────────────────────────────────────────
MARKER="# AlgoTrading"

# Get existing crontab minus old Algo lines (ignore errors if no crontab yet)
EXISTING=$(crontab -l 2>/dev/null | grep -v "$MARKER" || true)

printf '%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n' \
  "$EXISTING" \
  "$AUTO_TOKEN_CRON  $MARKER:auto_token" \
  "$REMINDER_CRON    $MARKER:token_reminder" \
  "$RECONCILE_CRON   $MARKER:reconcile" \
  "$GTT_AUDIT_CRON   $MARKER:gtt_price_audit" \
  "$PNL_CRON         $MARKER:pnl_summary" \
  "$GTT_COVERAGE_CRON $MARKER:gtt_coverage" \
  "$RUNNER_CRON      $MARKER:daily_run" \
  "$HEALTH_CRON      $MARKER:health_check" \
  "$BACKUP_CRON      $MARKER:db_backup" \
  "$UNIVERSE_CRON    $MARKER:universe_weekly" \
  "$UNIVERSE_DAILY_CRON $MARKER:universe_daily" \
  | crontab -

echo "Cron jobs installed:"
crontab -l | grep "$MARKER"

echo ""
echo "Logs will be written to: $LOG_DIR/"
echo ""
echo "Next steps:"
echo "  1. Token auto-refreshes at 08:30 IST (fallback reminder at 08:45)"
echo "  2. Position reconcile + P&L summary sent at 09:20–09:25 IST"
echo "  3. Strategy runs at 15:45 IST, health check at 15:55"
echo "  4. DB backed up at 16:30 IST → db/backups/ (30-day retention)"
echo "  5. Universe re-ranked every Friday 13:00 IST"
echo ""
echo "To switch to LIVE mode (real money):"
echo "  bash scripts/setup_cron.sh live"
echo ""
echo "To remove all cron jobs:"
echo "  crontab -l | grep -v '# AlgoTrading' | crontab -"
