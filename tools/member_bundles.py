#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Music for a phone, from the Export page, fetched over the members'
channel [REQ-AND-235].

A speaker is sent a bundle over ssh (`send_bundle.py`); a phone has no ssh,
and the hub never reaches into a member [SPEC-PGM-310]. So the Export page
builds the bundle and leaves it waiting for the phone, which fetches it when
a person there says so, and imports it with the importer every bundle goes
through [REQ-AND-230]. One offer per phone at a time; its zip is deleted
once the phone says it imported it.

**Whether it fits is measured against what the import needs at its peak**,
not the bundle's own size -- the maintainer's choice, 2026-10-17. The phone
unpacks the whole bundle into private staging, checks each file, and then
copies its audio into `Music/Lempi/` and its covers into the catalogue; the
staging goes only at the end. Both lie on one partition on a phone without a
card, so the peak is about **twice the unpacked bundle**. The zip itself
never lands on the phone: it is unpacked as it arrives.

Against the free space the phone last reported:
- above `CONFIRM_AT` of it, the export stops until a person confirms;
- above `REFUSE_AT`, it is refused, and no bundle is built.
The phone checks again before it fetches, against what is free then, with
the same two numbers (`HubBundles.kt`).
"""
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_db  # noqa: E402
import member_updates  # noqa: E402
import mesh  # noqa: E402

CONFIRM_AT = 0.80
REFUSE_AT = 0.99

_busy: dict[str, threading.Thread] = {}


def peak(unpacked_bytes: int) -> int:
    """What importing a bundle of `unpacked_bytes` needs free at its worst
    moment: the staged bundle, and the copy of it being placed."""
    return 2 * int(unpacked_bytes)


def verdict(need: int, available: int | None) -> dict:
    """`ok`, `confirm` (above CONFIRM_AT of what is free), `refuse` (above
    REFUSE_AT), or `unknown` when the phone has never said what is free --
    which is treated as `confirm`, said as itself [GOV-SRC-040]."""
    if not available:
        return {"verdict": "unknown", "ratio": None}
    r = need / available
    return {"verdict": "refuse" if r > REFUSE_AT else "confirm" if r > CONFIRM_AT else "ok", "ratio": round(r, 4)}


def _dir(mdir: str, fp: str) -> str:
    return os.path.join(mesh.member_dir(mdir, fp), "bundles")


def report_storage(mdir: str, fp: str, body: dict) -> dict:
    """What the phone says is free, kept with when it said it."""
    s = {"available_bytes": int(body.get("available_bytes") or 0), "reported_at": mesh.now()}
    os.makedirs(mesh.member_dir(mdir, fp), exist_ok=True)
    mesh.atomic_write(os.path.join(mesh.member_dir(mdir, fp), "storage.json"), json.dumps(s))
    return s


def storage(mdir: str, fp: str) -> dict | None:
    p = os.path.join(mesh.member_dir(mdir, fp), "storage.json")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def held(mdir: str, fp: str) -> set:
    """The files the phone is known to hold: those a catalogue update has
    been delivered for [SPEC-PL-107]. Empty for a phone that never took one,
    so nothing is left out on a guess."""
    p = os.path.join(member_updates._dir(mdir, fp), "delivered.json")
    if not os.path.isfile(p):
        return set()
    with open(p, encoding="utf-8") as fh:
        return set(json.load(fh))


def plan(db: str, mdir: str, fp: str, like: str) -> dict:
    """What a bundle of the files whose path matches `like` would be, before
    building it: the files, those the phone holds already (left out), the
    audio's size, the peak it needs, and the verdict against what is free."""
    conn = lempi_db.connect(db, lempi_db.ROLE_LIBRARY)
    try:
        rows = conn.execute("SELECT audio_md5, size_bytes FROM files WHERE path LIKE ? ORDER BY path",
                            (like,)).fetchall()
    finally:
        conn.close()
    have = held(mdir, fp)
    send = [(m, b) for m, b in rows if m not in have]
    audio = sum(b for _, b in send)
    st = storage(mdir, fp)
    need = peak(audio)
    return {"matched": len(rows), "held_already": len(rows) - len(send), "files": len(send),
            "audio_bytes": audio, "peak_bytes": need, "md5s": [m for m, _ in send],
            "available_bytes": st["available_bytes"] if st else None,
            "reported_at": st["reported_at"] if st else None,
            "estimate": True, **verdict(need, st["available_bytes"] if st else None)}


def status(mdir: str, fp: str) -> dict:
    p = os.path.join(_dir(mdir, fp), "status.json")
    if fp in _busy and _busy[fp].is_alive():
        return {"state": "building"}
    if not os.path.isfile(p):
        return {"state": "none"}
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _write_status(mdir: str, fp: str, s: dict) -> None:
    os.makedirs(_dir(mdir, fp), exist_ok=True)
    mesh.atomic_write(os.path.join(_dir(mdir, fp), "status.json"), json.dumps(s))


def _drop(mdir: str, fp: str) -> None:
    """The waiting offer's zip, if any: one offer per phone."""
    s = status(mdir, fp)
    if s.get("stamp"):
        zp = os.path.join(_dir(mdir, fp), s["stamp"] + ".zip")
        if os.path.isfile(zp):
            os.remove(zp)


