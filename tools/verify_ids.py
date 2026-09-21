#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Are the recording MBIDs on these passages actually right?

Every name Lempi shows, every rotation decision and every play it records is
keyed on a recording MBID. If one is wrong the error is invisible: the player
displays a real title by a real artist, and it is simply the wrong song. Nothing
downstream can catch that, because everything downstream trusts the id.

So this checks the id against evidence the id did not come from -- the file's own
tags, which were written by whoever ripped the disc and know nothing about
MusicBrainz. Agreement between two independent sources is weak evidence
separately and strong evidence together.

  agree      tag title and artist both match the recording
  title-only artist disagrees -- often a credit difference, sometimes wrong
  artist-only title disagrees -- alternate takes, live versions, or wrong
  disagree   neither matches: the id is questionable
  untagged   no tags to check against; this says nothing either way

    python tools/verify_ids.py data/library.db [--sample N] [--list-bad N]
"""

import argparse
import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]

# Titles carry accents and dashes the Windows console cannot encode, and a
# report that dies on the fourth line of its own findings is not a report.
def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc))


def jaro_winkler(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    a, b = a.lower(), b.lower()
    if a == b:
        return 1.0
    reach = max(max(len(a), len(b)) // 2 - 1, 0)
    ah = [False] * len(a)
    bh = [False] * len(b)
    m = 0
    for i, ch in enumerate(a):
        for j in range(max(0, i - reach), min(len(b), i + reach + 1)):
            if not bh[j] and b[j] == ch:
                ah[i] = bh[j] = True
                m += 1
                break
    if not m:
        return 0.0
    k = t = 0
    for i, ch in enumerate(a):
        if ah[i]:
            while not bh[k]:
                k += 1
            if ch != b[k]:
                t += 1
            k += 1
    j = (m / len(a) + m / len(b) + (m - t / 2) / m) / 3
    p = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        p += 1
    return j + p * 0.1 * (1 - j)


def norm(s: str | None) -> str:
    """Strip what differs between a tag and a database without changing which
    song it is: bracketed qualifiers, punctuation, case, leading articles."""
    if not s:
        return ""
    out = []
    depth = 0
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    s = "".join(out).lower().strip()
    s = "".join(ch for ch in s if ch.isalnum() or ch == " ")
    for article in ("the ", "a ", "an "):
        if s.startswith(article):
            s = s[len(article):]
    return " ".join(s.split())


MATCH = 0.87        # above this, two names are the same name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--sample", type=int, default=500)
    ap.add_argument("--list-bad", type=int, default=12)
    args = ap.parse_args()

    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True)
    rows = conn.execute(
        """SELECT p.passage_id, pr.mbid, r.title, ft.title, ft.artist,
                  (SELECT a.name FROM recording_artists ra
                     JOIN artists a ON a.mbid = ra.artist_mbid
                    WHERE ra.mbid = pr.mbid ORDER BY ra.weight DESC, a.name LIMIT 1)
             FROM passages p
             JOIN passage_recordings pr ON pr.passage_id = p.passage_id
             LEFT JOIN recordings r ON r.mbid = pr.mbid
             LEFT JOIN file_tags ft ON ft.file_id = p.file_id
            WHERE p.kind = 'radio'
            ORDER BY RANDOM() LIMIT ?1""",
        (args.sample,),
    ).fetchall()

    buckets = {"agree": 0, "title-only": 0, "artist-only": 0,
               "disagree": 0, "untagged": 0, "unknown-mbid": 0}
    bad = []
    for pid, mbid, rec_title, tag_title, tag_artist, rec_artist in rows:
        if rec_title is None:
            buckets["unknown-mbid"] += 1
            bad.append((pid, mbid, "(no recording row)", tag_title, "", tag_artist))
            continue
        if not tag_title and not tag_artist:
            buckets["untagged"] += 1
            continue
        t = jaro_winkler(norm(tag_title), norm(rec_title)) if tag_title else None
        a = jaro_winkler(norm(tag_artist), norm(rec_artist)) if tag_artist and rec_artist else None
        t_ok = t is not None and t >= MATCH
        a_ok = a is not None and a >= MATCH
        if t_ok and a_ok:
            buckets["agree"] += 1
        elif t_ok:
            buckets["title-only"] += 1
        elif a_ok:
            buckets["artist-only"] += 1
        else:
            buckets["disagree"] += 1
            bad.append((pid, mbid, rec_title, tag_title, rec_artist, tag_artist))

    n = len(rows)
    say(f"checked {n} radio passages\n")
    for k in ("agree", "title-only", "artist-only", "disagree", "untagged", "unknown-mbid"):
        v = buckets[k]
        say(f"  {k:13} {v:5d}  {v / max(n, 1):6.1%}")

    judged = buckets["agree"] + buckets["title-only"] + buckets["artist-only"] + buckets["disagree"]
    if judged:
        print(f"\n  of {judged} judgeable, {buckets['disagree'] / judged:.1%} disagree on both")

    if bad:
        print(f"\nquestionable (showing {min(len(bad), args.list_bad)} of {len(bad)}):")
        for pid, mbid, rt, tt, ra, ta in bad[: args.list_bad]:
            say(f"  passage {pid}  mbid {str(mbid)[:36]}")
            say(f"      says: {str(rt)[:44]:46} by {str(ra)[:28]}")
            say(f"      tags: {str(tt)[:44]:46} by {str(ta)[:28]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
