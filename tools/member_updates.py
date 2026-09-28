#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Catalogue updates to a mesh member, over the members' channel [REQ-AND-330].

What a bundle carried by hand did -- Vipunen's names, credits, releases and
covers for files the phone already holds [SPEC-PL-095] -- asked for and sent
over the members' channel [SPEC-MTR-200] instead.

The phone sends a manifest: each file in its catalogue, by byte hash and
audio signature. The hub builds the payload for the files it knows, and takes
a digest of each file's share of it:
- the encoding itself;
- the recordings its passages credit;
- the releases those recordings are on, their tracks narrowed to those
  recordings, with their covers' hashes;
- and its own cover.

A file whose digest differs from the one last delivered to this phone -- or
every file, when the phone asks for all -- goes into a payload-only bundle,
the same `export_bundle.py --payload-only` a person runs. The phone imports
it with the importer it already has. Only its word that the import succeeded
records the digests as delivered.

Measured 2026-09-28: the payload for all 5,709 of the desktop's files builds
in 10 s, so a phone waits seconds for its answer, not minutes.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_db  # noqa: E402
import mesh  # noqa: E402
import payload as payloadmod  # noqa: E402

PART_SIZE = 300          # encodings per payload part: a phone parses a few MB at a time
_busy: dict[str, threading.Thread] = {}


def digests(doc: dict) -> dict[str, str]:
    """Each encoding's share of a payload, as a digest."""
    recs = {r["mbid"]: r for r in doc.get("recordings", [])}
    by_rec: dict[str, list] = {}
    for rel in doc.get("releases", []):
        for t in rel.get("tracks", []):
            by_rec.setdefault(t["recording"], []).append(rel)
    out = {}
    for e in doc["encodings"]:
        credited = sorted({c["mbid"] for p in e.get("passages", []) for c in p.get("recordings", [])})
        rels = {}
        for m in credited:
            for rel in by_rec.get(m, []):
                mine = dict(rel, tracks=[t for t in rel.get("tracks", []) if t["recording"] in credited])
                rels[rel["mbid"]] = mine
        share = {"e": dict(e, bundle_path=None), "r": [recs[m] for m in credited if m in recs],
                 "l": [rels[k] for k in sorted(rels)]}
        out[e["audio_md5"]] = hashlib.sha256(json.dumps(share, sort_keys=True, default=str).encode()).hexdigest()
    return out


def music_root(conn) -> str:
    """The one folder every catalogued file lies under, as the console takes
    it: `bundle_path` is made relative to it."""
    paths = [r[0] for r in conn.execute("SELECT path FROM files")]
    try:
        return os.path.commonpath(paths) if paths else ""
    except ValueError:
        return ""


def _dir(mdir: str, fp: str) -> str:
    return os.path.join(mesh.member_dir(mdir, fp), "catalogue")


def status(mdir: str, fp: str) -> dict:
    p = os.path.join(_dir(mdir, fp), "status.json")
    if fp in _busy and _busy[fp].is_alive():
        return {"state": "preparing"}
    if not os.path.isfile(p):
        return {"state": "none"}
    with open(p, encoding="utf-8") as fh:
        s = json.load(fh)
    return {k: v for k, v in s.items() if k != "digests"}


def _write_status(mdir: str, fp: str, s: dict) -> None:
    os.makedirs(_dir(mdir, fp), exist_ok=True)
    mesh.atomic_write(os.path.join(_dir(mdir, fp), "status.json"), json.dumps(s))


