#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Vipunen S6: automatic lead-in/lead-out detection `[SPEC-SA-075]`.

A direct translation of the inherited McRhythm algorithm
(`docs/inherited/mcrhythm/MCR-SPEC025-amplitude_analysis.md`) -- an RMS
envelope over 100 ms windows, absolute-dB thresholds, a "quick ramp"
shortcut for passages that start or end at full volume already. Not the
segmentation cascade (`MCR-SPEC033`, still PROVISIONAL/P4 in `SPEC007` §6,
genuinely unbuilt): that solves "where are the passage boundaries inside one
continuous file"; this solves "how long does *this* passage's own amplitude
take to ramp up or down", a self-contained, much smaller question.

**A sign error in the inherited spec, corrected here, not copied blindly:**
its own worked formula `threshold = 10^(dB/20)` with `dB=45` computes
~177.8 -- a linear-amplitude value the same document says must be in
`[0.0, 1.0]` (RMS of normalized audio). Converting "N dB below full scale"
to a linear amplitude is `10^(-dB/20)`, matching every other dB convention
already in this codebase (`REQ-AUD-154`'s own table: -72 dB -> 0.00025, not
10^(72/20)). Used here: `threshold_lead_in = 10^(-45/20) ~ 0.0056`,
`threshold_lead_out = 10^(-40/20) = 0.01`.

A-weighting is skipped, matching the spec's own actual shipped default
(`apply_a_weighting = false`) -- a decision already made upstream, not a
shortfall of this port.

**Selection respects `[SPEC-SA-080]`**: "manual edits outrank computed
values permanently... never silently recomputed". A passage with
`boundary_src='manual'` is never touched, with or without `--recheck`. By
default, only passages with no lead-in/lead-out yet (`lead_in_ms IS NULL`)
are analyzed; `--recheck` re-analyzes already-auto-set (non-manual) ones too.

Writes directly, the same posture `extract_library.py` already takes for its
own cache-filling passes (an objective computation over what is missing, not
an editorial decision among candidates like `suggest_release.py --accept`
is) -- no `--commit` gate.

    python tools/analyze_amplitude.py data/library.db [--limit N] [--jobs N] [--recheck]
    python tools/analyze_amplitude.py data/library.db --folder "C:/Music/Foghat/The Best of Foghat"
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import shutil
import sqlite3
import subprocess
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import time

import numpy as np

FFMPEG = shutil.which("ffmpeg")

# [AMP-PARAM-010]'s own "hardcoded, not user-configurable" constants,
# unchanged except where noted.
RMS_WINDOW_MS = 100
LEAD_IN_THRESHOLD_DB = 45.0     # dB BELOW full scale -- see the sign note above
LEAD_OUT_THRESHOLD_DB = 40.0    # dB BELOW full scale
QUICK_RAMP_DURATION_S = 1.0
MAX_LEAD_S = 10.0
QUIET_PEAK_RMS = 0.05           # [AMP-EDGE-020]
CLIP_THRESHOLD = 0.99           # [AMP-EDGE-030]
SAMPLE_RATE = 44100


def db_to_linear(db_below_full_scale: float) -> float:
    return 10 ** (-db_below_full_scale / 20.0)


THRESHOLD_LEAD_IN = db_to_linear(LEAD_IN_THRESHOLD_DB)
THRESHOLD_LEAD_OUT = db_to_linear(LEAD_OUT_THRESHOLD_DB)


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


# --------------------------------------------------------------- the math ---

def rms_envelope(samples: np.ndarray, sample_rate: int = SAMPLE_RATE,
                 window_ms: int = RMS_WINDOW_MS) -> np.ndarray:
    """[AMP-RMS-010]: RMS over non-overlapping windows. `samples` already
    normalized to [-1, 1]; a trailing partial window is dropped rather than
    padded, since it would only ever pull an edge value toward zero.
    """
    window = max(1, int(sample_rate * window_ms / 1000))
    n_windows = len(samples) // window
    if n_windows == 0:
        return np.array([])
    trimmed = samples[: n_windows * window].reshape(n_windows, window).astype(np.float64)
    return np.sqrt(np.mean(trimmed ** 2, axis=1))


