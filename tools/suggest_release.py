#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Suggest a MusicBrainz release for a folder of already-split files
`[SPEC-SUI-215]`.

Not a reproduction of the inherited McRhythm album-matcher (`MCR-SPEC033`,
marked PROVISIONAL/P4 in `SPEC007` §6) -- that solves a harder, different
problem: finding cut points inside one continuous DAO-rip file, with no
identifiers at all. A folder like this one is already split one file per
track, and each file's own tags already carry a title, an artist, often a
track number. Matching a short list of already-separate files against a
release's own short tracklist needs none of the boundary-detection/DP-
assembly machinery that problem does -- just fuzzy title/duration matching
between two lists of ~10-20 items, which is what this does.

Two independent modes, rehearse-by-default like every other tool here:

**Discovery** -- read and cache only, never touches `passage_recordings`:

    python tools/suggest_release.py data/library.db "C:/Music/Foghat/The Best of Foghat" --json

Guesses `artist`/`album` from the folder's own file tags (majority vote) when
`--query` isn't given, searches MusicBrainz's release search, fetches full
track/recording detail for the top few candidates, scores each by how well
its tracklist lines up with the folder's actual files, and reports all of it
-- caching every release/track pulled in `releases`/`release_recordings`,
exactly the shape `fetch_releases.py`/`choose_release.py` already read and
write, so nothing downstream needs to know which tool populated them.

`--query` also accepts a release already found by hand on musicbrainz.org --
its bare id, or the whole page URL pasted as-is -- fetched and scored
directly rather than sent to the free-text search endpoint, where a raw id
matches nothing indexed as words and reports "0 candidate release(s)
scored" for a release that plainly exists.

**Accept** -- the write half, rehearse-by-default:

    python tools/suggest_release.py data/library.db "C:/Music/Foghat/The Best of Foghat" \\
        --accept e4d469ff-3633-4e16-8f49-03c48e37c5fb --commit --json

Re-derives the same per-file matches against that one release (from cache,
no new MusicBrainz calls needed unless it was never fetched) and, for each
confidently-matched file's passage, assigns the matched recording -- the
same shape `apply_changes.py`'s `apply_id_review` already writes -- and
records one `ingest_decisions` row per affected passage, the exact field
shape `choose_release.py` already established (`stage`, `outcome`=chosen id,
`confidence`=score, JSON `detail`), so `profile.html`'s existing "Ingest
decisions" table renders it with no changes of its own. Unmatched files are
left untouched and named, never guessed at.

Also clears any `listener_flags` row a reassigned passage's old or new
recording id, or the passage itself, could plausibly have been flagged
under `[SPEC-DF-112]` -- the identical `clear_flags_for()` `apply_changes.py`
already uses for the sync path, reused rather than reimplemented. Without
this, a flag set against the passage's *pre-accept* recording id survives
the reassignment pointing at an id nothing resolves to any more, and reads
on `/flags` as "no longer resolvable" -- indistinguishable from a passage
that vanished, when what actually happened is the opposite: the flag was
answered.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
from apply_changes import clear_flags_for  # noqa: E402  -- [SPEC-DF-112], reused not reinvented
from choose_release import name_match  # noqa: E402  -- reused, not re-derived
from fetch_releases import get as mb_get, CACHE_DDL  # noqa: E402
import artist_sort  # noqa: E402  -- an artist's sort name [REQ-VIS-182]
import ingest_cd  # noqa: E402  -- record_release(), the edition as a CD add records it
import release_sources  # noqa: E402  -- which editions are settled [SPEC-FIN-040]

