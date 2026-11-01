#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Identify the tracks of CD rips already added, by the rules an add uses now
`[SPEC-CDI-095]`.

*Pure McCartney* went in on 2026-10-31 as 24 tracks and no identity: its rips
stopped early and wrote no log, so there was no Disc ID; its sheets' `CATALOG`
was each disc's own code, not the set's barcode; and every track waited in the
review queue with nothing to offer. An add now also asks the disc's CD-TEXT
`[SPEC-CDI-093]`, and takes a track's recording with no pick when the disc's
ISRC confirms it `[SPEC-RIP-067]`. This brings a rip added before that up to
the same answer, so a disc added on the wrong side of a fix is never a one-off.

Each rip is found by its own sheet, still in the album folder: a folder with
two discs has two sheets, and each names its own files. Only a track still on
its placeholder (`local:audio:`, waiting in review) is looked at; one a person
or a lookup already named is left as it is.

    python tools/identify_cd_rips.py data/library.db                 # say what it would do
    python tools/identify_cd_rips.py data/library.db --commit --listener data/listener.db
    python tools/identify_cd_rips.py data/library.db --folder "C:/Music/X/Y" \\
        --release <mbid> --commit --listener data/listener.db       # a person's pick of edition
    python tools/identify_cd_rips.py data/library.db --audit --cache <dir>

A track identified is re-pointed as `relink_cd_disc.py` re-points one: both
its passages, its review row (gone: an add that identifies writes none), the
listener's plays of it with `--listener`, and its flavor -- moved to the
recording, except what the recording already has from another copy, which
stands. Each change is an `ingest_decisions` row, stage `identify`, saying
what it was. A track still not identified, where the lookup found candidates
and its review row offered none, is given them to choose from.

`--release`, with `--folder`: the edition a person chose, as at an add
`[SPEC-CDI-040]` -- every track it names is identified, and the disc is linked
as their album `[SPEC-CDI-098]`. A folder holding a set's discs takes the
set's edition for each of them; a disc that does not find it refuses it for
all.

`--audit` writes nothing. For every rip whose tracks are identified, it asks
what the CD-TEXT lookup would have answered had it been the only lookup, and
what the ISRC rule would have taken without a pick, from the CD-TEXT
candidates and from the disc's own Disc ID; each is compared with the
recording the track has. `--cache` keeps every MusicBrainz answer in a folder,
so a second audit asks nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cd_toc           # noqa: E402
import fetch_releases   # noqa: E402
import ingest_cd        # noqa: E402
import lempi_db         # noqa: E402

PLACEHOLDER = ("cd:text", "cd:ambiguous", "cd:unidentified")
STAGE = "identify"


def say(text: str) -> None:
    ingest_cd.say(text)


def paced_get(cache: str | None):
    """`fetch_releases.get` at one request a second, every answer kept in
    `cache` when given. The lookups' own pauses are turned off while it is in
    place, so a cached answer costs no wait."""
    real, gap, last = fetch_releases.get, fetch_releases.RATE_S, [0.0]

    def get(url: str, tries: int = 4):
        path = os.path.join(cache, hashlib.sha1(url.encode()).hexdigest() + ".json") if cache else None
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)["doc"]
        wait = gap - (time.monotonic() - last[0])
        if wait > 0:
            time.sleep(wait)
        doc = real(url, tries)
        last[0] = time.monotonic()
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"url": url, "doc": doc}, f)
        return doc
    return get


# ------------------------------------------------------------------ the rips --

def _latest_rip_details(conn) -> dict[str, dict]:
    """Each file's own disc decision, the newest: the row an add writes with
    the disc's `format`, not a missing or damaged track's."""
    out = {}
    for md5, outcome, detail in conn.execute(
            "SELECT audio_md5, outcome, detail FROM ingest_decisions WHERE stage = 'rip' ORDER BY decision_id"):
        d = json.loads(detail or "{}")
        if "format" in d:
            out[md5] = dict(d, _outcome=outcome)
    return out


def _sidecar_log(conn, md5s, folder: str) -> str | None:
    for (detail,) in conn.execute(
            f"SELECT detail FROM ingest_decisions WHERE stage = 'sidecars' AND audio_md5 IN "
            f"({','.join('?' * len(md5s))}) ORDER BY decision_id DESC", list(md5s)):
        for name in (json.loads(detail or "{}").get("files") or {}):
            if name.lower().endswith(".log") and os.path.isfile(os.path.join(folder, name)):
                return os.path.join(folder, name)
    return None


