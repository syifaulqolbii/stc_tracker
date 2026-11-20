#!/usr/bin/env python
"""Terapkan db-bootstrap.sql tanpa psql (pakai psycopg3 dari venv app).

Dipakai di VM intranet karena role `apps` tidak punya psql dan tidak ada root.

  venv/bin/python scripts/apply-db-bootstrap.py db-bootstrap.sql
  # kredensial dari DATABASE_URL di .env, atau dari --dsn

Seluruh file dieksekusi sebagai satu batch (simple query protocol), jadi
`SET search_path` + `BEGIN ... COMMIT` di dalamnya berlaku apa adanya.
Verifikasi sesudahnya dilakukan oleh pemanggil (lihat Task 2 Step 4).
"""
import argparse
import os
import sys
from pathlib import Path


def load_dsn(args) -> str:
    if args.dsn:
        return args.dsn
    dsn = os.getenv("DATABASE_URL", "")
    if dsn:
        return dsn
    env_file = Path(args.env)
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("DATABASE_URL tidak ditemukan (env, .env, atau --dsn)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sql_file", nargs="?", default="db-bootstrap.sql")
    ap.add_argument("--dsn", default="")
    ap.add_argument("--env", default=".env")
    args = ap.parse_args()

    sql = Path(args.sql_file).read_text()
    if "search_path" not in sql.lower():
        sys.exit(f"{args.sql_file}: tidak ada SET search_path — batalkan")

    import psycopg

    dsn = load_dsn(args)
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("select current_setting('search_path')")
            print("search_path:", cur.fetchone()[0])
    print(f"applied {args.sql_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
