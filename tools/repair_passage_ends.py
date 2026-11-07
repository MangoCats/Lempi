#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A passage that stops while the music is still playing `[SPEC-SA-112]`.

`repair_durations.py` `[REQ-LIB-145]` corrected `files.duration_ms` from a real
decode and clamped a passage that overran its file. It never extended one that
fell short, so an end set from the old bitrate estimate stayed where it was: on
2026-11-06, 194 files stopped with loud music still to come, by up to 100 s.

A rehearsal unless told otherwise, as `apply_boundary_reviews.py` is.

    python tools/repair_passage_ends.py data/library.db [--plan out.csv]
    python tools/repair_passage_ends.py data/library.db --write
    python tools/repair_passage_ends.py data/library.db --album-timing [--write]

What follows a passage's end decides what happens to it, measured by decoding:

  cut      >=3 s of sound, loud (mean >= -35 dB), after an end that is itself loud
  fade     audible but quiet: a fade the old boundary trimmed
  silence  nothing above -50 dB. Correct as it stands, and left alone.
  gap      sound, then >=2 s of silence, then more: perhaps a hidden track, perhaps
           a click. Said, and left alone: it is a person's to decide.

The two kinds of passage then want different things `[SPEC-SC-040]`:

  radio    ends where the fade gets quiet: the last sound at or above `lead_out_db`
           below full scale, the level `boundary-params.json` already calls a
           lead-out, plus its `end_pad_ms`. Measured before this was adopted: 400
           good radio ends sat at a median of -42 dB, and this rule landed a median
           of 216 ms from them. So it may move an end earlier as well as later. Its
           lead-in and lead-out, measured over the old span, are measured again.
  album    plays back to back with its neighbours as the album does. On a file of
           one track that is the whole file. Between two tracks in one file it is
           the join `split_point` finds, close to silence when there is any.

`manual` is never touched, on either kind `[SPEC-SA-080]`, nor is a passage held
back `[SPEC-HOLD-010]`, with a boundary draft not yet applied, or flagged
`[REQ-VIS-265]`: a flag means a person means to look at it. A cached analysis
of a span nothing plays any more is deleted, not re-keyed `[SPEC021 §5]`, which
leaves `analysis_gaps.py` something to report and `extract_library.py` something to
fill. Each change is recorded in `ingest_decisions` with what it replaced
`[SPEC-SA-085]`. A plan gone stale while the audio was being decoded is refused
whole: every statement names the value it expects to find.

`--album-timing` widens the album half to every album passage in the library, and
leaves every radio passage alone.

**No more than 200 tracks or 400 passages without being told** (the maintainer's
rule, 2026-11-06): that many whose timing changes. The count is the job's, not the
run's: what this tool has already changed counts too, so several small runs cannot
walk past it. `--allow-tracks N` raises it to N tracks and 2N passages for one run,
and is for a person who has been shown the number.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_amplitude as amp   # noqa: E402
import lempi_db                   # noqa: E402

SR = amp.SAMPLE_RATE
WINDOW_MS = amp.RMS_WINDOW_MS
SILENT_DB = -50.0           # below this a tail holds nothing worth hearing
GAP_WINDOWS = 2000 // WINDOW_MS     # this long below it, and what follows is something else
LOUD_DB = -35.0
MIN_CUT_S = 3.0
SPLIT_SILENCE_DB = -60.0    # `end_silence_db`: below this a join is in true silence
MIN_TAIL_MS = 5000
MIN_RADIO_MOVE_MS = 1000    # a smaller move is rounding, not a repair
TOLERANCE_MS = 50           # an album edge this close to the file's is already there
MAX_TRACKS = 200            # the job's total, without --allow-tracks
PASSAGES_PER_TRACK = 2      # a track is an album passage and a radio passage

RADIO_SUFFIX = "+radio-end:quiet"
ALBUM_SUFFIX = "+album-edge:decoded"
SUFFIXES = re.compile(r"(?:\+radio-end:quiet|\+album-edge:decoded)+$")


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def to_db(linear: float) -> float:
    return 20 * math.log10(max(linear, 1e-7))


