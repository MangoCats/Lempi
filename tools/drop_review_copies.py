#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Drop the review copies and the orphan reviews from the hub [IMPL-UID-044].

What is dropped, from evidence rather than by number:

- **A copy** is an `id_reviews` row with no `origin` whose `decided_at` is
  shared with exactly one row that has one -- its original, made once on the
  hub -- and that holds the original's decision, chosen recording, chosen
  release and previous recording. They are what remains of the copies the
  star merge made before 2026-10-18 [SPEC-STAR-051]: measured 2026-11-14, 27
  on the hub and on every node, 9 of them pending, which `apply_reviews.py
  --commit` would have applied to nine unrelated songs. A row that shares a
  time but differs from its original in any of those is not plainly a copy:
  it is named and the run is refused, since a person must look at it.
- **An orphan** is an `id_reviews` row naming a passage the catalogue does not
  hold. The maintainer's decision of 2026-11-14 drops them [IMPL-UID-902].

Only the hub is written. The next star-sync round removes the same rows from
every node, now that the review tables take removal evidence [IMPL-UID-042];
each node's rehearsal shows them before anything there is written.

Rehearses by default and writes nothing. `--commit` needs the counts the
rehearsal printed, `--expect-copies N --expect-orphans M`, and refuses if what
it finds then differs. It backs the listener up first through SQLite's backup
API -- never a copy of the files -- into `data/backups/pre-review-copies-<UTC>/`,
writes every dropped row to `dropped.json` beside it (each can be put back
exactly; `--restore DIR` does that), and deletes in one transaction.

    python tools/drop_review_copies.py data/listener.db
    python tools/drop_review_copies.py data/listener.db --commit --expect-copies 27 --expect-orphans 4
    python tools/drop_review_copies.py data/listener.db --restore data/backups/pre-review-copies-<UTC>
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_db  # noqa: E402
import row_drop  # noqa: E402  -- the backup, the record of what went, and putting it back
import staged  # noqa: E402  -- utf8_output: a title is not always cp1252

COLS =["passage_id", "decision", "chosen_mbid", "decided_at", "chosen_release_mbid",
        "previous_mbid", "applied_at", "origin"]
SAME = ("decision", "chosen_mbid", "chosen_release_mbid", "previous_mbid")


def say(*a):
    print(*a, flush=True)


def present(conn) -> list[str]:
    have = {r[1] for r in conn.execute("PRAGMA main.table_info(id_reviews)")}
    missing = [c for c in COLS if c not in have]
    if missing:
        raise SystemExit(f"id_reviews lacks {missing}: not the table this was written for")
    return COLS


def find(conn) -> tuple[list[dict], list[dict], list[str]]:
    """(copies, orphans, refusals): what would be dropped, and why it cannot be."""
    cols = present(conn)
    rows = [dict(zip(cols, r)) for r in conn.execute(f"SELECT {', '.join(cols)} FROM main.id_reviews")]
    by_time: dict = {}
    for r in rows:
        by_time.setdefault(r["decided_at"], []).append(r)
    copies, refusals = [], []
    for when, group in sorted(by_time.items()):
        originals = [r for r in group if r["origin"]]
        unmarked = [r for r in group if not r["origin"]]
        if len(group) < 2 or not unmarked or not originals:
            continue
        if len(originals) != 1:
            refusals.append(f"{when}: {len(originals)} originals and {len(unmarked)} unmarked row(s) -- "
                            "which is the copy is not plain")
            continue
        (orig,) = originals
        for r in unmarked:
            differs = [c for c in SAME if r[c] != orig[c]]
            if differs:
                refusals.append(f"{when}: passage {r['passage_id']} differs from its original "
                                f"(passage {orig['passage_id']}) in {differs}")
            else:
                copies.append(dict(r, original=orig["passage_id"]))
    held = {r[0] for r in conn.execute("SELECT passage_id FROM lib.passages")}
    orphans = [r for r in rows if r["passage_id"] not in held]
    copied = {(c["passage_id"], c["decided_at"]) for c in copies}
    orphans = [r for r in orphans if (r["passage_id"], r["decided_at"]) not in copied]
    return copies, orphans, refusals


def title(conn, mbid):
    if not mbid:
        return "-"
    r = conn.execute("SELECT title FROM lib.recordings WHERE mbid = ?", (mbid,)).fetchone()
    return r[0] if r and r[0] else mbid[:8]