SOURCE = "review:folder_release_match"
# `LEMPI_MUSICBRAINZ_URL` points both elsewhere -- a test's own server.
SEARCH_BASE = os.environ.get("LEMPI_MUSICBRAINZ_URL", "https://musicbrainz.org").rstrip("/") + "/ws/2/release"
DETAIL_BASE = SEARCH_BASE
# `labels` since 2026-11-19, so editions of one title can be told apart [SPEC-FIN-030].
DETAIL_INC = "media+recordings+artist-credits+release-groups+labels"
MAX_CANDIDATES = 5   # full-detail fetches per discovery run -- each one is a
                      # real, rate-limited MusicBrainz request

# Deliberately blunt, matching `choose_release.py`'s own stated posture:
# tuning past one decimal place would be fitting noise against a folder of
# ~10-20 files, not a labelled dataset.
MATCH_THRESHOLD = 0.5
NAME_FLOOR = 0.5      # below this, no bonus may rescue an unrelated title
TRACK_NO_BONUS = 0.15
DURATION_WINDOW_MS = 60_000  # a duration delta beyond this earns no tiebreak credit

# The base schema `[SPEC008]`-shaped, matching the exact columns
# `fetch_releases.py`/`choose_release.py` already read and write -- created
# defensively (`IF NOT EXISTS`) rather than assumed present, the same
# discipline `apply_changes.py`'s `ensure_review_tables()` already uses,
# since a library that has never run `fetch_releases.py` has none of this.
BASE_DDL = """
CREATE TABLE IF NOT EXISTS releases (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
    release_date TEXT, source TEXT NOT NULL, release_group TEXT, status TEXT,
    primary_type TEXT, secondary_types TEXT, country TEXT, track_count INTEGER,
    artist_credit TEXT);
CREATE TABLE IF NOT EXISTS release_recordings (
    release_mbid TEXT NOT NULL REFERENCES releases(mbid) ON DELETE CASCADE,
    mbid TEXT NOT NULL REFERENCES recordings(mbid) ON DELETE CASCADE,
    position INTEGER, source TEXT NOT NULL, track_length_ms INTEGER,
    chosen INTEGER DEFAULT 0, disc INTEGER,
    PRIMARY KEY (release_mbid, mbid)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ingest_decisions (
    decision_id INTEGER PRIMARY KEY, audio_md5 TEXT, stage TEXT,
    outcome TEXT, confidence REAL, detail TEXT, decided_at INTEGER);
"""


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(BASE_DDL)
    conn.executescript(CACHE_DDL)
    for ddl in ("ALTER TABLE releases ADD COLUMN artist_credit TEXT",          # [REQ-VIS-183]
                "ALTER TABLE release_recordings ADD COLUMN chosen INTEGER DEFAULT 0",
                "ALTER TABLE release_recordings ADD COLUMN disc INTEGER"):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass    # there already


# ------------------------------------------------------------- folder scope --

def gather_folder_files(conn: sqlite3.Connection, folder: str) -> list:
    """Files whose own directory is exactly `folder` -- not recursive. An
    album's tracks are flat in one directory; a recursive walk risks pulling
    in an unrelated subfolder's files as candidate matches.
    """
    folder_norm = os.path.normpath(folder)
    rows = conn.execute(
        "SELECT f.file_id, f.path, f.duration_ms, f.audio_md5, "
        "       t.title, t.artist, t.album, t.track_no, t.disc_no "
        "FROM files f LEFT JOIN file_tags t ON t.file_id = f.file_id "
        "WHERE f.path LIKE ?1", (folder + "%",)).fetchall()
    out = []
    for file_id, path, duration_ms, audio_md5, title, artist, album, track_no, disc_no in rows:
        if os.path.normpath(os.path.dirname(path)) != folder_norm:
            continue
        # The file's radio passage, and its album passage beside it: one
        # identification for both, as a CD add gives them [SPEC-SA-110].
        # Until 2026-11-19 only the radio one was credited, so a whole-album
        # play kept the placeholder [SPEC-FIN-045].
        passage_ids = [r[0] for r in conn.execute(
            "SELECT passage_id FROM passages WHERE file_id=?1 AND kind IN ('radio','album') "
            "ORDER BY kind = 'album', start_ms", (file_id,))]
        out.append({"file_id": file_id, "path": path, "duration_ms": duration_ms,
                    "audio_md5": audio_md5, "title": title, "artist": artist,
                    "album": album, "track_no": track_no, "disc_no": disc_no,
                    "passage_ids": passage_ids})
    return out


