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
`[SPEC-CDI-020]`. The move renames the files the ripper named after the disc
to match that folder, the album held to the Settings page's length
(`--album-name-max`), and refuses a destination Windows could not open before
anything is encoded `[SPEC063]`. After the catalogue is written the WAV is removed
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
import sqlite3
import subprocess
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import audio_duration        # noqa: E402
import cd_toc                 # noqa: E402
import artist_sort            # noqa: E402  -- an artist's sort name [REQ-VIS-182]
import fetch_cover_art        # noqa: E402  -- the disc's cover [SPEC-COV-060]
import fetch_releases         # noqa: E402  -- get(), UA, rate-limit/backoff
import ingest_folder          # noqa: E402  -- audio_md5(), LOCAL_PREFIX
import passage_hold           # noqa: E402  -- a damaged track held back [SPEC-HOLD-010]
from byte_hash import ensure_sha256_column, sha256_file  # noqa: E402  -- [REQ-AND-960]
import secret                 # noqa: E402
import sidecars               # noqa: E402  -- the rip log, AccurateRip and CUE hashed [GDE-INT-175]
import segment_dao           # noqa: E402  -- identify_recording() (AcoustID)

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
    if len(cues) > 1:
        # Several discs in one folder [SPEC-RIP-118]: taking the first CUE
        # took disc 1's identity for every file, 2026-10-15. The import page
        # gives each disc its own folder when it is previewed.
        raise ValueError(f"{folder!r} holds {len(cues)} CUE sheets -- {len(cues)} discs; each needs a folder "
                         "of its own (the import page's preview makes one)")
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


# The longest album name a folder or file is given `[SPEC-NAM-010]`, unless
# the Lempi skin's Settings page says otherwise. The same numbers as the
# player's `CD_ALBUM_NAME_MAX_*` in `player_store.rs`, which stores the choice.
ALBUM_NAME_MAX = 60
ALBUM_NAME_RANGE = (20, 200)
# Windows' MAX_PATH less its terminating NUL: what an ordinary program on the
# hub can open with `LongPathsEnabled` off, as it was when *The Idler Wheel*
# failed to file at 278 characters, 2026-10-13 `[SPEC-NAM-030]`.
WINDOWS_PATH_MAX = 259


def album_name_max_setting(db_path: str) -> int:
    """The Lempi skin's *album names, at most N characters* `[SPEC-NAM-010]`,
    from the desktop player's own settings. The default when unset, unreadable,
    or outside the range the Settings page offers."""
    try:
        conn = lempi_db.connect(db_path, lempi_db.ROLE_LISTENER)
        try:
            row = conn.execute(
                "SELECT value FROM player_settings WHERE key = 'cd_album_name_max'").fetchone()
        finally:
            conn.close()
        n = int(row[0]) if row else ALBUM_NAME_MAX
    except Exception:                                          # noqa: BLE001
        return ALBUM_NAME_MAX
    return n if ALBUM_NAME_RANGE[0] <= n <= ALBUM_NAME_RANGE[1] else ALBUM_NAME_MAX


_BAD_NAME = str.maketrans({c: "_" for c in '<>:"/\\|?*'})
_TRAILING = " .,;:-_&+"


def safe_name(text: str) -> str:
    """A folder name Windows accepts: no reserved characters, no trailing dot
    or space."""
    return (text or "").translate(_BAD_NAME).strip().rstrip(". ") or "Unknown"


def shorten(text: str, limit: int) -> str:
    """`text` held to `limit` characters `[SPEC-NAM-020]`: cut at the last word
    that fits, unless that loses more than half; a parenthesis the cut left open
    dropped; no trailing punctuation, dot or space -- Windows refuses a folder
    name ending in either of the last two."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    cut = cut.rstrip(_TRAILING)
    if cut.count("(") > cut.count(")") and cut.rfind("(") > 0:
        cut = cut[:cut.rfind("(")].rstrip(_TRAILING)
    while True:
        head, _, last = cut.rpartition(" ")
        if last.lower() not in _DANGLING or len(head) < limit // 2:
            break
        cut = head.rstrip(_TRAILING)
    return cut or text[:limit].rstrip(". ")


# Words a cut should not end on: "...of the Screw and" reads as a mistake,
# "...of the Screw" as a title.
_DANGLING = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on",
             "or", "the", "to", "with", "&", "+", "-"}


def proposed_folder(artist: str | None, album: str | None, year: str | None,
                    limit: int = ALBUM_NAME_MAX) -> str:
    """`Artist\\Album (Year)`, relative to the music folder `[SPEC-CDI-050]`,
    the album held to `limit` characters before the year `[SPEC-NAM-020]`."""
    title = shorten(safe_name(album or "Unknown album"), limit)
    if year:
        title = f"{title} ({year})"
    return os.path.join(safe_name(artist or "Unknown artist"), title)


_YEAR = re.compile(r"\s\(\d{4}\)$")
# The ripper's own word on the disc -- CUERipper's `(Disc 1)`, on a single disc
# as readily as on a set -- replaced by the set's when there is one.
_RIPPER_DISC = re.compile(r"\s*\((?:Disc|CD)\s*\d+\)$", re.I)
_TRACK_FILE = re.compile(r"^\d{1,3}[ .\-_]")
_HAS_DISC = re.compile(r"^\d{1,2}-\d{1,3}[ .\-_]")
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}


def disc_of_set(releases: list[dict]) -> tuple[int, int] | None:
    """`(disc, of)` when the rip is one disc of a set and which is settled:
    one edition, cut to this disc by `for_this_disc` `[SPEC-RIP-106]`. `None`
    for a single disc, or while the edition is still a choice."""
    if len(releases) != 1:
        return None
    r = releases[0]
    media, of = r.get("media") or [], r.get("_disc_count") or 0
    if of < 2 or len(media) != 1:
        return None
    return media[0].get("position") or 1, of


def _with_disc(name: str, disc: int, into: str) -> str:
    """`name` as one disc of a set files it beside the others `[SPEC-RIP-108]`:
    a track file `2-01. Title`, anything else `... (Disc 2)` in place of
    whatever disc the ripper wrote; a cover keeps its name unless the album
    folder already has one by it."""
    stem, ext = os.path.splitext(name)
    if ext.lower() in _IMAGE_EXT:
        return name if not os.path.exists(os.path.join(into, name)) else f"{stem} (Disc {disc}){ext}"
    if _HAS_DISC.match(name):
        return name
    if _TRACK_FILE.match(name):
        return f"{disc}-{name}"
    return f"{_RIPPER_DISC.sub('', stem)} (Disc {disc}){ext}"


def renamed_files(folder: str, into: str, artist: str | None, limit: int,
                  extra: tuple = (), disc: int | None = None) -> dict[str, str]:
    """The new name of each file the move renames `[SPEC-NAM-040]`, old to
    new; a file not in it keeps its name.

    A ripper names its files after the disc -- CUERipper `Artist - Album` and
    then `(Disc 1).cue`, EAC the same without the suffix -- and names the rip's
    folder in the inbox the same way. So a file whose name starts with the rip
    folder's name, where the CUE's does too, has that part replaced by
    `Artist - Album`, the album as its new folder names it (less the year) and
    held to `limit`: the names follow the folder a person saw and could edit.
    A name the ripper cut short to fit its own path -- `...Mor(Dis.accurip`
    beside `...Mor(Disc 1).cue` -- is given the CUE's whole new name. A rip
    whose names do not start with its folder's (track files, `01. Title.wav`)
    keeps them. `extra` names files not made yet: the MP3, the FLAC copy.

    `disc`, when the rip is that disc of a set, is then written into every
    name (`_with_disc`) `[SPEC-RIP-108]`: the set's discs share one folder, and
    the ripper's names cannot be trusted to keep them apart -- *An American
    Treasure*'s second disc arrived with no disc in its names at all."""
    names = [n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n))]
    names += [n for n in extra if n not in names]
    anchor = os.path.basename(os.path.normpath(folder))
    cues = [n for n in names if n.lower().endswith(".cue")]
    out = {}
    if cues and cues[0].startswith(anchor):
        album = shorten(safe_name(_YEAR.sub("", os.path.basename(os.path.normpath(into)))), limit)
        prefix = f"{safe_name(artist)} - {album}" if artist else album
        cue_stem = os.path.splitext(cues[0])[0]
        for n in names:
            stem, ext = os.path.splitext(n)
            if not n.startswith(anchor):
                continue
            if cue_stem.startswith(stem) and stem != cue_stem:
                new = prefix + cue_stem[len(anchor):] + ext          # cut short by the ripper
            else:
                new = prefix + n[len(anchor):]
            if new != n:
                out[n] = new
    if disc:
        for n in names:
            new = _with_disc(out.get(n, n), disc, into)
            if new != n:
                out[n] = new
    return out


class PathTooLong(ValueError):
    """A file the move would make is longer than Windows opens `[SPEC-NAM-030]`."""


def check_paths(folder: str, into: str, renames: dict[str, str], extra: tuple = ()) -> None:
    """Refuse, before anything is encoded or moved, a destination this
    computer cannot hold `[SPEC-NAM-030]`: two files given one name, a name
    the folder already holds `[SPEC-AGN-015]`, or -- on Windows -- a path
    longer than `WINDOWS_PATH_MAX`. The message names the longest and what to
    do about it."""
    names = [n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n))]
    names += [n for n in extra if n not in names]
    final = [renames.get(n, n) for n in names]
    twice = sorted({n for n in final if final.count(n) > 1})
    if twice:
        raise ValueError(f"the move would give two files one name: {twice[:3]}")
    # A file already there is a refusal too, and found here, before the
    # encode, rather than by the move after it [SPEC-AGN-015]: Highway 61's
    # second rip was encoded whole, then refused for its sheet's name.
    clash = sorted(n for n in set(final) if os.path.exists(os.path.join(into, n)))
    if clash:
        raise FileExistsError(f"{into!r} already holds {clash[:3]}: not moved over")
    if os.name != "nt":
        return
    longest = max((os.path.join(os.path.abspath(into), n) for n in final), key=len, default="")
    if len(longest) > WINDOWS_PATH_MAX:
        raise PathTooLong(
            f"{longest!r} would be {len(longest)} characters, and Windows opens at most {WINDOWS_PATH_MAX}: "
            "give the album a shorter folder name, or lower 'album names, at most' on the Lempi "
            "Settings page -- nothing was moved")


def rename_in_cue(cue_path: str, renames: dict[str, str]) -> None:
    """Each `FILE` line naming a renamed file names its new name `[SPEC-NAM-040]`,
    so the sheet still opens what lies beside it. Written back as UTF-8, as
    `point_cue_at` keeps it; untouched when nothing it names was renamed."""
    text = cd_toc.read_cue_text(cue_path)
    out = [_CUE_NAME.sub(lambda m: m.group(1) + renames.get(m.group(2), m.group(2)) + m.group(3), line, count=1)
           for line in text.splitlines(keepends=True)]
    if out == text.splitlines(keepends=True):
        return
    with open(cue_path, "wb") as fh:
        fh.write("".join(out).encode("utf-8"))


