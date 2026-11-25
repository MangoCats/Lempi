#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Give a phone's catalogue its passage uids in place, on the hub [IMPL-UID-066].

Stage A for a phone. The six nodes had their uids carried to them by star
sync's natural patch [IMPL-UID-052]; a phone gets no such patch, so a phone
built before Stage A holds a catalogue with no `passage_uid` at all, and an
app built after it refuses to open the listener beside it [IMPL-UID-060].
Replacing the phone's catalogue would renumber its passages under a listener
that still names them by number. This keeps every number and gives each
passage its uid, so the app's own conversion is then the exact join it is
on every node [IMPL-UID-062].

It works on a **copy** of the phone's catalogue, pulled while the app is
stopped (`android/phone-uids.sh` pulls it, runs this, and puts it back). Each
passage, by the rule `bundle.rs`'s `fill_uid` gives a resent bundle
[SPEC-PL-110], and one more:
- of a file the hub holds (by `audio_md5`), takes the hub's uid and birth key:
  the one hub passage of that file and kind with this span now, or failing
  that the one born at it, or failing both, where the file has exactly one
  passage of that kind here and one on the hub, that one -- a boundary edit
  the phone never received. Measured 2026-11-25 on the hub's catalogue of
  2026-11-06, a stand-in for a phone that missed that day's retiming: 433 of
  22,789 passages matched neither span. Each is checked against its birth
  key, as a bundle's is;
- of a file the hub does not hold, is born here: its uid from its own span,
  with the lowest salt not already born on that key, as the phone's scan
  gives a passage it finds today (`Birth::Here`).
A passage of a hub file that matches no hub passage is never guessed: it is
listed, and a commit is refused while there is one.

The catalogue gains the columns, the uids and the unique index
`passages_uid` -- what star sync gave the nodes [IMPL-UID-056], and no more:
no node holds the hub's triggers or `passage_lineage`.

With `--listener`, it also counts what the app's conversion will do to the
phone's listener: the rows that name a passage, and any whose passage will
have no uid, which convert to a NULL and are counted there too.

Rehearses by default and writes nothing. `--commit` needs `--expect N`, the
passage count the rehearsal printed. It first backs the copy up whole,
through SQLite's backup API, to `library.pre-uids.db` beside it, and refuses
a catalogue that already has uids.

    python tools/phone_uids.py work/library.db --listener work/listener.db
    python tools/phone_uids.py work/library.db --commit --expect 4310
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_db  # noqa: E402
import passage_uid as pu  # noqa: E402
import staged  # noqa: E402  -- utf8_output

COLUMNS = (("passage_uid", "TEXT"), ("birth_md5", "TEXT"), ("birth_kind", "TEXT"),
           ("birth_start_ms", "INTEGER"), ("birth_end_ms", "INTEGER"), ("birth_salt", "INTEGER"))
INDEX = "CREATE UNIQUE INDEX passages_uid ON passages(passage_uid)"
# listener_uid.rs's TABLES: each names a passage by `passage_id` until converted.
ONLY = "hub, the one passage of its file and kind on both, its span moved"
LISTENER_TABLES = ("listener_play_history", "listener_rejections", "selection_decisions", "player_state",
                   "player_queue", "id_reviews", "boundary_reviews", "artist_reviews")


def say(*a):
    print(*a, flush=True)


def hub_index(hub):
    """The hub's passages, by file: {audio_md5: [(kind, start, end, uid, birth)]}."""
    if not pu.has_uids(hub):
        raise SystemExit("refused: the hub's catalogue has no passage uids; Stage A comes first [IMPL-UID-052]")
    out = {}
    for md5, kind, s, e, uid, bm, bk, bs, be, salt in hub.execute(
            "SELECT f.audio_md5, p.kind, p.start_ms, p.end_ms, p.passage_uid, p.birth_md5, p.birth_kind, "
            "p.birth_start_ms, p.birth_end_ms, p.birth_salt FROM passages p JOIN files f USING (file_id)"):
        out.setdefault(md5, []).append((kind, s, e, uid, (bm, bk, bs, be, salt)))
    return out