def guess_query(files: list) -> str | None:
    """Majority vote over the folder's own file tags -- the same "trust the
    tag first" posture `choose_release.py`'s own `name_match` docstring
    argues for rippers: they abbreviate, they rarely invent.
    """
    from collections import Counter
    artists = Counter(f["artist"] for f in files if f["artist"])
    albums = Counter(f["album"] for f in files if f["album"])
    artist = artists.most_common(1)[0][0] if artists else None
    album = albums.most_common(1)[0][0] if albums else None
    if album and artist:
        return f'release:"{album}" AND artist:"{artist}"'
    if album:
        return f'release:"{album}"'
    if artist:
        return f'artist:"{artist}"'
    return None


# A release found by browsing musicbrainz.org directly -- as natural a thing
# to paste into "release-query" as a hand-picked search string, and a
# strictly *better* answer than one: found live pasting a release's own id
# (copied from its MusicBrainz page) into this exact box and getting "0
# candidate release(s) scored" back, because it was sent to the free-text
# search endpoint unchanged, where a bare UUID matches nothing indexed as
# words. Matched anywhere in the string, not anchored to the whole of it,
# so a pasted `https://musicbrainz.org/release/<id>` URL works the same as
# the bare id `choose_release.py`'s own `--accept` already expects.
RELEASE_ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.IGNORECASE)


def release_id_in(query: str) -> str | None:
    m = RELEASE_ID_RE.search(query or "")
    return m.group(1).lower() if m else None


# ---------------------------------------------------------- MusicBrainz calls --

def search_releases(query: str, limit: int = 15) -> list:
    url = f"{SEARCH_BASE}?query={urllib.parse.quote(query)}&fmt=json&limit={limit}"
    doc = mb_get(url)
    hits = list((doc or {}).get("releases") or [])
    hits.sort(key=lambda h: -(int(h.get("score") or 0)))
    return hits


def fetch_release_detail(conn: sqlite3.Connection, mbid: str, refresh: bool = False) -> dict | None:
    """Full track/recording detail for one release, cached in the same
    `musicbrainz_cache` table `fetch_releases.py` already uses -- a
    different `kind` value (`release-detail`) so the two never collide, but
    the same table, so a re-run after either tool asks nothing twice.
    """
    if not refresh:
        row = conn.execute(
            "SELECT response FROM musicbrainz_cache WHERE mbid=?1 AND kind='release-detail'",
            (mbid,)).fetchone()
        if row:
            return json.loads(row[0])
    doc = mb_get(f"{DETAIL_BASE}/{mbid}?inc={DETAIL_INC}&fmt=json")
    if doc:
        conn.execute(
            "INSERT OR REPLACE INTO musicbrainz_cache (mbid, kind, response, fetched_at) "
            "VALUES (?1,'release-detail',?2,?3)", (mbid, json.dumps(doc), int(time.time())))
    return doc


def artist_credit_name(credit: list) -> str | None:
    return "".join((c.get("name") or "") + (c.get("joinphrase") or "") for c in (credit or [])) or None


