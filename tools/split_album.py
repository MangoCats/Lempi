#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Find the release an album file holds, so it can be split into its tracks
`[SPEC-CDI-060]`.

An album captured as one file -- a DAO capture with no CUE sheet -- arrives
as one long passage. `segment_dao.py` splits it best when told how long each
track should be `[SPEC-SA-115..121]`, and those lengths are MusicBrainz's to
give. This finds the candidates: the file's own tags (or its folder names)
make the search, `suggest_release.py`'s own calls fetch each release in full,
and every disc of every candidate is ranked by how closely its total length
matches the file's. The import page shows them; the person picks; the page
hands that disc's lengths to `segment_dao.py`.

    python tools/split_album.py <db> --file <path in the library> [--query Q] [--json]

It writes only MusicBrainz's answers into the catalogue's own cache, as
`suggest_release.py` does, so asking twice asks the network once.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import suggest_release as sr  # noqa: E402  -- search_releases, fetch_release_detail


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(str(text).encode(enc, "replace").decode(enc), flush=True)


def the_file(conn, path: str) -> dict:
    """The library's row for `path`, with its own tags."""
    row = conn.execute("SELECT file_id, path, duration_ms FROM files WHERE path = ?1", (path,)).fetchone()
    if row is None:
        raise LookupError(f"{path!r} is not in the library -- induct its folder first")
    tags = {}
    try:
        t = conn.execute("SELECT artist, album, title FROM file_tags WHERE file_id = ?1", (row[0],)).fetchone()
        if t:
            tags = {"artist": t[0], "album": t[1], "title": t[2]}
    except Exception:                                          # noqa: BLE001  -- no tags table
        pass
    return {"file_id": row[0], "path": row[1], "duration_ms": row[2] or 0, **tags}


def guess_query(f: dict) -> str | None:
    """The search: the file's own album and artist tags, else its album
    folder and the folder above it -- where a library filed by artist and
    album keeps both."""
    album, artist = f.get("album"), f.get("artist")
    if not album:
        parts = os.path.normpath(f["path"]).split(os.sep)
        album = parts[-2] if len(parts) >= 2 else None
        artist = artist or (parts[-3] if len(parts) >= 3 else None)
    if not album:
        return None
    q = f'release:"{album}"'
    return q + (f' AND artist:"{artist}"' if artist else "")


def candidates(conn, f: dict, query: str, limit: int = sr.MAX_CANDIDATES) -> list:
    """Each disc of each release the search finds, ranked by how far its
    total length is from the file's. A disc with a track of unknown length is
    listed last: its lengths cannot drive a split."""
    out = []
    for i, hit in enumerate(sr.search_releases(query)[:limit]):
        if i:
            time.sleep(sr.RATE_S)
        detail = sr.fetch_release_detail(conn, hit["id"])
        if not detail:
            continue
        for medium in detail.get("media") or []:
            tracks = [{"title": t.get("title") or (t.get("recording") or {}).get("title"),
                       "length_ms": t.get("length") or (t.get("recording") or {}).get("length")}
                      for t in medium.get("tracks") or []]
            if not tracks:
                continue
            known = all(t["length_ms"] for t in tracks)
            total = sum(t["length_ms"] or 0 for t in tracks)
            out.append({"id": detail.get("id"), "title": detail.get("title"),
                        "artist": sr.artist_credit_name(detail.get("artist-credit")),
                        "year": (detail.get("date") or "")[:4] or None, "country": detail.get("country"),
                        "disc": medium.get("position"), "discs": len(detail.get("media") or []),
                        "tracks": tracks, "total_ms": total, "lengths_known": known,
                        "off_ms": abs(total - f["duration_ms"]) if known else None,
                        "expect": ",".join(str(round(t["length_ms"] / 1000, 1)) for t in tracks) if known else None})
    conn.commit()
    out.sort(key=lambda c: (c["off_ms"] is None, c["off_ms"] or 0))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--file", required=True)
    ap.add_argument("--query", help='the search, if the tags guess it wrong: release:"X" AND artist:"Y"')
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
        try:
            sr.ensure_schema(conn)
            f = the_file(conn, args.file)
            query = args.query or guess_query(f)
            if not query:
                raise ValueError("nothing to search by: the file has no album tag and no album folder")
            found = candidates(conn, f, query)
        finally:
            conn.close()
    except Exception as e:                                         # noqa: BLE001
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            say(f"error: {e}")
        return 1
    result = {"ok": True, "file": f, "query": query, "candidates": found}
    if args.json:
        print(json.dumps(result))
    else:
        say(f"{f['path']} -- {round(f['duration_ms'] / 60000, 1)} min; searched {query}")
        for c in found:
            off = "lengths unknown" if c["off_ms"] is None else f"{round(c['off_ms'] / 1000)} s off"
            say(f"  {c['id']} disc {c['disc']}/{c['discs']}  {c['artist']} - {c['title']} "
                f"({c['year'] or '?'})  {len(c['tracks'])} tracks, {off}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
