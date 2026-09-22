#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a disc-at-once rip's own table of contents [SPEC-RIP-030..035].

Pure parsing only -- no ffmpeg, no network, no database. `tools/ingest_cd.py`
is the orchestrator that calls into this; kept separate because `[SPEC-RIP-030]`
itself calls for the frame-conversion math to be "tested on its own rather
than folded into a larger one, the same lesson McRhythm's own tick-based
timing already cost this project's lineage once."

Two source formats, one output shape (`DiscToc`) -- the "thin adapter
boundary" `[SPEC-RIP-024]` describes, so nothing downstream of parsing cares
which tool actually ripped the disc:

  * EAC's `.cue` + extraction `.log`, the person-assisted Windows path.
  * cdrdao's `.toc`, the Linux path (parsing only in this pass -- Vipunen does
    not yet drive `cdrdao` itself; see `SPEC025 §2`'s Open-note and
    ROADMAP.md).

**The MusicBrainz/CDDB offset convention, verified against real data, not
assumed.** Both algorithms below measure a track's start in CD frames
(1/75 s) from a point 150 frames (2 seconds) *before* track 1's own audio --
the Red Book lead-in convention. Verified 2026-09-04 two ways against the
real 14-track disc ripped during this session's hardware test
(`C:\\tmp\\eac-test\\`, outside the repo):

  1. `cddb_disc_id()` against EAC's own `REM DISCID B90E090E` line in its
     `.cue` -- computed from this module reproduces `B90E090E` exactly,
     using EAC's reported start sectors + 150.
  2. `musicbrainz_disc_id()` against a live `GET /ws/2/discid/<id>` call --
     the computed id matched MusicBrainz's own `offsets` array exactly and
     resolved the disc: "The Essential Cyndi Lauper" (US, 2003), three
     country editions returned for the one physical disc -- itself a real
     example of `[SPEC-RIP-069]`'s down-select case, and evidence that even
     a exact-offset match can still return more than one release.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field


# --------------------------------------------------------------------- frames

def frames_to_ms(minutes: int, seconds: int, frames: int) -> int:
    """`[SPEC-RIP-030]`, verbatim: 75 frames per second, the CD's own
    timebase -- not milliseconds, and not to be confused with video frames."""
    return round((minutes * 60 + seconds) * 1000 + frames * 1000 / 75)


def frames_to_sectors(minutes: int, seconds: int, frames: int) -> int:
    """The same position as a 0-based sector count (LBA), for disc-id math
    and for comparing against a tool's own reported start/end sectors."""
    return (minutes * 60 + seconds) * 75 + frames


# ----------------------------------------------------------------------- toc

@dataclass
class TocTrack:
    number: int
    start_ms: int
    end_ms: int
    start_sector: int
    end_sector: int
    pregap_ms: int | None = None      # `[SPEC-RIP-035]`: silence before the
    index_points: dict[int, int] = field(default_factory=dict)  # index -> ms
    title: str | None = None           # CD-TEXT, when present
    performer: str | None = None


@dataclass
class DiscToc:
    tracks: list[TocTrack]
    leadout_sector: int
    title: str | None = None           # disc-level CD-TEXT
    performer: str | None = None
    source: str = ""                   # 'eac-cue' | 'cdrdao-toc'
    data_file: str | None = None       # the referenced audio, by name only --
                                        # resolved against the rip folder by
                                        # the caller, never an absolute path
                                        # this module invents itself

    @property
    def track_count(self) -> int:
        return len(self.tracks)


# --------------------------------------------------------------------- EAC .cue

_CUE_TRACK = re.compile(r'^\s*TRACK\s+(\d+)\s+AUDIO\s*$')
_CUE_INDEX = re.compile(r'^\s*INDEX\s+(\d+)\s+(\d+):(\d+):(\d+)\s*$')
_CUE_TITLE = re.compile(r'^\s*TITLE\s+"(.*)"\s*$')
_CUE_PERFORMER = re.compile(r'^\s*PERFORMER\s+"(.*)"\s*$')
_CUE_FILE = re.compile(r'^\s*FILE\s+"(.*)"\s+\w+\s*$')


