#!/bin/bash
# Cron wrapper for the auto-trader daily pipeline.

set -e

echo "$(date -u): Starting auto-trader pipeline..."
cd /root/auto-trading
uv run python -m src.main

echo "$(date -u): Pipeline finished."
