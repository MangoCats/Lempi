#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Segment a disc-at-once capture into its tracks `[SPEC-SA-070]`, `[SPEC024]`.

A DAO file is a whole side or disc in one recording. Ingested whole it becomes
one 160-minute "track": useless for rotation, and its flavor is the average of
forty songs, which describes none of them.

Inherited from McRhythm's `[AFS-SIL-010..040]`, with one correction its
document does not make. McRhythm places a boundary "at the midpoint of each
silent period". The 188 files already segmented in this library do not do
that -- their passages leave the silence OUT, ending where the music ends and
resuming where the next begins. A passage is the audible content, and the gap
between two of them is the gap between two songs. Matching the existing
convention is what makes the result comparable with 2,676 hand-checked
boundaries rather than merely plausible.

    python tools/segment_dao.py <db> --validate [--limit N]                against known files
    python tools/segment_dao.py <db> --file <path>                        propose, print only
    python tools/segment_dao.py <db> --file <path> --expect N              count-driven, grid only
    python tools/segment_dao.py <db> --file <path> --expect 245,198,312    full cascade, print only
    python tools/segment_dao.py <db> --file <path> --commit                propose, identify, write
    python tools/segment_dao.py <db> --file <path> --expect 245,198,312 --commit --json

`--commit` requires the file to already be in the library -- run
`tools/ingest_folder.py` on its folder first `[SPEC-SA-070]`, `[SPEC-SA-110]`.
It replaces that whole-file placeholder with one identified `radio`/`album`
pair per track, the same album/radio duality `ingest_folder.py` now writes
by default for a single-track file `[GDE-BMK-030]`. Without `--expect`, both
the preview and `--commit` need an AcoustID key (`secrets/acoustid.key` or
`ACOUSTID_KEY`) -- `propose()`'s own threshold choice is identification
rate, so even looking has to look things up.

