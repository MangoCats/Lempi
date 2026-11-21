#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Record an album MusicBrainz does not have, as a person names it `[SPEC-FIN-080]`.

The other half of the console's *Finish an album* page. There, an edition is
picked from MusicBrainz and everything follows from it `[SPEC-FIN-030]`. Music
published only on Jamendo, Bandcamp or archive.org has no such edition, and
until 2026-11-20 its folder could not be finished at all: the job stopped at
its first step, every track stayed a placeholder, and the artist was only a
tag. This records what a person confirms instead -- the album, its artist,
its date, each track's title and artists -- in the same tables an edition
from MusicBrainz is recorded in, so nothing downstream reads it differently:

- a release, `local:release:<hex>`, with its credit and date `[SPEC-FIN-090]`;
- each artist, the library's own when exactly one has that name, otherwise
  `local:artist:<hex>` from the name, so one artist across albums is one;
- each track's recording keeps its `local:audio:` id `[SPEC-FIN-090]`, and
  takes the title and the credits the person gave;
- the release as each recording's chosen one, at its place, and each file
  shown under it, as `folder:local`, which the tools that guess leave
  standing (`release_sources.py`);
- the cover found beside the files or in them `[SPEC-FIN-095]`, kept on the
  release as the player looks one up, where the release has none.

None of these ids is MusicBrainz-shaped, so nothing asks MusicBrainz or the
Cover Art Archive about them. A file whose recording *is* a MusicBrainz one is
left as it is and listed: it belongs to an edition that exists.

Rehearses unless `--commit`. The album's fields come from `--title`,
`--artist` and `--date`, or a `--spec` JSON file (what the console sends),
with per-track overrides; anything not said is taken from the files' tags.

    python tools/local_edition.py data/library.db "C:/Music/stellarmusic/Future Sound" \\
        --title "Future Sound: Stellar Piano and Instrumental Music" --artist stellarmusic --date 2014
    python tools/local_edition.py data/library.db FOLDER --spec edition.json --commit --json
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import release_sources  # noqa: E402  -- folder:local is settled [SPEC-FIN-040]
from finish_folder import LOCAL_RELEASE, MBID, same_folder  # noqa: E402  -- one folder rule, one id shape

LOCAL_ARTIST = "local:artist:"
SOURCE = "local:edition"
DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
# Several artists in one field, as the form takes them: "stellarmusic; Laura Aiisha".
ARTIST_SPLIT = re.compile(r"\s*;\s*")
# The first credit is the one Browse and history show `ARTIST_EXPR`, which
# orders by weight; the others are credited, and behind it.
LEAD_WEIGHT, FEATURED_WEIGHT = 1.0, 0.5

DDL = """
CREATE TABLE IF NOT EXISTS artists (mbid TEXT PRIMARY KEY, name TEXT NOT NULL, sort_name TEXT,
    source TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recording_artists (mbid TEXT NOT NULL, artist_mbid TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL, PRIMARY KEY (mbid, artist_mbid)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS releases (mbid TEXT PRIMARY KEY, title TEXT NOT NULL, release_date TEXT,
    source TEXT NOT NULL, release_group TEXT, status TEXT, primary_type TEXT, secondary_types TEXT,
    country TEXT, track_count INTEGER, artist_credit TEXT);
CREATE TABLE IF NOT EXISTS release_recordings (release_mbid TEXT NOT NULL, mbid TEXT NOT NULL,
    position INTEGER, source TEXT NOT NULL, track_length_ms INTEGER, chosen INTEGER DEFAULT 0,
    disc INTEGER, PRIMARY KEY (release_mbid, mbid)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS file_releases (audio_md5 TEXT PRIMARY KEY, release_mbid TEXT NOT NULL,
    source TEXT NOT NULL, decided_at TEXT);
CREATE TABLE IF NOT EXISTS cover_art (release_mbid TEXT PRIMARY KEY, front BLOB, back BLOB,
    source TEXT NOT NULL, fetched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ingest_decisions (decision_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL,
    stage TEXT NOT NULL, outcome TEXT NOT NULL, confidence REAL, detail BLOB, decided_at TEXT NOT NULL);
"""
# An older library's tables, short of what this writes: the same additions
# the release tools make.
ADDED = (("releases", "artist_credit", "TEXT"), ("releases", "track_count", "INTEGER"),
         ("releases", "primary_type", "TEXT"), ("release_recordings", "chosen", "INTEGER DEFAULT 0"),
         ("release_recordings", "track_length_ms", "INTEGER"), ("release_recordings", "disc", "INTEGER"))


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(str(text).encode(enc, "replace").decode(enc), flush=True)


