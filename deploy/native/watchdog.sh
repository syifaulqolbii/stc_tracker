#!/usr/bin/env bash
# watchdog.sh — cron */5. Restart hanya bila 2x gagal berturut-turut.
set -uo pipefail
APP_DIR="${APP_DIR:-/apps/www/it-tracker}"
STATE="$APP_DIR/run/watchdog.fail"
BASE="http://127.0.0.1:8000/health"   # langsung ke uvicorn: TANPA prefix

code=$(curl -s -o /dev/null -m 8 -w '%{http_code}' "$BASE" || echo 000)
if [[ "$code" == "200" ]]; then
  [[ -f "$STATE" ]] && rm -f "$STATE"
  exit 0
fi

n=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STATE"
if (( n >= 2 )); then
  echo "[$(date -Is)] health=$code gagal $n kali — restart" >> "$APP_DIR/logs/watchdog.log"
  "$APP_DIR/deploy/native/ctl.sh" restart >> "$APP_DIR/logs/watchdog.log" 2>&1
  rm -f "$STATE"
fi
exit 0
