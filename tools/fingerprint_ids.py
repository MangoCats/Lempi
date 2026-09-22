#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check every recording MBID against the audio itself `[REQ-LIB-165]`.

Every recording id in this library arrived the same way -- `source` on all
16,157 rows of `passage_recordings` reads `inherited:mulib` -- so they are all
as good, or as bad, as one migration. `verify_ids.py` compared them against the
file's own tags and found 2.8% plainly wrong and a third disagreeing on title.
But tags cannot settle it: the ids may well have been *derived* from the tags,
in which case agreement proves only that a copy matches its original.

A fingerprint owes nothing to either. Chromaprint reduces the audio to a
summary of how it actually sounds, AcoustID maps that to the recordings other
people have identified the same sound as, and neither has ever seen this
library's metadata. That is genuinely independent evidence, and it is the only
kind available here.

Verdicts, deliberately asymmetric:

  confirmed     the stored id is among the recordings AcoustID returns
  contradicted  AcoustID is confident, and the stored id is not among them
  inconclusive  a match too weak to argue either way
  unmatched     AcoustID does not know this audio; this says nothing
  unreadable    the file would not decode

Confirmation is lenient and contradiction is strict, because the two errors are
not equally costly: a wrongly confirmed id stays as wrong as it already was,
while a wrongly contradicted id sends a person to review something that was
fine. The review queue is only useful if nearly everything in it deserves to be
there.

The library is opened **read-only** and the findings are written to a sidecar
database beside it. Vipunen's fetches hold the library's write lock for a minute
at a time, and a two-hour pass that has to queue behind them would spend most of
its life waiting; a pass that cannot write to the library also cannot damage it.
`--merge` folds the sidecar in once the library is quiet.

    python tools/fingerprint_ids.py data/library.db [--limit N] [--recheck]
    python tools/fingerprint_ids.py data/library.db --merge
"""

import argparse
import concurrent.futures
import gzip
import json
import pathlib
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import secret  # noqa: E402

# fpcalc fingerprints the first 120 seconds and AcoustID's index is built from
# that, so a longer fingerprint would be asking a different question.
FP_SECONDS = 120


def fp_key(start_ms: int, end_ms: int) -> str:
    """Cache key for one passage's fingerprint.

    The slice is part of the key because one file can hold several passages,
    and their fingerprints are of different audio. Keying on the file alone
    would let the first passage's answer be served for all of them.
    """
    return f"chromaprint:{start_ms}-{end_ms}:{FP_SECONDS}:base64"

# Present anywhere in the acoustic cluster at this score or better, and the
# stored id stands. AcoustID groups recordings that sound identical, so an id
# appearing as the second entry of a match is still a match -- and a real
# pressing often produces a strong result plus weaker echoes of itself.
CONFIRM_SCORE = 0.50
# To claim an id is *wrong* the evidence has to be as good as evidence gets.
CONTRADICT_SCORE = 0.90

BATCH = 5           # fingerprints per lookup; the body is ~3.5 KB each
REQUEST_GAP = 1.0   # seconds between lookups; the published ceiling is 3/s
DECODE_JOBS = 4

ENDPOINT = "https://api.acoustid.org/v2/lookup"
AGENT = "Lempi/0.1 (+https://github.com/MangoCats/Lempi)"

RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS id_checks (
    passage_id   INTEGER PRIMARY KEY,
    stored_mbid  TEXT    NOT NULL,
    verdict      TEXT    NOT NULL,
    score        REAL,
    -- JSON array of what AcoustID says it is instead, best first. Only the
    -- contradicted rows carry it, and it is what the review screen offers.
    suggested    TEXT,
    checked_at   TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS fingerprints (
    -- By PASSAGE, not by file: one file can hold several passages and their
    -- fingerprints are of different audio.
    passage_id   INTEGER PRIMARY KEY,
    audio_md5    TEXT NOT NULL,
    slice_key    TEXT NOT NULL,
    fingerprint  TEXT NOT NULL,
    response     TEXT,
    fetched_at   TEXT NOT NULL
);
"""