def _hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:32]


def is_local_release(rid: str | None) -> bool:
    return bool(rid and rid.startswith(LOCAL_RELEASE))


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    for table, col, kind in ADDED:
        if col not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")


def folder_files(conn: sqlite3.Connection, folder: str) -> list[dict]:
    """The files directly in `folder`, with their tags and the recording their
    passages carry: the radio passage's, else the album passage's."""
    folder = os.path.normpath(folder)
    out = []
    for fid, md5, path, dur, title, artist, album, track_no, disc_no in conn.execute(
            "SELECT f.file_id, f.audio_md5, f.path, f.duration_ms, t.title, t.artist, t.album, t.track_no, "
            "       t.disc_no FROM files f LEFT JOIN file_tags t ON t.file_id = f.file_id "
            "WHERE f.path LIKE ?1 ORDER BY f.path", (folder.rstrip("\\/") + "%",)):
        if not same_folder(path, folder):
            continue
        rec = conn.execute(
            "SELECT pr.mbid FROM passages p JOIN passage_recordings pr ON pr.passage_id = p.passage_id "
            "WHERE p.file_id = ?1 AND p.kind IN ('radio', 'album') ORDER BY p.kind = 'album', p.start_ms "
            "LIMIT 1", (fid,)).fetchone()
        out.append({"file_id": fid, "audio_md5": md5, "path": path, "duration_ms": dur,
                    "tag_title": title, "tag_artist": artist, "tag_album": album,
                    "track_no": track_no, "disc_no": disc_no, "recording": rec[0] if rec else None})
    return out


def majority(values) -> str | None:
    values = [v.strip() for v in values if v and v.strip()]
    return collections.Counter(values).most_common(1)[0][0] if values else None


def resolve_artist(conn: sqlite3.Connection, name: str) -> tuple[str, bool]:
    """`(artist id, already in the library)`. The library's own artist when
    exactly one has this name; otherwise one made from the name, the same on
    every node and for every album that names it."""
    own = LOCAL_ARTIST + _hex(name.casefold())
    rows = [m for (m,) in conn.execute("SELECT mbid FROM artists WHERE name = ?1 COLLATE NOCASE", (name,))]
    if own in rows:
        return own, True
    if len(rows) == 1:
        return rows[0], True
    return own, False