`--expect` as a bare count runs Stage 2's grid search alone -- today's
behaviour, unchanged. As a comma-separated list of expected per-track
durations in seconds, it drives the full cascade `[SPEC-SA-115..121]`: grid
search, then DP assembly or extra-track merging if it over-segmented, or an
RMS quiet-spot fallback if silence detection found nothing usable at all.
Stage 3/4 need durations to score against, not only a count, so a bare
integer skips them rather than guessing at values it was not given
`[GOV-SRC-040]`.
"""

import argparse
import json
import os
import re
import subprocess
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import time

# `[AFS-SIL-020]` gives one threshold per source medium, and measurement says
# that is not enough. These are MP3 rips: lossy encoding leaves noise where the
# CD had digital silence, so -80 dB finds almost nothing -- 34 boundaries where
# 298 were known. Worse, the right value is not a property of the medium but of
# the individual rip. Across 14 known files the winner ranged from -70 dB to
# -30 dB, and no single value came close on all of them.
#
# So the threshold is SWEPT and chosen by the count it produces. That needs an
# expected count to aim at, which is exactly what `[AFS-MB-030]` provides: the
# track count of the MusicBrainz release. Given one, this found the known
# segmentation of 14 of 14 files exactly.
THRESHOLDS = {"cd": -80, "vinyl": -60, "cassette-dolby": -70,
              "cassette": -50, "other": -60}

# Coarse to fine; -70 wins most often, so it is first and cheap runs stop early.
SWEEP = (-70, -60, -50, -45, -40, -35, -30, -25, -80)
MIN_SILENCE_S = 0.5          # `[AFS-SIL-020]`

# Below this a "track" is an artefact -- a lead-in tick, applause between
# movements -- not something to put in rotation.
MIN_TRACK_S = 20.0

# ------------------------------------------------------ cascade `[SPEC024]` --
# Stage 2's own grid `[SPEC-SA-116]`: 15 thresholds x 12 durations = 180
# combinations, McRhythm's own bounds, not yet re-derived against this
# library `[GOV-SRC-020]`. Kept coarse-to-fine only in the trivial sense that
# an exact match still exits the search early; McRhythm's own "run27
# frequency" reordering is corpus-specific to its own 2024 test set and is
# not reproduced here.
def _linspace(lo: float, hi: float, n: int, digits: int) -> list[float]:
    if n <= 1:
        return [round(lo, digits)]
    step = (hi - lo) / (n - 1)
    return [round(lo + step * i, digits) for i in range(n)]


GRID_THRESHOLDS_DB = _linspace(-80.0, -30.0, 15, 0)
GRID_MIN_SILENCE_S = _linspace(0.1, 3.0, 12, 2)

# `[SPEC-SA-116]`'s floor -- lowered by McRhythm from 0.80 "to reduce false
# negatives"; carried forward as a starting point, not a re-derived figure.
STAGE2_MIN_MATCH_PCT = 0.65

# `[SPEC-SA-121]` -- past this many pair-merges the mismatch is too large for
# merging to be the right fix.
MAX_MERGES = 3

# `[SPEC-SA-120]` -- how far either side of an expected boundary position the
# RMS fallback searches for the true quiet spot.
RMS_SEARCH_S = 5.0


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def silences(path: str, threshold_db: int, min_s: float) -> list[tuple[float, float]]:
    """Every silent span, from ffmpeg's own detector.

    One decode of the file, which is the expensive part; the thresholds are
    cheap to vary afterwards only by decoding again, so callers that sweep
    should expect to pay per sweep.
    """
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
         "-af", f"silencedetect=noise={threshold_db}dB:d={min_s}",
         "-f", "null", "-"],
        capture_output=True, text=True, errors="replace")
    out = []
    start = None
    for m in re.finditer(r"silence_(start|end): (-?[\d.]+)", r.stderr):
        kind, at = m.group(1), float(m.group(2))
        if kind == "start":
            start = at
        elif start is not None:
            out.append((start, at))
            start = None
    return out


def duration(path: str) -> float | None:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def audible_spans_from_silences(sils: list[tuple[float, float]], total: float,
                                 min_track_s: float = MIN_TRACK_S):
    """Audible spans, in seconds, from a list of silent spans -- the silence
    between two of them belongs to neither. Shared by `spans_at` (ffmpeg's
    own `silencedetect`) and `spans_from_profile` (a decoded-once RMS
    profile, `[SPEC-SA-117]`), so the two paths can never disagree about
    what a silence list means."""
    spans, at = [], 0.0
    for s, e in sils:
        if s - at >= min_track_s:
            spans.append((at, s))
        at = e
    if total - at >= min_track_s:
        spans.append((at, total))
    return spans


def spans_at(path: str, threshold_db: int, total: float,
             min_track_s: float = MIN_TRACK_S):
    """Audible spans, in seconds. The silence between them belongs to neither."""
    return audible_spans_from_silences(
        silences(path, threshold_db, MIN_SILENCE_S), total, min_track_s)


def segment_with_threshold(path: str, medium: str = "cd", min_track_s: float = MIN_TRACK_S,
                            expect: int | None = None):
    """Tracks in a capture, and the threshold that found them.

    With `expect` -- the release's track count -- the threshold is swept and
    the one hitting that count wins, stopping at the first exact hit. Without
    it, the medium's single default is used and the answer is a guess: that is
    the mode that found 34 boundaries where 298 were known.

    Returns `(threshold_db, spans)`, or `None` if the file would not decode.
    The threshold travels with the spans because `--commit` needs it for
    `boundary_src` `[SPEC-SA-110]`; `segment()` below drops it for callers
    that only ever wanted the spans.
    """
    total = duration(path)
    if total is None:
        return None
    if expect is None:
        db = THRESHOLDS.get(medium, -60)
        return db, spans_at(path, db, total, min_track_s)
    best = None
    for db in SWEEP:
        spans = spans_at(path, db, total, min_track_s)
        err = abs(len(spans) - expect)
        if best is None or err < best[0]:
            best = (err, db, spans)
        if err == 0:
            break
    return best[1], best[2]


def segment(path: str, medium: str = "cd", min_track_s: float = MIN_TRACK_S,
            expect: int | None = None):
    """Tracks in a capture -- spans only. See `segment_with_threshold` for
    the threshold alongside them, which `validate()` below has no use for
    but `--commit` does.
    """
    result = segment_with_threshold(path, medium, min_track_s, expect)
    return None if result is None else result[1]


# --------------------------------------------------- cascade: Stage 2 `[SPEC024]`
#
# One decode into a windowed RMS profile, then 180 cheap re-filters against
# it instead of 180 separate `silencedetect` subprocesses `[SPEC-SA-117]`,
# `[GDE-FBD-010]`. Reuses `analyze_amplitude`'s own decode/RMS machinery
# rather than a second copy of it -- imported lazily so a caller that never
# touches the cascade (the plain `--file`/`--validate` paths above) pays
# nothing for numpy.

# Fine enough to resolve the grid's own tightest candidate, 0.1s.
PROFILE_WINDOW_MS = 20


def _amp():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import analyze_amplitude  # noqa: E402
    return analyze_amplitude


def _orphans():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import passage_orphans  # noqa: E402
    return passage_orphans


def db_profile(path: str, window_ms: int = PROFILE_WINDOW_MS, sample_rate: int = 44100):
    """One decode, one windowed RMS envelope. `None` if the file would not
    decode. Returns `(envelope, window_ms)` -- the window travels with the
    envelope because every reader needs it to convert an index back to a
    time.
    """
    amp = _amp()
    samples = amp.decode_pcm(path, 0, 0, sample_rate)
    if samples is None:
        return None
    return amp.rms_envelope(samples, sample_rate, window_ms), window_ms


def silences_from_profile(envelope, window_ms: int, threshold_db: float,
                           min_silence_s: float) -> list[tuple[float, float]]:
    """What ffmpeg's `silencedetect` would report, read instead off an
    already-decoded profile. An approximation of ffmpeg's own running
    filter over fixed non-overlapping windows, not byte-identical to it --
    calibrated the same way `SWEEP`'s own thresholds already are, against
    the library's own known segmentations, not asserted to match exactly.
    """
    amp = _amp()
    linear = amp.db_to_linear(-threshold_db)
    window_s = window_ms / 1000.0
    min_windows = max(1, round(min_silence_s / window_s))
    is_silent = envelope < linear
    out = []
    run_start = None
    for i, silent in enumerate(is_silent):
        if silent:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None and i - run_start >= min_windows:
                out.append((run_start * window_s, i * window_s))
            run_start = None
    if run_start is not None and len(is_silent) - run_start >= min_windows:
        out.append((run_start * window_s, len(is_silent) * window_s))
    return out


def spans_from_profile(envelope, window_ms: int, threshold_db: float, min_silence_s: float,
                        total_s: float, min_track_s: float = MIN_TRACK_S):
    sils = silences_from_profile(envelope, window_ms, threshold_db, min_silence_s)
    return audible_spans_from_silences(sils, total_s, min_track_s)


def grid_search_profile(envelope, window_ms: int, total_s: float, expected_count: int,
                         min_track_s: float = MIN_TRACK_S,
                         thresholds=GRID_THRESHOLDS_DB, durations=GRID_MIN_SILENCE_S,
                         min_match_pct: float = STAGE2_MIN_MATCH_PCT):
    """Stage 2 `[SPEC-SA-116]`: every (threshold, min-silence) combination in
    the grid, against the profile, until one hits the expected count
    exactly.

    Returns `(spans, threshold_db, min_silence_s, match_pct, outcome, tested)`.
    `outcome` is one of `'exact'` (accept, cascade ends here), `'close'`
    (within +-1, Stage 3 should run), `'partial'` (>= `min_match_pct`,
    accept outright) or `'none'` (Stage 4/5 should try). `tested` is every
    `(threshold_db, min_silence_s, count)` tried, for `ingest_decisions`.
    """
    best = None
    tested = []
    for db in thresholds:
        for min_s in durations:
            spans = spans_from_profile(envelope, window_ms, db, min_s, total_s, min_track_s)
            n = len(spans)
            tested.append((db, min_s, n))
            if n == 0:
                continue
            pct = min(n, expected_count) / max(n, expected_count)
            if best is None or pct > best[3]:
                best = (spans, db, min_s, pct)
            if n == expected_count:
                return spans, db, min_s, 1.0, "exact", tested
    if best is None:
        return None, None, None, 0.0, "none", tested
    spans, db, min_s, pct = best
    n = len(spans)
    if abs(n - expected_count) <= 1:
        outcome = "close"
    elif pct >= min_match_pct:
        outcome = "partial"
    else:
        outcome = "none"
    return spans, db, min_s, pct, outcome, tested


# --------------------------------------------------- cascade: Stage 3 `[SPEC024]`

def assemble_boundaries_dp(spans: list[tuple[float, float]],
                            expected_durations: list[float]) -> list[tuple[float, float]]:
    """Stage 3 `[SPEC-SA-119]`: partition N over-detected spans into K
    contiguous groups, one per expected track, minimizing the summed
    absolute duration error -- O(N^2 K). A group's span is (start of its
    first member, end of its last); the silences *inside* a group are
    folded away, kept only where two different expected tracks still meet
    at that boundary. Falls back to the input unchanged if there are fewer
    spans than expected tracks, which Stage 3 was never meant to resolve.
    """
    n, k = len(spans), len(expected_durations)
    if k == 0 or n < k:
        return spans

    def group_duration(j: int, i: int) -> float:
        return spans[i - 1][1] - spans[j][0]

    INF = float("inf")
    dp = [[INF] * (k + 1) for _ in range(n + 1)]
    choice = [[-1] * (k + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for t in range(1, k + 1):
        for i in range(t, n - (k - t) + 1):
            best_cost, best_j = INF, -1
            for j in range(t - 1, i):
                if dp[j][t - 1] == INF:
                    continue
                cost = dp[j][t - 1] + abs(group_duration(j, i) - expected_durations[t - 1])
                if cost < best_cost:
                    best_cost, best_j = cost, j
            dp[i][t] = best_cost
            choice[i][t] = best_j
    if dp[n][k] == INF:
        return spans
    groups = []
    i, t = n, k
    while t > 0:
        j = choice[i][t]
        groups.append((j, i))
        i, t = j, t - 1
    groups.reverse()
    return [(spans[j][0], spans[i - 1][1]) for j, i in groups]


# --------------------------------------------------- cascade: Stage 4 `[SPEC024]`

def rms_window_ms(min_expected_duration_s: float) -> int:
    """`[SPEC-SA-120]`'s adaptive window -- finer where the expected gap
    between boundaries is tighter, so the search does not blur past it."""
    if min_expected_duration_s <= 0.3:
        return 25
    if min_expected_duration_s <= 0.6:
        return 50
    return 100


def rms_quiet_spot(envelope, window_ms: int, expected_pos_s: float,
                    search_s: float = RMS_SEARCH_S) -> float:
    """Stage 4 `[SPEC-SA-120]`: the RMS-minimum position within `search_s`
    of `expected_pos_s`, read off an already-decoded profile. Falls back to
    the expected position unchanged if the envelope is empty or the search
    window lands entirely outside it.
    """
    if len(envelope) == 0:
        return expected_pos_s
    window_s = window_ms / 1000.0
    center = round(expected_pos_s / window_s)
    span = max(1, round(search_s / window_s))
    lo, hi = max(0, center - span), min(len(envelope), center + span + 1)
    if lo >= hi:
        return expected_pos_s
    local = envelope[lo:hi]
    best = 0
    for idx in range(1, len(local)):
        if local[idx] < local[best]:
            best = idx
    return (lo + best) * window_s


# --------------------------------------------------- cascade: Stage 5 `[SPEC024]`

def merge_extra_tracks(spans: list[tuple[float, float]], expected_count: int,
                        max_merges: int = MAX_MERGES) -> list[tuple[float, float]]:
    """Stage 5 `[SPEC-SA-121]`: keep the first K-1 detected spans intact;
    merge the remaining N-K+1 into one final track. Capped at `max_merges`
    pair-merges -- past that the mismatch is too large for merging to be
    the right fix, and the input is returned unchanged so the caller can
    fall through to the RMS fallback instead.
    """
    n, k = len(spans), expected_count
    if k <= 0 or n <= k:
        return spans
    extra = n - k + 1
    if extra - 1 > max_merges:
        return spans
    kept = spans[: k - 1]
    return kept + [(spans[k - 1][0], spans[-1][1])]


# --------------------------------------------------- cascade: orchestration `[SPEC024]`

def segment_cascade(path: str, expected_durations: list[float],
                     min_track_s: float = MIN_TRACK_S) -> dict | None:
    """The full cascade `[SPEC-SA-115..121]`: grid search, then whichever of
    DP assembly, extra-track merging or the RMS fallback the grid result
    calls for. `None` if the file would not decode.

    Returns `{"spans", "stage", "confidence", "threshold_db",
    "min_silence_s", "match_pct", "tested"}` -- `stage` is `'grid'`,
    `'dp'`, `'merge'` or `'rms'`, and everything here is what
    `commit_segments` writes to `ingest_decisions` `[SPEC-SA-123]`.
    """
    total = duration(path)
    if total is None:
        return None
    expected_count = len(expected_durations)
    profile = db_profile(path)
    if profile is None:
        return None
    envelope, window_ms = profile
    spans, db, min_s, pct, outcome, tested = grid_search_profile(
        envelope, window_ms, total, expected_count, min_track_s)

    if outcome == "exact":
        return {"spans": spans, "stage": "grid", "confidence": 1.0,
                "threshold_db": db, "min_silence_s": min_s,
                "match_pct": 1.0, "tested": tested}

    if outcome == "close" and spans is not None:
        assembled = assemble_boundaries_dp(spans, expected_durations)
        if len(assembled) == expected_count:
            return {"spans": assembled, "stage": "dp", "confidence": 1.0,
                    "threshold_db": db, "min_silence_s": min_s,
                    "match_pct": pct, "tested": tested}

    if spans is not None and len(spans) > expected_count:
        merged = merge_extra_tracks(spans, expected_count)
        if len(merged) == expected_count:
            return {"spans": merged, "stage": "merge", "confidence": 0.9,
                    "threshold_db": db, "min_silence_s": min_s,
                    "match_pct": pct, "tested": tested}

    if outcome == "partial" and spans is not None:
        return {"spans": spans, "stage": "grid", "confidence": pct,
                "threshold_db": db, "min_silence_s": min_s,
                "match_pct": pct, "tested": tested}

    # Nothing above resolved it -- Stage 4, the RMS fallback `[SPEC-SA-120]`.
    win_ms = rms_window_ms(min(expected_durations)) if expected_durations else PROFILE_WINDOW_MS
    fine_envelope, fine_window_ms = envelope, window_ms
    if win_ms != window_ms:
        fine = db_profile(path, window_ms=win_ms)
        if fine is not None:
            fine_envelope, fine_window_ms = fine

    positions, acc = [], 0.0
    for d in expected_durations[:-1]:
        acc += d
        positions.append(acc)
    bounds = [rms_quiet_spot(fine_envelope, fine_window_ms, p) for p in positions]

    rms_spans, prev = [], 0.0
    for b in bounds + [total]:
        rms_spans.append((prev, b))
        prev = b
    return {"spans": rms_spans, "stage": "rms", "confidence": 0.7,
            "threshold_db": None, "min_silence_s": None,
            "match_pct": pct, "tested": tested}


# ------------------------------------------------------------- identification

def _best_recording(path: str, start: float, end: float, key: str) -> dict | None:
    """The AcoustID `recordings` entry that best matches this span, as the
    raw structured record (`id`, `title`, `artists`), or `None`.

    Shared by `identify()` (a display string, for the threshold sweep and the
    plain preview) and `identify_recording()` (`--commit`'s own structured
    need `[SPEC-SA-110]`) -- one fingerprint-and-lookup, two shapes of
    answer, so the two can never disagree about what a lookup returned.
    """
    import json
    import urllib.error
    import urllib.parse
    import urllib.request
    fp = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.2f}",
         "-t", f"{min(120.0, end - start):.2f}", "-i", path,
         "-f", "chromaprint", "-fp_format", "base64", "-"],
        capture_output=True).stdout.decode("ascii", "ignore").strip()
    if not fp:
        return None
    q = urllib.parse.urlencode({"client": key, "duration": str(int(end - start)),
                                "fingerprint": fp, "meta": "recordings"})
    req = urllib.request.Request("https://api.acoustid.org/v2/lookup", data=q.encode(),
                                 headers={"User-Agent": "Lempi/0.1"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=45))
    except Exception:                                     # noqa: BLE001
        return None
    for r in d.get("results") or []:
        for rec in r.get("recordings") or []:
            if rec.get("title"):
                return rec
    return None


def identify(path: str, start: float, end: float, key: str):
    """What AcoustID calls the audio between two marks, or `None`.

    The acceptance test that generalises `[AFS-MB-030]`. Choosing a threshold
    by track count needs the count to be right, and a pressing that differs
    from MusicBrainz by two or three tracks -- which is the normal state of a
    special edition -- can never satisfy it. Whether a segment *identifies*
    asks nothing about the total: a boundary in the wrong place cuts a song in
    half and matches nothing, which is the signal worth having.
    """
    rec = _best_recording(path, start, end, key)
    if rec is None:
        return None
    artists = ", ".join(a.get("name", "") for a in rec.get("artists") or [])
    return f"{rec['title']} — {artists}" if artists else rec["title"]


def identify_recording(path: str, start: float, end: float, key: str) -> dict | None:
    """Structured identification for `--commit` `[SPEC-SA-110]`: the
    recording's own mbid, title, and every credited artist's `(mbid, name)`
    -- everything `recordings`/`recording_artists`/`passage_recordings` need,
    not just `identify()`'s display string. `None` for the same "nothing
    matched" case `identify()` already returns for.
    """
    rec = _best_recording(path, start, end, key)
    if rec is None:
        return None
    return {
        "mbid": rec["id"],
        "title": rec["title"],
        "artists": [(a["id"], a.get("name") or "?")
                    for a in rec.get("artists") or [] if a.get("id")],
    }


def propose(path: str, key: str, samples: int = 8, grid=SWEEP):
    """The threshold whose segments identify best, and its boundaries."""
    total = duration(path)
    if total is None:
        return None
    best = None
    for db in grid:
        spans = spans_at(path, db, total)
        if not spans:
            continue
        step = max(1, len(spans) // samples)
        picks = list(range(0, len(spans), step))[:samples]
        hits = sum(1 for i in picks if identify(path, *spans[i], key) is not None)
        rate = hits / len(picks)
        say(f"    {db:>4} dB: {len(spans):3d} segments, {hits}/{len(picks)} identified")
        if best is None or rate > best[0]:
            best = (rate, db, spans)
        if rate == 1.0:
            break
    return best


# ------------------------------------------------------------------ validate

def validate(db: str, limit: int, medium: str, tolerance: float, cascade: bool = False) -> int:
    """Run the detector over files whose segmentation is already known.

    The only honest way to find out whether this works. 188 files, 2,676
    boundaries, none of them produced by this code.

    `cascade=False` (the default) reproduces the original threshold-sweep
    baseline exactly, for comparison. `cascade=True` runs the full cascade
    `[SPEC-SA-115..121]`, with the known passages' own durations standing in
    for what a real MusicBrainz match would supply -- the only honest way to
    measure whether DP assembly, the RMS fallback and extra-track merging
    actually improve on Stage 2 alone, per `[GOV-SRC-020]`.
    """
    import sqlite3
    # Catalogue-only, read-only.
    c = lempi_db.connect(db, lempi_db.ROLE_LIBRARY)
    files = c.execute(
        """SELECT f.file_id, f.path, COUNT(*) n FROM passages p JOIN files f USING(file_id)
            WHERE p.kind='radio' GROUP BY f.file_id HAVING n>1 ORDER BY n DESC""").fetchall()
    files = [f for f in files if os.path.exists(f[1])]
    if limit:
        files = files[:limit]

    say(f"{len(files)} already-segmented file(s); "
        f"{'full cascade' if cascade else 'threshold swept'} per file\n")
    exact = 0
    total_known = total_found = matched = 0
    stage_counts: dict[str, int] = {}
    off = []
    drift = []
    for i, (fid, path, n) in enumerate(files, 1):
        known = [(s / 1000.0, e / 1000.0) for s, e in c.execute(
            "SELECT start_ms,end_ms FROM passages WHERE file_id=?1 AND kind='radio' "
            "ORDER BY start_ms", (fid,))]
        if cascade:
            durations = [e - s for s, e in known]
            result = segment_cascade(path, durations)
            found = None if result is None else result["spans"]
            if result is not None:
                stage_counts[result["stage"]] = stage_counts.get(result["stage"], 0) + 1
        else:
            # The known count stands in for the release's track count, which
            # is what `[AFS-MB-030]` supplies in the real pipeline.
            found = segment(path, medium, expect=len(known))
        if found is None:
            say(f"  {os.path.basename(path)[:44]}: would not decode")
            continue
        total_known += len(known)
        total_found += len(found)
        if len(found) == len(known):
            exact += 1
        # A found start counts as matching a known start within tolerance.
        used = set()
        for ks, _ in known:
            for j, (fs, _) in enumerate(found):
                if j not in used and abs(fs - ks) <= tolerance:
                    used.add(j)
                    matched += 1
                    break
        if len(found) != len(known):
            off.append((os.path.basename(path), len(known), len(found)))
        else:
            # Where the count is right, how close are the boundaries?
            drift.extend(abs(f[0]-k[0]) for k, f in zip(known, found))
        if i % 25 == 0:
            say(f"  {i}/{len(files)}  exact track count on {exact}")

    say(f"\n  files with the exact track count : {exact}/{len(files)}  "
        f"({exact/max(len(files),1):.0%})")
    say(f"  boundaries known / found         : {total_known} / {total_found}")
    say(f"  starts matched within {tolerance:.1f}s        : {matched} "
        f"({matched/max(total_known,1):.0%})")
    if cascade and stage_counts:
        say(f"  resolved by: " + ", ".join(f"{k}={v}" for k, v in sorted(stage_counts.items())))
    if off:
        say(f"\n  worst count mismatches (of {len(off)}):")
        for name, k, f in sorted(off, key=lambda x: -abs(x[1]-x[2]))[:8]:
            say(f"    known {k:3d}  found {f:3d}   {name[:46]}")
    return 0


# --------------------------------------------------------------------- commit

def commit_segments(conn, file_id: int, audio_md5: str, path: str,
                     spans: list[tuple[float, float]], decision: dict, key: str) -> dict:
    """Replace `file_id`'s passages with a real segmentation `[SPEC-SA-070]`,
    one identified `radio`/`album` pair per track `[SPEC-SA-110]`,
    `[GDE-BMK-030]`.

    Replaces rather than adds to what is already there: the ordinary
    starting point for a DAO capture is `tools/ingest_folder.py`'s own
    whole-file `radio`/`album` pair, a deliberate one-track-per-file
    placeholder for exactly this shape of file, not a rival segmentation
    worth keeping once a real one exists.

    Each span is identified independently, the same convention this
    library's own 133-of-136 already-segmented tracks were produced under: a
    confident AcoustID match gets the real MusicBrainz recording (and every
    credited artist); anything else gets `local:audio:<md5>:<start_ms>`, the
    same placeholder shape `local:audio:<md5>` alone already means for a
    single-track file, extended with the one thing that makes it unique
    inside a DAO capture -- where in the file it starts.

    `decision` carries which stage resolved it, at what confidence, and
    what the grid tried and rejected -- `boundary_src` names the algorithm
    and version only `[SPEC-SA-122]`; the parameters that produced this
    particular result go to `ingest_decisions` instead `[SPEC-SA-123]`,
    following `choose_release.py`'s own established shape.
    """
    old = [r[0] for r in conn.execute(
        "SELECT passage_id FROM passages WHERE file_id=?1", (file_id,))]
    if old:
        conn.execute(
            f"DELETE FROM passage_recordings WHERE passage_id IN ({','.join('?' * len(old))})", old)
        conn.execute("DELETE FROM passages WHERE file_id=?1", (file_id,))
        # The cascade that NULLs listener references to these passages is a
        # FOREIGN KEY, and a FOREIGN KEY does not cross an ATTACH boundary
        # `[IMPL009 §7.7]`. In one file this finds nothing, because the
        # cascade has already fired; on a split installation it is the only
        # thing that does the work at all. The same call, either shape.
        _orphans().clear(conn)

    boundary_src = "computed:segment-cascade@v1"
    identified = unidentified = 0
    for start_s, end_s in spans:
        start_ms, end_ms = round(start_s * 1000), round(end_s * 1000)
        rec = identify_recording(path, start_s, end_s, key)
        if rec is not None:
            mbid, source = rec["mbid"], "segment:acoustid"
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
            mbid, source = f"local:audio:{audio_md5}:{start_ms}", "segment:unidentified"
            conn.execute(
                "INSERT OR IGNORE INTO recordings (mbid,title,length_ms,source) "
                "VALUES (?1,?2,?3,?4)",
                (mbid, f"unidentified segment at {round(start_ms / 60000)} min",
                 end_ms - start_ms, source))
            unidentified += 1

        # Both kinds, one identification `[SPEC-SA-110]` -- see
        # `ingest_folder.py`'s own `--kind both` for why `album`'s lead/gain
        # are 0, not NULL: its segue points equal its own hard boundaries
        # `[GDE-BMK-030]`, permanently, not a value awaiting analysis.
        for kind in ("radio", "album"):
            if kind == "album":
                pid = conn.execute(
                    "INSERT INTO passages (file_id,kind,start_ms,end_ms,"
                    "lead_in_ms,lead_out_ms,gain_db,boundary_src) "
                    "VALUES (?1,?2,?3,?4,0,0,0.0,?5)",
                    (file_id, kind, start_ms, end_ms, boundary_src)).lastrowid
            else:
                pid = conn.execute(
                    "INSERT INTO passages (file_id,kind,start_ms,end_ms,boundary_src) "
                    "VALUES (?1,?2,?3,?4,?5)",
                    (file_id, kind, start_ms, end_ms, boundary_src)).lastrowid
            conn.execute(
                "INSERT INTO passage_recordings (passage_id,mbid,weight,source) "
                "VALUES (?1,?2,1.0,?3)", (pid, mbid, source))

    detail = {
        "expected_count": decision.get("expected_count"),
        "threshold_db": decision.get("threshold_db"),
        "min_silence_s": decision.get("min_silence_s"),
        "match_pct": decision.get("match_pct"),
        "tracks_found": len(spans),
        "tested": decision.get("tested"),
    }
    conn.execute(
        "INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
        "VALUES (?1,'segment',?2,?3,?4,?5)",
        (audio_md5, decision.get("stage", "unknown"), decision.get("confidence"),
         json.dumps(detail), int(time.time())))

    return {"tracks": len(spans), "identified": identified,
            "unidentified": unidentified, "replaced": len(old)}


def do_commit(db_path: str, path: str, spans: list[tuple[float, float]], decision: dict) -> int:
    """`--commit`'s own I/O: find the file, get a key, write the transaction.

    Deliberately does not create `files`/`file_tags` rows itself -- that is
    `tools/ingest_folder.py`'s job, and it already ran first for this file to
    exist here at all `[SPEC-SA-070]`. Refusing plainly when it has not, per
    `[SPEC-DF-095]`, beats guessing at tags this tool never read.
    """
    import sqlite3
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ingest_folder  # noqa: E402  -- same audio_md5(), not a second copy of it
    import secret  # noqa: E402

    md5 = ingest_folder.audio_md5(path)
    if md5 is None:
        say("would not decode")
        return 1

    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(db_path, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA foreign_keys = ON")
    row = conn.execute("SELECT file_id FROM files WHERE audio_md5=?1", (md5,)).fetchone()
    if row is None:
        say(f"\nnot in the library yet -- run tools/ingest_folder.py on its folder "
            f"first, then re-run this with --commit")
        return 1
    file_id = row[0]

    key = secret.acoustid_key()  # required=True: --commit always identifies
    conn.execute("BEGIN IMMEDIATE")
    result = commit_segments(conn, file_id, md5, path, spans, decision, key)
    conn.commit()
    say(f"\nreplaced {result['replaced']} old passage(s) with {result['tracks']} track(s), "
        f"{result['tracks']} radio + {result['tracks']} album: "
        f"{result['identified']} identified, {result['unidentified']} not "
        f"(stage: {decision.get('stage', 'unknown')})")
    return 0


def _parse_expect(value: str):
    """`--expect`: a bare count (Stage 2's grid search alone, today's
    behaviour) or a comma-separated list of expected per-track durations in
    seconds (the full cascade `[SPEC-SA-115..121]` -- Stage 3/4 need
    durations to score against, not only a count)."""
    if "," in value:
        return [float(v) for v in value.split(",") if v.strip()]
    return int(value)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--file")
    ap.add_argument("--medium", default="cd", choices=sorted(THRESHOLDS))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tolerance", type=float, default=2.0)
    ap.add_argument("--cascade", action="store_true",
                    help="with --validate, run the full cascade instead of Stage 2 alone")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--json", action="store_true",
                    help="one final JSON summary line, for job wiring")
    # The release's own track count, when it is known `[AFS-MB-030]` -- swept
    # for the threshold hitting that count, no AcoustID lookup needed to
    # *choose* one. As a comma-separated list of durations instead, drives
    # the full cascade rather than the grid search alone. Without it, the
    # threshold is chosen by identification rate instead (`propose()`),
    # which does need a key even just to preview.
    ap.add_argument("--expect", type=_parse_expect)
    args = ap.parse_args()

    if args.validate:
        return validate(args.db, args.limit, args.medium, args.tolerance, args.cascade)
    if args.file:
        decision: dict = {}
        if isinstance(args.expect, list) and args.expect:
            result = segment_cascade(args.file, args.expect)
            if result is None:
                if args.json:
                    say(json.dumps({"ok": False, "error": "would not decode"}))
                else:
                    say("would not decode")
                return 1
            spans = result["spans"]
            threshold_db = result["threshold_db"]
            decision = dict(result, expected_count=len(args.expect))
        elif args.expect:
            found = segment_with_threshold(args.file, args.medium, expect=args.expect)
            if found is None:
                if args.json:
                    say(json.dumps({"ok": False, "error": "would not decode"}))
                else:
                    say("would not decode")
                return 1
            threshold_db, spans = found
            decision = {"stage": "grid", "confidence": None, "threshold_db": threshold_db,
                        "min_silence_s": None, "match_pct": None, "tested": None,
                        "expected_count": args.expect}
        else:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import secret  # noqa: E402
            # A key is required here even for a bare preview: unlike
            # `--expect`, `propose()`'s own threshold choice is identification
            # rate, which has no meaning without looking anything up.
            key = secret.acoustid_key()
            proposed = propose(args.file, key)
            if proposed is None:
                if args.json:
                    say(json.dumps({"ok": False,
                                    "error": "would not decode, or nothing identified"}))
                else:
                    say("would not decode, or nothing identified at any threshold")
                return 1
            rate, threshold_db, spans = proposed
            decision = {"stage": "propose", "confidence": rate, "threshold_db": threshold_db,
                        "min_silence_s": MIN_SILENCE_S, "match_pct": None, "tested": None,
                        "expected_count": None}

        if not args.json:
            say(f"\n{len(spans)} track(s) in {os.path.basename(args.file)} "
                f"({threshold_db} dB, stage: {decision.get('stage')})")
            for i, (s, e) in enumerate(spans, 1):
                say(f"  {i:3d}  {s/60:6.2f}–{e/60:6.2f} min   ({e-s:6.1f}s)")

        if args.commit:
            rc = do_commit(args.db, args.file, spans, decision)
            if args.json:
                say(json.dumps({"ok": rc == 0, "tracks": len(spans),
                                "stage": decision.get("stage"),
                                "confidence": decision.get("confidence")}))
            return rc
        if args.json:
            say(json.dumps({"ok": True, "tracks": len(spans),
                            "stage": decision.get("stage"),
                            "confidence": decision.get("confidence")}))
        return 0
    say(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
