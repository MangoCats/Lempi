#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Give each saved row of words its passage's uid, once, on the hub [IMPL-UID-063].

A row of `passage_lyrics` names a save -- (audio_md5, kind, start_ms) -- never a
passage, and until Stage B a passage's words were found by segment alone, so a
boundary edit could move them. This sets each row's `passage_uid` where its
segment is exactly one current passage's, by the rule the player reads with
[SPEC-LYR-150]: the same file (a retired `audio_md5` followed [SPEC-RLK-155]),
the same kind, and the same segment [SPEC-LYR-110]. The merge then carries the
column to every node's copy; no node sets its own, since none holds every
passage.

Nothing else about a row changes, and no row is added or removed. A row whose
segment is no current passage's, or more than one's, keeps no uid, is listed by
name, and goes on speaking for its recording as before [SPEC-LYR-160].

Checked before it writes, and again after, from the file:
  - the same number of rows;
  - the same digest over every column but `passage_uid`;
  - for every passage, the same row as its words -- read by segment before,
    by uid and segment after -- so no passage shows other words, or none.

    python tools/set_lyrics_uids.py data/listener.db                    rehearse
    python tools/set_lyrics_uids.py data/listener.db --commit --expect N
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_db  # noqa: E402
import staged  # noqa: E402  -- utf8_output

COLS = ("audio_md5", "kind", "start_ms", "end_ms", "mbid", "text", "no_words", "updated_at", "origin")


def say(*a):
    print(*a, flush=True)


def same_segment(a, b) -> bool:
    """`[SPEC-LYR-110]`, as `lyrics::same_segment`: the stretch two share is at
    least half of the longer."""
    shared = min(a[1], b[1]) - max(a[0], b[0])
    longer = max(a[1] - a[0], b[1] - b[0])
    return shared > 0 and shared * 2 >= longer


def has_column(conn, table, col) -> bool:
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def aliases(conn) -> dict:
    if not lempi_db.has_table(conn, "audio_md5_aliases"):
        return {}
    return dict(conn.execute("SELECT old_md5, new_md5 FROM audio_md5_aliases"))


def read(conn) -> tuple[list, dict]:
    """The rows, each as a dict with its file followed through the aliases; and
    the passages by (file, kind)."""
    alias = aliases(conn)
    uid_col = "passage_uid" if has_column(conn, "passage_lyrics", "passage_uid") else "NULL"
    rows = [dict(zip(COLS + ("passage_uid",), r)) for r in conn.execute(
        f"SELECT {', '.join(COLS)}, {uid_col} FROM passage_lyrics")]
    for r in rows:
        r["file"] = alias.get(r["audio_md5"], r["audio_md5"])
    by = {}
    for md5, kind, s, e, uid in conn.execute(
            "SELECT f.audio_md5, p.kind, p.start_ms, p.end_ms, p.passage_uid "
            "FROM passages p JOIN files f ON f.file_id = p.file_id"):
        by.setdefault((md5, kind), []).append((s, e, uid))
    return rows, by


def plan(rows, by) -> tuple[dict, list]:
    """{row key: uid} for each row without one that is exactly one passage's; and
    the rows left, each with why."""
    out, left = {}, []
    for r in rows:
        if r["passage_uid"]:
            continue
        cands = [uid for s, e, uid in by.get((r["file"], r["kind"]), [])
                 if uid and same_segment((r["start_ms"], r["end_ms"]), (s, e))]
        k = (r["audio_md5"], r["kind"], r["start_ms"])
        if len(cands) == 1:
            out[k] = cands[0]
        else:
            left.append((k, "no current passage is its segment" if not cands
                         else f"{len(cands)} passages are its segment"))
    return out, left


def shown(rows, by, by_uid: bool) -> dict:
    """{passage uid: the key of the row that is its own entry} [SPEC-LYR-150]: the
    newest row of its file and kind that is its segment -- or, `by_uid`, that
    names its uid, or names none and is its segment."""
    out = {}
    order = sorted(rows, key=lambda r: (r["updated_at"], r["audio_md5"], r["kind"], r["start_ms"]), reverse=True)
    for (md5, kind), ps in by.items():
        for s, e, uid in ps:
            if not uid:
                continue
            for r in order:
                if r["kind"] != kind:
                    continue
                seg = r["file"] == md5 and same_segment((r["start_ms"], r["end_ms"]), (s, e))
                mine = (r["passage_uid"] == uid) if (by_uid and r["passage_uid"]) else seg
                if mine:
                    out[uid] = (r["audio_md5"], r["kind"], r["start_ms"])
                    break
    return out


