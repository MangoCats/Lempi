#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Vipunen S3, selection half: which release is a file actually from?

A recording appears on many releases -- 86 of them for one track in this
library -- and more than half of all candidates are compilations. "Earliest by
date" is close to a coin toss against a pool like that, and it is how a song
ends up filed under a greatest-hits collection it was never written for.

The criteria are McRhythm's `[AM-MB-020]`/`[AM-MB-030]`/`[AM-MB-040]`, adapted.
McRhythm was matching a whole album rip with no identifiers, so it searched by
name across seven strategies and scored artist similarity at 60% against album
at 40% -- artist being the more stable of the two across reissues. Here the
artist is already fixed by the recording MBID, so that term is spent, and what
remains is:

  * the file's own ALBUM tag against the release title (5,587 files have one),
    which is direct evidence of the record it was ripped from -- and when it
    matches confidently, that settles it: knowing which record a rip came from
    beats every other consideration, compilation or not;
  * failing that, the ORIGINAL release album -- an official album that is not a
    compilation, live set or soundtrack, earliest first;
  * kind -- an Album that is not a compilation, live album or soundtrack;
  * status -- Official over Promotion over Bootleg;
  * date, last, as the tiebreak McRhythm's cascade also left until last.

Nothing is discarded. The choice, its margin and its runners-up are written to
`ingest_decisions` `[REQ-VIS-110]`, because a selection nobody can argue with is
a selection nobody can correct.

    python tools/choose_release.py data/library.db [--limit N] [--explain MBID]