def prepare(db: str, mdir: str, fp: str, manifest: list[dict], full: bool = False) -> dict:
    """Work out what this member lacks, and bundle it. Returns the status."""
    conn = lempi_db.connect(db, lempi_db.ROLE_LIBRARY)
    try:
        by_md5 = {r[0] for r in conn.execute("SELECT audio_md5 FROM files")}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(files)")}
        by_sha = dict(conn.execute("SELECT sha256, audio_md5 FROM files WHERE sha256 IS NOT NULL")) \
            if "sha256" in cols else {}
        held, unknown = set(), 0
        for f in manifest:
            m = f.get("audio_md5") if f.get("audio_md5") in by_md5 else by_sha.get(f.get("sha256"))
            if m:
                held.add(m)
            else:
                unknown += 1
        md5s = sorted(held)
        root = music_root(conn)
        doc = payloadmod.build(conn, md5s, root)
    finally:
        conn.close()
    d = digests(doc)
    last_p = os.path.join(_dir(mdir, fp), "delivered.json")
    last = json.load(open(last_p, encoding="utf-8")) if os.path.isfile(last_p) and not full else {}
    changed = [m for m in md5s if last.get(m) != d.get(m)]
    base = {"asked": len(manifest), "known": len(md5s), "unknown": unknown, "full": full,
            "prepared_at": mesh.now()}
    if not changed:
        s = dict(base, state="up-to-date", files=0)
        _write_status(mdir, fp, s)
        return s
    # Unique even within a second: an ack names exactly the update it imported.
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(3)
    out = os.path.join(_dir(mdir, fp), stamp)
    shutil.rmtree(out, ignore_errors=True)
    md5_file = out + ".md5s"
    os.makedirs(_dir(mdir, fp), exist_ok=True)
    with open(md5_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(changed) + "\n")
    r = subprocess.run([sys.executable, os.path.join(HERE, "export_bundle.py"), db, "--md5-file", md5_file,
                        "--payload-only", "--root", root, "-o", out, "--zip", "--part-size", str(PART_SIZE)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    os.remove(md5_file)
    zp = out + ".zip"
    if r.returncode != 0 or not os.path.isfile(zp):
        s = dict(base, state="failed", error=(r.stdout + r.stderr).strip()[-600:])
        _write_status(mdir, fp, s)
        return s
    shutil.rmtree(out, ignore_errors=True)          # the zip is what travels
    h = hashlib.sha256()
    with open(zp, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    s = dict(base, state="ready", stamp=stamp, files=len(changed), bytes=os.path.getsize(zp),
             sha256=h.hexdigest(), digests={m: d[m] for m in changed})
    _write_status(mdir, fp, s)
    return {k: v for k, v in s.items() if k != "digests"}


def start(db: str, mdir: str, fp: str, manifest: list[dict], full: bool) -> None:
    """Prepare in the background: the phone polls `status`."""
    if fp in _busy and _busy[fp].is_alive():
        return

    def run():
        try:
            prepare(db, mdir, fp, manifest, full)
        except Exception as e:                      # reported to the phone, never swallowed
            _write_status(mdir, fp, {"state": "failed", "error": f"{type(e).__name__}: {e}"})
    t = threading.Thread(target=run, daemon=True)
    _busy[fp] = t
    t.start()


def bundle_path(mdir: str, fp: str, stamp: str) -> str | None:
    """The zip of a ready update, if `stamp` names the one waiting."""
    s = status(mdir, fp)
    if s.get("state") != "ready" or s.get("stamp") != stamp:
        return None
    p = os.path.join(_dir(mdir, fp), stamp + ".zip")
    return p if os.path.isfile(p) else None


def delivered(mdir: str, fp: str, stamp: str, counts: dict) -> bool:
    """The phone imported it: its digests are now what this phone holds."""
    p = os.path.join(_dir(mdir, fp), "status.json")
    if not os.path.isfile(p):
        return False
    with open(p, encoding="utf-8") as fh:
        s = json.load(fh)
    if s.get("state") != "ready" or s.get("stamp") != stamp:
        return False
    last_p = os.path.join(_dir(mdir, fp), "delivered.json")
    last = {} if s.get("full") or not os.path.isfile(last_p) else json.load(open(last_p, encoding="utf-8"))
    last.update(s["digests"])
    mesh.atomic_write(last_p, json.dumps(last))
    zp = os.path.join(_dir(mdir, fp), stamp + ".zip")
    if os.path.isfile(zp):
        os.remove(zp)
    _write_status(mdir, fp, {k: v for k, v in s.items() if k != "digests"} | {
        "state": "delivered", "delivered_at": mesh.now(), "counts": counts})
    return True