def tracks_from_detail(detail: dict) -> list:
    """One entry per track, carrying its own recording's mbid -- the thing
    `fetch_releases.py`'s own (opposite-direction, browse-by-recording)
    fetch never needed to extract, since it always started from a recording
    already known.
    """
    out = []
    for i, medium in enumerate(detail.get("media") or [], 1):
        for t in medium.get("tracks") or []:
            rec = t.get("recording") or {}
            if not rec.get("id"):
                continue
            credit = rec.get("artist-credit") or t.get("artist-credit") or detail.get("artist-credit") or []
            out.append({
                # A track is its disc and its place on it: a two-disc set has
                # two track 1s, and by place alone they were one [SPEC-FIN-035].
                "disc": medium.get("position") or i,
                "position": t.get("position"),
                "title": t.get("title") or rec.get("title"),
                "length": t.get("length") or rec.get("length"),
                "recording_mbid": rec["id"],
                "recording_title": rec.get("title") or t.get("title"),
                "artist_mbid": (credit[0].get("artist") or {}).get("id") if credit else None,
                "artist_name": artist_credit_name(credit),
                # Every credited artist, with the sort name Browse sorts by
                # [REQ-VIS-182], as a CD add records them.
                "artists": [(a["artist"]["id"], a["artist"].get("name") or "?", a["artist"].get("sort-name"))
                            for a in credit if isinstance(a.get("artist"), dict) and a["artist"].get("id")],
            })
    return out


def edition_facts(detail: dict) -> dict:
    """What tells one edition of an album from another, for a person choosing
    between them `[SPEC-FIN-030]`: two pressings of one title, both with the
    same tracks, differ only here. Each is `None` where MusicBrainz says
    nothing; `labels` needs `inc=labels`, which a response cached before
    2026-11-19 was fetched without."""
    media = detail.get("media") or []
    formats = [m.get("format") for m in media if m.get("format")]
    labels = [(li.get("label") or {}).get("name") for li in detail.get("label-info") or []]
    return {
        "country": detail.get("country"),
        "status": detail.get("status"),
        "barcode": detail.get("barcode") or None,
        "disambiguation": detail.get("disambiguation") or None,
        "discs": len(media) or None,
        "formats": ", ".join(dict.fromkeys(formats)) or None,
        "labels": ", ".join(dict.fromkeys(n for n in labels if n)) or None,
    }


def store_release_detail(conn: sqlite3.Connection, detail: dict) -> None:
    """The release and its full tracklist, in the exact `releases`/
    `release_recordings` shape `fetch_releases.py` already writes -- this
    tool fills them from the opposite direction (from a release, enumerating
    its recordings) but nothing downstream needs to know which direction
    filled a given row.
    """
    mbid = detail.get("id")
    if not mbid:
        return
    group = detail.get("release-group") or {}
    media = detail.get("media") or []
    # An upsert, not INSERT OR REPLACE: a replace would drop any column this
    # does not name, as the credit a CD add wrote `[REQ-VIS-183]`.
    conn.execute(
        "INSERT INTO releases "
        "(mbid, title, release_date, source, release_group, status, primary_type, "
        " secondary_types, country, track_count, artist_credit) "
        "VALUES (?1,?2,?3,'musicbrainz',?4,?5,?6,?7,?8,?9,?10) ON CONFLICT(mbid) DO UPDATE SET "
        "title = excluded.title, release_date = excluded.release_date, source = excluded.source, "
        "release_group = excluded.release_group, status = excluded.status, "
        "primary_type = excluded.primary_type, secondary_types = excluded.secondary_types, "
        "country = excluded.country, track_count = excluded.track_count, "
        "artist_credit = COALESCE(excluded.artist_credit, releases.artist_credit)",
        (mbid, detail.get("title"), detail.get("date"), group.get("id"), detail.get("status"),
         group.get("primary-type"), ",".join(group.get("secondary-types") or []) or None,
         detail.get("country"), sum(m.get("track-count") or 0 for m in media) or None,
         artist_credit_name(detail.get("artist-credit"))))
    for t in tracks_from_detail(detail):
        # The recording beside its tracklist row: until 2026-10-31 only the row was
        # written, and 34 of them named a recording the catalogue never held [SPEC-LNK-015].
        conn.execute(
            "INSERT OR IGNORE INTO recordings (mbid, title, length_ms, source) VALUES (?1,?2,?3,'musicbrainz')",
            (t["recording_mbid"], t["recording_title"] or t["title"] or t["recording_mbid"], t["length"]))
        # An upsert, as for the release: a replace reset `chosen` to 0 and
        # dropped the disc and the source, so a search that merely looked at a
        # release took back the edition a CD add or a person had settled on
        # [SPEC-FIN-040] -- and the finishing page searches freely.
        conn.execute(
            "INSERT INTO release_recordings (release_mbid, mbid, position, source, track_length_ms, disc) "
            "VALUES (?1,?2,?3,'musicbrainz',?4,?5) ON CONFLICT(release_mbid, mbid) DO UPDATE SET "
            "position = COALESCE(excluded.position, release_recordings.position), "
            "track_length_ms = COALESCE(excluded.track_length_ms, release_recordings.track_length_ms), "
            "disc = COALESCE(excluded.disc, release_recordings.disc)",
            (mbid, t["recording_mbid"], t["position"], t["length"], t["disc"]))