VERDICTS = ("confirmed", "contradicted", "inconclusive", "unmatched", "unreadable")


def sidecar_for(db: str) -> str:
    """Where the findings live until they are merged."""
    return os.path.splitext(db)[0] + ".idchecks.db"


def say(text: str) -> None:
    """Titles carry characters the Windows console cannot encode, and a run
    that dies on its own progress line has thrown away two hours of work."""
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


# ---------------------------------------------------------------- fingerprints

def fingerprint(path: str, start_ms: int, end_ms: int) -> str | None:
    """The **passage** as Chromaprint hears it, or `None` if it would not decode.

    ffmpeg carries the chromaprint muxer, which is the same library fpcalc
    wraps -- validated against AcoustID on known-good files before this was
    trusted, scoring 0.986 to 0.995 with the stored id present every time.
    Using it avoids making a second binary a prerequisite of the build.

    **From `start_ms`, not from the top of the file.** A passage is a slice: in
    this library the median file holds a fair amount that is not the song, and
    fingerprinting the file instead of the passage asks about the wrong audio.
    That mistake, made once, marked 3,940 passages "unmatched" -- including
    every track of *Magical Mystery Tour*, which AcoustID of course knows
    perfectly well. Each of them matched, and matched the id already stored,
    the moment the fingerprint started where the song does.
    """
    seconds = max(0.0, (end_ms - start_ms) / 1000.0)
    try:
        done = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             # Seek before -i: for audio this is both accurate and far faster
             # than decoding up to the mark and discarding it.
             "-ss", f"{start_ms / 1000.0:.3f}",
             "-t", f"{min(float(FP_SECONDS), seconds):.3f}",
             "-i", path,
             "-f", "chromaprint", "-fp_format", "base64", "-"],
            capture_output=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = done.stdout.decode("ascii", "ignore").strip()
    return out or None


# --------------------------------------------------------------------- lookups

def lookup(key: str, items: list[tuple]) -> dict[int, list]:
    """Ask AcoustID about several fingerprints at once.

    Returns results by position in `items`. A batch is one request rather than
    five, which is the difference between a run that takes half an hour of
    network time and one that takes two hours of it.
    """
    fields = {"client": key, "meta": "recordings", "batch": "1"}
    for i, (_, _, dur, fp) in enumerate(items):
        fields[f"fingerprint.{i}"] = fp
        # The PASSAGE's length. AcoustID filters candidates by duration, so
        # sending the file's length for a passage that is a slice of it rules
        # out the very recording being looked for.
        fields[f"duration.{i}"] = str(max(1, int(dur / 1000)))
    body = gzip.compress(urllib.parse.urlencode(fields).encode())
    req = urllib.request.Request(
        ENDPOINT, data=body,
        headers={"User-Agent": AGENT, "Content-Encoding": "gzip",
                 "Content-Type": "application/x-www-form-urlencoded"})

    delay = 2.0
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                payload = json.load(r)
            break
        except urllib.error.HTTPError as e:
            # 429 is the rate limit and 5xx is theirs to fix; both say wait.
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                say(f"  HTTP {e.code}, waiting {delay:.0f}s")
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt < 5:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    else:
        raise RuntimeError("lookup did not succeed after 6 attempts")

    if payload.get("status") != "ok":
        raise RuntimeError(f"acoustid: {payload.get('error', payload)}")

    by_index: dict[int, list] = {}
    for entry in payload.get("fingerprints", []):
        by_index[int(entry.get("index", 0))] = entry.get("results", [])
    return by_index


