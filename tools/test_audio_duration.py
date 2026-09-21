# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `audio_duration.py`'s real-decode duration probe.

The one fixture worth building is the failure mode itself: a VBR MP3 whose
Xing header has been corrupted, so `ffprobe -show_entries format=duration`
falls back to a bitrate estimate and gets it wrong -- reproducing, at test
time and with no committed binary, the exact class of file that broke
`08 - Slow Ride.mp3` in the real library.

    python tools/test_audio_duration.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audio_duration as ad  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def make_vbr_mp3(path: str, duration_s: float = 5.0) -> None:
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={duration_s}",
         "-c:a", "libmp3lame", "-q:a", "4", path],
        check=True,
    )


def corrupt_xing_header(path: str) -> bool:
    """Zero the 'Xing' (or 'Info') magic bytes in place, without disturbing
    anything else in the file -- the frame itself still decodes; only the
    tag a fast prober looks for is gone. Returns whether a header was found
    to corrupt at all, so a test that depends on it can skip cleanly rather
    than pass by accident against an encoder that stopped writing one."""
    data = bytearray(open(path, "rb").read())
    i = data.find(b"Xing")
    if i < 0:
        i = data.find(b"Info")
    if i < 0:
        return False
    data[i:i + 4] = b"\x00\x00\x00\x00"
    with open(path, "wb") as f:
        f.write(data)
    return True


def probe_ffprobe_ms(path: str) -> float | None:
    """The flawed method this module replaces, for comparison only."""
    import json
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, timeout=60, text=True,
    )
    if r.returncode != 0:
        return None
    return float(json.loads(r.stdout)["format"]["duration"]) * 1000


def test_correct_on_an_ordinary_file(tmp: str) -> None:
    print("a normal VBR file, header intact, decodes to its real length")
    path = os.path.join(tmp, "ordinary.mp3")
    make_vbr_mp3(path, 5.0)
    got = ad.probe_duration_ms(path)
    check(got is not None, "probe returned nothing for a readable file")
    if got is not None:
        check(abs(got - 5000) < 200, f"got {got} ms, wanted ~5000")


def test_survives_a_corrupted_header(tmp: str) -> None:
    print("a corrupted Xing header fools ffprobe but not a real decode "
          "-- the exact failure mode found on 08 - Slow Ride.mp3")
    path = os.path.join(tmp, "corrupt.mp3")
    make_vbr_mp3(path, 5.0)
    if not corrupt_xing_header(path):
        print("  (skipped: this ffmpeg build did not write a Xing/Info "
              "header to corrupt)")
        return

    flawed = probe_ffprobe_ms(path)
    fixed = ad.probe_duration_ms(path)

    check(flawed is not None, "ffprobe could not read the corrupted file at all")
    check(fixed is not None, "the real-decode probe could not read it either")
    if flawed is not None:
        check(abs(flawed - 5000) > 100,
              f"the corruption did not actually fool ffprobe (got {flawed} ms) "
              "-- fixture is not exercising the bug")
    if fixed is not None:
        check(abs(fixed - 5000) < 200,
              f"the real-decode probe should still get the true length, got {fixed} ms")


def test_missing_file_returns_none() -> None:
    print("a file that does not exist returns None, not an exception")
    check(ad.probe_duration_ms("/no/such/file.mp3") is None, "expected None")


def main() -> int:
    if not FFMPEG or not FFPROBE:
        print("SKIPPED: ffmpeg/ffprobe not found on PATH")
        return 0

    test_missing_file_returns_none()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_correct_on_an_ordinary_file(tmp)
        test_survives_a_corrupted_header(tmp)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("audio_duration: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
