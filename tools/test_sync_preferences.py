#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `sync_preferences.py`'s decision logic `[SPEC030]`.

`decide()` and `patch_sql_for()` are pure -- no database, no ssh call --
which is exactly what's checked here, the same discipline
`ingest_cd.py`/`segment_dao.py`'s own tests already use. `read_local_manifest`/
`apply_local` get one small in-memory-db round trip each, since that part
is genuinely I/O.

    python tools/test_sync_preferences.py
"""

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sync_preferences as sp  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


def always(_kind, _id):
    return True


def never(_kind, _id):
    return False


def main() -> int:
    print("decide: remote newer -> pull")
    local = {("recording", "r1"): (1.0, 2.0, 0.0, "2026-09-01T00:00:00")}
    remote = {("recording", "r1"): (1.5, 2.0, 0.0, "2026-09-02T00:00:00")}
    plan = sp.decide(local, remote, always, always)
    check(plan == {"pull": [("recording", "r1")], "push": [], "skip_missing": [], "tie": []},
          f"got {plan}")

    print()
    print("decide: local newer -> push")
    local = {("artist", "a1"): (1.0, 2.0, 0.0, "2026-09-03T00:00:00")}
    remote = {("artist", "a1"): (0.5, 2.0, 0.0, "2026-09-01T00:00:00")}
    plan = sp.decide(local, remote, always, always)
    check(plan["push"] == [("artist", "a1")], f"got {plan}")

    print()
    print("decide: identical rows on both sides -> neither list")
    row = (1.0, 2.0, 0.0, "2026-09-01T00:00:00")
    plan = sp.decide({("recording", "r1"): row}, {("recording", "r1"): row}, always, always)
    check(plan == {"pull": [], "push": [], "skip_missing": [], "tie": []}, f"got {plan}")

    print()
    print("decide: equal timestamp, differing values -> tie, not guessed at")
    local = {("recording", "r1"): (1.0, 2.0, 0.0, "2026-09-01T00:00:00")}
    remote = {("recording", "r1"): (1.5, 2.0, 0.0, "2026-09-01T00:00:00")}
    plan = sp.decide(local, remote, always, always)
    check(plan["tie"] == [("recording", "r1")], f"got {plan}")
    check(not plan["pull"] and not plan["push"], "a tie must not also be pulled or pushed")

    print()
    print("decide: tuned locally only, remote HAS the artist/recording -> push")
    local = {("artist", "a1"): (1.0, None, None, "2026-09-01T00:00:00")}
    plan = sp.decide(local, {}, always, always)
    check(plan["push"] == [("artist", "a1")], f"got {plan}")

    print()
    print("decide: tuned locally only, remote does NOT have that artist -> skip_missing")
    local = {("artist", "a1"): (1.0, None, None, "2026-09-01T00:00:00")}
    plan = sp.decide(local, {}, always, never)
    check(plan["skip_missing"] == [("artist", "a1")], f"got {plan}")
    check(not plan["push"], "must not push onto a library that lacks the artist")

    print()
    print("decide: tuned remotely only, local does NOT have that recording -> skip_missing")
    remote = {("recording", "r9"): (None, 2.0, -0.5, "2026-09-01T00:00:00")}
    plan = sp.decide({}, remote, never, always)
    check(plan["skip_missing"] == [("recording", "r9")], f"got {plan}")
    check(not plan["pull"], "must not pull onto a library that lacks the recording")

    print()
    print("decide: NULL fields are preserved through the comparison, not treated as 0")
    local = {("recording", "r1"): (None, None, None, "2026-09-01T00:00:00")}
    remote = {("recording", "r1"): (None, None, None, "2026-09-01T00:00:00")}
    plan = sp.decide(local, remote, always, always)
    check(plan == {"pull": [], "push": [], "skip_missing": [], "tie": []},
          f"two NULL rows with the same timestamp must read as identical, got {plan}")

    print()
    print("patch_sql_for: one INSERT OR REPLACE per row, literal-quoted, NULLs kept as NULL")
    source = {("recording", "r1"): (1.5, None, -0.939, "2026-09-01T00:00:00")}
    sql = sp.combined_patch(sp.patch_sql_for([("recording", "r1")], source))
    check("INSERT OR REPLACE INTO listener_preferences" in sql, f"got {sql!r}")
    check("'recording'" in sql and "'r1'" in sql, f"got {sql!r}")
    check("1.5" in sql, f"got {sql!r}")
    check("NULL" in sql, f"a None field must render as literal NULL, got {sql!r}")
    check("-0.939" in sql, f"got {sql!r}")
    check(sql.strip().startswith("BEGIN IMMEDIATE;") and sql.strip().endswith("COMMIT;"),
          f"got {sql!r}")

    # A value containing a quote must not break the statement -- the same
    # escaping discipline `remote_peek.literal` already tests for elsewhere.
    tricky = {("artist", "o'brien"): (1.0, None, None, "t")}
    sql2 = sp.combined_patch(sp.patch_sql_for([("artist", "o'brien")], tricky))
    check("o''brien" in sql2, f"a quote in an id must be doubled, got {sql2!r}")

    print()
    print("combined_patch: one transaction for every table that moved, empty for none")
    check(sp.combined_patch([], [], []) == "",
          "nothing to push must render as no patch at all, so no service restart happens")
    both = sp.combined_patch(
        sp.patch_sql_for([("recording", "r1")], source),
        sp.specials_patch_sql_for(
            [("recording", "r1", "user.spiritual", "spiritual")],
            {("recording", "r1", "user.spiritual", "spiritual"): (0.75, "2026-09-10T00:00:00")}))
    check(both.count("BEGIN IMMEDIATE;") == 1 and both.count("COMMIT;") == 1,
          f"two tables must still be one transaction, got {both!r}")
    check("listener_preferences" in both and "listener_characteristics" in both,
          f"both tables must be in it, got {both!r}")
    check(both.index("CREATE TABLE IF NOT EXISTS listener_characteristics")
          < both.index("INSERT OR REPLACE INTO listener_characteristics"),
          "the table must be created before anything is inserted into it")

    print()
    print("decide, over the specials' own key and row shape")
    skey = ("recording", "r1", "user.spiritual", "spiritual")
    when = lambda row: row[1]            # noqa: E731
    subject_of = lambda key: (key[0], key[1])   # noqa: E731
    splan = sp.decide({skey: (1.0, "2026-09-02T00:00:00")},
                      {skey: (0.0, "2026-09-01T00:00:00")},
                      always, always, when=when, subject_of=subject_of)
    check(splan["push"] == [skey] and not splan["pull"],
          f"the newer side must win on a special exactly as on a tuning, got {splan}")
    splan = sp.decide({skey: (1.0, "2026-09-01T00:00:00")},
                      {skey: (0.0, "2026-09-01T00:00:00")},
                      always, always, when=when, subject_of=subject_of)
    check(splan["tie"] == [skey], f"equal timestamps must tie, not guess, got {splan}")
    # The existence check has to be handed a *recording*, not the four-field key.
    splan = sp.decide({skey: (1.0, "t")}, {}, always, never,
                      when=when, subject_of=subject_of)
    check(splan["skip_missing"] == [skey],
          f"a tag must not be pushed onto a library with no such recording, got {splan}")

    print()
    print("decide_definitions: additive, and a disagreement is reported not resolved")
    curve_a = ("step", "Spiritual", ((1, 1, 1.0),))
    curve_b = ("step", "Spiritual", ((1, 1, 0.5),))
    dkey = ("user.spiritual", "spiritual")
    dplan = sp.decide_definitions({dkey: curve_a}, {})
    check(dplan["push"] == [dkey], f"a definition the far side lacks must travel, got {dplan}")
    dplan = sp.decide_definitions({}, {dkey: curve_a})
    check(dplan["pull"] == [dkey], f"and in the other direction, got {dplan}")
    dplan = sp.decide_definitions({dkey: curve_a}, {dkey: curve_a})
    check(dplan == {"pull": [], "push": [], "conflict": []},
          f"identical definitions are nothing to do, got {dplan}")
    dplan = sp.decide_definitions({dkey: curve_a}, {dkey: curve_b})
    check(dplan["conflict"] == [dkey] and not dplan["pull"] and not dplan["push"],
          f"a curve tuned differently on each side must be left alone, got {dplan}")

    print()
    print("definitions_patch_sql_for: label omitted where the remote cannot hold one")
    with_label = " ".join(sp.definitions_patch_sql_for([dkey], {dkey: curve_a}, True))
    check("'Spiritual'" in with_label and "label" in with_label, f"got {with_label!r}")
    without = " ".join(sp.definitions_patch_sql_for([dkey], {dkey: curve_a}, False))
    check("label" not in without,
          f"naming a column the remote lacks would fail the statement, got {without!r}")
    check("DELETE FROM listener_occasion_points" in without and
          "INSERT INTO listener_occasion_points" in without,
          f"the curve's own points must travel with it, got {without!r}")

    print()
    print("absent_table: an absent table is empty, every other failure is unknown")
    check(sp.absent_table({"ok": False, "error": "Error: in prepare, no such table: listener_characteristics"}),
          "a missing table must read as absent")
    check(not sp.absent_table({"ok": False, "error": "Error: database is locked"}),
          "a locked database must NOT read as an empty table -- that pushes stale values")
    check(not sp.absent_table({"ok": False, "error": "no answer from lempipi within 30s"}),
          "a timeout must not read as an empty table either")
    check(not sp.absent_table({"ok": False}),
          "a failure with no error text is unknown, not empty")

    print()
    print("read_local_manifest / apply_local: a real round trip against an in-memory db")
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE listener_preferences (subject_kind TEXT, subject_id TEXT, "
        "rotation REAL, recovery REAL, restraint REAL, updated_at TEXT, "
        "PRIMARY KEY (subject_kind, subject_id))")
    conn.execute(
        "INSERT INTO listener_preferences VALUES ('recording','r1',1.0,2.0,0.0,'2026-09-01T00:00:00')")
    manifest = sp.read_local_manifest(conn)
    check(manifest == {("recording", "r1"): (1.0, 2.0, 0.0, "2026-09-01T00:00:00")},
          f"got {manifest}")

    # apply_local: an INSERT for a subject not yet present, an UPDATE for one that is.
    incoming = {
        ("recording", "r1"): (1.5, 2.0, 0.0, "2026-09-02T00:00:00"),
        ("artist", "a1"): (None, 1.0, -0.5, "2026-09-02T00:00:00"),
    }
    sp.apply_local(conn, [("recording", "r1"), ("artist", "a1")], incoming)
    after = sp.read_local_manifest(conn)
    check(after[("recording", "r1")] == (1.5, 2.0, 0.0, "2026-09-02T00:00:00"),
          f"existing row must be overwritten, got {after}")
    check(after[("artist", "a1")] == (None, 1.0, -0.5, "2026-09-02T00:00:00"),
          f"a new subject must be inserted, got {after}")

    print()
    print("read_local_specials: a database predating the table reads empty, not an error")
    check(sp.read_local_specials(conn) == {},
          "an installation not yet running this build has tagged nothing, which is empty")
    sp.apply_local_specials(conn, [skey], {skey: (0.75, "2026-09-10T00:00:00")})
    check(sp.read_local_specials(conn) == {skey: (0.75, "2026-09-10T00:00:00")},
          f"apply_local_specials must create the table and land the row, "
          f"got {sp.read_local_specials(conn)}")
    sp.apply_local_specials(conn, [skey], {skey: (0.25, "2026-09-11T00:00:00")})
    check(sp.read_local_specials(conn) == {skey: (0.25, "2026-09-11T00:00:00")},
          "a second apply must overwrite rather than collide on the primary key")

    print()
    print("read_local_definitions / apply_local_definitions: a curve and its points")
    conn.execute("CREATE TABLE listener_occasions (characteristic TEXT, class TEXT, "
                 "interp TEXT, label TEXT, PRIMARY KEY (characteristic, class))")
    conn.execute("CREATE TABLE listener_occasion_points (characteristic TEXT, class TEXT, "
                 "month INTEGER, day INTEGER, multiplier REAL, "
                 "PRIMARY KEY (characteristic, class, month, day))")
    defs, has_label = sp.read_local_definitions(conn)
    check(defs == {} and has_label, f"an empty registry reads empty, got {defs}")
    sp.apply_local_definitions(conn, [dkey], {dkey: curve_a}, True)
    defs, _ = sp.read_local_definitions(conn)
    check(defs == {dkey: curve_a}, f"the curve must round-trip with its points, got {defs}")
    # Re-applying must not accumulate duplicate control points.
    sp.apply_local_definitions(conn, [dkey], {dkey: curve_b}, True)
    defs, _ = sp.read_local_definitions(conn)
    check(defs == {dkey: curve_b},
          f"re-applying replaces the curve rather than appending to it, got {defs}")
    conn.close()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("sync_preferences: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