def _passages(conn, file_id: int) -> dict[str, list]:
    out = {"radio": [], "album": []}
    for pid, kind, start, end, mbid, source in conn.execute(
            "SELECT p.passage_id, p.kind, p.start_ms, p.end_ms, pr.mbid, pr.source FROM passages p "
            "LEFT JOIN passage_recordings pr ON pr.passage_id = p.passage_id "
            "WHERE p.file_id = ?1 ORDER BY p.start_ms", (file_id,)):
        if kind in out:
            out[kind].append({"pid": pid, "start_ms": start, "end_ms": end, "mbid": mbid, "source": source})
    return out


def rips(conn, folder: str | None = None) -> list[dict]:
    """Every CD rip whose sheet is still beside its audio, as the disc it is:
    `toc` with each present track's length, and `tracks` -- each track's
    number, file, radio and album passages, and recording."""
    decided = _latest_rip_details(conn)
    files: dict[str, dict] = {}
    for md5 in decided:
        row = conn.execute("SELECT file_id, path, duration_ms FROM files WHERE audio_md5 = ?1", (md5,)).fetchone()
        if row and row[1]:
            files[os.path.normcase(os.path.abspath(row[1]))] = {"file_id": row[0], "path": row[1],
                                                                  "duration_ms": row[2], "md5": md5}
    folders = sorted({os.path.dirname(f["path"]) for f in files.values()})
    if folder:
        folders = [d for d in folders if os.path.normcase(os.path.abspath(d)) == os.path.normcase(os.path.abspath(folder))]
    out = []
    for d in folders:
        try:
            sheets = sorted(n for n in os.listdir(d) if n.lower().endswith(".cue"))
        except OSError:
            continue
        for sheet in sheets:
            try:
                toc = cd_toc.parse_eac_cue(os.path.join(d, sheet))
            except (OSError, ValueError):
                continue
            mine = {n: files.get(os.path.normcase(os.path.abspath(os.path.join(d, n)))) for n in toc.files}
            if not any(mine.values()):
                continue        # an earlier rip's sheet, its files retired
            rip = {"folder": d, "sheet": sheet, "toc": toc, "tracks": [], "tracks_mode": toc.tracks_mode}
            if toc.tracks_mode:
                for t in toc.tracks:
                    f = mine.get(t.file)
                    if f is None:
                        continue
                    t.end_ms = f["duration_ms"] or 0
                    ps = _passages(conn, f["file_id"])
                    if ps["radio"]:
                        rip["tracks"].append({"number": t.number, "file": f, "radio": ps["radio"][0],
                                              "album": ps["album"][0] if ps["album"] else None})
            else:
                f = mine.get(toc.data_file)
                if f is None or not f["duration_ms"]:
                    continue
                cd_toc.finalize_leadout(toc, f["duration_ms"])
                ps = _passages(conn, f["file_id"])
                for t in toc.tracks:
                    radio = next((p for p in ps["radio"] if p["start_ms"] == t.start_ms), None)
                    if radio:
                        album = next((p for p in ps["album"] if p["start_ms"] == t.start_ms), None)
                        rip["tracks"].append({"number": t.number, "file": f, "radio": radio, "album": album})
            if not rip["tracks"]:
                continue
            md5s = sorted({t["file"]["md5"] for t in rip["tracks"]})
            rip["md5s"] = md5s
            rip["decision"] = decided.get(rip["tracks"][0]["file"]["md5"]) or {}
            log = _sidecar_log(conn, md5s, d) if toc.tracks_mode else None
            rip["log_toc"] = cd_toc.toc_from_log(log) if log else None
            out.append(rip)
    return out


def waiting(rip: dict) -> list[dict]:
    """The rip's tracks still on their placeholder."""
    return [t for t in rip["tracks"] if (t["radio"]["mbid"] or "").startswith("local:audio:")
            and t["radio"]["source"] in PLACEHOLDER]


# ---------------------------------------------------------------- the fix --

