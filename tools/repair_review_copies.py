#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Undo the review copies the star sync made, on the hub [SPEC-STAR-051].

Until 2026-10-18 the star merge wrote a node's listener copy in the hub's
passage numbers and read it back as the node's, so each sync copied every id
review onto whatever passage its number meant on a node. Seven decisions
became 44 rows, and the twelve copies that were applied renamed real songs
("Late for the Sky", "Purple Rain", ...) after Planet Earth tracks.

This finds and undoes that, from evidence rather than guesswork:

- **Which rows are copies.** A decision made once is one row. Where several
  rows share a `decided_at`, the originals are the rows the first nightly
  backup after that moment holds -- taken before any sync could copy them.
  The rest are copies. Where a copy names a placeholder id
  (`local:audio:<md5>:0`) as its previous recording, that placeholder must
  belong to another file than the copy's passage; that is checked and said.
- **What an applied copy changed.** Applying replaced the passage's
  `passage_recordings` link and nothing else (`apply_reviews.py`). The link
  is put back as the newest nightly backup taken before the copy was applied
  held it, for the same passage -- same file, kind and start -- or the
  passage is named and left alone.

Rehearses by default and writes nothing. With --commit: both halves are
backed up first (into data/backups/pre-repair-<UTC>/), the local player is
paused, the copies are deleted and the links restored in one transaction
per half, the player is asked to rebuild against the library, and playback
resumes if it was playing.

    python tools/repair_review_copies.py data/library.db
    python tools/repair_review_copies.py data/library.db --commit
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_control  # noqa: E402


def say(text: str = "") -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def when(s: str) -> dt.datetime:
    """`decided_at`/`applied_at` ('YYYY-MM-DD HH:MM:SS', UTC) or a backup's
    ISO `taken_at`, as an aware UTC time."""
    s = s.replace("T", " ").replace("Z", "")
    d = dt.datetime.fromisoformat(s[:26] if "+" not in s else s)
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def open_ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)


def daily_backups(root: str) -> list[dict]:
    """The nightly backups, oldest first: taken_at and each half's file."""
    days = os.path.join(root, "days")
    out = []
    for name in sorted(os.listdir(days)):
        if not name.endswith(".json"):
            continue
        m = json.load(open(os.path.join(days, name), encoding="utf-8"))
        obj = lambda h: os.path.join(root, "objects", h + ".db")  # noqa: E731
        out.append(dict(day=name[:-5], taken_at=when(m["taken_at"]),
                        library=obj(m["library"]), listener=obj(m["listener"])))
    return out


def passage_key(conn, pid):
    return conn.execute("SELECT f.audio_md5, p.kind, p.start_ms FROM passages p "
                        "JOIN files f USING(file_id) WHERE p.passage_id=?", (pid,)).fetchone()


def title_of(conn, mbid):
    r = conn.execute("SELECT title FROM recordings WHERE mbid=?", (mbid,)).fetchone()
    return r[0] if r else "?"


def plan(library: str, listener: str, backups: list[dict]) -> dict:
    lib = sqlite3.connect(f"file:{library}?mode=ro", uri=True)
    lib.execute(f"ATTACH 'file:{listener}?mode=ro' AS l")
    groups = lib.execute("SELECT decided_at FROM l.id_reviews GROUP BY decided_at "
                         "HAVING COUNT(*) > 1 ORDER BY decided_at").fetchall()
    out = dict(groups=[], deletes=[], restores=[], unresolved=[], already=[])
    pr_cols = [r[1] for r in lib.execute("PRAGMA table_info(passage_recordings)")]
    for (decided,) in groups:
        rows = lib.execute("SELECT passage_id, decision, previous_mbid, chosen_mbid, applied_at "
                           "FROM l.id_reviews WHERE decided_at=? ORDER BY passage_id", (decided,)).fetchall()
        # The first nightly backup after the decision that holds it.
        ref = None
        for b in backups:
            if b["taken_at"] <= when(decided):
                continue
            c = open_ro(b["listener"])
            held = [r[0] for r in c.execute("SELECT passage_id FROM id_reviews WHERE decided_at=?", (decided,))]
            c.close()
            if held:
                ref = (b, held)
                break
        if ref is None:
            out["unresolved"].append(f"{decided}: no backup holds this decision -- left alone")
            continue
        b, originals = ref
        g = dict(decided=decided, reference=b["day"], originals=originals, copies=[])
        if len(originals) != 1:
            out["unresolved"].append(f"{decided}: the backup of {b['day']} already holds "
                                     f"{len(originals)} rows -- not one original; left alone")
            continue
        for pid, decision, prev, chosen, applied in rows:
            if pid in originals:
                continue
            key = passage_key(lib, pid)
            note = ""
            if prev and prev.startswith("local:audio:") and key:
                note = ("placeholder from another file" if prev.split(":")[2] != key[0]
                        else "PLACEHOLDER IS THIS PASSAGE'S OWN FILE")
            copy = dict(passage_id=pid, decision=decision, applied_at=applied, note=note,
                        chosen=title_of(lib, chosen) if chosen else None)
            g["copies"].append(copy)
            out["deletes"].append(pid)
            if not applied:
                continue
            # Only a passage still holding what the copy applied is put back.
            # Found in the rehearsal of 2026-10-18: two applied copies had
            # since been replaced by a later identification, and are right.
            now = [r[0] for r in lib.execute("SELECT mbid FROM passage_recordings WHERE passage_id=?", (pid,))]
            if now != [chosen]:
                out["already"].append(f"passage {pid}: holds {', '.join(title_of(lib, m) for m in now)} now, "
                                      f"not the copy's '{title_of(lib, chosen)}' -- changed since; left alone")
                continue
            # The link before this copy was applied: the newest backup taken
            # before then, holding the same passage (file, kind, start).
            before = [x for x in backups if x["taken_at"] < when(applied)]
            found = None
            for x in reversed(before):
                c = open_ro(x["library"])
                k = passage_key(c, pid)
                if k == key:
                    cols = [r[1] for r in c.execute("PRAGMA table_info(passage_recordings)")]
                    links = [dict(zip(cols, r)) for r in c.execute(
                        "SELECT * FROM passage_recordings WHERE passage_id=?", (pid,))]
                    titles = [title_of(c, r["mbid"]) for r in links]
                    found = (x["day"], links, titles)
                c.close()
                if found:
                    break
            if not found or not found[1]:
                out["unresolved"].append(f"passage {pid}: no backup before {applied} holds it with a "
                                         "link -- its link is left as it is")
                continue
            missing = [r["mbid"] for r in found[1]
                       if not lib.execute("SELECT 1 FROM recordings WHERE mbid=?", (r["mbid"],)).fetchone()]
            if missing:
                out["unresolved"].append(f"passage {pid}: the recording(s) {missing} it had are not in the "
                                         "library now -- left as it is")
                continue
            out["restores"].append(dict(
                passage_id=pid, from_day=found[0], now=[title_of(lib, m) for m in now],
                back=found[2], rows=[{c: r.get(c) for c in pr_cols} for r in found[1]],
                path=lib.execute("SELECT f.path FROM passages p JOIN files f USING(file_id) "
                                 "WHERE p.passage_id=?", (pid,)).fetchone()[0]))
        out["groups"].append(g)
    lib.close()
    return out


