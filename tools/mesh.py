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
    python tools/mesh.py LIBRARY remove FINGERPRINT

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
    if cmd == "remove":
        if not rest:
            say("remove takes a fingerprint")
            return 2
        r = remove(mdir, rest[0])
        say(f"removed; roster version {r['version']}")
        return 0
    say(f"unknown command {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
