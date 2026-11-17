#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for tools/mesh.py and the intake's members' port [SPEC049].

A real TLS server on loopback, and a candidate that behaves as the phone does:
it enrols without a certificate, takes the hub's key from the connection
itself, proves its own key by signing, and then uses that key as its TLS
client certificate.
"""
import base64
import gzip
import hashlib
import http.client
import json
import os
import secrets
import ssl
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import intake  # noqa: E402
import mesh  # noqa: E402
from cryptography import x509  # noqa: E402

FAILED = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


class Candidate:
    """A node that is not yet a member: its own key and certificate."""

    def __init__(self, tmp, name):
        self.key_path, self.cert_path = os.path.join(tmp, name + ".key"), os.path.join(tmp, name + ".pem")
        self.key = mesh.make_identity(self.key_path, self.cert_path, "lempi-node")
        self.pem = open(self.cert_path, encoding="ascii").read()
        self.fp = mesh.fingerprint(mesh.cert_from_pem(self.pem))

    def call(self, port, method, path, body=None, present=False, pin=None):
        """One request. `present`: offer this key as the client certificate.
        `pin`: the hub certificate to trust; without it, anything (as at
        enrolment, where the code is what proves who answered)."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        if pin:
            ctx.load_verify_locations(cadata=pin)
        else:
            ctx.verify_mode = ssl.CERT_NONE
        if present:
            ctx.load_cert_chain(self.cert_path, self.key_path)
        c = http.client.HTTPSConnection("127.0.0.1", port, context=ctx, timeout=10)
        try:
            c.request(method, path, body=None if body is None else json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
            server = c.sock.getpeercert(binary_form=True)    # before the server closes
            r = c.getresponse()
            data = r.read()
            return r.status, json.loads(data), mesh.fingerprint(x509.load_der_x509_certificate(server))
        finally:
            c.close()

    def sign(self, text: str) -> str:
        return mesh.sign(self.key, text.encode())


def refused(fn) -> bool:
    try:
        fn()
        return False
    except (ssl.SSLError, ConnectionError, OSError):
        return True


def test_sync_step(tmp):
    """[SPEC-NSH-030], the hub's side: a request signed by the mesh key, and an
    answer taken only when the player's own key signed it and it answers this
    request. A local server plays the player's part. Its own directory: main
    makes candidates of the same names."""
    import http.server
    tmp = tempfile.mkdtemp()
    from cryptography.hazmat.primitives import serialization
    mdir = mesh.mesh_dir(os.path.join(tmp, "sync", "library.db"))
    mesh.init(mdir, "Sync mesh", "desktop")
    player, stranger = Candidate(tmp, "player"), Candidate(tmp, "stranger")
    r = mesh.roster(mdir)
    r["members"].append(mesh.member_entry(mesh.cert_from_pem(player.pem), "player", "p"))
    mesh.publish(mdir, r)
    mesh_pub = serialization.load_pem_public_key(mesh.roster(mdir)["mesh"]["public_key"].encode())
    mode, seen = {"v": "good"}, {}

    class Player(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            # [SPEC-NSH-096]: a request sent gzipped, read as the player's handler reads it.
            seen["sent_gz"] = self.headers.get("Content-Encoding") == "gzip"
            seen["sent_bytes"] = len(raw)
            body = json.loads(gzip.decompress(raw) if seen["sent_gz"] else raw)
            seen["asked"] = seen.get("asked", 0) + 1
            if mode["v"] == "cut" or (mode["v"] == "drop-first" and seen["asked"] == 1):
                # [SPEC-NSH-092]: the player restarting mid-answer, or the radio re-associating.
                if mode["v"] == "cut":
                    self.send_response(200)
                    self.send_header("Content-Length", "100000")
                    self.end_headers()
                    self.wfile.write(b'{"answer": "')
                self.close_connection = True
                return
            seen["accept"] = self.headers.get("Accept-Encoding")
            zipped = mode["v"].endswith("-gz") and "gzip" in (seen["accept"] or "")

            def send(code, out):
                # [SPEC-NSH-096]: gzipped, as the player's own layer does for a client that asks.
                if zipped:
                    out = gzip.compress(out)
                    if mode["v"] == "cut-gz":
                        out = out[:len(out) // 2]
                self.send_response(code)
                if zipped:
                    self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
            if mode["v"] in ("refuse", "refuse-gz"):
                send(409, json.dumps({"error": "run 20260928T1830Z is not newer than 20260929T0000Z"}).encode())
                return
            req = json.loads(body["request"])
            seen["signed"] = mesh.verify(mesh_pub, body["request"].encode(), body["signature"])
            seen["req"] = req
            who = stranger if mode["v"] == "stranger" else player
            nonce = "0" * 32 if mode["v"] == "nonce" else req["nonce"]
            ans = json.dumps({"op": req["op"], "run": req["run"], "nonce": nonce, "node": req["node"],
                              "result": {"ok": 1}})
            send(200, json.dumps({"answer": ans, "signature": who.sign(ans)}).encode())

    srv = http.server.HTTPServer(("127.0.0.1", 0), Player)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    where = {"fingerprint": player.fp, "address": "127.0.0.1", "web_port": srv.server_address[1]}
    try:
        got = mesh.sync_step(mdir, where, "snapshot", "20260928T1830Z")
        check(got == {"ok": 1}, "sync_step: the player's signed answer is taken")
        check(seen["signed"], "sync_step: the request is signed by the mesh key")
        check(seen["req"]["node"] == player.fp and seen["req"]["mesh"] == mesh.roster(mdir)["mesh"]["fingerprint"],
              "sync_step: the request names the node and the mesh")
        for m, why in (("stranger", "an answer signed by another key"), ("nonce", "an answer to another request")):
            mode["v"] = m
            try:
                mesh.sync_step(mdir, where, "snapshot", "20260928T1830Z")
                check(False, f"sync_step: {why} is refused")
            except ValueError:
                check(True, f"sync_step: {why} is refused")
        try:
            mesh.sync_step(mdir, dict(where, fingerprint=stranger.fp), "snapshot", "20260928T1830Z")
            check(False, "sync_step: a node not in the roster is not asked")
        except ValueError:
            check(True, "sync_step: a node not in the roster is not asked")
        test_asked_again(mdir, where, mode, seen)
    finally:
        srv.shutdown()


def test_asked_again(mdir, where, mode, seen):
    """[SPEC-NSH-092], [SPEC-NSH-032]: a step the network failed is asked again, and said;
    a refusal never is, nor a commit unless the node says it answers one asked again."""
    said, slept = [], []
    real = (mesh.say, mesh.time.sleep)
    mesh.say, mesh.time.sleep = said.append, slept.append

    def ask(m, op="snapshot", again=False):
        mode["v"], seen["asked"] = m, 0
        said.clear()
        try:
            return mesh.sync_step(mdir, where, op, "20260928T1830Z", again=again), None
        except (ValueError, OSError) as err:
            return None, err
    try:
        got, err = ask("cut")
        check(isinstance(err, ConnectionError) and "cut off" in str(err) and seen["asked"] == mesh.ATTEMPTS,
              f"an answer cut off is a network failure, asked {mesh.ATTEMPTS} times, then raised: {err!r}")
        check(len(said) == mesh.ATTEMPTS - 1 and all("asking again" in s for s in said), "and each new attempt is said")
        got, err = ask("drop-first")
        check(got == {"ok": 1} and seen["asked"] == 2, f"a connection dropped once costs one more request: {err!r}")
        check(slept[-1:] == [mesh.BACKOFF_S[0]], "after a pause")
        got, err = ask("refuse")
        check(isinstance(err, ValueError) and "not newer" in str(err) and seen["asked"] == 1,
              "a refusal is the node's answer, and is never asked again")
        got, err = ask("drop-first", op="commit")
        check(isinstance(err, OSError) and seen["asked"] == 1,
              "a commit is not asked again of a node that has not said it answers one asked again")
        got, err = ask("drop-first", op="commit", again=True)
        check(got == {"ok": 1} and seen["asked"] == 2, "and is, of one that has")
        check(mesh.network_failed(TimeoutError("timed out"), "commit") and not mesh.network_failed(TimeoutError(), "rows"),
              "a step that ran out of time waiting for its answer is not asked again, but a commit is")
        # [SPEC-NSH-096]: every request asks for its answer gzipped, and a gzipped one is read.
        got, err = ask("good-gz")
        check(got == {"ok": 1} and seen["accept"] == "gzip", f"a gzipped answer is read, and was asked for: {err!r}")
        got, err = ask("good")
        check(got == {"ok": 1}, "and an answer not gzipped is read as before")
        got, err = ask("refuse-gz")
        check(isinstance(err, ValueError) and "not newer" in str(err), f"a gzipped refusal keeps its words: {err!r}")
        got, err = ask("cut-gz")
        check(isinstance(err, ConnectionError) and "gzip is not whole" in str(err) and seen["asked"] == mesh.ATTEMPTS,
              f"a gzip cut short is a network failure, and asked again: {err!r}")
        # [SPEC-NSH-096]: a request goes gzipped only when asked to, for a node that reads one.
        mode["v"], seen["asked"] = "good", 0
        part = {"part": {"text": json.dumps([{"name": "artists", "rows": [["mb%04d" % i, "Artist %d" % i]
                                                                           for i in range(2000)]}])}}
        plain = mesh.sync_step(mdir, where, "stage", "20260928T1830Z", extra=part)
        plain_bytes, plain_gz = seen["sent_bytes"], seen["sent_gz"]
        zipped = mesh.sync_step(mdir, where, "stage", "20260928T1830Z", extra=part, gz=True)
        check(plain == zipped == {"ok": 1} and not plain_gz and seen["sent_gz"],
              "a request goes gzipped with gz, and as it is without")
        check(seen["sent_bytes"] < plain_bytes / 4,
              f"and an escaped part is a fraction of itself gzipped: {seen['sent_bytes']} bytes against {plain_bytes}")
    finally:
        mesh.say, mesh.time.sleep = real
        mode["v"] = "good"


def main() -> int:
    tmp = tempfile.mkdtemp()
    test_sync_step(tmp)
    db = os.path.join(tmp, "library.db")
    mdir = mesh.mesh_dir(db)
    r = mesh.init(mdir, "Test mesh", "desktop")
    check(r["version"] == 1 and [m["role"] for m in r["members"]] == ["hub"], "init: a roster of the hub alone")
    try:
        mesh.init(mdir, "Again")
        check(False, "a second init is refused")
    except SystemExit:
        check(True, "a second init is refused, so no member is orphaned")
    hub_pem = mesh.pem_of(mesh.hub_certificate(mdir))
    hub_fp = mesh.fingerprint(mesh.hub_certificate(mdir))

    srv = intake.serve_members(mdir, "127.0.0.1", 0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    phone = Candidate(tmp, "phone")
    st, out, seen = phone.call(port, "POST", "/enrol/start",
                               {"certificate": phone.pem, "name": "Moto G", "role": "phone"})
    check(st == 200 and seen == hub_fp == out["hub_fingerprint"],
          "enrolment starts without a certificate, and the hub's key is the one the connection showed")
    sid, commit = out["session"], out["commit"]
    nonce_c = secrets.token_hex(16)
    st, bad, _ = phone.call(port, "POST", "/enrol/nonce", {"session": sid, "nonce": nonce_c,
                                                          "signature": Candidate(tmp, "other").sign("x")})
    check(st == 400, f"a nonce not signed by the key that asked is refused: {bad}")
    st, out, _ = phone.call(port, "POST", "/enrol/nonce", {
        "session": sid, "nonce": nonce_c, "signature": phone.sign(f"lempi-enrol|{sid}|{commit}|{nonce_c}")})
    nonce_h = out["nonce"]
    check(hashlib.sha256(nonce_h.encode()).hexdigest() == commit, "the hub's nonce matches its commitment")
    phone_code = mesh.code(seen, phone.fp, nonce_h, nonce_c)
    hub_side = [s for s in mesh.sessions(mdir) if s["session"] == sid][0]
    check(hub_side["code"] == phone_code and len(phone_code) == 7,
          f"both sides show the same code: {phone_code}")

    check(refused(lambda: phone.call(port, "GET", "/member/hello", present=True, pin=hub_pem)),
          "before it is accepted, its key is refused at the handshake")
    st, out, _ = phone.call(port, "POST", "/enrol/confirm",
                            {"session": sid, "signature": phone.sign(f"lempi-confirm|{sid}|{phone_code}")})
    check(out["state"] == "comparing" and out["candidate_confirmed"], "the candidate's confirmation alone is not enough")
    mesh.accept(mdir, sid)
    st, out, _ = phone.call(port, "GET", f"/enrol/status/{sid}")
    inner = json.loads(out["roster"]["roster"])
    check(out["state"] == "accepted" and any(m["fingerprint"] == phone.fp and m["role"] == "phone"
                                             for m in inner["members"]),
          "confirmed on both sides, it is in the roster the status returns")
    pub = mesh.serialization.load_pem_public_key(inner["mesh"]["public_key"].encode())
    check(mesh.verify(pub, out["roster"]["roster"].encode(), out["roster"]["signature"]),
          "and the roster verifies against the mesh key")

    st, out, seen = phone.call(port, "GET", "/member/hello", present=True, pin=hub_pem)
    check(st == 200 and out["you"] == phone.fp and out["role"] == "phone" and seen == hub_fp,
          f"a member is let in by its key, and pins the hub by its key: {out}")
    stranger = Candidate(tmp, "stranger")
    check(refused(lambda: stranger.call(port, "GET", "/member/hello", present=True, pin=hub_pem)),
          "a key not in the roster is refused at the handshake")
    st, out, _ = stranger.call(port, "GET", "/member/hello", pin=hub_pem)
    check(st == 403, "no certificate: the members' routes answer 403")
    evil = Candidate(tmp, "evil")
    check(refused(lambda: phone.call(port, "GET", "/member/hello", present=True, pin=evil.pem)),
          "a member that pins the hub refuses any other server key")

    # Rejected, and removed.
    kid = Candidate(tmp, "kid")
    st, o, _ = kid.call(port, "POST", "/enrol/start", {"certificate": kid.pem, "name": "", "role": "player"})
    mesh.reject(mdir, o["session"])
    st, o2, _ = kid.call(port, "GET", f"/enrol/status/{o['session']}")
    check(o2["state"] == "rejected" and "roster" not in o2, "a rejected enrolment gets no roster")
    key_before = mesh.roster(mdir)["discovery_key"]
    mesh.remove(mdir, phone.fp[:12])
    check(mesh.roster(mdir)["discovery_key"] != key_before,
          "a removal changes the discovery key: the one removed holds the old")
    check(refused(lambda: phone.call(port, "GET", "/member/hello", present=True, pin=hub_pem)),
          "a member removed is refused from its next connection")
    check(mesh.roster(mdir)["version"] == 3, "each change is a new roster version")

    # [SPEC-MTR-105]
    ms = [{"name": "Kitchen", "fingerprint": "aa" * 32}, {"name": "", "fingerprint": "bb" * 32},
          {"name": "Pi", "fingerprint": "cc" * 32}, {"name": "Pi", "fingerprint": "dd" * 32}]
    check(mesh.display_names(ms) == ["Kitchen", "bbbb bbbb", "Pi (cccc cccc)", "Pi (dddd dddd)"],
          f"names: empty shows the fingerprint, twins show theirs: {mesh.display_names(ms)}")
    check(mesh.display_names(ms, True)[0] == "Kitchen (aaaa aaaa)", "and every fingerprint when asked")

    # A roster altered after signing is refused.
    signed = mesh.signed_roster(mdir)
    inner = json.loads(signed["roster"])
    inner["members"].append({"fingerprint": "ee" * 32, "role": "phone", "name": "intruder"})
    signed["roster"] = json.dumps(inner, sort_keys=True)
    with open(os.path.join(mdir, "roster.json"), "w", encoding="utf-8") as fh:
        json.dump(signed, fh)
    try:
        mesh.roster(mdir)
        check(False, "an altered roster is refused")
    except SystemExit:
        check(True, "an altered roster is refused")
    srv.shutdown()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("mesh: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
