#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Vipunen's intake: music a Lempi node sends for induction [REQ-AND-280].

A phone finds music of its own that the catalogue does not hold. This is where
it sends it -- over the local network, to a node named by host name or address
[REQ-AND-285], [REQ-AND-286] -- and where it waits for a person to decide
[REQ-AND-289].

Two requests, and nothing else:

    POST /offer            what the sender would send: byte hash, signature,
                           size, length and tags per file. Answered per file:
                           held already (by those bytes, or by that audio in
                           other bytes), a near match (same artist and title,
                           length within 3 s), or new -- and whether it is
                           wanted [REQ-AND-288]. Nothing moves until this.
    PUT  /file/<sha256>    one wanted file's bytes. Checked against the hash it
                           was offered under, then kept in the pending folder
                           beside a note of what the sender said and what
                           Vipunen made of it.

Both need the key [REQ-AND-287]: the application's own by default, shared by
every copy, which keeps out mistakes and not people; or one of the user's own,
given to both ends with --key-file. Neither is security against someone on the
LAN who wants in, and this is not presented as such.

**Why a server of its own, and not the console.** The console listens on
loopback only, and has write paths behind it [SPEC-SUI-010]. This listens on
the network, so it does the least a network-facing thing can: the library is
opened read-only, to recognise what is offered, and the one place it writes is
the pending folder. Deciding -- listening, identifying, inducting -- happens in
the console, on this machine [REQ-AND-289].

    python tools/intake.py data/library.db
    python tools/intake.py data/library.db --port 5731 --key-file my.key
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pending as pendingmod  # noqa: E402  -- the decisions a person made [SPEC048]

# The application's own key [REQ-AND-287]: every copy of Lempi and Vipunen
# carries it, so it keeps out a stray request, not a person. The phone's copy is
# `Intake.APP_KEY` in the Android app; the two must agree.
APP_KEY = "lempi-intake-7f3c2a9e5d1b4c86"
DEFAULT_PORT = 5731
# Past this a single file is refused rather than written: the largest file in
# the library is 1.7 GB of DAO capture; a phone's are a few MB.
MAX_FILE = 2_000_000_000
NEAR_MS = 3000
SHA = re.compile(r"^[0-9a-f]{64}$")


def say(msg: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(str(msg).encode(enc, "replace").decode(enc), flush=True)


def norm(s) -> str:
    """As the phone's Director keys an artist tag [REQ-AND-315]: trimmed, lower
    case, inner whitespace collapsed. Applied to titles too."""
    return " ".join(str(s or "").lower().split())


def open_ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True, check_same_thread=False)


def load(db: str):
    """The library as recognition needs it, read fresh for each offer so an
    induction made since is seen."""
    c = open_ro(db)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(files)")}
        by_sha, by_sig, by_name = {}, {}, {}
        tags = "LEFT JOIN file_tags t USING(file_id)" if c.execute(
            "SELECT 1 FROM sqlite_master WHERE name='file_tags'").fetchone() else ""
        q = ("SELECT f.path, f.audio_md5, f.duration_ms, "
             + ("f.sha256" if "sha256" in cols else "NULL")
             + (", t.artist, t.title FROM files f " + tags if tags else ", NULL, NULL FROM files f"))
        for path, sig, dur, sha, artist, title in c.execute(q):
            if sha:
                by_sha[sha] = path
            by_sig[sig] = path
            if artist and title:
                by_name.setdefault((norm(artist), norm(title)), []).append(
                    {"path": path, "artist": artist, "title": title, "duration_ms": dur})
        return by_sha, by_sig, by_name
    finally:
        c.close()


def judge(lib, f: dict) -> dict:
    """One offered file's verdict [REQ-AND-288]."""
    by_sha, by_sig, by_name = lib
    out = {"id": f.get("id"), "sha256": f.get("sha256")}
    if f.get("sha256") in by_sha:
        return {**out, "verdict": "held", "by": "bytes", "held": by_sha[f["sha256"]], "want": False}
    if f.get("audio_md5") and f["audio_md5"] in by_sig:
        return {**out, "verdict": "held", "by": "audio", "held": by_sig[f["audio_md5"]], "want": False}
    tags = f.get("tags") or {}
    dur = f.get("duration_ms")
    near = [n for n in by_name.get((norm(tags.get("artist")), norm(tags.get("title"))), [])
            if not dur or not n["duration_ms"] or abs(n["duration_ms"] - dur) <= NEAR_MS]
    if near:
        return {**out, "verdict": "near", "near": near[:5], "want": True}
    return {**out, "verdict": "new", "want": True}