def judge(stored: str, results: list) -> tuple[str, float | None, list]:
    """What the fingerprint says about the stored id."""
    if not results:
        return "unmatched", None, []

    best = max((r.get("score") or 0.0) for r in results)

    # Everything AcoustID names for this audio, best score first, deduplicated.
    named: dict[str, dict] = {}
    for r in results:
        score = r.get("score") or 0.0
        for rec in r.get("recordings") or []:
            mbid = rec.get("id")
            if not mbid:
                continue
            artists = ", ".join(a.get("name", "") for a in rec.get("artists") or [])
            prior = named.get(mbid)
            if prior is None or score > prior["score"]:
                named[mbid] = {"mbid": mbid, "title": rec.get("title"),
                               "artist": artists or None, "score": round(score, 4)}

    if not named:
        # The audio is known but nobody has tied it to a recording. That is a
        # gap in AcoustID, not a finding about this library.
        return "unmatched", best, []

    for r in results:
        if (r.get("score") or 0.0) < CONFIRM_SCORE:
            continue
        if any(rec.get("id") == stored for rec in r.get("recordings") or []):
            return "confirmed", best, []

    ranked = sorted(named.values(), key=lambda x: -x["score"])
    if best >= CONTRADICT_SCORE:
        return "contradicted", best, ranked
    return "inconclusive", best, ranked


# ------------------------------------------------------------------------ main

