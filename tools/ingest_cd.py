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

    python tools/ingest_cd.py <db> --folder <rip-output-dir> [--json]
    python tools/ingest_cd.py <db> --folder <rip-output-dir> --commit
        [--release MBID] [--into ALBUM-FOLDER] [--keep-flac | --no-keep-flac] [--json]

**Without `--commit` it is a preview, and a true one** `[SPEC-CDI-040]`: the CUE,
the log and the Disc ID lookup, every candidate edition with its track list and
how much of it the library already holds -- nothing encoded, nothing written.
Until 2026-09-29 the "dry run" encoded the whole MP3 and left it on disk first.

`--release` is the edition a person chose from the preview: it is the answer,
so no track of it waits in the review queue for a down-select. `--into` is the
album's permanent folder; the rip is moved there before anything is recorded,
so the catalogue names where the album lives, not the inbox it was ripped into
`[SPEC-CDI-020]`. After the catalogue is written the WAV is removed
`[SPEC-RIP-040]` -- it was left beside every album until 2026-09-29 -- with a
FLAC copy made first if `--keep-flac`, or if the desktop player's Settings
switch says so when neither flag is given `[SPEC-CDI-058]`. The CUE's `FILE`
line is then pointed at the MP3, so the sheet still names a file that exists,
and the sheet is kept as UTF-8 whatever code page the ripper wrote it in. The
log is left exactly as written: it is the rip's evidence, and EAC's carries a
checksum over its exact text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
# `LEMPI_MUSICBRAINZ_URL` points the lookup elsewhere -- a test's own server,
# answering one disc, so the page's whole path runs without the network.
MB_DISCID_BASE = os.environ.get("LEMPI_MUSICBRAINZ_URL", "https://musicbrainz.org").rstrip("/") + "/ws/2/discid"


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


def encode_to_flac(wav_path: str, out_path: str) -> bool:
    """The lossless copy kept when a person asked for one `[SPEC-CDI-058]`."""
    if not FFMPEG:
        return False
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", wav_path, "-c:a", "flac", out_path],
                       capture_output=True, timeout=1800)
    return r.returncode == 0 and os.path.exists(out_path)


def keep_lossless_setting(db_path: str) -> bool:
    """The Lempi skin's switch, *keep a lossless FLAC copy of CD rips*, from the
    desktop player's own settings `[SPEC-CDI-058]`. Off when unset, and off
    when it cannot be read: keeping a copy is what a person asks for."""
    try:
        conn = lempi_db.connect(db_path, lempi_db.ROLE_LISTENER)
        try:
            row = conn.execute(
                "SELECT value FROM player_settings WHERE key = 'keep_lossless_rips'").fetchone()
        finally:
            conn.close()
    except Exception:                                          # noqa: BLE001
        return False
    return row is not None and row[0] == "1"


_BAD_NAME = str.maketrans({c: "_" for c in '<>:"/\\|?*'})


def safe_name(text: str) -> str:
    """A folder name Windows accepts: no reserved characters, no trailing dot
    or space."""
    return (text or "").translate(_BAD_NAME).strip().rstrip(". ") or "Unknown"


def proposed_folder(artist: str | None, album: str | None, year: str | None) -> str:
    """`Artist\\Album (Year)`, relative to the music folder `[SPEC-CDI-050]`."""
    title = safe_name(album or "Unknown album")
    if year:
        title = f"{title} ({year})"
    return os.path.join(safe_name(artist or "Unknown artist"), title)


def move_rip(folder: str, into: str) -> str:
    """Move everything the rip left in `folder` to `into`, the album's
    permanent home; returns `into`. Refuses to write over a file already there
    -- an album folder that exists may hold another edition."""
    os.makedirs(into, exist_ok=True)
    names = [n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n))]
    clash = [n for n in names if os.path.exists(os.path.join(into, n))]
    if clash:
        raise FileExistsError(f"{into!r} already holds {clash[:3]}: not moved over")
    for n in names:
        shutil.move(os.path.join(folder, n), os.path.join(into, n))
    try:
        os.rmdir(folder)                        # only if the rip left it empty
    except OSError:
        pass
    return into


_CUE_FILE = re.compile(r'^(\s*FILE\s+"[^"]*?)\.[A-Za-z0-9]+("\s+)\S+', re.I)


def point_cue_at(cue_path: str, ext: str = ".mp3", kind: str = "MP3") -> None:
    """Point the CUE's first `FILE` line at the same name with `ext`, as `kind`,
    and keep the sheet as UTF-8 -- once the WAV is gone, a sheet naming it would
    send any player that opens it to a file that does not exist.

    The sheet is read as the ripper wrote it (`cd_toc.read_cue_text`: UTF-8, or
    the Windows code page EAC and CUERipper use) and written back as UTF-8
    without a byte-order mark, as Lempi's own sheets are (`player/src/cue.rs`):
    a code-page sheet reads differently under each Windows locale, UTF-8 the
    same everywhere. Its line endings are kept. Already in that form, it is
    left byte-for-byte as it was, so running this again changes nothing."""
    text = cd_toc.read_cue_text(cue_path)
    out, done = [], False
    for line in text.splitlines(keepends=True):
        if not done:
            line, n = _CUE_FILE.subn(lambda m: m.group(1) + ext + m.group(2) + kind, line, count=1)
            done = bool(n)
        out.append(line)
    data = "".join(out).encode("utf-8")
    with open(cue_path, "rb") as fh:
        if fh.read() == data:
            return
    with open(cue_path, "wb") as fh:
        fh.write(data)


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


