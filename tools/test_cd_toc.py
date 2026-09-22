#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `cd_toc.py`'s parsing and disc-id math `[SPEC-RIP-030..035]`.

The disc-id fixture below is not invented -- it is the real 14-track disc
ripped during this session's hardware test (`C:\\tmp\\eac-test\\`, outside the
repo). `EAC_CUE` is a byte-for-byte copy of that disc's actual `.cue`
(including its own `REM DISCID B90E090E` line), and the expected CDDB/
MusicBrainz disc ids below are independently verified, not assumed:
`B90E090E` reproduces EAC's own printed value, and the MusicBrainz id was
checked live against `GET /ws/2/discid/<id>` 2026-09-04, which returned
MusicBrainz's own `offsets` array matching exactly and resolved the real
release ("The Essential Cyndi Lauper", US, 2003). See `cd_toc.py`'s module
docstring for the full account.

    python tools/test_cd_toc.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cd_toc  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


EAC_CUE = """REM DISCID B90E090E
REM COMMENT "ExactAudioCopy v1.8"
PERFORMER "Unknown Artist"
TITLE "Unknown Title"
REM COMPOSER ""
FILE "Unknown Artist - Unknown Title.wav" WAVE
  TRACK 01 AUDIO
    TITLE "Track01"
    PERFORMER "Unknown Artist"
    REM COMPOSER ""
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    TITLE "Track02"
    PERFORMER "Unknown Artist"
    REM COMPOSER ""
    INDEX 01 03:55:35
  TRACK 03 AUDIO
    TITLE "Track03"
    PERFORMER "Unknown Artist"
    REM COMPOSER ""
    INDEX 01 08:58:35
  TRACK 04 AUDIO
    TITLE "Track04"
    PERFORMER "Unknown Artist"
    REM COMPOSER ""
    INDEX 01 13:36:00
  TRACK 05 AUDIO
    TITLE "Track05"
    PERFORMER "Unknown Artist"
    REM COMPOSER ""
    INDEX 01 17:24:60
  TRACK 06 AUDIO
    TITLE "Track06"
    PERFORMER "Unknown Artist"
    REM COMPOSER ""
    INDEX 01 21:25:72
  TRACK 07 AUDIO
    TITLE "Track07"
    PERFORMER "Unknown Artist"
    REM COMPOSER ""
    INDEX 00 25:36:67
    INDEX 01 25:38:55
  TRACK 08 AUDIO
    TITLE "Track08"
    PERFORMER "Unknown Artist"
    REM COMPOSER ""
    INDEX 01 30:07:27
  TRACK 09 AUDIO
    TITLE "Track09"
    PERFORMER "Unknown Artist"
    REM COMPOSER ""
    INDEX 00 34:30:52
    INDEX 01 34:32:35
  TRACK 10 AUDIO
    TITLE "Track10"
    PERFORMER "Unknown Artist"
    REM COMPOSER ""
    INDEX 00 38:52:57
    INDEX 01 38:54:40
  TRACK 11 AUDIO
    TITLE "Track11"
    PERFORMER "Unknown Artist"
    REM COMPOSER ""
    INDEX 01 43:24:25
  TRACK 12 AUDIO
    TITLE "Track12"
    PERFORMER "Unknown Artist"
    REM COMPOSER ""
    INDEX 01 48:28:22
  TRACK 13 AUDIO
    TITLE "Track13"
    PERFORMER "Unknown Artist"
    REM COMPOSER ""
    INDEX 00 52:14:35
    INDEX 01 52:16:35
  TRACK 14 AUDIO
    TITLE "Track14"
    PERFORMER "Unknown Artist"
    REM COMPOSER ""
    INDEX 00 56:13:57
    INDEX 01 56:15:27
"""

# The disc's real total length, decoded (EAC's log: track 14 ends at sector
# 269526, i.e. 269527 sectors total -- `finalize_leadout` takes milliseconds).
REAL_TOTAL_MS = round(269527 * 1000 / 75)

EXPECTED_CDDB_ID = "B90E090E"
EXPECTED_MB_ID = "HRmNGPJzLD8bB7TMvpMgEIhgdag-"
EXPECTED_MB_OFFSETS = [150, 17810, 40535, 61350, 78510, 96597, 115555, 135702,
                       155585, 175240, 195475, 218272, 235385, 253302]

# A short excerpt of the real log from the same disc (`C:\\tmp\\eac-test\\
# Unknown Artist - Unknown Title.log`), UTF-8 here for the fixture's own
# readability -- the real file is UTF-16, which `_read_log_text` handles
# separately and this fixture does not need to re-exercise.
EAC_CUE_REAL_CDTEXT = """REM DISCID AABBCCDD
PERFORMER "Real Artist"
TITLE "Real Album"
FILE "Real Artist - Real Album.wav" WAVE
  TRACK 01 AUDIO
    TITLE "First Real Song"
    PERFORMER "Real Artist"
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    TITLE "Second Real Song"
    PERFORMER "Real Artist"
    INDEX 01 03:00:00
"""

