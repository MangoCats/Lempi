#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How far an album added from files has come, stage by stage `[SPEC-FIN-020]`.

A CD add does everything at once: the edition, both passages' identity, the
album each file is shown under, flavor, lead-in and out, the cover. A folder
of files added by `ingest_folder.py` gets its passages and its tags, and
nothing else, until a person finishes it on the console's *Finish an album*
page. This is what that page reads: one folder's stages, each done, partly
done or not done, measured from the catalogue as it stands -- never from what
a job said it did `[GOV-SRC-010]`.

Read-only. `status` for one folder; `folders` for every folder with something
left to do.

    python tools/finish_folder.py data/library.db "C:/Music/Traffic/Smiling Phases (1991)"
    python tools/finish_folder.py data/library.db --list
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402
from fetch_cover_art import MIN_BYTES as MIN_COVER_BYTES  # noqa: E402  -- one floor for a real side

MBID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
STAGES = ("edition", "flavor", "amplitude", "cover", "works")


def same_folder(path: str, folder: str) -> bool:
    """Whether `path` lies directly in `folder`: not below it, as an album's
    tracks lie flat, and in the file system's own sense of case."""
    return (os.path.normcase(os.path.dirname(os.path.normpath(path)))
            == os.path.normcase(os.path.normpath(folder)))


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?1", (table,)).fetchone() is not None