def parse_eac_cue(path: str) -> DiscToc:
    """EAC's own `.cue` sheet. Real example (this session's test disc):

        REM DISCID B90E090E
        PERFORMER "Unknown Artist"
        TITLE "Unknown Title"
        FILE "Unknown Artist - Unknown Title.wav" WAVE
          TRACK 01 AUDIO
            TITLE "Track01"
            PERFORMER "Unknown Artist"
            INDEX 01 00:00:00
          TRACK 02 AUDIO
            ...
            INDEX 01 03:55:35

    `INDEX 00`, when present, is the pregap start `[SPEC-RIP-035]` -- kept
    distinct from `INDEX 01`, the audible start, never collapsed into the
    previous track's own length.

    A disc-level `TITLE`/`PERFORMER` before the first `TRACK` line is
    EAC's CD-TEXT reading when the disc carries it; "Unknown Artist"/
    "Unknown Title" (EAC's own placeholder when the disc has none) is
    recognised and treated as **absent**, not as real CD-TEXT -- a disc
    with genuinely no CD-TEXT must not be misreported as one titled
    "Unknown Title" `[SPEC-RIP-066]`.
    """
    disc_title: str | None = None
    disc_performer: str | None = None
    data_file: str | None = None
    tracks: list[TocTrack] = []
    cur_track: int | None = None
    cur_title: str | None = None
    cur_performer: str | None = None
    cur_indexes: dict[int, int] = {}
    seen_track_line = False

    def flush():
        nonlocal cur_track, cur_title, cur_performer, cur_indexes
        if cur_track is not None:
            tracks.append(TocTrack(
                number=cur_track, start_ms=0, end_ms=0,
                start_sector=0, end_sector=0,
                index_points=dict(cur_indexes),
                title=cur_title, performer=cur_performer))
        cur_track, cur_title, cur_performer, cur_indexes = None, None, None, {}

    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            m = _CUE_FILE.match(line)
            if m and data_file is None:
                # A single-file DAO image is the shape both the
                # person-assisted flow and this module assume; a cue sheet
                # naming more than one `FILE` (per-track rips) is read as
                # its first reference only -- multi-file cue support is not
                # in this pass's scope.
                data_file = m.group(1)
                continue
            m = _CUE_TRACK.match(line)
            if m:
                flush()
                cur_track = int(m.group(1))
                seen_track_line = True
                continue
            m = _CUE_INDEX.match(line)
            if m and cur_track is not None:
                idx = int(m.group(1))
                ms = frames_to_ms(int(m.group(2)), int(m.group(3)), int(m.group(4)))
                cur_indexes[idx] = ms
                continue
            m = _CUE_TITLE.match(line)
            if m:
                if cur_track is not None:
                    cur_title = m.group(1)
                elif not seen_track_line:
                    disc_title = m.group(1)
                continue
            m = _CUE_PERFORMER.match(line)
            if m:
                if cur_track is not None:
                    cur_performer = m.group(1)
                elif not seen_track_line:
                    disc_performer = m.group(1)
                continue
        flush()

    no_real_cd_text = disc_title == "Unknown Title" and disc_performer == "Unknown Artist"
    if disc_title == "Unknown Title":
        disc_title = None
    if disc_performer == "Unknown Artist":
        disc_performer = None

    # **Found against real data, not anticipated up front**: EAC does not
    # merely leave per-track TITLE/PERFORMER blank when a disc carries no
    # real CD-TEXT -- it fabricates `TITLE "Track01"`, `TITLE "Track02"`,
    # ... and repeats `PERFORMER "Unknown Artist"` on every track, the same
    # placeholder convention as the disc-level fields, one level down. A
    # first version of this parser missed that (it only checked the
    # disc-level fields) and, run against this session's own real rip,
    # wrote every one of 14 tracks as if it carried genuine CD-TEXT titled
    # "Track01".."Track14" -- caught by the real smoke test, not a unit
    # test, per `[GOV-SRC-020]`. When the disc-level fields are the
    # placeholder, no per-track field is trusted either.
    if no_real_cd_text:
        for t in tracks:
            if t.title == f"Track{t.number:02d}":
                t.title = None
            if t.performer == "Unknown Artist":
                t.performer = None

    # Boundaries: each track's audible start is its own INDEX 01 (or INDEX
    # 00 -- some cue writers only emit one INDEX line and mean it as the
    # start of *audio*, not a pregap, when 00 never appears at all).
    # `end_ms` is the next track's own start; the very last track's end is
    # supplied by the caller once the audio's real decoded length is known
    # (a cue sheet, unlike a TOC read from the disc live, cannot state the
    # length of a track it never measured against real leadout).
    for i, t in enumerate(tracks):
        t.start_ms = t.index_points.get(1, t.index_points.get(0, 0))
        t.pregap_ms = None
        if 0 in t.index_points and 1 in t.index_points:
            t.pregap_ms = t.index_points[1] - t.index_points[0]
        if i + 1 < len(tracks):
            nxt = tracks[i + 1]
            t.end_ms = nxt.index_points.get(0, nxt.index_points.get(1, 0))
        t.start_sector = round(t.start_ms * 75 / 1000)

    return DiscToc(tracks=tracks, leadout_sector=0, title=disc_title,
                    performer=disc_performer, source="eac-cue", data_file=data_file)


