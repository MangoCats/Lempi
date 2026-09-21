#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ingest a completed CD rip -- Disc ID/CD-TEXT/AcoustID identification
cascade, TOC-exact segmentation `[SPEC-RIP-010]`, `[SPEC025]`/`[SPEC028]`.

**Person-assisted, per `[SPEC-RIP-088]`.** This tool does not drive a rip --
it reads one a person already ran to completion (EAC's own GUI on Windows;
by hand, `cdrdao read-cd`, on Linux) and finds sitting in a folder: a
`.cue`+log (EAC) or a `.toc` (cdrdao), plus the audio it names. Nothing here
touches an optical drive.

Down-select and freeform entry are **not built as a new page.** A passage
whose identity is ambiguous or unresolved is written with the same
`local:audio:<md5>:<start_ms>` placeholder `segment_dao.py` already uses,
plus an `id_checks` row whose `suggested` carries the real Disc ID
candidates -- which the *existing* `/review` queue already renders as a
down-select, unconditionally, for any grade. See `SPEC028 §3` and this
session's own build-out plan for why that queue needed no changes at all
to serve this case.

    python tools/ingest_cd.py <db> --folder <rip-output-dir> [--commit] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import audio_duration        # noqa: E402
import cd_toc                 # noqa: E402
import fetch_releases         # noqa: E402  -- get(), UA, rate-limit/backoff
import ingest_folder          # noqa: E402  -- audio_md5(), LOCAL_PREFIX
import secret                 # noqa: E402
import segment_dao            # noqa: E402  -- identify_recording() (AcoustID)

FFMPEG = shutil.which("ffmpeg")
MB_DISCID_BASE = "https://musicbrainz.org/ws/2/discid"


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


# ------------------------------------------------------------------- locate

def find_rip(folder: str) -> tuple[str, str]:
    """`('eac', cue_path)` or `('cdrdao', toc_path)` -- whichever this rip
    folder actually holds `[SPEC-RIP-024]`'s "thin adapter boundary": one
    format detected, everything after this reads identically.
    """
    cues = [f for f in os.listdir(folder) if f.lower().endswith(".cue")]
    tocs = [f for f in os.listdir(folder) if f.lower().endswith(".toc")]
    if cues:
        return "eac", os.path.join(folder, cues[0])
    if tocs:
        return "cdrdao", os.path.join(folder, tocs[0])
    raise FileNotFoundError(
        f"no .cue (EAC) or .toc (cdrdao) file found in {folder!r}")


def find_log(folder: str) -> str | None:
    logs = [f for f in os.listdir(folder) if f.lower().endswith(".log")]
    return os.path.join(folder, logs[0]) if logs else None


# ------------------------------------------------------------------- encode

def encode_to_mp3(wav_path: str, out_path: str) -> bool:
    """One encode, discarding the WAV working copy `[SPEC-RIP-040/045]` --
    the same `-c:a libmp3lame -q:a 4` convention `extract_library.py`'s own
    tests already establish for this codebase."""
    if not FFMPEG:
        return False
    r = subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-i", wav_path,
         "-c:a", "libmp3lame", "-q:a", "4", out_path],
        capture_output=True, timeout=1800)
    return r.returncode == 0 and os.path.exists(out_path)


# -------------------------------------------------------------- disc lookup

def _discid_url(disc_id: str, toc_param: str | None) -> str:
    q = {"fmt": "json", "inc": "recordings+artist-credits"}
    if toc_param is not None:
        q["toc"] = toc_param
    return f"{MB_DISCID_BASE}/{urllib.parse.quote(disc_id)}?{urllib.parse.urlencode(q)}"


def lookup_disc_id(toc: cd_toc.DiscToc) -> tuple[str, list[dict]]:
    """The Disc ID cascade `[SPEC-RIP-060]`: exact id lookup first: a fuzzy
    `toc=` search only if that finds nothing. Returns `(outcome, releases)`
    where `outcome` is `'exact'`, `'fuzzy'` or `'none'` and `releases` is
    MusicBrainz's own release list, each carrying `media[].tracks[]` with
    real `recording` ids already embedded (`inc=recordings+artist-credits`)
    -- verified live 2026-09-04 to need no separate per-release fetch.

    **An exact id match can still return more than one release** -- proven
    against this session's real test disc, which matched exactly and still
    returned three country editions of the same album. `[SPEC-RIP-069]`'s
    down-select therefore applies by *candidate count*, not by whether the
    match was exact or fuzzy.
    """
    disc_id = cd_toc.musicbrainz_disc_id(toc)
    toc_param = cd_toc.musicbrainz_toc_param(toc)

    doc = fetch_releases.get(_discid_url(disc_id, None))
    if doc is not None:
        return "exact", doc.get("releases", []) or []

    time.sleep(fetch_releases.RATE_S)
    doc = fetch_releases.get(_discid_url(disc_id, toc_param))
    if doc is not None and doc.get("releases"):
        return "fuzzy", doc["releases"]

    return "none", []


