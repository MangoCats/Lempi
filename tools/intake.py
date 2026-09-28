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
import hmac
import ipaddress
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
import mesh as meshmod  # noqa: E402  -- membership, enrolment and the members' channel [SPEC049]
import member_updates  # noqa: E402  -- catalogue updates to a member [REQ-AND-330]

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
        # Constant-time `[SecurityReview S2]`: the key keeps out a stray
        # request, not a person, but `==` on a secret leaks its length and
        # leading bytes through timing for no reason when the stdlib compares
        # in constant time for free.
        given = self.headers.get("X-Lempi-Key") or ""
        if hmac.compare_digest(given, self.intake.key):
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


# ------------------------------------------------------ the members' port --
#
# [SPEC-MTR-200]: TLS 1.3, the hub presenting its node key, and a client
# certificate accepted only if its key is in the current roster -- the trust
# store *is* the roster, rebuilt whenever its version changes, so a member
# removed is refused from its next connection. No certificate at all is let in
# too, for one purpose: a candidate asking to enrol, which a person must then
# confirm on both sides [SPEC-MTR-130]. Every `/member/` route needs a member.

class MemberServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler, mdir: str, db: str | None = None):
        self.mdir, self.db = mdir, db
        self._ctx, self._version = None, None
        super().__init__(addr, handler)

    def context(self):
        import ssl
        r = meshmod.roster(self.mdir)
        if r["version"] != self._version:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_3
            ctx.load_cert_chain(os.path.join(self.mdir, "node.pem"), os.path.join(self.mdir, "node.key"))
            ctx.verify_mode = ssl.CERT_OPTIONAL
            ctx.load_verify_locations(cadata="".join(m["certificate"] for m in r["members"]))
            self._ctx, self._version = ctx, r["version"]
        return self._ctx

    def get_request(self):
        sock, addr = self.socket.accept()
        # The handshake is made in the request's own thread, not here: a slow
        # or hostile client must not hold up everyone else's accept.
        return self.context().wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

    def handle_error(self, request, client_address):
        say(f"intake: members' port: {client_address[0]}: {sys.exc_info()[1]}")


class MemberHandler(BaseHTTPRequestHandler):
    server: MemberServer

    def setup(self):
        self.request.settimeout(20)
        self.request.do_handshake()      # a stranger's certificate fails here
        super().setup()

    def log_message(self, fmt, *args):
        say(f"intake: members' port: {self.client_address[0]} " + fmt % args)

    def reply(self, code: int, body: dict):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= 64_000:
            raise ValueError("no body, or too large")
        return json.loads(self.rfile.read(n))

    def peer(self) -> dict | None:
        """The member at the other end, by the key its certificate proved."""
        from cryptography import x509
        der = self.request.getpeercert(binary_form=True)
        if not der:
            return None
        return meshmod.member(self.server.mdir, meshmod.fingerprint(x509.load_der_x509_certificate(der)))

    def do_GET(self):
        mdir = self.server.mdir
        if self.path.startswith("/enrol/status/"):
            try:
                return self.reply(200, meshmod.status(mdir, self.path.rsplit("/", 1)[-1]))
            except (ValueError, OSError):
                return self.reply(404, {"error": "no such enrolment"})
        if not self.path.startswith("/member/"):
            return self.reply(404, {"error": "unknown"})
        m = self.peer()
        if m is None:
            return self.reply(403, {"error": "members only: present an enrolled key"})
        if self.path == "/member/hello":
            r = meshmod.roster(mdir)
            return self.reply(200, {"you": m["fingerprint"], "name": m["name"], "role": m["role"],
                                    "mesh": r["mesh"]["name"], "roster_version": r["version"]})
        if self.path == "/member/roster":
            return self.reply(200, meshmod.signed_roster(mdir))
        if self.path == "/member/updates":
            # [REQ-AND-330]: what the last star sync decided for this member,
            # until it says it has applied it.
            o = meshmod.outbox(mdir, m["fingerprint"])
            return self.reply(200, o or {"run": None})
        if self.path == "/member/catalogue":
            return self.reply(200, member_updates.status(mdir, m["fingerprint"]))
        mt = re.fullmatch(r"/member/catalogue/([0-9TZ]+-[0-9a-f]{6})\.zip", self.path)
        if mt:
            # This member's own update, and only while it waits for it.
            p = member_updates.bundle_path(mdir, m["fingerprint"], mt.group(1))
            if p is None:
                return self.reply(404, {"error": "no such update waiting"})
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(os.path.getsize(p)))
            self.end_headers()
            with open(p, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile, 1 << 16)
            return None
        self.reply(404, {"error": "unknown"})

    def do_POST(self):
        mdir = self.server.mdir
        if self.path.startswith("/member/"):
            return self.member_post(mdir)
        try:
            b = self.body()
            if self.path == "/enrol/start":
                sender = str(self.headers.get("X-Lempi-Sender") or self.client_address[0])
                out = meshmod.enrol_start(mdir, str(b.get("certificate", "")), str(b.get("name", "")),
                                          str(b.get("role", "")), sender)
                say(f"intake: enrolment asked by {sender} ({out['session'][:8]}): confirm it at the hub")
                return self.reply(200, out)
            if self.path == "/enrol/nonce":
                return self.reply(200, meshmod.enrol_nonce(mdir, str(b.get("session")), str(b.get("nonce")),
                                                           str(b.get("signature"))))
            if self.path == "/enrol/confirm":
                return self.reply(200, meshmod.enrol_confirm(mdir, str(b.get("session")), str(b.get("signature"))))
        except (ValueError, KeyError, OSError) as e:
            return self.reply(400, {"error": str(e)})
        self.reply(404, {"error": "unknown"})

    def member_post(self, mdir: str):
        """A member's writes [REQ-AND-330]: its upload of the shared tables, and
        word that it has applied its patch."""
        m = self.peer()
        if m is None:
            return self.reply(403, {"error": "members only: present an enrolled key"})
        n = int(self.headers.get("Content-Length") or 0)
        if self.path == "/member/listener":
            if not 0 < n <= 50_000_000:
                return self.reply(413, {"error": f"length {n} refused"})
            data = self.rfile.read(n)
            try:
                clock = int(self.headers.get("X-Lempi-Clock") or "")
            except ValueError:
                clock = None
            try:
                meta = meshmod.store_upload(mdir, m["fingerprint"], data, clock)
            except ValueError as e:
                return self.reply(409, {"error": str(e)})
            say(f"intake: {m['name'] or meshmod.show_fp(m['fingerprint'], True)} uploaded its shared edits: "
                f"{meta['rows']}, clock {meta['clock_offset_ms']:+d} ms")
            return self.reply(200, meta)
        if self.path == "/member/catalogue":
            # A manifest of what the phone holds; the answer is prepared in
            # the background, and the phone polls for it.
            if not 0 < n <= 20_000_000 or not self.server.db:
                return self.reply(413 if self.server.db else 503, {"error": "manifest refused"})
            b = json.loads(self.rfile.read(n))
            files = [f for f in b.get("files", []) if isinstance(f, dict)]
            member_updates.start(self.server.db, mdir, m["fingerprint"], files, bool(b.get("full")))
            say(f"intake: {m['name'] or meshmod.show_fp(m['fingerprint'], True)} asks for catalogue updates "
                f"for {len(files)} file(s){' (all of it)' if b.get('full') else ''}")
            return self.reply(202, {"state": "preparing"})
        mi = re.fullmatch(r"/member/catalogue/([0-9TZ]+-[0-9a-f]{6})/imported", self.path)
        if mi:
            b = json.loads(self.rfile.read(n) or b"{}") if 0 < n <= 64_000 else {}
            if member_updates.delivered(mdir, m["fingerprint"], mi.group(1), b):
                say(f"intake: {m['name'] or meshmod.show_fp(m['fingerprint'], True)} imported update {mi.group(1)}: {b}")
                return self.reply(200, {"ok": True})
            return self.reply(409, {"error": "no such update waiting"})
        if self.path == "/member/updates/applied":
            b = json.loads(self.rfile.read(n) or b"{}") if 0 < n <= 64_000 else {}
            counts = {k: int(b.get(k, 0)) for k in ("applied", "already", "kept")}
            if meshmod.outbox_applied(mdir, m["fingerprint"], str(b.get("run")), counts):
                say(f"intake: {m['name'] or meshmod.show_fp(m['fingerprint'], True)} applied run {b.get('run')}: {counts}")
                return self.reply(200, {"ok": True})
            return self.reply(409, {"error": "no such patch waiting"})
        self.reply(404, {"error": "unknown"})