# ------------------------------------------------------------------- EAC .log

@dataclass
class TrackRipReport:
    number: int
    ok: bool
    detail: str


@dataclass
class RipReport:
    tracks: list[TrackRipReport]
    all_ok: bool


_LOG_TOC_ROW = re.compile(
    r'^\s*(\d+)\s*\|\s*[\d:.]+\s*\|\s*[\d:.]+\s*\|\s*(\d+)\s*\|\s*(\d+)\s*$')
_LOG_ACCURATERIP = re.compile(
    r'Track\s+(\d+)\s+(accurately ripped|(?:could not be verified)'
    r'|(?:differs from AccurateRip))', re.IGNORECASE)
_LOG_COPY_OK = re.compile(r'Copy\s+OK', re.IGNORECASE)
# `\bError\b`, not a bare substring search: "No errors occurred" -- EAC's
# own all-clear line -- contains "error" as a substring and would otherwise
# read as a failure it explicitly is not. The word boundary after "Error"
# also excludes "Errors" from a bare match, for the same reason.
_LOG_COPY_ERR = re.compile(r'(?:Copy CRC.*differ|\bError\b|Suspicious position)', re.IGNORECASE)


def parse_eac_log(path: str) -> RipReport:
    """EAC's extraction log -- specifically the AccurateRip summary and any
    'No errors occurred'/error lines, feeding `[SPEC-RIP-054]`'s per-track
    verification-failure recording.

    EAC's own log is UTF-16 with embedded padding between characters (a
    Windows console-legacy quirk) -- read permissively rather than assuming
    one encoding, the same posture `ingest_folder.py`'s own `say()` already
    takes toward console encoding limits.
    """
    text = _read_log_text(path)
    verdicts: dict[int, tuple[bool, str]] = {}
    for m in _LOG_ACCURATERIP.finditer(text):
        n = int(m.group(1))
        ok = m.group(2).lower() == "accurately ripped"
        verdicts[n] = (ok, m.group(2))
    # A track AccurateRip never mentions (no DB entry, or the plugin was
    # absent) is judged by "Copy OK" elsewhere in its own range instead --
    # best effort, never silent `[SPEC-RIP-054]`. This module does not
    # attempt to slice the log into per-track ranges for that fallback; the
    # orchestrator applies it only for tracks AccurateRip stayed silent on.
    all_ok = _LOG_COPY_ERR.search(text) is None and bool(_LOG_COPY_OK.search(text))
    tracks = [TrackRipReport(number=n, ok=ok, detail=detail)
              for n, (ok, detail) in sorted(verdicts.items())]
    return RipReport(tracks=tracks, all_ok=all_ok and all(t.ok for t in tracks))


