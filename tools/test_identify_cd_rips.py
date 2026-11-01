#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `identify_cd_rips.py` `[SPEC-CDI-095]`.

The fixture is *Pure McCartney* as it went in on 2026-10-31, made small: two
discs' sheets in one album folder, their track numbers overlapping, every
track on its CD-TEXT placeholder with a review row, its flavor measured under
that placeholder, one of them played. No network: the lookups under
`identify_disc` are replaced, so the tool's own path -- the sheet, the
cascade, the add's rule -- is what runs.

    python tools/test_identify_cd_rips.py
"""

import argparse
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import identify_cd_rips as icr   # noqa: E402
import ingest_cd                 # noqa: E402
import test_ingest_cd as tic     # noqa: E402  -- its schema and edition builder

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


DISCS = {   # disc: [(number, title, isrc)]
    1: [(1, "Maybe I’m Amazed", "GBCCS1000115"), (2, "Heart of the Country", "GBCCS1100094")],
    2: [(1, "Band on the Run", "GBCCS1000004"), (2, "Arrow Through Me", None)],
}


def build(tmp):
    album = os.path.join(tmp, "Paul McCartney", "Pure McCartney")
    os.makedirs(album)
    db = os.path.join(tmp, "lib.db")
    c = sqlite3.connect(db)
    c.executescript(tic.SCHEMA + "CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT, characteristic TEXT, "
                    "class TEXT, value REAL, source TEXT, accuracy REAL, "
                    "PRIMARY KEY (subject_kind, subject_id, characteristic, class));")
    c.execute("ALTER TABLE ingest_decisions ADD COLUMN unused TEXT")    # a wider real table changes nothing
    for disc, tracks in DISCS.items():
        sheet = "Paul McCartney - Pure McCartney" + ("(Disc 2)" if disc == 2 else "") + ".cue"
        lines = ['PERFORMER "Paul McCartney"', 'TITLE "Pure McCartney"']
        for n, title, isrc in tracks:
            name = f"{n:02d}. {title}.mp3"
            lines += [f'FILE "{name}" MP3', f"  TRACK {n:02d} AUDIO", f'    TITLE "{title}"']
            lines += [f"    ISRC {isrc}"] if isrc else []
            lines += ["    INDEX 01 00:00:00"]
            path = os.path.join(album, name)
            with open(path, "wb") as f:
                f.write(b"\0" * 64)
            md5 = f"{disc}{n}".ljust(32, "0")
            fid = c.execute("INSERT INTO files (audio_md5, path, duration_ms) VALUES (?1, ?2, 200000)",
                            (md5, path)).lastrowid
            local = f"local:audio:{md5}:0"
            c.execute("INSERT INTO recordings VALUES (?1, ?2, 200000, 'cd:text')", (local, title))
            for kind in ("radio", "album"):
                pid = c.execute("INSERT INTO passages (file_id, kind, start_ms, end_ms, boundary_src) "
                                "VALUES (?1, ?2, 0, 200000, 'imported:tracks-cue')", (fid, kind)).lastrowid
                c.execute("INSERT INTO passage_recordings VALUES (?1, ?2, 1.0, 'cd:text')", (pid, local))
                if kind == "radio":
                    c.execute("INSERT INTO id_checks VALUES (?1, ?2, 'unmatched', NULL, NULL, 'then')", (pid, local))
            for ch in ("danceability", "mood_happy"):
                c.execute("INSERT INTO flavor VALUES ('recording', ?1, ?2, 'x', 0.5, 'local:essentia', NULL)",
                          (local, ch))
            c.execute("INSERT INTO ingest_decisions (audio_md5, stage, outcome, detail, decided_at) "
                      "VALUES (?1, 'rip', 'none', ?2, 'then')",
                      (md5, json.dumps({"track": n, "tracks_mode": True, "format": "tracks-cue", "track_count": 2,
                                        "candidates": 0, "chosen": None, "disc_id": None})))
        with open(os.path.join(album, sheet), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    # The recording behind disc 1's track 1 already has a measurement of its
    # own, from another copy: it stands, and the placeholder's stays put.
    c.execute("INSERT INTO flavor VALUES ('recording', 'rec-amazed', 'danceability', 'x', 0.9, 'other', NULL)")
    c.commit()
    c.close()
    listener = os.path.join(tmp, "listener.db")
    lc = sqlite3.connect(listener)
    lc.execute("CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY, passage_id INTEGER, mbid TEXT)")
    lc.execute("INSERT INTO listener_play_history (passage_id, mbid) VALUES (1, ?1)", ("local:audio:11" + "0" * 30 + ":0",))
    lc.commit()
    lc.close()
    return db, listener, album


def editions():
    """Three country editions of the set, cut to each disc, sharing every
    recording -- as MusicBrainz has *Pure McCartney* -- and a fourth whose
    disc 1 track 2 is another recording."""
    rec = {1: [("rec-amazed", "Maybe I'm Amazed", ["GBCCS1000115"], 200000),
               ("rec-heart", "Heart of the Country", ["GBCCS1100094"], 200000)],
           2: [("rec-band", "Band on the Run", ["GBCCS1000004"], 200000),
               ("rec-arrow", "Arrow Through Me", [], 200000)]}
    out = {d: [tic._edition(f"rel-{c}", recs) for c in ("us", "au", "eu")] for d, recs in rec.items()}
    out[1].append(tic._edition("rel-disc1-only", rec[1]))     # a release only one disc finds
    return out


def run(db, **kw):
    args = argparse.Namespace(db=db, commit=False, listener=None, folder=None, release=None, audit=False, cache=None)
    for k, v in kw.items():
        setattr(args, k, v)
    return icr.fix(args)


def state(db):
    c = sqlite3.connect(db)
    try:
        return {"links": sorted(c.execute(
                    "SELECT f.path, p.kind, pr.mbid, pr.source FROM passage_recordings pr JOIN passages p "
                    "USING (passage_id) JOIN files f USING (file_id)").fetchall()),
                "checks": sorted(c.execute("SELECT passage_id, suggested FROM id_checks").fetchall()),
                "flavor": sorted(c.execute("SELECT subject_id, characteristic, value FROM flavor").fetchall()),
                "decisions": [(s, o, json.loads(d)) for s, o, d in c.execute(
                    "SELECT stage, outcome, detail FROM ingest_decisions WHERE stage IN ('identify', 'rip') "
                    "AND decided_at != 'then' ORDER BY decision_id")]}
    finally:
        c.close()


def main() -> int:
    tmp = tempfile.mkdtemp()
    db, listener, album = build(tmp)
    eds = editions()
    looked = []
    old = (ingest_cd.lookup_disc_id, ingest_cd.lookup_barcode, ingest_cd.lookup_cd_text,
           ingest_cd.fetch_releases.get, ingest_cd.fetch_releases.RATE_S)
    ingest_cd.lookup_disc_id = lambda toc: ("none", [])
    ingest_cd.lookup_barcode = lambda b: ("none", [])
    # [SPEC-RIP-067]: each ISRC asked after, as MusicBrainz has them -- Band
    # on the Run's under two recordings, every other code under one.
    isrcs_asked = []

    def by_isrc(url, tries=4):
        code = url.split("/isrc/")[1].split("?")[0]
        isrcs_asked.append(code)
        return {"recordings": [{"id": "a"}, {"id": "b"}] if code == "GBCCS1000004" else [{"id": "a"}]}
    ingest_cd.fetch_releases.get, ingest_cd.fetch_releases.RATE_S = by_isrc, 0

    def by_text(toc, disc_id=None):
        disc = 2 if toc.tracks[0].title == "Band on the Run" else 1
        looked.append(disc)
        return "cd-text", eds[disc]
    ingest_cd.lookup_cd_text = by_text
    try:
        print("the rips: each disc by its own sheet, though both share one folder")
        c = sqlite3.connect(db)
        found = icr.rips(c)
        c.close()
        check(sorted((r["sheet"], [t["number"] for t in r["tracks"]]) for r in found)
              == [("Paul McCartney - Pure McCartney(Disc 2).cue", [1, 2]), ("Paul McCartney - Pure McCartney.cue", [1, 2])],
              f"two rips of two tracks: {[(r['sheet'], len(r['tracks'])) for r in found]}")
        check(all(len(icr.waiting(r)) == 2 for r in found), "every track waiting")

        print("a rehearsal decides each rip and keeps nothing")
        before = state(db)
        check(run(db) == 0 and state(db) == before and sorted(looked) == [1, 2],
              "both discs looked up, nothing written")

        print("--commit: the ISRC rule, by the add's own code")
        check(run(db, commit=True, listener=listener) == 0, "ran")
        s = state(db)
        radio = {os.path.basename(p): (m, src) for p, k, m, src in s["links"] if k == "radio"}
        album_ = {os.path.basename(p): m for p, k, m, src in s["links"] if k == "album"}
        check(radio["01. Maybe I’m Amazed.mp3"] == ("rec-amazed", "musicbrainz")
              and radio["02. Heart of the Country.mp3"] == ("rec-heart", "musicbrainz"),
              f"each track with a code its recording's alone, named: {radio}")
        check(radio["01. Band on the Run.mp3"][1] == "cd:text" and radio["02. Arrow Through Me.mp3"][1] == "cd:text",
              f"a code MusicBrainz files under two recordings, and no code: both wait for a person: {radio}")
        check(sorted(set(isrcs_asked)) == ["GBCCS1000004", "GBCCS1000115", "GBCCS1100094"],
              f"only the codes the editions would confirm were asked after: {isrcs_asked}")
        check(all(album_[n] == radio[n][0] for n in radio), f"both passages of each track alike: {album_}")
        offers = sorted(json.loads(sug)[0]["mbid"] for _pid, sug in s["checks"])
        check(offers == ["rec-arrow", "rec-band"],
              f"their review rows now offer the recording; the others are gone: {s['checks']}")
        fl = {(sid, ch): v for sid, ch, v in s["flavor"]}
        check(fl[("rec-amazed", "danceability")] == 0.9 and fl[("rec-amazed", "mood_happy")] == 0.5
              and ("local:audio:11" + "0" * 30 + ":0", "danceability") in fl,
              f"flavor moved, except what the recording had: that stands, and the placeholder's stays: {fl}")
        check(fl[("rec-heart", "danceability")] == 0.5, "a recording with none takes all of it")
        dec = [d for st, o, d in s["decisions"] if st == "identify"]
        check(sorted((d["track"], d["to"], d["by"]) for d in dec)
              == [(1, "rec-amazed", "isrc"), (2, "rec-heart", "isrc")],
              f"each change recorded, by what: {dec}")
        check(not any(st == "rip" for st, o, d in s["decisions"]), "no edition chosen: no disc decision")
        lc = sqlite3.connect(listener)
        check(lc.execute("SELECT mbid FROM listener_play_history").fetchone()[0] == "rec-amazed",
              "the play moved with its track")
        lc.close()
        c = sqlite3.connect(db)
        check(not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'release_recordings'").fetchone(),
              "and no album link without a pick")
        c.close()

        print("again: nothing left to do but the tracks a person must name")
        looked.clear()
        check(run(db, commit=True) == 0 and looked == [2] and state(db)["links"] == s["links"],
              f"only disc 2 looked at, nothing changed: {looked}")

        print("--release: a person's pick of edition, after the add")
        before = state(db)
        check(run(db, commit=True, folder=album, release="rel-nowhere") == 2 and state(db) == before,
              "a release the lookups did not find is refused, and nothing written")
        check(run(db, commit=True, folder=album, release="rel-disc1-only") == 2 and state(db) == before,
              "a release only one of the set's discs finds is refused for both, and nothing written")
        # Both discs' sheets stay in the one folder: the set's edition is each disc's.
        check(run(db, commit=True, listener=listener, folder=album, release="rel-au") == 0, "ran")
        s2 = state(db)
        radio2 = {os.path.basename(p): m for p, k, m, src in s2["links"] if k == "radio"}
        check(radio2["01. Band on the Run.mp3"] == "rec-band" and radio2["02. Arrow Through Me.mp3"] == "rec-arrow",
              f"the pick names both tracks left: {radio2}")
        check(radio2["01. Maybe I’m Amazed.mp3"] == "rec-amazed", "and leaves the ISRC's answers as they were")
        rip_rows = [(o, d["chosen"], d["chosen_by"], d["track"]) for st, o, d in s2["decisions"] if st == "rip"]
        check(sorted(rip_rows) == [("chosen", "rel-au", "person", 1)] * 2 + [("chosen", "rel-au", "person", 2)] * 2,
              f"a disc decision a file, both discs, a person's pick: {rip_rows}")
        c = sqlite3.connect(db)
        links = sorted(c.execute("SELECT release_mbid, mbid, position FROM release_recordings WHERE chosen = 1"))
        c.close()
        check(links == [("rel-au", "rec-amazed", 1), ("rel-au", "rec-arrow", 2), ("rel-au", "rec-band", 1),
                        ("rel-au", "rec-heart", 2)],
              f"both discs linked as their tracks' album: {links}")
    finally:
        (ingest_cd.lookup_disc_id, ingest_cd.lookup_barcode, ingest_cd.lookup_cd_text,
         ingest_cd.fetch_releases.get, ingest_cd.fetch_releases.RATE_S) = old

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("identify_cd_rips: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