def merge(db: str, side: str) -> int:
    """Fold the findings into the library, once nothing else is writing it."""
    if not os.path.exists(side):
        say(f"nothing to merge: {side} does not exist")
        return 1
    # uri=True so ATTACH below may use a `file:` URI. A plain path is still
    # treated as a plain path; only strings starting with `file:` are parsed.
    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(db, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("""CREATE TABLE IF NOT EXISTS id_checks (
        passage_id   INTEGER PRIMARY KEY REFERENCES passages(passage_id) ON DELETE CASCADE,
        stored_mbid  TEXT NOT NULL, verdict TEXT NOT NULL, score REAL,
        suggested    TEXT, checked_at TEXT NOT NULL)""")
    # `as_uri()` rather than an f-string: this path contains a space, and an
    # unencoded one makes SQLite reject the URI outright.
    side_uri = pathlib.Path(side).resolve().as_uri() + "?mode=ro"
    conn.execute("ATTACH DATABASE ? AS side", (side_uri,))
    conn.execute("""INSERT OR REPLACE INTO main.id_checks
                    SELECT * FROM side.id_checks""")
    # The fingerprints belong in the cache the schema already provides for them.
    # `slice_key` is the request key, so two passages of one file stay distinct.
    conn.execute(
        "INSERT OR REPLACE INTO main.identification_cache "
        "SELECT audio_md5,'fpcalc',slice_key,fingerprint,fetched_at FROM side.fingerprints")
    conn.execute(
        "INSERT OR REPLACE INTO main.identification_cache "
        "SELECT audio_md5,'acoustid',slice_key,response,fetched_at FROM side.fingerprints "
        "WHERE response IS NOT NULL")
    n = conn.execute("SELECT COUNT(*) FROM main.id_checks").fetchone()[0]
    conn.commit()
    conn.execute("DETACH DATABASE side")
    say(f"merged; the library now holds {n} check(s)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--limit", type=int, default=0, help="stop after N passages")
    ap.add_argument("--recheck", action="store_true",
                    help="re-examine passages already judged")
    ap.add_argument("--merge", action="store_true",
                    help="fold the sidecar findings into the library and stop")
    ap.add_argument("--out", default=None, help="sidecar path")
    args = ap.parse_args()

    side_path = args.out or sidecar_for(args.db)
    if args.merge:
        return merge(args.db, side_path)

    key = secret.acoustid_key()
    # Read-only: Vipunen may be writing this, and under WAL a reader never waits.
    # Catalogue-only, read-only.
    lib = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, timeout=30)
    side = sqlite3.connect(side_path, timeout=30)
    side.executescript(RESULTS_DDL)
    side.commit()

    done_already = {r[0] for r in side.execute("SELECT passage_id FROM id_checks")} \
        if not args.recheck else set()
    rows = lib.execute("""
        SELECT p.passage_id, f.path, p.end_ms - p.start_ms, pr.mbid, f.audio_md5,
               p.start_ms, p.end_ms
          FROM passages p
          JOIN files f ON f.file_id = p.file_id
          JOIN passage_recordings pr ON pr.passage_id = p.passage_id
         WHERE p.kind = 'radio'
         ORDER BY p.passage_id""").fetchall()
    todo = [r for r in rows if r[0] not in done_already]
    if args.limit:
        todo = todo[:args.limit]

    total = len(todo)
    say(f"{len(rows)} radio passage(s); {len(done_already)} already checked; "
        f"{total} to do\n")
    if not total:
        return 0

    counts: dict[str, int] = {}
    started = time.time()
    done_n = 0

    known_fp = {(r[0], r[1]): r[2] for r in
                side.execute("SELECT passage_id, slice_key, fingerprint FROM fingerprints")}

    def cached_fp(row) -> str | None:
        pid, path, _dur, _mbid, _md5, start, end = row
        return known_fp.get((pid, fp_key(start, end))) or fingerprint(path, start, end)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=DECODE_JOBS)
    try:
        for start in range(0, total, BATCH):
            chunk = todo[start:start + BATCH]
            # Decode in parallel; the lookup that follows is one request, and
            # waiting on ffmpeg serially would dominate the whole run.
            fps = list(pool.map(cached_fp, chunk))

            ready, unreadable = [], []
            for row, fp in zip(chunk, fps):
                (ready if fp else unreadable).append((row, fp))

            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            for (pid, _path, _dur, mbid, _md5, _s, _e), _ in unreadable:
                side.execute(
                    "INSERT OR REPLACE INTO id_checks VALUES (?1,?2,'unreadable',NULL,NULL,?3)",
                    (pid, mbid, now))
                counts["unreadable"] = counts.get("unreadable", 0) + 1

            if ready:
                items = [(r[0], r[3], r[2], fp) for r, fp in ready]
                try:
                    results = lookup(key, items)
                except Exception as e:              # noqa: BLE001
                    say(f"  batch at {start} failed, leaving it for a rerun: {e}")
                    time.sleep(REQUEST_GAP)
                    continue

                for i, ((pid, _path, _dur, mbid, md5, s_ms, e_ms), fp) in enumerate(ready):
                    verdict, score, suggested = judge(mbid, results.get(i, []))
                    side.execute(
                        "INSERT OR REPLACE INTO id_checks VALUES (?1,?2,?3,?4,?5,?6)",
                        (pid, mbid, verdict, score,
                         json.dumps(suggested) if suggested else None, now))
                    side.execute(
                        "INSERT OR REPLACE INTO fingerprints VALUES (?1,?2,?3,?4,?5,?6)",
                        (pid, md5, fp_key(s_ms, e_ms), fp,
                         json.dumps(results.get(i, [])), now))
                    counts[verdict] = counts.get(verdict, 0) + 1
                time.sleep(REQUEST_GAP)

            side.commit()
            done_n += len(chunk)
            if start % (BATCH * 20) == 0 or done_n >= total:
                rate = done_n / max(time.time() - started, 1e-9)
                left = (total - done_n) / max(rate, 1e-9)
                say(f"  {done_n}/{total}  {rate*60:.0f}/min  ~{left/60:.0f} min left  "
                    + "  ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    finally:
        pool.shutdown(wait=False)
        side.commit()

    say("")
    judged = sum(counts.values())
    for k in VERDICTS:
        v = counts.get(k, 0)
        say(f"  {k:13} {v:6d}  {v / max(judged, 1):6.1%}")
    decisive = counts.get("confirmed", 0) + counts.get("contradicted", 0)
    if decisive:
        say(f"\n  of {decisive} the fingerprint could settle, "
            f"{counts.get('contradicted', 0) / decisive:.1%} are wrong")
    say(f"\nfindings in {side_path}; merge with --merge when Vipunen is idle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
