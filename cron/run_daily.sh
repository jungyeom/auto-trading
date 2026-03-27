#!/bin/bash
# Cron wrapper for the auto-trader daily pipeline.
#
# Crontab entries (add both to handle EDT/EST automatically):
#   # EDT (Mar–Nov): 4:30pm ET = 20:30 UTC
#   30 20 * * 1-5 /home/kevin/auto-trader/cron/run_daily.sh >> /home/kevin/auto-trader/logs/cron.log 2>&1
#   # EST (Nov–Mar): 4:30pm ET = 21:30 UTC
#   30 21 * * 1-5 /home/kevin/auto-trader/cron/run_daily.sh >> /home/kevin/auto-trader/logs/cron.log 2>&1
#
# The ET_HOUR guard below ensures only one of the two cron entries actually runs.
set -e

# Guard: only run if it is currently 4pm ET (hour 16). This prevents double-firing
# from the dual EDT/EST cron entries — exactly one will match.
ET_HOUR=$(TZ="America/New_York" date +%H)
if [ "$ET_HOUR" != "16" ]; then
    echo "$(date -u): Skipping — not 4pm ET (current ET hour: $ET_HOUR)"
    exit 0
fi

echo "$(date -u): Starting auto-trader pipeline..."

cd /home/kevin/auto-trader
source venv/bin/activate
python -m src.main

echo "$(date -u): Pipeline finished."
