#!/usr/bin/env bash
# =============================================================================
# update-waha.sh — maintenance bulanan WAHA (v1.26 era, 2026-09-25)
#
# Kenapa: engine WEBJS = Chrome headless yang menjalankan WhatsApp Web.
# WhatsApp bisa mengubah JS server-side kapan pun; gejala stagnan image:
#   sendText OK (201) tapi sendImage/sendFile 500
#   dengan error berisi "static.whatsapp.net" / "must include an id property".
# Solusinya hampir selalu: update image WAHA.
#
# Apa yang dilakukan script ini:
#   1. catat versi & config lama
#   2. docker pull image terbaru
#   3. kalau image berubah → stop+rm container, recreate dengan config
#      IDENTIK (env, volume sessions, network stc_tracker_moban-net)
#   4. verifikasi: session WORKING + health backend via waha
#   5. kalau image tidak berubah → tidak ada yang di-recreate (aman)
#
# Aman dijalankan otomatis (cron): session tersimpan di named volume
# waha_waha_sessions — recreate TIDAK menghapus session/QR.
#
# Usage:
#   bash scripts/update-waha.sh            # manual
#   bash scripts/update-waha.sh --dry-run  # hanya cek, tidak mengubah apa pun
# =============================================================================
set -uo pipefail

IMAGE="devlikeapro/waha:chrome"
CONTAINER="waha"
SESSIONS_VOLUME="waha_waha_sessions"
NETWORK_APP="stc_tracker_moban-net"   # network compose stc_tracker (backend -> http://waha:3000)
LOG_PREFIX="[update-waha]"

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

log() { echo "$LOG_PREFIX $(date '+%F %T') $*"; }

# ---------------------------------------------------------------- 0. pre-flight
if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  log "ERROR: container $CONTAINER tidak ada — jalankan manual, script hanya untuk update container yang sudah ada."
  exit 1
fi

OLD_IMAGE_ID=$(docker inspect "$CONTAINER" --format '{{.Image}}')
OLD_VERSION=$(docker exec "$CONTAINER" wget -qO- http://localhost:3000/api/version 2>/dev/null || echo "?")
log "versi lama: $OLD_VERSION"

# ---------------------------------------------------------------- 1. pull
log "docker pull $IMAGE ..."
docker pull "$IMAGE" >/dev/null 2>&1 || { log "ERROR: pull gagal (network?)"; exit 1; }
NEW_IMAGE_ID=$(docker image inspect "$IMAGE" --format '{{.Id}}')

if [[ "$OLD_IMAGE_ID" == "$NEW_IMAGE_ID" ]]; then
  log "image tidak berubah — WAHA sudah terbaru ($OLD_VERSION). Selesai, tanpa downtime."
  exit 0
fi
log "image BARU terdeteksi — lanjut recreate..."

if $DRY_RUN; then
  log "(dry-run) berhenti di sini — jalankan tanpa --dry-run untuk menerapkan."
  exit 0
fi

# ---------------------------------------------------------------- 2. backup env lama
ENV_FILE=$(mktemp)
docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' > "$ENV_FILE"

get_env() {  # get_env KEY — ambil nilai env dari container lama
  grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-
}

WAHA_API_KEY_VAL=$(get_env WAHA_API_KEY)
DASH_USER=$(get_env WAHA_DASHBOARD_USERNAME)
DASH_PASS=$(get_env WAHA_DASHBOARD_PASSWORD)
SWAG_USER=$(get_env WHATSAPP_SWAGGER_USERNAME)
SWAG_PASS=$(get_env WHATSAPP_SWAGGER_PASSWORD)
TZ_VAL=$(get_env TZ)
ENGINE_VAL=$(get_env WHATSAPP_DEFAULT_ENGINE)
[[ -z "$ENGINE_VAL" ]] && ENGINE_VAL="WEBJS"

log "config lama ter-capture: dashboard=$DASH_USER engine=$ENGINE_VAL"

# ---------------------------------------------------------------- 3. recreate
docker stop "$CONTAINER" >/dev/null
docker rm "$CONTAINER" >/dev/null

docker run -d --name "$CONTAINER" \
  --restart unless-stopped \
  -p 127.0.0.1:3000:3000 \
  -v "$SESSIONS_VOLUME:/app/.sessions" \
  -e "WAHA_API_KEY=$WAHA_API_KEY_VAL" \
  -e "WAHA_DASHBOARD_USERNAME=$DASH_USER" \
  -e "WAHA_DASHBOARD_PASSWORD=$DASH_PASS" \
  -e "WHATSAPP_SWAGGER_USERNAME=$SWAG_USER" \
  -e "WHATSAPP_SWAGGER_PASSWORD=$SWAG_PASS" \
  -e "TZ=${TZ_VAL:-Asia/Jakarta}" \
  -e "WHATSAPP_DEFAULT_ENGINE=$ENGINE_VAL" \
  "$IMAGE" >/dev/null || { log "ERROR: recreate gagal — CEK SEGERA, WAHA DOWN"; exit 1; }

# sambungkan ke network backend (compose stc_tracker)
docker network connect "$NETWORK_APP" "$CONTAINER" 2>/dev/null \
  || log "WARN: tidak bisa connect ke $NETWORK_APP (mungkin sudah terhubung)"

# ---------------------------------------------------------------- 4. verifikasi
log "menunggu WAHA siap..."
for i in $(seq 1 12); do   # maks 60 detik
  sleep 5
  STATUS=$(docker exec "$CONTAINER" wget -qO- http://localhost:3000/api/sessions 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(','.join(f\"{s['name']}:{s['status']}\" for s in d))" 2>/dev/null || true)
  [[ -n "$STATUS" ]] && break
done

NEW_VERSION=$(docker exec "$CONTAINER" wget -qO- http://localhost:3000/api/version 2>/dev/null || echo "?")
log "versi baru: $NEW_VERSION | sessions: ${STATUS:-TIDAK TERBACA}"

if echo "$STATUS" | grep -q "WORKING"; then
  log "session OK (WORKING) — session/QR tidak terpengaruh ✅"
else
  log "PERINGATAN: session belum WORKING (${STATUS:-timeout}) — cek dashboard WAHA, mungkin perlu scan QR ulang."
fi

# verifikasi dari sisi backend (health endpoint mengecek waha)
HEALTH=$(curl -s --max-time 10 http://localhost:8000/health 2>/dev/null || true)
log "health backend: ${HEALTH:-gagal}"
echo "$HEALTH" | grep -q '"waha":"ok"' \
  && log "SELESAI ✅ WAHA terbaru & terhubung ke backend" \
  || log "PERINGATAN: backend belum melihat WAHA ok — cek network/$NETWORK_APP"

rm -f "$ENV_FILE"