def _candidates(releases: list[dict], number: int) -> list[dict]:
    out: list[dict] = []
    for rel in releases[:8]:
        c = ingest_cd._candidate_for(rel, number)
        if c is not None and all(c["mbid"] != s["mbid"] for s in out):
            out.append(c)
    return out


def identify(conn, listener, rip: dict, outcome: str, releases: list[dict], chosen: bool) -> dict:
    """Each waiting track, by `ingest_cd._resolve_track` itself -- the add's own
    rule, not a copy of it -- in a savepoint kept only when it identifies.
    The caller commits."""
    toc, now = rip["toc"], ingest_cd._now()
    by_number = {t.number: t for t in toc.tracks}
    done, offered, flavor_kept, plays = [], 0, 0, 0
    for tr in waiting(rip):
        t, radio, album = by_number[tr["number"]], tr["radio"], tr["album"]
        conn.execute("SAVEPOINT one_track")
        result, mbid, source = ingest_cd._resolve_track(
            conn, t.number, t.title or toc.title, radio["pid"], tr["file"]["path"], radio["start_ms"],
            radio["end_ms"], tr["file"]["md5"], outcome, releases, chosen, None, now, isrc=t.isrc)
        if result != "identified" or mbid == radio["mbid"]:
            conn.execute("ROLLBACK TO one_track")
            conn.execute("RELEASE one_track")
            cands = _candidates(releases, t.number) if outcome != "none" else []
            if cands and conn.execute("SELECT 1 FROM id_checks WHERE passage_id = ?1 AND suggested IS NULL",
                                      (radio["pid"],)).fetchone():
                conn.execute("UPDATE id_checks SET suggested = ?1, score = ?2 WHERE passage_id = ?3",
                             (json.dumps(cands), cands[0]["score"], radio["pid"]))
                offered += 1
            continue
        conn.execute("RELEASE one_track")
        old = radio["mbid"]
        pids = [p["pid"] for p in (radio, album) if p]
        q = ",".join("?" * len(pids))
        conn.execute(f"UPDATE passage_recordings SET mbid = ?, source = ? WHERE mbid = ? AND passage_id IN ({q})",
                     [mbid, source, old, *pids])
        conn.execute(f"DELETE FROM id_checks WHERE passage_id IN ({q})", pids)
        try:
            moved = conn.execute("UPDATE OR IGNORE flavor SET subject_id = ?1 WHERE subject_kind = 'recording' "
                                 "AND subject_id = ?2", (mbid, old)).rowcount
            kept = conn.execute("SELECT COUNT(*) FROM flavor WHERE subject_kind = 'recording' AND subject_id = ?1",
                                (old,)).fetchone()[0]
        except sqlite3.OperationalError:
            moved = kept = 0        # no flavor table: nothing measured to move
        flavor_kept += kept
        if listener is not None:
            plays += listener.execute(f"UPDATE listener_play_history SET mbid = ? WHERE mbid = ? AND passage_id IN ({q})",
                                      [mbid, old, *pids]).rowcount
        by = "person" if chosen else ("isrc" if ingest_cd.isrc_confirms(releases, t.number, t.isrc) else "single")
        conn.execute("INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
                     "VALUES (?1,?2,'identified',1.0,?3,?4)",
                     (tr["file"]["md5"], STAGE,
                      json.dumps({"track": t.number, "from": old, "to": mbid, "by": by, "isrc": t.isrc,
                                  "lookup": outcome, "candidates": [r.get("id") for r in releases[:8]],
                                  "passages": pids, "flavor_moved": moved, "flavor_kept_on_old": kept}), now))
        title = conn.execute("SELECT title FROM recordings WHERE mbid = ?1", (mbid,)).fetchone()
        done.append({"track": t.number, "from": old, "to": mbid, "by": by, "title": title[0] if title else None})
    return {"identified": done, "offered": offered, "flavor_kept": flavor_kept, "plays": plays}


