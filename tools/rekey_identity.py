#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Re-key the library to the player's own identity hash [SPEC-RLK-150].

Hashes every catalogued file with Lempi's `hash_audio` -- the MD5 of its
encoded packets as Symphonia reads them -- and where that differs from the
stored `audio_md5`, rewrites the value in every table keyed by it, in both
halves, in one transaction. A half-applied re-key would orphan every cache
while looking merely cold, so it is all or nothing.

What it records:
  files.md5_generator   the hasher's name@version, on every row it hashed --
                        a measurement just made, not an inference about who
                        wrote the value [SPEC-SC-038]
  audio_md5_aliases     old -> new for each key that changed, so a copy still
                        holding the old one can be translated rather than read
                        as a different recording [SPEC-RLK-155]

A file that cannot be hashed keeps its key and its generator, and is listed.
A file not at its recorded path is left alone: relink finds moved files
[SPEC-RLK-090].

Usage:
  python tools/rekey_identity.py <library.db> [--write]

Without --write it hashes, reports what would change, and writes nothing.
With --write it first copies both halves to backups/pre-rekey-<UTC>/ beside them,
checked, and afterwards re-reads every table to prove no old key remains.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
from ingest_folder import hash_audio_binary, md5_generator  # noqa: E402

ALIASES = """CREATE TABLE IF NOT EXISTS audio_md5_aliases (
    old_md5    TEXT PRIMARY KEY,
    new_md5    TEXT NOT NULL,
    generator  TEXT NOT NULL,
    rekeyed_at TEXT NOT NULL
)"""


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(str(text).encode(enc, "replace").decode(enc), flush=True)


def keyed_tables(conn) -> list[tuple[str, str]]:
    """Every (schema, table) with an `audio_md5` column, in both halves."""
    out = []
    for (schema,) in [(r[1],) for r in conn.execute("PRAGMA database_list")]:
        for (t,) in conn.execute(f"SELECT name FROM \"{schema}\".sqlite_master WHERE type='table'"):
            if t == "audio_md5_aliases":
                continue
            if "audio_md5" in {r[1] for r in conn.execute(f'PRAGMA "{schema}".table_info("{t}")')}:
                out.append((schema, t))
    return out


