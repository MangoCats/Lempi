#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Record which release each file was taken from `[SPEC-SC-125]`.

A recording is on many releases, and the library chose one per recording, so
every copy of a hit was shown under one album: on 2026-10-02 *She's So
Unusual* listed tracks ripped from *Twelve Deadly Cyns* and *The Essential
Cyndi Lauper*, and those two looked short. `file_releases` names the album per
FILE; this fills it for the library as it is.

- **A CD rip** recorded the exact edition when it was added (`ingest_decisions`,
  stage `rip`, `detail.chosen`): taken as it is, source `cd:import`.
- **A folder of files** is matched to a release that holds at least 60% of the
  folder's recordings and whose title is the folder's album tag or its own
  name ("Album (1999)"). Several editions of one title often fit; the one
  already chosen for most of its recordings wins, then the one holding most,
  then the earliest. Source `folder:match`.
- **Anything else** -- a folder of mixed albums, recordings with no releases --
  is left to the recording's chosen release, as before.

A file already recorded by a CD add or by hand is not touched. Dry run unless
`--commit`; the dry run lists every album name that would change.

    python tools/align_file_releases.py data/library.db [--commit] [--all-changes]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402

SHARE = 0.6
DDL = ("CREATE TABLE IF NOT EXISTS file_releases (audio_md5 TEXT PRIMARY KEY, release_mbid TEXT NOT NULL, "
       "source TEXT NOT NULL, decided_at TEXT)")
_PUNCT = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
                        "‘": "'", "’": "'", "“": '"', "”": '"', "…": "..."})


def norm(s: str | None) -> str:
    """A title as a comparison key: case, accents and punctuation aside, and a
    folder's trailing "(1999)" dropped."""
    s = unicodedata.normalize("NFKD", (s or "").translate(_PUNCT)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(\d{4}\)\s*$", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def plan(conn) -> dict:
    """What would be recorded, and what it changes. Read-only."""
    conn.execute(DDL)
    files: dict[int, list] = {}
    for fid, md5, path, mbid, tag in conn.execute(
            "SELECT f.file_id, f.audio_md5, f.path, pr.mbid, ft.album FROM files f "
            "JOIN passages p ON p.file_id = f.file_id AND p.kind = 'radio' "
            "JOIN passage_recordings pr ON pr.passage_id = p.passage_id "
            "LEFT JOIN file_tags ft ON ft.file_id = f.file_id"):
        files.setdefault(fid, [md5, path, [], tag])[2].append(mbid)
    kept = {m: s for m, s in conn.execute("SELECT audio_md5, source FROM file_releases")}
    title = dict(conn.execute("SELECT mbid, title FROM releases"))
    date = dict(conn.execute("SELECT mbid, release_date FROM releases"))
    chosen = dict(conn.execute("SELECT mbid, release_mbid FROM release_recordings WHERE chosen = 1"))
    used = {m for v in files.values() for m in v[2]}
    holds = collections.defaultdict(set)
    for rel, m in conn.execute("SELECT release_mbid, mbid FROM release_recordings"):
        if m in used:
            holds[m].add(rel)

    rip = {}
    for md5, detail in conn.execute("SELECT audio_md5, detail FROM ingest_decisions WHERE stage = 'rip'"):
        try:
            ch = (json.loads(detail or "{}") or {}).get("chosen")
        except ValueError:
            ch = None
        if ch:
            rip[md5] = ch

    out = {"write": {}, "cd": 0, "folder": 0, "unmatched": 0, "no_releases": 0, "kept": 0, "changes": []}

    def shown(m, tag):
        return title.get(chosen.get(m)) or tag

    def decide(fid, rel, source):
        md5, _, recs, tag = files[fid]
        if kept.get(md5) in ("cd:import", "manual") and source != "cd:import":
            out["kept"] += 1
            return
        out["write"][md5] = (rel, source)
        for m in recs:
            if norm(shown(m, tag)) != norm(title.get(rel)):
                out["changes"].append((shown(m, tag), title.get(rel), files[fid][1]))

    # Grouped by folder AND album tag: a folder can hold several albums --
    # `Music\Eagles` does, and as one group its tracks matched a boxed set
    # titled "Eagles" (2026-10-02 dry run). A folder named for an artist on its
    # own recordings is no album title.
    artists = collections.defaultdict(set)
    for m, name in conn.execute("SELECT ra.mbid, a.name FROM recording_artists ra "
                                "JOIN artists a ON a.mbid = ra.artist_mbid"):
        if m in used:
            artists[m].add(norm(name))
            # "Jackson, Michael" as a folder is the same artist.
            if "," in name:
                artists[m].add(norm(" ".join(reversed([x.strip() for x in name.split(",", 1)]))))
    folders = collections.defaultdict(list)
    for fid, (md5, path, recs, tag) in files.items():
        if md5 in rip:
            decide(fid, rip[md5], "cd:import")
            out["cd"] += 1
        else:
            folders[(os.path.dirname(path), norm(tag) if tag else None)].append(fid)
    for (d, tag), fids in folders.items():
        recs = [m for f in fids for m in files[f][2]]
        here = {a for m in recs for a in artists.get(m, ())}
        names = {tag} if tag else set()
        if norm(os.path.basename(d)) not in here:
            names.add(norm(os.path.basename(d)))
        count = collections.Counter(r for m in recs for r in holds.get(m, ()))
        if not count:
            out["no_releases"] += len(fids)
            continue
        fits = [r for r, n in count.items() if norm(title.get(r)) in names and n >= SHARE * len(recs)]
        if not fits:
            out["unmatched"] += len(fids)
            continue
        already = collections.Counter(chosen.get(m) for m in recs)
        best = min(fits, key=lambda r: (-already[r], -count[r], date.get(r) or "9999", r))
        for f in fids:
            decide(f, best, "folder:match")
        out["folder"] += len(fids)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("db")
    ap.add_argument("--commit", action="store_true", help="write; without it, only say what")
    ap.add_argument("--all-changes", action="store_true", help="list every album-name change, not the first 40")
    args = ap.parse_args()
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True)
    p = plan(conn)
    print(f"CD rips, their recorded edition:          {p['cd']} file(s)")
    print(f"folders matched to a release:             {p['folder']} file(s)")
    print(f"folders with no release titled and full:  {p['unmatched']} file(s) -- left to each recording's choice")
    print(f"recordings with no releases at all:       {p['no_releases']} file(s)")
    if p["kept"]:
        print(f"recorded already by a CD add or by hand:  {p['kept']} file(s), kept")
    ch = collections.Counter((a, b) for a, b, _ in p["changes"])
    print(f"\n{len(p['changes'])} passage(s) would be shown under a different album name:")
    for (a, b), n in (ch.most_common() if args.all_changes else ch.most_common(40)):
        print(f"  {n:4}  {a}  ->  {b}")
    if not args.all_changes and len(ch) > 40:
        print(f"  ... and {len(ch) - 40} more; --all-changes lists them")
    if not args.commit:
        print(f"\nwould record {len(p['write'])} file(s). Nothing written; re-run with --commit.")
        return 0
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    conn.executemany(
        "INSERT INTO file_releases (audio_md5, release_mbid, source, decided_at) VALUES (?1, ?2, ?3, ?4) "
        "ON CONFLICT(audio_md5) DO UPDATE SET release_mbid = excluded.release_mbid, source = excluded.source, "
        "decided_at = excluded.decided_at WHERE file_releases.release_mbid IS NOT excluded.release_mbid",
        [(md5, rel, src, now) for md5, (rel, src) in p["write"].items()])
    conn.commit()
    print(f"\nrecorded {len(p['write'])} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
