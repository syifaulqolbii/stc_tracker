#!/usr/bin/env bash
# watchdog.sh — cron */5. Restart hanya bila 2x gagal berturut-turut.
set -uo pipefail
APP_DIR="${APP_DIR:-/apps/www/it-tracker}"
STATE="$APP_DIR/run/watchdog.fail"
PORT="${PORT:-8000}"
BASE="http://127.0.0.1:$PORT/health"   # langsung ke uvicorn: TANPA prefix

# -m 45: /health bisa lambat saat DB bermasalah (dibuktikan ~32 s di uji lokal);
# timeout 8 s membuat watchdog me-restart app sehat dan tidak pernah stabil.
code=$(curl -s -o /dev/null -m 45 -w '%{http_code}' "$BASE" 2>/dev/null)
[[ -z "$code" || "$code" == "000" ]] && code=000
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
