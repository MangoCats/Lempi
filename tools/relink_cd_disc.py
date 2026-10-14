#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Re-link the tracks of a CD rip catalogued as another disc's `[SPEC-CDI-099]`.

Until 2026-10-14 the importer looked each track up by its number on the
release's *first* disc, whichever disc was in the drive. A second disc of a
set was catalogued as the first disc's songs: Woodstock's disc 2 (seven long
live sets) went in as disc 1's first seven, played on the radio under those
names, and was sent so to every speaker.

This finds every CD-imported rip whose tracks are linked to a recording of
its own chosen release that sits on a *different* disc than the one ripped --
the bug's signature, and nothing else: a track still waiting in the review
queue, or identified some other way, is left as it is. The disc is the one
the rip's own Disc ID names on MusicBrainz, or, without one, the disc whose
track count and lengths fit (`ingest_cd.disc_medium`). A tracks-mode rip's
files are taken together, as the one disc they are.

    python tools/relink_cd_disc.py data/library.db                 # say what is wrong
    python tools/relink_cd_disc.py data/library.db --commit \\
        --listener data/listener.db                                # and put it right

A fix re-points both passages of each track (radio and album) at the right
recording, writes that recording and its artists as an add would, links it to
its own disc of the release, moves the listener's plays of those passages to
it, and records each change in `ingest_decisions` (stage `relink`) with what
it was -- so it can be read back, or undone.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import artist_sort      # noqa: E402
import cd_toc           # noqa: E402
import fetch_releases   # noqa: E402
import ingest_cd        # noqa: E402
import lempi_db         # noqa: E402


def rips(conn) -> list[dict]:
    """Every CD rip with an edition chosen for it, as the disc it is: an image
    rip's radio passages numbered in order; a tracks-mode rip's files grouped
    by folder and edition, each by the track number its decision recorded.
    Each track is `(number, passage_id, start_ms, end_ms, mbid, file_id, md5)`."""
    decided = {}
    for md5, detail in conn.execute(
            "SELECT audio_md5, detail FROM ingest_decisions WHERE stage = 'rip' ORDER BY decision_id"):
        d = json.loads(detail or "{}")
        if d.get("chosen"):
            decided[md5] = d
    groups: dict[tuple, dict] = {}
    for md5, d in decided.items():
        row = conn.execute("SELECT file_id, path FROM files WHERE audio_md5 = ?1", (md5,)).fetchone()
        if row is None:
            continue
        file_id, path = row
        radio = conn.execute(
            "SELECT p.passage_id, p.start_ms, p.end_ms, pr.mbid FROM passages p "
            "JOIN passage_recordings pr ON pr.passage_id = p.passage_id "
            "WHERE p.file_id = ?1 AND p.kind = 'radio' ORDER BY p.start_ms", (file_id,)).fetchall()
        if d.get("tracks_mode"):
            key = (d["chosen"], os.path.dirname(path))
            numbered = [(d["track"], *radio[0], file_id, md5)] if d.get("track") and radio else []
        else:
            key = (d["chosen"], path)
            numbered = [(n, *r, file_id, md5) for n, r in enumerate(radio, 1)]
        g = groups.setdefault(key, {"release": d["chosen"], "disc_id": d.get("disc_id"),
                                    "track_count": d.get("track_count"), "path": key[1], "tracks": []})
        g["disc_id"] = g["disc_id"] or d.get("disc_id")
        g["tracks"] += numbered
    for g in groups.values():
        g["tracks"].sort()
    return list(groups.values())


