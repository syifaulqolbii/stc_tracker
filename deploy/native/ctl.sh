#!/usr/bin/env bash
# ctl.sh — start/stop/restart/status uvicorn (tanpa systemd, tanpa root).
#
# Dua penjagaan penting (dua-duanya pernah gagal di uji nyata):
#  1. --workers > 1 membuat uvicorn punya proses induk + anak. `kill -9` pada
#     induk meninggalkan anak yang tetap memegang port; instance baru lalu
#     listen di port yang sama. Karena itu stop() mematikan proses group, dan
#     start() menolak jalan bila port sudah dipegang proses lain.
#  2. start() menunggu /health, bukan sekadar "proses hidup" — uvicorn bisa
#     hidup tapi belum siap menerima request.
set -uo pipefail

APP_DIR="${APP_DIR:-/apps/www/it-tracker}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
PID_FILE="$APP_DIR/run/uvicorn.pid"
LOG_FILE="$APP_DIR/logs/app.log"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-2}"
ROOT_PATH="${ROOT_PATH:-/it-tracker}"
HEALTH_WAIT="${HEALTH_WAIT:-45}"      # detik menunggu /health setelah start

mkdir -p "$APP_DIR/run" "$APP_DIR/logs"

pid_alive() { [[ -n "${1:-}" ]] && kill -0 "$1" 2>/dev/null; }

is_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid; pid=$(cat "$PID_FILE" 2>/dev/null) || return 1
  pid_alive "$pid"
}

# Ada yang listen di port ini? curl paling portabel (dipakai juga oleh
# watchdog); /dev/tcp dipakai bila tersedia. 000 = tidak ada yang menjawab.
port_busy() {
  local code
  code=$(curl -s -o /dev/null -m 2 -w '%{http_code}' "http://127.0.0.1:$PORT/" 2>/dev/null || true)
  [[ -n "$code" && "$code" != "000" ]] && return 0
  (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && { exec 3>&-; return 0; }
  return 1
}

# Proses uvicorn app ini milik user yang sama (induk maupun worker yatim).
# Kosong bila pgrep tidak tersedia (mis. Windows) — stop() lalu hanya bisa
# mematikan process group dari pidfile.
app_pids() {
  command -v pgrep >/dev/null 2>&1 || return 0
  # `-u` mempersempit ke milik user ini; sebagian implementasi pgrep
  # (mis. Windows) tidak mendukungnya, jadi jatuh ke pola saja.
  pgrep -u "$(id -u)" -f "uvicorn main-v1-1:app" 2>/dev/null \
    || pgrep -f "uvicorn main-v1-1:app" 2>/dev/null || true
}

kill_tree() {
  local sig="$1" pid="$2"
  kill "-$sig" -- "-$pid" 2>/dev/null || kill "-$sig" "$pid" 2>/dev/null
  local kid
  for kid in $(pgrep -P "$pid" 2>/dev/null); do kill "-$sig" "$kid" 2>/dev/null; done
}

wait_gone() {
  local i
  for i in $(seq 1 20); do
    port_busy || return 0
    sleep 0.5
  done
  return 1
}

start() {
  if is_running; then echo "already running (pid $(cat "$PID_FILE"))"; return 0; fi
  if [[ ! -x "$APP_DIR/venv/bin/uvicorn" ]]; then echo "venv belum ada"; return 1; fi
  if [[ ! -r "$ENV_FILE" ]]; then echo "env tidak terbaca: $ENV_FILE"; return 1; fi
  if port_busy; then
    echo "port $PORT sudah dipakai proses lain (kemungkinan worker yatim)"
    echo "jalankan: $0 stop     atau: pkill -u \$(id -u) -f 'uvicorn main-v1-1:app'"
    return 1
  fi
  cd "$APP_DIR" || return 1
  set -a; . "$ENV_FILE"; set +a
  # setsid: satu process group untuk induk+worker, supaya stop() bisa
  # mematikan semuanya meski induk sudah mati.
  if command -v setsid >/dev/null 2>&1; then
    nohup setsid "$APP_DIR/venv/bin/uvicorn" main-v1-1:app \
      --host 127.0.0.1 --port "$PORT" \
      --root-path "$ROOT_PATH" \
      --workers "$WORKERS" \
      --proxy-headers --forwarded-allow-ips=127.0.0.1 \
      --log-level info >>"$LOG_FILE" 2>&1 &
  else
    nohup "$APP_DIR/venv/bin/uvicorn" main-v1-1:app \
      --host 127.0.0.1 --port "$PORT" \
      --root-path "$ROOT_PATH" \
      --workers "$WORKERS" \
      --proxy-headers --forwarded-allow-ips=127.0.0.1 \
      --log-level info >>"$LOG_FILE" 2>&1 &
  fi
  echo $! > "$PID_FILE"

  # Anggaran waktu nyata (detik), bukan jumlah iterasi: /health bisa lambat
  # (~32 s saat DB bermasalah) sehingga tiap iterasi tidak konstan.
  local deadline=$((SECONDS + HEALTH_WAIT)) code=000
  while (( SECONDS < deadline )); do
    code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$PORT/health" 2>/dev/null || true)
    [[ -z "$code" ]] && code=000
    [[ "$code" == "200" ]] && break
    if ! is_running && ! port_busy; then
      echo "GAGAL start — proses mati"; tail -20 "$LOG_FILE"; rm -f "$PID_FILE"; return 1
    fi
    sleep 1
  done

  if [[ "$code" == "200" ]]; then
    echo "started (pid $(cat "$PID_FILE"))"; return 0
  fi
  echo "GAGAL start — /health tidak 200 dalam ${HEALTH_WAIT}s (terakhir: $code)"
  echo "app masih hidup; cek $LOG_FILE, lalu: $0 stop"
  return 1
}

stop() {
  local pid
  pid=$(cat "$PID_FILE" 2>/dev/null || true)

  if ! is_running && ! port_busy; then echo "not running"; rm -f "$PID_FILE"; return 0; fi

  if pid_alive "$pid"; then kill_tree TERM "$pid"; fi
  wait_gone || true

  # Sisa worker yatim (induk sudah mati) — matikan per pola, hanya milik user ini.
  local p
  for p in $(app_pids); do
    kill -TERM "$p" 2>/dev/null
  done
  if ! wait_gone; then
    for p in $(app_pids); do kill -9 "$p" 2>/dev/null; done
    if pid_alive "$pid"; then kill -9 "$pid" 2>/dev/null; fi
    wait_gone || true
  fi

  rm -f "$PID_FILE"
  if port_busy; then
    echo "PERINGATAN: port $PORT masih dipakai proses lain"
    if ! command -v pgrep >/dev/null 2>&1; then
      echo "pgrep tidak tersedia — matikan manual: ss -ltnp | grep :$PORT"
    fi
    return 1
  fi
  echo "stopped"
}

status() {
  if is_running; then echo "running (pid $(cat "$PID_FILE"))"
  elif port_busy; then echo "running (port $PORT dipakai proses di luar pidfile)"; exit 4
  else echo "not running"; exit 3; fi
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  *) echo "usage: $0 {start|stop|restart|status}"; exit 2 ;;
esac
