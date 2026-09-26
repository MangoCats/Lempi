#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Record the SHA-256 of every file the catalogue holds `[REQ-AND-960]`.

The one-time pass the maintainer decided on 2026-09-26: `files.sha256` for
every file already inducted, so a phone's byte hashes can be matched exactly
`[REQ-AND-288]`. `ingest_folder.py` sets it at induction from then on.

Reads every file once. At the desktop's 44 GB that is minutes on a local disk,
so it commits as it goes and a second run takes up only what the first did
not finish. A row whose file is not at its recorded path is left NULL and
reported, never guessed at: relink is what finds a moved file `[SPEC-RLK-090]`.

Usage:
  python tools/add_byte_hashes.py <library.db> [--write]

Without --write it reports what it would do and writes nothing, not even the
column.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
from byte_hash import ensure_sha256_column, sha256_file  # noqa: E402

BATCH = 50


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def main(argv: list[str]) -> int:
    flags = [a for a in argv if a.startswith("-")]
    paths = [a for a in argv if not a.startswith("-")]
    # An unknown flag is refused, not ignored: `add_fade_columns.py` once took
    # a mistyped `--commit`, did a dry run, and exited 0 -- which reads
    # exactly like having written (CLAUDE.md section 6).
    unknown = [f for f in flags if f != "--write"]
    if len(paths) != 1 or unknown:
        say(__doc__)
        if unknown:
            say(f"unknown option(s): {' '.join(unknown)}")
        return 2
    write = "--write" in flags
    conn = lempi_db.connect(paths[0], lempi_db.ROLE_LIBRARY, writable=write)
    have = {r[1] for r in conn.execute("PRAGMA table_info(files)")}
    if "sha256" in have:
        todo = conn.execute(
            "SELECT file_id, path, size_bytes FROM files WHERE sha256 IS NULL").fetchall()
        done = conn.execute("SELECT COUNT(*) FROM files WHERE sha256 IS NOT NULL").fetchone()[0]
    else:
        todo = conn.execute("SELECT file_id, path, size_bytes FROM files").fetchall()
        done = 0
    present = [(fid, p, n) for fid, p, n in todo if os.path.isfile(p)]
    absent = [p for _, p, _ in todo if not os.path.isfile(p)]
    total = sum(n or 0 for _, _, n in present)
    say(f"files: {done} already hashed, {len(present)} to hash "
        f"({total / 1e9:.1f} GB), {len(absent)} not at their recorded path")
    for p in absent[:10]:
        say(f"  absent  {p}")
    if len(absent) > 10:
        say(f"  ... and {len(absent) - 10} more")
    if not write:
        say("\n(dry run -- pass --write to apply)")
        return 0

    ensure_sha256_column(conn)
    conn.commit()
    t0 = time.time()
    hashed = failed = 0
    for i, (fid, path, _) in enumerate(present, 1):
        try:
            conn.execute("UPDATE files SET sha256 = ?1 WHERE file_id = ?2",
                         (sha256_file(path), fid))
            hashed += 1
        except OSError as e:
            say(f"  unreadable  {path}: {e}")
            failed += 1
        if i % BATCH == 0:
            conn.commit()
            say(f"  {i}/{len(present)}  {time.time() - t0:.0f}s")
    conn.commit()
    say(f"hashed {hashed}, unreadable {failed}, absent {len(absent)}, "
        f"in {time.time() - t0:.0f}s")
    # Absent or unreadable files are not a failure of this pass, but they are
    # not hashed either, and a zero status must not hide that (section 6).
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