def link_edition(conn, rip: dict, release: dict) -> int:
    """`[SPEC-CDI-098]` for a pick made after the add: the disc as its tracks'
    album, and a disc decision saying a person chose it, as an add's would --
    what `relink_cd_disc.py` and `backfill_cd_releases.py` read."""
    placed, now = [], ingest_cd._now()
    for tr in rip["tracks"]:
        m = conn.execute("SELECT mbid FROM passage_recordings WHERE passage_id = ?1 ORDER BY weight DESC LIMIT 1",
                         (tr["radio"]["pid"],)).fetchone()
        if m and not m[0].startswith("local:"):
            placed.append((tr["number"], m[0]))
    decided = _latest_rip_details(conn)
    for md5 in rip["md5s"]:
        d = dict(decided.get(md5) or {})
        d.pop("_outcome", None)
        if rip["tracks_mode"]:
            d["track"] = next(t["number"] for t in rip["tracks"] if t["file"]["md5"] == md5)
        d.update(chosen=release.get("id"), chosen_by="person", candidates=1, titles=[release.get("title")],
                 picked_after_add=True)
        conn.execute("INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
                     "VALUES (?1,'rip','chosen',1.0,?2,?3)", (md5, json.dumps(d), now))
    return ingest_cd.record_release(conn, release, placed, rip["md5s"]) if placed else 0


def fix(args) -> int:
    # Writable even to rehearse: the add's own rule writes as it decides, so a
    # rehearsal runs it inside a transaction and rolls that back -- the same
    # answer --commit would keep, not a second account of what it might be.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    listener = (sqlite3.connect(args.listener, timeout=60, isolation_level=None)
                if args.commit and args.listener else None)
    say(f"library {args.db} ({'written' if args.commit else 'a rehearsal: each rip decided, then rolled back'}); "
        f"listener {args.listener if listener is not None else 'not opened: plays are not moved'}")
    found = [r for r in rips(conn, args.folder) if waiting(r) or args.release]
    if args.release and not found:
        say(f"--release names the edition of the rips in {args.folder!r}: there are none -- nothing done")
        return 2
    # Every rip looked up before any is written: a set's edition is each of
    # its discs' -- each cut to its own disc `[SPEC-CDI-099]` -- so one disc
    # that does not find it refuses the pick for all of them.
    looked = []
    for rip in found:
        outcome, releases, _disc_id = ingest_cd.identify_disc(rip["toc"], rip["log_toc"])
        if args.release:
            releases = [r for r in releases if r.get("id") == args.release]
            if not releases:
                say(f"{os.path.join(rip['folder'], rip['sheet'])}: release {args.release} is not among "
                    "this disc's matches -- nothing done")
                return 2
        looked.append((rip, outcome, releases))
    total = offered = 0
    for rip, outcome, releases in looked:
        wait = waiting(rip)
        say(f"{os.path.join(rip['folder'], rip['sheet'])}: {len(wait)} of {len(rip['tracks'])} track(s) waiting")
        countries = ", ".join(filter(None, (r.get("country") for r in releases)))
        say(f"  lookup: {outcome}, {len(releases)} candidate edition(s)" + (f" -- {countries}" if countries else ""))
        if not releases:
            continue
        conn.execute("BEGIN IMMEDIATE")
        if listener is not None:
            listener.execute("BEGIN IMMEDIATE")
        try:
            res = identify(conn, listener, rip, outcome, releases, chosen=bool(args.release))
            linked = link_edition(conn, rip, releases[0]) if args.release else 0
            if args.commit:
                conn.commit()
                if listener is not None:
                    listener.execute("COMMIT")
            else:
                conn.rollback()
        except Exception:
            conn.rollback()
            if listener is not None:
                listener.execute("ROLLBACK")
            raise
        for d in res["identified"]:
            say(f"    track {d['track']}: {d['title']!r} -> {d['to']} (by {d['by']})")
        left = len(wait) - len(res["identified"])
        say(f"  {len(res['identified'])} identified, {left} still for review"
            + (f", {res['offered']} given candidates to choose from" if res["offered"] else "")
            + (f"; the disc linked as {linked} track(s)' album" if linked else "")
            + (f"; {res['flavor_kept']} flavor row(s) left on the placeholder, the recording having its own"
               if res["flavor_kept"] else "")
            + (f"; {res['plays']} play(s) moved" if listener is not None else ""))
        total += len(res["identified"])
        offered += res["offered"]
    say(f"\n{len(found)} rip(s) looked at; {total} track(s) identified, {offered} given candidates"
        + ("" if args.commit else " -- nothing written; --commit (and --listener) to write it"))
    return 0