def _track_at_position(release: dict, position: int) -> dict | None:
    for medium in release.get("media") or []:
        for t in medium.get("tracks") or []:
            if t.get("position") == position or t.get("number") == str(position):
                return t
    return None


def _candidate_for(release: dict, position: int) -> dict | None:
    """One `Suggestion`-shaped candidate (`mbid`/`title`/`artist`/`score`)
    -- the exact JSON shape `player/src/db/library.rs`'s `Suggestion`
    deserializes and `fingerprint_ids.py`'s own `judge()` already writes,
    reused rather than invented `[SPEC-RIP-074]`."""
    t = _track_at_position(release, position)
    if t is None:
        return None
    rec = t.get("recording") or {}
    mbid = rec.get("id")
    if not mbid:
        return None
    artists = ", ".join(a.get("name", "") for a in rec.get("artist-credit") or []
                         if a.get("name"))
    return {"mbid": mbid, "title": rec.get("title") or t.get("title"),
            "artist": artists or None, "score": 1.0}


def _artists_for(release: dict, position: int) -> list[tuple[str, str]]:
    """`(artist_mbid, name)` pairs for `recording_artists`, from the same
    embedded `artist-credit` `_candidate_for` reads for its own display
    string -- kept separate because `Suggestion`'s own shape has no room
    for a real per-artist mbid, only the joined name a review card shows."""
    t = _track_at_position(release, position)
    if t is None:
        return []
    rec = t.get("recording") or {}
    return [(a["artist"]["id"], a["artist"].get("name") or "?")
            for a in rec.get("artist-credit") or []
            if isinstance(a.get("artist"), dict) and a["artist"].get("id")]


# -------------------------------------------------------------------- commit

