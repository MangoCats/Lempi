#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end: a boundary edit's real journey from draft to a landed value on
a separate "remote" database `[SPEC021 §5]`, `[SPEC-DF-120]`.

Every other test in this directory proves one stage of the push pipeline in
isolation, given a well-formed input at that stage's own boundary --
`test_remote_snapshot.py` given a `changes.json`, `test_jobs_remote_push.py`
given faked stage outputs. None of them start from a raw, unapplied
`boundary_reviews` draft -- the actual shape Lempi's own editor leaves
behind -- and walk it through every real module a real edit passes through.
That gap is exactly what let a saved-but-unapplied draft look identical to a
pushed edit from Vipunen's own profile page `[REQ-VIS-275]`: nothing exercised
the boundary between "saved" and "exportable" at all.

This does, using the real functions from `apply_boundary_reviews.py`,
`export_changes.py`, `remote_snapshot.py`, and `apply_changes.py` -- no
subprocesses (each is called as the library it already is), but no
shortcuts either: the same SQL, the same schema-ensure calls, the same
merge logic a real push runs.

    python tools/test_e2e_push_pipeline.py
"""

import io
import json
import os
import sqlite3
import sys
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import apply_boundary_reviews  # noqa: E402
import apply_changes  # noqa: E402
import export_changes  # noqa: E402
import remote_peek  # noqa: E402
import remote_snapshot  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")
    return cond


AUDIO_MD5 = "e2e00000000000000000000000000000"
DESKTOP_SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT,
    start_ms INTEGER, end_ms INTEGER, lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL,
    fade_in_ms INTEGER NOT NULL DEFAULT 20, fade_out_ms INTEGER NOT NULL DEFAULT 20,
    fade_in_curve TEXT NOT NULL DEFAULT 'exponential', fade_out_curve TEXT NOT NULL DEFAULT 'exponential',
    boundary_src TEXT);
CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL, source TEXT);
CREATE TABLE lowlevel_cache (audio_md5 TEXT, start_ms INTEGER, end_ms INTEGER);
"""

# The passage as it exists everywhere before any edit -- desktop and "remote"
# start identical, the ordinary case for something never touched before.
ORIG = {"start_ms": 100, "end_ms": 50000, "lead_in_ms": 10, "lead_out_ms": 20, "gain_db": 0.0}
# What the edit changes it to.
NEW = {"start_ms": 150, "end_ms": 49500, "lead_in_ms": 25, "lead_out_ms": 40, "gain_db": -1.0}