_CUE_NAME = re.compile(r'^(\s*FILE\s+")([^"]*)(")', re.I)


def move_rip(folder: str, into: str, renames: dict[str, str] | None = None) -> str:
    """Move everything the rip left in `folder` to `into`, the album's
    permanent home, each file under its name in `renames` if it has one;
    returns `into`. Refuses to write over a file already there -- an album
    folder that exists may hold another edition.

    All or nothing `[SPEC-NAM-050]`: a move that fails part-way puts back what
    it moved and removes the folders it made, so the rip is in the inbox as it
    was and no empty album folder is left behind -- *The Idler Wheel* left one,
    2026-10-13."""
    renames = renames or {}
    names = [n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n))]
    check_paths(folder, into, renames)
    clash = [renames.get(n, n) for n in names if os.path.exists(os.path.join(into, renames.get(n, n)))]
    if clash:
        raise FileExistsError(f"{into!r} already holds {clash[:3]}: not moved over")
    made, d = [], os.path.abspath(into)
    while not os.path.isdir(d):
        made.append(d)
        d = os.path.dirname(d)
    moved, sheets = [], []
    try:
        os.makedirs(into, exist_ok=True)
        for n in names:
            dst = os.path.join(into, renames.get(n, n))
            shutil.move(os.path.join(folder, n), dst)
            moved.append((n, dst))
        for n in names:
            if renames and n.lower().endswith(".cue"):
                sheets.append(os.path.join(into, renames.get(n, n)))
                rename_in_cue(sheets[-1], renames)
    except Exception:
        back = {new: old for old, new in renames.items()}
        for sheet in sheets:
            try:
                rename_in_cue(sheet, back)
            except OSError:
                pass
        for n, dst in reversed(moved):
            try:
                shutil.move(dst, os.path.join(folder, n))
            except OSError:
                pass
        for d in made:                          # deepest first
            try:
                os.rmdir(d)
            except OSError:
                break
        raise
    try:
        os.rmdir(folder)                        # only if the rip left it empty
    except OSError:
        pass
    return into


_CUE_FILE = re.compile(r'^(\s*FILE\s+"[^"]*?)\.[A-Za-z0-9]+("\s+)\S+', re.I)


def point_cue_at(cue_path: str, ext: str = ".mp3", kind: str = "MP3") -> None:
    """Point every `FILE` line of the CUE at the same name with `ext`, as
    `kind` -- one for an image rip, one per track for a tracks-mode rip
    `[SPEC-CDI-090]` -- and keep the sheet as UTF-8: once the WAVs are gone, a
    sheet naming them would send any player that opens it to files that do not
    exist.

    The sheet is read as the ripper wrote it (`cd_toc.read_cue_text`: UTF-8, or
    the Windows code page EAC and CUERipper use) and written back as UTF-8
    without a byte-order mark, as Lempi's own sheets are (`player/src/cue.rs`):
    a code-page sheet reads differently under each Windows locale, UTF-8 the
    same everywhere. Its line endings are kept. Already in that form, it is
    left byte-for-byte as it was, so running this again changes nothing."""
    text = cd_toc.read_cue_text(cue_path)
    out = [_CUE_FILE.sub(lambda m: m.group(1) + ext + m.group(2) + kind, line, count=1)
           for line in text.splitlines(keepends=True)]
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


MB_WS = MB_DISCID_BASE.rsplit("/", 1)[0]


def lookup_barcode(barcode: str) -> tuple[str, list[dict]]:
    """The releases carrying this barcode -- the sheet's `CATALOG` -- each
    fetched with its track list, as `lookup_disc_id` returns them. For a rip
    whose disc positions are unknown: a tracks-mode rip with no log
    `[SPEC-CDI-092]`. A barcode names a product, not a pressing, so it can
    match several editions; the person picks one, as for a Disc ID."""
    q = urllib.parse.urlencode({"query": f"barcode:{barcode}", "fmt": "json", "limit": 8})
    doc = fetch_releases.get(f"{MB_WS}/release/?{q}")
    found = [r.get("id") for r in (doc or {}).get("releases") or [] if r.get("id")]
    releases = []
    for rid in found[:5]:
        time.sleep(fetch_releases.RATE_S)
        full = fetch_releases.get(f"{MB_WS}/release/{rid}?fmt=json&inc=recordings+artist-credits")
        if full:
            releases.append(full)
    return ("barcode" if releases else "none"), releases


def identify_disc(toc: cd_toc.DiscToc, log_toc: cd_toc.DiscToc | None) -> tuple[str, list[dict], str | None]:
    """`(outcome, releases, disc_id)`: the Disc ID from the disc's own
    positions -- the sheet's for an image rip, the log's table of contents
    for a tracks-mode rip, whose sheet has only each file's own times -- then
    the barcode if that finds nothing `[SPEC-CDI-092]`."""
    positions = log_toc if toc.tracks_mode else toc
    disc_id = None
    outcome, releases = "none", []
    if positions is not None and positions.leadout_sector:
        disc_id = cd_toc.musicbrainz_disc_id(positions)
        outcome, releases = lookup_disc_id(positions)
    if outcome == "none" and toc.barcode:
        outcome, releases = lookup_barcode(toc.barcode)
    return outcome, for_this_disc(releases, disc_id, positions or toc), disc_id


def _medium_tracks(medium: dict) -> int:
    return medium.get("track-count") or len(medium.get("tracks") or [])


def disc_medium(release: dict, disc_id: str | None, toc: cd_toc.DiscToc | None,
                track_count: int | None = None) -> dict | None:
    """Which of `release`'s discs this rip is `[SPEC-CDI-099]`. MusicBrainz
    answers a Disc ID with whole releases -- every disc of a set, each listing
    the Disc IDs it is known by -- so the disc is the medium naming this one's
    own id. Without an id to go on (a fuzzy match, a barcode), the medium with
    the disc's track count (`track_count`, else the TOC's), and among several,
    the one whose track lengths fit its own best: *An American Treasure*'s two
    discs have 13 tracks each."""
    media = release.get("media") or []
    if len(media) <= 1:
        return media[0] if media else None
    if disc_id:
        for m in media:
            if any(d.get("id") == disc_id for d in m.get("discs") or []):
                return m
    if toc is None or not toc.tracks:
        return None
    count = track_count or toc.track_count
    same = [m for m in media if _medium_tracks(m) == count] or media
    if len(same) == 1:
        return same[0]
    mine = {t.number: t.end_ms - t.start_ms for t in toc.tracks
            if t.end_ms is not None and t.start_ms is not None}

    def misfit(m):
        theirs = {t.get("position"): t.get("length") for t in m.get("tracks") or []}
        pairs = [(a, theirs[n]) for n, a in mine.items() if theirs.get(n) is not None]
        return sum(abs(a - b) for a, b in pairs) / len(pairs) if pairs else float("inf")
    best = min(same, key=misfit)
    return best if misfit(best) != float("inf") else None


def for_this_disc(releases: list[dict], disc_id: str | None, toc: cd_toc.DiscToc | None) -> list[dict]:
    """Each release cut down to the disc this rip is, its other discs dropped,
    so a track's number means its place on *this* disc `[SPEC-CDI-099]`. Until
    2026-10-14 every lookup took the first disc's track at that number, and
    Woodstock's second disc was catalogued as the first disc's seven songs.
    A release whose disc cannot be told is left out: a wrong answer is worse
    than none, and the review queue takes a track nobody can name. The whole
    set's track count is kept, as `_track_total`, for `record_release`; its
    discs, as `_disc_count` and `_set`, for the names the move gives the files
    and the preview's word on the rest of the set `[SPEC-RIP-108]`, `[SPEC-RIP-110]`."""
    out = []
    for r in releases:
        media = r.get("media") or []
        if len(media) <= 1:
            out.append(r)
            continue
        m = disc_medium(r, disc_id, toc)
        if m is None:
            say(f"  {r.get('title')!r} ({r.get('id')}) has {len(media)} discs and which this is cannot "
                "be told: left out")
            continue
        out.append(dict(r, media=[m], _track_total=sum(_medium_tracks(x) for x in media),
                        _disc_count=len(media), _set=media))
    return out


def _track_at_position(release: dict, position: int, mbid: str | None = None) -> dict | None:
    """The track at `position` -- on a release cut to one disc
    (`for_this_disc`), that disc's. With `mbid`, only the track at that place
    holding that recording, on whichever disc of a whole release it is: how a
    backfill, which has the recording and not the disc, finds it."""
    for medium in release.get("media") or []:
        for t in medium.get("tracks") or []:
            if t.get("position") == position or t.get("number") == str(position):
                if mbid is None or (t.get("recording") or {}).get("id") == mbid:
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