# --------------------------------------------------------------- the audit --

def audit(args) -> int:
    """`[SPEC-CDI-093]`'s measurement: read-only, the answer to "would the new
    lookups ever have named a recording other than the one the track has?" """
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY)
    found = rips(conn, args.folder)
    say(f"library {args.db}, read only; {len(found)} rip(s) with their sheet beside them")
    tally = {k: 0 for k in ("rips", "titled", "found", "pick_among", "tracks", "agree", "differ", "split", "none",
                            "isrc_tracks", "isrc_agree", "isrc_differ", "disc_isrc_agree", "disc_isrc_differ")}
    differ = []
    for i, rip in enumerate(found, 1):
        toc = rip["toc"]
        named = {t["number"]: t["radio"]["mbid"] for t in rip["tracks"]
                 if t["radio"]["mbid"] and not t["radio"]["mbid"].startswith("local:")}
        if not named:
            continue
        tally["rips"] += 1
        where = os.path.join(os.path.basename(rip["folder"]), rip["sheet"])
        rels = []
        if toc.title:
            tally["titled"] += 1
            _o, rels = ingest_cd.lookup_cd_text(toc)
            ingest_cd.mark_shared_isrcs(toc, rels)      # as identify_disc does for these
            tally["found"] += bool(rels)
            tally["pick_among"] += rip["decision"].get("chosen") in {r.get("id") for r in rels}
        by_isrc = {t.number: t.isrc for t in toc.tracks}
        disc_rels = []
        if rip["decision"].get("disc_id"):
            doc = fetch_releases.get(ingest_cd._discid_url(rip["decision"]["disc_id"], None))
            disc_rels = ingest_cd.for_this_disc((doc or {}).get("releases") or [], rip["decision"]["disc_id"],
                                                rip["log_toc"] or toc)
        for n, have in sorted(named.items()):
            tally["tracks"] += 1
            cands = _candidates(rels, n)
            verdict = "none" if not cands else ("split" if len(cands) > 1 else
                                                ("agree" if cands[0]["mbid"] == have else "differ"))
            tally[verdict] += 1
            if verdict == "differ":
                differ.append((where, n, "cd-text", have, cands[0]))
            isrc = by_isrc.get(n)
            if isrc:
                tally["isrc_tracks"] += 1
                for key, pool in (("isrc", rels), ("disc_isrc", disc_rels)):
                    m = ingest_cd.isrc_confirms(pool, n, isrc)
                    if m:
                        tally[f"{key}_{'agree' if m == have else 'differ'}"] += 1
                        if m != have:
                            differ.append((where, n, key, have, {"mbid": m}))
        say(f"  {i}/{len(found)} {where}: {len(rels)} CD-TEXT edition(s), {len(disc_rels)} by Disc ID")
    say("\n" + json.dumps(tally, indent=1))
    for where, n, how, have, c in differ:
        t = conn.execute("SELECT title FROM recordings WHERE mbid = ?1", (have,)).fetchone()
        say(f"DIFFERS ({how}) {where} track {n}: has {have} {t[0] if t else ''!r}, "
            f"would be {c['mbid']} {c.get('title') or ''!r}")
    return 1 if differ else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("db")
    ap.add_argument("--commit", action="store_true", help="write; without it, say what it would do")
    ap.add_argument("--listener", help="the listener database, whose plays of an identified track move with it")
    ap.add_argument("--folder", help="only the rips in this album folder")
    ap.add_argument("--release", help="with --folder: the edition a person chose for its rips")
    ap.add_argument("--audit", action="store_true", help="measure the lookups against identified rips; writes nothing")
    ap.add_argument("--cache", help="a folder keeping every MusicBrainz answer, for --audit or a re-run")
    args = ap.parse_args()
    if args.release and not args.folder:
        ap.error("--release names an album's edition: give its --folder")
    if args.audit and (args.commit or args.release):
        ap.error("--audit writes nothing: not with --commit or --release")
    if args.cache:
        os.makedirs(args.cache, exist_ok=True)
    fetch_releases.get = paced_get(args.cache)
    fetch_releases.RATE_S = 0
    return audit(args) if args.audit else fix(args)


if __name__ == "__main__":
    sys.exit(main())