def _read_log_text(path: str) -> str:
    """EAC's own log is UTF-16 LE with a BOM (`\\xff\\xfe`), confirmed
    against the real log from this session's hardware test. **The BOM must
    be checked explicitly, not merely tried** -- UTF-16 decoding rarely
    raises on arbitrary bytes (it just pairs them into code units), so a
    plain UTF-8 file tried as UTF-16 first "succeeds" into mojibake instead
    of failing over, which is not hypothetical: it broke this function's
    own first version against a synthetic UTF-8 test fixture."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except (UnicodeDecodeError, UnicodeError):
            pass
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("latin-1", errors="replace")


# ---------------------------------------------------------------- cdrdao .toc

_TOC_TRACK = re.compile(r'^\s*TRACK\s+AUDIO\s*$')
_TOC_FILE_START = re.compile(
    r'^\s*(?:FILE|AUDIOFILE)\s+"([^"]*)"\s+(\d+):(\d+):(\d+)'
    r'(?:\s+(\d+):(\d+):(\d+))?\s*$')
_TOC_PREGAP = re.compile(r'^\s*PREGAP\s+(\d+):(\d+):(\d+)\s*$')
_TOC_CD_TITLE = re.compile(r'^\s*TITLE\s+"(.*)"\s*$')
_TOC_CD_PERFORMER = re.compile(r'^\s*PERFORMER\s+"(.*)"\s*$')


def parse_cdrdao_toc(path: str) -> DiscToc:
    """cdrdao's own text `.toc` (`toc2cue`'s input format) -- structurally
    different from EAC's `.cue` (blocks rather than one `INDEX` line per
    mark) but expressing the same `MM:SS:FF` positions `[SPEC-RIP-030]`.

    Parsing only in this pass -- Vipunen does not yet spawn `cdrdao` itself.
    Deferred past "no test hardware", per `[LOG-RIP-030]`: the one drive
    tested this session returns noise, not audio, over `cdrdao`'s own Linux
    DAE path, so automating the spawn has nothing verified-working to
    drive yet -- a functioning Linux DAO rip needs independent verification
    on some drive first, the same discipline `[LOG-RIP-040..050]` applied
    on Windows before EAC was trusted. This function exists so a `.toc`
    produced by a person running `cdrdao read-cd` by hand already feeds the
    same downstream pipeline as EAC's output does, once that verification
    happens.

    **Unlike `parse_eac_cue`, this parser is built from cdrdao's documented
    `.toc` grammar and a synthetic fixture, not checked against a real
    cdrdao-generated file** -- no Linux hardware exists in this session to
    rip one against (`[GOV-SRC-020]`). `FILE "name" start length` is read
    as "start reading the referenced audio at `start`, for `length`" (the
    documented meaning for the single-shared-datafile shape `read-cd`
    itself produces); a real fixture should replace/extend the synthetic
    one in `test_cd_toc.py` the first time this actually runs against
    genuine cdrdao output.
    """
    disc_title: str | None = None
    disc_performer: str | None = None
    data_file: str | None = None
    tracks: list[TocTrack] = []
    cd_text_depth = 0   # >0 while inside a CD_TEXT block, at any nesting
    seen_track = False

    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            if cd_text_depth == 0 and line.startswith("CD_TEXT"):
                cd_text_depth = 1
                continue
            if cd_text_depth > 0:
                # Depth tracks nesting (CD_TEXT { LANGUAGE_MAP { ... } ...
                # LANGUAGE 0 { ... } }) so the block's own first inner `}`
                # (closing LANGUAGE_MAP) does not end it early.
                cd_text_depth += line.count("{") - line.count("}")
                m = _TOC_CD_TITLE.match(raw_line)
                if m:
                    if seen_track and tracks and tracks[-1].title is None:
                        tracks[-1].title = m.group(1)
                    elif not seen_track and disc_title is None:
                        disc_title = m.group(1)
                m = _TOC_CD_PERFORMER.match(raw_line)
                if m:
                    if seen_track and tracks and tracks[-1].performer is None:
                        tracks[-1].performer = m.group(1)
                    elif not seen_track and disc_performer is None:
                        disc_performer = m.group(1)
                if cd_text_depth <= 0:
                    cd_text_depth = 0
                continue
            if _TOC_TRACK.match(raw_line):
                seen_track = True
                tracks.append(TocTrack(
                    number=len(tracks) + 1, start_ms=0, end_ms=0,
                    start_sector=0, end_sector=0))
                continue
            m = _TOC_PREGAP.match(raw_line)
            if m and tracks:
                tracks[-1].pregap_ms = frames_to_ms(*(int(g) for g in m.groups()))
                continue
            m = _TOC_FILE_START.match(raw_line)
            if m and tracks:
                if data_file is None:
                    data_file = m.group(1)
                start_ms = frames_to_ms(int(m.group(2)), int(m.group(3)), int(m.group(4)))
                tracks[-1].start_ms = start_ms
                if m.group(5) is not None:
                    length_ms = frames_to_ms(int(m.group(5)), int(m.group(6)), int(m.group(7)))
                    tracks[-1].end_ms = start_ms + length_ms

    # A `.toc` line's own `FILE`/`AUDIOFILE` length, when stated, already
    # set `end_ms` above; otherwise each track ends where the next begins,
    # same as the `.cue` parser.
    for i, t in enumerate(tracks):
        if not t.end_ms and i + 1 < len(tracks):
            t.end_ms = tracks[i + 1].start_ms
        t.start_sector = round(t.start_ms * 75 / 1000)

    return DiscToc(tracks=tracks, leadout_sector=0, title=disc_title,
                    performer=disc_performer, source="cdrdao-toc", data_file=data_file)


def finalize_leadout(toc: DiscToc, total_ms: int) -> None:
    """Neither parser above can state the leadout or the last track's own
    end -- a cue sheet or a `.toc` states *starts*; the true end is wherever
    the audio actually stops, which only the decoded file can say. Called
    once, by the orchestrator, after probing the real audio duration --
    mutates `toc` in place since every track after the last is already
    correct and only the tail needs filling in.
    """
    if toc.tracks:
        toc.tracks[-1].end_ms = total_ms
    toc.leadout_sector = round(total_ms * 75 / 1000)


# --------------------------------------------------------------------- disc id

def _offsets_with_leadin(toc: DiscToc) -> tuple[list[int], int]:
    """Track start sectors and the leadout sector, both shifted by the
    150-frame (2 s) Red Book lead-in `[SPEC-RIP-030]`'s own timebase implies
    but does not itself state -- the convention both algorithms below need.
    Verified against real data; see this module's own docstring."""
    offsets = [round(t.start_ms * 75 / 1000) + 150 for t in toc.tracks]
    leadout = toc.leadout_sector + 150
    return offsets, leadout


