#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fetch missing cover art from the Cover Art Archive `[REQ-VIS-170]`.

Measured on this library: 1,986 files carry no embedded picture, and 1,656 of
them (83%) already have a `folder.jpg` beside them -- the player now looks
there and needs nothing from the network. Those folders are asked about
anyway, and deliberately: `folder.jpg` is one image, and the archive also
carries the **back** of the sleeve, which no folder convention provides.

**Which releases: every album Browse shows that lacks a side of its own**
`[SPEC-COV-060]` -- the chosen release behind a radio passage, with no front or
no back in `cover_art`. Until 2026-10-02 this asked only about releases behind
a file whose tags said it had no embedded picture, and never twice: a CD add
writes no tags, so that week's discs were never asked about, and a release
whose front came from MuLibPlay or a folder was never asked for its back. A
probe of the archive that day found the front of all 17 albums showing none,
41 backs for the 171 showing none or some, and art for 88 releases asked in
August that had none then. So a release is asked again once its last asking
is `--recheck-days` old (60), recorded in `cover_art.caa_asked_at`.

**Only the side that is missing is filled.** A front from MuLibPlay, a folder
or an earlier fetch is never replaced; a back arriving beside it is added.

Keyed by **release**, not by folder. A directory can hold more than one album,
which is exactly the case on the DAO rips that make up most of the remaining
gap, and `folder.jpg` cannot tell them apart.

**Front and back both**, because MuLibPlay carried both and showed them side by
side; 559 of its 675 albums had a back. Nothing else: its art totalled 80.5 MB
of a 90.7 MB database, and a third image nobody displays would be that bargain
again.

**An archive that fails is not an archive with nothing.** A 404 is the
ordinary answer for a release nobody has photographed; a 5xx or no answer at
all, after retries, leaves the release unasked, to be asked next time.

Vipunen's job, not the player's. `[REQ-NEG-100]` forbids *playback* depending on
a live external service; fetching at build time into local storage is the
division that requirement exists to protect. Nothing here runs on the appliance.

    python tools/fetch_cover_art.py data/library.db [--limit N] [--recheck-days D]
                                                    [--release MBID ...]
"""

import argparse
import json
import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import time
import urllib.error
import urllib.request

# The archive asks for one request a second and a User-Agent that identifies
# the client. Both are conditions of use, not politeness.
GAP = 1.0
AGENT = "Lempi/0.1 (+https://github.com/MangoCats/Lempi)"
BASE = "https://coverartarchive.org"

# Below this it is not a picture -- the same floor the player applies, and the
# one MuLibPlay applied before it.
MIN_BYTES = 256

# **Thumbnails, not the originals.** The archive serves full-resolution scans:
# measured here at 2.9 MB average and one at 18.8 MB, against MuLibPlay's 68 KB
# average for the same job. Fetching originals would have added roughly 1.2 GB
# to a 978 MB library to fill a 200-pixel-tall box.
#
# Which size is chosen happens in `covers()` below, from the manifest.
# No thumbnail should approach this. A hit means the archive served something
# unexpected and it is not worth putting in the library.
MAX_BYTES = 2_000_000

DDL = """
CREATE TABLE IF NOT EXISTS cover_art (
    release_mbid TEXT PRIMARY KEY,
    front        BLOB,
    back         BLOB,
    source       TEXT NOT NULL,
    fetched_at   TEXT NOT NULL);
