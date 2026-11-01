#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `backfill_retired.py` `[SPEC-RTF-015]`.

A merge made before the hub recorded what it retired: its decision names
where each file went, a copy from before it names the files. The backfill
records each audio file as the merge would have, dated by the merge, leaves a
sheet that went with them alone, says what no copy names, and writes nothing
a second time.

    python tools/test_backfill_retired.py
"""

import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backfill_retired as br   # noqa: E402
import ingest_cd               # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL: {msg}")


SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE, path TEXT,
    size_bytes INTEGER, sha256 TEXT);
CREATE TABLE ingest_decisions (decision_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL,
    stage TEXT NOT NULL, outcome TEXT NOT NULL, confidence REAL, detail TEXT, decided_at TEXT);
"""


def main() -> int:
    tmp = tempfile.mkdtemp()
    music = os.path.join(tmp, "Music")
    album = os.path.join(music, "Artist", "Album")
    to = os.path.join(music, "_Retired", "Artist", "Album", "2026-10-30")
    before, now = os.path.join(tmp, "before.db"), os.path.join(tmp, "library.db")
    for p in (before, now):
        c = sqlite3.connect(p)
        c.executescript(SCHEMA)
        c.execute("INSERT INTO files (audio_md5, path, size_bytes, sha256) VALUES ('dao', ?1, 900, 'shadao')",
                  (os.path.join(album, "Artist - Album.mp3"),))
        c.commit()
        c.close()
    c = sqlite3.connect(before)
    for n, md5 in ((1, "old1"), (2, "old2")):
        c.execute("INSERT INTO files (audio_md5, path, size_bytes, sha256) VALUES (?1, ?2, ?3, ?4)",
                  (md5, os.path.join(album, f"0{n}. Song.mp3"), 100 * n, f"sha{n}"))
    c.commit()
    c.close()
    c = sqlite3.connect(now)
    c.execute("INSERT INTO ingest_decisions (audio_md5, stage, outcome, confidence, detail, decided_at) "
              "VALUES ('dao', 'rip', 'merged', 1.0, ?1, '2026-10-30T22:19:02')",
              (json.dumps({"to": to, "retired": [os.path.join(to, "01. Song.mp3"), os.path.join(to, "02. Song.mp3"),
                                                 os.path.join(to, "03. Song.mp3"), os.path.join(to, "Album.cue")]}),))
    c.commit()
    todo, lost = br.plan(c, [before])
    check(sorted(t["audio_md5"] for t in todo) == ["old1", "old2"], f"each audio file the copy names: {todo}")
    one = next((t for t in todo if t["audio_md5"] == "old1"), {})
    check(one.get("at") == "2026-10-30T22:19:02" and one.get("detail", {}).get("rel_path") == "Artist/Album/01. Song.mp3"
          and one["detail"]["sha256"] == "sha1" and one["detail"]["size_bytes"] == 100
          and one["detail"]["replaced_by"] == "dao" and one["detail"]["backfilled"] is True,
          f"as the merge would have written it, dated by the merge: {one}")
    check(len(lost) == 1 and "03. Song.mp3" in lost[0], f"a file no copy names is said, not invented: {lost}")
    c.close()

    import subprocess
    run = [sys.executable, os.path.join(HERE, "backfill_retired.py"), now, "--before", before]
    r = subprocess.run(run + ["--commit"], capture_output=True, text=True)
    check(r.returncode == 1 and "NOT FOUND" in r.stdout, f"exit 1, the missing one said: {r.returncode} {r.stdout}")
    c = sqlite3.connect(now)
    check(sorted(ingest_cd.retired_files(c)) == ["old1", "old2"], "both recorded, as ingest_cd reads them")
    n = c.execute("SELECT COUNT(*) FROM ingest_decisions WHERE stage = 'retire'").fetchone()[0]
    c.close()
    subprocess.run(run + ["--commit"], capture_output=True, text=True)
    c = sqlite3.connect(now)
    check(n == 2 and c.execute("SELECT COUNT(*) FROM ingest_decisions WHERE stage = 'retire'").fetchone()[0] == 2,
          "and a second run writes nothing")
    # Catalogued again since: not retired, whatever was said.
    c.execute("INSERT INTO files (audio_md5, path) VALUES ('old1', 'back.mp3')")
    check(sorted(ingest_cd.retired_files(c)) == ["old2"], "a file the hub catalogues again is not retired")
    c.close()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("backfill_retired: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
