# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's real, decoded duration -- never a header/bitrate estimate.

`ffprobe -show_entries format=duration` is fast (~50 ms) and correct for a
CBR file, or a VBR file with a valid Xing/Info header. For a VBR file
*without* one it falls back to `file_size / average_bitrate` -- ffprobe
says so on stderr ("Estimating duration from bitrate, this may be
inaccurate") -- and returns a number shaped exactly like a real duration,
no error flag, wrong by however far the file's actual average bitrate
differs from that estimate.

Measured against the real library, full-decode vs. stored: **29.7% of
files (1,695 of 5,709) wrong by more than a second**, median error 34s,
worst case 32.8 minutes. Every one of those 1,695 is *also* invisible to
`ffprobe format=duration` re-checked today -- it agrees with the wrong
stored value in all 1,695, because that same bitrate-estimate method is
almost certainly what produced it in the first place. A repair that
checks a number against the method that generated it can never disagree
with it `[REQ-LIB-145]`, `[GDE-FEX-106]`.

`probe_duration_ms` actually decodes the file and reads ffmpeg's own
`-progress` accounting instead. Costs low-single-digit seconds on a
typical track rather than milliseconds -- real, not free, but small next
to what already runs per file in this pipeline (extraction alone is
~27s/file) -- and is the only number here that answers what
`[SPEC-SC-030]`'s own schema comment asks for: "decoded, not
header-claimed".
"""

from __future__ import annotations

import re
import shutil
import subprocess

FFMPEG = shutil.which("ffmpeg")


def probe_duration_ms(path: str, timeout: float = 600.0) -> float | None:
    """The file's real decoded length in ms, or `None` if it can't be read.

    Reads `out_time_us` from `ffmpeg -progress pipe:1`, the last line of it
    seen before the process exits -- named "us" and, in every ffmpeg build
    this was checked against, *also* what `out_time_ms` itself reports (a
    known quirk: both keys carry microseconds despite the name). Verified
    on a real file against an independent raw-PCM sample-count cross-check,
    agreeing to five decimal places. Divide by 1000, not 1e6, to reach ms.
    """
    if not FFMPEG:
        return None
    try:
        r = subprocess.run(
            [FFMPEG, "-v", "error", "-nostats", "-progress", "pipe:1",
             "-i", path, "-vn", "-f", "null", "-"],
            capture_output=True, timeout=timeout, text=True,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    last = None
    for line in r.stdout.splitlines():
        if line.startswith("out_time_us="):
            last = line.split("=", 1)[1]
    if last is None or not re.fullmatch(r"-?\d+", last):
        return None
    return int(last) / 1000.0
