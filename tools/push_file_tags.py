#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Push locally-known `file_tags` to a remote installation whose own copy of
a file has an entirely empty one `[SPEC-DF-122]`.

`[REQ-LIB-146]` fixed `ingest_folder.py`'s `probe()` (Ogg Vorbis comments
were never read at all) and `tools/backfill_file_tags.py` repaired the
*local* library, but neither reaches a remote installation like lempipi's
own copy: `export_changes.py`/`apply_changes.py` `[SPEC-DF-120]` only ever
carry the three review-draft tables (`id_reviews`/`boundary_reviews`/
`artist_reviews`) -- raw ingest metadata like `file_tags` was never in that
pipeline's scope. Found live 2026-08-31: lempipi's own mirror of the same
28 files this project's own library had just fixed still showed the
identical gap, which matters there too -- Lempi's own browse page falls
back to `file_tags` for artist/album/title whenever a passage has no linked
MusicBrainz recording (`ARTIST_EXPR`/`ALBUM_EXPR`/`TITLE_EXPR`, `player/src/
db.rs`, `[REQ-VIS-250]`), not only Vipunen's own MusicBrainz-matching tools.

**The remote drives what gets checked, not the local library.** A naive
version would ask "does the remote already have what I have?" for every
locally-tagged file -- thousands of round trips, or one giant one, for a
defect that is rare by construction (this project's own real run found 28
out of 5,709 files). Instead, one single, remote-side-filtered `SELECT`
asks the remote directly for the audio it *itself* considers entirely
untagged (`[REQ-LIB-146]`'s own defect shape: every field NULL, not merely
one). That answer is only ever as large as the remote's real gap -- healthy
on a library that has already been fixed, and always at most the size of
the actual problem, never the size of the library `[SPEC-DF-114]`'s own
lesson about full-copy costs. Only *those* audio ids are then looked up
locally, and only the ones this library can actually offer something for
are pushed.

Matched by `audio_md5`, never `file_id`: two installations of "the same"
library are not guaranteed to agree on row numbering `[SPEC-DF-040]`, only
on what the audio itself hashes to.

    python tools/push_file_tags.py <db>                                   dry run
    python tools/push_file_tags.py <db> --commit                         write
    python tools/push_file_tags.py <db> --target user@host:/path --commit