def serve_members(mdir: str, host: str, port: int, db: str | None = None) -> MemberServer:
    return MemberServer((host, port), MemberHandler, mdir, db)


def _is_loopback(bind: str) -> bool:
    """Whether a `--bind` address reaches only this machine. `localhost`
    resolves to a loopback address; a literal is loopback only if it is one.
    `0.0.0.0`/`::` (all interfaces) and any LAN address are not."""
    if bind == "localhost":
        return True
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return False


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("db")
    ap.add_argument("--pending", help="where arrivals wait (default: pending-identification/ beside the library)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--member-port", type=int, default=5732,
                    help="the members' TLS channel, served where a mesh exists [SPEC-MTR-200]")
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
    # The published application key is safe only where nobody else can reach
    # the port `[SecurityReview S2/C5]`. A bind that is not loopback exposes
    # it to the LAN, where a key everyone can read from the repo is no gate at
    # all -- so refuse to start with it, and say why, rather than listen with
    # a guard that guards nothing `[GDE-DEP-060]`. `--from-folder` takes a
    # hand-carried offer and never listens, so the bind is moot there.
    if key == APP_KEY and not _is_loopback(args.bind) and not args.from_folder:
        say(f"intake: refusing to serve the published application key on {args.bind} "
            f"(reachable from the LAN). Pass --key-file, or --bind 127.0.0.1.")
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
    mdir = meshmod.mesh_dir(args.db)
    if meshmod.initialised(mdir):
        r = meshmod.roster(mdir)
        members = serve_members(mdir, args.bind, args.member_port, db=args.db)
        threading.Thread(target=members.serve_forever, daemon=True).start()
        say(f"intake: mesh    '{r['mesh']['name']}' {meshmod.show_fp(r['mesh']['fingerprint'], True)}, "
            f"{len(r['members'])} member(s); members' port {args.bind}:{args.member_port} (TLS)")
    else:
        say(f"intake: mesh    none in {mdir}, so no members' port "
            f"(python tools/mesh.py {args.db} init --mesh NAME)")
    say(f"intake: listening on {args.bind}:{args.port}")
    with serve(intake, args.bind, args.port) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            say("intake: stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