def detect_lead_in(envelope: np.ndarray, window_ms: int = RMS_WINDOW_MS) -> tuple[int, bool]:
    """`[AMP-LEADIN-020]` Steps 3-5. Returns `(lead_in_ms, quick_ramp_up)`."""
    if len(envelope) == 0:
        return 0, False
    over_out = np.nonzero(envelope >= THRESHOLD_LEAD_OUT)[0]
    if len(over_out) == 0:
        return 0, False  # never reaches full-content loudness at all
    time_to_75_s = over_out[0] * window_ms / 1000.0
    if time_to_75_s < QUICK_RAMP_DURATION_S:
        return 0, True
    over_in = np.nonzero(envelope >= THRESHOLD_LEAD_IN)[0]
    if len(over_in) == 0:
        return 0, False
    lead_in_s = min(over_in[0] * window_ms / 1000.0, MAX_LEAD_S)
    return round(lead_in_s * 1000), False


def detect_lead_out(envelope: np.ndarray, window_ms: int = RMS_WINDOW_MS) -> tuple[int, bool]:
    """`[AMP-LEADOUT-020]` Steps 3-5, mirrored from the end. Index arithmetic
    matches the spec's own two worked examples exactly (window 1795 of 1800
    -> 0.5s; window 1768 of 1800 -> 3.2s), i.e. `(n - index) * window_ms`,
    not `(n - 1 - index)`.
    """
    n = len(envelope)
    if n == 0:
        return 0, False
    over_out = np.nonzero(envelope >= THRESHOLD_LEAD_OUT)[0]
    if len(over_out) == 0:
        return 0, False
    time_from_75_to_end_s = (n - over_out[-1]) * window_ms / 1000.0
    if time_from_75_to_end_s < QUICK_RAMP_DURATION_S:
        return 0, True
    over_in = np.nonzero(envelope >= THRESHOLD_LEAD_IN)[0]
    if len(over_in) == 0:
        return 0, False
    lead_out_s = min((n - over_in[-1]) * window_ms / 1000.0, MAX_LEAD_S)
    return round(lead_out_s * 1000), False


# ------------------------------------------------------------- the decode ---