# ------------------------------------------------------------------ scoring --

def match_files_to_tracks(files: list, tracks: list) -> tuple:
    """Greedy, one release track at a time in tracklist order, each claiming
    the best still-unclaimed file. `[MATCH_THRESHOLD]` guards against a
    release with far more tracks than this folder has files claiming
    something merely because it was the least-bad option left.
    """
    claimed = set()
    matches = []
    def place(t):
        return (t.get("disc") or 1, t.get("position") if t.get("position") is not None else 999)

    for track in sorted(tracks, key=place):
        best_i, best_score, best_delta = None, -1.0, None
        for i, f in enumerate(files):
            if i in claimed:
                continue
            # The name floor is a hard gate, not a term in the sum: an
            # unrelated title (a bonus track, studio chatter, a hidden
            # track) must never be rescued over MATCH_THRESHOLD purely by
            # sharing a track number or a similar duration with some other
            # song entirely -- both of those are legitimate tiebreaks
            # *among plausible titles*, never evidence on their own.
            name_score = name_match(f.get("title") or "", track.get("title") or "")
            if name_score < NAME_FLOOR:
                continue
            score = name_score
            # The same place on the same disc, where both say which disc: a
            # track 3 tagged disc 2 is not disc 1's track 3 [SPEC-FIN-035].
            same_disc = not (f.get("disc_no") and track.get("disc")) or f["disc_no"] == track["disc"]
            if f.get("track_no") and track.get("position") and f["track_no"] == track["position"] and same_disc:
                score += TRACK_NO_BONUS
            delta = None
            if f.get("duration_ms") and track.get("length"):
                delta = abs(f["duration_ms"] - track["length"])
                score += 0.1 * max(0.0, 1.0 - delta / DURATION_WINDOW_MS)
            if score > best_score:
                best_i, best_score, best_delta = i, score, delta
        if best_i is not None and best_score >= MATCH_THRESHOLD:
            claimed.add(best_i)
            f = files[best_i]
            matches.append({
                "file": f["path"], "file_id": f["file_id"], "passage_ids": f["passage_ids"],
                "track_disc": track.get("disc"),
                "track_position": track.get("position"), "track_title": track.get("title"),
                "recording_mbid": track.get("recording_mbid"), "recording_title": track.get("recording_title"),
                "artist_mbid": track.get("artist_mbid"), "artist_name": track.get("artist_name"),
                "artists": track.get("artists") or [],
                "similarity": round(min(best_score, 1.0), 3), "duration_delta_ms": best_delta,
            })
    unmatched_files = [f["path"] for i, f in enumerate(files) if i not in claimed]
    matched = {place({"disc": m["track_disc"], "position": m["track_position"]}) for m in matches}
    unmatched_tracks = [t.get("title") for t in tracks if place(t) not in matched]
    return matches, unmatched_files, unmatched_tracks