def build(db: str, mdir: str, fp: str, like: str, confirmed: bool) -> dict:
    """Build the bundle and leave it waiting, if it fits. Checked twice: on
    the estimate before anything is built, and on the exact unpacked size
    after, since covers and the payload add to the audio."""
    p = plan(db, mdir, fp, like)
    base = {"query": like, "files": p["files"], "held_already": p["held_already"],
            "available_bytes": p["available_bytes"], "reported_at": p["reported_at"], "made_at": mesh.now()}
    if not p["files"]:
        s = dict(base, state="nothing", why="the phone holds every file that matches" if p["matched"]
                 else "no file matches")
        _write_status(mdir, fp, s)
        return s
    if p["verdict"] == "refuse":
        s = dict(base, state="refused", peak_bytes=p["peak_bytes"], ratio=p["ratio"])
        _write_status(mdir, fp, s)
        return s
    if p["verdict"] in ("confirm", "unknown") and not confirmed:
        s = dict(base, state="needs-confirmation", peak_bytes=p["peak_bytes"], ratio=p["ratio"],
                 verdict=p["verdict"], estimate=True)
        _write_status(mdir, fp, s)
        return s
    _drop(mdir, fp)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(3)
    # Built in a short folder, and only the zip moved under the member's:
    # that one's name carries a 64-character fingerprint, and the first phone
    # export, built there, failed on two files at Windows' 260-character
    # limit (2026-10-17). The bundle's own folders are a music library's.
    work = tempfile.mkdtemp(prefix="lb-")
    out = os.path.join(work, "b")
    md5_file = os.path.join(work, "md5s")
    os.makedirs(_dir(mdir, fp), exist_ok=True)
    with open(md5_file, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(p["md5s"]) + "\n")
    conn = lempi_db.connect(db, lempi_db.ROLE_LIBRARY)
    try:
        root = member_updates.music_root(conn)
    finally:
        conn.close()
    r = subprocess.run([sys.executable, os.path.join(HERE, "export_bundle.py"), db, "--md5-file", md5_file,
                        "--root", root, "-o", out, "--zip"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    built = out + ".zip"
    if r.returncode != 0 or not os.path.isfile(built):
        shutil.rmtree(work, ignore_errors=True)
        s = dict(base, state="failed", error=(r.stdout + r.stderr).strip()[-600:])
        _write_status(mdir, fp, s)
        return s
    unpacked = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(out) for f in fs)
    zp = os.path.join(_dir(mdir, fp), stamp + ".zip")
    shutil.move(built, zp)                          # the zip is what travels
    shutil.rmtree(work, ignore_errors=True)
    need = peak(unpacked)
    v = verdict(need, p["available_bytes"])
    # The phone hashes what arrives as it unpacks it, and imports nothing
    # unless this matches.
    h = hashlib.sha256()
    with open(zp, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    exact = dict(base, stamp=stamp, bytes=os.path.getsize(zp), sha256=h.hexdigest(),
                 unpacked_bytes=unpacked, peak_bytes=need,
                 ratio=v["ratio"], verdict=v["verdict"], estimate=False)
    if v["verdict"] == "refuse":
        os.remove(zp)
        s = dict(exact, state="refused")
    elif v["verdict"] in ("confirm", "unknown") and not confirmed:
        s = dict(exact, state="needs-confirmation")
    else:
        s = dict(exact, state="ready")
    _write_status(mdir, fp, s)
    return s


def start(db: str, mdir: str, fp: str, like: str, confirmed: bool) -> None:
    """Build in the background; the page polls `status`."""
    if fp in _busy and _busy[fp].is_alive():
        return

    def run():
        try:
            build(db, mdir, fp, like, confirmed)
        except Exception as e:                      # shown on the page, never swallowed
            _write_status(mdir, fp, {"state": "failed", "error": f"{type(e).__name__}: {e}"})
    t = threading.Thread(target=run, daemon=True)
    _busy[fp] = t
    t.start()


def confirm(mdir: str, fp: str, stamp: str) -> bool:
    """A person's yes to a built bundle above CONFIRM_AT. Never past
    REFUSE_AT, which `build` has already refused."""
    s = status(mdir, fp)
    if s.get("state") != "needs-confirmation" or s.get("stamp") != stamp:
        return False
    _write_status(mdir, fp, dict(s, state="ready", confirmed_at=mesh.now()))
    return True


def cancel(mdir: str, fp: str) -> None:
    _drop(mdir, fp)
    _write_status(mdir, fp, {"state": "none", "cancelled_at": mesh.now()})


def offered(mdir: str, fp: str) -> dict:
    """What the phone is shown: a ready offer, or that there is none."""
    s = status(mdir, fp)
    if s.get("state") != "ready":
        return {"state": "none"}
    return {k: s[k] for k in ("state", "stamp", "files", "bytes", "sha256", "unpacked_bytes", "peak_bytes", "query")}


def bundle_path(mdir: str, fp: str, stamp: str) -> str | None:
    s = status(mdir, fp)
    if s.get("state") != "ready" or s.get("stamp") != stamp:
        return None
    p = os.path.join(_dir(mdir, fp), stamp + ".zip")
    return p if os.path.isfile(p) else None


def delivered(mdir: str, fp: str, stamp: str, counts: dict) -> bool:
    """The phone imported it: the zip goes, and what it said is kept."""
    s = status(mdir, fp)
    if s.get("state") != "ready" or s.get("stamp") != stamp:
        return False
    _drop(mdir, fp)
    _write_status(mdir, fp, dict(s, state="delivered", delivered_at=mesh.now(), counts=counts))
    return True