def _cols(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def works_cache_for(library: str) -> str:
    """Where `fetch_works.py` keeps MusicBrainz's answers: beside the library,
    as the console's induct has always passed it."""
    return os.path.join(os.path.dirname(os.path.abspath(library)), "work_relations.db")


def _state(done: int, total: int) -> str:
    return "done" if total and done >= total else "partly" if done else "not done"


def status(conn: sqlite3.Connection, folder: str, works_cache: str | None = None) -> dict:
    """Every stage of finishing `folder`, measured. Each stage is `{state,
    done, total, ...}`, `state` one of done / partly / not done; `next` names
    the stages the finishing job would still do."""
    folder = os.path.normpath(folder)
    like = folder.rstrip("\\/") + "%"
    files = [r for r in conn.execute(
        "SELECT f.file_id, f.audio_md5, f.path, t.title, t.track_no, t.disc_no, t.artist, t.album, "
        "       COALESCE(t.has_art, 0) "
        "FROM files f LEFT JOIN file_tags t ON t.file_id = f.file_id WHERE f.path LIKE ?1 ORDER BY f.path",
        (like,)) if same_folder(r[2], folder)]
    ids = [f[0] for f in files]
    out: dict = {"folder": folder, "files": len(files), "exists": os.path.isdir(folder)}
    if not files:
        out.update({"radio": 0, "album": 0, "stages": {}, "next": [], "finished": False})
        return out
    marks = ",".join("?" * len(ids))
    psg = conn.execute(
        f"SELECT p.passage_id, p.file_id, p.kind, p.lead_in_ms, p.boundary_src, pr.mbid, p.start_ms, p.end_ms "
        f"FROM passages p LEFT JOIN passage_recordings pr ON pr.passage_id = p.passage_id "
        f"WHERE p.file_id IN ({marks}) AND p.kind IN ('radio', 'album')", ids).fetchall()
    radio = {r[0]: r for r in psg if r[2] == "radio"}
    album = {r[0]: r for r in psg if r[2] == "album"}
    real = lambda m: bool(m and MBID.match(m))  # noqa: E731
    out.update({"radio": len(radio), "album": len(album),
                "artist": collections.Counter(f[6] for f in files if f[6]).most_common(1)[0][0]
                if any(f[6] for f in files) else None,
                "album_tag": collections.Counter(f[7] for f in files if f[7]).most_common(1)[0][0]
                if any(f[7] for f in files) else None})

    # The edition: the album each file is shown under, and both passages named.
    held = {}
    if _has(conn, "file_releases"):
        md5s = [f[1] for f in files]
        held = dict(conn.execute(
            f"SELECT audio_md5, release_mbid FROM file_releases WHERE audio_md5 IN ({','.join('?' * len(md5s))})",
            md5s).fetchall())
    count = collections.Counter(held.values())
    edition = None
    if count:
        rel, n = count.most_common(1)[0]
        cols = _cols(conn, "releases") if _has(conn, "releases") else set()
        want = [c for c in ("title", "release_date", "country") if c in cols]
        row = conn.execute(f"SELECT {', '.join(want)} FROM releases WHERE mbid = ?1", (rel,)).fetchone() \
            if want else None
        info = dict(zip(want, row)) if row else {}
        source = conn.execute("SELECT source FROM file_releases WHERE release_mbid = ?1 AND audio_md5 IN "
                              f"({','.join('?' * len(held))}) LIMIT 1", (rel, *held)).fetchone()
        edition = {"mbid": rel, "title": info.get("title"), "date": info.get("release_date"),
                   "country": info.get("country"), "files": n, "source": source[0] if source else None,
                   "others": len(count) - 1}
    named_radio = sum(real(r[5]) for r in radio.values())
    named_album = sum(real(r[5]) for r in album.values())
    total = len(files) + len(radio) + len(album)
    done = sum(1 for f in files if f[1] in held) + named_radio + named_album
    stages = {"edition": {"state": _state(done, total), "release": edition,
                          "files_shown": sum(1 for f in files if f[1] in held), "files": len(files),
                          "radio_named": named_radio, "radio": len(radio),
                          "album_named": named_album, "album": len(album)}}

    # Flavor belongs to a recording [SPEC-SC-060]; a passage has it when its
    # recording does, which is what the Program Director reads.
    recs = sorted({r[5] for r in radio.values() if r[5]})
    flavored = set()
    if recs and _has(conn, "flavor"):
        flavored = {m for (m,) in conn.execute(
            f"SELECT DISTINCT subject_id FROM flavor WHERE subject_kind = 'recording' "
            f"AND subject_id IN ({','.join('?' * len(recs))})", recs)}
    n = sum(1 for r in radio.values() if r[5] in flavored)
    # Which of those were measured from these files, and which carry flavor
    # their recording already had from another copy -- a CD of the same song.
    # On 2026-11-19 *Smiling Phases* read 20 of 26 the moment its edition was
    # named, with 10 measured: right, and easy to misread mid-run.
    md5_of = {f[0]: f[1] for f in files}
    cached: set = set()
    if radio and _has(conn, "lowlevel_cache"):
        cached = {tuple(x) for x in conn.execute(
            f"SELECT audio_md5, start_ms, end_ms FROM lowlevel_cache WHERE audio_md5 IN "
            f"({','.join('?' * len(md5_of))})", list(md5_of.values()))}
    here = {pid for pid, r in radio.items() if (md5_of[r[1]], r[6], r[7]) in cached}
    stages["flavor"] = {"state": _state(n, len(radio)), "done": n, "total": len(radio),
                        "measured_here": len(here),
                        "from_elsewhere": sum(1 for pid, r in radio.items() if r[5] in flavored and pid not in here)}

    # Lead-in and out, measured; a boundary set by hand is never recomputed.
    n = sum(1 for r in radio.values() if r[3] is not None or r[4] == "manual")
    stages["amplitude"] = {"state": _state(n, len(radio)), "done": n, "total": len(radio)}

    # The cover: the edition's front and back from the archive; what the files
    # and the folder carry themselves, said beside it.
    front = back = False
    if edition and _has(conn, "cover_art"):
        row = conn.execute("SELECT COALESCE(LENGTH(front), 0), COALESCE(LENGTH(back), 0) FROM cover_art "
                           "WHERE release_mbid = ?1", (edition["mbid"],)).fetchone()
        front, back = bool(row and row[0] >= MIN_COVER_BYTES), bool(row and row[1] >= MIN_COVER_BYTES)
    stages["cover"] = {"state": "done" if front else "not done" if edition else "waiting",
                       "front": front, "back": back,
                       "folder_jpg": os.path.isfile(os.path.join(folder, "folder.jpg")),
                       "embedded": sum(1 for f in files if f[8])}

    # Works: which recordings MusicBrainz has been asked about, and which
    # perform one. Asked is the stage; a recording may perform none.
    named = sorted({r[5] for r in radio.values() if real(r[5])})
    asked: set = set()
    cache = works_cache or None
    if named and cache and os.path.isfile(cache):
        c = sqlite3.connect(lempi_db.ro_uri(cache), uri=True)
        try:
            if _has(c, "work_cache"):
                asked = {m for (m,) in c.execute(
                    f"SELECT mbid FROM work_cache WHERE response NOT LIKE '%\"unreachable\"%' "
                    f"AND mbid IN ({','.join('?' * len(named))})", named)}
        finally:
            c.close()
    with_work = 0
    if named and _has(conn, "recording_works"):
        with_work = conn.execute(f"SELECT COUNT(DISTINCT mbid) FROM recording_works WHERE mbid IN "
                                 f"({','.join('?' * len(named))})", named).fetchone()[0]
    stages["works"] = {"state": _state(len(asked), len(named)) if named else "waiting",
                       "asked": len(asked), "recordings": len(named), "with_work": with_work}

    out["stages"] = stages
    out["next"] = [s for s in STAGES if stages[s]["state"] != "done"]
    out["finished"] = not out["next"]
    return out


def folders(conn: sqlite3.Connection) -> list[dict]:
    """Every folder with a radio passage still on a placeholder or not yet
    analyzed: what the page's index lists, most recently added first. A file
    with no album recorded is counted but does not list a folder on its own:
    33 folders on 2026-11-19 were only that, their albums shown by each
    recording's chosen release, as `align_file_releases.py` leaves them."""
    has_fr = _has(conn, "file_releases")
    cached = _has(conn, "lowlevel_cache")
    rows = conn.execute(
        "SELECT f.path, f.first_seen, pr.mbid, p.lead_in_ms IS NULL AND COALESCE(p.boundary_src, '') <> 'manual', "
        + ("EXISTS (SELECT 1 FROM file_releases r WHERE r.audio_md5 = f.audio_md5)" if has_fr else "0") + ", "
        + ("EXISTS (SELECT 1 FROM lowlevel_cache c WHERE c.audio_md5 = f.audio_md5 "
           "AND c.start_ms = p.start_ms AND c.end_ms = p.end_ms)" if cached else "0")
        + " FROM passages p JOIN files f ON f.file_id = p.file_id "
          "LEFT JOIN passage_recordings pr ON pr.passage_id = p.passage_id WHERE p.kind = 'radio'").fetchall()
    by: dict[str, dict] = {}
    for path, first_seen, mbid, no_amp, shown, extracted in rows:
        d = by.setdefault(os.path.dirname(os.path.normpath(path)), {
            "radio": 0, "placeholders": 0, "no_album": 0, "no_analysis": 0, "newest": ""})
        d["radio"] += 1
        d["placeholders"] += not (mbid and MBID.match(mbid))
        d["no_album"] += not shown
        d["no_analysis"] += bool(no_amp or not extracted)
        d["newest"] = max(d["newest"], first_seen or "")
    out = [{"folder": k, **v} for k, v in by.items() if v["placeholders"] or v["no_analysis"]]
    out.sort(key=lambda d: d["newest"], reverse=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("db")
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--list", action="store_true", help="every folder with something left to do")
    args = ap.parse_args()
    if not args.list and not args.folder:
        ap.error("name a folder, or --list")
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY)
    sys.stdout.reconfigure(encoding="utf-8")    # folder names are not the console's code page
    result = folders(conn) if args.list else status(conn, args.folder, works_cache_for(args.db))
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