def cddb_disc_id(toc: DiscToc) -> str:
    """The classic CDDB/FreeDB disc id -- 8 hex chars. Not what MusicBrainz
    looks discs up by (`musicbrainz_disc_id` is), but EAC prints this one
    itself (`REM DISCID`) which is what makes it independently checkable:
    this implementation reproduces EAC's own `B90E090E` for the real disc
    ripped in this session's hardware test, computed from nothing but the
    track start sectors already in this module's own `DiscToc`.
    """
    offsets, leadout = _offsets_with_leadin(toc)

    def digit_sum(n: int) -> int:
        s = 0
        while n > 0:
            s += n % 10
            n //= 10
        return s

    n = sum(digit_sum(o // 75) for o in offsets)
    t = leadout // 75 - offsets[0] // 75
    value = ((n % 0xFF) << 24) | (t << 8) | len(offsets)
    return format(value & 0xFFFFFFFF, "08X")


def musicbrainz_disc_id(toc: DiscToc) -> str:
    """The MusicBrainz disc id `[SPEC-RIP-060]` looks discs up by --
    `GET /ws/2/discid/<id>`. SHA-1 over first/last track number, the
    leadout offset, and 99 fixed track-offset slots (zero-padded past the
    real track count), base64 with the URL-unsafe characters remapped.

    Verified live 2026-09-04 against MusicBrainz's own API using this
    session's real 14-track test disc: the id computed here matched
    MusicBrainz's own returned `offsets` array exactly and resolved a real
    release ("The Essential Cyndi Lauper", US, 2003) -- not merely a
    self-consistency check, an actual round-trip against the service this
    exists to query.
    """
    offsets, leadout = _offsets_with_leadin(toc)
    sha = hashlib.sha1()
    sha.update(b"%02X" % 1)
    sha.update(b"%02X" % len(offsets))
    sha.update(b"%08X" % leadout)
    for i in range(99):
        sha.update(b"%08X" % (offsets[i] if i < len(offsets) else 0))
    b64 = base64.b64encode(sha.digest()).decode("ascii")
    return b64.replace("+", ".").replace("/", "_").replace("=", "-")


def musicbrainz_toc_param(toc: DiscToc) -> str:
    """The `toc=` query value MusicBrainz's own fuzzy-match endpoint takes
    alongside the id -- `first last leadout offset1 offset2 ...`, the exact
    numbers `musicbrainz_disc_id` hashes, space-joined per MB's own API
    (percent-encoded to `+` by the caller's own URL builder)."""
    offsets, leadout = _offsets_with_leadin(toc)
    parts = [str(1), str(len(offsets)), str(leadout)] + [str(o) for o in offsets]
    return " ".join(parts)
