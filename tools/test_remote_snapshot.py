#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `remote_snapshot.py` `[SPEC-DF-120]`.

The one property that matters: for the same `changes.json`, running
`apply_changes.py` against a snapshot this tool built produces the
byte-identical write statements (the ones that actually reach `--emit-sql`'s
patch, not the comparison `SELECT`s a scratch db never ships) that running it
against a full copy of the same "remote" would -- across fast-forward, noop,
conflict, missing, an id_review whose target recording must be created,
one whose target recording already exists remotely (the one case a naive
reconstruction corrupts: a plain, non-`OR IGNORE` `INSERT INTO recordings`
would collide with a row already there), a boundary edit needing the
target-span fallback, and a remote genuinely missing the fade columns.

`remote_peek.run_remote_sql` is monkeypatched to run the literal SQL this
tool builds against a real, local, disposable "remote" fixture directly --
exercising the actual SQL text against a real SQLite engine, without ssh.

    python tools/test_remote_snapshot.py
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import apply_changes  # noqa: E402
import remote_peek  # noqa: E402
import remote_snapshot  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


REMOTE_SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT,
    start_ms INTEGER, end_ms INTEGER, lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL,
    fade_in_ms INTEGER NOT NULL DEFAULT 20, fade_out_ms INTEGER NOT NULL DEFAULT 20,
    fade_in_curve TEXT NOT NULL DEFAULT 'exponential', fade_out_curve TEXT NOT NULL DEFAULT 'exponential',
    boundary_src TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL, source TEXT);
CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT, length_ms INTEGER, source TEXT);
CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT, source TEXT);
CREATE TABLE recording_artists (mbid TEXT, artist_mbid TEXT, weight REAL, source TEXT);
CREATE TABLE lowlevel_cache (audio_md5 TEXT, start_ms INTEGER, end_ms INTEGER);
"""

MD5_A = "a" * 32
MD5_B = "b" * 32
REC_EXISTING = "aaaaaaaa-0000-0000-0000-000000000001"   # what passage A is currently linked to
REC_TARGET_NEW = "aaaaaaaa-0000-0000-0000-000000000002"  # not known to the remote yet
REC_TARGET_KNOWN = "aaaaaaaa-0000-0000-0000-000000000003"  # already known to the remote, elsewhere
ARTIST_EXISTING = "bbbbbbbb-0000-0000-0000-000000000001"
ARTIST_TARGET = "bbbbbbbb-0000-0000-0000-000000000002"


def make_remote(path: str) -> None:
    c = sqlite3.connect(path)
    c.executescript(REMOTE_SCHEMA)
    c.executescript(f"""
        INSERT INTO files VALUES (1, '{MD5_A}'), (2, '{MD5_B}');
        -- passage 100: id_review's subject. Already linked to REC_EXISTING.
        INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, lead_in_ms,
            lead_out_ms, gain_db, boundary_src)
            VALUES (100, 1, 'radio', 1000, 200000, 300, 1200, -2.0, 'src');
        INSERT INTO passage_recordings VALUES (100, '{REC_EXISTING}', 1.0, 's');
        -- REC_TARGET_KNOWN already exists here, from some other passage entirely --
        -- the case a naive reconstruction would try to duplicate-INSERT.
        INSERT INTO recordings VALUES ('{REC_TARGET_KNOWN}', 'Known Elsewhere', NULL, 's');

        -- passage 200: boundary_review's subject, already moved once (so the
        -- anchor's own pre-edit span no longer resolves -- only the target span does).
        INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, lead_in_ms,
            lead_out_ms, gain_db, boundary_src)
            VALUES (200, 1, 'radio', 5000, 190000, 100, 900, -1.0, 'manual');

        -- passage 300: on file 2, whose passages table lacks fade columns --
        -- simulated by a *second* remote file below, not here.

        -- passage 400: artist_review's subject.
        INSERT INTO recordings VALUES ('{REC_EXISTING}', 'A Song', NULL, 's');
        INSERT INTO artists VALUES ('{ARTIST_EXISTING}', 'Old Artist', 's');
        INSERT INTO recording_artists VALUES ('{REC_EXISTING}', '{ARTIST_EXISTING}', 1.0, 's');

        -- passage 500: a previously-applied boundary_review, for the
        -- conflict-history path -- current diverges from baseline AND target.
        INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, lead_in_ms,
            lead_out_ms, gain_db, boundary_src)
            VALUES (500, 2, 'radio', 0, 100000, 50, 50, 0.0, 'manual');
    """)
    apply_changes.ensure_review_tables(c)
    c.execute(
        "INSERT INTO boundary_reviews (passage_id, start_ms, end_ms, lead_in_ms, lead_out_ms, "
        "gain_db, decided_at, applied_at, origin) VALUES (500, 0, 100000, 50, 50, 0.0, "
        "'2026-08-01T00:00:00', datetime('now'), 'some-other-host')")
    c.commit()
    c.close()


def make_remote_no_fade(path: str) -> None:
    """A remote whose `passages` predates `tools/add_fade_columns.py`
    entirely -- the one case `_boundary_row()`'s own retry exists for.
    """
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE);
        CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT,
            start_ms INTEGER, end_ms INTEGER, lead_in_ms INTEGER, lead_out_ms INTEGER,
            gain_db REAL, boundary_src TEXT);
    """)
    c.execute(f"INSERT INTO files VALUES (1, '{MD5_A}')")
    c.execute("INSERT INTO passages VALUES (600, 1, 'radio', 2000, 180000, 200, 800, -1.5, 'src')")
    apply_changes.ensure_review_tables(c)
    c.commit()
    c.close()


