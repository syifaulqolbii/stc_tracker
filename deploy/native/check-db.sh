#!/usr/bin/env bash
# check-db.sh [dsn] — verifikasi Task 2 tanpa psql (pakai psycopg3 dari venv app).
# Tanpa argumen memakai DATABASE_URL dari /apps/www/it-tracker/.env.
# Exit non-zero bila gate Task 2 gagal (tabel dc_schema tidak kosong / objek bocor ke public).
set -uo pipefail
APP_DIR="${APP_DIR:-/apps/www/it-tracker}"
PY="$APP_DIR/venv/bin/python"
DSN="${1:-}"

[[ -x "$PY" ]] || { echo "FAIL: $PY tidak ada — jalankan Task 3 dulu"; exit 1; }

exec "$PY" - "$APP_DIR" "$DSN" <<'PY'
import os, sys
import psycopg

app_dir, dsn = sys.argv[1], sys.argv[2]
if not dsn:
    for line in open(os.path.join(app_dir, ".env")).read().splitlines():
        if line.strip().startswith("DATABASE_URL="):
            dsn = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
if not dsn:
    sys.exit("FAIL: DATABASE_URL tidak ditemukan")

q = lambda cur, sql: (cur.execute(sql), cur.fetchall())[1]
with psycopg.connect(dsn, connect_timeout=10) as conn, conn.cursor() as cur:
    user, db, sp = q(cur, "select current_user, current_database(), current_setting('search_path')")[0]
    print(f"user={user} db={db} search_path={sp}")
    exists = q(cur, "select to_regnamespace('dc_schema') is not null")[0][0]
    if not exists:
        sys.exit("FAIL: schema dc_schema belum ada — minta DBA/owner membuatnya, lalu ulangi")
    use, create = q(cur, "select has_schema_privilege(current_user,'dc_schema','USAGE'),"
                         " has_schema_privilege(current_user,'dc_schema','CREATE')")[0]
    print(f"can_use={use} can_create={create}")
    tables = [r[0] for r in q(cur, "select table_name from information_schema.tables"
                                   " where table_schema='dc_schema' order by 1")]
    print(f"dc_schema tables ({len(tables)}): {', '.join(tables) or '-'}")
    leak = q(cur, "select count(*) from information_schema.tables where table_schema='public'"
                  " and table_name in ('cases','wa_messages','wa_groups')")[0][0]
    print(f"public leaks={leak}")
    if len(tables) >= 11:
        print("sumber_tickets:", [r[0] for r in q(cur, "select name from dc_schema.sumber_tickets order by 1")])
        print("jenis_cases:", [r[0] for r in q(cur, "select name from dc_schema.jenis_cases order by 1")])
        print("alert_state rows:", q(cur, "select count(*) from dc_schema.alert_state")[0][0])
PY
