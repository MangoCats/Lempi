# SPDX-License-Identifier: AGPL-3.0-or-later
"""Extract lowlevel features and classify them into flavor `[LOG-FEX-102]`.

Two stages, deliberately separate:

  audio → streaming_extractor_music → lowlevel JSON → `lowlevel_cache`
  cache → 18 Gaia chains          → 71 dimensions  → `flavor`

Extraction is the only expensive step (~27 s/passage) and the only one needing
audio, so it caches against `audio_md5` `[SPEC-SC-080]`: improving a classifier
re-runs stage two over the cache and never re-decodes the library.

Values are written with `source = 'local:<extractor>+gaia'`. Provenance must
stay uniform across a library `[SPEC-FD-145]` -- mixing local and inherited
values costs ~8 points of retrieval accuracy -- so a partial run is for
measurement, never for listening.

Usage:
  python tools/extract_library.py <listener.db> [--limit N] [--jobs N]
  python tools/extract_library.py <listener.db> --passage <passage_id>
  python tools/extract_library.py <listener.db> --folder <album folder>
"""

from __future__ import annotations

import concurrent.futures as futures
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import gaia_classify as gc  # noqa: E402
import audio_duration  # noqa: E402

EXTRACTOR = gc.ESSENTIA_DIR / "streaming_extractor_music.exe"
FFMPEG = shutil.which("ffmpeg")
SOURCE = "local:essentia-2.1-beta2+gaia-beta1"

# A passage covering all but this much of its file is treated as the whole file,
# skipping the decode. Trim points are seconds, not minutes.
WHOLE_FILE_SLACK_MS = 5_000

# The published Windows extractor is a 32-bit build, and dies with rc=3 on a
# WAV over roughly 265 MB -- about 25 minutes of stereo 44.1 kHz
# `[LOG-FEX-107]`. A longer passage is analysed as a CENTRED window of this
# length, and tagged distinctly so the approximation is visible `[REQ-VIS-120]`
# rather than passing as a full-length measurement.
MAX_ANALYSIS_MS = 20 * 60 * 1000
WINDOW_SUFFIX = "+window20m"


_DURATION_CACHE: dict[str, float | None] = {}


def probe_duration_ms(path: str) -> float | None:
    """Real decoded duration in ms, from `audio_duration`. Cached; `None`
    if unavailable.

    Was `ffprobe -show_entries format=duration` (~50 ms) until that was
    found to silently return a bitrate ESTIMATE for a VBR file with no
    valid Xing/Info header -- 29.7% of this library, worst case 32.8
    minutes wrong -- with no error, nothing to distinguish it from a real
    answer `[REQ-LIB-145]`. A real decode costs low seconds rather than
    milliseconds, still small against ~27 s of extraction per file, and is
    the only way to actually answer the question this function is named for.
    """
    if path in _DURATION_CACHE:
        return _DURATION_CACHE[path]
    out = audio_duration.probe_duration_ms(path)
    _DURATION_CACHE[path] = out
    return out


