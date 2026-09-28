#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for tools/member_updates.py [REQ-AND-330]: a member is sent exactly
the files whose share of the catalogue changed since it last imported, and
only its word that it imported records them as delivered."""
import json
import os
import sqlite3
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import member_updates as mu  # noqa: E402
import mesh  # noqa: E402
from test_payload import SCHEMA  # noqa: E402

FAILED = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


def main() -> int:
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "library.db")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.execute("ALTER TABLE files ADD COLUMN sha256 TEXT")
    for i, (md5, rec, title) in enumerate((("md5-a", "rec-a", "Alpha"), ("md5-b", "rec-b", "Beta")), 1):
        c.execute("INSERT INTO files VALUES (?,?,?,1,1.0,'mp3',10000,'t','t',?)",
                  (i, md5, os.path.join(tmp, "Music", f"{title}.mp3"), f"{i:064x}"))
        c.execute("INSERT INTO passages (passage_id,file_id,kind,start_ms,end_ms,boundary_src) "
                  "VALUES (?,?,'radio',0,10000,'x')", (i, i))
        c.execute("INSERT INTO recordings VALUES (?,?,NULL,'mb')", (rec, title))
        c.execute("INSERT INTO passage_recordings VALUES (?,?,1.0,'mb')", (i, rec))
    c.commit()
    c.close()
    mdir = mesh.mesh_dir(db)
    mesh.init(mdir, "Test", "desktop")
    fp = "ab" * 32
    manifest = [{"audio_md5": "md5-a", "sha256": "x"},              # by its signature
                {"audio_md5": "retagged", "sha256": f"{2:064x}"},   # by its bytes
                {"audio_md5": "phones-own", "sha256": "y"}]         # not Vipunen's

    s = mu.prepare(db, mdir, fp, manifest)
    check(s["state"] == "ready" and s["files"] == 2 and s["known"] == 2 and s["unknown"] == 1,
          f"a first ask sends every file it knows, matched by signature or by bytes: {s}")
    zp = mu.bundle_path(mdir, fp, s["stamp"])
    names = zipfile.ZipFile(zp).namelist() if zp else []
    check("payload.json" in names and not any(n.startswith("audio/") for n in names),
          f"as a payload-only bundle, no audio: {names}")
    check(mu.bundle_path(mdir, fp, "20000101T000000Z") is None, "no other stamp is served")
    check(mu.status(mdir, fp)["state"] == "ready" and "digests" not in mu.status(mdir, fp),
          "its status says ready, and keeps its digests to itself")

    check(mu.delivered(mdir, fp, s["stamp"], {"imported": 0, "upgraded": 2})
          and mu.status(mdir, fp)["state"] == "delivered" and not os.path.exists(zp),
          "the phone's word that it imported records it as delivered, and the zip goes")
    s2 = mu.prepare(db, mdir, fp, manifest)
    check(s2["state"] == "up-to-date" and s2["files"] == 0, f"asked again, nothing has changed: {s2}")

    c = sqlite3.connect(db)
    c.execute("UPDATE recordings SET title='Alpha (remastered)' WHERE mbid='rec-a'")
    c.commit()
    c.close()
    s3 = mu.prepare(db, mdir, fp, manifest)
    check(s3["state"] == "ready" and s3["files"] == 1, f"a changed recording sends its own file only: {s3}")
    doc = json.loads(zipfile.ZipFile(mu.bundle_path(mdir, fp, s3["stamp"])).read("payload.json"))
    check([e["audio_md5"] for e in doc["encodings"]] == ["md5-a"]
          and doc["recordings"][0]["title"] == "Alpha (remastered)", "the one that changed, as it now is")
    s4 = mu.prepare(db, mdir, fp, manifest, full=True)
    check(s4["files"] == 2, "all of it, when the phone asks for all")
    check(not mu.delivered(mdir, fp, s3["stamp"], {}), "an update replaced by a newer one cannot be acknowledged")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("member_updates: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