def release_summary(release: dict, track_count: int, conn=None) -> dict:
    """One candidate edition as the preview shows it `[SPEC-CDI-040]`: who,
    what, when, its track list for this disc, the folder it would be filed
    under, and how many of its recordings the library already plays -- the
    "already in the library" signal `[SPEC-CDI-045]`."""
    credit = "".join((a.get("name") or "") + (a.get("joinphrase") or "")
                     for a in release.get("artist-credit") or []).strip()
    media = release.get("media") or []
    medium = next((m for m in media if len(m.get("tracks") or []) == track_count), media[0] if media else {})
    tracks = [{"position": t.get("position"), "title": t.get("title"), "length_ms": t.get("length")}
              for t in medium.get("tracks") or []]
    mbids = [((t.get("recording") or {}).get("id")) for t in medium.get("tracks") or []]
    mbids = [m for m in mbids if m]
    held = 0
    if conn is not None and mbids:
        q = f"SELECT COUNT(DISTINCT mbid) FROM passage_recordings WHERE mbid IN ({','.join('?' * len(mbids))})"
        held = conn.execute(q, mbids).fetchone()[0]
    year = (release.get("date") or "")[:4] or None
    return {"id": release.get("id"), "title": release.get("title"), "artist": credit or None,
            "year": year, "country": release.get("country"), "tracks": tracks,
            "in_library": held, "folder": proposed_folder(credit, release.get("title"), year)}


# -------------------------------------------------------------------- commit