def envelope_of(path: str) -> tuple[np.ndarray, np.ndarray, int] | None:
    """The whole file's RMS envelope, its peak per window, and its decoded length in
    ms, read as a stream.

    A stream because the library holds a 245-minute file: as one array of samples,
    `rms_envelope` would want 5 GB for it, and it only needs one window at a time.
    """
    window = SR * WINDOW_MS // 1000
    p = subprocess.Popen([amp.FFMPEG, "-nostdin", "-v", "error", "-i", path, "-vn", "-ac", "2", "-ar", str(SR),
                          "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    env, peak, total, spare = [], [], 0, b""
    while True:
        chunk = p.stdout.read(window * 4 * 600)
        if not chunk:
            break
        data = spare + chunk
        usable = len(data) // (window * 4) * (window * 4)
        spare = data[usable:]
        total += usable // 4
        if usable:
            x = np.frombuffer(data[:usable], dtype="<i2").astype(np.float64).reshape(-1, 2) / 32768.0
            mono = x.mean(axis=1).reshape(-1, window)
            env.append(np.sqrt(np.mean(mono ** 2, axis=1)))
            # Per channel: a blip in one side is half as loud in the mix, and it is
            # still what says something follows the silence.
            peak.append(np.max(np.abs(x), axis=1).reshape(-1, window).max(axis=1))
    total += len(spare) // 4
    if p.wait() != 0 or not total:
        return None
    if not env:
        return np.array([]), np.array([]), int(total * 1000 / SR)
    return np.concatenate(env), np.concatenate(peak), int(total * 1000 / SR)


def sound_spans(peak: np.ndarray) -> list[tuple[int, int]]:
    """Stretches of `peak` (window indices) not separated by a silence of GAP_WINDOWS.

    By peak, as ffmpeg's `silencedetect` is: a 0.2 s blip after a silence is what
    marks a gap, and its RMS over a 100 ms window can sit below the floor.
    """
    loud = peak >= 10 ** (SILENT_DB / 20)
    spans, i, start, n = [], 0, 0, len(peak)
    while i < n:
        if loud[i]:
            i += 1
            continue
        j = i
        while j < n and not loud[j]:
            j += 1
        if j - i >= GAP_WINDOWS or j == n:
            if i > start:
                spans.append((start, i))
            start = j
        i = j
    if n > start:
        spans.append((start, n))
    return spans


def rms_db(env: np.ndarray) -> float:
    return to_db(float(np.sqrt(np.mean(env ** 2)))) if len(env) else -math.inf


def classify(env: np.ndarray, end_ms: int, peak: np.ndarray | None = None) -> dict:
    """What follows `end_ms`, in the terms the module's docstring gives."""
    at = int(end_ms // WINDOW_MS)
    tail = env[at:]
    before = env[max(0, at - 5000 // WINDOW_MS):at]
    spans = sound_spans((env if peak is None else peak)[at:])
    sound_s = sum(b - a for a, b in spans) * WINDOW_MS / 1000
    out = {"tail_s": round(len(tail) * WINDOW_MS / 1000, 1), "sound_s": round(sound_s, 1),
           "tail_db": round(rms_db(tail), 1), "before_db": round(rms_db(before), 1)}
    if not spans:
        return {**out, "kind": "silence"}
    if len(spans) > 1 or spans[0][0] >= GAP_WINDOWS:
        return {**out, "kind": "gap"}
    if out["tail_db"] >= LOUD_DB and sound_s >= MIN_CUT_S and out["before_db"] >= LOUD_DB:
        return {**out, "kind": "cut"}
    return {**out, "kind": "fade"}


def radio_shape(path: str, start_ms: int, decoded_ms: int) -> dict | None:
    """Where a radio passage starting at `start_ms` should end, and its leads there."""
    mono = amp.decode_pcm(path, 0, 0, SR, timeout=900.0)
    if mono is None:
        return None
    p = {**amp.RADIO, "trim_start": False, "trim_end": True, "end_silence_db": amp.RADIO["lead_out_db"]}
    return amp.detect(mono, SR, 0, {"lo_ms": start_ms, "hi_ms": decoded_ms, "lo_is_file_edge": True,
                                    "hi_is_file_edge": True}, p)


def split_point(env: np.ndarray, a_ms: int, b_ms: int) -> int:
    """Where to join two tracks that sit `a_ms`..`b_ms` apart in one file.

    The end of the longest true silence in the gap, so the first track keeps what
    rings on and the second starts where its sound does. With no silence to find,
    the quietest window in the gap. Either way both passages meet at one point, and
    no audio is lost from between them.
    """
    a, b = int(a_ms // WINDOW_MS), min(int(math.ceil(b_ms / WINDOW_MS)), len(env))
    if b <= a:
        return b_ms
    gap = env[a:b]
    quiet = gap <= 10 ** (SPLIT_SILENCE_DB / 20)
    best_end, best_len, i, n = None, 0, 0, len(gap)
    while i < n:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < n and quiet[j]:
            j += 1
        if j - i > best_len:
            best_len, best_end = j - i, j
        i = j
    if best_end is not None:
        return max(a_ms, min(b_ms, (a + best_end) * WINDOW_MS))
    return max(a_ms, min(b_ms, (a + int(np.argmin(gap))) * WINDOW_MS))


def source_with(src: str, suffix: str) -> str:
    return SUFFIXES.sub("", src) + suffix


def blocked(p: dict, drafts: set) -> str | None:
    if p["boundary_src"] == "manual":
        return "set by hand"
    if p["director_hold"]:
        return "held back"
    if p.get("flagged"):
        return "flagged for a person"
    if p["passage_id"] in drafts:
        return "boundary draft pending"
    return None


def change(p: dict, why: str, **new) -> dict:
    return {"passage_id": p["passage_id"], "kind": p["kind"], "audio_md5": p["audio_md5"], "path": p["path"], "why": why,
            "old": {k: p[k] for k in new}, "new": new, "span": (p["start_ms"], p["end_ms"])}


def plan_ends(f: dict, passages: list, drafts: set, min_tail_ms: int, kinds: tuple = ("cut", "fade")) -> dict:
    """One file's answer: what follows its last album passage, and what to do."""
    albums = sorted((p for p in passages if p["kind"] == "album"), key=lambda p: p["start_ms"])
    radios = sorted((p for p in passages if p["kind"] == "radio"), key=lambda p: p["start_ms"])
    last = albums[-1]
    if any(p.get("flagged") for p in passages):
        # The whole file: the two kinds' ends are one decision, and it is a person's.
        return {"file": f, "kind": "flagged", "changes": [], "skipped": []}
    got = envelope_of(f["path"])
    if got is None:
        return {"file": f, "kind": "unreadable", "changes": [], "skipped": []}
    env, peak, decoded = got
    seen = classify(env, last["end_ms"], peak)
    out = {"file": f, "decoded_ms": decoded, "changes": [], "skipped": [], **seen}
    # Measured, not trusted: the row said there was a tail, the decode may not.
    if decoded - last["end_ms"] < min_tail_ms or seen["kind"] not in kinds:
        return out

    why = blocked(last, drafts)
    if why:
        out["skipped"].append(("album", why))
    else:
        new = {"end_ms": decoded, "boundary_src": source_with(last["boundary_src"], ALBUM_SUFFIX)}
        if len(albums) == 1 and last["start_ms"] != 0:
            new["start_ms"] = 0
        out["changes"].append(change(last, seen["kind"], **new))

    if radios:
        r = radios[-1]
        why = blocked(r, drafts)
        if why:
            out["skipped"].append(("radio", why))
        else:
            shape = radio_shape(f["path"], r["start_ms"], decoded)
            if shape is None:
                out["skipped"].append(("radio", "could not decode"))
            elif abs(shape["end_ms"] - r["end_ms"]) >= MIN_RADIO_MOVE_MS:
                out["changes"].append(change(r, seen["kind"], end_ms=shape["end_ms"], lead_in_ms=shape["lead_in_ms"],
                                             lead_out_ms=shape["lead_out_ms"],
                                             boundary_src=source_with(r["boundary_src"], RADIO_SUFFIX)))
    return out


def plan_album_timing(f: dict, passages: list, drafts: set) -> dict:
    """Every album passage of one file, reaching the file's edges and its neighbours."""
    albums = sorted((p for p in passages if p["kind"] == "album"), key=lambda p: p["start_ms"])
    out = {"file": f, "changes": [], "skipped": [], "kind": "album-timing"}
    if len(albums) == 1:
        p = albums[0]
        decoded = f["duration_ms"]      # already decoded, and no join to look for
        if p["start_ms"] == 0 and abs(p["end_ms"] - decoded) <= TOLERANCE_MS:
            return out
        why = blocked(p, drafts)
        if why:
            out["skipped"].append(("album", why))
            return out
        new = {"end_ms": decoded, "boundary_src": source_with(p["boundary_src"], ALBUM_SUFFIX)}
        if p["start_ms"] != 0:
            new["start_ms"] = 0
        out["changes"].append(change(p, "album timing", **new))
        return out

    got = envelope_of(f["path"])
    if got is None:
        return {**out, "kind": "unreadable"}
    env, _peak, decoded = got
    starts = {p["passage_id"]: p["start_ms"] for p in albums}
    ends = {p["passage_id"]: p["end_ms"] for p in albums}
    free = {p["passage_id"]: not blocked(p, drafts) for p in albums}
    if free[albums[0]["passage_id"]]:
        starts[albums[0]["passage_id"]] = 0
    if free[albums[-1]["passage_id"]]:
        ends[albums[-1]["passage_id"]] = decoded
    for x, y in zip(albums, albums[1:]):
        gap = y["start_ms"] - x["end_ms"]
        # Overlap is a different fault; a join that already meets has nothing to find.
        if gap <= TOLERANCE_MS or not (free[x["passage_id"]] and free[y["passage_id"]]):
            continue
        ends[x["passage_id"]] = starts[y["passage_id"]] = split_point(env, x["end_ms"], y["start_ms"])
    for p in albums:
        if starts[p["passage_id"]] == p["start_ms"] and ends[p["passage_id"]] == p["end_ms"]:
            continue
        out["changes"].append(change(p, "album timing", start_ms=starts[p["passage_id"]], end_ms=ends[p["passage_id"]],
                                     boundary_src=source_with(p["boundary_src"], ALBUM_SUFFIX)))
    return out


class Stale(Exception):
    pass


def job_size(conn, changes: list) -> tuple[int, int]:
    """Tracks and passages this job has changed or would: earlier runs' and this plan's.

    A track is a file, or on a whole-album file each album passage: so the larger of
    the two counts, since a radio change alone still retimes one track.
    """
    rows = conn.execute("SELECT audio_md5, outcome, detail FROM ingest_decisions WHERE stage = 'passage_ends'").fetchall()
    passages = {json.loads(r[2])["passage_id"] for r in rows} | {c["passage_id"] for c in changes}
    albums = {json.loads(r[2])["passage_id"] for r in rows if r[1].startswith("album")} | {c["passage_id"] for c in changes if c["kind"] == "album"}
    files = {r[0] for r in rows} | {c["audio_md5"] for c in changes}
    return max(len(albums), len(files)), len(passages)


def apply(conn, changes: list) -> int:
    """One transaction. Each change names what it expects; any miss refuses them all."""
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for c in changes:
            sets = ", ".join(f"{k} = ?" for k in c["new"])
            guard = " AND ".join("start_ms = ?" if k == "start_ms" else "end_ms = ?" for k in ("start_ms", "end_ms"))
            n = conn.execute(f"UPDATE passages SET {sets} WHERE passage_id = ? AND {guard}",
                             (*c["new"].values(), c["passage_id"], *c["span"])).rowcount
            if n != 1:
                raise Stale(f"passage {c['passage_id']} is no longer {c['span']}: {c['path']}")
            conn.execute("INSERT INTO ingest_decisions (audio_md5, stage, outcome, detail, decided_at) "
                         "VALUES (?,'passage_ends',?,?,?)",
                         (c["audio_md5"], f"{c['kind']} {c['why']}: end {c['span'][1]} -> {c['new'].get('end_ms', c['span'][1])}",
                          json.dumps({"passage_id": c["passage_id"], "old": {"start_ms": c["span"][0], "end_ms": c["span"][1], **c["old"]},
                                      "new": c["new"]}), now))
        gone = 0
        if lempi_db.has_table(conn, "lowlevel_cache"):
            for md5, (s, e) in {(c["audio_md5"], c["span"]) for c in changes}:
                if not conn.execute("SELECT 1 FROM passages p JOIN files f USING (file_id) "
                                    "WHERE f.audio_md5 = ? AND p.start_ms = ? AND p.end_ms = ?", (md5, s, e)).fetchone():
                    gone += conn.execute("DELETE FROM lowlevel_cache WHERE audio_md5 = ? AND start_ms = ? AND end_ms = ?",
                                         (md5, s, e)).rowcount
        conn.execute("COMMIT")
        return gone
    except Exception:
        conn.execute("ROLLBACK")
        raise


def load(conn, min_tail_ms: int, everything: bool):
    conn.row_factory = None
    rows = conn.execute("""SELECT f.file_id, f.path, f.duration_ms, f.audio_md5, p.passage_id, p.kind, p.start_ms, p.end_ms,
                                  p.lead_in_ms, p.lead_out_ms, p.boundary_src, p.director_hold
                             FROM passages p JOIN files f USING (file_id) ORDER BY f.file_id""").fetchall()
    keys = ("file_id", "path", "duration_ms", "audio_md5", "passage_id", "kind", "start_ms", "end_ms",
            "lead_in_ms", "lead_out_ms", "boundary_src", "director_hold")
    files, by = {}, {}
    for r in rows:
        d = dict(zip(keys, r))
        files[d["file_id"]] = {k: d[k] for k in ("file_id", "path", "duration_ms", "audio_md5")}
        by.setdefault(d["file_id"], []).append(d)
    drafts = set()
    if lempi_db.has_table(conn, "boundary_reviews"):
        drafts = {r[0] for r in conn.execute("SELECT passage_id FROM boundary_reviews WHERE applied_at IS NULL")}
    if lempi_db.has_table(conn, "listener_flags"):
        flagged = {r[0] for r in conn.execute("""SELECT pr.passage_id FROM listener_flags x JOIN passage_recordings pr ON pr.mbid = x.subject_id
                                                  WHERE x.subject_kind = 'recording'""")}
        flagged |= {int(r[0]) for r in conn.execute("SELECT subject_id FROM listener_flags WHERE subject_kind = 'passage'")}
        for ps in by.values():
            for p in ps:
                p["flagged"] = p["passage_id"] in flagged
    if not everything:
        keep = {}
        for fid, ps in by.items():
            ends = [p["end_ms"] for p in ps if p["kind"] == "album"]
            if ends and files[fid]["duration_ms"] - max(ends) >= min_tail_ms:
                keep[fid] = ps
        by = keep
    return files, by, drafts


def write_plan(path: str, results: list) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["kind", "passage_id", "why", "old_start_ms", "old_end_ms", "new_start_ms", "new_end_ms", "moved_s",
                    "tail_s", "sound_s", "tail_db", "before_db", "new_boundary_src", "path"])
        for r in results:
            for c in r["changes"]:
                new_end = c["new"].get("end_ms", c["span"][1])
                w.writerow([c["kind"], c["passage_id"], c["why"], c["span"][0], c["span"][1], c["new"].get("start_ms", c["span"][0]), new_end,
                            round((new_end - c["span"][1]) / 1000, 1), r.get("tail_s"), r.get("sound_s"), r.get("tail_db"), r.get("before_db"),
                            c["new"].get("boundary_src"), c["path"]])


def main() -> int:
    ap = argparse.ArgumentParser(description="Repair passages that stop before the music does [SPEC-SA-112].")
    ap.add_argument("library")
    ap.add_argument("--write", action="store_true", help="apply it; without this, nothing is written")
    ap.add_argument("--plan", help="write every proposed change to this CSV")
    ap.add_argument("--min-tail-ms", type=int, default=MIN_TAIL_MS)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    ap.add_argument("--album-timing", action="store_true",
                    help="every album passage in the library, not only those above; radio is left alone")
    ap.add_argument("--only", choices=("cut", "fade"), help="propose only this kind of tail")
    ap.add_argument("--allow-tracks", type=int, default=MAX_TRACKS,
                    help=f"the most tracks this job may have retimed in total, this run's and earlier ones', and twice that in passages (default {MAX_TRACKS})")
    args = ap.parse_args()
    if not amp.FFMPEG:
        say("ffmpeg not found")
        return 2

    conn = lempi_db.connect(args.library, lempi_db.ROLE_LIBRARY, writable=args.write, timeout=60)
    conn.isolation_level = None
    files, by, drafts = load(conn, args.min_tail_ms, args.album_timing)
    say(f"{len(by)} file(s) to measure{'; every album passage' if args.album_timing else f', with a tail of at least {args.min_tail_ms / 1000:.0f} s'}")

    with futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        if args.album_timing:
            results = list(pool.map(lambda fid: plan_album_timing(files[fid], by[fid], drafts), by))
        else:
            wanted = (args.only,) if args.only else ("cut", "fade")
            results = list(pool.map(lambda fid: plan_ends(files[fid], by[fid], drafts, args.min_tail_ms, wanted), by))

    kinds: dict = {}
    for r in results:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    say("what follows the end: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    for r in results:
        if r["kind"] == "flagged":
            say(f"  flagged, a person's: {r['file']['path']}")
        if r["kind"] == "gap":
            say(f"  for a person: {r['file']['path']}  (+{r['sound_s']} s of sound, {r['tail_db']} dB)")
        if r["kind"] == "unreadable":
            say(f"  UNREADABLE: {r['file']['path']}")
    skipped: dict = {}
    for r in results:
        for k, why in r["skipped"]:
            skipped[(k, why)] = skipped.get((k, why), 0) + 1
    if skipped:
        say("left alone: " + ", ".join(f"{k} {why} {v}" for (k, why), v in sorted(skipped.items())))

    changes = [c for r in results for c in r["changes"]]
    album, radio = [c for c in changes if c["kind"] == "album"], [c for c in changes if c["kind"] == "radio"]
    earlier = sum(c["new"].get("end_ms", c["span"][1]) < c["span"][1] for c in radio)
    say(f"proposed: {len(changes)} passage(s) on {len({c['audio_md5'] for c in changes})} file(s): {len(album)} album, "
        f"{len(radio)} radio ({len(radio) - earlier} later, {earlier} earlier)")
    if args.plan:
        write_plan(args.plan, results)
        say(f"plan: {args.plan}")
    tracks, passages = job_size(conn, changes)
    limit = args.allow_tracks * PASSAGES_PER_TRACK
    say(f"this job, with earlier runs': {tracks} tracks, {passages} passages (limit {args.allow_tracks} and {limit})")
    if args.write and changes and (tracks > args.allow_tracks or passages > limit):
        say(f"REFUSED, nothing written: {tracks} tracks and {passages} passages is past {args.allow_tracks} and {limit}. "
            "Permission to go further is a person's; --allow-tracks says they gave it.")
        return 1
    if not args.write:
        say("(rehearsal: nothing written. --write applies it)")
        return 0
    if not changes:
        say("nothing to do")
        return 0
    try:
        gone = apply(conn, changes)
    except Stale as e:
        say(f"REFUSED, nothing written: {e}")
        return 1
    say(f"written: {len(changes)} passage(s), {gone} cached analysis row(s) of a span nothing plays now")
    say(f"quick_check: {conn.execute('PRAGMA quick_check').fetchone()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