def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def commit_rip(conn, folder: str, toc: cd_toc.DiscToc, mp3_path: str,
               audio_md5: str, disc_outcome: str, releases: list[dict],
               rip_report: "cd_toc.RipReport | None", acoustid_key: str | None) -> dict:
    """Register the file, write TOC-exact passages, resolve each track's
    identity, and record every decision -- the CD-ripping analogue of
    `segment_dao.commit_segments()`, same shape, ground-truth boundaries
    instead of an inferred cascade `[SPEC-RIP-010]`.
    """
    st = os.stat(mp3_path)
    total_ms = toc.tracks[-1].end_ms if toc.tracks else 0
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    cur = conn.execute(
        "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,"
        "                   first_seen,last_seen) VALUES (?1,?2,?3,?4,'mp3',?5,?6,?6)",
        (audio_md5, mp3_path, st.st_size, st.st_mtime, total_ms, now))
    file_id = cur.lastrowid

    boundary_src = f"imported:{'eac-cue' if toc.source == 'eac-cue' else 'cdrdao-toc'}"
    identified = ambiguous = unidentified = failed = 0

    for track in toc.tracks:
        start_ms, end_ms = track.start_ms, track.end_ms

        # Both kinds, one identification `[SPEC-SA-110]`, `[GDE-BMK-030]`.
        radio_pid = conn.execute(
            "INSERT INTO passages (file_id,kind,start_ms,end_ms,boundary_src) "
            "VALUES (?1,'radio',?2,?3,?4)",
            (file_id, start_ms, end_ms, boundary_src)).lastrowid
        album_pid = conn.execute(
            "INSERT INTO passages (file_id,kind,start_ms,end_ms,"
            "lead_in_ms,lead_out_ms,gain_db,boundary_src) "
            "VALUES (?1,'album',?2,?3,0,0,0.0,?4)",
            (file_id, start_ms, end_ms, boundary_src)).lastrowid

        # -------------------------------------------------- resolve identity
        # `candidates` and `candidate_releases` stay index-aligned so the
        # single-match branch below can find which release the one
        # surviving candidate actually came from, for its artist credits --
        # not necessarily `releases[0]`, if that release lacks this track.
        candidates: list[dict] = []
        candidate_releases: list[dict] = []
        if disc_outcome != "none":
            for rel in releases[:8]:
                c = _candidate_for(rel, track.number)
                if c is not None and all(c["mbid"] != seen["mbid"] for seen in candidates):
                    candidates.append(c)
                    candidate_releases.append(rel)

        cd_text_title = track.title or toc.title

        if cd_text_title:
            # CD-TEXT is the default shown identity when the disc carries
            # it `[SPEC-RIP-066]` -- always through the placeholder/review
            # path (no stable artist mbid comes from CD-TEXT alone, so
            # there is nothing to link even when a performer name is also
            # printed), with any MusicBrainz answer sitting one click away
            # `[SPEC-RIP-068]`, regardless of whether Disc ID resolved a
            # single release or several.
            mbid, source = f"local:audio:{audio_md5}:{start_ms}", "cd:text"
            conn.execute(
                "INSERT OR IGNORE INTO recordings (mbid,title,length_ms,source) "
                "VALUES (?1,?2,?3,?4)",
                (mbid, cd_text_title, end_ms - start_ms, source))
            _write_id_check(conn, radio_pid, mbid, candidates, now)
            ambiguous += 1
        elif len(candidates) == 1:
            c = candidates[0]
            mbid, source = c["mbid"], "musicbrainz"
            conn.execute(
                "INSERT OR IGNORE INTO recordings (mbid,title,length_ms,source) "
                "VALUES (?1,?2,?3,?4)", (mbid, c["title"], end_ms - start_ms, source))
            for artist_mbid, name in _artists_for(candidate_releases[0], track.number):
                conn.execute(
                    "INSERT OR IGNORE INTO artists (mbid,name,source) VALUES (?1,?2,?3)",
                    (artist_mbid, name, source))
                conn.execute(
                    "INSERT OR IGNORE INTO recording_artists (mbid,artist_mbid,weight,source) "
                    "VALUES (?1,?2,1.0,?3)", (mbid, artist_mbid, source))
            identified += 1
        elif len(candidates) > 1:
            mbid, source = f"local:audio:{audio_md5}:{start_ms}", "cd:ambiguous"
            conn.execute(
                "INSERT OR IGNORE INTO recordings (mbid,title,length_ms,source) "
                "VALUES (?1,?2,?3,?4)",
                (mbid, f"unidentified track {track.number} (disc ambiguous)",
                 end_ms - start_ms, source))
            _write_id_check(conn, radio_pid, mbid, candidates, now)
            ambiguous += 1
        else:
            rec = None
            if acoustid_key:
                start_s, end_s = start_ms / 1000.0, end_ms / 1000.0
                rec = segment_dao.identify_recording(mp3_path, start_s, end_s, acoustid_key)
            if rec is not None:
                mbid, source = rec["mbid"], "cd:acoustid"
                conn.execute(
                    "INSERT OR IGNORE INTO recordings (mbid,title,length_ms,source) "
                    "VALUES (?1,?2,?3,?4)", (mbid, rec["title"], end_ms - start_ms, source))
                for artist_mbid, name in rec["artists"]:
                    conn.execute(
                        "INSERT OR IGNORE INTO artists (mbid,name,source) VALUES (?1,?2,?3)",
                        (artist_mbid, name, source))
                    conn.execute(
                        "INSERT OR IGNORE INTO recording_artists (mbid,artist_mbid,weight,source) "
                        "VALUES (?1,?2,1.0,?3)", (mbid, artist_mbid, source))
                identified += 1
            else:
                mbid, source = f"local:audio:{audio_md5}:{start_ms}", "cd:unidentified"
                conn.execute(
                    "INSERT OR IGNORE INTO recordings (mbid,title,length_ms,source) "
                    "VALUES (?1,?2,?3,?4)",
                    (mbid, f"unidentified track {track.number}", end_ms - start_ms, source))
                _write_id_check(conn, radio_pid, mbid, [], now)
                unidentified += 1

        for pid in (radio_pid, album_pid):
            conn.execute(
                "INSERT INTO passage_recordings (passage_id,mbid,weight,source) "
                "VALUES (?1,?2,1.0,?3)", (pid, mbid, source))

        # ----------------------------------------------- rip-failure record
        if rip_report is not None:
            tr = next((t for t in rip_report.tracks if t.number == track.number), None)
            if tr is not None and not tr.ok:
                conn.execute(
                    "INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,"
                    "detail,decided_at) VALUES (?1,'rip','verification_failed',NULL,?2,?3)",
                    (audio_md5, json.dumps({"track": track.number, "detail": tr.detail}), now))
                failed += 1

    # ------------------------------------------------------------- disc decision
    detail = {
        "track_count": toc.track_count, "format": toc.source,
        "candidates": len(releases),
        "chosen": releases[0].get("id") if disc_outcome == "exact" and len(releases) == 1
        else None,
        "titles": [r.get("title") for r in releases[:5]],
    }
    conn.execute(
        "INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
        "VALUES (?1,'rip',?2,?3,?4,?5)",
        (audio_md5, disc_outcome,
         1.0 if disc_outcome == "exact" and len(releases) == 1 else None,
         json.dumps(detail), now))

    return {"tracks": toc.track_count, "identified": identified,
            "ambiguous": ambiguous, "unidentified": unidentified,
            "verification_failed": failed, "file_id": file_id,
            "disc_outcome": disc_outcome, "candidates": len(releases)}


