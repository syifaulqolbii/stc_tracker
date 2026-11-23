#!/usr/bin/env bash
# verify.sh [base_url] — smoke test berurutan, berhenti di kegagalan pertama.
set -uo pipefail
BASE="${1:-http://127.0.0.1:8000}"
# -m 60: /health bisa ~32 s saat DB bermasalah; app mati tetap gagal cepat
# (connection refused), jadi timeout besar tidak menyembunyikan kegagalan.
CURL=(curl -sk -m 60)
# pilih interpreter python yang benar-benar jalan (di Windows, alias python3
# Microsoft Store hanya stub yang gagal saat dipanggil).
PY3=""
for c in /usr/bin/python3 python3 python; do
  if "$c" -c "" >/dev/null 2>&1; then PY3="$c"; break; fi
done
[[ -n "$PY3" ]] || { echo "FAIL: python3 tidak ada"; exit 1; }

fail() { echo "FAIL: $1"; exit 1; }

code=$("${CURL[@]}" -o /tmp/it-health.json -w '%{http_code}' "$BASE/health") || fail "health tidak reachable"
[[ "$code" == "200" ]] || fail "health HTTP $code"
"$PY3" - /tmp/it-health.json <<'PY' || fail "health body tidak sesuai"
import json, sys
d = json.load(open(sys.argv[1]))
assert d["db"]["status"] == "ok", "db bukan ok"
print("WARN: status degraded (WAHA belum siap)" if d["status"] != "ok" else "health ok")
PY

[[ "$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE/docs")" == "200" ]] || fail "docs"
[[ "$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE/openapi.json")" == "200" ]] || fail "openapi"
[[ "$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE/api/cases")" == "401" ]] || fail "auth tidak menolak tanpa API key"

echo "OK: $BASE"
