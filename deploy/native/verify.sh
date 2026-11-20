#!/usr/bin/env bash
# verify.sh [base_url] — smoke test berurutan, berhenti di kegagalan pertama.
set -uo pipefail
BASE="${1:-http://127.0.0.1:8000}"
CURL=(curl -sk -m 10)

fail() { echo "FAIL: $1"; exit 1; }

code=$("${CURL[@]}" -o /tmp/it-health.json -w '%{http_code}' "$BASE/health") || fail "health tidak reachable"
[[ "$code" == "200" ]] || fail "health HTTP $code"
/usr/bin/python3 - /tmp/it-health.json <<'PY' || fail "health body tidak sesuai"
import json, sys
d = json.load(open(sys.argv[1]))
assert d["db"]["status"] == "ok", "db bukan ok"
print("WARN: status degraded (WAHA belum siap)" if d["status"] != "ok" else "health ok")
PY

[[ "$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE/docs")" == "200" ]] || fail "docs"
[[ "$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE/openapi.json")" == "200" ]] || fail "openapi"
[[ "$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE/api/cases")" == "401" ]] || fail "auth tidak menolak tanpa API key"

echo "OK: $BASE"