CHANGES = [
    {  # 1: id_review, fast-forward (current == baseline)
        "kind": "id_review",
        "anchor": {"audio_md5": MD5_A, "passage_kind": "radio", "start_ms": 1000, "end_ms": 200000},
        "baseline": {"mbid": REC_EXISTING},
        "target": {"mbid": REC_TARGET_NEW, "title": "Brand New Song", "artists": []},
        "decided_at": "2026-08-31T00:00:00", "origin": "desktop",
    },
    {  # 2: id_review whose target already exists remotely, elsewhere
        "kind": "id_review",
        "anchor": {"audio_md5": MD5_A, "passage_kind": "radio", "start_ms": 1000, "end_ms": 200000},
        "baseline": {"mbid": REC_EXISTING},
        "target": {"mbid": REC_TARGET_KNOWN, "title": "Known Elsewhere", "artists": []},
        "decided_at": "2026-08-31T00:01:00", "origin": "desktop",
    },
    {  # 3: id_review, missing (anchor does not resolve at all)
        "kind": "id_review",
        "anchor": {"audio_md5": "c" * 32, "passage_kind": "radio", "start_ms": 0, "end_ms": 1000},
        "baseline": {"mbid": None},
        "target": {"mbid": REC_TARGET_NEW, "title": "Nowhere", "artists": []},
        "decided_at": "2026-08-31T00:02:00", "origin": "desktop",
    },
    {  # 4: boundary_review, found only via the target-span fallback
        "kind": "boundary_review",
        "anchor": {"audio_md5": MD5_A, "passage_kind": "radio", "start_ms": 4000, "end_ms": 191000},
        "baseline": {"start_ms": 4000, "end_ms": 191000, "lead_in_ms": 150, "lead_out_ms": 950, "gain_db": -1.2},
        "target": {"start_ms": 5000, "end_ms": 190000, "lead_in_ms": 100, "lead_out_ms": 900, "gain_db": -1.0},
        "decided_at": "2026-08-31T00:03:00", "origin": "desktop",
    },
    {  # 5: artist_review, conflict (current diverges from both baseline and target)
        "kind": "artist_review",
        "anchor": {"recording_mbid": REC_EXISTING},
        "baseline": {"artist_mbid": "cccccccc-0000-0000-0000-000000000009", "artist_name": "Baseline Artist"},
        "target": {"artist_mbid": ARTIST_TARGET, "artist_name": "New Artist"},
        "decided_at": "2026-08-31T00:04:00", "origin": "desktop",
    },
    {  # 6: boundary_review, conflict against a passage with prior applied
       # history -- exercises history_for()'s "diverged independently" note,
       # which never reaches the write patch and so is not covered by the
       # identical-patch check above.
        "kind": "boundary_review",
        "anchor": {"audio_md5": MD5_B, "passage_kind": "radio", "start_ms": 0, "end_ms": 100000},
        "baseline": {"start_ms": 0, "end_ms": 100000, "lead_in_ms": 999, "lead_out_ms": 999, "gain_db": -9.0},
        "target": {"start_ms": 0, "end_ms": 100000, "lead_in_ms": 111, "lead_out_ms": 111, "gain_db": -1.1},
        "decided_at": "2026-08-31T00:06:00", "origin": "desktop",
    },
]


