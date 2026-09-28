#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The hub's side of mesh membership [SPEC049].

The hub is its mesh's authority [SPEC-MTR-010]: it holds the mesh key, signs
the roster of members, and enrols a device only when a person has confirmed
the same six-digit code on both sides [SPEC-MTR-130]. Everything lives in
`mesh/` beside the library -- never in the catalogue, and never in git:

    node.key, node.pem    the hub's own identity, which its TLS server presents
    mesh.key              the key that signs the roster: the mesh *is* this key
    roster.json           the signed roster, the one every member holds
    enrol/<id>.json       enrolments in progress

Keys are ECDSA P-256, not the Ed25519 SPEC049 first named: a phone keeps its
key in Android's Keystore, where it cannot be copied out, and the Keystore on
Android 11 -- the floor, and the Moto G -- has no Ed25519. P-256 is also what a
Keystore key can present as a TLS client certificate [SPEC-MTR-100].

    python tools/mesh.py LIBRARY init --mesh "Mango's mesh" [--name desktop]
    python tools/mesh.py LIBRARY status
    python tools/mesh.py LIBRARY accept SESSION | reject SESSION
    python tools/mesh.py LIBRARY invite ADDRESS [--port WEB_PORT]   a player, which has no HTTPS client
    python tools/mesh.py LIBRARY remove FINGERPRINT
    python tools/mesh.py LIBRARY push                     the current roster to every player
    python tools/mesh.py LIBRARY rename --mesh NAME  |  rename FINGERPRINT --name NAME

The network side -- enrolment and the members' channel -- is served by
`tools/intake.py` on its members' port [SPEC-MTR-200].
"""
from __future__ import annotations

import base64
import contextlib
import datetime as dt
import hashlib
import json
import os
import secrets
import sys
import time

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

# The player's web port comes from the one place `[GDE-CLI-100]`, not a second
# literal here -- `tools/cli/tests.rs` refuses a re-written default.
from lempi_control import LEMPI_DEFAULT_PORT

ROLES = ("hub", "leaf-vipunen", "player", "phone")
MEMBER_PORT = 5732
# An enrolment not finished within this is dropped: the person has walked away.
ENROL_TTL_S = 15 * 60


def say(text) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(str(text).encode(enc, "replace").decode(enc), flush=True)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mesh_dir(db: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db)), "mesh")


# ------------------------------------------------------------ identities --

def fingerprint(cert_or_pub) -> str:
    """A key's fingerprint [SPEC-MTR-100]: SHA-256 of its public key's DER
    (SubjectPublicKeyInfo), in lower-case hex. The same bytes Android gives as
    `certificate.publicKey.encoded`, so both ends compute the same thing."""
    pub = cert_or_pub.public_key() if isinstance(cert_or_pub, x509.Certificate) else cert_or_pub
    spki = pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(spki).hexdigest()


def show_fp(fp: str, short: bool = False) -> str:
    """Eight groups of four hex digits; the first two as the short form."""
    groups = [fp[i:i + 4] for i in range(0, 32, 4)]
    return " ".join(groups[:2] if short else groups)


def cert_from_pem(pem: str) -> x509.Certificate:
    return x509.load_pem_x509_certificate(pem.encode() if isinstance(pem, str) else pem)


def pem_of(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def make_identity(key_path: str, cert_path: str | None, cn: str):
    """A new P-256 key, and a self-signed certificate for it if asked. The
    certificate carries nothing but the key: a member is trusted because its
    key is in the roster, never because of what a certificate says."""
    key = ec.generate_private_key(ec.SECP256R1())
    write_private(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
    if cert_path:
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
        t = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(t - dt.timedelta(days=1)).not_valid_after(t + dt.timedelta(days=36500))
                .sign(key, hashes.SHA256()))
        with open(cert_path, "w", encoding="ascii") as fh:
            fh.write(pem_of(cert))
    return key


def write_private(path: str, data: bytes) -> None:
    """Written readable by this user only, where the host allows it."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def load_key(path: str):
    with open(path, "rb") as fh:
        return serialization.load_pem_private_key(fh.read(), password=None)


