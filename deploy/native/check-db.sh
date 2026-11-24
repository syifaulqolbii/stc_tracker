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
env_path = os.path.join(app_dir, ".env")
if not dsn and os.path.isfile(env_path):
    for line in open(env_path).read().splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            dsn = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
if not dsn:
    dsn = os.environ.get("DATABASE_URL", "")
if not dsn:
    sys.exit(f"FAIL: DATABASE_URL tidak ditemukan di {env_path} maupun environment "
             f"— buat .env (runbook 3E) atau jalankan: check-db.sh '<dsn>'")

q = lambda cur, sql: (cur.execute(sql), cur.fetchall())[1]
EXPECTED = {"alert_state", "areas", "cases", "jenis_cases", "progress_updates",
            "regionals", "reminder_log", "solver_contacts", "sumber_tickets",
            "wa_groups", "wa_messages"}
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
    if len(tables) >= 11 and set(tables) <= EXPECTED:
        print("sumber_tickets:", [r[0] for r in q(cur, "select name from dc_schema.sumber_tickets order by 1")])
        print("jenis_cases:", [r[0] for r in q(cur, "select name from dc_schema.jenis_cases order by 1")])
        print("alert_state rows:", q(cur, "select count(*) from dc_schema.alert_state")[0][0])

    # Gate Task 2 — dua kondisi yang harus menghentikan deploy, bukan sekadar dicetak.
    extra = sorted(set(tables) - EXPECTED)
    if extra:
        sys.exit(f"FAIL: dc_schema berisi tabel di luar milik app ({', '.join(extra)})"
                 f" — schema dipakai aplikasi lain, jangan bootstrap")
    if leak:
        sys.exit(f"FAIL: {leak} objek app bocor ke schema public"
                 f" — bersihkan dulu, bootstrap akan membuat duplikat di dc_schema")
    if not use or not create:
        sys.exit("FAIL: role tidak punya USAGE/CREATE pada dc_schema — minta DBA")
    if set(tables) == EXPECTED:
        print("gate: OK (sudah ter-bootstrap)")
    elif tables:
        missing = sorted(EXPECTED - set(tables))
        print(f"gate: OK (bootstrap parsial — {len(tables)}/11 tabel, kurang: {', '.join(missing)})"
              f" — ulangi apply-db-bootstrap.py (idempoten)")
    else:
        print("gate: OK (siap bootstrap)")
PY