"""


class Unavailable(Exception):
    """The archive failed to answer -- not the same as having nothing."""


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def get(url: str, want_json: bool = False) -> bytes | None:
    """One image or manifest, or `None` if the archive has not got it.

    A 404 is the ordinary answer for a release nobody has photographed, and is
    not worth a retry or a mention. 503 means slow down. A failure that
    outlasts the retries raises `Unavailable`.
    """
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    delay = 2.0
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                data = r.read()
            if want_json:
                return data
            return data if len(data) >= MIN_BYTES else None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504):
                if attempt < 3:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise Unavailable(f"HTTP {e.code}") from e
            return None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            raise Unavailable(str(e)) from e
    raise Unavailable("no answer")


def thumbnail(image: dict) -> str | None:
    """The thumbnail to take of one manifest image.

    500 is the smallest with headroom over a 200px box on a high-density
    screen; 250 for releases that lack it. The original is never taken --
    that is what cost 2.9 MB a cover. Older entries name the same two
    `large` and `small`: on 2026-10-02 that was every image of three CDs
    added that week, and asking only for "500"/"250" took none of them."""
    thumbs = image.get("thumbnails") or {}
    return thumbs.get("500") or thumbs.get("large") or thumbs.get("250") or thumbs.get("small")


def covers(mbid: str, group: bool = False) -> tuple[bytes | None, bytes | None]:
    """The front and back of one release, via its manifest.

    **One request to find out what exists**, then at most one download per
    side. The obvious alternative -- ask for `/front-500`, fall back to
    `-250`, fall back to the original, and the same again for the back --
    is up to nine requests, most of them 404s, and measured at 84 seconds
    per release. The manifest costs about four and answers exactly.
    """
    what = "release-group" if group else "release"
    raw = get(f"{BASE}/{what}/{mbid}", want_json=True)
    time.sleep(GAP)
    if raw is None:
        return None, None
    try:
        images = json.loads(raw).get("images", [])
    except (json.JSONDecodeError, AttributeError):
        return None, None

    def pick(flag: str) -> bytes | None:
        for im in images:
            if not im.get(flag):
                continue
            url = thumbnail(im)
            if not url:
                continue
            data = get(url)
            time.sleep(GAP)
            if data is not None and len(data) <= MAX_BYTES:
                return data
        return None

    return pick("front"), pick("back")


def prepare(conn) -> None:
    conn.executescript(DDL)
    try:
        conn.execute("ALTER TABLE cover_art ADD COLUMN caa_asked_at TEXT")
    except sqlite3.OperationalError:
        pass    # there already
    conn.commit()


def wanted(conn, recheck_days: int, releases=None) -> list[tuple[str, bool, bool]]:
    """`(release, lacks_front, lacks_back)` for each album Browse shows that
    lacks a side of its own and was not asked within `recheck_days` -- or,
    given `releases`, for those alone, however recently asked."""
    have = (f"SELECT rel, COALESCE(LENGTH(a.front), 0) >= {MIN_BYTES}, COALESCE(LENGTH(a.back), 0) >= {MIN_BYTES}, "
            "a.caa_asked_at FROM ({}) LEFT JOIN cover_art a ON a.release_mbid = rel")
    if releases is not None:
        rows = conn.execute(have.format(" UNION ".join("SELECT ? AS rel" for _ in releases)),
                            list(releases)).fetchall() if releases else []
        return [(r, not f, not b) for r, f, b, _ in rows if not f or not b]
    shown = ("SELECT DISTINCT rr.release_mbid AS rel FROM release_recordings rr "
             "JOIN passage_recordings pr ON pr.mbid = rr.mbid "
             "JOIN passages p ON p.passage_id = pr.passage_id WHERE rr.chosen = 1 AND p.kind = 'radio'")
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - recheck_days * 86400))
    return [(r, not f, not b) for r, f, b, asked in conn.execute(have.format(shown) + " ORDER BY rel")
            if (not f or not b) and (asked is None or asked < cutoff)]


def fetch_for(conn, todo, say=say) -> dict:
    """Ask the archive for each `(release, lacks_front, lacks_back)` and fill
    only the missing sides. Commits after each release, so a long run that
    is stopped keeps what it found. Returns the counts."""
    n = {"asked": 0, "fronts": 0, "backs": 0, "unavailable": 0}
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for i, (mbid, lack_front, lack_back) in enumerate(todo, 1):
        try:
            front, back = covers(mbid)
            # The release group is the fallback for a front only. A back cover
            # belongs to a particular pressing, so the group's is not this one's.
            # Asked by the group's own id: until 2026-10-02 the release's was
            # sent to the group endpoint, which can never match, so this
            # fallback had never found anything.
            if lack_front and front is None:
                group = conn.execute("SELECT release_group FROM releases WHERE mbid = ?1",
                                     (mbid,)).fetchone() if _has_col(conn, "releases", "release_group") else None
                if group and group[0]:
                    front, _ = covers(group[0], group=True)
        except Unavailable as e:
            say(f"  {mbid}: the archive did not answer ({e}) -- left to ask again")
            n["unavailable"] += 1
            continue
        front = front if lack_front else None
        back = back if lack_back else None
        conn.execute(
            "INSERT INTO cover_art (release_mbid, front, back, source, fetched_at, caa_asked_at) "
            "VALUES (?1, ?2, ?3, 'coverartarchive', ?4, ?4) "
            "ON CONFLICT(release_mbid) DO UPDATE SET "
            "  front = CASE WHEN COALESCE(LENGTH(cover_art.front), 0) >= ?5 THEN cover_art.front "
            "               ELSE COALESCE(excluded.front, cover_art.front) END, "
            "  back = CASE WHEN COALESCE(LENGTH(cover_art.back), 0) >= ?5 THEN cover_art.back "
            "              ELSE COALESCE(excluded.back, cover_art.back) END, "
            # Provenance says where each side came from, not just the first.
            "  source = CASE WHEN (excluded.front IS NOT NULL OR excluded.back IS NOT NULL) "
            "                 AND cover_art.source NOT LIKE '%coverartarchive%' "
            "            THEN cover_art.source || '+coverartarchive' ELSE cover_art.source END, "
            "  fetched_at = CASE WHEN excluded.front IS NOT NULL OR excluded.back IS NOT NULL "
            "                THEN excluded.fetched_at ELSE cover_art.fetched_at END, "
            "  caa_asked_at = excluded.caa_asked_at",
            (mbid, front, back, now, MIN_BYTES))
        conn.commit()
        n["asked"] += 1
        n["fronts"] += front is not None
        n["backs"] += back is not None
        if i % 25 == 0 or i == len(todo):
            say(f"  {i}/{len(todo)}   {n['fronts']} front(s), {n['backs']} back(s) found")
    return n


def _has_col(conn, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--recheck-days", type=int, default=60,
                    help="ask again about a release last asked this many days ago (default 60)")
    ap.add_argument("--release", action="append",
                    help="ask about this release alone, however recently asked (repeatable)")
    args = ap.parse_args()

    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    prepare(conn)

    todo = wanted(conn, args.recheck_days, args.release)
    if args.limit:
        todo = todo[:args.limit]
    # A manifest plus up to two images, each followed by the courtesy gap.
    say(f"{len(todo)} release(s) to ask about: {sum(f for _, f, _ in todo)} lacking a front, "
        f"{sum(b for _, _, b in todo)} a back  (~{len(todo) * GAP * 2.5 / 60:.0f} min)\n")
    if not todo:
        return 0
    n = fetch_for(conn, todo)
    size = conn.execute(
        "SELECT COALESCE(SUM(LENGTH(front)),0) + COALESCE(SUM(LENGTH(back)),0) "
        "  FROM cover_art").fetchone()[0]
    say(f"\n  asked {n['asked']}: {n['fronts']} front(s), {n['backs']} back(s) found"
        + (f"; {n['unavailable']} not answered, left for next time" if n["unavailable"] else ""))
    say(f"  cover_art now holds {size / 1048576:.1f} MB")
    return 1 if n["unavailable"] else 0


if __name__ == "__main__":
    sys.exit(main())