def sign(key, data: bytes) -> str:
    return base64.b64encode(key.sign(data, ec.ECDSA(hashes.SHA256()))).decode()


def verify(pub, data: bytes, sig_b64: str) -> bool:
    try:
        pub.verify(base64.b64decode(sig_b64), data, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False


# -------------------------------------------------------------- the roster --

def atomic_write(path: str, text: str) -> None:
    with open(path + ".part", "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(path + ".part", path)


@contextlib.contextmanager
def locked(mdir: str):
    """One writer at a time: the intake's server and the console's jobs are
    two processes over the same files."""
    path = os.path.join(mdir, ".lock")
    for _ in range(200):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.time() - os.path.getmtime(path) > 30:
                os.remove(path)            # left by a process that died holding it
            time.sleep(0.05)
    else:
        raise TimeoutError("mesh files are locked")
    try:
        yield
    finally:
        os.close(fd)
        os.remove(path)


def initialised(mdir: str) -> bool:
    return os.path.isfile(os.path.join(mdir, "roster.json"))


def init(mdir: str, mesh_name: str, hub_name: str = "") -> dict:
    """Make the hub's identity and the mesh key, and the first roster, which
    lists the hub alone. Refuses to run over an existing mesh: a second
    `init` would orphan every member enrolled under the first key."""
    if initialised(mdir):
        raise SystemExit(f"a mesh already exists in {mdir}: nothing done")
    os.makedirs(os.path.join(mdir, "enrol"), exist_ok=True)
    make_identity(os.path.join(mdir, "node.key"), os.path.join(mdir, "node.pem"), "lempi-node")
    mesh_key = make_identity(os.path.join(mdir, "mesh.key"), None, "")
    hub_cert = hub_certificate(mdir)
    roster = {
        "mesh": {"name": mesh_name, "public_key": mesh_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
            "fingerprint": fingerprint(mesh_key.public_key())},
        "version": 0,
        # For discovery [SPEC-MTR-310]: members prove their mesh with it. It
        # travels only inside the roster, and so only to members.
        "discovery_key": base64.b64encode(secrets.token_bytes(32)).decode(),
        "members": [member_entry(hub_cert, "hub", hub_name)],
    }
    return publish(mdir, roster)


def member_entry(cert: x509.Certificate, role: str, name: str) -> dict:
    return {"fingerprint": fingerprint(cert), "certificate": pem_of(cert), "role": role,
            "name": name, "enrolled_at": now()}


def hub_certificate(mdir: str) -> x509.Certificate:
    with open(os.path.join(mdir, "node.pem"), encoding="ascii") as fh:
        return cert_from_pem(fh.read())


def publish(mdir: str, roster: dict) -> dict:
    """Sign the roster as its next version and make it the current one."""
    roster = dict(roster, version=roster["version"] + 1, signed_at=now())
    body = json.dumps(roster, ensure_ascii=False, sort_keys=True)
    signed = {"roster": body, "signature": sign(load_key(os.path.join(mdir, "mesh.key")), body.encode())}
    atomic_write(os.path.join(mdir, "roster.json"), json.dumps(signed, ensure_ascii=False))
    return roster


def signed_roster(mdir: str) -> dict:
    with open(os.path.join(mdir, "roster.json"), encoding="utf-8") as fh:
        return json.load(fh)


def roster(mdir: str) -> dict:
    """The current roster, its signature checked against its own mesh key."""
    s = signed_roster(mdir)
    r = json.loads(s["roster"])
    pub = serialization.load_pem_public_key(r["mesh"]["public_key"].encode())
    if fingerprint(pub) != r["mesh"]["fingerprint"] or not verify(pub, s["roster"].encode(), s["signature"]):
        raise SystemExit("the roster's signature does not verify: refusing to use it")
    return r


def member(mdir: str, fp: str) -> dict | None:
    return next((m for m in roster(mdir)["members"] if m["fingerprint"] == fp), None)


def rename(mdir: str, name: str, fp: str | None = None) -> dict:
    """A new name for the mesh, or for one member [SPEC-MTR-105]: a label, so
    changing it changes no one's identity. A new roster version all the same,
    so every member learns it."""
    with locked(mdir):
        r = roster(mdir)
        if fp is None:
            r["mesh"]["name"] = name
        else:
            hit = [m for m in r["members"] if m["fingerprint"].startswith(fp.replace(" ", ""))]
            if len(hit) != 1:
                raise SystemExit(f"{fp}: {'no' if not hit else len(hit)} member(s) match")
            hit[0]["name"] = name
        return publish(mdir, r)


def remove(mdir: str, fp: str) -> dict:
    """[SPEC-MTR-140]: a member leaves by a new roster without it."""
    with locked(mdir):
        r = roster(mdir)
        gone = [m for m in r["members"] if m["fingerprint"].startswith(fp.replace(" ", ""))]
        if len(gone) != 1:
            raise SystemExit(f"{fp}: {'no' if not gone else len(gone)} member(s) match")
        if gone[0]["role"] == "hub":
            raise SystemExit("the hub cannot remove itself [SPEC-MTR-070]")
        r["members"] = [m for m in r["members"] if m is not gone[0]]
        # A new discovery key: the removed member holds the old one, and with
        # it could still answer the hub's members' query [SPEC-MTR-310].
        r["discovery_key"] = base64.b64encode(secrets.token_bytes(32)).decode()
        return publish(mdir, r)


# -------------------------------------------------------------- enrolment --
#
# [SPEC-MTR-130], Bluetooth's numeric comparison. The hub commits to its nonce
# before it sees the candidate's, so a device in the middle cannot search for
# nonces that make two codes agree; and the code covers both keys as each side
# saw them in its own TLS connection, so a device in the middle, which must
# present keys of its own, shows each side a different code.

def code(hub_fp: str, candidate_fp: str, nonce_h: str, nonce_c: str) -> str:
    d = hashlib.sha256(f"lempi-sas|{hub_fp}|{candidate_fp}|{nonce_h}|{nonce_c}".encode()).digest()
    n = int.from_bytes(d[:8], "big") % 1_000_000
    return f"{n // 1000:03d} {n % 1000:03d}"


def _session_path(mdir: str, sid: str) -> str:
    if not sid or not all(c in "0123456789abcdef" for c in sid) or len(sid) != 32:
        raise ValueError("not a session")
    return os.path.join(mdir, "enrol", sid + ".json")


def _load_session(mdir: str, sid: str) -> dict:
    with open(_session_path(mdir, sid), encoding="utf-8") as fh:
        s = json.load(fh)
    if s["state"] in ("started", "comparing") and time.time() - s["started"] > ENROL_TTL_S:
        s["state"] = "expired"
    return s


def _save_session(mdir: str, s: dict) -> None:
    atomic_write(_session_path(mdir, s["session"]), json.dumps(s, ensure_ascii=False))


def enrol_start(mdir: str, cert_pem: str, name: str, role: str, sender: str) -> dict:
    """A candidate asks to join. Answered with the hub's commitment to its
    nonce; the nonce itself is revealed only after the candidate's."""
    cert = cert_from_pem(cert_pem)
    if not isinstance(cert.public_key(), ec.EllipticCurvePublicKey):
        raise ValueError("an ECDSA P-256 key is expected")
    if role not in ROLES or role == "hub":
        raise ValueError(f"not a role a candidate may ask for: {role}")
    fp = fingerprint(cert)
    r = roster(mdir)
    if any(m["fingerprint"] == fp for m in r["members"]):
        raise ValueError("already a member")
    nonce_h = secrets.token_hex(16)
    s = {"session": secrets.token_hex(16), "state": "started", "started": time.time(),
         "certificate": pem_of(cert), "fingerprint": fp, "name": str(name or "")[:120], "role": role,
         "sender": sender, "nonce_h": nonce_h,
         "commit_h": hashlib.sha256(nonce_h.encode()).hexdigest(),
         "candidate_confirmed": False, "hub_confirmed": False}
    with locked(mdir):
        _save_session(mdir, s)
    return {"session": s["session"], "commit": s["commit_h"], "mesh": r["mesh"]["name"],
            "mesh_fingerprint": r["mesh"]["fingerprint"], "hub_fingerprint": fingerprint(hub_certificate(mdir))}


def enrol_nonce(mdir: str, sid: str, nonce_c: str, signature: str) -> dict:
    """The candidate's nonce, signed with its key -- which proves it holds the
    key it asked to enrol. Answered with the hub's nonce; both sides can now
    show the code."""
    with locked(mdir):
        s = _load_session(mdir, sid)
        if s["state"] != "started":
            raise ValueError(f"enrolment is {s['state']}")
        pub = cert_from_pem(s["certificate"]).public_key()
        if not verify(pub, f"lempi-enrol|{sid}|{s['commit_h']}|{nonce_c}".encode(), signature):
            raise ValueError("the signature does not verify: not the key that asked")
        s["nonce_c"] = str(nonce_c)
        s["code"] = code(fingerprint(hub_certificate(mdir)), s["fingerprint"], s["nonce_h"], s["nonce_c"])
        s["state"] = "comparing"
        _save_session(mdir, s)
    return {"nonce": s["nonce_h"]}


def enrol_confirm(mdir: str, sid: str, signature: str) -> dict:
    """The candidate's person says the codes match, signed by its key."""
    with locked(mdir):
        s = _load_session(mdir, sid)
        if s["state"] != "comparing":
            raise ValueError(f"enrolment is {s['state']}")
        pub = cert_from_pem(s["certificate"]).public_key()
        if not verify(pub, f"lempi-confirm|{sid}|{s['code']}".encode(), signature):
            raise ValueError("the signature does not verify")
        s["candidate_confirmed"] = True
        _complete(mdir, s)
        _save_session(mdir, s)
    return status(mdir, sid)


def accept(mdir: str, sid: str) -> dict:
    """The hub's person says the codes match."""
    with locked(mdir):
        s = _load_session(mdir, sid)
        if s["state"] != "comparing":
            raise SystemExit(f"enrolment {sid[:8]} is {s['state']}: nothing to accept")
        s["hub_confirmed"] = True
        _complete(mdir, s)
        _save_session(mdir, s)
    return s


def reject(mdir: str, sid: str) -> dict:
    with locked(mdir):
        s = _load_session(mdir, sid)
        if s["state"] in ("accepted",):
            raise SystemExit("already accepted: remove the member instead")
        s["state"] = "rejected"
        _save_session(mdir, s)
    return s


def _complete(mdir: str, s: dict) -> None:
    """Both people have confirmed: the candidate joins the roster."""
    if not (s["candidate_confirmed"] and s["hub_confirmed"]):
        return
    r = roster(mdir)
    if not any(m["fingerprint"] == s["fingerprint"] for m in r["members"]):
        r["members"].append(member_entry(cert_from_pem(s["certificate"]), s["role"], s["name"]))
        publish(mdir, r)
    s["state"] = "accepted"
    s["accepted_at"] = now()


def _http(method: str, url: str, body: dict | None = None, timeout: float = 10.0) -> dict:
    import urllib.error
    import urllib.request
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            raise ValueError(json.loads(e.read()).get("error") or f"HTTP {e.code}") from e
        except json.JSONDecodeError:
            raise ValueError(f"HTTP {e.code}") from e


def invite(mdir: str, address: str, web_port: int = LEMPI_DEFAULT_PORT, wait_s: int = ENROL_TTL_S) -> dict:
    """[SPEC-MTR-130] for a player, which has no HTTPS client: the hub invites
    it over its web port, and a person compares the code in the console with
    the one in the player's Settings. The invitation carries the mesh key
    signed by the hub's own key, which the code covers; the player's answer is
    signed by the key it names. Waits for both people, then sends the roster."""
    import socket
    r = roster(mdir)
    hub_cert = hub_certificate(mdir)
    hub_fp = fingerprint(hub_cert)
    base = f"http://{address}:{web_port}"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:     # the address the player reaches us by
        s.connect((address, web_port))
        here = s.getsockname()[0]
    nonce_h = secrets.token_hex(16)
    commit = hashlib.sha256(nonce_h.encode()).hexdigest()
    ans = _http("POST", f"{base}/mesh/invite", {
        "mesh": r["mesh"]["name"], "mesh_public_key": r["mesh"]["public_key"],
        "hub_certificate": pem_of(hub_cert), "commit": commit, "hub_address": here,
        "signature": sign(load_key(os.path.join(mdir, "node.key")), f"lempi-invite|{r['mesh']['fingerprint']}|{commit}".encode()),
    })
    cert = cert_from_pem(ans["certificate"])
    if not verify(cert.public_key(), f"lempi-enrol|{ans['invite']}|{commit}|{ans['nonce']}".encode(), ans["signature"]):
        raise SystemExit("the player's answer is not signed by the key it names: nothing done")
    fp = fingerprint(cert)
    if any(m["fingerprint"] == fp for m in r["members"]):
        raise SystemExit(f"{address} is already a member")
    s = {"session": secrets.token_hex(16), "state": "comparing", "started": time.time(), "certificate": pem_of(cert),
         "fingerprint": fp, "name": str(ans.get("name") or "")[:120], "role": "player", "sender": address,
         "nonce_h": nonce_h, "commit_h": commit, "nonce_c": ans["nonce"], "code": code(hub_fp, fp, nonce_h, ans["nonce"]),
         "candidate_confirmed": False, "hub_confirmed": False, "invite": ans["invite"]}
    with locked(mdir):
        _save_session(mdir, s)
    _http("POST", f"{base}/mesh/invite/{ans['invite']}/reveal", {"nonce": nonce_h})
    say(f"invited {s['name'] or address} ({show_fp(fp, True)}): code {s['code']}")
    say("accept it on this mesh page, and confirm it in the player's own Settings")
    until = time.time() + wait_s
    while time.time() < until:
        time.sleep(2)
        s = _load_session(mdir, s["session"])
        if s["state"] == "accepted":
            got = _http("POST", f"{base}/mesh/invite/{ans['invite']}/roster", signed_roster(mdir))
            say(f"{s['name'] or address} joined; it holds roster version {got.get('version')}")
            return s
        if s["state"] in ("rejected", "expired"):
            try:
                _http("POST", f"{base}/mesh/invite/{ans['invite']}/reject")
            except (ValueError, OSError):
                pass
            raise SystemExit(f"the invitation was {s['state']} here")
        inv = (_http("GET", f"{base}/mesh").get("invite") or {})
        if inv.get("id") != ans["invite"] or inv.get("state") == "rejected":
            with locked(mdir):
                s = _load_session(mdir, s["session"])
                s["state"] = "rejected"
                _save_session(mdir, s)
            raise SystemExit("the player rejected it, or was invited by someone else meanwhile")
        if inv.get("state") == "confirmed" and not s["candidate_confirmed"]:
            if not verify(cert.public_key(), f"lempi-confirm|{ans['invite']}|{s['code']}".encode(),
                          inv.get("confirmation") or ""):
                raise SystemExit("the player's confirmation is not signed by its key: nothing done")
            with locked(mdir):
                s = _load_session(mdir, s["session"])
                s["candidate_confirmed"] = True
                _complete(mdir, s)
                _save_session(mdir, s)
            say("confirmed on the player" + ("" if s["state"] == "accepted" else "; waiting for the hub's accept"))
    raise SystemExit("no answer in time: the invitation lapses")


def push_roster(mdir: str, key: bytes | None = None) -> dict:
    """The current roster to every player that answers the members' query,
    found with `key` -- the discovery key they hold, which after a removal is
    the previous one [SPEC-MTR-120]. A player keeps it only if newer and
    signed by its pinned mesh key; one it no longer names leaves."""
    import discovery
    r = roster(mdir)
    key = key or base64.b64decode(r["discovery_key"])
    out = {}
    for a in discovery.query("members", mesh_fp=r["mesh"]["fingerprint"], key=key):
        try:
            got = _http("POST", f"http://{a['address']}:{a.get('web_port', 5720)}/mesh/roster", signed_roster(mdir))
            out[a["name"] or a["fingerprint"][:8]] = got.get("state")
        except (ValueError, OSError) as e:
            out[a["name"] or a["fingerprint"][:8]] = f"not taken: {e}"
    return out


def status(mdir: str, sid: str) -> dict:
    """What a candidate polls for; the signed roster once it is a member."""
    s = _load_session(mdir, sid)
    out = {"state": s["state"], "hub_confirmed": s["hub_confirmed"],
           "candidate_confirmed": s["candidate_confirmed"]}
    if s["state"] == "accepted":
        out["roster"] = signed_roster(mdir)
    return out


def sessions(mdir: str) -> list[dict]:
    """Enrolments worth showing a person: in progress, or ended in the last hour."""
    d = os.path.join(mdir, "enrol")
    out = []
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not n.endswith(".json"):
            continue
        s = _load_session(mdir, n[:-5])
        if s["state"] in ("started", "comparing") or time.time() - s["started"] < 3600:
            out.append({k: s.get(k) for k in ("session", "state", "started", "fingerprint", "name", "role",
                                              "sender", "code", "candidate_confirmed", "hub_confirmed")})
    return out


# ------------------------------------------------ a member's listening --
#
# [SPEC-MTR-030], [REQ-AND-330]. A phone uploads the household tables it
# holds -- preferences, occasion values, flags; never its plays -- and the next
# star sync merges it as a node. The patch that sync prepares for it waits in
# its outbox until the phone fetches and applies it.

SHARED = ("listener_preferences", "listener_characteristics", "listener_flags")
# Last write wins compares the phone's own `updated_at` with every other
# node's [SPEC-PREF-105]; a phone's clock is set by its network, not by the
# fleet's chrony. Measured at each upload, and refused past this, so a wrong
# clock is a visible refusal rather than an edit that silently wins or loses
# [SPEC-MTR-920].
CLOCK_TOLERANCE_MS = 120_000
KEEP_UPLOADS = 10


def member_dir(mdir: str, fp: str) -> str:
    if not fp or not all(c in "0123456789abcdef" for c in fp):
        raise ValueError("not a fingerprint")
    return os.path.join(mdir, "members", fp)


def store_upload(mdir: str, fp: str, data: bytes, clock_ms: int | None) -> dict:
    """Keep a member's upload, once it proves to be what it should: an intact
    SQLite database holding shared tables only, from a clock the hub agrees
    with. The latest is `listener.db`; earlier ones are kept under
    `uploads/` as the evidence of what the member has removed since
    [SPEC-STAR-050]."""
    import sqlite3
    offset = None if clock_ms is None else clock_ms - int(time.time() * 1000)
    if offset is None or abs(offset) > CLOCK_TOLERANCE_MS:
        raise ValueError("this device's clock is " + ("not given" if offset is None else
                         f"{offset / 1000:+.0f} s from the hub's") + ": its edits could not be ordered "
                         "against the household's. Set its time automatically, then sync again.")
    d = member_dir(mdir, fp)
    os.makedirs(os.path.join(d, "uploads"), exist_ok=True)
    part = os.path.join(d, "listener.db.part")
    with open(part, "wb") as fh:
        fh.write(data)
    try:
        c = sqlite3.connect(f"file:{part}?mode=ro", uri=True)
        try:
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("the upload is not an intact database")
            have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if have - set(SHARED):
                raise ValueError(f"the upload holds tables that are not shared: {sorted(have - set(SHARED))}")
            rows = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in sorted(have)}
        finally:
            c.close()
    except (sqlite3.Error, ValueError) as e:
        os.remove(part)
        raise ValueError(str(e)) from e
    epoch = int(time.time())
    os.replace(part, os.path.join(d, "listener.db"))
    with open(os.path.join(d, "listener.db"), "rb") as src, \
            open(os.path.join(d, "uploads", f"listener-{epoch}.db"), "wb") as dst:
        dst.write(src.read())
    for old in sorted(os.listdir(os.path.join(d, "uploads")))[:-KEEP_UPLOADS]:
        os.remove(os.path.join(d, "uploads", old))
    meta = {"uploaded_at": now(), "epoch": epoch, "clock_offset_ms": offset, "rows": rows,
            "sha256": hashlib.sha256(data).hexdigest()}
    atomic_write(os.path.join(d, "meta.json"), json.dumps(meta))
    return meta


def upload_of(mdir: str, fp: str) -> dict | None:
    """A member's latest upload: its meta, with `path` and `uploads`."""
    d = member_dir(mdir, fp)
    if not os.path.isfile(os.path.join(d, "meta.json")):
        return None
    with open(os.path.join(d, "meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    return dict(meta, path=os.path.join(d, "listener.db"), uploads=os.path.join(d, "uploads"))


def put_outbox(mdir: str, fp: str, run: str, patch: dict) -> None:
    atomic_write(os.path.join(member_dir(mdir, fp), "outbox.json"),
                 json.dumps({"run": run, "patch": patch}, ensure_ascii=False))


def outbox(mdir: str, fp: str) -> dict | None:
    p = os.path.join(member_dir(mdir, fp), "outbox.json")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def outbox_applied(mdir: str, fp: str, run: str, counts: dict) -> bool:
    """The member has applied the patch of `run`: it leaves the outbox, and
    what the member did with it is kept."""
    o = outbox(mdir, fp)
    if o is None or o["run"] != run:
        return False
    d = member_dir(mdir, fp)
    os.makedirs(os.path.join(d, "applied"), exist_ok=True)
    atomic_write(os.path.join(d, "applied", f"{run}.json"),
                 json.dumps({"run": run, "applied_at": now(), "counts": counts}))
    os.remove(os.path.join(d, "outbox.json"))
    return True


# ------------------------------------------------- the signed star sync --
#
# [SPEC-NSH-020..030]. The hub reaches a member player the way it sends
# rosters: over its web port, each request signed by the mesh key, each
# answer signed by the player's own key and checked against its certificate
# in the roster. The player's half is `player/src/star_node.rs`.

SYNC_STEPS = ("snapshot", "rehearse", "commit")


def sync_players(mdir: str) -> dict:
    """Every player that answers the members' query, by fingerprint: where to
    reach it. The roster says who belongs; the query says where they are."""
    import discovery
    r = roster(mdir)
    players = {m["fingerprint"] for m in r["members"] if m["role"] == "player"}
    found = discovery.query("members", mesh_fp=r["mesh"]["fingerprint"], key=base64.b64decode(r["discovery_key"]))
    return {a["fingerprint"]: a for a in found if a["fingerprint"] in players}


def sync_step(mdir: str, player: dict, op: str, run: str, catalogue_patch: dict | None = None,
              listener_patch: dict | None = None, timeout: float = 600.0) -> dict:
    """One step on one player; its result, once the answer is proven to be the
    player's, and to this request."""
    if op not in SYNC_STEPS:
        raise ValueError(f"not a step: {op}")
    r = roster(mdir)
    fp = player["fingerprint"]
    m = next((m for m in r["members"] if m["fingerprint"] == fp and m["role"] == "player"), None)
    if m is None:
        raise ValueError(f"{fp[:8]} is not a player in this mesh")
    req = {"op": op, "run": run, "node": fp, "mesh": r["mesh"]["fingerprint"], "nonce": os.urandom(16).hex(),
           "at_ms": int(time.time() * 1000), "catalogue_patch": catalogue_patch, "listener_patch": listener_patch}
    text = json.dumps(req, ensure_ascii=False, separators=(",", ":"))
    got = _http("POST", f"http://{player['address']}:{player.get('web_port', LEMPI_DEFAULT_PORT)}/mesh/sync/{op}",
                {"request": text, "signature": sign(load_key(os.path.join(mdir, "mesh.key")), text.encode())},
                timeout=timeout)
    pub = cert_from_pem(m["certificate"]).public_key()
    if not verify(pub, str(got.get("answer", "")).encode(), str(got.get("signature", ""))):
        raise ValueError(f"the answer from {player['address']} is not signed by {fp[:8]}")
    a = json.loads(got["answer"])
    if (a.get("op"), a.get("run"), a.get("nonce"), a.get("node")) != (op, run, req["nonce"], fp):
        raise ValueError("the answer is not to this request")
    return a["result"]


def sync_members(mdir: str) -> list[dict]:
    """The members star sync takes as nodes by upload rather than ssh: the
    phones, each named for its report `phone-<short fingerprint>`."""
    if not initialised(mdir):
        return []
    return [dict(m, node=f"phone-{m['fingerprint'][:8]}") for m in roster(mdir)["members"] if m["role"] == "phone"]


# --------------------------------------------------------------- display --

def display_names(members: list[dict], show_all: bool = False) -> list[str]:
    """[SPEC-MTR-105]: a member by its name; its short fingerprint where the
    name is empty, after a name another member in the list shares, or after
    every name when a person asks to see them."""
    names = [(m.get("name") or "").strip() for m in members]
    out = []
    for m, n in zip(members, names):
        short = show_fp(m["fingerprint"], short=True)
        if not n:
            out.append(short)
        elif show_all or names.count(n) > 1:
            out.append(f"{n} ({short})")
        else:
            out.append(n)
    return out


# ------------------------------------------------------------------- CLI --

def main(argv: list[str]) -> int:
    if len(argv) < 2:
        say(__doc__)
        return 2
    db, cmd, rest = argv[0], argv[1], argv[2:]
    mdir = mesh_dir(db)

    def opt(name, default=""):
        return rest[rest.index(name) + 1] if name in rest and rest.index(name) + 1 < len(rest) else default

    if cmd == "init":
        if not opt("--mesh"):
            say("init needs --mesh NAME")
            return 2
        r = init(mdir, opt("--mesh"), opt("--name"))
        say(f"mesh '{r['mesh']['name']}' {show_fp(r['mesh']['fingerprint'])}, in {mdir}")
        say(f"hub {show_fp(r['members'][0]['fingerprint'])}; roster version {r['version']}")
        say("the mesh key is in mesh.key: back it up -- losing it means enrolling every member again")
        return 0
    if not initialised(mdir):
        say(f"no mesh in {mdir}: run `mesh.py {db} init --mesh NAME` first")
        return 1
    if cmd == "status":
        r = roster(mdir)
        say(f"mesh '{r['mesh']['name']}' {show_fp(r['mesh']['fingerprint'])}, roster version {r['version']}")
        for m, shown in zip(r["members"], display_names(r["members"], "--fingerprints" in rest)):
            say(f"  {m['role']:12} {shown}   enrolled {m['enrolled_at']}")
        for s in sessions(mdir):
            say(f"  enrolment {s['session'][:8]} {s['state']}: {s['name'] or show_fp(s['fingerprint'], True)} "
                f"code {s.get('code') or '-'}, confirmed by candidate {s['candidate_confirmed']}, "
                f"by hub {s['hub_confirmed']}")
        return 0
    if cmd in ("accept", "reject"):
        if not rest:
            say(f"{cmd} takes a session")
            return 2
        match = [s["session"] for s in sessions(mdir) if s["session"].startswith(rest[0])]
        if len(match) != 1:
            say(f"{rest[0]}: {'no' if not match else len(match)} enrolment(s) match")
            return 1
        s = accept(mdir, match[0]) if cmd == "accept" else reject(mdir, match[0])
        say(f"enrolment {s['session'][:8]}: {s['state']}"
            + (" -- waiting for the candidate to confirm too" if s["state"] == "comparing" else ""))
        return 0
    if cmd == "invite":
        if not rest:
            say("invite takes ADDRESS [--port WEB_PORT]")
            return 2
        invite(mdir, rest[0], int(opt("--port", "5720")))
        return 0
    if cmd == "rename":
        if not opt("--name") and not opt("--mesh"):
            say("rename takes --mesh NAME, or FINGERPRINT --name NAME")
            return 2
        r = rename(mdir, opt("--mesh"), None) if opt("--mesh") else rename(mdir, opt("--name"), rest[0])
        say(f"renamed; roster version {r['version']}")
        return 0
    if cmd == "remove":
        if not rest:
            say("remove takes a fingerprint")
            return 2
        before = base64.b64decode(roster(mdir)["discovery_key"])
        r = remove(mdir, rest[0])
        say(f"removed; roster version {r['version']}")
        say(f"sent to the players: {push_roster(mdir, before)}")
        return 0
    if cmd == "push":
        say(f"roster version {roster(mdir)['version']} sent to the players: {push_roster(mdir)}")
        return 0
    say(f"unknown command {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