def plan(conn: sqlite3.Connection, folder: str, spec: dict) -> dict:
    """What would be written, or why nothing can be. `spec` is `{title,
    artist, date?, tracks?: [{audio_md5, position?, disc?, title?, artist?}]}`;
    a track not named in `tracks` is taken from its tags, and when `tracks`
    is given, a placeholder file it leaves out is left as it is."""
    files = folder_files(conn, folder)
    if not files:
        return {"ok": False, "error": f"no catalogued file lies directly in {folder}"}
    title = (spec.get("title") or majority(f["tag_album"] for f in files) or "").strip()
    album_artist = (spec.get("artist") or majority(f["tag_artist"] for f in files) or "").strip()
    date = (spec.get("date") or "").strip() or None
    if not title:
        return {"ok": False, "error": "the album needs a title"}
    if not album_artist:
        return {"ok": False, "error": "the album needs an artist"}
    if date and not DATE.match(date):
        return {"ok": False, "error": f"a date is YYYY, YYYY-MM or YYYY-MM-DD, not {date!r}"}

    given = spec.get("tracks")
    named = {t.get("audio_md5"): t for t in given or []}
    unknown = [m for m in named if m not in {f["audio_md5"] for f in files}]
    if unknown:
        return {"ok": False, "error": f"{len(unknown)} track(s) name no file in this folder"}
    skipped, chosen = [], []
    for f in files:
        if f["recording"] and MBID.match(f["recording"]):
            skipped.append({"file": f["path"], "why": "a MusicBrainz recording: left as it is"})
        elif not f["recording"]:
            skipped.append({"file": f["path"], "why": "no passage to name"})
        elif given is not None and f["audio_md5"] not in named:
            skipped.append({"file": f["path"], "why": "left out of the edition"})
        else:
            chosen.append(f)
    if not chosen:
        return {"ok": False, "error": "no file here is a placeholder to name", "skipped": skipped}

    tracks = []
    for f in chosen:
        t = named.get(f["audio_md5"], {})
        # A track the person was shown takes the album's artist where they left
        # its own blank; one they were not, its tag's.
        names = [n for n in ARTIST_SPLIT.split(
            (t.get("artist") or (album_artist if t else f["tag_artist"]) or album_artist).strip()) if n]
        tracks.append({"file": f["path"], "audio_md5": f["audio_md5"], "recording": f["recording"],
                       "duration_ms": f["duration_ms"],
                       "position": t["position"] if t.get("position") is not None else f["track_no"],
                       "disc": t["disc"] if t.get("disc") is not None else f["disc_no"],
                       "title": (t.get("title") or f["tag_title"]
                                 or os.path.splitext(os.path.basename(f["path"]))[0]).strip(),
                       "artists": names or [album_artist]})
    # A track with no number takes the next after the highest, in file order:
    # a release's track with none cannot be placed.
    top = max((t["position"] for t in tracks if isinstance(t["position"], int)), default=0)
    for t in tracks:
        if not isinstance(t["position"], int):
            top += 1
            t["position"] = top
    seen = collections.Counter((t["disc"] or 1, t["position"]) for t in tracks)
    twice = sorted(k for k, n in seen.items() if n > 1)
    if twice:
        where = ", ".join(f"{d}-{p}" if d != 1 else str(p) for d, p in twice)
        return {"ok": False, "error": f"two tracks share a place: {where}"}
    tracks.sort(key=lambda t: (t["disc"] or 1, t["position"]))

    # The release: the one these files are already shown under, when it is a
    # local edition -- so naming it again corrects it rather than making a
    # second -- else one made from the files, the same wherever they are.
    held = collections.Counter(r for (r,) in conn.execute(
        f"SELECT release_mbid FROM file_releases WHERE audio_md5 IN ({','.join('?' * len(tracks))})",
        [t["audio_md5"] for t in tracks]) if is_local_release(r)) \
        if lempi_db.has_table(conn, "file_releases") else collections.Counter()
    release = held.most_common(1)[0][0] if held else LOCAL_RELEASE + _hex("\n".join(
        sorted(t["audio_md5"] for t in tracks)))

    artists: dict[str, dict] = {}
    for name in [album_artist] + [n for t in tracks for n in t["artists"]]:
        if name.casefold() not in artists:
            aid, existing = resolve_artist(conn, name) if lempi_db.has_table(conn, "artists") \
                else (LOCAL_ARTIST + _hex(name.casefold()), False)
            artists[name.casefold()] = {"name": name, "id": aid, "existing": existing}
    for t in tracks:
        t["credits"] = [artists[n.casefold()]["id"] for n in t["artists"]]
    return {"ok": True, "folder": os.path.normpath(folder), "release": release, "title": title,
            "artist": album_artist, "date": date, "tracks": tracks, "skipped": skipped,
            "artists": list(artists.values()), "files": len(files)}


def find_cover(tracks: list[dict]):
    """The picture beside the files, else the first one a file carries, as
    `induct_covers.py` would take it -- a folder of one album, so its picture
    is this release's `[SPEC-COV-020]`."""
    import induct_covers
    counts = collections.Counter()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for t in tracks:
        got = induct_covers.found(t["file"], True, now, counts)
        if got:
            return got
    return None


