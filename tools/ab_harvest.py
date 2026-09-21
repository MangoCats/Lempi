# SPDX-License-Identifier: AGPL-3.0-or-later
"""
[GDE-FEX-050] Tier 0 harvest: extract library flavor vectors from the archived
AcousticBrainz highlevel dumps.

The AcousticBrainz API died between 2026-01 and 2026-08; the 2022-06-23 dumps are
the last copy. Every recording found here is an exact 71-dimension vector needing
no local extraction at all -- only the misses require Tier 1 [GDE-FEX-060].

Streams each .tar.zst shard without extracting it, so mirroring 37 GB of archive
costs ~100 MB of storage for the subset we care about.

Storage is deliberately long/narrow: partial vectors are normal [GDE-ARC-030]
(11 dims from mulib, 71 from the dump, some locally computed), and multiple
submissions per recording are real and significant -- inter-submission spread is
the reproducibility ceiling measured in [GDE-FEX-085].

Usage:
    python tools/ab_harvest.py --dumps data/ab-dumps --out data/flavor.db
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import tarfile

import zstandard

# highlevel/<xx>/<x>/<mbid>-<submission>.json
NAME_RE = re.compile(r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-(\d+)\.json$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS flavor (
    recording_mbid TEXT    NOT NULL,
    submission     INTEGER NOT NULL,
    characteristic TEXT    NOT NULL,   -- e.g. 'mood_happy', 'genre_dortmund'
    class          TEXT    NOT NULL,   -- e.g. 'happy', 'rock'
    value          REAL    NOT NULL,
    source         TEXT    NOT NULL,   -- [GDE-FBD-020] provenance, never null
    PRIMARY KEY (recording_mbid, submission, characteristic, class)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_flavor_mbid   ON flavor(recording_mbid);
CREATE INDEX IF NOT EXISTS idx_flavor_source ON flavor(source);
"""


def library_mbids(db_paths):
    """Recording MBIDs we care about, unioned across the predecessor databases."""
    queries = [
        "SELECT DISTINCT musicbrainz_track_id FROM tracks WHERE musicbrainz_track_id IS NOT NULL",
        "SELECT DISTINCT mbidRecording FROM tracks WHERE mbidRecording IS NOT NULL",
    ]
    found = set()
    for path in db_paths:
        if not os.path.exists(path):
            print(f"  skip (absent): {path}", file=sys.stderr)
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        before = len(found)
        for q in queries:
            try:
                found.update(r[0] for r in conn.execute(q) if r[0])
            except sqlite3.OperationalError:
                pass  # that column doesn't exist in this database; try the next
        conn.close()
        print(f"  {path}: +{len(found) - before} new ({len(found)} total)", file=sys.stderr)
    return found


def harvest_shard(path, wanted, conn, source):
    """Stream one .tar.zst shard, storing highlevel vectors for wanted MBIDs."""
    rows, scanned, matched = [], 0, 0
    dctx = zstandard.ZstdDecompressor()
    with open(path, "rb") as fh, dctx.stream_reader(fh) as reader:
        # mode='r|' is the streaming form -- never seeks, so the archive is
        # never materialised on disk.
        with tarfile.open(fileobj=reader, mode="r|") as tf:
            for member in tf:
                if not member.isfile():
                    continue
                scanned += 1
                m = NAME_RE.match(os.path.basename(member.name))
                if not m:
                    continue
                mbid, submission = m.group(1), int(m.group(2))
                if mbid not in wanted:
                    continue
                try:
                    doc = json.load(tf.extractfile(member))
                except (json.JSONDecodeError, OSError):
                    continue
                matched += 1
                for characteristic, body in doc.get("highlevel", {}).items():
                    for cls, value in body.get("all", {}).items():
                        rows.append((mbid, submission, characteristic, cls, value, source))
                if len(rows) >= 50_000:
                    conn.executemany(
                        "INSERT OR REPLACE INTO flavor VALUES (?,?,?,?,?,?)", rows
                    )
                    conn.commit()
                    rows.clear()
    if rows:
        conn.executemany("INSERT OR REPLACE INTO flavor VALUES (?,?,?,?,?,?)", rows)
        conn.commit()
    return scanned, matched


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dumps", default="data/ab-dumps", help="directory of .tar.zst shards")
    ap.add_argument("--out", default="data/flavor.db")
    ap.add_argument("--source", default="acousticbrainz-dump-20220623")
    ap.add_argument("--libs", nargs="*", default=[
        "lempi.db",
        "../MuLibPlay/mulib.db",
    ])
    args = ap.parse_args()

    print("Collecting library recording MBIDs...", file=sys.stderr)
    wanted = library_mbids(args.libs)
    if not wanted:
        sys.exit("No library MBIDs found -- check --libs paths.")

    shards = sorted(
        os.path.join(args.dumps, f)
        for f in os.listdir(args.dumps)
        if f.endswith(".tar.zst") and "highlevel" in f
    )
    if not shards:
        sys.exit(f"No highlevel shards in {args.dumps}")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    conn = sqlite3.connect(args.out)
    conn.executescript(SCHEMA)

    total_scanned = total_matched = 0
    for i, shard in enumerate(shards, 1):
        scanned, matched = harvest_shard(shard, wanted, conn, args.source)
        total_scanned += scanned
        total_matched += matched
        print(f"[{i}/{len(shards)}] {os.path.basename(shard)}: "
              f"{scanned:,} scanned, {matched:,} matched", file=sys.stderr)

    covered = conn.execute("SELECT COUNT(DISTINCT recording_mbid) FROM flavor").fetchone()[0]
    dims = conn.execute("SELECT COUNT(*) FROM flavor").fetchone()[0]
    multi = conn.execute(
        "SELECT COUNT(*) FROM (SELECT recording_mbid FROM flavor "
        "GROUP BY recording_mbid HAVING COUNT(DISTINCT submission) > 1)"
    ).fetchone()[0]
    conn.close()

    print("\n=== Harvest summary ===", file=sys.stderr)
    print(f"  documents scanned : {total_scanned:,}", file=sys.stderr)
    print(f"  library recordings: {covered:,} of {len(wanted):,} "
          f"({100.0 * covered / len(wanted):.1f}% coverage)  [GDE-OPN-010]", file=sys.stderr)
    print(f"  with >1 submission: {multi:,}  (see [GDE-FEX-085])", file=sys.stderr)
    print(f"  dimension values  : {dims:,}", file=sys.stderr)
    print(f"  written to        : {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