EAC_LOG_EXCERPT = """Range status and errors

Selected range

Peak level 100.0 %
Extraction speed 15.7 X
Test CRC CB7F2FF1
Copy CRC CB7F2FF1
Copy OK

No errors occurred


AccurateRip summary

Track 1 accurately ripped (confidence 24) [44BC4037] (AR v2)
Track 2 accurately ripped (confidence 24) [2A3DEE14] (AR v2)

All tracks accurately ripped

End of status report
"""

EAC_LOG_WITH_FAILURE = """Range status and errors

Selected range

Copy CRC 12345678
Test CRC 87654321
Copy CRC differs from Test CRC

AccurateRip summary

Track 1 accurately ripped (confidence 24) [44BC4037] (AR v2)
Track 2 could not be verified as accurate (confidence 0)  [00000000] (AR v2)

End of status report
"""

CDRDAO_TOC = """CD_DA

CD_TEXT {
  LANGUAGE_MAP {
    0 : EN
  }
  LANGUAGE 0 {
    TITLE "Some Album"
    PERFORMER "Some Artist"
  }
}

TRACK AUDIO
CD_TEXT {
  LANGUAGE 0 {
    TITLE "First Song"
    PERFORMER "Some Artist"
  }
}
FILE "data.bin" 0:00:00 3:00:00

TRACK AUDIO
CD_TEXT {
  LANGUAGE 0 {
    TITLE "Second Song"
    PERFORMER "Some Artist"
  }
}
PREGAP 0:02:00
FILE "data.bin" 3:00:00 2:30:00
"""