def decode_pcm(path: str, start_ms: int, end_ms: int,
               sample_rate: int = SAMPLE_RATE, timeout: float = 120.0) -> np.ndarray | None:
    """Mono PCM, normalized to `[-1, 1]` -- the amplitude envelope needs
    overall level, not channels. Reuses the exact `ffmpeg -ss/-t` slicing
    `extract_library.py` already established `[LOG-FEX-105]`, piped to
    stdout instead of a temp WAV since nothing downstream needs a file.
    """
    if not FFMPEG:
        return None
    args = [FFMPEG, "-v", "error"]
    if start_ms > 0:
        args += ["-ss", f"{start_ms / 1000:.3f}"]
    if end_ms > start_ms:
        args += ["-t", f"{(end_ms - start_ms) / 1000:.3f}"]
    args += ["-i", path, "-ac", "1", "-ar", str(sample_rate),
             "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    raw = np.frombuffer(r.stdout, dtype="<i2")
    if len(raw) == 0:
        return None
    return raw.astype(np.float64) / 32768.0


def analyze_passage(path: str, start_ms: int, end_ms: int,
                    sample_rate: int = SAMPLE_RATE) -> dict | None:
    """`None` if the audio could not be decoded at all; otherwise always a
    result, even a `0/0` one -- constant amplitude and "never gets loud"
    both fall out of `detect_lead_in`/`detect_lead_out` naturally rather
    than needing their own special case `[AMP-EDGE-010]`.
    """
    samples = decode_pcm(path, start_ms, end_ms, sample_rate)
    if samples is None:
        return None
    envelope = rms_envelope(samples, sample_rate)
    lead_in_ms, quick_up = detect_lead_in(envelope)
    lead_out_ms, quick_down = detect_lead_out(envelope)
    peak_rms = float(np.max(envelope)) if len(envelope) else 0.0
    return {
        "lead_in_ms": lead_in_ms,
        "lead_out_ms": lead_out_ms,
        "peak_rms": round(peak_rms, 4),
        "quick_ramp_up": quick_up,
        "quick_ramp_down": quick_down,
        "quiet": peak_rms < QUIET_PEAK_RMS,           # [AMP-EDGE-020]
        "clipping": bool(np.any(np.abs(samples) >= CLIP_THRESHOLD)),  # [AMP-EDGE-030]
    }


# --------------------------------------------------------------- selection ---

def select_passages(conn: sqlite3.Connection, folder: str | None, recheck: bool) -> list:
    """`[SPEC-SA-080]`: `boundary_src='manual'` is excluded unconditionally,
    `--recheck` or not. `--folder` is an exact directory match, not
    recursive -- the same convention `suggest_release.py`'s own folder
    scoping already established, kept local here rather than imported since
    that function's own shape is tied to file-tag/track matching this tool
    has no use for.
    """
    where = ["p.kind='radio'", "(p.boundary_src IS NULL OR p.boundary_src != 'manual')"]
    if not recheck:
        where.append("p.lead_in_ms IS NULL")
    rows = conn.execute(
        f"SELECT p.passage_id, f.path, p.start_ms, p.end_ms, f.audio_md5 "
        f"FROM passages p JOIN files f USING(file_id) WHERE {' AND '.join(where)}"
    ).fetchall()
    if folder:
        folder_norm = os.path.normpath(folder)
        rows = [r for r in rows if os.path.normpath(os.path.dirname(r[1])) == folder_norm]
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--folder", help="exact directory match, not recursive; omit for the whole library")
    ap.add_argument("--limit", type=int, default=0, help="stop after N passages")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument("--recheck", action="store_true",
                     help="re-analyze passages this tool already set, not just NULL ones "
                          "-- boundary_src='manual' is still excluded either way")
    ap.add_argument("--json", action="store_true",
                     help="also print one final JSON summary line, for a caller "
                          "(the Vipunen console's analyze-amplitude job) rather than a person")
    args = ap.parse_args()

    if not FFMPEG:
        say("ffmpeg not found on PATH -- cannot decode audio for analysis")
        if args.json:
            say(json.dumps({"ok": False, "error": "ffmpeg not found"}))
        return 1

    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("""CREATE TABLE IF NOT EXISTS ingest_decisions (
        decision_id INTEGER PRIMARY KEY, audio_md5 TEXT, stage TEXT,
        outcome TEXT, confidence REAL, detail TEXT, decided_at INTEGER)""")

    todo = select_passages(conn, args.folder, args.recheck)
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        say("nothing to analyze" + (f" in {args.folder}" if args.folder else ""))
        if args.json:
            say(json.dumps({"ok": True, "analyzed": 0, "failed": 0, "quiet": 0, "clipped": 0}))
        return 0

    say(f"{len(todo)} passage(s) to analyze, {args.jobs} job(s)")
    analyzed = failed = quiet = clipped = 0
    now = int(time.time())
    t0 = time.time()
    with futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = {pool.submit(analyze_passage, path, start_ms, end_ms): (passage_id, audio_md5)
                   for passage_id, path, start_ms, end_ms, audio_md5 in todo}
        for i, fut in enumerate(futures.as_completed(pending), 1):
            passage_id, audio_md5 = pending[fut]
            result = fut.result()
            if result is None:
                failed += 1
                continue
            conn.execute(
                "UPDATE passages SET lead_in_ms=?1, lead_out_ms=?2 WHERE passage_id=?3",
                (result["lead_in_ms"], result["lead_out_ms"], passage_id))
            conn.execute(
                "INSERT INTO ingest_decisions (audio_md5, stage, outcome, confidence, detail, decided_at) "
                "VALUES (?1,'amplitude_analysis',?2,?3,?4,?5)",
                (audio_md5, f"lead_in={result['lead_in_ms']}ms lead_out={result['lead_out_ms']}ms",
                 result["peak_rms"], json.dumps(result), now))
            analyzed += 1
            if result["quiet"]:
                quiet += 1
            if result["clipping"]:
                clipped += 1
            if i % 25 == 0 or i == len(todo):
                conn.commit()
                el = time.time() - t0
                say(f"  {i}/{len(todo)}  ok={analyzed} failed={failed}  "
                    f"{el / i:.1f}s/passage  eta {(len(todo) - i) * el / i / 60:.0f} min")
    conn.commit()

    el = time.time() - t0
    say(f"\n{analyzed} analyzed, {failed} failed in {el / 60:.1f} min "
        f"({quiet} quiet, {clipped} with clipping detected)")
    if args.json:
        say(json.dumps({"ok": True, "analyzed": analyzed, "failed": failed,
                        "quiet": quiet, "clipped": clipped}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
