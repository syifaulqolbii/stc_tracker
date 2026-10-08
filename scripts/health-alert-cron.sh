#!/usr/bin/env bash
# =============================================================================
# health-alert-cron.sh — panggil POST /api/health/alert tiap 5 menit (cron).
# Kalau backend tidak merespons, kirim alert "Backend DOWN" langsung ke
# Telegram (fallback saat backend sendiri mati).
# Usage:
#   */5 * * * * cd ~/stc_tracker && bash scripts/health-alert-cron.sh >> backups/health-alert.log 2>&1
# =============================================================================
set -uo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && export $(grep -v '^#' .env | xargs)

RESP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
  -X POST http://localhost:8000/api/health/alert -H "X-API-Key: ${BACKEND_API_KEY:-}")

if [ "$RESP" != "200" ]; then
  curl -s -o /dev/null \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=🚨 [STC Tracker] Backend DOWN — health alert tidak merespons (HTTP ${RESP:-timeout})"
fi