def hash_all(paths: list[str]) -> dict[str, str]:
    """path -> audio_md5, or 'ERR <reason>'. One `hash_audio` process for the
    lot, paths fed from a file so neither pipe can fill and stall."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as f:
        f.write("".join(p + "\n" for p in paths))
        listing = f.name
    try:
        with open(listing, encoding="utf-8") as stdin:
            r = subprocess.run([hash_audio_binary(), "--stdin"], stdin=stdin,
                               capture_output=True, encoding="utf-8")
    finally:
        os.unlink(listing)
    if r.returncode != 0:
        raise SystemExit(f"hash_audio exited {r.returncode}: {r.stderr.strip()}")
    out = {}
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if parts[0] == "ERR":
            out[parts[1]] = "ERR " + (parts[2] if len(parts) > 2 else "")
        else:
            out[parts[1]] = parts[0]
    missing = [p for p in paths if p not in out]
    if missing:
        # Every path must come back answered. One that did not is a hasher
        # that stopped early, and must not read as "unchanged" (section 6).
        raise SystemExit(f"hash_audio answered {len(out)} of {len(paths)}; first unanswered: {missing[0]}")
    return out


def backup(conn, dest_dir: str) -> None:
    os.makedirs(dest_dir)
    for schema, path in [(r[1], r[2]) for r in conn.execute("PRAGMA database_list")]:
        dest = os.path.join(dest_dir, os.path.basename(path))
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        out = sqlite3.connect(dest)
        src.backup(out)
        ok = out.execute("PRAGMA integrity_check").fetchone()[0]
        n = out.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
        out.close()
        src.close()
        if ok != "ok" or n == 0:
            raise SystemExit(f"backup of {schema} to {dest} failed its check: {ok}, {n} objects")
        say(f"  backed up {schema:8} -> {dest} ({os.path.getsize(dest) / 1e6:.0f} MB, integrity ok)")


def main(argv: list[str]) -> int:
    flags = [a for a in argv if a.startswith("-")]
    paths = [a for a in argv if not a.startswith("-")]
    unknown = [f for f in flags if f != "--write"]
    if len(paths) != 1 or unknown:
        say(__doc__)
        if unknown:
            say(f"unknown option(s): {' '.join(unknown)}")
        return 2
    write = "--write" in flags
    generator = md5_generator()
    conn = lempi_db.connect(paths[0], lempi_db.ROLE_LIBRARY, writable=write, peer_writable=write)
    tables = keyed_tables(conn)
    say(f"hasher: {generator} ({hash_audio_binary()})")
    say("keyed by audio_md5: " + ", ".join(f"{s}.{t}" for s, t in tables))

    rows = conn.execute("SELECT file_id, audio_md5, path FROM files").fetchall()
    present = [r for r in rows if os.path.isfile(r[2])]
    absent = [r for r in rows if not os.path.isfile(r[2])]
    say(f"files: {len(rows)}, {len(present)} at their path, {len(absent)} not")
    t0 = time.time()
    got = hash_all([p for _, _, p in present])
    say(f"hashed {len(present)} in {time.time() - t0:.0f}s")

    same, changed, failed = [], [], []
    for fid, old, path in present:
        h = got[path]
        (failed if h.startswith("ERR") else same if h == old else changed).append((fid, old, path, h))
    new_keys = [h for _, _, _, h in changed]
    held = {r[1] for r in rows}
    clash = [h for h in new_keys if h in held] + [h for h in set(new_keys) if new_keys.count(h) > 1]
    say(f"  unchanged {len(same)}   changed {len(changed)}   cannot hash {len(failed)}   absent {len(absent)}")
    for _, _, path, h in failed:
        say(f"  cannot hash  {path}: {h[4:]}")
    for _, _, path in absent[:10]:
        say(f"  absent       {path}")
    if clash:
        # Two files becoming one identity would merge two recordings' rows.
        # Nothing is written; a person decides.
        say(f"REFUSED: {len(clash)} new key(s) already held or repeated, e.g. {clash[:3]}")
        return 1

    affected = {}
    olds = [o for _, o, _, _ in changed]
    for s, t in tables:
        n = 0
        for i in range(0, len(olds), 500):
            chunk = olds[i:i + 500]
            n += conn.execute(f'SELECT COUNT(*) FROM "{s}"."{t}" WHERE audio_md5 IN '
                              f'({",".join("?" * len(chunk))})', chunk).fetchone()[0]
        affected[(s, t)] = n
        say(f"  {s}.{t}: {n} row(s) keyed by a changing value")
    if not write:
        say("\n(dry run -- pass --write to apply)")
        return 0

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%MZ")
    dest = os.path.join(os.path.dirname(os.path.abspath(paths[0])), "backups", f"pre-rekey-{stamp}")
    say(f"\nbacking up to {dest}")
    backup(conn, dest)

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(ALIASES.replace("audio_md5_aliases", "main.audio_md5_aliases", 1))
        for fid, old, _, new in changed:
            for s, t in tables:
                conn.execute(f'UPDATE "{s}"."{t}" SET audio_md5 = ?1 WHERE audio_md5 = ?2', (new, old))
            conn.execute("INSERT OR REPLACE INTO main.audio_md5_aliases VALUES (?1, ?2, ?3, ?4)",
                         (old, new, generator, now))
            # A key that changes back later must not leave a chain pointing at
            # itself: an alias whose target is now retired follows it.
            conn.execute("UPDATE main.audio_md5_aliases SET new_md5 = ?1 WHERE new_md5 = ?2", (new, old))
        for fid, _, _, _ in same + changed:
            conn.execute("UPDATE main.files SET md5_generator = ?1 WHERE file_id = ?2", (generator, fid))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    # Verify what landed, not the absence of an exception (section 6).
    bad = 0
    for fid, _, path, h in same + changed:
        k, g = conn.execute("SELECT audio_md5, md5_generator FROM main.files WHERE file_id = ?1",
                            (fid,)).fetchone()
        if k != h or g != generator:
            bad += 1
            say(f"  MISMATCH after write: {path}: {k} / {g}")
    for s, t in tables:
        for i in range(0, len(olds), 500):
            chunk = olds[i:i + 500]
            n = conn.execute(f'SELECT COUNT(*) FROM "{s}"."{t}" WHERE audio_md5 IN '
                             f'({",".join("?" * len(chunk))})', chunk).fetchone()[0]
            if n:
                bad += n
                say(f"  {s}.{t} still holds {n} old key(s)")
    for (schema,) in [(r[1],) for r in conn.execute("PRAGMA database_list")]:
        ok = conn.execute(f'PRAGMA "{schema}".integrity_check').fetchone()[0]
        say(f"  integrity {schema}: {ok}")
        bad += ok != "ok"
    n_alias = conn.execute("SELECT COUNT(*) FROM main.audio_md5_aliases").fetchone()[0]
    say(f"re-keyed {len(changed)} file(s), {sum(affected.values())} row(s); "
        f"{len(same) + len(changed)} now record {generator}; {n_alias} alias(es) held")
    if bad:
        say(f"VERIFY FAILED: {bad} problem(s). The backup is {dest}")
        return 1
    # A file that could not be hashed is not a failure of the re-key, but it
    # is not re-keyed either, and a zero status must not hide it (section 6).
    return 3 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