"""

import argparse
import json
import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import time

# Weights. Deliberately blunt: the name term decides when a tag exists, and the
# kind term decides when it does not. Tuning these past one decimal place would
# be fitting noise -- there is no labelled truth to fit against.
W_NAME = 5.0
W_KIND = 3.0
W_STATUS = 1.0
W_DATE = 0.5

# What disqualifies a release from being "the record this song is from". Live
# and soundtrack are included because a studio track appearing on either is
# almost never the release it belongs to.
DEMOTE = {"Compilation": -1.0, "Live": -0.7, "Soundtrack": -0.6,
          "Remix": -0.5, "DJ-mix": -0.8, "Interview": -1.0, "Demo": -0.3}


def jaro_winkler(a: str, b: str) -> float:
    """Similarity in [0, 1]. Pure Python: one file, no dependencies.

    Winkler's prefix bonus is what makes it right for album titles -- "Aja" and
    "Aja (Remastered)" should read as near-identical, and they do.
    """
    if not a or not b:
        return 0.0
    a, b = a.lower(), b.lower()
    if a == b:
        return 1.0
    reach = max(len(a), len(b)) // 2 - 1
    if reach < 0:
        reach = 0
    a_hit = [False] * len(a)
    b_hit = [False] * len(b)
    matches = 0
    for i, ch in enumerate(a):
        for j in range(max(0, i - reach), min(len(b), i + reach + 1)):
            if not b_hit[j] and b[j] == ch:
                a_hit[i] = b_hit[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    # Transpositions: matched characters that arrive in a different order.
    k = transpositions = 0
    for i, ch in enumerate(a):
        if a_hit[i]:
            while not b_hit[k]:
                k += 1
            if ch != b[k]:
                transpositions += 1
            k += 1
    m = float(matches)
    jaro = (m / len(a) + m / len(b) + (m - transpositions / 2) / m) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        prefix += 1
    return jaro + prefix * 0.1 * (1 - jaro)


def name_match(tag: str, title: str) -> float:
    """How much a release title looks like the file's album tag.

    Jaro-Winkler alone is not enough, and the failure is systematic: a tag of
    "Tomb Raider" against "Lara Croft: Tomb Raider: Music From the Motion
    Picture" scores 0.60, because the measure is punished by the length gap --
    while the tag being *contained whole* in the title is about as strong a
    signal as exists. Rippers abbreviate; they rarely invent.

    So containment is taken as its own near-certainty and the two are combined
    by the better of them. McRhythm hit the same family of problem from the
    other side, with CamelCase splitting and per-token fuzzing `[AM-ARCH-020]`.
    """
    a = "".join(ch for ch in (tag or "").lower() if ch.isalnum() or ch == " ").strip()
    b = "".join(ch for ch in (title or "").lower() if ch.isalnum() or ch == " ").strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0                 # identical beats contained, and should read so
    # Four characters, so that "four" still counts and "a" never does.
    if len(a) >= 4 and (a in b or b in a):
        return 0.9
    return jaro_winkler(a, b)


def kind_score(primary: str | None, secondary: str | None) -> float:
    """How much this looks like the record a song belongs to."""
    score = 1.0 if primary == "Album" else (0.4 if primary == "EP" else 0.0)
    for tag in (secondary or "").split(","):
        if tag.strip() in DEMOTE:
            score += DEMOTE[tag.strip()]
    return score


def status_score(status: str | None) -> float:
    return {"Official": 1.0, "Promotion": 0.3, "Bootleg": -0.5}.get(status, 0.5)


def year(date: str | None) -> int | None:
    if not date or not date[:4].isdigit():
        return None
    return int(date[:4])


def score_all(rows, album_tag: str | None, oldest: int | None):
    """Score every candidate, best first. `rows` are the release columns."""
    out = []
    for r in rows:
        rid, title, date, status, primary, secondary, track_count, group = r
        name = name_match(album_tag or "", title or "")
        kind = kind_score(primary, secondary)
        stat = status_score(status)
        # Earlier is better, but gently: the original pressing usually is the
        # record, and a remaster thirty years later usually is not.
        y = year(date)
        age = 0.0
        if y and oldest:
            age = max(0.0, 1.0 - (y - oldest) / 30.0)
        total = (W_NAME * name + W_KIND * kind + W_STATUS * stat + W_DATE * age)
        out.append({
            "release": rid, "release_group": group,
            "title": title, "date": date, "status": status,
            "primary_type": primary, "secondary_types": secondary,
            "track_count": track_count,
            "name_similarity": round(name, 3), "kind": round(kind, 2),
            "score": round(total, 3),
        })
    # Ordered criteria, not a weighted sum -- which is how McRhythm ranked
    # `[AM-MB-040]`, and the reason matters. A sum lets `kind` outvote a name
    # that matched almost exactly: a release titled the same as the file's tag
    # at 0.99 lost to an unrelated album at 0.48, because "is an album" was
    # worth more than "is the record this file names".
    #
    # So a confident tag match settles it, and everything else only breaks ties
    # WITHIN that tier. The tag is evidence of the edition the file came from,
    # which is the question being asked; whether that edition is a compilation
    # is not the question.
    best_name = max((d["name_similarity"] for d in out), default=0.0)
    if best_name >= 0.85:
        # Tier 1: the record the rip came from. The file's tag names it, and
        # nothing else outranks knowing. A compilation is a legitimate answer
        # here -- if the file is a compilation rip, that IS the record.
        tier = [d for d in out if d["name_similarity"] >= best_name - 0.05]
        tier.sort(key=lambda d: (-d["score"], d["date"] or "9999", d["title"] or ""))
    else:
        # Tier 2: the original release album. With no tag to trust, the answer
        # is the record the song was released ON, which means an album that is
        # not a compilation, live set or soundtrack, officially issued, and the
        # earliest such -- a reissue thirty years later is the same album, but
        # it is not the original.
        originals = [d for d in out
                     if d["primary_type"] == "Album"
                     and d["kind"] >= 1.0
                     and d["status"] == "Official"
                     and year(d["date"]) is not None]
        if originals:
            tier = sorted(originals, key=lambda d: (year(d["date"]), d["title"] or ""))
        else:
            # Neither known. Rank by everything at once and record a thin
            # margin, which is the signal that a human should look.
            tier = sorted(out, key=lambda d: (-d["score"], d["date"] or "9999"))
    # One decision per ALBUM, not per pressing `[AM-MB-020]`. Five identical
    # 1982 pressings of Rio are one record in five territories; ranking them
    # against each other is a tie broken by sort order, which is not a choice.
    # Collapsing to the group first means the ordering above decides between
    # different albums, and only then between editions of the winner -- where
    # the earliest official pressing is the one wanted.
    seen: dict[str, dict] = {}
    collapsed = []
    for d in tier:
        key = d.get("release_group")
        if not key:
            collapsed.append(d)
            continue
        if key not in seen:
            seen[key] = d
            collapsed.append(d)
        else:
            # Within a group, prefer the earliest official pressing.
            best = seen[key]
            mine = ((d["status"] != "Official"), year(d["date"]) or 9999)
            theirs = ((best["status"] != "Official"), year(best["date"]) or 9999)
            if mine < theirs:
                collapsed[collapsed.index(best)] = d
                seen[key] = d
    tier = collapsed
    chosen_ids = {id(d) for d in tier}
    rest = sorted((d for d in out if id(d) not in chosen_ids),
                  key=lambda d: (-d["score"], d["date"] or "9999"))
    return tier + rest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--explain", help="show the scoring for one recording MBID")
    args = ap.parse_args()

    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True)
    conn.execute("PRAGMA busy_timeout = 5000")
    # The chosen flag lives beside the link it qualifies. Lempi reads it with
    # `ORDER BY chosen DESC`, which still behaves when nothing has been chosen.
    try:
        conn.execute("ALTER TABLE release_recordings ADD COLUMN chosen INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # Release groups can be recovered from responses already cached, so a
    # library fetched before the column existed does not have to ask again.
    try:
        conn.execute("ALTER TABLE releases ADD COLUMN release_group TEXT")
    except sqlite3.OperationalError:
        pass
    if conn.execute("SELECT COUNT(*) FROM releases WHERE release_group IS NULL"
                    ).fetchone()[0]:
        filled = 0
        for (raw,) in conn.execute("SELECT response FROM musicbrainz_cache"):
            for rel in (json.loads(raw).get("releases") or []):
                gid = (rel.get("release-group") or {}).get("id")
                if gid and rel.get("id"):
                    filled += conn.execute(
                        "UPDATE releases SET release_group = ?1 "
                        " WHERE mbid = ?2 AND release_group IS NULL",
                        (gid, rel["id"])).rowcount
        conn.commit()
        if filled:
            print(f"recovered {filled} release group(s) from the cache")

    conn.execute("""CREATE TABLE IF NOT EXISTS ingest_decisions (
        decision_id INTEGER PRIMARY KEY, audio_md5 TEXT, stage TEXT,
        outcome TEXT, confidence REAL, detail TEXT, decided_at INTEGER)""")

    mbids = [r[0] for r in conn.execute(
        "SELECT DISTINCT mbid FROM release_recordings ORDER BY mbid")]
    if args.explain:
        mbids = [args.explain]
    elif args.limit:
        mbids = mbids[: args.limit]

    decided = tagged = 0
    now = int(time.time())
    for mbid in mbids:
        rows = conn.execute(
            "SELECT rel.mbid, rel.title, rel.release_date, rel.status, "
            "       rel.primary_type, rel.secondary_types, rel.track_count, "
            "       rel.release_group "
            "  FROM release_recordings rr JOIN releases rel ON rel.mbid = rr.release_mbid "
            " WHERE rr.mbid = ?1", (mbid,)).fetchall()
        if not rows:
            continue
        # The file's own album tag, via any passage using this recording.
        tag = conn.execute(
            "SELECT ft.album, f.audio_md5 FROM passage_recordings pr "
            "  JOIN passages p ON p.passage_id = pr.passage_id "
            "  JOIN files f ON f.file_id = p.file_id "
            "  LEFT JOIN file_tags ft ON ft.file_id = f.file_id "
            " WHERE pr.mbid = ?1 LIMIT 1", (mbid,)).fetchone()
        album_tag, md5 = (tag or (None, None))
        if album_tag:
            tagged += 1
        years = [year(r[2]) for r in rows]
        oldest = min([y for y in years if y], default=None)

        ranked = score_all(rows, album_tag, oldest)
        if args.explain:
            print(f"recording {mbid}   file's album tag: {album_tag!r}")
            for d in ranked[:8]:
                print(f"  {d['score']:6.2f}  name {d['name_similarity']:.2f} "
                      f"kind {d['kind']:+.2f}  {str(d['date'])[:4]:>4}  "
                      f"{(d['primary_type'] or '?')}/{d['secondary_types'] or '-'}  "
                      f"{d['title']}")
            return 0

        best = ranked[0]
        runner = ranked[1] if len(ranked) > 1 else None
        conn.execute("UPDATE release_recordings SET chosen = 0 WHERE mbid = ?1", (mbid,))
        conn.execute("UPDATE release_recordings SET chosen = 1 "
                     " WHERE mbid = ?1 AND release_mbid = ?2", (mbid, best["release"]))
        # The margin is the part worth keeping: a win by 0.02 over a compilation
        # is a coin toss that someone should look at, and a win by 4 is not.
        conn.execute(
            "INSERT INTO ingest_decisions "
            "  (audio_md5, stage, outcome, confidence, detail, decided_at) "
            "VALUES (?1, 'release_match', ?2, ?3, ?4, ?5)",
            (md5, best["release"],
             round(best["score"] - (runner["score"] if runner else 0.0), 3),
             json.dumps({"recording": mbid, "album_tag": album_tag,
                         "candidates": len(ranked), "chosen": best,
                         "runners_up": ranked[1:4]}),
             now))
        decided += 1
        if decided % 200 == 0:
            conn.commit()
            print(f"  {decided}/{len(mbids)}", flush=True)

    conn.commit()
    print(f"chose a release for {decided} recording(s); {tagged} had an album tag to "
          f"match against")
    return 0


if __name__ == "__main__":
    sys.exit(main())