def plan(phone, hub):
    """Each phone passage's identity. Returns (given, unmatched, counts):
    given is [(passage_id, uid, (md5, kind, start, end, salt), how)]."""
    rows = phone.execute("SELECT p.passage_id, f.audio_md5, p.kind, p.start_ms, p.end_ms FROM passages p "
                         "LEFT JOIN files f ON f.file_id = p.file_id ORDER BY p.passage_id").fetchall()
    orphans = [r[0] for r in rows if r[1] is None]
    if orphans:
        raise SystemExit(f"refused: {len(orphans)} passage(s) name no file, so have no birth key: {orphans[:10]}")
    given, unmatched, born = [], [], set()
    counts = {"hub, by its span now": 0, "hub, by its birth span": 0, ONLY: 0, "born here (a file the hub lacks)": 0}
    per_kind = {}
    for _, md5, kind, _, _ in rows:
        per_kind[(md5, kind)] = per_kind.get((md5, kind), 0) + 1
    for pid, md5, kind, s, e in rows:
        if md5 not in hub:
            continue
        found = None
        for how, pick in (("hub, by its span now", lambda h: (h[1], h[2]) == (s, e)),
                          ("hub, by its birth span", lambda h: (h[4][2], h[4][3]) == (s, e)),
                          # A boundary edit the phone never received [SPEC-PL-110] moved
                          # neither span here; one passage of this file and kind on each
                          # side is still one passage, as star sync paired a moved one
                          # before uids [SPEC-NKP-046]. Two or more on either side, never:
                          # here, by this count; on the hub, as `hits` is then all of them.
                          (ONLY, lambda h: per_kind[(md5, kind)] == 1)):
            hits = [h for h in hub[md5] if h[0] == kind and pick(h)]
            if len(hits) == 1:
                found = (how, hits[0])
                break
        if not found:
            unmatched.append((pid, md5, kind, s, e))
            continue
        how, (_, _, _, uid, birth) = found
        if pu.passage_uid(*birth) != uid:
            raise SystemExit(f"refused: the hub's uid {uid} is not its birth key's {birth} [SPEC-PL-110]")
        given.append((pid, uid, birth, how))
        born.add(birth)
        counts[how] += 1
    for pid, md5, kind, s, e in rows:
        if md5 in hub:
            continue
        salt = 0
        while (md5, kind, s, e, salt) in born:
            salt += 1
        birth = (md5, kind, s, e, salt)
        born.add(birth)
        given.append((pid, pu.passage_uid(*birth), birth, "born here (a file the hub lacks)"))
        counts["born here (a file the hub lacks)"] += 1
    seen = {}
    for pid, uid, _, _ in given:
        if uid in seen:
            raise SystemExit(f"refused: passages {seen[uid]} and {pid} would hold one uid {uid} [SPEC-UID-052]")
        seen[uid] = pid
    return given, unmatched, counts


