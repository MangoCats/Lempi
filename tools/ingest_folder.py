#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bring a folder of audio into the library `[REQ-LIB-100]`.

The smallest honest version of the requirement the project exists for. Lempi's
library was a one-time migration of MuLibPlay's catalogue -- every row of
`passage_recordings` reads `inherited:mulib` -- so nothing has ever noticed a
new file appearing on disk. A four-track EP sat in `Music/Mangocats/Tropicat`
through the entire scan, four months old and perfectly readable, and was never
ingested because MuLibPlay had never heard of it.

What this does NOT do, deliberately: segment multi-track files `[SPEC-SA-070]`,
compute flavor `[REQ-LIB-120]`, or invent a MusicBrainz identity. One passage
per file, spanning the whole file, is right for single-track audio and wrong
for a DAO capture -- so it refuses nothing and claims nothing.

    python tools/ingest_folder.py data/library.db "C:/path/to/album"
    python tools/ingest_folder.py data/library.db "C:/path/to/album" --commit
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import audio_duration  # noqa: E402

AUDIO = (".mp3", ".flac", ".ogg", ".m4a", ".wav", ".opus")

# Self-published music has no MusicBrainz entry and inventing one would be a
# lie that everything downstream believes. The id is derived from the audio, so
# it is stable across re-ingests, unique per recording, and -- crucially --
# fails `is_mbid()`, which is how the player knows not to trust it as one.
#
# Distinct from the migration's `local:track:N`, which was a positional
# placeholder: two passages shared `local:track:827`, so it did not even
# identify a track.
LOCAL_PREFIX = "local:audio:"
LOCAL_SOURCE = "local:ingest"


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def audio_md5(path: str) -> str | None:
    """Essentia's `md5_encoded`, via ffmpeg -- the same value the migration
    used, so a file ingested here and one migrated hash alike."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-c:a", "copy",
                        "-f", "md5", "-"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    m = re.search(r"MD5=([0-9a-f]{32})", r.stdout)
    return m.group(1) if m else None


def probe(path: str) -> dict | None:
    """Duration and tags. Two different questions, asked two different ways.

    Tags still come from one fast `ffprobe` call -- there is no cheaper way
    to read them, and nothing about them is estimated. Duration does not:
    `ffprobe format=duration` silently falls back to a bitrate ESTIMATE for
    a VBR file with no valid Xing/Info header, wrong by however far the
    file's real average bitrate differs from that guess -- measured on this
    library at 29.7% of files, worst case 32.8 minutes off
    `[REQ-LIB-145]`. `audio_duration.probe_duration_ms` actually decodes
    the file instead, which is the only way to answer what `[SPEC-SC-030]`'s
    schema comment requires: "decoded, not header-claimed".
    """
    r = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format_tags=title,artist,album,track,disc",
         "-show_entries", "stream_tags=title,artist,album,track,disc",
         "-show_entries", "stream=codec_type", "-of", "json", path],
        capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    fmt = d.get("format") or {}
    duration_ms = audio_duration.probe_duration_ms(path)
    if not duration_ms:
        return None
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    # MP3's ID3 tags land on `format`, which the lookup above already covers
    # -- but Ogg Vorbis comments land on the *stream* instead, and ffprobe
    # never copies them up: `format.tags` comes back empty for an Ogg file
    # with a full tag set sitting one level down `[REQ-LIB-146]`, found live
    # against `Xavier Rudd/White Moth`, systemic across all 27 `.ogg` files
    # then in the library (0/27 tagged, vs. 5490/5682 `.mp3`). `setdefault`
    # per field, not a blanket second source, so a format-level tag a file
    # genuinely has is never overwritten by a same-named stream-level one.
    for s in d.get("streams") or []:
        for k, v in (s.get("tags") or {}).items():
            tags.setdefault(k.lower(), v)
    has_art = any(s.get("codec_type") == "video" for s in d.get("streams") or [])
    return {
        "duration_ms": int(round(duration_ms)),
        "title": tags.get("title"),
        "artist": tags.get("artist"),
        "album": tags.get("album"),
        # "3/12" is as common as "3"; the leading number is the answer.
        "track_no": first_int(tags.get("track")),
        "disc_no": first_int(tags.get("disc")),
        "has_art": 1 if has_art else 0,
    }


def first_int(v):
    if not v:
        return None
    m = re.match(r"\s*(\d+)", str(v))
    return int(m.group(1)) if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("folder")
    ap.add_argument("--commit", action="store_true")
    # Both by default `[SPEC-SA-110]`: a single-track file earns the same
    # album/radio duality `[GDE-BMK-030]` a migrated one already has, without
    # a second manual run someone has to remember to make -- 116 files across
    # this library's own history never got that second run, and sat with a
    # radio cut and nothing else until `tools/backfill_album_cuts.py` found
    # them. `radio`/`album` alone are kept for the one caller with a real
    # reason to want just one -- `tools/backfill_album_cuts.py`'s own tests,
    # and anyone re-ingesting a single kind on purpose.
    ap.add_argument("--kind", default="both", choices=("radio", "album", "both"))
    # Structured output, for a caller that is not a terminal `[SPEC-SUI-085]`.
    # The prose form renders a smart-quoted title as `?Duala?` under a Windows
    # console encoding -- harmless there and wrong in a browser, so a consumer
    # gets the record rather than the rendering.
    ap.add_argument("--json", action="store_true", help="emit the plan/result as JSON")
    args = ap.parse_args()
    record = []

    if not os.path.isdir(args.folder):
        if args.json:
            print(json.dumps({"error": f"not a folder: {args.folder}"}))
        else:
            say(f"not a folder: {args.folder}")
        return 1

    # Normalised to the platform's own separators. The first run of this stored
    # paths exactly as they were typed -- "C:/a/b\file.mp3" from a
    # forward-slash argument -- while the migrated 5,590 were pure backslash.
    # Nothing broke, because `audio_md5` is UNIQUE and catches a re-import
    # regardless of spelling, but a scan comparing disk against `files.path`
    # then reports already-imported audio as missing.
    files = sorted(
        os.path.normpath(os.path.join(dp, n))
        for dp, _, ns in os.walk(args.folder)
        for n in ns if n.lower().endswith(AUDIO))
    if not files:
        if args.json:
            print(json.dumps({"folder": args.folder, "files": 0, "items": []}))
        else:
            say(f"no audio under {args.folder}")
        return 1
    if not args.json:
        say(f"{len(files)} audio file(s) under {args.folder}\n")

    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA foreign_keys = ON")

    added = skipped = failed = 0
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if args.commit:
        conn.execute("BEGIN IMMEDIATE")

    for path in files:
        md5 = audio_md5(path)
        info = probe(path)
        name = os.path.basename(path)
        if md5 is None or info is None:
            record.append({"path": path, "name": name, "outcome": "unreadable"})
            if not args.json:
                say(f"  SKIP  {name}  (would not decode)")
            failed += 1
            continue
        # `files.audio_md5` is UNIQUE, and it is the identity: the same audio
        # under a different name is the same file, which is also what makes
        # re-running this safe.
        seen = conn.execute("SELECT file_id FROM files WHERE audio_md5 = ?1", (md5,)).fetchone()
        if seen:
            record.append({"path": path, "name": name, "outcome": "have",
                           "audio_md5": md5, "file_id": seen[0]})
            if not args.json:
                say(f"  have  {name}")
            skipped += 1
            continue

        record.append({"path": path, "name": name, "outcome": "add",
                       "audio_md5": md5, "duration_ms": info["duration_ms"],
                       "title": info["title"], "artist": info["artist"],
                       "album": info["album"], "has_art": info["has_art"]})
        if not args.json:
            say(f"  ADD   {name}  {info['duration_ms']/1000:.0f}s"
                + (f"  \u201c{info['title']}\u201d" if info["title"] else "")
                + ("  [embedded art]" if info["has_art"] else ""))
        added += 1
        if not args.commit:
            continue

        st = os.stat(path)
        cur = conn.execute(
            "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,"
            "                   first_seen,last_seen) VALUES (?1,?2,?3,?4,?5,?6,?7,?7)",
            (md5, path, st.st_size, st.st_mtime,
             os.path.splitext(path)[1].lstrip(".").lower(), info["duration_ms"], now))
        fid = cur.lastrowid

        conn.execute(
            "INSERT INTO file_tags (file_id,title,artist,album,track_no,disc_no,"
            "                       has_art,scanned_at) VALUES (?1,?2,?3,?4,?5,?6,?7,?8)",
            (fid, info["title"], info["artist"], info["album"],
             info["track_no"], info["disc_no"], info["has_art"], int(time.time())))

        # One passage per kind spanning the file `[SPEC-SA-110]`. Right for
        # single-track audio; a DAO capture needs segmentation, which is not
        # this tool's business. `album`'s lead/gain are 0, not NULL: its segue
        # points equal its own hard boundaries `[GDE-BMK-030]`, so it is never
        # "awaiting analysis" the way a fresh radio cut is -- and never will
        # be, since `analyze_amplitude.py` only ever touches `kind='radio'`.
        kinds = ("radio", "album") if args.kind == "both" else (args.kind,)
        pids = {}
        for kind in kinds:
            if kind == "album":
                pids[kind] = conn.execute(
                    "INSERT INTO passages "
                    "(file_id,kind,start_ms,end_ms,lead_in_ms,lead_out_ms,gain_db,boundary_src) "
                    "VALUES (?1,?2,0,?3,0,0,0.0,'ingest:whole-file')",
                    (fid, kind, info["duration_ms"])).lastrowid
            else:
                pids[kind] = conn.execute(
                    "INSERT INTO passages (file_id,kind,start_ms,end_ms,boundary_src) "
                    "VALUES (?1,?2,0,?3,'ingest:whole-file')",
                    (fid, kind, info["duration_ms"])).lastrowid

        mbid = LOCAL_PREFIX + md5
        conn.execute(
            "INSERT OR IGNORE INTO recordings (mbid,title,length_ms,source) VALUES (?1,?2,?3,?4)",
            (mbid, info["title"] or os.path.splitext(name)[0], info["duration_ms"], LOCAL_SOURCE))
        for pid in pids.values():
            conn.execute(
                "INSERT INTO passage_recordings (passage_id,mbid,weight,source) "
                "VALUES (?1,?2,1.0,?3)", (pid, mbid, LOCAL_SOURCE))

        # What this stage decided, and on what `[SPEC-SA-085]`, `[SPEC-SC-100]`.
        # Added 2026-08-20: the table held 15,050 rows and every one was
        # `release_match`. One stage of seven was recording anything, which is
        # not what "every decision is recorded, not just logged" describes, and
        # it stayed invisible until the console read the table `[IMPL-SUI-045]`.
        conn.execute(
            "INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
            "VALUES (?1,'ingest','added',NULL,?2,?3)",
            (md5, json.dumps({
                "passages": pids, "file_id": fid, "mbid": mbid,
                "boundary": "whole-file",
                # Why no MusicBrainz id was sought here: this stage does not
                # identify, and recording that is the point of the table.
                "identity": "derived from audio; this stage does not identify",
                "duration_ms": info["duration_ms"], "has_art": info["has_art"],
            }), now))

    if args.commit:
        conn.commit()
    if args.json:
        print(json.dumps({
            "folder": args.folder, "committed": bool(args.commit),
            "files": len(files), "added": added, "have": skipped, "unreadable": failed,
            "items": record,
        }, ensure_ascii=False))
        return 0
    if args.commit:
        say(f"\nadded {added}, already present {skipped}, unreadable {failed}")
        if args.kind == "both":
            say("Each got a `radio` cut (what the Director picks) and an `album` "
                "cut (full span, matching what a migrated track already has).")
        elif args.kind == "radio":
            say("They are `kind=radio` passages, so the Director can pick them now.")
        else:
            say("They are `kind=album` passages -- the Director only ever picks `radio`.")
        say(f"Identity is {LOCAL_PREFIX}<md5> -- deliberately not an MBID, because")
        say("there is no MusicBrainz entry to point at and inventing one would")
        say("be a lie the whole library would then believe.")
    else:
        say(f"\nwould add {added}, already present {skipped}, unreadable {failed}")
        say("nothing was written. Re-run with --commit to do it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