def show(conn, copies, orphans, refusals):
    say(f"copies: {len(copies)}")
    for c in copies:
        now = conn.execute("SELECT mbid FROM lib.passage_recordings WHERE passage_id = ? "
                           "ORDER BY weight DESC, mbid LIMIT 1", (c["passage_id"],)).fetchone()
        say(f"  passage {c['passage_id']:>6}  {c['decided_at']}  {c['decision']:<10} "
            f"{'applied' if c['applied_at'] else 'PENDING'}  copy of {c['original']}; it is "
            f"\"{title(conn, now[0] if now else None)}\", the copy says \"{title(conn, c['chosen_mbid'])}\"")
    say(f"orphans: {len(orphans)}")
    for o in orphans:
        say(f"  passage {o['passage_id']:>6}  {o['decided_at']}  {o['decision']:<10} "
            f"origin {o['origin']}: no such passage in the catalogue")
    for x in refusals:
        say(f"REFUSED  {x}")


def open_pair(listener, library, writable):
    conn = lempi_db.connect(listener, lempi_db.ROLE_LISTENER, writable=writable, peer=library, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    if not lempi_db.has_table(conn, "id_reviews"):
        raise SystemExit(f"{listener} holds no id_reviews")
    if "lib" not in {r[1] for r in conn.execute("PRAGMA database_list")}:
        raise SystemExit("the catalogue half is not attached: an orphan cannot be told without it")
    return conn


def commit(listener, library, expect_copies, expect_orphans, backups) -> int:
    conn = open_pair(listener, library, writable=False)
    copies, orphans, refusals = find(conn)
    show(conn, copies, orphans, refusals)
    conn.close()
    if refusals:
        raise SystemExit("refused: a row above is not plainly a copy; nothing was written")
    if (len(copies), len(orphans)) != (expect_copies, expect_orphans):
        raise SystemExit(f"refused: found {len(copies)} copies and {len(orphans)} orphans, expected "
                         f"{expect_copies} and {expect_orphans}; nothing was written")
    if not copies and not orphans:
        say("nothing to drop")
        return 0
    gone = [dict((c, r[c]) for c in COLS) for r in copies + orphans]
    folder = row_drop.keep(listener, backups, "pre-review-copies", "id_reviews", gone)
    say(f"backed up to {folder}, with every row to be dropped in dropped.json")
    conn = open_pair(listener, library, writable=True)
    try:
        conn.execute("BEGIN IMMEDIATE")
        again = find(conn)
        if [(c["passage_id"], c["decided_at"]) for c in again[0]] != [(c["passage_id"], c["decided_at"]) for c in copies] \
                or [o["passage_id"] for o in again[1]] != [o["passage_id"] for o in orphans] or again[2]:
            raise SystemExit("refused: the reviews changed while backing up; nothing was written")
        n = 0
        for r in gone:
            n += conn.execute("DELETE FROM main.id_reviews WHERE passage_id = ? AND decided_at = ?",
                              (r["passage_id"], r["decided_at"])).rowcount
        if n != len(gone):
            raise SystemExit(f"refused: {n} row(s) would go, not {len(gone)}; nothing was written")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    left = find(conn)
    ok = conn.execute("PRAGMA main.quick_check").fetchone()[0]
    conn.close()
    say(f"dropped {n} row(s); now {len(left[0])} copies and {len(left[1])} orphans; quick_check {ok}")
    return 0 if ok == "ok" and not left[0] and not left[1] else 1


def restore(listener, library, folder) -> int:
    """Put back exactly the rows a commit dropped, where nothing has taken their place."""
    conn = open_pair(listener, library, writable=True)
    try:
        n = row_drop.put_back(conn, folder, "id_reviews", ("passage_id",))
    finally:
        conn.close()
    say(f"restored {n} row(s) from {folder}")
    return 0


def main(argv=None) -> int:
    # Found on the real catalogue, 2026-11-14: a title holding U+2010 raised
    # UnicodeEncodeError on a Windows console halfway through the list.
    staged.utf8_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("listener", help="the listener half, e.g. data/listener.db")
    ap.add_argument("--library", help="the catalogue half, if not beside the listener")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--expect-copies", type=int)
    ap.add_argument("--expect-orphans", type=int)
    ap.add_argument("--backups", default=os.path.join(os.path.dirname(HERE), "data", "backups"))
    ap.add_argument("--restore", metavar="DIR")
    a = ap.parse_args(argv)
    if a.restore:
        return restore(a.listener, a.library, a.restore)
    if a.commit:
        if a.expect_copies is None or a.expect_orphans is None:
            raise SystemExit("--commit needs --expect-copies and --expect-orphans: the counts a rehearsal printed")
        return commit(a.listener, a.library, a.expect_copies, a.expect_orphans, a.backups)
    conn = open_pair(a.listener, a.library, writable=False)
    copies, orphans, refusals = find(conn)
    show(conn, copies, orphans, refusals)
    conn.close()
    say("\nnothing was written. To drop them: --commit "
        f"--expect-copies {len(copies)} --expect-orphans {len(orphans)}" if not refusals else
        "\nnothing was written, and a commit would be refused until the rows above are looked at")
    return 1 if refusals else 0


if __name__ == "__main__":
    sys.exit(main())