def folder_score(files: list, matches: list) -> float:
    """Coverage (how much of the folder this release explains) times average
    match confidence -- both must be reasonable for the release to be a good
    account of this folder, so they multiply rather than average.
    """
    if not files or not matches:
        return 0.0
    avg_sim = sum(m["similarity"] for m in matches) / len(matches)
    coverage = len(matches) / len(files)
    return coverage * avg_sim


# --------------------------------------------------------------------- main --

def do_discover(conn: sqlite3.Connection, args, files: list) -> int:
    query = args.query or guess_query(files)
    if not query:
        say("no artist/album tag information to search with -- pass --query")
        if args.json:
            say(json.dumps({"ok": False, "error": "nothing to search with"}))
        return 1

    direct = release_id_in(query)
    if direct:
        # Not a search at all -- a specific release someone already found
        # and named, fetched and scored exactly like a search hit would be,
        # skipping the free-text endpoint a raw id would return nothing
        # from. Still just one candidate offered for acceptance, same as
        # any other: this does not shortcut the confirm-before-apply step.
        say(f"{len(files)} file(s) in {args.folder}; release {direct} named directly, not searched")
        hits = [{"id": direct}]
    else:
        say(f"{len(files)} file(s) in {args.folder}; searching MusicBrainz for {query!r}")
        try:
            hits = search_releases(query)
        except Exception as e:  # noqa: BLE001 - report and stop, like every other tool here
            say(f"MusicBrainz search failed: {e}")
            if args.json:
                say(json.dumps({"ok": False, "error": str(e)}))
            return 1

    scored = []
    for hit in hits[:MAX_CANDIDATES]:
        mbid = hit.get("id")
        if not mbid:
            continue
        try:
            detail = fetch_release_detail(conn, mbid)
        except Exception as e:  # noqa: BLE001
            say(f"  {mbid}: {e}")
            continue
        if not detail:
            continue
        store_release_detail(conn, detail)
        tracks = tracks_from_detail(detail)
        matches, unmatched_files, unmatched_tracks = match_files_to_tracks(files, tracks)
        scored.append({
            "mbid": mbid, "title": detail.get("title"),
            "artist": artist_credit_name(detail.get("artist-credit")),
            "date": detail.get("date"), "track_count": len(tracks),
            **edition_facts(detail),
            "score": round(folder_score(files, matches), 3),
            "matches": matches, "unmatched_files": unmatched_files,
            "unmatched_tracks": unmatched_tracks,
        })
        # No sleep: a request waits its own turn [SPEC-MBP-010], and a
        # release read from the cache made none.
    conn.commit()

    scored.sort(key=lambda c: -c["score"])
    say(f"\n{len(scored)} candidate release(s) scored")
    for c in scored:
        say(f"  {c['score']:.2f}  {c['title']!r} ({c['artist']}, {c['date']})  "
            f"{len(c['matches'])}/{c['track_count']} track(s) matched  {c['mbid']}")
    if not scored:
        say(f"could not fetch release {direct} -- check the id is right and MusicBrainz is reachable"
            if direct else "nothing found -- try --query with a hand-picked search")
    if args.json:
        say(json.dumps({"ok": True, "query": query, "candidates": scored}))
    return 0