def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def commit_rip(conn, folder: str, toc: cd_toc.DiscToc, mp3_path: str,
               audio_md5: str, disc_outcome: str, releases: list[dict],
               rip_report: "cd_toc.RipReport | None", acoustid_key: str | None,
               chosen: bool = False) -> dict:
    """Register the file, write TOC-exact passages, resolve each track's
    identity, and record every decision -- the CD-ripping analogue of
    `segment_dao.commit_segments()`, same shape, ground-truth boundaries
    instead of an inferred cascade `[SPEC-RIP-010]`.

    `chosen`: `releases` is the one edition a person picked from the preview
    `[SPEC-CDI-040]`. It is then the answer for every track it names -- CD-TEXT
    no longer sends those to the review queue `[SPEC-RIP-066]`, since a person
    has already looked.
    """
    st = os.stat(mp3_path)
    total_ms = toc.tracks[-1].end_ms if toc.tracks else 0
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    # `[SPEC-RLK-150]` precondition 3, through the same helper that computed
    # the hash -- one definition of what ffmpeg is here, not a second.
    ingest_folder.ensure_md5_generator_column(conn)
    cur = conn.execute(
        "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,"
        "                   first_seen,last_seen,md5_generator)"
        " VALUES (?1,?2,?3,?4,'mp3',?5,?6,?6,?7)",
        (audio_md5, mp3_path, st.st_size, st.st_mtime, total_ms, now,
         ingest_folder.md5_generator()))
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

        if cd_text_title and not (chosen and len(candidates) == 1):
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
    certain = chosen or (disc_outcome == "exact" and len(releases) == 1)
    detail = {
        "track_count": toc.track_count, "format": toc.source,
        "candidates": len(releases),
        "chosen": releases[0].get("id") if certain and releases else None,
        "chosen_by": "person" if chosen else None,
        "disc_id": cd_toc.musicbrainz_disc_id(toc),
        "titles": [r.get("title") for r in releases[:5]],
    }
    conn.execute(
        "INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
        "VALUES (?1,'rip',?2,?3,?4,?5)",
        (audio_md5, "chosen" if chosen else disc_outcome, 1.0 if certain else None,
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

def open_rip(folder: str):
    """The rip in `folder`, parsed and checked, before anything is decided:
    `(fmt, cue_path, toc, audio_path, rip_report)`."""
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
    log_path = find_log(folder)
    rip_report = cd_toc.parse_eac_log(log_path) if fmt == "eac" and log_path else None
    return fmt, toc_path, toc, wav_path, rip_report


def rip_verdict(rip_report) -> dict | None:
    """The log's word on the rip, per track `[SPEC-CDI-035]`."""
    if rip_report is None:
        return None
    return {"all_ok": rip_report.all_ok,
            "tracks": [{"number": t.number, "ok": t.ok, "detail": t.detail} for t in rip_report.tracks]}


def preview(db_path: str, folder: str) -> dict:
    """Everything the page shows before anything is done `[SPEC-CDI-040]` --
    nothing encoded, nothing written."""
    fmt, _cue, toc, wav_path, rip_report = open_rip(folder)
    disc_outcome, releases = lookup_disc_id(toc)
    conn = lempi_db.connect(db_path, lempi_db.ROLE_LIBRARY)
    try:
        rels = [release_summary(r, toc.track_count, conn) for r in releases[:8]]
    finally:
        conn.close()
    cd_folder = proposed_folder(toc.performer, toc.title, None) if toc.title else None
    return {"dry_run": True, "format": fmt, "audio": os.path.basename(wav_path),
            "audio_bytes": os.path.getsize(wav_path), "tracks": toc.track_count,
            "track_list": [{"number": t.number, "start_ms": t.start_ms, "end_ms": t.end_ms,
                            "title": t.title, "performer": t.performer} for t in toc.tracks],
            "cd_text": {"title": toc.title, "performer": toc.performer},
            "disc_id": cd_toc.musicbrainz_disc_id(toc), "disc_outcome": disc_outcome,
            "candidates": len(releases), "releases": rels, "rip": rip_verdict(rip_report),
            "folder": rels[0]["folder"] if len(rels) == 1 else cd_folder}


def do_ingest(db_path: str, folder: str, commit: bool, release: str | None = None,
              into: str | None = None, keep_flac: bool | None = None) -> dict:
    import sqlite3  # noqa: F401  -- the connection below is lempi_db's

    if not commit:
        return preview(db_path, folder)

    fmt, _cue, toc, wav_path, rip_report = open_rip(folder)
    if keep_flac is None:
        keep_flac = keep_lossless_setting(db_path)

    say("encoding the MP3 ...")
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
    try:
        existing = conn.execute(
            "SELECT file_id, path FROM files WHERE audio_md5=?1", (audio_md5,)).fetchone()
        if existing is not None:
            raise RuntimeError(
                f"this disc is already in the library as {existing[1]!r} (file_id={existing[0]})")

        say("looking the disc up ...")
        disc_outcome, releases = lookup_disc_id(toc)
        if release:
            releases = [r for r in releases if r.get("id") == release]
            if not releases:
                raise ValueError(f"release {release} is not among this disc's matches")

        if into:
            say(f"filing it in {into} ...")
            folder = move_rip(folder, into)
            wav_path = os.path.join(folder, os.path.basename(wav_path))
            mp3_path = os.path.join(folder, os.path.basename(mp3_path))

        say("writing the album ...")
        conn.execute("BEGIN IMMEDIATE")
        result = commit_rip(conn, folder, toc, mp3_path, audio_md5, disc_outcome, releases,
                            rip_report, secret.acoustid_key(required=False), chosen=bool(release))
        conn.commit()
    finally:
        conn.close()

    # The catalogue is written; only now is the WAV touched, and only once
    # anything asked to be kept from it has been made `[SPEC-RIP-040]`.
    result["folder"] = folder
    result["flac"] = None
    if keep_flac:
        say("keeping a lossless FLAC copy ...")
        flac_path = os.path.splitext(wav_path)[0] + ".flac"
        if not encode_to_flac(wav_path, flac_path):
            result["wav_kept"] = wav_path
            say(f"the FLAC copy failed; the WAV is kept at {wav_path}")
            return result
        result["flac"] = flac_path
    os.remove(wav_path)
    result["wav_removed"] = True
    cue = next((os.path.join(folder, n) for n in os.listdir(folder) if n.lower().endswith(".cue")), None)
    if cue:
        point_cue_at(cue)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--folder", required=True)
    ap.add_argument("--paranoia", type=int, default=2, choices=range(0, 4),
                     help="informational only -- the setting lives in EAC/cdrdao "
                          "itself under the person-assisted flow [SPEC-RIP-050]")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--release", help="the edition chosen from the preview (a MusicBrainz release id)")
    ap.add_argument("--into", help="the album's permanent folder; the rip is moved there first")
    flac = ap.add_mutually_exclusive_group()
    flac.add_argument("--keep-flac", dest="keep_flac", action="store_true", default=None,
                      help="keep a lossless FLAC copy (default: the Lempi Settings switch)")
    flac.add_argument("--no-keep-flac", dest="keep_flac", action="store_false")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        result = do_ingest(args.db, args.folder, args.commit, release=args.release,
                           into=args.into, keep_flac=args.keep_flac)
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
                f"({result['candidates']} candidate release(s)) -- a preview, "
                f"nothing written; re-run with --commit")
            for r in result["releases"]:
                say(f"  {r['id']}  {r['artist']} - {r['title']} ({r['year'] or '?'}, "
                    f"{r['country'] or '?'})  {r['in_library']}/{len(r['tracks'])} already held")
        else:
            say(f"{result['tracks']} track(s): {result['identified']} identified, "
                f"{result['ambiguous']} ambiguous (in the review queue), "
                f"{result['unidentified']} unidentified, "
                f"{result['verification_failed']} verification failure(s) "
                f"(disc: {result['disc_outcome']}, {result['candidates']} candidate(s))")
            say(f"in {result['folder']}; WAV {'removed' if result.get('wav_removed') else 'kept'}"
                + (f"; FLAC kept at {result['flac']}" if result.get("flac") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
