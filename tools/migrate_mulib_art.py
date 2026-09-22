#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bring MuLibPlay's hand-curated album art into Lempi's `cover_art` table
`[REQ-VIS-170]` `[GDE-PHS-010]`.

`migrate_mulib.py` moved six years of MuLibPlay history into Lempi -- files,
recordings, passages, play history, preferences -- but never `albums.coverArt`
/`backCover`. Everything since has come from `fetch_cover_art.py`'s Cover Art
Archive pass, which only asks about a narrow gap (releases behind a file with
no embedded picture and no `folder.jpg`) and only found something for about
half of what it asked. MuLibPlay's own art was never in the running.

The mapping turns out to be clean, not fuzzy: every one of MuLibPlay's 673
art-bearing albums (of 675) carries a MusicBrainz release `mbid`, and that
`mbid` is exactly Lempi's `cover_art.release_mbid`. No title/artist matching
needed -- this is a straight key join.

**MuLibPlay wins where both have art**, per-side rather than per-row: it is
six years of a person choosing a picture, against an archive script's first
hit. An existing archive image is kept only for the side MuLibPlay has
nothing for (2 albums have no front at all; 116 have no back). Source becomes
`inherited:mulib` for any row this touches -- the same tag `migrate_mulib.py`
uses for the rest of MuLibPlay's data -- which loses per-side provenance on a
mixed row, but the table only ever tracked one source per row regardless of
how many sides it covers.

This alone does not make the art appear everywhere MuLibPlay showed it: the
player looks up `cover_art` through the release Vipunen *chose* for a
recording, and MuLibPlay's pick is Vipunen's chosen release for only 164 of
these 673 -- a different pressing, usually. `player/src/db.rs::stored_art`
now falls back to any release known to carry the same recording when the
chosen one has no art (matching what `player/src/covers.rs` already did for
the MPD-facing cover file), which is what turns this from 164 passages worth
of art into roughly 15,600 of the library's 16,400.

Usage:
    python tools/migrate_mulib_art.py --mulib mulib.db data/library.db
    python tools/migrate_mulib_art.py --mulib mulib.db --dry-run /srv/library/library.db
"""

import argparse
import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import time

SRC = "inherited:mulib"

# The same floor the player applies, and the one fetch_cover_art.py applies:
# below this it is not a picture.
MIN_BYTES = 256

DDL = """
CREATE TABLE IF NOT EXISTS cover_art (
    release_mbid TEXT PRIMARY KEY,
    front        BLOB,
    back         BLOB,
    source       TEXT NOT NULL,
    fetched_at   TEXT NOT NULL);
"""


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", help="target Lempi library to write into")
    ap.add_argument("--mulib", default="mulib.db", help="MuLibPlay's database (read-only)")
    ap.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    args = ap.parse_args()

    src = sqlite3.connect(f"file:{args.mulib}?mode=ro&immutable=1", uri=True)
    rows = src.execute(
        "SELECT mbid, coverArt, backCover FROM albums "
        "WHERE mbid IS NOT NULL AND (coverArt IS NOT NULL OR backCover IS NOT NULL)"
    ).fetchall()
    say(f"{len(rows)} MuLibPlay album(s) with an mbid and some art")

    def usable(blob: bytes | None) -> bytes | None:
        return blob if blob is not None and len(blob) >= MIN_BYTES else None

    # Catalogue-only, and it writes -- library half as `main`.
    out = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
    out.execute("PRAGMA busy_timeout = 60000")
    out.executescript(DDL)
    out.commit()

    inserted = merged = unchanged = skipped_small = 0
    front_bytes_added = back_bytes_added = 0
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    for mbid, cover_art, back_cover in rows:
        m_front, m_back = usable(cover_art), usable(back_cover)
        if m_front is None and m_back is None:
            skipped_small += 1
            continue

        existing = out.execute(
            "SELECT front, back FROM cover_art WHERE release_mbid = ?", (mbid,)
        ).fetchone()
        if existing is None:
            new_front, new_back = m_front, m_back
            inserted += 1
        else:
            e_front, e_back = existing
            new_front = m_front if m_front is not None else e_front
            new_back = m_back if m_back is not None else e_back
            if new_front == e_front and new_back == e_back:
                unchanged += 1
                continue
            merged += 1

        if m_front is not None:
            front_bytes_added += len(m_front)
        if m_back is not None:
            back_bytes_added += len(m_back)

        if not args.dry_run:
            out.execute(
                "INSERT OR REPLACE INTO cover_art VALUES (?,?,?,?,?)",
                (mbid, new_front, new_back, SRC, now),
            )

    if not args.dry_run:
        out.commit()

    total = out.execute(
        "SELECT COALESCE(SUM(LENGTH(front)),0) + COALESCE(SUM(LENGTH(back)),0) FROM cover_art"
    ).fetchone()[0]

    say(f"\n  {inserted} new row(s), {merged} existing row(s) gained a side, "
        f"{unchanged} already had everything MuLibPlay offers")
    say(f"  {skipped_small} had nothing usable (below the {MIN_BYTES}-byte floor)")
    say(f"  +{front_bytes_added / 1048576:.1f} MB front, +{back_bytes_added / 1048576:.1f} MB back added")
    if args.dry_run:
        # `total` did not move -- nothing was written -- so it is the size
        # before this run, not after. Overwritten sides mean the added bytes
        # do not simply sum onto it, so the post-run size is not computed here.
        say(f"  cover_art currently holds {total / 1048576:.1f} MB (dry run -- nothing written)")
    else:
        say(f"  cover_art now holds {total / 1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