class _FakeRemoteSql:
    """Redirects `remote_peek.run_remote_sql`'s ssh round trip to a real,
    local sqlite connection -- the actual SQL text this tool builds runs
    against a real engine, just not over a network.
    """
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def __call__(self, remote, sql, timeout=None):
        try:
            cur = self.conn.execute(sql)
            return {"ok": True, "rows": [dict(r) for r in cur.fetchall()]}
        except sqlite3.OperationalError as e:
            return {"ok": False, "error": str(e)}

    def close(self):
        self.conn.close()


def run_apply(db_path: str, changes: list, emit_sql_path: str) -> tuple[dict, str]:
    """`apply_changes.py --commit --emit-sql ... --json`, as a library call.
    Returns the parsed `--json` summary and the full stdout text -- the
    latter is where `report_conflict()`'s own use of `history_for()` shows
    up, since it never reaches the write patch a snapshot is otherwise
    compared by.
    """
    changes_path = db_path + ".changes.json"
    with open(changes_path, "w", encoding="utf-8") as f:
        json.dump({"format_version": 1, "changes": changes}, f)
    old_argv = sys.argv
    sys.argv = ["apply_changes.py", db_path, changes_path, "--commit",
                "--emit-sql", emit_sql_path, "--json"]
    import io
    import contextlib
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            apply_changes.main()
    finally:
        sys.argv = old_argv
    text = buf.getvalue()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line), text
    raise AssertionError(f"no JSON summary line in output:\n{text}")