def digest(rows) -> str:
    h = hashlib.sha256()
    for r in sorted(rows, key=lambda r: (r["audio_md5"], r["kind"], r["start_ms"])):
        h.update(repr(tuple(r[c] for c in COLS)).encode("utf-8"))
    return h.hexdigest()


def main(argv=None) -> int:
    staged.utf8_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("listener", help="the hub's listener, e.g. data/listener.db")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--expect", type=int)
    ap.add_argument("--backups", default=os.path.join(os.path.dirname(HERE), "data", "backups"))
    a = ap.parse_args(argv)
    conn = lempi_db.connect(a.listener, lempi_db.ROLE_LISTENER, writable=a.commit, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    if not lempi_db.has_table(conn, "passage_lyrics"):
        say("no passage_lyrics here: nothing to do")
        return 0
    if not has_column(conn, "passages", "passage_uid"):
        raise SystemExit("refused: the catalogue's passages carry no uids (IMPL023 Stage A); nothing written")
    rows, by = read(conn)
    todo, left = plan(rows, by)
    before = shown(rows, by, by_uid=False)
    say(f"{len(rows)} row(s) of words; {sum(1 for r in rows if r['passage_uid'])} already name their passage; "
        f"{len(todo)} to be given one; {len(left)} left without")
    for k, why in left:
        say(f"  left: {k[0]} {k[1]} at {k[2]} ms -- {why}")
    # What each passage would show, read the new way over the planned rows.
    after_rows = [dict(r, passage_uid=todo.get((r["audio_md5"], r["kind"], r["start_ms"]), r["passage_uid"]))
                  for r in rows]
    after = shown(after_rows, by, by_uid=True)
    if after != before:
        moved = sorted(set(before.items()) ^ set(after.items()))
        raise SystemExit(f"refused: {len(moved)} passage(s) would show other words, or none: {moved[:6]}; nothing written")
    say(f"every passage shows the same row as before: {len(before)} passage(s) with words")
    if not a.commit:
        conn.close()
        say(f"\nnothing was written. To set them: --commit --expect {len(todo)}")
        return 0
    if a.expect != len(todo):
        raise SystemExit(f"refused: {len(todo)} row(s) to set, expected {a.expect}; nothing written")
    conn.close()
    import star_sync  # noqa: E402  -- its backup: the API, integrity-checked [SPEC-STAR-106]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = os.path.join(a.backups, f"pre-lyrics-uids-{stamp}")
    star_sync.local_backup(a.listener, os.path.join(folder, os.path.basename(a.listener)))
    say(f"backed up the listener to {folder}")
    conn = lempi_db.connect(a.listener, lempi_db.ROLE_LISTENER, writable=True, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    try:
        conn.execute("BEGIN IMMEDIATE")
        now_rows, now_by = read(conn)
        if digest(now_rows) != digest(rows) or plan(now_rows, now_by)[0] != todo:
            raise SystemExit("refused: the words changed while backing up; nothing written")
        if not has_column(conn, "passage_lyrics", "passage_uid"):
            conn.execute("ALTER TABLE main.passage_lyrics ADD COLUMN passage_uid TEXT")
        conn.executemany("UPDATE main.passage_lyrics SET passage_uid = ? WHERE audio_md5 = ? AND kind = ? AND start_ms = ?",
                         [(uid, *k) for k, uid in todo.items()])
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    done, done_by = read(conn)
    ok = conn.execute("PRAGMA main.quick_check").fetchone()[0]
    conn.close()
    checks = [(len(done) == len(rows), f"rows {len(rows)} -> {len(done)}"),
              (digest(done) == digest(rows), "every column but passage_uid as it was"),
              (shown(done, done_by, by_uid=True) == before, "every passage shows the same row"),
              (sum(1 for r in done if r["passage_uid"]) == sum(1 for r in rows if r["passage_uid"]) + len(todo),
               f"{len(todo)} row(s) given their uid"),
              (ok == "ok", f"quick_check {ok}")]
    for good, what in checks:
        say(f"  {'ok  ' if good else 'FAIL'} {what}")
    return 0 if all(g for g, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
