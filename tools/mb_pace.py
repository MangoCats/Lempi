#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One pace for every request this machine makes to MusicBrainz `[SPEC-MBP-010]`.

MusicBrainz asks for at most one request a second, per IP address, and blocks
an address that keeps going faster. The tools here each slept a second after
their own requests: no margin, and nothing between them, so two tools, or two
steps of one console job, could ask back to back. Every request now waits
here first, for **1.2 s** after the last one this machine made -- the limit
with a fifth to spare `[SPEC-MBP-020]` -- whichever process made it.

The last request's time is kept in one small file, `pace_file()`, locked while
it is read and moved on, so separate processes share it `[SPEC-MBP-030]`. The
player's own MusicBrainz search, on the review page, waits on the same file
(`player/src/web/musicbrainz.rs`). Each caller reserves the next free moment
and then sleeps until it outside the lock, so waiting callers queue in turn
rather than holding one another.

Only a request to musicbrainz.org waits. A test's own server, reached through
`LEMPI_MUSICBRAINZ_URL`, has no limit to keep, and the Cover Art Archive and
AcoustID keep their own `[SPEC-MBP-040]`.

    import mb_pace
    mb_pace.wait(url)       # before every request, retries included
"""
from __future__ import annotations

import os
import sys
import time
import urllib.parse

# MusicBrainz's published rate, and the margin over it the maintainer set on
# 2026-11-19: a fifth, so a slow clock or a request timed at the edge of the
# second never reads to their counter as two in one second.
LIMIT_S = 1.0
MARGIN = 0.2
MIN_INTERVAL_S = LIMIT_S * (1 + MARGIN)

# A reservation further ahead than this is not a queue of waiting callers but
# a clock that was set back: forgotten, rather than waited out for hours.
STALE_AHEAD_S = 60.0


def pace_file() -> str:
    """Where the last request's time is kept: `LEMPI_MB_PACE_FILE`, else one
    file per user -- `%LOCALAPPDATA%\\Lempi\\musicbrainz.pace` on Windows,
    `$XDG_CACHE_HOME/lempi/` (or `~/.cache/lempi/`) elsewhere. The player
    computes the same path. Not the temporary folder: a shell can set its own,
    and two processes that disagree on it would not share the pace."""
    override = os.environ.get("LEMPI_MB_PACE_FILE")
    if override:
        return override
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return os.path.join(os.environ["LOCALAPPDATA"], "Lempi", "musicbrainz.pace")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "lempi", "musicbrainz.pace")


def paced(url: str) -> bool:
    """Whether `url` is MusicBrainz's own, and so waits."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == "musicbrainz.org" or host.endswith(".musicbrainz.org")


def _lock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt
        while True:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.02)
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_UN)


def reserve(path: str | None = None, interval: float = MIN_INTERVAL_S, now: float | None = None) -> float:
    """Take the next free moment, at least `interval` after the last one taken
    on this machine, and record it. Returns that moment, in `time.time()`
    seconds; the caller sleeps until it."""
    path = path or pace_file()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        _lock(fd)
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            raw = os.read(fd, 64).decode("ascii", "replace").strip()
            now = time.time() if now is None else now
            try:
                last = float(raw)
            except ValueError:
                last = None     # a new file, or one a crash left half-written
            if last is not None and last > now + STALE_AHEAD_S:
                last = None
            slot = now if last is None else max(now, last + interval)
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, f"{slot:.3f}".encode("ascii"))
        finally:
            _unlock(fd)
    finally:
        os.close(fd)
    return slot


def wait(url: str, path: str | None = None, interval: float = MIN_INTERVAL_S) -> float:
    """Wait before asking `url`, if it is MusicBrainz's; returns the seconds
    waited. Call it before every request, a retry included: a retry is a
    request too."""
    if not paced(url):
        return 0.0
    delay = reserve(path, interval) - time.time()
    if delay > 0:
        time.sleep(delay)
        return delay
    return 0.0
