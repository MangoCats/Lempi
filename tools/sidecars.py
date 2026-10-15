#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The files a CD rip leaves beside its audio -- the rip log, the AccurateRip
report, the CUE sheet -- hashed when the rip is added, so they can be checked
with the audio after `[GDE-INT-175]`.

The audit of 2026-10-14 found them recorded nowhere: a rip log is the only
evidence a rip was verified, and it could be truncated or lost with nothing to
say so. Now a CD add writes one `ingest_decisions` row, stage `sidecars`, whose
`detail` is `{"files": {name: sha256}}` -- names, not paths: the folder is the
one the row's `audio_md5` lies in, so a relink that moves the album, or a rekey
that renames its audio, carries the record with it. `verify_library.py` checks
them.

A rip already in the library when this was written has no such row. `--adopt`
writes one for each, outcome `adopted`, not `recorded`: its hashes are of the
files as they are today, not as the rip left them, and the row says so.

    python tools/sidecars.py data/library.db            # what --adopt would record
    python tools/sidecars.py data/library.db --adopt    # record it
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from byte_hash import sha256_file  # noqa: E402

STAGE = "sidecars"
EXTS = (".cue", ".toc", ".accurip", ".log")
# A `.log` is a rip log only if it says so: seven of the hub's 67 were FTP
# client logs from 2002, beside the albums they downloaded (2026-10-15).
RIPPERS = ("CUERipper", "Exact Audio Copy", "EAC extraction logfile", "X Lossless Decoder",
           "XLD extraction logfile", "whipper", "cdrdao", "dBpoweramp", "morituri")


def is_rip_log(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(1024)
    except OSError:
        return False
    enc = "utf-16" if head[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
    text = head.decode(enc, "replace")
    return any(r in text for r in RIPPERS)


def of_rip(folder: str, names=None) -> list[str]:
    """The sidecars in `folder`, by name, sorted. Of `names` when given -- the
    files a rip brought, where a `.log` is the rip's log because the rip brought
    it -- else of everything there, where a `.log` must say it is one."""
    try:
        here = names if names is not None else os.listdir(folder)
    except OSError:
        return []
    out = []
    for n in here:
        p = os.path.join(folder, n)
        ext = os.path.splitext(n)[1].lower()
        if ext in EXTS and os.path.isfile(p) and (ext != ".log" or names is not None or is_rip_log(p)):
            out.append(n)
    return sorted(out)


def record(conn, audio_md5: str, folder: str, names, outcome: str = "recorded",
           now: str | None = None) -> int:
    """One row naming each of `names` in `folder` with its SHA-256; the count.
    Nothing is written for none. The caller commits."""
    files = {n: sha256_file(os.path.join(folder, n)) for n in names}
    if not files:
        return 0
    conn.execute(
        "INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
        "VALUES (?1,?2,?3,NULL,?4,?5)",
        (audio_md5, STAGE, outcome, json.dumps({"files": files}, sort_keys=True),
         now or time.strftime("%Y-%m-%dT%H:%M:%S")))
    return len(files)


def has_decisions(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ingest_decisions'"
                        ).fetchone() is not None


def recorded(conn) -> list[dict]:
    """Every recorded sidecar, the newest record of each: `folder` is where its
    audio is now, None when no catalogued file has that audio any more."""
    if not has_decisions(conn):
        return []
    where = {}
    for md5, path in conn.execute("SELECT audio_md5, path FROM files WHERE path IS NOT NULL ORDER BY file_id DESC"):
        where[md5] = os.path.dirname(path)       # the lowest file_id wins, as the import's first track
    latest = {}
    for md5, outcome, detail, at in conn.execute(
            "SELECT audio_md5, outcome, detail, decided_at FROM ingest_decisions "
            "WHERE stage = ?1 ORDER BY decided_at, decision_id", (STAGE,)):
        folder = where.get(md5)
        for name, sha in (json.loads(detail or "{}").get("files") or {}).items():
            key = (os.path.normcase(folder) if folder else md5, name.lower())
            latest[key] = {"audio_md5": md5, "folder": folder, "name": name, "sha256": sha,
                           "outcome": outcome, "decided_at": at}
    return sorted(latest.values(), key=lambda r: (r["folder"] or "", r["name"]))


def unrecorded(conn) -> list[tuple[str, str, list[str]]]:
    """The sidecars beside catalogued audio that no record names, as (folder,
    audio_md5, names): each keyed to the audio of its own name when there is one
    -- `EatAPeach.cue` to `EatAPeach.mp3`, in an artist's folder of several
    albums, so a relink of one album takes only its own -- else to the folder's
    first file."""
    have = {(os.path.normcase(r["folder"]), r["name"].lower()) for r in recorded(conn) if r["folder"]}
    folders = {}
    for md5, path in conn.execute("SELECT audio_md5, path FROM files WHERE path IS NOT NULL ORDER BY file_id"):
        f = folders.setdefault(os.path.normcase(os.path.dirname(path)),
                               {"folder": os.path.dirname(path), "first": md5, "stems": {}})
        f["stems"].setdefault(os.path.splitext(os.path.basename(path))[0].lower(), md5)
    out = []
    for key in sorted(folders):
        f = folders[key]
        by = {}
        for n in of_rip(f["folder"]):
            if (key, n.lower()) not in have:
                by.setdefault(f["stems"].get(os.path.splitext(n)[0].lower(), f["first"]), []).append(n)
        out += [(f["folder"], md5, names) for md5, names in sorted(by.items(), key=lambda kv: kv[1])]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("library")
    ap.add_argument("--adopt", action="store_true",
                    help="record the sidecars no row names yet, outcome 'adopted'")
    args = ap.parse_args()
    import lempi_db
    conn = lempi_db.connect(args.library, lempi_db.ROLE_LIBRARY, writable=args.adopt, timeout=60)
    try:
        if not has_decisions(conn):
            print("no ingest_decisions table: nothing to read or record")
            return 2
        todo = unrecorded(conn)
        n = sum(len(names) for _f, _m, names in todo)
        print(f"{len(recorded(conn))} sidecar(s) recorded; {n} in {len({f for f, _m, _n in todo})} folder(s) not yet")
        if not args.adopt:
            for folder, _md5, names in todo[:10]:
                print(f"  {folder}: {', '.join(names)}")
            if todo:
                print("run again with --adopt to record them as they are today")
            return 0
        conn.execute("BEGIN IMMEDIATE")
        done = sum(record(conn, md5, folder, names, outcome="adopted") for folder, md5, names in todo)
        conn.commit()
        print(f"adopted {done} sidecar(s) in {len({f for f, _m, _n in todo})} folder(s)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