def patch_writes(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip() not in ("BEGIN IMMEDIATE;", "COMMIT;")]
    return lines


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="lempi-remote-snapshot-test-")
    try:
        remote_path = os.path.join(tmp, "remote.db")
        make_remote(remote_path)

        print("full-copy baseline: apply_changes.py against a literal copy of the remote")
        full_copy = os.path.join(tmp, "full-copy.db")
        shutil.copyfile(remote_path, full_copy)
        full_patch = os.path.join(tmp, "full.sql")
        full_summary, full_text = run_apply(full_copy, CHANGES, full_patch)

        print()
        print("targeted snapshot: remote_snapshot.py against the same remote, no copy")
        fake = _FakeRemoteSql(remote_path)
        old_run_remote_sql = remote_peek.run_remote_sql
        remote_peek.run_remote_sql = fake
        try:
            fetched, schema = remote_snapshot.fetch("fake@remote:/path", CHANGES)
        finally:
            remote_peek.run_remote_sql = old_run_remote_sql
            fake.close()
        snap_path = os.path.join(tmp, "snapshot.db")
        remote_snapshot.build(CHANGES, fetched, schema, snap_path)
        # A toy fixture this small hits SQLite's own minimum page allocation
        # either way, so file size proves nothing at this scale -- the real
        # ~1.16 GB vs. a handful of rows is `[SPEC-DF-114]`'s own measurement,
        # not something a unit test's fixture can meaningfully repeat. What a
        # fixture *can* prove: the snapshot holds only the passages these
        # changes actually touched (5 anchors, one resolving via the
        # target-span fallback to the same row an anchor-span lookup would
        # have used had it existed) -- not a passage the remote fixture has
        # that nothing here references.
        snap_conn = sqlite3.connect(snap_path)
        n = snap_conn.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
        snap_conn.close()
        check(n <= len(CHANGES),
              f"a snapshot must hold at most one row per change, got {n} for {len(CHANGES)} changes")
        snap_patch = os.path.join(tmp, "snap.sql")
        snap_summary, snap_text = run_apply(snap_path, CHANGES, snap_patch)

        print()
        print("the two approaches must agree on every classification")
        for key in ("fastforward", "noop", "conflict", "missing", "error"):
            check(full_summary[key] == snap_summary[key],
                  f"{key}: full-copy said {full_summary[key]}, snapshot said {snap_summary[key]}")

        print()
        print("and must emit the identical write statements -- what actually ships to the "
              "real remote, not merely 'the same counts'")
        full_writes = patch_writes(full_patch)
        snap_writes = patch_writes(snap_patch)
        check(full_writes == snap_writes,
              "patch.sql must be identical between the two approaches:\n"
              f"  full-copy: {full_writes}\n"
              f"  snapshot:  {snap_writes}")

        print()
        print("the conflict report's own applied-review history -- never part of the write "
              "patch, so not covered by the check above -- must also agree")
        check("diverged independently" in full_text,
              f"the full-copy baseline itself must see passage 500's prior applied review, "
              f"got:\n{full_text}")
        check(("diverged independently" in snap_text) == ("diverged independently" in full_text),
              f"snapshot and full-copy must agree on whether history was found:\n"
              f"  full-copy: {'found' if 'diverged independently' in full_text else 'not found'}\n"
              f"  snapshot:  {'found' if 'diverged independently' in snap_text else 'not found'}")
        check("some-other-host" in full_text,
              f"the full-copy baseline must show the history's own origin, got:\n{full_text}")
        # The snapshot's own synthetic history row cannot carry `origin` --
        # that column is `ALTER`-added, and pre-adding it here would make
        # `apply_changes.py`'s own later `ensure_review_tables()` call treat
        # it as already there, silently dropping the ALTER a genuinely
        # virgin remote needs captured and shipped (see `build()`'s own
        # comment). A blank origin in the conflict report -- checked for
        # above via "diverged independently" appearing in both -- is the
        # accepted cost of that correctness: `history_for()` reads a bare
        # `NULL` as "here", not a crash and not a wrong host name.

        print()
        print("the target-already-known-elsewhere case did not try to duplicate-INSERT it "
              "(would show up as a refused/errored change, not a clean fast-forward)")
        check(full_summary["error"] == 0, f"the full-copy baseline itself must have no errors, got {full_summary}")

        print()
        print("an unmigrated remote (no fade columns at all) is handled by the retry, "
              "not a hard failure")
        no_fade_remote = os.path.join(tmp, "no-fade-remote.db")
        make_remote_no_fade(no_fade_remote)
        fade_change = [{
            "kind": "boundary_review",
            "anchor": {"audio_md5": MD5_A, "passage_kind": "radio", "start_ms": 2000, "end_ms": 180000},
            "baseline": {"start_ms": 2000, "end_ms": 180000, "lead_in_ms": 200, "lead_out_ms": 800, "gain_db": -1.5},
            "target": {"start_ms": 2100, "end_ms": 179000, "lead_in_ms": 150, "lead_out_ms": 750, "gain_db": -1.0},
            "decided_at": "2026-08-31T00:05:00", "origin": "desktop",
        }]
        fake2 = _FakeRemoteSql(no_fade_remote)
        remote_peek.run_remote_sql = fake2
        try:
            fetched2, schema2 = remote_snapshot.fetch("fake@remote:/path", fade_change)
        finally:
            remote_peek.run_remote_sql = old_run_remote_sql
            fake2.close()
        check(fetched2[0]["has_fade"] is False,
              f"an unmigrated remote must be detected as such, got {fetched2[0]}")
        check(fetched2[0]["row"].get("passage_id") == 600, f"got {fetched2[0]['row']}")
        snap2_path = os.path.join(tmp, "snap2.db")
        remote_snapshot.build(fade_change, fetched2, schema2, snap2_path)
        snap2_summary, _ = run_apply(snap2_path, fade_change, os.path.join(tmp, "snap2.sql"))
        check(snap2_summary["fastforward"] == 1, f"got {snap2_summary}")
        check(snap2_summary["error"] == 0, f"got {snap2_summary}")

        print()
        print("a remote whose boundary_reviews TABLE exists but predates "
              "[SPEC-DF-104]/[SPEC-SUI-226] (bare columns only): the snapshot must "
              "reproduce exactly that, not silently claim it is fully migrated, "
              "or claim it is missing entirely -- the bug this whole fix closes")
        partial_remote = os.path.join(tmp, "partial-remote.db")
        c = sqlite3.connect(partial_remote)
        c.executescript("""
            CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE);
            CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT,
                start_ms INTEGER, end_ms INTEGER, lead_in_ms INTEGER, lead_out_ms INTEGER,
                gain_db REAL, fade_in_ms INTEGER NOT NULL DEFAULT 20,
                fade_out_ms INTEGER NOT NULL DEFAULT 20,
                fade_in_curve TEXT NOT NULL DEFAULT 'exponential',
                fade_out_curve TEXT NOT NULL DEFAULT 'exponential', boundary_src TEXT);
        """)
        c.execute(apply_changes.BOUNDARY_REVIEWS_TABLE)  # bare -- no origin/orig_*/fade_* yet
        c.execute(f"INSERT INTO files VALUES (1, '{MD5_A}')")
        c.execute("INSERT INTO passages VALUES (700, 1, 'radio', 3000, 170000, 50, 50, 0.0, "
                  "20, 20, 'exponential', 'exponential', 'src')")
        # A genuinely pre-origin applied row -- this column does not exist yet.
        c.execute("INSERT INTO boundary_reviews (passage_id, start_ms, end_ms, lead_in_ms, "
                  "lead_out_ms, gain_db, decided_at, applied_at) VALUES "
                  "(700, 3000, 170000, 50, 50, 0.0, '2026-07-01T00:00:00', datetime('now'))")
        c.commit()
        c.close()

        partial_change = [{
            "kind": "boundary_review",
            "anchor": {"audio_md5": MD5_A, "passage_kind": "radio", "start_ms": 3000, "end_ms": 170000},
            "baseline": {"start_ms": 3000, "end_ms": 170000, "lead_in_ms": 50, "lead_out_ms": 50, "gain_db": 0.0},
            "target": {"start_ms": 3100, "end_ms": 169000, "lead_in_ms": 40, "lead_out_ms": 60, "gain_db": -0.5},
            "decided_at": "2026-08-31T00:06:00", "origin": "desktop",
        }]
        fake3 = _FakeRemoteSql(partial_remote)
        remote_peek.run_remote_sql = fake3
        try:
            fetched3, schema3 = remote_snapshot.fetch("fake@remote:/path", partial_change)
        finally:
            remote_peek.run_remote_sql = old_run_remote_sql
            fake3.close()
        check(schema3["boundary_reviews"] is not None and "origin" not in schema3["boundary_reviews"],
              f"the real remote's own schema must read as bare (no origin yet), got {schema3}")
        snap3_path = os.path.join(tmp, "snap3.db")
        remote_snapshot.build(partial_change, fetched3, schema3, snap3_path)
        snap3_summary, snap3_text = run_apply(snap3_path, partial_change, os.path.join(tmp, "snap3.sql"))
        check(snap3_summary["fastforward"] == 1, f"got {snap3_summary}")
        check(snap3_summary["error"] == 0, f"got {snap3_summary}")
        check("here" in snap3_text.lower() or "no correction history" not in snap3_text,
              f"a bare-schema remote must not crash the conflict-report path, got:\n{snap3_text}")
        patch3 = patch_writes(os.path.join(tmp, "snap3.sql"))
        check(any("ADD COLUMN origin" in s and "boundary_reviews" in s for s in patch3),
              f"a genuinely pre-origin remote's own patch must still add that column, got:\n{patch3}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("remote_snapshot: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
