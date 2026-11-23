#!/usr/bin/env python
"""Bangun it-tracker-src.tgz deterministik dari isi repo.

Kenapa bukan `git archive`: tar dari git menyematkan commit SHA di pax global
header, jadi hash berubah setiap commit walau isi file sama. Skrip ini menulis
tar sendiri dengan mtime/mode/uid tetap, sehingga hash hanya berubah kalau isi
file yang ikut berubah — aman dipakai sebagai pin verifikasi di VM.

  python scripts/build-src-tarball.py offline/it-tracker-src.tgz
"""
import gzip
import io
import os
import subprocess
import sys
import tarfile

# Dokumentasi & CI tidak ikut ke VM: isinya tidak dipakai app, dan menambah
# noise pada verifikasi hash.
EXCLUDE_PREFIX = ("docs/", ".github/", "offline/", "tests/", "temp/", ".worktrees/")
FIXED_MTIME = 1763596800  # 2026-11-20 00:00:00 UTC
# File non-.sh/.py yang juga harus executable.
EXEC_EXTRA = frozenset()


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
    names = [n.decode() for n in out.split(b"\0") if n]
    return [n for n in names if not n.startswith(EXCLUDE_PREFIX)]


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit("usage: build-src-tarball.py <output.tgz>")
    out_path = sys.argv[1]

    files = [f for f in tracked_files() if os.path.isfile(f)]
    if "main-v1-1.py" not in files:
        sys.exit("main-v1-1.py tidak ada — jalankan dari root repo")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    # Tulis tar ke memori lalu gzip dengan mtime=0: header gzip standar
    # menyematkan waktu build, yang membuat hash berubah tiap kali dijalankan.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name in sorted(files):
            info = tf.gettarinfo(name, arcname=name)
            info.mtime = FIXED_MTIME
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            # Skrip tetap executable, sisanya 644.
            info.mode = 0o755 if name.endswith((".sh", ".py")) or name in EXEC_EXTRA else 0o644
            with open(name, "rb") as fh:
                tf.addfile(info, fh)
    with open(out_path, "wb") as out, gzip.GzipFile(
        fileobj=out, mode="wb", compresslevel=9, mtime=0
    ) as gz:
        gz.write(buf.getvalue())

    print(f"wrote {out_path} ({len(files)} file, {os.path.getsize(out_path)} byte)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