def wrong_tracks(rip: dict, release: dict) -> tuple[dict | None, list[dict]]:
    """`(medium, fixes)`: the disc this rip is, and each track linked to a
    recording of this release on another disc."""
    toc = cd_toc.DiscToc(tracks=[cd_toc.TocTrack(number=t[0], start_ms=t[2], end_ms=t[3], start_sector=0,
                                                 end_sector=0) for t in rip["tracks"]],
                         leadout_sector=0, source="eac-cue")
    medium = ingest_cd.disc_medium(release, rip["disc_id"], toc, rip["track_count"])
    if medium is None:
        return None, []
    elsewhere = {(t.get("recording") or {}).get("id"): m.get("position")
                 for m in release.get("media") or [] if m is not medium for t in m.get("tracks") or []}
    fixes = []
    for n, pid, start, end, mbid, file_id, md5 in rip["tracks"]:
        if mbid not in elsewhere:
            continue
        t = next((t for t in medium.get("tracks") or [] if t.get("position") == n), None)
        if t is None:
            continue
        fixes.append({"track": n, "radio": pid, "start_ms": start, "end_ms": end, "file_id": file_id, "md5": md5,
                      "from": mbid, "from_disc": elsewhere[mbid], "to": (t.get("recording") or {}).get("id"),
                      "title": t.get("title"), "disc": medium.get("position")})
    return medium, fixes


def album_passage(conn, file_id: int, radio_pid: int) -> int | None:
    """The album passage sharing the radio one's identity: an image rip's at
    the same start, a tracks-mode file's single album passage."""
    rows = conn.execute("SELECT p.passage_id, p.start_ms FROM passages p WHERE p.file_id = ?1 AND p.kind = 'album' "
                        "ORDER BY p.start_ms", (file_id,)).fetchall()
    start = conn.execute("SELECT start_ms FROM passages WHERE passage_id = ?1", (radio_pid,)).fetchone()[0]
    same = [r[0] for r in rows if r[1] == start]
    return same[0] if same else (rows[0][0] if len(rows) == 1 else None)


def apply(conn, listener, release: dict, medium: dict, fixes: list[dict]) -> int:
    narrowed = dict(release, media=[medium],
                    _track_total=sum(ingest_cd._medium_tracks(m) for m in release.get("media") or []))
    now = ingest_cd._now()
    plays = 0
    for f in fixes:
        t = ingest_cd._track_at_position(narrowed, f["track"])
        rec = t.get("recording") or {}
        conn.execute("INSERT OR IGNORE INTO recordings (mbid,title,length_ms,source) VALUES (?1,?2,?3,'musicbrainz')",
                     (f["to"], rec.get("title") or t.get("title"), f["end_ms"] - f["start_ms"]))
        for artist_mbid, name, sort_name in ingest_cd._artists_for(narrowed, f["track"]):
            conn.execute("INSERT OR IGNORE INTO artists (mbid,name,sort_name,source) VALUES (?1,?2,?3,'musicbrainz')",
                         (artist_mbid, name, sort_name))
            artist_sort.record(conn, artist_mbid, sort_name)
            conn.execute("INSERT OR IGNORE INTO recording_artists (mbid,artist_mbid,weight,source) "
                         "VALUES (?1,?2,1.0,'musicbrainz')", (f["to"], artist_mbid))
        pids = [p for p in (f["radio"], album_passage(conn, f["file_id"], f["radio"])) if p]
        q = ",".join("?" * len(pids))
        conn.execute(f"UPDATE passage_recordings SET mbid = ?, source = 'musicbrainz' "
                     f"WHERE mbid = ? AND passage_id IN ({q})", [f["to"], f["from"], *pids])
        try:
            conn.execute("UPDATE id_checks SET stored_mbid = ?1 WHERE passage_id = ?2 AND stored_mbid = ?3",
                         (f["to"], f["radio"], f["from"]))
        except sqlite3.OperationalError:
            pass    # no id_checks table: nothing to correct
        if listener is not None:
            plays += listener.execute(
                f"UPDATE listener_play_history SET mbid = ? WHERE mbid = ? AND passage_id IN ({q})",
                [f["to"], f["from"], *pids]).rowcount
        conn.execute("INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
                     "VALUES (?1,'relink','disc_corrected',1.0,?2,?3)",
                     (f["md5"], json.dumps({k: f[k] for k in ("track", "from", "from_disc", "to", "disc", "title")}
                                           | {"passages": pids}), now))
    ingest_cd.record_release(conn, narrowed, [(f["track"], f["to"]) for f in fixes],
                             sorted({f["md5"] for f in fixes}))
    return plays