def make_db(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.executescript(DESKTOP_SCHEMA)
    c.execute("INSERT INTO files VALUES (1, ?)", (AUDIO_MD5,))
    c.execute(
        "INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, lead_in_ms, "
        "lead_out_ms, gain_db, boundary_src) VALUES (1, 1, 'radio', ?, ?, ?, ?, ?, 'src')",
        (ORIG["start_ms"], ORIG["end_ms"], ORIG["lead_in_ms"], ORIG["lead_out_ms"], ORIG["gain_db"]))
    c.execute("INSERT INTO passage_recordings VALUES (1, 'rec-e2e', 1.0, 's')")
    c.commit()
    c.close()


class _FakeRemoteSql:
    """Redirects `remote_peek.run_remote_sql`'s ssh round trip to a real
    local connection, the same stand-in `test_remote_snapshot.py` uses.
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


def run_cli(module, argv) -> str:
    old_argv = sys.argv
    sys.argv = argv
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            module.main()
    finally:
        sys.argv = old_argv
    return buf.getvalue()


def main() -> int:
    tmp_dir = os.path.join(HERE, "..", "out", "test-e2e-push-pipeline")
    os.makedirs(tmp_dir, exist_ok=True)
    desktop_path = os.path.join(tmp_dir, "desktop.db")
    remote_path = os.path.join(tmp_dir, "remote.db")
    make_db(desktop_path)
    make_db(remote_path)   # identical to the desktop -- nothing has diverged yet

    desktop = sqlite3.connect(desktop_path)
    apply_changes.ensure_review_tables(desktop)   # the one true schema, reused
    desktop.execute(
        "INSERT INTO boundary_reviews (passage_id, start_ms, end_ms, lead_in_ms, lead_out_ms, "
        "gain_db, fade_in_ms, fade_out_ms, fade_in_curve, fade_out_curve, audio_md5, orig_kind, "
        "orig_start_ms, orig_end_ms, orig_lead_in_ms, orig_lead_out_ms, orig_gain_db, "
        "orig_fade_in_ms, orig_fade_out_ms, orig_fade_in_curve, orig_fade_out_curve, decided_at) "
        "VALUES (1, ?, ?, ?, ?, ?, 20, 20, 'exponential', 'exponential', ?, 'radio', ?, ?, ?, ?, ?, "
        "20, 20, 'exponential', 'exponential', '2026-08-31T00:00:00')",
        (NEW["start_ms"], NEW["end_ms"], NEW["lead_in_ms"], NEW["lead_out_ms"], NEW["gain_db"],
         AUDIO_MD5, ORIG["start_ms"], ORIG["end_ms"], ORIG["lead_in_ms"], ORIG["lead_out_ms"], ORIG["gain_db"]))
    desktop.commit()

    print("stage 1 -- a saved draft, exactly as Lempi's editor leaves it: "
          "applied_at IS NULL, passages itself untouched")
    p = desktop.execute("SELECT start_ms, end_ms FROM passages WHERE passage_id=1").fetchone()
    check(p == (ORIG["start_ms"], ORIG["end_ms"]), f"passages must still show the original span, got {p}")

    print()
    print("stage 2 -- export_changes finds NOTHING: this is the exact gap "
          "[REQ-VIS-275] found live -- an unapplied draft must not be exportable")
    changes = export_changes.export_boundary_reviews(desktop, "desktop-host")
    check(changes == [], f"an unapplied draft must export nothing, got {changes}")
    desktop.close()

    print()
    print("stage 3 -- apply_boundary_reviews.py --commit folds it into passages")
    out = run_cli(apply_boundary_reviews, ["apply_boundary_reviews.py", desktop_path, "--commit", "--json"])
    summary = json.loads(out.strip().splitlines()[-1])
    check(summary["applied"] == 1, f"expected 1 applied, got {summary}")
    desktop = sqlite3.connect(desktop_path)
    p = desktop.execute("SELECT start_ms, end_ms, lead_in_ms, lead_out_ms, gain_db "
                        "FROM passages WHERE passage_id=1").fetchone()
    check(p == (NEW["start_ms"], NEW["end_ms"], NEW["lead_in_ms"], NEW["lead_out_ms"], NEW["gain_db"]),
          f"passages must now show the new values, got {p}")

    print()
    print("stage 4 -- export_changes now finds exactly one change, anchored "
          "on the PRE-edit span, carrying the new values as its target")
    changes = export_changes.export_boundary_reviews(desktop, "desktop-host")
    check(len(changes) == 1, f"expected 1 exportable change, got {len(changes)}")
    change = changes[0]
    check(change["anchor"] == {"audio_md5": AUDIO_MD5, "passage_kind": "radio",
                               "start_ms": ORIG["start_ms"], "end_ms": ORIG["end_ms"]},
          f"anchor must be the pre-edit span, got {change['anchor']}")
    check(all(change["target"][k] == v for k, v in NEW.items()), f"got {change['target']}")
    desktop.close()
    changes_path = os.path.join(tmp_dir, "changes.json")
    with open(changes_path, "w", encoding="utf-8") as f:
        json.dump({"format_version": 1, "changes": changes}, f)

    print()
    print("stage 5 -- remote_snapshot.py reads the (still-original) remote "
          "with no full copy, targeted at exactly this one change")
    fake = _FakeRemoteSql(remote_path)
    old_run_remote_sql = remote_peek.run_remote_sql
    remote_peek.run_remote_sql = fake
    try:
        fetched, schema = remote_snapshot.fetch("fake@remote:/path", changes)
    finally:
        remote_peek.run_remote_sql = old_run_remote_sql
        fake.close()
    check(fetched[0]["row"].get("passage_id") == 1, f"got {fetched[0]}")
    snapshot_path = os.path.join(tmp_dir, "snapshot.db")
    remote_snapshot.build(changes, fetched, schema, snapshot_path)

    print()
    print("stage 6 -- apply_changes.py classifies it a clean fast-forward "
          "and emits the patch, without ever touching the snapshot itself")
    patch_path = os.path.join(tmp_dir, "patch.sql")
    out = run_cli(apply_changes, ["apply_changes.py", snapshot_path, changes_path,
                                  "--commit", "--emit-sql", patch_path, "--json"])
    result = json.loads(out.strip().splitlines()[-1])
    check(result["fastforward"] == 1 and result["landed"] is True, f"got {result}")
    snap_check = sqlite3.connect(snapshot_path).execute(
        "SELECT start_ms FROM passages WHERE passage_id=1").fetchone()
    check(snap_check == (ORIG["start_ms"],),
          "--emit-sql must never modify the snapshot it compared against, "
          f"got {snap_check}")

    print()
    print("stage 7 -- applying the patch to a copy of the real remote lands "
          "the exact new values there, closing the loop")
    remote = sqlite3.connect(remote_path)
    remote.executescript(open(patch_path, encoding="utf-8").read())
    remote.commit()
    landed = remote.execute("SELECT start_ms, end_ms, lead_in_ms, lead_out_ms, gain_db "
                            "FROM passages WHERE passage_id=1").fetchone()
    check(landed == (NEW["start_ms"], NEW["end_ms"], NEW["lead_in_ms"], NEW["lead_out_ms"], NEW["gain_db"]),
          f"the remote must now show the new values, got {landed}")
    remote.close()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("e2e push pipeline: all checks passed -- draft -> apply -> export -> "
          "snapshot -> merge -> landed on the remote")
    return 0


if __name__ == "__main__":
    sys.exit(main())