`--target` defaults to the console's own remembered remote (`remote_config
.sync_remote`, in `<db>`'s `.console.db` sidecar -- the identical setting
the console's own "Sync with a remote" page already writes `[SPEC-DF-113]`)
so a builder who has that configured needs nothing extra; anyone else names
the remote directly.

This is a small, standalone tool on purpose, not a widened `apply_changes
.py`: raw `file_tags` has no baseline to three-way-merge against and cannot
meaningfully "conflict" the way a human's own review edit can -- the same
audio hashes to the same tags everywhere, so an empty remote row is only
ever a gap to fill, never a difference to arbitrate.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import remote_peek  # noqa: E402 -- literal()/run_remote_sql(), not duplicated

BATCH = 200  # audio_md5 values per local lookup -- generous, and the common
             # case (a real gap, by construction rare) fits in one.
WRITE_KEYWORDS = ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "REPLACE")
TAG_FIELDS = ("title", "artist", "album", "track_no", "disc_no")


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def sync_remote_from_sidecar(db_path: str) -> str | None:
    """The same `remote_config.sync_remote` value the console's own "Sync
    with a remote" page reads and writes `[SPEC-DF-113]` -- in `<db>`'s own
    `.console.db` sidecar, the exact derivation `console.py`'s own `/api/
    system/open` route uses, not duplicated as a separate setting here.
    """
    sidecar = os.path.splitext(db_path)[0] + ".console.db"
    if not os.path.exists(sidecar):
        return None
    conn = sqlite3.connect(sidecar)
    try:
        row = conn.execute("SELECT value FROM remote_config WHERE key='sync_remote'").fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    return row[0] if row else None


def remote_gaps(target: str, timeout: float) -> list[str]:
    """Every `audio_md5` the remote's own `file_tags` row is entirely empty
    for -- one filtered `SELECT`, answered only as large as the remote's
    real gap, never the size of its library.
    """
    sql = ("SELECT f.audio_md5 FROM files f JOIN file_tags t ON t.file_id = f.file_id "
           "WHERE t.title IS NULL AND t.artist IS NULL AND t.album IS NULL "
           "AND t.track_no IS NULL AND t.disc_no IS NULL")
    result = remote_peek.run_remote_sql(target, sql, timeout=timeout)
    if not result["ok"]:
        raise RuntimeError(result["error"])
    return [row["audio_md5"] for row in result["rows"]]


def local_fixes_for(conn: sqlite3.Connection, audio_md5s: list[str]) -> list[dict]:
    """This library's own `file_tags` for exactly the remote's named gaps --
    only the ones with at least one non-null field to actually offer; a
    file genuinely untagged here too has nothing to contribute either way.
    """
    out = []
    for i in range(0, len(audio_md5s), BATCH):
        chunk = audio_md5s[i:i + BATCH]
        placeholders = ",".join("?" * len(chunk))
        has_data = " OR ".join(f"t.{c} IS NOT NULL" for c in TAG_FIELDS)
        rows = conn.execute(
            "SELECT f.audio_md5, t.title, t.artist, t.album, t.track_no, t.disc_no, "
            "       t.has_art, t.scanned_at "
            "  FROM files f JOIN file_tags t ON t.file_id = f.file_id "
            f" WHERE f.audio_md5 IN ({placeholders}) AND ({has_data})",
            chunk).fetchall()
        out.extend(dict(r) for r in rows)
    return out


def build_patch(rows: list[dict]) -> str:
    """The literal SQL text to apply on the remote. SQLite's own parameter
    binding does the quoting, via `set_trace_callback` against a throwaway
    connection -- the exact pattern `apply_changes.py --emit-sql` already
    established `[SPEC-DF-111]`, reused rather than hand-rolling an escaper.
    """
    scratch = sqlite3.connect(":memory:")
    # Just enough shape for the statement below to compile -- the subquery
    # need not actually resolve to anything here; only the literal SQL text
    # SQLite renders is kept, never this connection's own effect on rows.
    scratch.executescript("""
        CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT);
        CREATE TABLE file_tags (file_id INTEGER, title TEXT, artist TEXT, album TEXT,
            track_no INTEGER, disc_no INTEGER, has_art INTEGER, scanned_at INTEGER);
    """)
    sql_log: list[str] = []
    scratch.set_trace_callback(sql_log.append)
    for row in rows:
        scratch.execute(
            "UPDATE file_tags SET title=?1, artist=?2, album=?3, track_no=?4, disc_no=?5, "
            "has_art=?6, scanned_at=?7 WHERE file_id = "
            "(SELECT file_id FROM files WHERE audio_md5=?8)",
            (row["title"], row["artist"], row["album"], row["track_no"], row["disc_no"],
             row["has_art"], row["scanned_at"], row["audio_md5"]))
    scratch.close()
    patch = [s for s in sql_log if s.lstrip().upper().startswith(WRITE_KEYWORDS)]
    lines = ["BEGIN IMMEDIATE;"]
    lines.extend(stmt.rstrip().rstrip(";") + ";" for stmt in patch)
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


REMOTE_TMP = "/tmp/lempi-file-tags-patch.sql"


def _scp(local_path: str, host: str, timeout: float) -> None:
    r = subprocess.run(["scp", local_path, f"{host}:{REMOTE_TMP}"],
                        capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "scp failed").strip()[:300])


def _ssh_apply(host: str, remote_path: str, sudo: bool, timeout: float) -> None:
    """Stop so the patch is never applied underneath a live writer, apply
    it through the remote's own `sqlite3`, restart -- the identical shape
    `[SPEC-DF-111]`'s own recipe uses. `sudo` is the default: this
    project's own appliance needs it (`[SPEC-DF-121]`, found live -- a bare
    `systemctl` fails outright as an unprivileged deploy user), and `sudo`
    in front of an already-privileged command is harmless where it is not
    needed, which `--no-sudo` exists for regardless.
    """
    prefix = "sudo " if sudo else ""
    remote_cmd = (f"{prefix}systemctl stop lempi && sqlite3 {remote_path} < {REMOTE_TMP} "
                  f"&& {prefix}systemctl start lempi")
    r = subprocess.run(["ssh", host, remote_cmd], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "ssh apply failed").strip()[:300])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--target", help="user@host:/path/to/listener.db -- defaults to the "
                                      "console's own remembered remote")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-sudo", action="store_true",
                     help="skip sudo for the remote's systemctl -- most setups need it")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    def finish(ok: bool, **fields) -> int:
        if args.json:
            print(json.dumps({"ok": ok, **fields}))
        return 0 if ok else 1

    target = args.target or sync_remote_from_sidecar(args.db)
    if not target:
        say("no --target given and no remote remembered in the console's own sidecar -- "
            "pass --target user@host:/path/to/listener.db, or set one via the console's "
            "'Sync with a remote' section first")
        return finish(False, error="no remote configured")

    say(f"checking {target} for entirely-untagged files ...")
    try:
        gaps = remote_gaps(target, args.timeout)
    except RuntimeError as e:
        say(f"could not reach {target}: {e}")
        return finish(False, error=str(e))

    if not gaps:
        say("the remote has nothing to fix.")
        return finish(True, remote_gaps=0, fixed=0, unresolved=0, landed=False)

    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True)
    conn.row_factory = sqlite3.Row
    fixes = local_fixes_for(conn, gaps)
    conn.close()
    unresolved = len(gaps) - len(fixes)

    say(f"{len(gaps)} entirely-untagged file(s) on the remote; this library can fix "
        f"{len(fixes)} of them" + (f", {unresolved} remain untagged here too" if unresolved else ""))
    for row in fixes:
        say(f"  {'would fix' if not args.commit else 'fixed'}  {row['audio_md5']}  "
            + (f"“{row['title']}”" if row["title"] else "(no title)"))

    if not fixes:
        say("nothing this library can offer -- the remote was not touched.")
        return finish(True, remote_gaps=len(gaps), fixed=0, unresolved=unresolved, landed=False)

    if not args.commit:
        say("\nRe-run with --commit to build and apply the patch.")
        return finish(True, remote_gaps=len(gaps), fixed=len(fixes),
                       unresolved=unresolved, landed=False)

    host, sep, remote_path = target.partition(":")
    if not sep or not remote_path:
        say(f"target must be user@host:/path, got {target!r}")
        return finish(False, error="malformed target")

    patch_sql = build_patch(fixes)
    tmp_local = os.path.join(os.path.dirname(os.path.abspath(args.db)),
                              ".push-file-tags-patch.sql")
    with open(tmp_local, "w", encoding="utf-8") as f:
        f.write(patch_sql)
    try:
        _scp(tmp_local, host, args.timeout + 30)
        _ssh_apply(host, remote_path, not args.no_sudo, args.timeout + 30)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
        say(f"apply failed: {e}")
        return finish(False, error=str(e))
    finally:
        os.remove(tmp_local)

    say(f"\n{len(fixes)} file(s) fixed on {host}.")
    return finish(True, remote_gaps=len(gaps), fixed=len(fixes),
                  unresolved=unresolved, landed=True)


if __name__ == "__main__":
    sys.exit(main())
