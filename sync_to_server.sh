#!/bin/bash
# Sync local code changes to server (skips DB, data, logs, secrets)
rsync -avz --progress \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.env' \
  --exclude 'db/' \
  --exclude 'data/parquet*' \
  --exclude 'data/ohlcv_cache.db' \
  --exclude 'outputs/' \
  --exclude 'logs/' \
  --exclude 'algo-key.pem' \
  -e "ssh -i /home/ravi.prajapati@brainvire.com/Workspace/algo-key.pem -o StrictHostKeyChecking=no" \
  /home/ravi.prajapati@brainvire.com/Workspace/AlgoV2/ \
  ubuntu@3.109.104.170:~/AlgoV2/