def main() -> int:
    print("frames_to_ms: the SPEC-RIP-030 formula, exactly")
    check(cd_toc.frames_to_ms(0, 0, 0) == 0, "zero is zero")
    check(cd_toc.frames_to_ms(3, 55, 35) == (3 * 60 + 55) * 1000 + round(35 * 1000 / 75),
          "3:55:35 -> ms")
    check(cd_toc.frames_to_ms(1, 0, 0) == 60000, "one minute exactly")

    print()
    print("parse_eac_cue: real fixture from this session's own hardware test")
    with tempfile.TemporaryDirectory() as d:
        cue_path = os.path.join(d, "rip.cue")
        with open(cue_path, "w", encoding="utf-8") as f:
            f.write(EAC_CUE)
        toc = cd_toc.parse_eac_cue(cue_path)

    check(toc.track_count == 14, f"expected 14 tracks, got {toc.track_count}")
    check(toc.data_file == "Unknown Artist - Unknown Title.wav",
          f"the cue's own FILE line names the audio, got {toc.data_file!r}")
    check(toc.title is None, "'Unknown Title' is EAC's own placeholder, not real CD-TEXT")
    check(toc.performer is None, "'Unknown Artist' is EAC's own placeholder, not real CD-TEXT")
    # Found live against the real disc: EAC also fabricates per-track
    # TITLE "TrackNN" / PERFORMER "Unknown Artist" when there is no real
    # CD-TEXT -- a first version of this parser missed that and wrote
    # every track as if "Track01".."Track14" were genuine titles.
    check(all(t.title is None for t in toc.tracks),
          f"EAC's per-track 'TrackNN' placeholder must not read as real CD-TEXT, "
          f"got {[t.title for t in toc.tracks]}")
    check(all(t.performer is None for t in toc.tracks),
          "EAC's repeated 'Unknown Artist' per track must not read as real CD-TEXT")

    with tempfile.TemporaryDirectory() as d:
        cue_path = os.path.join(d, "real.cue")
        with open(cue_path, "w", encoding="utf-8") as f:
            f.write(EAC_CUE_REAL_CDTEXT)
        real_toc = cd_toc.parse_eac_cue(cue_path)
    check(real_toc.title == "Real Album", f"genuine disc CD-TEXT, got {real_toc.title!r}")
    check(real_toc.tracks[0].title == "First Real Song",
          f"a disc with real CD-TEXT must keep its own per-track titles, "
          f"got {real_toc.tracks[0].title!r}")
    check(real_toc.tracks[1].title == "Second Real Song",
          f"got {real_toc.tracks[1].title!r}")
    check(toc.tracks[0].start_ms == 0, "track 1 starts at 0:00:00")
    check(toc.tracks[1].start_ms == cd_toc.frames_to_ms(3, 55, 35),
          f"track 2 starts at 3:55:35, got {toc.tracks[1].start_ms}")
    check(toc.tracks[0].end_ms == toc.tracks[1].start_ms,
          "track 1 ends where track 2 begins (no pregap on track 1)")
    # Track 7 carries both INDEX 00 (pregap) and INDEX 01 (audible start).
    t7 = toc.tracks[6]
    check(t7.pregap_ms is not None and t7.pregap_ms > 0,
          f"track 7 has a real pregap, got {t7.pregap_ms}")
    check(t7.start_ms == cd_toc.frames_to_ms(25, 38, 55),
          "track 7's start_ms is its audible INDEX 01, not the pregap")

    cd_toc.finalize_leadout(toc, REAL_TOTAL_MS)
    check(toc.tracks[-1].end_ms == REAL_TOTAL_MS, "finalize_leadout sets the last track's end")
    check(toc.leadout_sector == 269527,
          f"expected leadout sector 269527, got {toc.leadout_sector}")

    print()
    print("cddb_disc_id: reproduces EAC's own REM DISCID for the real disc")
    got_cddb = cd_toc.cddb_disc_id(toc)
    check(got_cddb == EXPECTED_CDDB_ID,
          f"expected {EXPECTED_CDDB_ID} (EAC's own printed value), got {got_cddb}")

    print()
    print("musicbrainz_disc_id: verified live against GET /ws/2/discid/<id> 2026-09-04")
    got_mb = cd_toc.musicbrainz_disc_id(toc)
    check(got_mb == EXPECTED_MB_ID,
          f"expected {EXPECTED_MB_ID} (MusicBrainz's own resolved id), got {got_mb}")
    check(len(got_mb) == 28, f"MusicBrainz disc ids are always 28 chars, got {len(got_mb)}")
    check(all(c not in got_mb for c in "+/="),
          "base64 must be remapped to MusicBrainz's URL-safe alphabet")

    toc_param = cd_toc.musicbrainz_toc_param(toc)
    parts = toc_param.split(" ")
    check(int(parts[0]) == 1, "toc= starts with first-track 1")
    check(int(parts[1]) == 14, "toc= states last-track 14")
    check([int(x) for x in parts[3:]] == EXPECTED_MB_OFFSETS,
          f"toc= offsets must match MusicBrainz's own returned offsets, got {parts[3:]}")

    print()
    print("parse_cdrdao_toc: synthetic two-track fixture with CD-TEXT and a pregap")
    with tempfile.TemporaryDirectory() as d:
        toc_path = os.path.join(d, "rip.toc")
        with open(toc_path, "w", encoding="utf-8") as f:
            f.write(CDRDAO_TOC)
        ctoc = cd_toc.parse_cdrdao_toc(toc_path)

    check(ctoc.track_count == 2, f"expected 2 tracks, got {ctoc.track_count}")
    check(ctoc.data_file == "data.bin", f"the toc's own FILE line, got {ctoc.data_file!r}")
    check(ctoc.title == "Some Album", f"disc CD-TEXT title, got {ctoc.title!r}")
    check(ctoc.performer == "Some Artist", f"disc CD-TEXT performer, got {ctoc.performer!r}")
    check(ctoc.tracks[0].start_ms == 0, "track 1 starts at 0:00:00")
    check(ctoc.tracks[0].end_ms == cd_toc.frames_to_ms(3, 0, 0),
          f"track 1's FILE length sets its own end, got {ctoc.tracks[0].end_ms}")
    check(ctoc.tracks[1].pregap_ms == cd_toc.frames_to_ms(0, 2, 0),
          f"track 2's PREGAP is read distinctly, got {ctoc.tracks[1].pregap_ms}")
    check(ctoc.tracks[1].start_ms == cd_toc.frames_to_ms(3, 0, 0),
          "track 2 starts where its own FILE says (after the pregap)")
    check(ctoc.tracks[0].title == "First Song",
          f"per-track CD-TEXT title, got {ctoc.tracks[0].title!r}")
    check(ctoc.tracks[1].title == "Second Song",
          f"per-track CD-TEXT title, got {ctoc.tracks[1].title!r}")

    print()
    print("parse_eac_log: 'No errors occurred' must not itself read as a failure")
    with tempfile.TemporaryDirectory() as d:
        log_path = os.path.join(d, "rip.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(EAC_LOG_EXCERPT)
        report = cd_toc.parse_eac_log(log_path)
    check(report.all_ok, "an all-clear log with 'No errors occurred' must read as ok")
    check(len(report.tracks) == 2, f"expected 2 AccurateRip lines, got {len(report.tracks)}")
    check(all(t.ok for t in report.tracks), "both excerpt tracks are 'accurately ripped'")

    with tempfile.TemporaryDirectory() as d:
        log_path = os.path.join(d, "rip.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(EAC_LOG_WITH_FAILURE)
        bad_report = cd_toc.parse_eac_log(log_path)
    check(not bad_report.all_ok, "a real CRC-differ line must read as a failure")
    check(bad_report.tracks[1].ok is False,
          f"track 2 'could not be verified' must read as not-ok, got {bad_report.tracks[1]}")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("cd_toc: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
