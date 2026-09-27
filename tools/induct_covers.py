#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bring the covers found beside the music into the catalogue [SPEC047].

Lempi shows covers from the catalogue alone [SPEC-COV-010]. This fills the
catalogue from the covers already on disk, where it has none: for each file,
the picture beside it (folder.jpg, cover.jpg, ...), else the picture embedded
in the file itself [SPEC-COV-020]. The files on disk are read and never
changed -- they stay where other players find them [SPEC-COV-030].

**Keyed as the player looks a cover up.** A file with a chosen MusicBrainz
release gets its cover on that release, in `cover_art`. A file with none -- an
unidentified or tags-only file -- gets one of its own, in `file_art`, by its
signature [SPEC-COV-040]. A release or file that already has a front cover is
left alone: the catalogue's own is never replaced by a found one.

**The catalogue keeps a display copy** [SPEC-COV-050]. A found picture over
1 MB, or over 1200 px on its long side, is stored as a 1200 px JPEG; the rest
byte for byte. Measured 2026-09-27: 45 scans of 2 to 14 MB made 218 MB of the
315 MB found, where the catalogue's covers average 100 KB -- and a phone
receives these in bundles and decodes them to show them. The original on disk
is not touched.

**A folder's picture counts only where the folder is one album's.** A folder
holds one cover, so a folder of several albums would give some of its songs the
wrong picture: a picture beside the audio is taken for a release only when every
catalogued file in that folder has that release, and for a file with no release
only when every file there shares its album tag. Otherwise the file's own
embedded picture is the source.

Usage:
  python tools/induct_covers.py <library.db> [--write]

Without --write it reports what it would take and writes nothing.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]

# The names a player takes for a folder's cover, case-insensitively: the same
# lists as lempi_core::bundle::SIBLING_FRONT / SIBLING_BACK.
SIBLING_FRONT = ["folder.jpg", "cover.jpg", "front.jpg", "album.jpg",
                 "folder.png", "cover.png", "front.png", "albumart.jpg"]
SIBLING_BACK = ["back.jpg", "back.png", "backcover.jpg", "folder-back.jpg"]
# lempi_core::tags::MIN_ART_BYTES: anything smaller is not a picture.
MIN_ART_BYTES = 256
# The display copy [SPEC-COV-050].
DISPLAY_PX = 1200
DISPLAY_BYTES = 1_000_000

FILE_ART_DDL = """CREATE TABLE IF NOT EXISTS file_art (
    audio_md5  TEXT PRIMARY KEY,
    front      BLOB,
    back       BLOB,
    source     TEXT NOT NULL,
    fetched_at TEXT NOT NULL
)"""
COVER_ART_DDL = """CREATE TABLE IF NOT EXISTS cover_art (
    release_mbid TEXT PRIMARY KEY,
    front        BLOB,
    back         BLOB,
    source       TEXT NOT NULL,
    fetched_at   TEXT NOT NULL
)"""


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(str(text).encode(enc, "replace").decode(enc), flush=True)


