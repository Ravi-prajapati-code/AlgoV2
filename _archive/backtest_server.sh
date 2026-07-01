#!/bin/bash
# Run backtest on server and show key metrics
# Usage: ./backtest_server.sh [start_date] [end_date]
# Default: 2022-11-01 to 2026-05-07 (baseline window)

START=${1:-2022-11-01}
END=${2:-2026-05-07}

echo "Running backtest on server: $START → $END"
ssh -i /home/ravi.prajapati@brainvire.com/Workspace/algo-key.pem -o StrictHostKeyChecking=no ubuntu@3.109.104.170 \
  "cd ~/AlgoV2 && .venv/bin/python3 main.py backtest --start $START --end $END 2>&1 | grep -E 'CAGR|Sharpe|Drawdown|Win Rate|Profit Factor|Total Trades|PASS|FAIL'"