def fetch_release(rid: str) -> dict:
    """The release with every disc, its tracks and the Disc IDs each is known
    by. A connection that drops is tried again; one that never answers is
    said and left out, never taken for a release with no discs."""
    url = f"{ingest_cd.MB_WS}/release/{rid}?fmt=json&inc=recordings+artist-credits+discids"
    for attempt in range(4):
        try:
            return fetch_releases.get(url) or {}
        except OSError as e:
            print(f"  MusicBrainz did not answer for {rid} ({e}); trying again", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    return {"_unreachable": True}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("db")
    ap.add_argument("--commit", action="store_true", help="write; without it, only say what is wrong")
    ap.add_argument("--listener", help="the listener database, whose plays of a re-linked track move with it")
    args = ap.parse_args()
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=args.commit, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    listener = (sqlite3.connect(args.listener, timeout=60, isolation_level=None)
                if args.commit and args.listener else None)

    found = rips(conn)
    by_release: dict[str, dict] = {}
    checked = wrong_rips = wrong = unknown = plays = 0
    for rip in found:
        rid = rip["release"]
        if rid not in by_release:
            if by_release:
                time.sleep(fetch_releases.RATE_S)
            by_release[rid] = fetch_release(rid)
        release = by_release[rid]
        if not release.get("media"):
            print(f"  {'UNREACHABLE' if release.get('_unreachable') else 'NOT FOUND'}  {rid} -- "
                  f"not checked: {rip['path']}")
            unknown += 1
            continue
        checked += 1
        if len(release["media"]) == 1:
            continue    # one disc: nothing to have taken the wrong one of
        medium, fixes = wrong_tracks(rip, release)
        if medium is None:
            print(f"  CANNOT TELL which of {len(release['media'])} discs {rip['path']} is -- left as it is")
            unknown += 1
            continue
        if not fixes:
            print(f"  ok: {rip['path']} is disc {medium.get('position')} of {len(release['media'])}, "
                  "and catalogued as that disc")
            continue
        wrong_rips += 1
        wrong += len(fixes)
        print(f"{rip['path']}\n  is disc {medium.get('position')} of {release.get('title')!r}; "
              f"{len(fixes)} track(s) were catalogued as disc {fixes[0]['from_disc']}'s:")
        for f in fixes:
            old = conn.execute("SELECT title FROM recordings WHERE mbid = ?1", (f["from"],)).fetchone()
            print(f"    track {f['track']}: {old[0] if old else f['from']!r} -> {f['title']!r}")
        if args.commit:
            conn.execute("BEGIN IMMEDIATE")
            if listener is not None:
                listener.execute("BEGIN IMMEDIATE")
            try:
                plays += apply(conn, listener, release, medium, fixes)
                conn.commit()
                if listener is not None:
                    listener.execute("COMMIT")
            except Exception:
                conn.rollback()
                if listener is not None:
                    listener.execute("ROLLBACK")
                raise
    print(f"\n{checked} CD rip(s) checked against MusicBrainz, {len(by_release)} release(s); "
          f"{wrong} track(s) on {wrong_rips} rip(s) catalogued as another disc's"
          + (f"; {unknown} could not be checked" if unknown else ""))
    if wrong and args.commit:
        print(f"re-linked {wrong} track(s)" + (f"; {plays} play(s) moved with them" if listener is not None else
                                               "; plays not moved -- no --listener given"))
    elif wrong:
        print("Nothing written; re-run with --commit (and --listener) to put it right.")
    return 1 if unknown else 0


if __name__ == "__main__":
    sys.exit(main())