def do_accept(conn: sqlite3.Connection, args, files: list) -> int:
    try:
        detail = fetch_release_detail(conn, args.accept)
    except Exception as e:  # noqa: BLE001
        say(f"could not fetch release {args.accept}: {e}")
        if args.json:
            say(json.dumps({"ok": False, "error": str(e)}))
        return 1
    if not detail:
        say(f"no such release: {args.accept}")
        if args.json:
            say(json.dumps({"ok": False, "error": "release not found"}))
        return 1
    store_release_detail(conn, detail)
    conn.commit()

    tracks = tracks_from_detail(detail)
    matches, unmatched_files, unmatched_tracks = match_files_to_tracks(files, tracks)
    score = folder_score(files, matches)
    files_by_id = {f["file_id"]: f for f in files}

    say(f"applying {detail.get('title')!r} ({args.accept}) to {args.folder}: "
        f"{len(matches)}/{len(files)} file(s) matched, score {score:.2f}")
    for m in matches:
        say(f"  {os.path.basename(m['file'])}  ->  {m['recording_title']} "
            f"(pos {m['track_position']}, similarity {m['similarity']})")
    if unmatched_files:
        say(f"  not matched, left untouched: {', '.join(os.path.basename(p) for p in unmatched_files)}")

    # A compare copy predating `[REQ-VIS-265]` entirely has no `listener_flags`
    # at all -- clearing is then simply nothing to do, the same reasoning
    # `apply_changes.py`'s own `clear_flags_ok` gate already uses.
    # `listener_flags` is LISTENER-side while this script runs with the
    # catalogue as `main`, so a bare `sqlite_master` here reports it absent
    # on every split installation and silently turns clearing off
    # `[IMPL-DBSPLIT-025]`.
    have = lempi_db.tables(conn)
    clear_flags_ok = "listener_flags" in have
    # `id_checks` is `fingerprint_ids.py`'s own AcoustID-fingerprint verdict,
    # a *different* identification method than a release-tracklist match --
    # this write never runs that check, so a stale row would sit there
    # naming the passage's PRE-accept id (`stored_mbid`) as if it were still
    # current. Lempi's own review queue reads `id_checks.stored_mbid`
    # directly, not `passage_recordings` (`player/src/db.rs`'s
    # `review_queue()`), so a passage this run just resolved would otherwise
    # keep surfacing there under its old id, captioned "no MusicBrainz id" --
    # true when the fingerprint pass ran, false now.
    clear_checks_ok = "id_checks" in have

    applied = cleared = rechecked = 0
    if args.commit:
        now = int(time.time())
        conn.execute("BEGIN IMMEDIATE")
        try:
            for m in matches:
                mbid = m["recording_mbid"]
                if not conn.execute("SELECT 1 FROM recordings WHERE mbid=?1", (mbid,)).fetchone():
                    conn.execute(
                        "INSERT INTO recordings (mbid, title, source) VALUES (?1,?2,?3)",
                        (mbid, m["recording_title"] or m["track_title"] or "?", SOURCE))
                # Every credited artist, with the sort name Browse sorts by, as
                # a CD add records them [REQ-VIS-182]; the first alone, named
                # only, until 2026-11-19.
                credited = m.get("artists") or (
                    [(m["artist_mbid"], m["artist_name"] or "?", None)] if m.get("artist_mbid") else [])
                for artist_mbid, name, sort_name in credited:
                    conn.execute(
                        "INSERT OR IGNORE INTO artists (mbid, name, sort_name, source) VALUES (?1,?2,?3,?4)",
                        (artist_mbid, name or "?", sort_name, SOURCE))
                    artist_sort.record(conn, artist_mbid, sort_name)
                    conn.execute(
                        "INSERT OR IGNORE INTO recording_artists (mbid, artist_mbid, weight, source) "
                        "VALUES (?1,?2,1.0,?3)", (mbid, artist_mbid, SOURCE))
                for n, passage_id in enumerate(m["passage_ids"]):
                    # Captured before the reassignment -- `clear_flags_for`
                    # needs the id a flag was plausibly set against, which is
                    # about to stop being this passage's own [SPEC-DF-112].
                    old_row = conn.execute(
                        "SELECT mbid FROM passage_recordings WHERE passage_id=?1", (passage_id,)).fetchone()
                    old_mbid = old_row[0] if old_row else None

                    conn.execute("DELETE FROM passage_recordings WHERE passage_id=?1", (passage_id,))
                    conn.execute(
                        "INSERT INTO passage_recordings (passage_id, mbid, weight, source) "
                        "VALUES (?1,?2,1.0,?3)", (passage_id, mbid, SOURCE))
                    if n == 0:
                        # One decision per file: it is the file that was
                        # matched, whichever of its passages took the id.
                        conn.execute(
                            "INSERT INTO ingest_decisions (audio_md5, stage, outcome, confidence, detail, decided_at) "
                            "VALUES (?1,'folder_release_match',?2,?3,?4,?5)",
                            (files_by_id[m["file_id"]]["audio_md5"], args.accept, round(score, 3),
                             json.dumps({"folder": args.folder, "matched": len(matches),
                                         "total_files": len(files), "match": m}), now))
                    applied += 1
                    if clear_flags_ok:
                        before = conn.total_changes
                        clear_flags_for(conn, "id_review", passage_id,
                                        {"target": {"mbid": mbid}, "baseline": {"mbid": old_mbid}})
                        cleared += conn.total_changes - before
                    if clear_checks_ok:
                        rechecked += conn.execute(
                            "DELETE FROM id_checks WHERE passage_id=?1", (passage_id,)).rowcount
            # The edition, recorded as a CD add records the disc in hand
            # [SPEC-FIN-040]: the chosen release of each matched recording, at
            # its place on its disc, and the album each matched file is shown
            # under -- `folder:chosen`, which the tools that guess leave
            # standing. An unmatched file is left as it was here too.
            linked = ingest_cd.record_release(
                conn, detail, [(m["track_position"], m["recording_mbid"]) for m in matches],
                files=[files_by_id[m["file_id"]]["audio_md5"] for m in matches],
                source=release_sources.FOLDER_CHOSEN) if matches else 0
        except sqlite3.Error as e:
            conn.rollback()
            say(f"refused: {e}")
            if args.json:
                say(json.dumps({"ok": False, "error": str(e)}))
            return 1
        conn.commit()
        say(f"committed: {applied} passage(s) updated"
            + (f", {cleared} flag(s) cleared" if clear_flags_ok else "")
            + (f", {rechecked} stale fingerprint-check row(s) cleared" if clear_checks_ok else "")
            + f"; the edition recorded for {len(matches)} file(s), {linked} recording(s) linked to it")
    else:
        linked = 0
        say("nothing was written. Re-run with --commit to do it.")

    if args.json:
        say(json.dumps({"ok": True, "release": args.accept, "matched": len(matches),
                        "rechecked": rechecked,
                        "total_files": len(files), "applied": applied, "cleared": cleared,
                        "linked": linked, "unmatched_files": unmatched_files}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("folder")
    ap.add_argument("--query", help='override the guessed search, e.g. \'release:"X" AND artist:"Y"\'')
    ap.add_argument("--accept", metavar="MBID",
                     help="apply this release's matches instead of discovering candidates")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--json", action="store_true",
                     help="also print one final JSON summary line, for a caller "
                          "(the Vipunen console's suggest-release/accept-release jobs) "
                          "rather than a person")
    args = ap.parse_args()

    # The catalogue is `main` and written; the listener half is written too,
    # because an accept clears the flags set on a passage's old id
    # [SPEC-DF-112], and those live there [IMPL-DBSPLIT-025]. Attached
    # read-only, every accept on the hub was refused whole until 2026-11-19
    # ("attempt to write a readonly database"), since it writes in one
    # transaction. Discovery writes neither half's flags, and is unchanged.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True,
                            peer_writable=bool(args.accept and args.commit))
    conn.execute("PRAGMA busy_timeout = 5000")
    ensure_schema(conn)

    files = gather_folder_files(conn, args.folder)
    if not files:
        say(f"no files found directly in {args.folder}")
        if args.json:
            say(json.dumps({"ok": False, "error": "no files in folder"}))
        return 1

    if args.accept:
        return do_accept(conn, args, files)
    return do_discover(conn, args, files)


if __name__ == "__main__":
    sys.exit(main())
