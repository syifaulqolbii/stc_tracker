#!/usr/bin/env bash
# preflight.sh — cek Task 3/4 sebelum start. Satu perintah, ringkasan ringkas.
#
#   APP_DIR=/apps/www/it-tracker /apps/www/it-tracker/deploy/native/preflight.sh
#
# Exit non-zero bila ada cek yang gagal. Tidak mencetak isi .env (hanya nama
# variabel dan hasil parsing DSN tanpa password).
set -uo pipefail
APP_DIR="${APP_DIR:-/apps/www/it-tracker}"
PY310="${PY310:-/apps/Python3.10/bin/python3.10}"
# Direktori kerja untuk file stderr sementara; tanpa ini redirect gagal saat
# `run/` belum ada dan pesan error aslinya hilang.
TMPD="${TMPDIR:-/tmp}"
[[ -d "$TMPD" && -w "$TMPD" ]] || TMPD="$APP_DIR"
mkdir -p "$APP_DIR/run" 2>/dev/null || TMPD="/tmp"
fails=0
warns=0

ok()   { printf '  OK    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; fails=$((fails+1)); }
warn() { printf '  WARN  %s\n' "$*"; warns=$((warns+1)); }

echo "== runtime Python"
if [[ -x "$PY310" ]]; then
  ok "$("$PY310" -V 2>&1 | tr -d '\r' | head -1) @ $PY310"
  if "$PY310" -c "import ssl, venv, ensurepip" 2>/dev/null; then ok "ssl+venv+ensurepip"
  else warn "ensurepip/venv tidak lengkap — lihat catatan 3C di runbook"; fi
else
  bad "tidak ada $PY310"
fi

echo "== isi APP_DIR ($APP_DIR)"
for d in deploy/native scripts wheels logs run media; do
  [[ -d "$APP_DIR/$d" ]] && ok "dir $d" || bad "dir $d hilang"
done
for f in main-v1-1.py templates.py requirements.txt; do
  [[ -f "$APP_DIR/$f" ]] && ok "file $f" || bad "file $f hilang"
done

echo "== wheel bundle"
n=$(ls "$APP_DIR"/wheels/*.whl 2>/dev/null | wc -l)
if [[ "$n" == "35" ]]; then ok "$n wheel"
else bad "$n wheel (harus 35 — bundle lama = 34)"; fi
[[ -f "$APP_DIR/wheels/exceptiongroup-1.3.2-py3-none-any.whl" ]] \
  && ok "exceptiongroup ada" \
  || bad "exceptiongroup TIDAK ada — wajib untuk anyio di Python 3.10"

echo "== skrip executable"
for s in deploy/native/ctl.sh deploy/native/watchdog.sh deploy/native/verify.sh deploy/native/check-db.sh; do
  [[ -x "$APP_DIR/$s" ]] && ok "$s +x" || { chmod +x "$APP_DIR/$s" 2>/dev/null && ok "$s +x (baru di-set)" || bad "$s tidak executable"; }
done

echo "== venv"
VPY="$APP_DIR/venv/bin/python"
if [[ -x "$VPY" ]]; then
  ok "$("$VPY" -V 2>&1 | tr -d '\r' | head -1)"
  err="$(mktemp "${TMPD%/}/pf-imp.XXXXXX" 2>/dev/null || echo "${TMPD%/}/pf-imp.err")"
  if "$VPY" -c "import fastapi, uvicorn, httpx, psycopg, psycopg_pool, jwt, openpyxl, pydantic, exceptiongroup" 2>"$err"; then
    ok "semua import dependency"
  else
    bad "import gagal: $(tr '\n' ' ' < "$err" | tail -c 180)"
  fi
  err="$(mktemp "${TMPD%/}/pf-app.XXXXXX" 2>/dev/null || echo "${TMPD%/}/pf-app.err")"
  if MEDIA_DIR="$APP_DIR/media" "$VPY" -c "import importlib.util as u; s=u.spec_from_file_location('m','main-v1-1.py'); m=u.module_from_spec(s); s.loader.exec_module(m)" 2>"$err"; then
    ok "app import (main-v1-1.py)"
  else
    bad "app import gagal: $(grep -E 'Error|Exception' "$err" | tail -1)"
  fi
  rm -f "$APP_DIR/run/.pf-imp.err" "$APP_DIR/run/.pf-app.err" "$TMPD"/pf-imp.* "$TMPD"/pf-app.* 2>/dev/null || true
else
  bad "venv/bin/python tidak ada — jalankan 3C"
fi

echo "== .env"
if [[ -r "$APP_DIR/.env" ]]; then
  perm=$(stat -c '%a' "$APP_DIR/.env" 2>/dev/null || echo '?')
  [[ "$perm" == "600" ]] && ok ".env mode 600" || warn ".env mode $perm (harusnya 600)"
  for k in DATABASE_URL BACKEND_API_KEY JWT_SECRET WAHA_WEBHOOK_SECRET MEDIA_DIR BACKEND_PUBLIC_URL; do
    grep -q "^$k=" "$APP_DIR/.env" && ok "$k terisi" || bad "$k tidak ada di .env"
  done
  grep -q '^DATABASE_URL=.*search_path' "$APP_DIR/.env" && ok "search_path di DATABASE_URL" || bad "search_path hilang dari DATABASE_URL"
  grep -q '^DATABASE_URL=.*GANTI' "$APP_DIR/.env" && bad "DATABASE_URL masih placeholder GANTI" || true
  if [[ -x "$VPY" ]]; then
    "$VPY" - "$APP_DIR/.env" <<'PY' && ok "DSN ter-parse & host benar"
import sys
from psycopg.conninfo import conninfo_to_dict
for line in open(sys.argv[1]).read().splitlines():
    if line.startswith("DATABASE_URL="):
        dsn = line.split("=", 1)[1].strip().strip("'\"")
        break
else:
    sys.exit(1)
i = conninfo_to_dict(dsn)
print(f"        host={i.get('host')} db={i.get('dbname')} user={i.get('user')} options={i.get('options')}")
sys.exit(0 if i.get("host") == "10.53.142.244" and "dc_schema" in (i.get("options") or "") else 1)
PY
  fi
else
  bad ".env belum ada"
fi

echo "== hasil"
if (( fails )); then
  echo "  $fails gagal, $warns peringatan — jangan lanjut start"
  exit 1
fi
echo "  semua cek inti lulus ($warns peringatan)"
