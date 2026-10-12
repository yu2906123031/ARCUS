#!/bin/sh
set -eu
day=$(TZ=America/New_York date +%u)
hour=$(TZ=America/New_York date +%-H)
if [ "$day" -le 5 ] && [ "$hour" -ge 4 ] && [ "$hour" -lt 20 ]; then
  config=/root/ARCUS/mm_live_spy_account2.json
else
  config=/root/ARCUS/mm_live_btc_account2.json
fi
exec /root/.hermes/hermes-agent/venv/bin/python3 -u -m arcus_mm run \
  --config "$config" --live --auto-resume --auto-restart --seconds 0 --flatten-on-exit