def record_release(conn, release: dict, tracks, files=()) -> int:
    """The disc in hand is its release `[SPEC-CDI-098]`: `release` (a
    MusicBrainz release, `media[].tracks[]` with recordings) becomes the chosen
    release of each recording in `tracks` -- `(position, mbid)` -- that it
    carries at that position. That is what names the album in Browse; until
    2026-10-01 a CD add wrote no release at all, so 162 of 169 newly added
    radio passages had no album there, on the desktop and every speaker.

    `chosen` is set for these recordings alone and moved off any other
    release they were on -- unless another disc held here has it already,
    which keeps it; `source` is `cd:import`, which `choose_release.py`
    leaves standing -- knowing which record a rip came from settles it.
    And `files` -- the rip's audio_md5s -- are recorded as taken from it
    `[SPEC-SC-125]`, so each is shown under this disc whatever its
    recordings' chosen releases are. Returns how many recordings were linked."""
    rid = release.get("id")
    if not rid:
        return 0
    conn.execute("CREATE TABLE IF NOT EXISTS releases (mbid TEXT PRIMARY KEY, title TEXT NOT NULL, "
                 "release_date TEXT, source TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS release_recordings (release_mbid TEXT NOT NULL, "
                 "mbid TEXT NOT NULL, position INTEGER, source TEXT NOT NULL, "
                 "PRIMARY KEY (release_mbid, mbid)) WITHOUT ROWID")
    for ddl in ("ALTER TABLE release_recordings ADD COLUMN chosen INTEGER DEFAULT 0",
                "ALTER TABLE release_recordings ADD COLUMN track_length_ms INTEGER",
                "ALTER TABLE release_recordings ADD COLUMN disc INTEGER",
                "ALTER TABLE releases ADD COLUMN artist_credit TEXT"):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass    # there already
    have = {r[1] for r in conn.execute("PRAGMA table_info(releases)")}
    group = release.get("release-group") or {}
    row = {"mbid": rid, "title": release.get("title") or "?", "release_date": release.get("date"),
           "source": "musicbrainz", "release_group": group.get("id"), "status": release.get("status"),
           "primary_type": group.get("primary-type"),
           "secondary_types": ",".join(group.get("secondary-types") or []) or None,
           "country": release.get("country"),
           "track_count": release.get("_track_total")
           or sum(_medium_tracks(m) for m in release.get("media") or []) or None,
           "artist_credit": release_credit(release) or None}
    cols = [c for c in row if c in have]
    # A release already known keeps its row: fetch_releases.py's is the fuller.
    conn.execute(f"INSERT OR IGNORE INTO releases ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [row[c] for c in cols])
    # ... but takes the disc's credit if it has none: what Browse shows the
    # album under `[REQ-VIS-183]`, which a release fetch did not ask for.
    if "artist_credit" in have and row["artist_credit"]:
        conn.execute("UPDATE releases SET artist_credit = ?1 WHERE mbid = ?2 AND artist_credit IS NULL",
                     (row["artist_credit"], rid))
    linked = 0
    for position, mbid in tracks:
        t = _track_at_position(release, position, mbid)
        if t is None:
            continue    # not this release's recording at this place: not linked
        disc = next((m.get("position") for m in release.get("media") or [] if t in (m.get("tracks") or [])), None)
        # A recording on two discs held here -- a hit on its album and on a
        # compilation; thirteen such on 2026-10-01 -- keeps the first disc's
        # choice. Each taking it from the last made the choice depend on
        # order, and a backfill that never settled.
        other_disc = conn.execute(
            "SELECT 1 FROM release_recordings WHERE mbid = ?1 AND release_mbid <> ?2 "
            "AND chosen = 1 AND source = 'cd:import'", (mbid, rid)).fetchone() is not None
        if not other_disc:
            conn.execute("UPDATE release_recordings SET chosen = 0 WHERE mbid = ?1", (mbid,))
        conn.execute(
            "INSERT INTO release_recordings (release_mbid, mbid, position, source, track_length_ms, chosen, disc) "
            "VALUES (?1, ?2, ?3, 'cd:import', ?4, ?5, ?6) "
            # A row already here -- one a release fetch wrote, without its
            # place -- takes the place from the disc in hand: until
            # 2026-10-14 it kept none, and Browse could not order the track.
            "ON CONFLICT(release_mbid, mbid) DO UPDATE SET chosen = excluded.chosen, source = 'cd:import', "
            "position = COALESCE(excluded.position, release_recordings.position), "
            "disc = COALESCE(excluded.disc, release_recordings.disc), "
            "track_length_ms = COALESCE(excluded.track_length_ms, release_recordings.track_length_ms)",
            (rid, mbid, t.get("position") or position, t.get("length"), 0 if other_disc else 1, disc))
        linked += 1
    if files:
        conn.execute("CREATE TABLE IF NOT EXISTS file_releases (audio_md5 TEXT PRIMARY KEY, "
                     "release_mbid TEXT NOT NULL, source TEXT NOT NULL, decided_at TEXT)")
        now = _now()
        for md5 in files:
            conn.execute("INSERT INTO file_releases (audio_md5, release_mbid, source, decided_at) "
                         "VALUES (?1, ?2, 'cd:import', ?3) ON CONFLICT(audio_md5) DO UPDATE SET "
                         "release_mbid = excluded.release_mbid, source = excluded.source, "
                         "decided_at = excluded.decided_at", (md5, rid, now))
    return linked


def fetch_disc_cover(conn, release_id: str | None) -> dict | None:
    """The disc's own cover, front and back, from the Cover Art Archive
    `[SPEC-COV-060]`, once its release is recorded -- only the sides it lacks.
    Asked for 2026-10-02: that week's CDs had no front at all, and the archive
    had every one. A failure is said and does not fail the add; the cover
    fetcher asks again later."""
    if not release_id:
        return None
    try:
        fetch_cover_art.prepare(conn)
        todo = fetch_cover_art.wanted(conn, 0, [release_id])
        if not todo:
            return {"asked": 0, "fronts": 0, "backs": 0, "unavailable": 0}
        say("asking the Cover Art Archive for the cover ...")
        n = fetch_cover_art.fetch_for(conn, todo, say=lambda s: None)
        say("  " + ("the archive did not answer; tools/fetch_cover_art.py asks again" if n["unavailable"] else
                    f"front {'found' if n['fronts'] else 'not there'}, back {'found' if n['backs'] else 'not there'}"))
        return n
    except Exception as e:  # a cover is not worth failing an add over
        say(f"  the cover was not fetched ({e}); tools/fetch_cover_art.py can, later")
        return None


def _artists_for(release: dict, position: int) -> list[tuple[str, str, str | None]]:
    """`(artist_mbid, name, sort_name)` for `recording_artists`, from the same
    embedded `artist-credit` `_candidate_for` reads for its own display
    string -- kept separate because `Suggestion`'s own shape has no room
    for a real per-artist mbid, only the joined name a review card shows.
    The sort name rides in the same answer, and is what Browse sorts by
    `[REQ-VIS-182]`; until 2026-10-01 it was dropped here."""
    t = _track_at_position(release, position)
    if t is None:
        return []
    rec = t.get("recording") or {}
    return [(a["artist"]["id"], a["artist"].get("name") or "?", a["artist"].get("sort-name"))
            for a in rec.get("artist-credit") or []
            if isinstance(a.get("artist"), dict) and a["artist"].get("id")]


# ------------------------------------------------- occasions, for the whole disc
#
# A Christmas collection, or a children's album, is that from its first track
# to its last; marking its recordings one by one in the preference panel is
# the chore this saves `[SPEC-CDI-096]`. The disc's title suggests it; the
# person decides, on the import page, before anything is added. Written as the
# inherited MuLibPlay tagging is -- both classes of the pair, in `flavor` -- so
# the panel shows it as inherited, a person's own value still overrides it,
# and Reset returns to it `[SPEC-PREF-085]`. Unticked writes nothing: no
# opinion, never "not Christmas".
OCCASIONS = {
    "christmas": ("user.christmas", "christmasy", "not_christmasy"),
    "childrens": ("user.childrens", "for_children", "not_for_children"),
}
# Conservative on purpose: a box the guess left unticked is one click, and a
# children's guess on "Baby One More Time" would be a wrong mark to undo.
_OCCASION_GUESS = {
    "christmas": re.compile(r"christmas|\bx-?mas\b|\bno[eë]l\b|navidad|weihnacht", re.I),
    "childrens": re.compile(r"\bchildren|\bkids?\b|\bkidz\b|nursery|lullab|sing-?a-?long|toddlers?\b", re.I),
}
OCCASION_SOURCE = "cd:import"


def guess_occasions(*titles: str | None) -> list[str]:
    """Which occasions the disc's titles suggest, for the import page to tick
    in advance: `["christmas"]` for "Now That's What I Call Christmas! 3"."""
    text = " ".join(t for t in titles if t)
    return [o for o, pat in _OCCASION_GUESS.items() if pat.search(text)]


def mark_occasions(conn, mbids, occasions, now: str | None = None) -> dict:
    """Every recording in `mbids` marked fully of each occasion -- the class
    1.0, its opposite 0.0 -- unless already so. `{occasion: recordings}`."""
    out = {}
    for occ in occasions:
        if occ not in OCCASIONS:
            raise ValueError(f"no occasion {occ!r}")
        ch, yes, no = OCCASIONS[occ]
        n = 0
        for mbid in dict.fromkeys(m for m in mbids if m):
            for cls, value in ((yes, 1.0), (no, 0.0)):
                conn.execute(
                    "INSERT INTO flavor (subject_kind,subject_id,characteristic,class,value,source,accuracy) "
                    "VALUES ('recording',?1,?2,?3,?4,?5,NULL) "
                    "ON CONFLICT(subject_kind,subject_id,characteristic,class) DO UPDATE SET "
                    "value=excluded.value, source=excluded.source WHERE flavor.value != excluded.value",
                    (mbid, ch, cls, value, OCCASION_SOURCE))
            n += 1
        out[occ] = n
    return out


class AlreadyHeld(RuntimeError):
    """The whole edition is already in the library `[SPEC-CDI-047]`."""


def held_of_edition(conn, release: dict, positions) -> tuple[int, int] | None:
    """`(held, total)`: how many of the edition's recordings at `positions`
    -- the tracks this rip would add -- the library already plays. `None`
    when a position has no recording to compare."""
    mbids = []
    for n in positions:
        rec = ((_track_at_position(release, n) or {}).get("recording") or {}).get("id")
        if not rec:
            return None
        mbids.append(rec)
    mbids = list(dict.fromkeys(mbids))
    if not mbids:
        return None
    q = f"SELECT COUNT(DISTINCT mbid) FROM passage_recordings WHERE mbid IN ({','.join('?' * len(mbids))})"
    return conn.execute(q, mbids).fetchone()[0], len(mbids)


def earlier_rip(conn, release: dict | None, disc_id: str | None = None,
                barcode: str | None = None) -> dict | None:
    """An earlier rip of this disc already in the library `[SPEC-AGN-015]`, or
    `None`: the files taken from this edition (`file_releases`), or added
    under this Disc ID or barcode, that hold this disc's own recordings -- so
    a set's other disc, the same release, is not it. `release` is cut to this
    disc (`for_this_disc`). A rip added with no log has no Disc ID: *Highway
    61*'s first rip is found by its edition, and under any other of the
    sixteen MusicBrainz offers for the disc, by the sheet's barcode.

    `{"folder", "files", "tracks", "held", "missing", "damaged", "since",
    "release", "said"}`: the folder holding most of it, the disc's track count
    and which of its tracks the earlier rip holds, never ripped, and ripped
    damaged, by track number; `since`, the day it was added; `release`, the
    edition it was added as; `said`, all that in words."""
    media = (release or {}).get("media") or []
    pos_of = {t.get("position"): (t.get("recording") or {}).get("id")
              for t in (media[0].get("tracks") or [] if len(media) == 1 else [])}
    md5s = set()
    try:
        if release and release.get("id"):
            md5s |= {r[0] for r in conn.execute(
                "SELECT audio_md5 FROM file_releases WHERE release_mbid = ?1", (release["id"],))}
        for key, value in (("disc_id", disc_id), ("barcode", barcode)):
            if value:
                md5s |= {r[0] for r in conn.execute(
                    "SELECT audio_md5 FROM ingest_decisions WHERE stage = 'rip' "
                    f"AND json_extract(detail, '$.{key}') = ?1", (value,))}
    except sqlite3.OperationalError:
        return None     # no file_releases yet: no disc has been added
    if not md5s:
        return None
    q = ",".join("?" * len(md5s))
    files = conn.execute(f"SELECT file_id, path, audio_md5 FROM files WHERE audio_md5 IN ({q})",
                         sorted(md5s)).fetchall()
    mbids_of = {}
    for fid, mbid in conn.execute(
            "SELECT p.file_id, pr.mbid FROM passages p JOIN passage_recordings pr USING (passage_id) "
            f"WHERE p.file_id IN ({','.join('?' * len(files))})", [f[0] for f in files]) if files else []:
        mbids_of.setdefault(fid, set()).add(mbid)
    ours = set(pos_of.values()) - {None}
    files = [f for f in files if not ours or mbids_of.get(f[0], set()) & ours]
    if not files:
        return None
    held_mbids = set().union(*(mbids_of.get(f[0], set()) for f in files))
    mine = [f[2] for f in files]
    q = ",".join("?" * len(mine))
    damaged, since = set(), None
    for outcome, detail, at in conn.execute(
            f"SELECT outcome, detail, decided_at FROM ingest_decisions WHERE stage = 'rip' AND audio_md5 IN ({q})",
            mine):
        since = min(since or at, at) if at else since
        if outcome == "verification_failed":
            n = (json.loads(detail or "{}") or {}).get("track")
            if n is not None:
                damaged.add(n)
    folders = [os.path.dirname(f[1]) for f in files if f[1]]
    held = sorted(n for n, m in pos_of.items() if m in held_mbids)
    e = {"folder": max(set(folders), key=folders.count) if folders else None, "files": len(files),
         "tracks": len(pos_of) or None, "held": held,
         "missing": sorted(n for n in pos_of if n not in held) if pos_of else [],
         "damaged": sorted(damaged), "since": since[:10] if since else None, "release": None}
    linked = [r[0] for r in conn.execute(
        f"SELECT release_mbid FROM file_releases WHERE audio_md5 IN ({q})", mine)]
    if linked:
        e["release"] = max(set(linked), key=linked.count)
    e["said"] = said_earlier(e)     # the import page shows it as written
    return e


def said_earlier(e: dict) -> str:
    """`earlier_rip`'s answer in words: *6 of 9 tracks; 7, 8 and 9 never
    ripped; added 2026-10-24*."""
    def listed(ns):
        ns = [str(n) for n in ns]
        return ns[0] if len(ns) == 1 else f"{', '.join(ns[:-1])} and {ns[-1]}"
    parts = [f"{len(e['held'])} of {e['tracks']} tracks" if e.get("tracks") else
             f"{e['files']} file{'s' if e['files'] != 1 else ''}"]
    if e.get("missing"):
        parts.append(f"{listed(e['missing'])} never ripped")
    if e.get("damaged"):
        parts.append(f"{listed(e['damaged'])} ripped damaged")
    if e.get("since"):
        parts.append(f"added {e['since']}")
    return "; ".join(parts)


def refuse_clash(conn, err: FileExistsError, releases: list[dict], disc_id: str | None,
                 barcode: str | None, into: str) -> FileExistsError:
    """The move's clash `[SPEC-AGN-015]`, found before anything is encoded,
    and said as what it is when the folder holds an earlier rip of this disc:
    re-ripping a disc into its own album is SPEC069's merge, not built yet.
    Any other clash is returned as it was."""
    e = earlier_rip(conn, releases[0] if len(releases) == 1 else None, disc_id, barcode)
    same = e and e["folder"] and os.path.normcase(os.path.abspath(e["folder"])) == \
        os.path.normcase(os.path.abspath(into))
    if not same:
        return err
    return FileExistsError(
        f"{into!r} holds an earlier rip of this disc ({said_earlier(e)}): adding a disc again to its own "
        "album folder is not built yet -- nothing was encoded or moved")


def refuse_duplicate(conn, releases: list[dict], positions, allow: bool) -> None:
    """A second copy of an album is added only when a person asked for one
    `[SPEC-CDI-047]`: the disc resolved to one edition, and every recording
    this rip would add is already in the library. Checked before anything is
    encoded or moved, so a refusal leaves the rip as it was."""
    if allow or len(releases) != 1:
        return
    got = held_of_edition(conn, releases[0], positions)
    if got and got[0] == got[1]:
        raise AlreadyHeld(
            f"every track of this edition is already in the library ({got[0]} of {got[1]}): a second copy "
            "is added only when asked -- tick \"Add it again\" on the import page, or pass --allow-duplicate")


def release_credit(release: dict) -> str:
    """An edition's artist credit as MusicBrainz writes it, joins and all."""
    return "".join((a.get("name") or "") + (a.get("joinphrase") or "")
                   for a in release.get("artist-credit") or []).strip()


def release_summary(release: dict, track_count: int, conn=None, positions=None,
                    limit: int = ALBUM_NAME_MAX, disc_id: str | None = None,
                    barcode: str | None = None) -> dict:
    """One candidate edition as the preview shows it `[SPEC-CDI-040]`: who,
    what, when, its track list for this disc, the folder it would be filed
    under -- its album name held to `limit` `[SPEC-NAM-020]` -- and how many of
    its recordings the library already plays -- the "already in the library"
    signal `[SPEC-CDI-045]`."""
    credit = release_credit(release)
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
    # `duplicate`: every recording this rip would add -- its tracks at
    # `positions`, all of them for an image -- already in the library. The page
    # then asks before adding a second copy, and the add refuses without it
    # `[SPEC-CDI-047]`.
    dup = held_of_edition(conn, release, positions if positions is not None
                          else [t["position"] for t in tracks]) if conn is not None else None
    return {"id": release.get("id"), "title": release.get("title"), "artist": credit or None,
            "year": year, "country": release.get("country"), "tracks": tracks,
            "in_library": held, "duplicate": bool(dup and dup[0] == dup[1]),
            "folder": proposed_folder(credit, release.get("title"), year, limit),
            # [SPEC-AGN-015]: an earlier rip of this disc, said before Add.
            "earlier": earlier_rip(conn, release, disc_id, barcode) if conn is not None else None,
            **set_summary(release, conn)}


def set_summary(release: dict, conn=None) -> dict:
    """For one disc of a set, which disc of how many, and each of the set's
    discs with how much of it the library already plays `[SPEC-RIP-110]` -- so
    the page says *disc 2 of 2*, and that disc 1 is not here yet, before
    anything is added. Empty for a single disc."""
    whole = release.get("_set") or []
    if len(whole) < 2 or len(release.get("media") or []) != 1:
        return {}
    this = release["media"][0].get("position")
    discs = []
    for i, m in enumerate(whole, 1):
        mbids = list(dict.fromkeys(((t.get("recording") or {}).get("id")) for t in m.get("tracks") or []))
        mbids = [x for x in mbids if x]
        held = 0
        if conn is not None and mbids:
            held = conn.execute("SELECT COUNT(DISTINCT mbid) FROM passage_recordings WHERE mbid IN "
                                f"({','.join('?' * len(mbids))})", mbids).fetchone()[0]
        discs.append({"number": m.get("position") or i, "format": m.get("format"), "title": m.get("title") or None,
                      "tracks": _medium_tracks(m), "recordings": len(mbids), "held": held})
    return {"disc": this, "discs": len(whole), "set": discs}


# -------------------------------------------------------------- hidden audio

# What counts as hidden audio rather than the silence a gap ordinarily is
# `[SPEC-RIP-090]`: at least this long, and this loud on average. A pressed
# gap is digital silence (about -91 dB) or a tape's hiss (about -60 dB); a
# hidden track is music, -30 dB and up. A live album's applause carried into
# a two-second gap stays shorter than the floor, and with its track.
HIDDEN_MIN_MS = 4000
HIDDEN_MEAN_DB = -45.0
# A silence inside the last track this long, music after it, is a hidden
# track after the album -- the common case, minutes of nothing and then a
# song. Not looked for within the track's first half minute.
POSTGAP_SILENCE_S = 8
POSTGAP_SILENCE_DB = -50
POSTGAP_AFTER_MS = 30000

_MEAN = re.compile(r"mean_volume:\s*(-?[\d.]+|-inf) dB")
_SILENCE = re.compile(r"silence_(start|end):\s*(-?[\d.]+)")


def _mean_db(wav_path: str, start_ms: int, end_ms: int) -> float | None:
    """The span's mean volume, by ffmpeg's `volumedetect`; `None` if it could
    not be measured."""
    r = subprocess.run([FFMPEG, "-v", "info", "-nostats", "-ss", f"{start_ms / 1000:.3f}",
                        "-t", f"{(end_ms - start_ms) / 1000:.3f}", "-i", wav_path,
                        "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True, timeout=600)
    m = _MEAN.search(r.stderr or "")
    if r.returncode != 0 or not m:
        return None
    return float("-inf") if m.group(1) == "-inf" else float(m.group(1))


def _silences(wav_path: str, start_ms: int, end_ms: int) -> list[tuple[int, int]]:
    """The long silences in a span, as `(start_ms, end_ms)` on the disc."""
    r = subprocess.run([FFMPEG, "-v", "info", "-nostats", "-ss", f"{start_ms / 1000:.3f}",
                        "-t", f"{(end_ms - start_ms) / 1000:.3f}", "-i", wav_path, "-af",
                        f"silencedetect=noise={POSTGAP_SILENCE_DB}dB:d={POSTGAP_SILENCE_S}", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=600)
    out, begun = [], None
    for kind, at in _SILENCE.findall(r.stderr or ""):
        ms = start_ms + int(float(at) * 1000)
        if kind == "start":
            begun = ms
        elif begun is not None:
            out.append((begun, ms))
            begun = None
    return out


def _pregap_track(release: dict | None) -> dict | None:
    """MusicBrainz's hidden track before track 1 -- a medium's `pregap`,
    position 0 -- when the edition is settled and has one."""
    media = (release or {}).get("media") or []
    return (media[0].get("pregap") or None) if len(media) == 1 else None


def find_hidden(wav_path: str, toc: cd_toc.DiscToc, release: dict | None = None) -> list[dict]:
    """Audio on an image rip that no track holds `[SPEC-RIP-090]`, each
    `{"where", "start_ms", "end_ms", "title", "mbid"?, "trims"?, "leads_into"?}`:
    before track 1 (a pregap, where MusicBrainz may name it), in a gap between
    two tracks, or after a long silence at the end of the last track (`trims`:
    that track's end moves to where the silence begins). Measured, never
    guessed: a span is hidden audio only if it is long enough and loud enough.
    Until 2026-10-14 each of these was in the file and in no passage at all.

    `leads_into`: the number of the track the span runs straight into -- a
    pregap's track 1, a gap's next track -- which the import page offers to
    fold it into `[SPEC-RIP-092]`. A hidden track after the last one's silence
    leads into nothing, and has none.

    `release` is the settled edition, cut to this disc, for a pregap's name."""
    if not FFMPEG or not toc.tracks:
        return []
    out = []
    first, last = toc.tracks[0], toc.tracks[-1]
    title = lambda t: f" ('{t.title}')" if t.title else ""  # noqa: E731

    def loud(a, b):
        db = _mean_db(wav_path, a, b) if b - a >= HIDDEN_MIN_MS else None
        return db is not None and db > HIDDEN_MEAN_DB

    if first.start_ms and loud(0, first.start_ms):
        mb = _pregap_track(release)
        rec = (mb or {}).get("recording") or {}
        out.append({"where": "pregap", "start_ms": 0, "end_ms": first.start_ms,
                    "title": (mb or {}).get("title") or rec.get("title")
                    or f"Hidden audio, before track 1{title(first)}", "leads_into": first.number,
                    **({"mbid": rec["id"]} if rec.get("id") else {})})
    for prev, t in zip(toc.tracks, toc.tracks[1:]):
        if prev.end_ms is not None and t.start_ms - prev.end_ms >= HIDDEN_MIN_MS and loud(prev.end_ms, t.start_ms):
            out.append({"where": "gap", "start_ms": prev.end_ms, "end_ms": t.start_ms, "leads_into": t.number,
                        "title": f"Hidden audio, between tracks {prev.number} and {t.number}{title(t)}"})
    if last.end_ms and last.end_ms - last.start_ms > POSTGAP_AFTER_MS + HIDDEN_MIN_MS:
        # Each stretch of music after the first long silence is its own piece:
        # a disc may hide two, a silence apart. The last track then ends where
        # that first silence begins.
        quiet = _silences(wav_path, last.start_ms + POSTGAP_AFTER_MS, last.end_ms)
        pieces = [(a[1], b[0]) for a, b in zip(quiet, quiet[1:])] + ([(quiet[-1][1], last.end_ms)] if quiet else [])
        pieces = [p for p in pieces if loud(*p)]
        for n, (a, b) in enumerate(pieces, 1):
            out.append({"where": "postgap", "start_ms": a, "end_ms": b, "trims": quiet[0][0],
                        "title": f"Hidden track{f' {n}' if len(pieces) > 1 else ''}, "
                                 f"after track {last.number}{title(last)}"})
    return out


def check_fold(hidden: list | tuple, fold: list | tuple, whole_disc: bool = False) -> None:
    """Refuse a join `[SPEC-RIP-092]` that names no hidden span leading into a
    track -- a preview measured on another WAV, a page left open, a hand-typed
    `--fold` -- rather than add the disc without it and say nothing."""
    if not fold:
        return
    if whole_disc:
        raise ValueError("with One piece nothing is hidden, so there is nothing to join to a track")
    stray = sorted(set(fold) - {h["start_ms"] for h in hidden if h.get("leads_into") is not None})
    if stray:
        raise ValueError(f"no hidden audio that leads into a track starts at {', '.join(map(str, stray))} ms; "
                         "nothing to join")


# -------------------------------------------------------------------- commit

def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _resolve_track(conn, number: int, cd_text_title: str | None, radio_pid: int, mp3_path: str,
                   start_ms: int, end_ms: int, audio_md5: str, disc_outcome: str,
                   releases: list[dict], chosen: bool, acoustid_key: str | None,
                   now: str) -> tuple[str, str, str]:
    """One track's identity, by the release's track at `number`, CD-TEXT, or
    AcoustID -- `(outcome, mbid, source)`, `outcome` one of identified,
    ambiguous, unidentified. Shared by an image rip and a tracks-mode rip
    `[SPEC-CDI-090]`: the same rules whichever way the disc was ripped."""
    # -------------------------------------------------- resolve identity
    # `candidates` and `candidate_releases` stay index-aligned so the
    # single-match branch below can find which release the one
    # surviving candidate actually came from, for its artist credits --
    # not necessarily `releases[0]`, if that release lacks this track.
    candidates: list[dict] = []
    candidate_releases: list[dict] = []
    if disc_outcome != "none":
        for rel in releases[:8]:
            c = _candidate_for(rel, number)
            if c is not None and all(c["mbid"] != seen["mbid"] for seen in candidates):
                candidates.append(c)
                candidate_releases.append(rel)

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
        outcome = 'ambiguous'
    elif len(candidates) == 1:
        c = candidates[0]
        mbid, source = c["mbid"], "musicbrainz"
        conn.execute(
            "INSERT OR IGNORE INTO recordings (mbid,title,length_ms,source) "
            "VALUES (?1,?2,?3,?4)", (mbid, c["title"], end_ms - start_ms, source))
        for artist_mbid, name, sort_name in _artists_for(candidate_releases[0], number):
            conn.execute(
                "INSERT OR IGNORE INTO artists (mbid,name,sort_name,source) VALUES (?1,?2,?3,?4)",
                (artist_mbid, name, sort_name, source))
            # One already here without a sort name gets this one; one with
            # its own keeps it.
            artist_sort.record(conn, artist_mbid, sort_name)
            conn.execute(
                "INSERT OR IGNORE INTO recording_artists (mbid,artist_mbid,weight,source) "
                "VALUES (?1,?2,1.0,?3)", (mbid, artist_mbid, source))
        outcome = 'identified'
    elif len(candidates) > 1:
        mbid, source = f"local:audio:{audio_md5}:{start_ms}", "cd:ambiguous"
        conn.execute(
            "INSERT OR IGNORE INTO recordings (mbid,title,length_ms,source) "
            "VALUES (?1,?2,?3,?4)",
            (mbid, f"unidentified track {number} (disc ambiguous)",
             end_ms - start_ms, source))
        _write_id_check(conn, radio_pid, mbid, candidates, now)
        outcome = 'ambiguous'
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
            # AcoustID names an artist and not its sort name, so it is asked
            # of MusicBrainz -- once per artist, only while it has none.
            try:
                artist_sort.fill(conn, [a for a, _ in rec["artists"]], say=say)
            except Exception as e:  # a sort name is not worth failing an add over
                say(f"  sort name not looked up ({e}); tools/artist_sort.py can, later")
            outcome = 'identified'
        else:
            mbid, source = f"local:audio:{audio_md5}:{start_ms}", "cd:unidentified"
            conn.execute(
                "INSERT OR IGNORE INTO recordings (mbid,title,length_ms,source) "
                "VALUES (?1,?2,?3,?4)",
                (mbid, f"unidentified track {number}", end_ms - start_ms, source))
            _write_id_check(conn, radio_pid, mbid, [], now)
            outcome = 'unidentified'

    return outcome, mbid, source


def commit_rip(conn, folder: str, toc: cd_toc.DiscToc, mp3_path: str,
               audio_md5: str, disc_outcome: str, releases: list[dict],
               rip_report: "cd_toc.RipReport | None", acoustid_key: str | None,
               chosen: bool = False, occasions: tuple = (), whole_disc: bool = False,
               hidden: list | tuple = (), fold: list | tuple = ()) -> dict:
    """Register the file, write TOC-exact passages, resolve each track's
    identity, and record every decision -- the CD-ripping analogue of
    `segment_dao.commit_segments()`, same shape, ground-truth boundaries
    instead of an inferred cascade `[SPEC-RIP-010]`.

    `chosen`: `releases` is the one edition a person picked from the preview
    `[SPEC-CDI-040]`. It is then the answer for every track it names -- CD-TEXT
    no longer sends those to the review queue `[SPEC-RIP-066]`, since a person
    has already looked.

    `whole_disc` `[SPEC-RIP-114]`: one radio passage from track 1's start to
    the end, and one album passage of the whole file, in place of a pair per
    track. Each track is still identified and linked to its release; the
    passages carry every track's recording, weighted by its share of the disc.
    The review queue's check is per passage, so of several unidentified tracks
    it holds the last.

    `hidden` `[SPEC-RIP-090]`: `find_hidden`'s spans, each a radio and an album
    passage of its own; a span after the last track's long silence ends that
    track where the silence begins.

    `fold` `[SPEC-RIP-092]`: the `start_ms` of each hidden span a person
    ticked to be joined to the track it leads into -- one more radio passage,
    from the span's start to that track's end, carrying both recordings by
    their share of it. Radio only: the Director plays radio passages, and is
    what clipped the intro by starting at the track; a whole-album play runs
    album passages one after another, which already plays the intro into its
    track, and a folded album passage beside them would play it twice. A start
    that names no foldable span is refused, never quietly dropped.
    """
    check_fold(hidden, fold, whole_disc)
    st = os.stat(mp3_path)
    total_ms = toc.tracks[-1].end_ms if toc.tracks else 0
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    # `[SPEC-RLK-150]` precondition 3, through the same helper that computed
    # the hash -- one definition of what ffmpeg is here, not a second.
    ingest_folder.ensure_md5_generator_column(conn)
    # The file's byte hash, as folder induction records it [REQ-AND-960]: what
    # a bundle checks its copy against, and how a speaker or phone that already
    # holds the file finds it. Missing from every CD add until 2026-10-01.
    ensure_sha256_column(conn)
    cur = conn.execute(
        "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,"
        "                   first_seen,last_seen,md5_generator,sha256)"
        " VALUES (?1,?2,?3,?4,'mp3',?5,?6,?6,?7,?8)",
        (audio_md5, mp3_path, st.st_size, st.st_mtime, total_ms, now,
         ingest_folder.md5_generator(), sha256_file(mp3_path)))
    file_id = cur.lastrowid

    boundary_src = f"imported:{'eac-cue' if toc.source == 'eac-cue' else 'cdrdao-toc'}"
    identified = ambiguous = unidentified = failed = 0
    mbids: list[str] = []
    placed: list[tuple[int, str]] = []

    def pair(start_ms, end_ms, album_start_ms=None):
        # Both kinds, one identification `[SPEC-SA-110]`, `[GDE-BMK-030]`.
        radio = conn.execute(
            "INSERT INTO passages (file_id,kind,start_ms,end_ms,boundary_src) "
            "VALUES (?1,'radio',?2,?3,?4)",
            (file_id, start_ms, end_ms, boundary_src)).lastrowid
        album = conn.execute(
            "INSERT INTO passages (file_id,kind,start_ms,end_ms,"
            "lead_in_ms,lead_out_ms,gain_db,boundary_src) "
            "VALUES (?1,'album',?2,?3,0,0,0.0,?4)",
            (file_id, start_ms if album_start_ms is None else album_start_ms, end_ms, boundary_src)).lastrowid
        return radio, album

    hidden = [] if whole_disc else list(hidden)
    # A hidden track after the last one's silence ends that track there.
    trim = min((h["trims"] for h in hidden if h.get("trims") is not None), default=None)
    # One pair for the whole disc: radio from track 1's own start, album from
    # the file's, so a whole-disc play keeps whatever came before it.
    whole = pair(toc.tracks[0].start_ms, total_ms, 0) if whole_disc and toc.tracks else None
    shares: dict[str, list] = {}
    # Each track's span, recording and hold, for a hidden span folded into it.
    landed: dict[int, dict] = {}

    for track in toc.tracks:
        start_ms, end_ms = track.start_ms, track.end_ms
        if trim is not None and track is toc.tracks[-1]:
            end_ms = trim
        radio_pid, album_pid = whole or pair(start_ms, end_ms)

        outcome, mbid, source = _resolve_track(
            conn, track.number, track.title or toc.title, radio_pid, mp3_path, start_ms, end_ms,
            audio_md5, disc_outcome, releases, chosen, acoustid_key, now)
        mbids.append(mbid)
        placed.append((track.number, mbid))
        identified += outcome == "identified"
        ambiguous += outcome == "ambiguous"
        unidentified += outcome == "unidentified"
        landed[track.number] = {"start_ms": start_ms, "end_ms": end_ms, "mbid": mbid, "source": source,
                                "hold": None}

        if whole:
            share = shares.setdefault(mbid, [0, source])
            share[0] += max(end_ms - start_ms, 1)
        else:
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
                # And held at once, both its passages `[SPEC-HOLD-030]`: the
                # radio one, so the Director never chooses it, and the album
                # one, so a whole-album play skips or replaces it
                # `[SPEC-HOLD-070]`. The album's file is untouched, and either
                # passage still plays when a person picks it.
                passage_hold.ensure_column(conn)
                landed[track.number]["hold"] = passage_hold.damage_reason(tr.detail)
                conn.execute(f"UPDATE passages SET {passage_hold.COLUMN} = ?1 WHERE passage_id IN (?2, ?3)",
                             (landed[track.number]["hold"], radio_pid, album_pid))
                failed += 1

    if whole:
        span = sum(n for n, _ in shares.values()) or 1
        for pid in whole:
            for mbid, (n, source) in shares.items():
                conn.execute("INSERT INTO passage_recordings (passage_id,mbid,weight,source) "
                             "VALUES (?1,?2,?3,?4)", (pid, mbid, round(n / span, 4), source))

    found = [_commit_hidden(conn, h, pair, mp3_path, audio_md5, acoustid_key, now) for h in hidden]
    mbids += [m for m, _ in found if not m.startswith("local:")]
    folded = [_commit_folded(conn, h, m, landed[h["leads_into"]], file_id, boundary_src, audio_md5, now)
              for h, m in zip(hidden, found) if h["start_ms"] in set(fold)]

    marked = mark_occasions(conn, mbids, occasions, now) if occasions else {}

    # ------------------------------------------------------------- disc decision
    certain = chosen or (disc_outcome == "exact" and len(releases) == 1)
    albums = record_release(conn, releases[0], placed, [audio_md5]) if certain and releases else 0
    detail = {
        "track_count": toc.track_count, "format": toc.source,
        "candidates": len(releases),
        "chosen": releases[0].get("id") if certain and releases else None,
        "chosen_by": "person" if chosen else None,
        "disc_id": cd_toc.musicbrainz_disc_id(toc),
        # By which a later rip of the disc finds this one [SPEC-AGN-015].
        "barcode": toc.barcode,
        "titles": [r.get("title") for r in releases[:5]],
    }
    conn.execute(
        "INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
        "VALUES (?1,'rip',?2,?3,?4,?5)",
        (audio_md5, "chosen" if chosen else disc_outcome, 1.0 if certain else None,
         json.dumps(detail), now))

    return {"tracks": toc.track_count, "identified": identified,
            "ambiguous": ambiguous, "unidentified": unidentified,
            "verification_failed": failed, "file_id": file_id, "album_linked": albums,
            "disc_outcome": disc_outcome, "candidates": len(releases), "occasions": marked,
            "whole_disc": bool(whole), "hidden": [dict(h, mbid=m) for h, (m, _) in zip(hidden, found)],
            "folded": folded}


def _commit_hidden(conn, h: dict, pair, mp3_path: str, audio_md5: str, acoustid_key: str | None,
                   now: str) -> tuple[str, str]:
    """One hidden span's passages and identity `[SPEC-RIP-090]`; returns its
    recording and its source. MusicBrainz's pregap track where it names one,
    else AcoustID's answer for the span, else a recording of its own titled by where it lies
    -- *Hidden track, after track 12 ('...')* -- and said, never dropped."""
    radio, album = pair(h["start_ms"], h["end_ms"])
    length = h["end_ms"] - h["start_ms"]
    mbid, source = h.get("mbid"), "musicbrainz"
    if mbid:
        conn.execute("INSERT OR IGNORE INTO recordings (mbid,title,length_ms,source) VALUES (?1,?2,?3,?4)",
                     (mbid, h["title"], length, source))
    else:
        rec = (segment_dao.identify_recording(mp3_path, h["start_ms"] / 1000.0, h["end_ms"] / 1000.0, acoustid_key)
               if acoustid_key else None)
        if rec is not None:
            mbid, source = rec["mbid"], "cd:acoustid"
            conn.execute("INSERT OR IGNORE INTO recordings (mbid,title,length_ms,source) VALUES (?1,?2,?3,?4)",
                         (mbid, rec["title"], length, source))
            for artist_mbid, name in rec["artists"]:
                conn.execute("INSERT OR IGNORE INTO artists (mbid,name,source) VALUES (?1,?2,?3)",
                             (artist_mbid, name, source))
                conn.execute("INSERT OR IGNORE INTO recording_artists (mbid,artist_mbid,weight,source) "
                             "VALUES (?1,?2,1.0,?3)", (mbid, artist_mbid, source))
        else:
            mbid, source = f"local:audio:{audio_md5}:{h['start_ms']}", "cd:hidden"
            conn.execute("INSERT OR IGNORE INTO recordings (mbid,title,length_ms,source) VALUES (?1,?2,?3,?4)",
                         (mbid, h["title"], length, source))
    for pid in (radio, album):
        conn.execute("INSERT INTO passage_recordings (passage_id,mbid,weight,source) VALUES (?1,?2,1.0,?3)",
                     (pid, mbid, source))
    conn.execute("INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
                 "VALUES (?1,'rip','hidden_audio',NULL,?2,?3)",
                 (audio_md5, json.dumps({k: h[k] for k in ("where", "start_ms", "end_ms", "title")}), now))
    return mbid, source


def _commit_folded(conn, h: dict, hidden_mbid: tuple[str, str], track: dict, file_id: int,
                   boundary_src: str, audio_md5: str, now: str) -> dict:
    """The hidden span and the track it leads into as one radio passage
    `[SPEC-RIP-092]`, beside the two it spans. Both recordings, each by its
    share of the passage, so it is named by the longer and rotates with it; a
    track held as damaged holds this passage too, since it plays that audio."""
    start, end = h["start_ms"], track["end_ms"]
    pid = conn.execute("INSERT INTO passages (file_id,kind,start_ms,end_ms,boundary_src) "
                       "VALUES (?1,'radio',?2,?3,?4)", (file_id, start, end, boundary_src)).lastrowid
    span = max(end - start, 1)
    shares: dict[str, list] = {}
    for mbid, source, ms in ((*hidden_mbid, h["end_ms"] - start), (track["mbid"], track["source"],
                                                                   end - track["start_ms"])):
        shares.setdefault(mbid, [0, source])[0] += max(ms, 0)
    for mbid, (ms, source) in shares.items():
        conn.execute("INSERT INTO passage_recordings (passage_id,mbid,weight,source) VALUES (?1,?2,?3,?4)",
                     (pid, mbid, round(ms / span, 4), source))
    if track["hold"]:
        conn.execute(f"UPDATE passages SET {passage_hold.COLUMN} = ?1 WHERE passage_id = ?2", (track["hold"], pid))
    said = {"start_ms": start, "end_ms": end, "title": h["title"], "track": h["leads_into"]}
    conn.execute("INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
                 "VALUES (?1,'rip','hidden_folded',NULL,?2,?3)", (audio_md5, json.dumps(said), now))
    return said


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
    if toc.tracks_mode:
        # A sheet naming a file per track is open_tracks' `[SPEC-CDI-090]`;
        # read here, it would be track 1's file holding every track.
        raise ValueError(f"{toc_path!r} is a tracks-mode rip, one file per track")
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


# ------------------------------------------------------------- tracks mode

def is_tracks_rip(folder: str) -> bool:
    """A rip with a file per track `[SPEC-CDI-090]`: its CUE names more than one."""
    fmt, path = find_rip(folder)
    return fmt == "eac" and cd_toc.parse_eac_cue(path).tracks_mode


def open_tracks(folder: str) -> dict:
    """A tracks-mode rip, parsed and checked: each track with its file, or
    named as missing. A track the ripper never wrote -- a damaged one it
    stopped on -- is missing, said, and never the reason the rest cannot be
    added `[SPEC-CDI-094]`. Each present track's span is its whole file, its
    end where the audio ends; nothing is encoded or written."""
    _fmt, cue = find_rip(folder)
    toc = cd_toc.parse_eac_cue(cue)
    log = find_log(folder)
    present, missing = [], []
    for t in toc.tracks:
        path = os.path.join(folder, t.file or "")
        if not t.file or not os.path.isfile(path):
            missing.append(t.number)
            continue
        ms = audio_duration.probe_duration_ms(path)
        if not ms:
            raise ValueError(f"could not decode {path!r}")
        t.end_ms = int(round(ms))
        present.append((t, path))
    if not present:
        raise FileNotFoundError(f"{cue!r} names {len(toc.tracks)} track file(s), and none is there")
    return {"cue": cue, "toc": toc, "present": present, "missing": missing,
            "rip_report": cd_toc.parse_eac_log(log) if log else None,
            "log_toc": cd_toc.toc_from_log(log) if log else None}


def _missing_titles(toc: cd_toc.DiscToc, missing: list[int]) -> list[dict]:
    return [{"number": t.number, "title": t.title} for t in toc.tracks if t.number in missing]


def preview_tracks(db_path: str, folder: str) -> dict:
    """`preview` for a tracks-mode rip `[SPEC-CDI-090]`."""
    rip = open_tracks(folder)
    toc = rip["toc"]
    disc_outcome, releases, disc_id = identify_disc(toc, rip["log_toc"])
    limit = album_name_max_setting(db_path)
    conn = lempi_db.connect(db_path, lempi_db.ROLE_LIBRARY)
    try:
        rels = [release_summary(r, toc.track_count, conn, [t.number for t, _p in rip["present"]], limit,
                                disc_id, toc.barcode) for r in releases[:8]]
        # [SPEC-AGN-015]: a disc MusicBrainz does not know is found by its Disc ID or barcode alone.
        earlier = None if rels else earlier_rip(conn, None, disc_id, toc.barcode)
    finally:
        conn.close()
    cd_folder = proposed_folder(toc.performer, toc.title, None, limit) if toc.title else None
    return {"dry_run": True, "format": "eac", "mode": "tracks", "album_name_max": limit,
            "audio": f"{len(rip['present'])} of {toc.track_count} track files",
            "audio_bytes": sum(os.path.getsize(p) for _t, p in rip["present"]),
            "tracks": toc.track_count, "missing": _missing_titles(toc, rip["missing"]),
            "track_list": [{"number": t.number, "start_ms": 0, "end_ms": t.end_ms, "title": t.title,
                            "performer": t.performer, "file": t.file} for t, _p in rip["present"]],
            "cd_text": {"title": toc.title, "performer": toc.performer},
            "barcode": toc.barcode, "disc_id": disc_id, "disc_outcome": disc_outcome,
            "candidates": len(releases), "releases": rels, "rip": rip_verdict(rip["rip_report"]),
            "occasions": guess_occasions(toc.title, *(r.get("title") for r in releases[:8])),
            "earlier": earlier, "folder": rels[0]["folder"] if len(rels) == 1 else cd_folder}


def commit_tracks(conn, toc: cd_toc.DiscToc, tracks: list[tuple], missing: list[int],
                  disc_outcome: str, releases: list[dict], rip_report: "cd_toc.RipReport | None",
                  acoustid_key: str | None, chosen: bool = False, disc_id: str | None = None,
                  occasions: tuple = ()) -> dict:
    """A tracks-mode rip's catalogue `[SPEC-CDI-090]`: each track its own
    file, `tracks` as `(TocTrack, mp3_path, audio_md5)`, with one radio and one
    album passage, identified by the same rules as an image rip's
    (`_resolve_track`).

    The album passage is the whole file -- the track as the disc plays it,
    with whatever gap the ripper wrote into the file -- so the album passages
    played in order are the disc `[SPEC-SC-047]`. The radio passage starts at
    the track's own INDEX 01, where the music does, and ends with the file;
    amplitude analysis then sets the crossfade window inside it, as for any
    radio passage `[SPEC-SA-075]`.

    A missing track is recorded against the album, never silently absent."""
    now = _now()
    ingest_folder.ensure_md5_generator_column(conn)
    ensure_sha256_column(conn)
    counts = {"identified": 0, "ambiguous": 0, "unidentified": 0}
    failed, file_ids, mbids = 0, [], []
    certain = chosen or (disc_outcome == "exact" and len(releases) == 1)
    for t, mp3_path, audio_md5 in tracks:
        st = os.stat(mp3_path)
        end_ms = t.end_ms
        file_id = conn.execute(
            "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,"
            "                   first_seen,last_seen,md5_generator,sha256)"
            " VALUES (?1,?2,?3,?4,'mp3',?5,?6,?6,?7,?8)",
            (audio_md5, mp3_path, st.st_size, st.st_mtime, end_ms, now,
             ingest_folder.md5_generator(), sha256_file(mp3_path))).lastrowid
        file_ids.append(file_id)
        radio_start = min(t.index_points.get(1, 0), max(end_ms - 1, 0))
        radio_pid = conn.execute(
            "INSERT INTO passages (file_id,kind,start_ms,end_ms,boundary_src) "
            "VALUES (?1,'radio',?2,?3,'imported:tracks-cue')", (file_id, radio_start, end_ms)).lastrowid
        album_pid = conn.execute(
            "INSERT INTO passages (file_id,kind,start_ms,end_ms,lead_in_ms,lead_out_ms,gain_db,boundary_src) "
            "VALUES (?1,'album',0,?2,0,0,0.0,'imported:tracks-cue')", (file_id, end_ms)).lastrowid
        outcome, mbid, source = _resolve_track(
            conn, t.number, t.title or toc.title, radio_pid, mp3_path, radio_start, end_ms,
            audio_md5, disc_outcome, releases, chosen, acoustid_key, now)
        counts[outcome] += 1
        mbids.append(mbid)
        for pid in (radio_pid, album_pid):
            conn.execute("INSERT INTO passage_recordings (passage_id,mbid,weight,source) "
                         "VALUES (?1,?2,1.0,?3)", (pid, mbid, source))
        tr = next((r for r in rip_report.tracks if r.number == t.number), None) if rip_report else None
        if tr is not None and not tr.ok:
            # `in_file`: this track is the file's first and only one, so the
            # damage tools find its passages there, not at position `track`.
            conn.execute(
                "INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
                "VALUES (?1,'rip','verification_failed',NULL,?2,?3)",
                (audio_md5, json.dumps({"track": t.number, "in_file": 1, "detail": tr.detail}), now))
            passage_hold.ensure_column(conn)
            conn.execute(f"UPDATE passages SET {passage_hold.COLUMN} = ?1 WHERE passage_id IN (?2, ?3)",
                         (passage_hold.damage_reason(tr.detail), radio_pid, album_pid))
            failed += 1
        detail = {"track": t.number, "tracks_mode": True, "track_count": toc.track_count,
                  "missing": missing, "verified": rip_report is not None, "format": "tracks-cue",
                  "candidates": len(releases), "chosen": releases[0].get("id") if certain and releases else None,
                  "chosen_by": "person" if chosen else None, "disc_id": disc_id, "barcode": toc.barcode,
                  "titles": [r.get("title") for r in releases[:5]]}
        conn.execute(
            "INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
            "VALUES (?1,'rip',?2,?3,?4,?5)",
            (audio_md5, "chosen" if chosen else disc_outcome, 1.0 if certain else None, json.dumps(detail), now))
    for n in missing:
        title = next((t.title for t in toc.tracks if t.number == n), None)
        conn.execute(
            "INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
            "VALUES (?1,'rip','track_missing',NULL,?2,?3)",
            (tracks[0][2], json.dumps({"track": n, "title": title}), now))
    marked = mark_occasions(conn, mbids, occasions, now) if occasions else {}
    albums = (record_release(conn, releases[0], [(t.number, m) for (t, _, _), m in zip(tracks, mbids)],
                             [md5 for _, _, md5 in tracks])
              if certain and releases else 0)
    return {"tracks": len(tracks), **counts, "verification_failed": failed, "missing": missing,
            "occasions": marked, "album_linked": albums,
            "file_ids": file_ids, "file_id": file_ids[0] if file_ids else None,
            "disc_outcome": disc_outcome, "candidates": len(releases), "mode": "tracks"}


def _record_sidecars(db_path: str, audio_md5: str, folder: str, brought) -> int | None:
    """`[GDE-INT-175]`: the hashes of the rip log, AccurateRip report and CUE
    this rip brought -- not another disc's beside them in a shared album
    folder -- taken last, after the CUE is pointed at the MP3, so they are of
    the files as they stay. In a short transaction of their own after the
    album's: a failure here is said, the add stands, and `sidecars.py --adopt`
    can record them later. The count; None if it failed."""
    names = sidecars.of_rip(folder, brought)
    if not names:
        return 0
    try:
        conn = lempi_db.connect(db_path, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
        try:
            conn.execute("PRAGMA busy_timeout = 60000")
            conn.execute("BEGIN IMMEDIATE")
            n = sidecars.record(conn, audio_md5, folder, names)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:                                   # noqa: BLE001
        say(f"WARNING: the rip's log, AccurateRip and CUE files were not recorded ({e}); "
            "tools/sidecars.py --adopt records them")
        return None
    return n


def _drop(paths) -> None:
    """MP3s made for an add that was then refused: the rip goes back to the
    inbox as it was."""
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def _plan_move(folder: str, into: str | None, releases: list[dict], toc: cd_toc.DiscToc,
               limit: int, made: list[str]) -> dict[str, str]:
    """The names the move gives each file, `made` included (the MP3s and FLACs
    not encoded yet), and the destination refused now if it cannot be held --
    before twenty seconds of encoding, not after `[SPEC-NAM-030]`. The artist
    is the one the proposed folder was named after: the edition's credit when
    there is one, else the disc's CD-TEXT."""
    if not into:
        return {}
    artist = release_credit(releases[0]) if len(releases) == 1 else toc.performer
    disc = disc_of_set(releases)
    renames = renamed_files(folder, into, artist, limit, tuple(made), disc[0] if disc else None)
    check_paths(folder, into, renames, tuple(made))
    return renames


def _move_or_drop(folder: str, into: str, renames: dict[str, str], made: list[str]) -> str:
    """`move_rip`, and on failure the encoded files removed as well: the rip
    is left in the inbox exactly as the ripper wrote it `[SPEC-NAM-050]` -- a
    failed move left the MP3 beside *The Idler Wheel*'s WAV, 2026-10-13."""
    try:
        return move_rip(folder, into, renames)
    except Exception:
        _drop(os.path.join(folder, n) for n in made)
        raise


def ingest_tracks(db_path: str, folder: str, release: str | None, into: str | None,
                  keep_flac: bool, occasions: tuple = (), allow_duplicate: bool = False,
                  album_name_max: int = ALBUM_NAME_MAX) -> dict:
    """`do_ingest` for a tracks-mode rip: each WAV encoded as the image's is,
    then the same order -- the catalogue written before any WAV is touched,
    and a FLAC copy made first when asked `[SPEC-RIP-040]`."""
    rip = open_tracks(folder)
    toc = rip["toc"]
    conn = lempi_db.connect(db_path, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA foreign_keys = ON")
    encoded = []
    try:
        # Looked up first, and a whole edition already held refused
        # `[SPEC-CDI-047]` -- before anything is encoded or moved.
        say("looking the disc up ...")
        disc_outcome, releases, disc_id = identify_disc(toc, rip["log_toc"])
        if release:
            releases = [r for r in releases if r.get("id") == release]
            if not releases:
                raise ValueError(f"release {release} is not among this disc's matches")
        refuse_duplicate(conn, releases, [t.number for t, _w in rip["present"]], allow_duplicate)
        exts = (".mp3", ".flac") if keep_flac else (".mp3",)
        made = [os.path.splitext(os.path.basename(w))[0] + e for _t, w in rip["present"] for e in exts]
        try:
            renames = _plan_move(folder, into, releases, toc, album_name_max, made)
        except FileExistsError as e:
            raise refuse_clash(conn, e, releases, disc_id, toc.barcode, into) from None

        for i, (t, wav) in enumerate(rip["present"], 1):
            say(f"encoding track {t.number} ({i} of {len(rip['present'])}) ...")
            mp3 = os.path.splitext(wav)[0] + ".mp3"
            if not encode_to_mp3(wav, mp3):
                raise RuntimeError(f"ffmpeg failed to encode {wav!r}")
            encoded.append((t, wav, mp3, ingest_folder.audio_md5(mp3)))
            if encoded[-1][3] is None:
                raise RuntimeError(f"could not hash the encoded {mp3!r}")
        for t, _wav, _mp3, md5 in encoded:
            existing = conn.execute("SELECT file_id, path FROM files WHERE audio_md5=?1", (md5,)).fetchone()
            if existing is not None:
                raise RuntimeError(f"track {t.number} is already in the library as {existing[1]!r} "
                                   f"(file_id={existing[0]})")
    except Exception:
        _drop(m for _t, _w, m, _h in encoded)
        conn.close()
        raise
    # The rip's own files, by the names the move gives them [GDE-INT-175].
    brought = [renames.get(n, n) for n in os.listdir(folder)]
    try:
        if into:
            say(f"filing it in {into} ...")
            folder = _move_or_drop(folder, into, renames, made)
            moved = lambda p: os.path.join(folder, renames.get(os.path.basename(p), os.path.basename(p)))  # noqa: E731
            encoded = [(t, moved(w), moved(m), md5) for t, w, m, md5 in encoded]
            rip["cue"] = moved(rip["cue"])
        say("writing the album ...")
        conn.execute("BEGIN IMMEDIATE")
        result = commit_tracks(conn, toc, [(t, m, md5) for t, _w, m, md5 in encoded], rip["missing"],
                               disc_outcome, releases, rip["rip_report"], secret.acoustid_key(required=False),
                               chosen=bool(release), disc_id=disc_id, occasions=occasions)
        conn.commit()
        if result.get("album_linked"):
            result["cover"] = fetch_disc_cover(conn, releases[0].get("id"))
            result["set"] = set_summary(releases[0], conn)   # [SPEC-RIP-112]
    finally:
        conn.close()

    result["folder"] = folder
    result["flac"] = None
    kept = []
    for _t, wav, _mp3, _md5 in encoded:
        if keep_flac:
            flac = os.path.splitext(wav)[0] + ".flac"
            if not encode_to_flac(wav, flac):
                kept.append(wav)
                say(f"the FLAC copy failed; the WAV is kept at {wav}")
                continue
            result["flac"] = os.path.dirname(flac)
        os.remove(wav)
    if kept:
        result["wav_kept"] = kept
    result["wav_removed"] = not kept
    cue = os.path.join(folder, os.path.basename(rip["cue"]))
    if os.path.isfile(cue):
        point_cue_at(cue)
    result["sidecars"] = _record_sidecars(db_path, encoded[0][3], folder, brought) if encoded else 0
    return result


def preview(db_path: str, folder: str) -> dict:
    """Everything the page shows before anything is done `[SPEC-CDI-040]` --
    nothing encoded, nothing written."""
    if is_tracks_rip(folder):
        return preview_tracks(db_path, folder)
    fmt, _cue, toc, wav_path, rip_report = open_rip(folder)
    disc_outcome, releases = lookup_disc_id(toc)
    releases = for_this_disc(releases, cd_toc.musicbrainz_disc_id(toc), toc)   # [SPEC-CDI-099]
    limit = album_name_max_setting(db_path)
    conn = lempi_db.connect(db_path, lempi_db.ROLE_LIBRARY)
    try:
        disc_id = cd_toc.musicbrainz_disc_id(toc)
        rels = [release_summary(r, toc.track_count, conn, limit=limit, disc_id=disc_id, barcode=toc.barcode)
                for r in releases[:8]]
        earlier = None if rels else earlier_rip(conn, None, disc_id, toc.barcode)    # [SPEC-AGN-015]
    finally:
        conn.close()
    cd_folder = proposed_folder(toc.performer, toc.title, None, limit) if toc.title else None
    # [SPEC-RIP-090]: audio no track holds, measured on the WAV, so the page
    # can say what the add will make of it.
    hidden = find_hidden(wav_path, toc, releases[0] if len(releases) == 1 else None)
    return {"dry_run": True, "format": fmt, "album_name_max": limit, "audio": os.path.basename(wav_path),
            "hidden": hidden,
            "audio_bytes": os.path.getsize(wav_path), "tracks": toc.track_count,
            "track_list": [{"number": t.number, "start_ms": t.start_ms, "end_ms": t.end_ms,
                            "title": t.title, "performer": t.performer} for t in toc.tracks],
            "cd_text": {"title": toc.title, "performer": toc.performer},
            "disc_id": cd_toc.musicbrainz_disc_id(toc), "disc_outcome": disc_outcome,
            "candidates": len(releases), "releases": rels, "rip": rip_verdict(rip_report),
            "occasions": guess_occasions(toc.title, *(r.get("title") for r in releases[:8])),
            "earlier": earlier,
            "folder": rels[0]["folder"] if len(rels) == 1 else cd_folder}


def do_ingest(db_path: str, folder: str, commit: bool, release: str | None = None,
              into: str | None = None, keep_flac: bool | None = None, occasions: tuple = (),
              allow_duplicate: bool = False, album_name_max: int | None = None,
              whole_disc: bool = False, fold: tuple = ()) -> dict:
    import sqlite3  # noqa: F401  -- the connection below is lempi_db's

    if not commit:
        return preview(db_path, folder)

    if keep_flac is None:
        keep_flac = keep_lossless_setting(db_path)
    if album_name_max is None:
        album_name_max = album_name_max_setting(db_path)
    if is_tracks_rip(folder):
        if whole_disc:
            # [SPEC-RIP-114]: a passage is a span of one file, and this rip is
            # a file a track. Refused before anything is touched.
            raise ValueError("one passage for the whole disc needs an image rip -- one file for the disc; "
                             "this rip is a file per track")
        if fold:
            # [SPEC-RIP-092]: nor is hidden audio looked for in one.
            raise ValueError("hidden audio is joined to its track only in an image rip -- one file for the disc; "
                             "this rip is a file per track")
        return ingest_tracks(db_path, folder, release, into, keep_flac, occasions, allow_duplicate,
                             album_name_max)
    fmt, _cue, toc, wav_path, rip_report = open_rip(folder)

    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(db_path, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA foreign_keys = ON")
    mp3_path = os.path.splitext(wav_path)[0] + ".mp3"
    try:
        # Looked up first, and a whole edition already held refused
        # `[SPEC-CDI-047]` -- before anything is encoded or moved.
        say("looking the disc up ...")
        disc_outcome, releases = lookup_disc_id(toc)
        releases = for_this_disc(releases, cd_toc.musicbrainz_disc_id(toc), toc)   # [SPEC-CDI-099]
        if release:
            releases = [r for r in releases if r.get("id") == release]
            if not releases:
                raise ValueError(f"release {release} is not among this disc's matches")
        refuse_duplicate(conn, releases, [t.number for t in toc.tracks], allow_duplicate)
        # The destination, before the WAV is measured or encoded: a refusal
        # here costs nothing [SPEC-AGN-015].
        stem = os.path.splitext(os.path.basename(wav_path))[0]
        made = [stem + ".mp3"] + ([stem + ".flac"] if keep_flac else [])
        try:
            renames = _plan_move(folder, into, releases, toc, album_name_max, made)
        except FileExistsError as e:
            raise refuse_clash(conn, e, releases, cd_toc.musicbrainz_disc_id(toc), toc.barcode, into) from None
        # [SPEC-RIP-090]: measured on the WAV before it is encoded and gone;
        # inside the one passage of a whole disc, nothing is hidden.
        hidden = [] if whole_disc else find_hidden(wav_path, toc, releases[0] if len(releases) == 1 else None)
        if hidden:
            say(f"hidden audio: {'; '.join(h['title'] for h in hidden)}")
        # [SPEC-RIP-092]: measured again here, so a join the preview offered
        # and this measurement does not find is refused before encoding.
        check_fold(hidden, fold, whole_disc)

        say("encoding the MP3 ...")
        if not encode_to_mp3(wav_path, mp3_path):
            raise RuntimeError(f"ffmpeg failed to encode {wav_path!r}")
        audio_md5 = ingest_folder.audio_md5(mp3_path)
        if audio_md5 is None:
            raise RuntimeError(f"could not hash the encoded {mp3_path!r}")
        existing = conn.execute(
            "SELECT file_id, path FROM files WHERE audio_md5=?1", (audio_md5,)).fetchone()
        if existing is not None:
            raise RuntimeError(
                f"this disc is already in the library as {existing[1]!r} (file_id={existing[0]})")
    except Exception:
        _drop([mp3_path])
        conn.close()
        raise
    # The rip's own files, by the names the move gives them [GDE-INT-175].
    brought = [renames.get(n, n) for n in os.listdir(folder)]
    try:
        if into:
            say(f"filing it in {into} ...")
            folder = _move_or_drop(folder, into, renames, made)
            wav_path = os.path.join(folder, renames.get(os.path.basename(wav_path), os.path.basename(wav_path)))
            mp3_path = os.path.join(folder, renames.get(os.path.basename(mp3_path), os.path.basename(mp3_path)))

        say("writing the album ...")
        conn.execute("BEGIN IMMEDIATE")
        result = commit_rip(conn, folder, toc, mp3_path, audio_md5, disc_outcome, releases,
                            rip_report, secret.acoustid_key(required=False), chosen=bool(release),
                            occasions=occasions, whole_disc=whole_disc, hidden=hidden, fold=fold)
        conn.commit()
        if result.get("album_linked"):
            result["cover"] = fetch_disc_cover(conn, releases[0].get("id"))
            result["set"] = set_summary(releases[0], conn)   # [SPEC-RIP-112]
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
            result["sidecars"] = _record_sidecars(db_path, audio_md5, folder, brought)
            return result
        result["flac"] = flac_path
    os.remove(wav_path)
    result["wav_removed"] = True
    # This rip's sheet, not the first in the folder: a second disc filed beside
    # the first re-pointed disc 1's, already pointed, and left its own naming
    # the WAV just removed -- two such sheets found on the hub, 2026-10-15.
    cue = next((os.path.join(folder, n) for n in brought if n.lower().endswith(".cue")), None)
    if cue and os.path.isfile(cue):
        point_cue_at(cue)
    result["sidecars"] = _record_sidecars(db_path, audio_md5, folder, brought)
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
    ap.add_argument("--album-name-max", type=int, default=None, metavar="N",
                    help="hold album names in folder and file names to N characters "
                         "(default: the Lempi Settings value, else %d) [SPEC-NAM-010]" % ALBUM_NAME_MAX)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--allow-duplicate", action="store_true",
                    help="add a second copy of an edition the library already holds whole [SPEC-CDI-047]")
    ap.add_argument("--whole-disc", action="store_true",
                    help="one radio and one album passage for the whole disc, not one per track "
                         "(an image rip only) [SPEC-RIP-114]")
    ap.add_argument("--fold", type=int, action="append", default=[], metavar="START_MS",
                    help="also join the hidden audio starting at START_MS to the track it leads into, "
                         "as one more radio passage; repeat for each [SPEC-RIP-092]")
    for occ, label in (("christmas", "Christmas"), ("childrens", "children's")):
        ap.add_argument(f"--{occ}", dest="occasions", action="append_const", const=occ,
                        help=f"mark every recording on the disc fully {label} [SPEC-CDI-096]")
    args = ap.parse_args()

    try:
        result = do_ingest(args.db, args.folder, args.commit, release=args.release,
                           into=args.into, keep_flac=args.keep_flac,
                           occasions=tuple(args.occasions or ()), allow_duplicate=args.allow_duplicate,
                           album_name_max=args.album_name_max, whole_disc=args.whole_disc,
                           fold=tuple(args.fold))
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
            for h in result.get("hidden") or ():
                say(f"  hidden audio: {h['title']}, {h['start_ms']}-{h['end_ms']} ms"
                    + (f" -- --fold {h['start_ms']} also joins it to track {h['leads_into']} [SPEC-RIP-092]"
                       if h.get("leads_into") is not None else ""))
        else:
            say(f"{result['tracks']} track(s): {result['identified']} identified, "
                f"{result['ambiguous']} ambiguous (in the review queue), "
                f"{result['unidentified']} unidentified, "
                f"{result['verification_failed']} verification failure(s) "
                f"(disc: {result['disc_outcome']}, {result['candidates']} candidate(s))")
            for f in result.get("folded") or ():
                say(f"  {f['title']} joined to track {f['track']}: one passage, {f['start_ms']}-{f['end_ms']} ms")
            say(f"in {result['folder']}; WAV {'removed' if result.get('wav_removed') else 'kept'}"
                + (f"; FLAC kept at {result['flac']}" if result.get("flac") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