def report(p: dict) -> None:
    for g in p["groups"]:
        say(f"decision of {g['decided']}: original passage {g['originals'][0]} "
            f"(the backup of {g['reference']}), {len(g['copies'])} copies")
        for c in g["copies"]:
            state = f"applied {c['applied_at']}" if c["applied_at"] else "pending"
            say(f"    copy on passage {c['passage_id']}: {c['decision']}"
                + (f" -> '{c['chosen']}'" if c["chosen"] else "") + f", {state}"
                + (f" ({c['note']})" if c["note"] else ""))
    say()
    say(f"{len(p['deletes'])} copied review row(s) to delete")
    say(f"{len(p['restores'])} passage link(s) to put back:")
    for r in p["restores"]:
        name = r["path"].replace("\\", "/").split("/Music/")[-1]
        say(f"    passage {r['passage_id']}: {', '.join(r['now'])} -> {', '.join(r['back'])}"
            f"  (as of {r['from_day']})  {name}")
    for a in p["already"]:
        say(f"  already right: {a}")
    for u in p["unresolved"]:
        say(f"  NOT CHANGED: {u}")


def commit(library: str, listener: str, p: dict, backup_root: str) -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%MZ")
    dest = os.path.join(backup_root, f"pre-repair-{stamp}")
    os.makedirs(dest)
    for src in (library, listener):
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        d = sqlite3.connect(os.path.join(dest, os.path.basename(src)))
        s.backup(d)
        ok = d.execute("PRAGMA integrity_check").fetchone()[0]
        d.close()
        s.close()
        if ok != "ok":
            raise SystemExit(f"the backup of {src} failed its integrity check ({ok}): nothing written")
    say(f"backed up both halves to {dest}, integrity ok")

    was_playing = lempi_control.pause_lempi()
    say("local player paused" if was_playing else "no local player playing; nothing to pause")
    try:
        lib = sqlite3.connect(library, timeout=30)
        try:
            lib.execute("BEGIN IMMEDIATE")
            for r in p["restores"]:
                lib.execute("DELETE FROM passage_recordings WHERE passage_id=?", (r["passage_id"],))
                for row in r["rows"]:
                    cols = list(row)
                    lib.execute(f"INSERT INTO passage_recordings ({', '.join(cols)}) "
                                f"VALUES ({', '.join('?' * len(cols))})", [row[c] for c in cols])
            lib.execute("COMMIT")
        finally:
            lib.close()
        say(f"{len(p['restores'])} passage link(s) put back")
        lis = sqlite3.connect(listener, timeout=30)
        try:
            lis.execute("BEGIN IMMEDIATE")
            for g in p["groups"]:
                for c in g["copies"]:
                    lis.execute("DELETE FROM id_reviews WHERE passage_id=? AND decided_at=?",
                                (c["passage_id"], g["decided"]))
            lis.execute("COMMIT")
        finally:
            lis.close()
        say(f"{len(p['deletes'])} copied review row(s) deleted")
    finally:
        if lempi_control.reload_lempi_library():
            say("local player asked to rebuild against the library")
        if was_playing:
            say("playback resumed" if lempi_control.play_lempi() else "COULD NOT resume playback")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("library", help="the hub's library.db; its listener.db is beside it")
    ap.add_argument("--listener", help="the listener half, if not beside the library")
    ap.add_argument("--backups", help="the nightly backups (default: data/backups beside the library)")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    library = os.path.abspath(a.library)
    listener = os.path.abspath(a.listener or os.path.join(os.path.dirname(library), "listener.db"))
    backups = os.path.abspath(a.backups or os.path.join(os.path.dirname(library), "backups"))
    say(f"library {library}\nlistener {listener}\nbackups {backups}")
    p = plan(library, listener, daily_backups(backups))
    report(p)
    if not a.commit:
        say("\nnothing was written. Re-run with --commit to do it.")
        return 0
    commit(library, listener, p, backups)
    return 0


if __name__ == "__main__":
    sys.exit(main())
