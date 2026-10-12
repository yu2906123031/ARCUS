#!/bin/sh
set -eu
day=$(date +%u)
case "$day" in
  6|7) config=/root/ARCUS/mm_live_btc_account1.json ;;
  *)   config=/root/ARCUS/mm_live_spy_account1.json ;;
esac
exec /root/.hermes/hermes-agent/venv/bin/python3 -u -m arcus_mm run \
  --config "$config" --live --auto-resume --auto-restart --seconds 0 --flatten-on-exit