class Intake:
    """What a running intake knows: the library, the pending folder, the key,
    and what has been offered and is wanted, by byte hash."""

    def __init__(self, db: str, pending: str, key: str, signature=None):
        self.db, self.pending, self.key = db, pending, key
        self.offered: dict[str, dict] = {}
        self.lock = threading.Lock()
        # The signature of what arrives, for the note: the same hash_audio every
        # Vipunen tool uses [SPEC-RLK-152]. Injectable so a test need not build it.
        if signature is None:
            from ingest_folder import audio_md5 as signature
        self.signature = signature
        os.makedirs(pending, exist_ok=True)

    def offer(self, body: dict, sender: str) -> dict:
        lib = load(self.db)
        answers = []
        for f in body.get("files", []):
            if not isinstance(f, dict) or not SHA.match(str(f.get("sha256", ""))):
                answers.append({"id": f.get("id") if isinstance(f, dict) else None,
                                "verdict": "refused", "why": "no valid sha256", "want": False})
                continue
            a = judge(lib, f)
            if a["want"] and os.path.exists(self._path(f["sha256"], f.get("name", ""))[0]):
                a = {**a, "verdict": "pending", "want": False}
            elif a["want"] and pendingmod.decided(self.pending, f["sha256"]) == "rejected":
                # A person decided against it once [SPEC-PID-030]: not asked again.
                a = {**a, "verdict": "rejected", "want": False}
            answers.append(a)
            if a["want"]:
                with self.lock:
                    self.offered[f["sha256"]] = {"offered": f, "answer": a, "sender": sender}
        return {"files": answers}

    def keep(self, sha: str, source: str, entry: dict):
        """Move a received file, already checked, into pending beside its
        note. Returns where it went and the note."""
        dest, note = self._path(sha, entry["offered"].get("name", ""))
        os.replace(source, dest)
        record = {
            "received_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sender": entry["sender"],
            "offered": entry["offered"],
            "verdict": entry["answer"]["verdict"],
            "near": entry["answer"].get("near", []),
            "signature": self.signature(dest),
            "bytes": os.path.getsize(dest),
            "file": os.path.basename(dest),
        }
        with open(note, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, ensure_ascii=False)
        with self.lock:
            self.offered.pop(sha, None)
        say(f"intake: kept {os.path.basename(dest)} ({record['verdict']}) from {entry['sender']}")
        return dest, record

    def from_folder(self, folder: str) -> dict:
        """[REQ-AND-285]: the same offer, carried by hand -- `offer.json` and
        the files named by their byte hash, as the phone writes them where no
        node answers. Judged exactly as an offer over the network is, and each
        wanted file kept only if its bytes are what it was offered as."""
        with open(os.path.join(folder, "offer.json"), encoding="utf-8") as fh:
            body = json.load(fh)
        answers = self.offer(body, "folder " + os.path.basename(os.path.normpath(folder)))
        out = {"kept": [], "not_wanted": [], "damaged": [], "missing": []}
        by_id = {f.get("id"): f for f in body.get("files", []) if isinstance(f, dict)}
        for a in answers["files"]:
            f = by_id.get(a.get("id"), {})
            if not a.get("want"):
                out["not_wanted"].append((f.get("name"), a["verdict"]))
                continue
            sha = f["sha256"]
            src = os.path.join(folder, sha + os.path.splitext(f.get("name", ""))[1])
            if not os.path.isfile(src):
                out["missing"].append(f.get("name"))
                continue
            h = hashlib.sha256()
            with open(src, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != sha:
                out["damaged"].append(f.get("name"))
                continue
            staged = self._path(sha, f.get("name", ""))[0] + ".part"
            shutil.copyfile(src, staged)
            with self.lock:
                entry = self.offered[sha]
            self.keep(sha, staged, entry)
            out["kept"].append((f.get("name"), a["verdict"]))
        return out

    def _path(self, sha: str, name: str):
        ext = os.path.splitext(name)[1].lower()
        ext = ext if re.fullmatch(r"\.[a-z0-9]{1,5}", ext) else ".bin"
        base = os.path.join(self.pending, sha)
        return base + ext, base + ".json"


class Handler(BaseHTTPRequestHandler):
    intake: Intake = None  # set on the class by serve()

    def log_message(self, fmt, *args):
        say(f"intake: {self.client_address[0]} " + fmt % args)

    def drain(self):
        """Read a refused request's body before answering it, so the sender
        hears the refusal rather than a connection cut mid-send. Up to a MB;
        past that the answer goes out and the connection closes."""
        n = int(self.headers.get("Content-Length") or 0)
        if 0 < n <= 1 << 20:
            self.rfile.read(n)
        elif n:
            self.close_connection = True

    def reply(self, code: int, body: dict):
        if code >= 400 and not getattr(self, "consumed", False):
            self.drain()
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorised(self) -> bool:
        if self.headers.get("X-Lempi-Key") == self.intake.key:
            return True
        self.reply(403, {"error": "wrong or missing key"})
        return False

    def do_GET(self):
        if not self.authorised():
            return
        if self.path == "/repairs":
            return self.reply(200, {"repairs": pendingmod.repairs(self.intake.pending)})
        m = re.fullmatch(r"/good/([0-9a-f]{64})", self.path)
        if m:
            return self.good(m.group(1))
        self.reply(200, {"intake": "vipunen", "pending": self.intake.pending})

    def good(self, sha: str):
        """[SPEC-PID-040]: the library's good copy, for a repair a person
        decided -- and only then: this is not a way to fetch the library.
        Checked against its hash before it goes, so what is sent is what the
        decision named."""
        for e in pendingmod.entries(self.intake.pending):
            d = e.get("decision") or {}
            if d.get("action") == "repair" and d.get("good_sha256") == sha:
                path = d["good_path"]
                break
        else:
            return self.reply(404, {"error": "no repair offers that file"})
        if not os.path.isfile(path) or pendingmod.sha256_file(path) != sha:
            return self.reply(410, {"error": "the library's copy has changed since the repair was decided"})
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(os.path.getsize(path)))
        self.end_headers()
        with open(path, "rb") as fh:
            shutil.copyfileobj(fh, self.wfile, 1 << 16)

    def do_POST(self):
        if not self.authorised():
            return
        m = re.fullmatch(r"/repaired/([0-9a-f]{64})", self.path)
        if m:
            # The sender has written the good copy over its own and says what
            # its file's bytes now hash to; only the good copy's hash closes it.
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if 0 < n <= 4096 else {}
            self.consumed = True
            if pendingmod.repaired(self.intake.pending, m.group(1), str(body.get("sha256", ""))):
                say(f"intake: repaired {m.group(1)[:12]} at {self.headers.get('X-Lempi-Sender') or self.client_address[0]}")
                return self.reply(200, {"repaired": True})
            return self.reply(409, {"error": "no such repair, or the bytes are not the good copy's"})
        if self.path != "/offer":
            return self.reply(404, {"error": "unknown"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 20_000_000:
            return self.reply(413, {"error": "an offer is a description, not the files"})
        raw = self.rfile.read(n)
        self.consumed = True
        try:
            body = json.loads(raw)
        except ValueError:
            return self.reply(400, {"error": "not JSON"})
        sender = str(self.headers.get("X-Lempi-Sender") or self.client_address[0])
        self.reply(200, self.intake.offer(body, sender))

    def do_PUT(self):
        if not self.authorised():
            return
        m = re.fullmatch(r"/file/([0-9a-f]{64})", self.path)
        if not m:
            return self.reply(404, {"error": "unknown"})
        sha = m.group(1)
        with self.intake.lock:
            entry = self.intake.offered.get(sha)
        if entry is None:
            return self.reply(409, {"error": "not offered, or not wanted -- offer it first"})
        n = int(self.headers.get("Content-Length") or -1)
        if n <= 0 or n > MAX_FILE:
            return self.reply(413, {"error": f"length {n} refused"})
        part = self.intake._path(sha, entry["offered"].get("name", ""))[0] + ".part"
        h = hashlib.sha256()
        left = n
        self.consumed = True
        with open(part, "wb") as out:
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 16))
                if not chunk:
                    break
                h.update(chunk)
                out.write(chunk)
                left -= len(chunk)
        if left or h.hexdigest() != sha:
            os.remove(part)
            return self.reply(422, {"error": "what arrived is not what was offered; nothing kept"})
        dest, record = self.intake.keep(sha, part, entry)
        self.reply(201, {"kept": os.path.basename(dest), "verdict": record["verdict"]})