def extract_one(path: str, start_ms: int = 0, end_ms: int = -1,
                duration_ms: int = 0) -> tuple[dict, bool] | str:
    """Extract lowlevel features for one passage -- or, if it fails, why, in
    words. Every failure here was once a bare `None`, so a run in which the
    extractor itself was missing reported "218 failed" and nothing more.

    A passage that is not the whole file is **decoded to a temporary WAV first**
    `[LOG-FEX-105]`. The extractor accepts `startTime`/`endTime` in a profile,
    but on a 192-minute MP3 that cost 169-230 s for a 4-minute window and
    *failed outright* at non-zero offsets (rc=1, rc=4). ffmpeg cuts the same
    window in 1.5 s and the extractor then sees a short file: 32.5 s total.

    Whole-file passages skip the decode entirely, which is 5,402 of 5,590 files.

    Returns `(features, windowed)`; `windowed` is true when the passage exceeded
    `MAX_ANALYSIS_MS` and only a centred window was analysed.
    """
    src = Path(path)
    if not src.exists():
        return "the audio file is not there"

    # The stored duration can be badly wrong on VBR MP3 -- 29% of files differ
    # from the decoded length by more than 5 s, and one file overstates by 38
    # MINUTES, leaving a "passage" entirely past the end of the audio
    # `[LOG-FEX-106]`. Ask the file, and skip what is not there.
    real_ms = probe_duration_ms(str(src))
    if real_ms and end_ms > 0:
        if start_ms >= real_ms - 1000:
            return "the passage starts past the end of its audio"
        end_ms = min(end_ms, real_ms)
        duration_ms = real_ms

    # Cap over-long passages to a centred window. Centred rather than leading:
    # a 43-minute work opens and closes unrepresentatively often enough that
    # the middle is the better single sample.
    windowed = False
    if end_ms > 0 and end_ms - start_ms > MAX_ANALYSIS_MS:
        mid = (start_ms + end_ms) // 2
        start_ms, end_ms = mid - MAX_ANALYSIS_MS // 2, mid + MAX_ANALYSIS_MS // 2
        windowed = True
    tag = f"{os.getpid()}_{abs(hash((path, start_ms, end_ms)))}"
    tmp_json = Path(tempfile.gettempdir()) / f"ll_{tag}.json"
    tmp_wav: Path | None = None
    try:
        target = str(src)
        whole = end_ms < 0 or (
            start_ms <= WHOLE_FILE_SLACK_MS
            and duration_ms
            and end_ms >= duration_ms - WHOLE_FILE_SLACK_MS
        )
        if not whole:
            if not FFMPEG:
                return "ffmpeg is needed to cut the passage out, and was not found"
            tmp_wav = Path(tempfile.gettempdir()) / f"ll_{tag}.wav"
            cut = subprocess.run(
                [FFMPEG, "-v", "error", "-y",
                 "-ss", f"{start_ms / 1000:.3f}", "-t", f"{(end_ms - start_ms) / 1000:.3f}",
                 "-i", str(src), "-ac", "2", "-ar", "44100", str(tmp_wav)],
                capture_output=True, timeout=300,
            )
            if cut.returncode != 0 or not tmp_wav.exists():
                return f"ffmpeg could not cut the passage (exit {cut.returncode})"
            target = str(tmp_wav)

        r = subprocess.run([str(EXTRACTOR), target, str(tmp_json)],
                           capture_output=True, timeout=600)
        if r.returncode != 0 or not tmp_json.exists():
            return f"the extractor failed (exit {r.returncode})"
        return json.loads(tmp_json.read_text(encoding="utf-8", errors="replace")), windowed
    except subprocess.TimeoutExpired:
        return "timed out"
    except json.JSONDecodeError:
        return "the extractor wrote unreadable output"
    except OSError as e:
        return f"{type(e).__name__}: {e}"
    finally:
        tmp_json.unlink(missing_ok=True)
        if tmp_wav:
            tmp_wav.unlink(missing_ok=True)


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    db = Path(args[0])
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 0
    jobs = int(args[args.index("--jobs") + 1]) if "--jobs" in args else max(1, (os.cpu_count() or 4) - 1)
    # One passage, not the whole library or a folder -- for refreshing a
    # single passage's own flavor after its boundaries change, without
    # re-running extraction over everything else that is already cached
    # and unaffected `[LOG-FEX-105]`.
    passage_id = int(args[args.index("--passage") + 1]) if "--passage" in args else None
    # One album's folder -- a CD rip just added `[REQ-LIB-305]` -- by exact
    # directory, not recursive: the convention `analyze_amplitude.py --folder`
    # already has, so the two stages of one analysis cover the same passages.
    folder = os.path.normpath(args[args.index("--folder") + 1]) if "--folder" in args else None

    # Catalogue-only, and it writes -- library half as `main`.
    con = lempi_db.connect(db, lempi_db.ROLE_LIBRARY, writable=True)
    con.execute(
        """CREATE TABLE IF NOT EXISTS lowlevel_cache (
             audio_md5 TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
             features BLOB NOT NULL, extractor TEXT NOT NULL, extracted_at TEXT NOT NULL,
             PRIMARY KEY (audio_md5, start_ms, end_ms))"""
    )
    done = {(r[0], r[1], r[2]) for r in
            con.execute("SELECT audio_md5, start_ms, end_ms FROM lowlevel_cache")}

    # Per PASSAGE, not per file [LOG-FEX-105]: one feature vector for a
    # 40-track compilation describes the average of 40 songs, which is wrong
    # flavor for every one of them.
    sql = ("SELECT f.audio_md5, f.path, pr.mbid, p.start_ms, p.end_ms, f.duration_ms "
           "FROM files f JOIN passages p USING (file_id) "
           "JOIN passage_recordings pr USING (passage_id) WHERE p.kind = 'radio'")
    params: tuple = ()
    if passage_id is not None:
        sql += " AND p.passage_id = ?"
        params = (passage_id,)
    rows = con.execute(sql, params).fetchall()
    if folder:
        rows = [r for r in rows if os.path.normpath(os.path.dirname(r[1])) == folder]
    todo = [r for r in rows if (r[0], r[3], r[4]) not in done]
    if limit:
        todo = todo[:limit]
    print(f"{len(rows)} files, {len(done)} cached, {len(todo)} to extract, {jobs} jobs", flush=True)
    if not todo:
        return 0

    # Both tools, checked before any work: without them every passage fails,
    # and on 2026-10-01 that read only as "0 extracted, 218 failed", after
    # two silent minutes, under a job marked done.
    if not EXTRACTOR.is_file():
        print(f"ERROR: the Essentia extractor is missing: {EXTRACTOR}\n"
              "  It is committed in vendor/essentia/ -- see vendor/essentia/README.md "
              "for where it comes from.", flush=True)
        return 2
    try:
        clf = gc.Classifier()
    except gc.ModelsMissing as e:
        print(f"ERROR: {e}", flush=True)
        return 2
    print(f"extracting {len(todo)} passage(s), {jobs} at a time -- about half a minute each, "
          "so the first results take a while", flush=True)

    t0 = time.time()
    ok = fail = 0
    reasons: dict[str, int] = {}
    with futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        pending = {pool.submit(extract_one, r[1], r[3], r[4], r[5]): r for r in todo}
        for i, fut in enumerate(futures.as_completed(pending), 1):
            md5, path, mbid, start_ms, end_ms, _dur = pending[fut]
            result = fut.result()
            if isinstance(result, str):
                fail += 1
                reasons[result] = reasons.get(result, 0) + 1
                print(f"  failed: {path} [{start_ms}-{end_ms} ms]: {result}", flush=True)
            else:
                doc, windowed = result
                source = SOURCE + (WINDOW_SUFFIX if windowed else "")
                con.execute(
                    "INSERT OR REPLACE INTO lowlevel_cache VALUES (?,?,?,?,?,datetime('now'))",
                    (md5, start_ms, end_ms, zlib.compress(json.dumps(doc).encode()),
                     "essentia-2.1-beta2"),
                )
                for ch, classes in clf.classify(doc).items():
                    for cls, val in classes.items():
                        con.execute(
                            "INSERT OR REPLACE INTO flavor VALUES ('recording',?,?,?,?,?,NULL)",
                            (mbid, ch, cls, val, source),
                        )
                ok += 1
            # Every tenth passage, failed or not -- counting only successes is
            # what kept an all-failing run silent to the end.
            if i % 10 == 0 or i == len(todo):
                el = time.time() - t0
                con.commit()
                print(f"  {i}/{len(todo)}  ok={ok} fail={fail}  "
                      f"{el/i:.1f}s/passage  eta {(len(todo)-i)*el/i/60:.0f} min", flush=True)
    con.commit()
    el = time.time() - t0
    print(f"\n{ok} extracted, {fail} failed in {el/60:.1f} min "
          f"({el/max(ok,1):.1f}s/passage wall, {jobs} jobs)")
    for why, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {n} failed: {why}")
    if ok:
        print(f"full library estimate: {len(rows)*el/max(ok,1)/3600:.1f} h at this rate")
    # Nothing done is a failure, not a quiet zero [CLAUDE.md section 6]; some
    # failed among many done is reported above and in the job, and exits 0.
    return 1 if ok == 0 else 0

if __name__ == "__main__":
    raise SystemExit(main())