def write(conn: sqlite3.Connection, p: dict) -> dict:
    """Record the plan, in one transaction. Returns what it did."""
    # Each table's own convention: a decision in epoch seconds, as
    # suggest_release.py writes it; a file's release as an ISO stamp.
    now = int(time.time())
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
    rel = p["release"]
    recs = [t["recording"] for t in p["tracks"]]
    md5s = [t["audio_md5"] for t in p["tracks"]]
    cover = None
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO releases (mbid, title, release_date, source, primary_type, track_count, artist_credit) "
            "VALUES (?1,?2,?3,?4,'Album',?5,?6) ON CONFLICT(mbid) DO UPDATE SET title = excluded.title, "
            "release_date = excluded.release_date, track_count = excluded.track_count, "
            "artist_credit = excluded.artist_credit",
            (rel, p["title"], p["date"], SOURCE, len(p["tracks"]), p["artist"]))
        for a in p["artists"]:
            conn.execute("INSERT OR IGNORE INTO artists (mbid, name, source) VALUES (?1,?2,?3)",
                         (a["id"], a["name"], SOURCE))
        for t in p["tracks"]:
            rec = t["recording"]
            conn.execute("UPDATE recordings SET title = ?2 WHERE mbid = ?1", (rec, t["title"]))
            # The person's credits are the recording's: a placeholder has no
            # other source that could know better.
            conn.execute("DELETE FROM recording_artists WHERE mbid = ?1", (rec,))
            for n, aid in enumerate(dict.fromkeys(t["credits"])):
                conn.execute("INSERT INTO recording_artists (mbid, artist_mbid, weight, source) "
                             "VALUES (?1,?2,?3,?4)", (rec, aid, LEAD_WEIGHT if n == 0 else FEATURED_WEIGHT, SOURCE))
            # This release names its album, and no other does `[SPEC-FIN-040]`.
            conn.execute("UPDATE release_recordings SET chosen = 0 WHERE mbid = ?1 AND release_mbid <> ?2",
                         (rec, rel))
            conn.execute(
                "INSERT INTO release_recordings (release_mbid, mbid, position, source, track_length_ms, chosen, "
                "disc) VALUES (?1,?2,?3,?4,?5,1,?6) ON CONFLICT(release_mbid, mbid) DO UPDATE SET "
                "position = excluded.position, source = excluded.source, chosen = 1, disc = excluded.disc, "
                "track_length_ms = excluded.track_length_ms",
                (rel, rec, t["position"], release_sources.FOLDER_LOCAL, t["duration_ms"], t["disc"]))
            conn.execute(
                "INSERT INTO file_releases (audio_md5, release_mbid, source, decided_at) VALUES (?1,?2,?3,?4) "
                "ON CONFLICT(audio_md5) DO UPDATE SET release_mbid = excluded.release_mbid, "
                "source = excluded.source, decided_at = excluded.decided_at",
                (t["audio_md5"], rel, release_sources.FOLDER_LOCAL, stamp))
            conn.execute(
                "INSERT INTO ingest_decisions (audio_md5, stage, outcome, confidence, detail, decided_at) "
                "VALUES (?1,'folder_local_edition',?2,1.0,?3,?4)",
                (t["audio_md5"], rel, json.dumps({"folder": p["folder"], "title": t["title"],
                                                  "artists": t["artists"], "position": t["position"],
                                                  "disc": t["disc"]}), str(now)))
        # A track taken out of the edition since it was last named leaves it.
        marks = ",".join("?" * len(recs))
        dropped = conn.execute(f"DELETE FROM release_recordings WHERE release_mbid = ?1 AND mbid NOT IN ({marks})",
                               (rel, *recs)).rowcount
        conn.execute(f"DELETE FROM file_releases WHERE release_mbid = ?1 AND audio_md5 NOT IN "
                     f"({','.join('?' * len(md5s))})", (rel, *md5s))
        # The cover, where the release has none: the catalogue's own is never
        # replaced by a found one `[SPEC-COV-010]`.
        held = conn.execute("SELECT COALESCE(LENGTH(front), 0) FROM cover_art WHERE release_mbid = ?1",
                            (rel,)).fetchone()
        if not held or not held[0]:
            got = find_cover(p["tracks"])
            if got:
                front, back, source, fetched = got
                conn.execute(
                    "INSERT INTO cover_art (release_mbid, front, back, source, fetched_at) VALUES (?1,?2,?3,?4,?5) "
                    "ON CONFLICT(release_mbid) DO UPDATE SET front = excluded.front, "
                    "back = COALESCE(cover_art.back, excluded.back), source = excluded.source, "
                    "fetched_at = excluded.fetched_at", (rel, front, back, source, fetched))
                cover = source
        else:
            cover = "held already"
    except sqlite3.Error:
        conn.rollback()
        raise
    conn.commit()
    return {"cover": cover, "dropped": dropped}


MAX_TRACKS = 500
MAX_TEXT = 500
MD5 = re.compile(r"^[0-9a-f]{32}$")


