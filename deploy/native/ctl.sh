#!/usr/bin/env bash
# ctl.sh — start/stop/restart/status uvicorn (tanpa systemd, tanpa root).
set -uo pipefail

APP_DIR="${APP_DIR:-/apps/www/it-tracker}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
PID_FILE="$APP_DIR/run/uvicorn.pid"
LOG_FILE="$APP_DIR/logs/app.log"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-2}"
ROOT_PATH="${ROOT_PATH:-/it-tracker}"

mkdir -p "$APP_DIR/run" "$APP_DIR/logs"

is_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid; pid=$(cat "$PID_FILE" 2>/dev/null) || return 1
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

start() {
  if is_running; then echo "already running (pid $(cat "$PID_FILE"))"; return 0; fi
  if [[ ! -x "$APP_DIR/venv/bin/uvicorn" ]]; then echo "venv belum ada"; return 1; fi
  if [[ ! -r "$ENV_FILE" ]]; then echo "env tidak terbaca: $ENV_FILE"; return 1; fi
  cd "$APP_DIR" || return 1
  set -a; . "$ENV_FILE"; set +a
  nohup "$APP_DIR/venv/bin/uvicorn" main-v1-1:app \
    --host 127.0.0.1 --port "$PORT" \
    --root-path "$ROOT_PATH" \
    --workers "$WORKERS" \
    --proxy-headers --forwarded-allow-ips=127.0.0.1 \
    --log-level info >>"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  sleep 3
  if is_running; then echo "started (pid $(cat "$PID_FILE"))"; else
    echo "GAGAL start — lihat $LOG_FILE"; tail -20 "$LOG_FILE"; return 1; fi
}

stop() {
  if ! is_running; then echo "not running"; rm -f "$PID_FILE"; return 0; fi
  local pid; pid=$(cat "$PID_FILE")
  kill "$pid" 2>/dev/null
  for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
  rm -f "$PID_FILE"; echo "stopped"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) if is_running; then echo "running (pid $(cat "$PID_FILE"))"; else echo "not running"; exit 3; fi ;;
  *) echo "usage: $0 {start|stop|restart|status}"; exit 2 ;;
esac