def _write_id_check(conn, passage_id: int, stored_mbid: str,
                     candidates: list[dict], checked_at: str) -> None:
    """One `id_checks` row per ambiguous/unresolved passage -- the same
    table, same shape `fingerprint_ids.py` writes, so the existing
    `/review` queue picks these up unmodified `[SPEC-RIP-069/072]`.
    `verdict='unmatched'` (never `'contradicted'`: nothing here disagrees
    with a stored id, there simply isn't a real one yet) is what
    `review_queue()`'s own WHERE clause requires to surface the row, and
    `is_mbid()` failing on the `local:audio:...` placeholder is what grades
    it `no-mbid`, rank 0, top of the queue -- before `suggested` is even
    considered.
    """
    conn.execute(
        "INSERT OR REPLACE INTO id_checks (passage_id,stored_mbid,verdict,score,"
        "suggested,checked_at) VALUES (?1,?2,'unmatched',?3,?4,?5)",
        (passage_id, stored_mbid,
         candidates[0]["score"] if candidates else None,
         json.dumps(candidates) if candidates else None, checked_at))


# ---------------------------------------------------------------------- main

def do_ingest(db_path: str, folder: str, commit: bool) -> dict:
    import sqlite3

    fmt, toc_path = find_rip(folder)
    toc = cd_toc.parse_eac_cue(toc_path) if fmt == "eac" else cd_toc.parse_cdrdao_toc(toc_path)
    if not toc.tracks:
        raise ValueError(f"no tracks found in {toc_path!r}")
    if not toc.data_file:
        raise ValueError(f"{toc_path!r} does not name its own audio file")

    wav_path = os.path.join(folder, toc.data_file)
    if not os.path.exists(wav_path):
        raise FileNotFoundError(f"{toc_path!r} names {wav_path!r}, which does not exist")

    total_ms = audio_duration.probe_duration_ms(wav_path)
    if not total_ms:
        raise ValueError(f"could not decode {wav_path!r}")
    cd_toc.finalize_leadout(toc, int(round(total_ms)))

    rip_report = None
    log_path = find_log(folder)
    if fmt == "eac" and log_path:
        rip_report = cd_toc.parse_eac_log(log_path)

    mp3_path = os.path.splitext(wav_path)[0] + ".mp3"
    if not encode_to_mp3(wav_path, mp3_path):
        raise RuntimeError(f"ffmpeg failed to encode {wav_path!r}")

    audio_md5 = ingest_folder.audio_md5(mp3_path)
    if audio_md5 is None:
        raise RuntimeError(f"could not hash the encoded {mp3_path!r}")

    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(db_path, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA foreign_keys = ON")
    existing = conn.execute(
        "SELECT file_id FROM files WHERE audio_md5=?1", (audio_md5,)).fetchone()
    if existing is not None:
        conn.close()
        raise RuntimeError(
            f"a file with this audio already exists (file_id={existing[0]}) -- "
            f"this disc appears to already be in the library")

    disc_outcome, releases = lookup_disc_id(toc)
    key = secret.acoustid_key(required=False)

    result: dict = {}
    if commit:
        conn.execute("BEGIN IMMEDIATE")
        result = commit_rip(conn, folder, toc, mp3_path, audio_md5,
                             disc_outcome, releases, rip_report, key)
        conn.commit()
    else:
        result = {"tracks": toc.track_count, "disc_outcome": disc_outcome,
                  "candidates": len(releases), "dry_run": True}
    conn.close()
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--folder", required=True)
    ap.add_argument("--paranoia", type=int, default=2, choices=range(0, 4),
                     help="informational only -- the setting lives in EAC/cdrdao "
                          "itself under the person-assisted flow [SPEC-RIP-050]")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        result = do_ingest(args.db, args.folder, args.commit)
    except Exception as e:                                    # noqa: BLE001
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            say(f"error: {e}")
        return 1

    if args.json:
        print(json.dumps({"ok": True, **result}))
    else:
        if result.get("dry_run"):
            say(f"{result['tracks']} track(s); disc {result['disc_outcome']} "
                f"({result['candidates']} candidate release(s)) -- dry run, "
                f"nothing written; re-run with --commit")
        else:
            say(f"{result['tracks']} track(s): {result['identified']} identified, "
                f"{result['ambiguous']} ambiguous (in the review queue), "
                f"{result['unidentified']} unidentified, "
                f"{result['verification_failed']} verification failure(s) "
                f"(disc: {result['disc_outcome']}, {result['candidates']} candidate(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