def sibling(folder: str, names: list[str]) -> tuple[bytes, str] | None:
    """The first of `names` present in `folder`, case-insensitively, that is a
    picture: (bytes, its file name)."""
    try:
        present = {e.lower(): e for e in os.listdir(folder)}
    except OSError:
        return None
    for n in names:
        real = present.get(n)
        if real:
            try:
                with open(os.path.join(folder, real), "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            if len(data) >= MIN_ART_BYTES:
                return data, real
    return None


def display_copy(data: bytes) -> tuple[bytes, str]:
    """What the catalogue keeps of a found picture [SPEC-COV-050], and a note
    of what was done: a large one shrunk to DISPLAY_PX on its long side as a
    JPEG, anything else as found. One that will not open is kept as found --
    the player decides whether it can show it."""
    import io
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        w, h = img.size
    except Exception:
        return data, ""
    if max(w, h) <= DISPLAY_PX and len(data) <= DISPLAY_BYTES:
        return data, ""
    img = img.convert("RGB")
    img.thumbnail((DISPLAY_PX, DISPLAY_PX), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88, optimize=True)
    return out.getvalue(), f":shrunk {w}x{h}->{img.size[0]}x{img.size[1]}"


def embedded(path: str) -> bytes | None:
    """The file's own front cover, or failing that its first picture -- as the
    player reads one [REQ-VIS-170]."""
    try:
        import mutagen
        from mutagen.flac import Picture
        f = mutagen.File(path)
    except Exception:
        return None
    if f is None:
        return None
    pics: list[tuple[int, bytes]] = []
    tags = getattr(f, "tags", None)
    if tags is not None and hasattr(tags, "getall"):          # ID3
        pics += [(p.type, p.data) for p in tags.getall("APIC")]
    for p in getattr(f, "pictures", []) or []:                 # FLAC
        pics.append((p.type, p.data))
    if tags is not None and hasattr(tags, "get"):
        for c in tags.get("covr", []) or []:                   # MP4
            pics.append((3, bytes(c)))
        for b64 in tags.get("metadata_block_picture", []) or []:  # Ogg
            try:
                import base64
                p = Picture(base64.b64decode(b64))
                pics.append((p.type, p.data))
            except Exception:
                pass
    pics = [(t, d) for t, d in pics if d and len(d) >= MIN_ART_BYTES]
    if not pics:
        return None
    front = [d for t, d in pics if t == 3]
    return front[0] if front else pics[0][1]


def plan(conn) -> tuple[dict, dict, dict]:
    """What to take: {release: (front, back, source)}, {audio_md5: (...)},
    and counts of what was looked at and passed over."""
    has = lambda t: conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()  # noqa: E731
    covered_rel = {r[0] for r in conn.execute(
        "SELECT release_mbid FROM cover_art WHERE front IS NOT NULL")} if has("cover_art") else set()
    covered_file = {r[0] for r in conn.execute(
        "SELECT audio_md5 FROM file_art WHERE front IS NOT NULL")} if has("file_art") else set()
    chosen = "rr.chosen = 1" if "chosen" in {r[1] for r in conn.execute(
        "PRAGMA table_info(release_recordings)")} else "1"
    rows = conn.execute(f"""
        SELECT f.path, f.audio_md5, t.album,
               (SELECT rr.release_mbid FROM passages p
                  JOIN passage_recordings pr ON pr.passage_id = p.passage_id
                  JOIN release_recordings rr ON rr.mbid = pr.mbid AND {chosen}
                 WHERE p.file_id = f.file_id
                 ORDER BY rr.release_mbid LIMIT 1)
          FROM files f LEFT JOIN file_tags t ON t.file_id = f.file_id
         ORDER BY f.path""").fetchall()
    # What each folder holds, to judge whether its picture is one album's.
    folders: dict[str, list[tuple]] = {}
    for path, md5, album, rel in rows:
        folders.setdefault(os.path.dirname(path), []).append((rel, (album or "").strip().lower()))
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    by_rel: dict[str, tuple] = {}
    by_file: dict[str, tuple] = {}
    counts = {"files": len(rows), "already": 0, "none_found": 0, "folder": 0, "embedded": 0, "shrunk": 0}
    for path, md5, album, rel in rows:
        if rel:
            if rel in covered_rel or rel in by_rel:
                counts["already"] += 1
                continue
        elif md5 in covered_file or md5 in by_file:
            counts["already"] += 1
            continue
        folder = os.path.dirname(path)
        members = folders[folder]
        alone = (all(r == rel for r, _ in members) if rel
                 else all(r is None and a == (album or "").strip().lower() and a for r, a in members))
        front = back = None
        source = ""
        if alone:
            s = sibling(folder, SIBLING_FRONT)
            if s:
                front, source = s[0], f"found:folder:{s[1]}"
                b = sibling(folder, SIBLING_BACK)
                back = b[0] if b else None
        if front is None and os.path.isfile(path):
            e = embedded(path)
            if e:
                front, source = e, "found:embedded"
        if front is None:
            counts["none_found"] += 1
            continue
        counts["folder" if source.startswith("found:folder") else "embedded"] += 1
        front, note = display_copy(front)
        if back is not None:
            back, _ = display_copy(back)
        if note:
            counts["shrunk"] += 1
        entry = (front, back, source + note, now)
        if rel:
            by_rel[rel] = entry
        else:
            by_file[md5] = entry
    return by_rel, by_file, counts


def main(argv: list[str]) -> int:
    flags = [a for a in argv if a.startswith("-")]
    paths = [a for a in argv if not a.startswith("-")]
    unknown = [f for f in flags if f != "--write"]
    if len(paths) != 1 or unknown:
        say(__doc__)
        if unknown:
            say(f"unknown option(s): {' '.join(unknown)}")
        return 2
    write = "--write" in flags
    conn = lempi_db.connect(paths[0], lempi_db.ROLE_LIBRARY, writable=write)
    by_rel, by_file, c = plan(conn)
    say(f"files looked at: {c['files']}")
    say(f"  their release or file already has a cover: {c['already']}")
    say(f"  a cover found beside the audio: {c['folder']}; embedded in the file: {c['embedded']}")
    say(f"  nothing found: {c['none_found']}")
    say(f"  kept as a display copy, being large: {c['shrunk']}")
    say(f"to take: {len(by_rel)} release cover(s), {len(by_file)} file cover(s) "
        f"({sum(len(v[0]) + len(v[1] or b'') for v in list(by_rel.values()) + list(by_file.values())) / 1e6:.1f} MB)")
    if not write:
        say("\n(dry run -- pass --write to apply)")
        return 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(COVER_ART_DDL.replace("cover_art", "main.cover_art", 1))
        conn.execute(FILE_ART_DDL.replace("file_art", "main.file_art", 1))
        for rel, (front, back, source, now) in by_rel.items():
            # A row with a back and no front keeps its back.
            conn.execute(
                "INSERT INTO main.cover_art (release_mbid, front, back, source, fetched_at) VALUES (?1,?2,?3,?4,?5) "
                "ON CONFLICT(release_mbid) DO UPDATE SET front = excluded.front, "
                "back = COALESCE(cover_art.back, excluded.back), source = excluded.source, "
                "fetched_at = excluded.fetched_at WHERE cover_art.front IS NULL",
                (rel, front, back, source, now))
        for md5, (front, back, source, now) in by_file.items():
            conn.execute(
                "INSERT INTO main.file_art (audio_md5, front, back, source, fetched_at) VALUES (?1,?2,?3,?4,?5) "
                "ON CONFLICT(audio_md5) DO NOTHING", (md5, front, back, source, now))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    n_rel = conn.execute("SELECT COUNT(*) FROM main.cover_art WHERE front IS NOT NULL").fetchone()[0]
    n_file = conn.execute("SELECT COUNT(*) FROM main.file_art WHERE front IS NOT NULL").fetchone()[0]
    say(f"written. The catalogue now holds {n_rel} release cover(s) and {n_file} file cover(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