def clean_spec(raw) -> tuple[dict | None, str | None]:
    """A spec as the console receives it, reduced to the fields this tool
    reads and checked: `(spec, None)`, or `(None, why)`. Only its shape is
    judged here; whether it fits the folder is `plan`'s to say."""
    if not isinstance(raw, dict):
        return None, "the edition must be an object"

    def text(v, what):
        if v is None:
            return ""
        if not isinstance(v, str) or len(v) > MAX_TEXT:
            raise ValueError(f"{what} must be text of at most {MAX_TEXT} characters")
        return v.strip()

    def place(v, what):
        if v is None or v == "":
            return None
        if isinstance(v, bool) or not isinstance(v, int) or not 0 < v < 1000:
            raise ValueError(f"{what} must be a whole number from 1 to 999")
        return v

    try:
        spec = {"title": text(raw.get("title"), "the title"), "artist": text(raw.get("artist"), "the artist"),
                "date": text(raw.get("date"), "the date")}
        if not spec["title"] or not spec["artist"]:
            return None, "the album needs a title and an artist"
        if spec["date"] and not DATE.match(spec["date"]):
            return None, "a date is YYYY, YYYY-MM or YYYY-MM-DD"
        tracks = raw.get("tracks")
        if tracks is not None:
            if not isinstance(tracks, list) or len(tracks) > MAX_TRACKS:
                return None, f"tracks must be a list of at most {MAX_TRACKS}"
            spec["tracks"] = []
            for i, t in enumerate(tracks, 1):
                if not isinstance(t, dict) or not MD5.match(str(t.get("audio_md5") or "")):
                    return None, f"track {i} names no file"
                spec["tracks"].append({"audio_md5": t["audio_md5"], "title": text(t.get("title"), f"track {i}'s title"),
                                       "artist": text(t.get("artist"), f"track {i}'s artist"),
                                       "position": place(t.get("position"), f"track {i}'s number"),
                                       "disc": place(t.get("disc"), f"track {i}'s disc")})
    except ValueError as e:
        return None, str(e)
    return spec, None


def load_spec(args) -> dict:
    spec: dict = {}
    if args.spec:
        with open(args.spec, encoding="utf-8") as fh:
            spec = json.load(fh)
    for key in ("title", "artist", "date"):
        if getattr(args, key) is not None:
            spec[key] = getattr(args, key)
    return spec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("db")
    ap.add_argument("folder")
    ap.add_argument("--title")
    ap.add_argument("--artist")
    ap.add_argument("--date", help="YYYY, YYYY-MM or YYYY-MM-DD")
    ap.add_argument("--spec", help="a JSON file: {title, artist, date, tracks: [...]}, as the console sends")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--json", action="store_true", help="end with one JSON summary line, for a job")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    def done(result: dict, code: int) -> int:
        if args.json:
            say(json.dumps(result, ensure_ascii=False))
        return code

    try:
        spec = load_spec(args)
    except (OSError, ValueError) as e:
        say(f"could not read the spec: {e}")
        return done({"ok": False, "error": f"could not read the spec: {e}"}, 1)
    if args.spec:
        spec, why = clean_spec(spec)
        if spec is None:
            say(f"refused: {why}")
            return done({"ok": False, "error": why}, 1)
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=bool(args.commit))
    conn.execute("PRAGMA busy_timeout = 5000")
    p = plan(conn, args.folder, spec)
    if not p["ok"]:
        say(f"refused: {p['error']}")
        return done({"ok": False, "error": p["error"]}, 1)

    say(f"a local edition: \u201c{p['title']}\u201d by {p['artist']}" + (f", {p['date']}" if p["date"] else "")
        + f" -- {p['release']}, not on MusicBrainz")
    for a in p["artists"]:
        say(f"  artist {a['name']}: " + (f"the library's own, {a['id']}" if a["existing"] else f"new, {a['id']}"))
    for t in p["tracks"]:
        say(f"  {(str(t['disc']) + '-') if t['disc'] and t['disc'] != 1 else ''}{t['position']:>2}. {t['title']}"
            f"  -- {'; '.join(t['artists'])}  ({os.path.basename(t['file'])})")
    for s in p["skipped"]:
        say(f"  left as it is: {os.path.basename(s['file'])} -- {s['why']}")
    summary = {"ok": True, "local": True, "release": p["release"], "title": p["title"], "artist": p["artist"],
               "named": len(p["tracks"]), "applied": len(p["tracks"]), "files": p["files"],
               "skipped": p["skipped"], "artists": p["artists"], "committed": False, "cover": None,
               "matched": len(p["tracks"])}
    if not args.commit:
        say("nothing was written. Re-run with --commit to do it.")
        return done(summary, 0)
    ensure_schema(conn)
    try:
        did = write(conn, p)
    except sqlite3.Error as e:
        say(f"refused: {e}")
        return done({"ok": False, "error": str(e)}, 1)
    summary.update(committed=True, cover=did["cover"])
    say(f"committed: {len(p['tracks'])} track(s) named under the local edition"
        + (f", {did['dropped']} left out since removed from it" if did["dropped"] else "")
        + "; cover " + (did["cover"] or "not found beside or in the files"))
    return done(summary, 0)


if __name__ == "__main__":
    sys.exit(main())