def serve(intake: Intake, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("Bound", (Handler,), {"intake": intake})
    return ThreadingHTTPServer((host, port), handler)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("db")
    ap.add_argument("--pending", help="where arrivals wait (default: pending-identification/ beside the library)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--bind", default="0.0.0.0",
                    help="address to listen on (default all -- a phone must reach it)")
    ap.add_argument("--key-file", help="a key of the user's own [REQ-AND-287]; default: the application's")
    ap.add_argument("--from-folder", help="take an offer carried by hand -- a folder the phone wrote -- "
                                          "and stop, without listening [REQ-AND-285]")
    args = ap.parse_args(argv)
    if not os.path.isfile(args.db):
        say(f"no such database: {args.db}")
        return 1
    pending = args.pending or os.path.join(os.path.dirname(os.path.abspath(args.db)), "pending-identification")
    key = APP_KEY
    if args.key_file:
        with open(args.key_file, encoding="utf-8") as fh:
            key = fh.read().strip()
        if not key:
            say(f"{args.key_file} is empty")
            return 1
    intake = Intake(args.db, pending, key)
    if args.from_folder:
        out = intake.from_folder(args.from_folder)
        for name, verdict in out["kept"]:
            say(f"  kept        {name} ({verdict})")
        for name, verdict in out["not_wanted"]:
            say(f"  not wanted  {name} ({verdict})")
        for name in out["damaged"]:
            say(f"  DAMAGED     {name}: its bytes are not what the offer says; not kept")
        for name in out["missing"]:
            say(f"  MISSING     {name}: offered, but not in the folder")
        say(f"intake: {len(out['kept'])} kept in {pending}")
        return 1 if out["damaged"] or out["missing"] else 0
    # Say what this is and where it listens [GDE-DEP-060]: a network-facing
    # listener should never be a surprise.
    say(f"intake: library {os.path.abspath(args.db)} (read-only)")
    say(f"intake: pending {pending}")
    say(f"intake: key     {'the application key' if key == APP_KEY else 'from ' + args.key_file}")
    say(f"intake: listening on {args.bind}:{args.port}")
    with serve(intake, args.bind, args.port) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            say("intake: stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
