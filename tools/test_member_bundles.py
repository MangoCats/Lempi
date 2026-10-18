#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for tools/member_bundles.py [REQ-AND-235]: a bundle for a phone is
measured by the peak its import needs against what the phone said is free;
above 80% it stops for a yes, above 99% it is refused and nothing is built;
files the phone holds are left out; and the zip goes once it is imported."""
import json
import os
import sqlite3
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import member_bundles as mb  # noqa: E402
import member_updates  # noqa: E402
import mesh  # noqa: E402
from test_export_bundle import SCHEMA  # noqa: E402

FAILED = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


def main() -> int:
    v = mb.verdict
    check(v(80, 100)["verdict"] == "ok" and v(81, 100)["verdict"] == "confirm",
          "80% of what is free goes ahead; above it stops for a yes")
    check(v(99, 100)["verdict"] == "confirm" and v(100, 100)["verdict"] == "refuse",
          "99% still asks; above it is refused")
    check(v(10, None)["verdict"] == "unknown" and v(10, 0)["verdict"] == "unknown",
          "a phone that never said what it has free is unknown, not ok")
    check(mb.peak(1000) == 2000, "the peak is the staged bundle and the copy being placed")

    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "library.db")
    music = os.path.join(tmp, "Music")
    os.makedirs(music)
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    for i, (md5, name, size) in enumerate((("a" * 32, "Alpha.wav", 4000), ("b" * 32, "Beta.wav", 6000)), 1):
        p = os.path.join(music, name)
        with open(p, "wb") as fh:
            fh.write(os.urandom(size))
        c.execute("INSERT INTO files VALUES (?,?,?,?,1.0,'wav',1000,'t','t')", (i, md5, p, size))
        c.execute("INSERT INTO passages VALUES (?,?,'radio',0,1000,NULL,NULL,NULL,'x')", (i, i))
    c.commit()
    c.close()
    mdir = mesh.mesh_dir(db)
    mesh.init(mdir, "Test", "desktop")
    fp = "cd" * 32

    p = mb.plan(db, mdir, fp, "%.wav")
    check(p["files"] == 2 and p["audio_bytes"] == 10000 and p["peak_bytes"] == 20000 and p["verdict"] == "unknown",
          f"a plan before the phone said anything: {p['files']} files, {p['audio_bytes']} bytes, unknown")
    s = mb.build(db, mdir, fp, "%.wav", confirmed=False)
    check(s["state"] == "needs-confirmation" and s.get("estimate") and "stamp" not in s,
          "unknown free space stops for a yes before anything is built")

    mb.report_storage(mdir, fp, {"available_bytes": 19_000})
    s = mb.build(db, mdir, fp, "%.wav", confirmed=True)
    check(s["state"] == "refused" and not any(f.endswith(".zip") for _, _, fs in os.walk(mdir) for f in fs),
          "above 99% it is refused even with a yes, and no zip is left")

    os.makedirs(member_updates._dir(mdir, fp), exist_ok=True)
    with open(os.path.join(member_updates._dir(mdir, fp), "delivered.json"), "w", encoding="utf-8") as fh:
        json.dump({"b" * 32: "digest"}, fh)
    mb.report_storage(mdir, fp, {"available_bytes": 1_000_000})
    p = mb.plan(db, mdir, fp, "%.wav")
    check(p["files"] == 1 and p["held_already"] == 1 and p["md5s"] == ["a" * 32] and p["verdict"] == "ok",
          "a file the phone holds is left out, and the rest fits")
    s = mb.build(db, mdir, fp, "%.wav", confirmed=False)
    zp = os.path.join(mb._dir(mdir, fp), s.get("stamp", "") + ".zip")
    check(s["state"] == "ready" and not s["estimate"] and os.path.isfile(zp)
          and s["peak_bytes"] == 2 * s["unpacked_bytes"] and s["unpacked_bytes"] > 4000,
          f"it fits: built, measured exactly, and waiting: {s.get('unpacked_bytes')} unpacked")
    names = zipfile.ZipFile(zp).namelist()
    check(any(n.startswith("audio/") and n.endswith("Alpha.wav") for n in names)
          and not any(n.endswith("Beta.wav") for n in names), f"the zip carries what was chosen: {names}")
    o = mb.offered(mdir, fp)
    check(o["state"] == "ready" and o["stamp"] == s["stamp"] and mb.bundle_path(mdir, fp, s["stamp"]) == zp,
          "the phone is offered it, by its stamp")

    mb.report_storage(mdir, fp, {"available_bytes": int(s["peak_bytes"] / 0.9)})
    s2 = mb.build(db, mdir, fp, "%.wav", confirmed=False)
    check(s2["state"] == "needs-confirmation" and not s2["estimate"] and not os.path.isfile(zp)
          and mb.offered(mdir, fp)["state"] == "none",
          "a new build replaces the old offer; built at 90% it waits for a yes, and the phone sees nothing")
    check(not mb.confirm(mdir, fp, "20990101T000000Z-000000") and mb.confirm(mdir, fp, s2["stamp"])
          and mb.offered(mdir, fp)["state"] == "ready", "a yes names the bundle it is for, and then it is offered")
    check(mb.delivered(mdir, fp, s2["stamp"], {"imported": 1})
          and not os.path.isfile(os.path.join(mb._dir(mdir, fp), s2["stamp"] + ".zip"))
          and mb.status(mdir, fp)["state"] == "delivered" and mb.offered(mdir, fp)["state"] == "none",
          "imported: the zip goes, and nothing more is offered")
    check(not mb.delivered(mdir, fp, s2["stamp"], {}), "a second word for the same bundle is refused")

    # A deep music folder: the first phone export failed in Windows' 260-character
    # limit, building under the member's folder, whose name is a 64-character
    # fingerprint (2026-10-17, "browne": 2 of 24 files at 262 and 265).
    deep = os.path.join(music, "A" * 60, "B" * 60)
    os.makedirs(deep)
    long_src = os.path.join(deep, "C" * 50 + ".wav")
    with open(long_src, "wb") as fh:
        fh.write(os.urandom(3000))
    c = sqlite3.connect(db)
    c.execute("INSERT INTO files VALUES (3,?,?,3000,1.0,'wav',1000,'t','t')", ("e" * 32, long_src))
    c.execute("INSERT INTO passages VALUES (3,3,'radio',0,1000,NULL,NULL,NULL,'x')")
    c.commit()
    c.close()
    in_member_dir = os.path.join(mb._dir(mdir, fp), "20261018T000000Z-abcdef", "audio",
                                 os.path.relpath(long_src, music))
    s = mb.build(db, mdir, fp, "%CCCC%", confirmed=True)
    check(s["state"] == "ready" and os.path.isfile(os.path.join(mb._dir(mdir, fp), s["stamp"] + ".zip")),
          f"a file whose place under the member's folder would be {len(os.path.abspath(in_member_dir))} "
          f"characters is bundled all the same: {s.get('state')} {s.get('error', '')[-120:]}")
    mb.cancel(mdir, fp)

    # Over the members' channel, as the phone does it, with an enrolled key.
    import http.client
    import ssl
    import threading
    import intake
    from test_mesh import Candidate
    from test_programmes import enrol
    srv = intake.serve_members(mdir, "127.0.0.1", 0, db=db)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    hub_pem = mesh.pem_of(mesh.hub_certificate(mdir))
    phone = Candidate(tmp, "phone")
    enrol(port, mdir, phone)
    st, out, _ = phone.call(port, "POST", "/member/storage", {"available_bytes": 50_000_000},
                            present=True, pin=hub_pem)
    check(st == 200 and mb.storage(mdir, phone.fp)["available_bytes"] == 50_000_000,
          "the phone says what it has free")
    st, out, _ = phone.call(port, "GET", "/member/bundle", present=True, pin=hub_pem)
    check(st == 200 and out == {"state": "none"}, "nothing waits until the Export page builds it")
    s = mb.build(db, mdir, phone.fp, "%.wav", confirmed=False)
    st, out, _ = phone.call(port, "GET", "/member/bundle", present=True, pin=hub_pem)
    check(st == 200 and out["state"] == "ready" and out["stamp"] == s["stamp"] and out["peak_bytes"] == s["peak_bytes"],
          "then it is offered, with the peak the phone checks again")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.load_verify_locations(cadata=hub_pem)
    ctx.load_cert_chain(phone.cert_path, phone.key_path)
    conn = http.client.HTTPSConnection("127.0.0.1", port, context=ctx, timeout=10)
    conn.request("GET", f"/member/bundle/{s['stamp']}.zip")
    r = conn.getresponse()
    body = r.read()
    conn.close()
    import hashlib
    check(r.status == 200 and len(body) == s["bytes"] and hashlib.sha256(body).hexdigest() == out["sha256"],
          f"the zip streams whole, and is the one whose hash was offered: {len(body)} of {s['bytes']} bytes")
    st, out, _ = phone.call(port, "POST", f"/member/bundle/{s['stamp']}/imported", {"imported": 2},
                            present=True, pin=hub_pem)
    check(st == 200 and mb.status(mdir, phone.fp)["state"] == "delivered", "its word that it imported ends it")
    srv.shutdown()

    print(f"\n{len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