def listener_report(path, with_uid):
    """What the app's conversion will make of each table [IMPL-UID-062]."""
    lis = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = {r[0] for r in lis.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        lost_total = 0
        for t in LISTENER_TABLES:
            if t not in names:
                continue
            if "passage_id" not in [r[1] for r in lis.execute(f"PRAGMA table_info({t})")]:
                say(f"  {t}: already by uid")
                continue
            ids = [r[0] for r in lis.execute(f"SELECT passage_id FROM {t} WHERE passage_id IS NOT NULL")]
            lost = sum(1 for i in ids if i not in with_uid)
            lost_total += lost
            say(f"  {t}: {len(ids)} row(s) name a passage; {lost} would convert to no uid")
        return lost_total
    finally:
        lis.close()


def main(argv=None) -> int:
    staged.utf8_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("library", help="a copy of the phone's catalogue, pulled with the app stopped")
    ap.add_argument("--hub", default=os.path.join(os.path.dirname(HERE), "data", "library.db"),
                    help="the hub's catalogue, read only (default data/library.db)")
    ap.add_argument("--listener", help="the copy of the phone's listener pulled with it, to count")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--expect", type=int)
    a = ap.parse_args(argv)

    if not os.path.isfile(a.library):
        raise SystemExit(f"refused: no catalogue at {a.library}")
    # A rehearsal opens it read only, so one can be run on a backup.
    phone = sqlite3.connect(a.library) if a.commit else sqlite3.connect(f"file:{a.library}?mode=ro", uri=True)
    if pu.has_uids(phone):
        phone.close()
        raise SystemExit("refused: this catalogue's passages already carry uids; a uid is never recalculated")
    hub = lempi_db.connect(a.hub, lempi_db.ROLE_LIBRARY, timeout=60)
    say(f"hub catalogue: {a.hub}, read only")
    given, unmatched, counts = plan(phone, hub_index(hub))
    hub.close()
    total = phone.execute("SELECT count(*) FROM passages").fetchone()[0]
    say(f"{total} passage(s) on the phone:")
    for how, n in counts.items():
        say(f"  {n:6}  {how}")
    say(f"  {len(unmatched):6}  of a hub file, matching no hub passage: never guessed")
    for pid, md5, kind, s, e in unmatched[:20]:
        say(f"          passage {pid}: {md5} {kind} {s}..{e}")
    sha_keyed = phone.execute("SELECT count(*) FROM files WHERE audio_md5 LIKE 'sha256:%'").fetchone()[0]
    if sha_keyed:
        say(f"  ({sha_keyed} file(s) still keyed by byte hash, born here under that key)")
    if a.listener:
        say(f"the listener beside it ({a.listener}), as the app will convert it:")
        lost = listener_report(a.listener, {pid for pid, *_ in given})
        say(f"  {lost} row(s) in all would lose their passage")
    if not a.commit:
        phone.close()
        say(f"\nnothing was written. To give them: --commit --expect {total}")
        return 0
    if unmatched:
        raise SystemExit(f"refused: {len(unmatched)} passage(s) of hub files match no hub passage; nothing was written")
    if a.expect != total or len(given) != total:
        raise SystemExit(f"refused: {total} passage(s), {len(given)} given, expected {a.expect}; nothing was written")
    backup = os.path.join(os.path.dirname(os.path.abspath(a.library)), "library.pre-uids.db")
    if os.path.exists(backup):
        raise SystemExit(f"refused: {backup} exists, and a backup is never written over; nothing was written")
    dst = sqlite3.connect(backup)
    phone.backup(dst)
    dst.close()
    say(f"backed up to {backup}")
    try:
        phone.execute("BEGIN IMMEDIATE")
        for name, decl in COLUMNS:
            phone.execute(f"ALTER TABLE passages ADD COLUMN {name} {decl}")
        phone.executemany("UPDATE passages SET passage_uid = ?, birth_md5 = ?, birth_kind = ?, birth_start_ms = ?, "
                          "birth_end_ms = ?, birth_salt = ? WHERE passage_id = ?",
                          [(uid, *birth, pid) for pid, uid, birth, _ in given])
        phone.execute(INDEX)
        missing = phone.execute("SELECT count(*) FROM passages WHERE passage_uid IS NULL").fetchone()[0]
        if missing:
            raise SystemExit(f"refused: {missing} passage(s) left without a uid; nothing was written")
        phone.execute("COMMIT")
    except BaseException:
        if phone.in_transaction:
            phone.execute("ROLLBACK")
        raise
    n = phone.execute("SELECT count(DISTINCT passage_uid) FROM passages").fetchone()[0]
    ok = phone.execute("PRAGMA quick_check").fetchone()[0]
    phone.close()
    say(f"gave {n} uid(s); quick_check {ok}")
    return 0 if ok == "ok" and n == total else 1


if __name__ == "__main__":
    sys.exit(main())
