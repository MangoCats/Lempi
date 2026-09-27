#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What waits in pending-identification/, and a person's decision on each
[REQ-AND-289], [SPEC048].

The intake keeps what a Lempi node sends [REQ-AND-280]; nothing is inducted
until a person has decided. This is the deciding, as a command a person can
run and the console runs as a job [SPEC-SUI-015]:

    python tools/pending.py LIBRARY list [--json]
    python tools/pending.py LIBRARY identify [SHA ...]      default: every undecided file
    python tools/pending.py LIBRARY induct SHA --root MUSIC
    python tools/pending.py LIBRARY reject SHA
    python tools/pending.py LIBRARY repair SHA

**Three outcomes** [SPEC-PID-030]. *Induct*: the file goes to
`MUSIC/<artist>/<album>/` by its own tags, and the usual induction follows.
*Reject*: it is set aside, and the intake declines it if it is offered again.
*Repair*: it is a damaged copy of a file the library holds -- same name, same
size, a few blocks of other data -- and the sender is offered the good copy
back, through the intake [SPEC-PID-040]. Found 2026-09-27: all four files the
Moto G first sent were such copies, one run of 20 to 52 KB each, on 4 KiB
boundaries, on its SD card.

A SHA may be given as any unique prefix. The library is only read; the one
place this writes is the pending folder, and for an induction the music folder.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

AGENT = "Lempi/0.1 (+https://github.com/MangoCats/Lempi)"
MB = "https://musicbrainz.org/ws/2/recording"
BLOCK = 4096
# A copy is damaged, not different, when at most this share of its blocks
# differ from the library's file of the same name and size.
DAMAGED_SHARE = 0.05
NEAR_MS = 3000
DONE = ("inducted", "rejected", "repaired")


def say(text) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(str(text).encode(enc, "replace").decode(enc), flush=True)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm(s) -> str:
    return " ".join(str(s or "").lower().replace("’", "'").split())


def pending_dir(db: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db)), "pending-identification")


def open_ro(db: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{os.path.abspath(db)}?mode=ro", uri=True)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------- the folder --

def entries(pdir: str) -> list[dict]:
    """Every file waiting in `pdir` -- not those already decided and moved on
    -- as its note, with `sha` and `audio` added."""
    out = []
    if not os.path.isdir(pdir):
        return out
    for name in sorted(os.listdir(pdir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(pdir, name), encoding="utf-8") as fh:
            note = json.load(fh)
        note["sha"] = name[:-5]
        note["audio"] = os.path.join(pdir, note.get("file") or "")
        out.append(note)
    return out


def find(pdir: str, prefix: str) -> dict:
    hits = [e for e in entries(pdir) if e["sha"].startswith(prefix.lower())]
    if len(hits) != 1:
        raise SystemExit(f"{prefix}: {'no' if not hits else len(hits)} waiting file(s) match")
    return hits[0]


def save(pdir: str, note: dict) -> None:
    body = {k: v for k, v in note.items() if k not in ("sha", "audio")}
    path = os.path.join(pdir, note["sha"] + ".json")
    with open(path + ".part", "w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=2, ensure_ascii=False)
    os.replace(path + ".part", path)


def move_on(pdir: str, note: dict, where: str) -> None:
    """A decided file and its note, out of the waiting list into `where`/."""
    dest = os.path.join(pdir, where)
    os.makedirs(dest, exist_ok=True)
    save(pdir, note)
    if os.path.exists(note["audio"]):
        os.replace(note["audio"], os.path.join(dest, os.path.basename(note["audio"])))
    os.replace(os.path.join(pdir, note["sha"] + ".json"), os.path.join(dest, note["sha"] + ".json"))


def decided(pdir: str, sha: str) -> str | None:
    """Which way a file was decided, if it was and has moved on."""
    for where in DONE:
        if os.path.exists(os.path.join(pdir, where, sha + ".json")):
            return where
    return None


# ------------------------------------------------------ a damaged copy ------

def damaged_copy(conn: sqlite3.Connection, note: dict) -> dict | None:
    """The library's file this is a damaged copy of, if it is one
    [SPEC-PID-040]: the same file name and the same size, at most
    DAMAGED_SHARE of its 4 KiB blocks different, and the library's own copy
    still exactly the bytes the catalogue recorded."""
    audio = note["audio"]
    if not os.path.isfile(audio):
        return None
    size = os.path.getsize(audio)
    name = (note.get("offered", {}).get("name") or "").lower()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(files)")}
    if "sha256" not in cols or not name:
        return None
    for path, sig, sha in conn.execute(
            "SELECT path, audio_md5, sha256 FROM files WHERE size_bytes = ?", (size,)):
        if re.split(r"[\\/]", path)[-1].lower() != name or not os.path.isfile(path):
            continue
        if sig == note.get("signature"):
            continue            # the same audio: held, not damaged
        differ, first, last, blocks = 0, None, None, 0
        with open(audio, "rb") as a, open(path, "rb") as b:
            while True:
                x, y = a.read(BLOCK), b.read(BLOCK)
                if not x and not y:
                    break
                if x != y:
                    differ += 1
                    first = blocks * BLOCK if first is None else first
                    last = blocks * BLOCK + max(len(x), len(y)) - 1
                blocks += 1
        if not differ or differ > DAMAGED_SHARE * blocks:
            continue
        if sha and sha256_file(path) != sha:
            continue            # the library's copy is no longer what it was
        return {"path": path, "sha256": sha, "audio_md5": sig, "size": size,
                "blocks": blocks, "blocks_differ": differ, "first_byte": first, "last_byte": last}
    return None


# -------------------------------------------------------- identification --

def duration_ms(path: str) -> int:
    """Measured, not taken from the sender: the Moto G offered "Think" as
    160.5 s, and AcoustID, which weighs the length it is given, found nothing
    until it was told 136."""
    try:
        import mutagen
        f = mutagen.File(path)
        return int(f.info.length * 1000) if f and f.info else 0
    except Exception:
        return 0


def acoustid(path: str, ms: int, key: str) -> list[dict]:
    import fingerprint_ids as fi
    fp = fi.fingerprint(path, 0, ms)
    if not fp:
        return []
    fields = {"client": key, "meta": "recordings releasegroups compress", "fingerprint": fp,
              "duration": str(max(1, ms // 1000))}
    req = urllib.request.Request(fi.ENDPOINT, data=gzip.compress(urllib.parse.urlencode(fields).encode()),
                                 headers={"User-Agent": AGENT, "Content-Encoding": "gzip",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.load(r)
    out = []
    for res in body.get("results", []):
        for rec in res.get("recordings", []) or []:
            out.append({
                "score": round(res.get("score", 0), 2), "recording": rec.get("id"), "title": rec.get("title"),
                "artist": "".join(a.get("name", "") + a.get("joinphrase", "") for a in rec.get("artists", [])),
                "length_ms": int(rec["duration"] * 1000) if rec.get("duration") else None,
                "releases": [g.get("title") for g in rec.get("releasegroups", []) or []][:4]})
    return out[:8]


def musicbrainz(tags: dict, ms: int) -> list[dict]:
    """A search by the file's own tags, with the album first and then without,
    ranked by agreeing title and artist, then length within 3 s."""
    title, artist, album = tags.get("title"), tags.get("artist"), tags.get("album")
    if not (title and artist):
        return []
    q = lambda s: '"' + str(s).replace('"', "") + '"'  # noqa: E731
    found = []
    for alb in ([album, None] if album else [None]):
        query = f"recording:{q(title)} AND artist:{q(artist)}" + (f" AND release:{q(alb)}" if alb else "")
        req = urllib.request.Request(f"{MB}?fmt=json&limit=15&query=" + urllib.parse.quote(query),
                                     headers={"User-Agent": AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            found = json.load(r).get("recordings", [])
        time.sleep(1.1)       # MusicBrainz asks for one request a second
        if found:
            break
    rows = []
    for rec in found:
        who = "".join(a.get("name", "") + a.get("joinphrase", "") for a in rec.get("artist-credit", []))
        length = rec.get("length")
        rows.append({
            "recording": rec["id"], "title": rec.get("title"), "artist": who, "length_ms": length,
            "tags_agree": norm(rec.get("title")) == norm(title) and (norm(artist) in norm(who) or norm(who) in norm(artist)),
            "length_agrees": bool(length and ms and abs(length - ms) <= NEAR_MS),
            "releases": [r.get("title") for r in rec.get("releases", [])][:4]})
    rows.sort(key=lambda r: (not r["tags_agree"], not r["length_agrees"]))
    return rows[:6]


def held(conn: sqlite3.Connection, mbid: str) -> list[str]:
    """Where the library holds this recording, if it does."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT f.path FROM passage_recordings pr JOIN passages p ON p.passage_id = pr.passage_id "
        "JOIN files f ON f.file_id = p.file_id WHERE pr.mbid = ? LIMIT 5", (mbid,))]


def identify(conn: sqlite3.Connection, note: dict, key: str | None) -> dict:
    """What can be established about one file [REQ-AND-289]: AcoustID by its
    sound, MusicBrainz by its tags, whether the library already holds what
    either names, and whether it is a damaged copy of a library file."""
    ms = duration_ms(note["audio"])
    out: dict = {"at": now(), "duration_ms": ms, "acoustid": [], "musicbrainz": []}
    if key:
        try:
            out["acoustid"] = acoustid(note["audio"], ms, key)
        except Exception as e:
            out["acoustid_error"] = f"{type(e).__name__}: {e}"
    else:
        out["acoustid_error"] = "no AcoustID key (secrets/acoustid.key)"
    try:
        out["musicbrainz"] = musicbrainz(note.get("offered", {}).get("tags") or {}, ms)
    except Exception as e:
        out["musicbrainz_error"] = f"{type(e).__name__}: {e}"
    # A song has many MusicBrainz recordings, and AcoustID's first is often
    # not the one the library credits (Hendrix's "The Wind Cries Mary": four
    # at 0.96, the library's second). So the first strong candidate the
    # library holds, else the first strong one.
    strong = [a for a in out["acoustid"] if a["score"] >= 0.9] + [
        m for m in out["musicbrainz"] if m["tags_agree"] and m["length_agrees"]]
    for c in strong:
        c["held"] = held(conn, c["recording"])
    best = next((c for c in strong if c["held"]), None) or (strong[0] if strong else None)
    if best:
        out["best"] = {k: best.get(k) for k in ("recording", "title", "artist", "releases", "held")}
        out["best"]["by"] = "acoustid" if "score" in best else "musicbrainz"
    out["damaged_copy"] = damaged_copy(conn, note)
    return out


# -------------------------------------------------------------- deciding --

def safe(part: str) -> str:
    """A folder name from a tag, as Windows and every other host accept it."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(part)).strip().rstrip(". ")
    return s or "_"


def induct(pdir: str, note: dict, root: str) -> dict:
    """Place the file at `root/<artist>/<album>/<its name>` by its own tags,
    checked by its bytes, and move it on. The induction itself is the usual
    one, over that folder, run after."""
    tags = note.get("offered", {}).get("tags") or {}
    if not tags.get("artist"):
        raise SystemExit("no artist tag: tag the file, or reject it")
    folder = os.path.join(root, safe(tags["artist"]), *([safe(tags["album"])] if tags.get("album") else []))
    name = note.get("offered", {}).get("name") or os.path.basename(note["audio"])
    dest = os.path.join(folder, safe(name))
    if os.path.exists(dest):
        if sha256_file(dest) != note["sha"]:
            raise SystemExit(f"{dest} exists and is another file: nothing moved")
    else:
        os.makedirs(folder, exist_ok=True)
        shutil.copyfile(note["audio"], dest + ".part")
        if sha256_file(dest + ".part") != note["sha"]:
            os.remove(dest + ".part")
            raise SystemExit("the copy is not the file: nothing moved")
        os.replace(dest + ".part", dest)
    note["decision"] = {"action": "induct", "at": now(), "path": dest}
    move_on(pdir, note, "inducted")
    return {"folder": folder, "path": dest}


def reject(pdir: str, note: dict) -> None:
    note["decision"] = {"action": "reject", "at": now()}
    move_on(pdir, note, "rejected")


def repair(conn: sqlite3.Connection, pdir: str, note: dict) -> dict:
    """[SPEC-PID-040]: offer the sender the library's good copy. The note
    stays waiting until the sender says it has replaced its copy."""
    good = damaged_copy(conn, note)
    if good is None:
        raise SystemExit("not a damaged copy of any file the library holds: induct or reject it")
    note["decision"] = {"action": "repair", "at": now(), "good_path": good["path"],
                        "good_sha256": good["sha256"], "good_size": good["size"]}
    save(pdir, note)
    return good


def repairs(pdir: str) -> list[dict]:
    """The repairs decided and not yet made, as the intake offers them."""
    return [{"damaged_sha256": e["sha"], "good_sha256": e["decision"]["good_sha256"],
             "size": e["decision"]["good_size"], "name": e.get("offered", {}).get("name")}
            for e in entries(pdir) if (e.get("decision") or {}).get("action") == "repair"]


def repaired(pdir: str, damaged: str, got: str) -> bool:
    """The sender reports its copy replaced, and what its bytes now hash to."""
    for e in entries(pdir):
        d = e.get("decision") or {}
        if e["sha"] == damaged and d.get("action") == "repair":
            if got != d["good_sha256"]:
                return False
            d["done_at"] = now()
            move_on(pdir, e, "repaired")
            return True
    return False


# ------------------------------------------------------------------ CLI ---

def show(e: dict) -> None:
    t = e.get("offered", {}).get("tags") or {}
    d = e.get("decision") or {}
    say(f"{e['sha'][:12]}  {t.get('artist')} - {t.get('title')}  [{t.get('album')}]  "
        f"{e.get('verdict')}{'  -- ' + d['action'] + ' decided' if d else ''}")
    i = e.get("identification") or {}
    if i.get("best"):
        b = i["best"]
        say(f"   by {b['by']}: {b['artist']} - {b['title']} {b['recording']}"
            + (f"; the library holds it: {b['held'][0]}" if b.get("held") else "; not in the library"))
    if i.get("damaged_copy"):
        g = i["damaged_copy"]
        say(f"   DAMAGED COPY of {g['path']}: {g['blocks_differ']} of {g['blocks']} blocks differ, "
            f"bytes {g['first_byte']}..{g['last_byte']}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        say(__doc__)
        return 2
    db, cmd, rest = argv[0], argv[1], argv[2:]
    pdir = pending_dir(db)
    conn = open_ro(db)
    if cmd == "list":
        es = entries(pdir)
        if "--json" in rest:
            print(json.dumps(es, ensure_ascii=False))
        else:
            for e in es:
                show(e)
            say(f"{len(es)} waiting in {pdir}")
        return 0
    if cmd == "identify":
        import secret
        key = secret.acoustid_key(required=False)
        todo = [find(pdir, s) for s in rest] or [e for e in entries(pdir) if not e.get("decision")]
        for e in todo:
            e["identification"] = identify(conn, e, key)
            save(pdir, e)
            show(e)
        return 0
    if cmd in ("induct", "reject", "repair"):
        shas = [a for a in rest if not a.startswith("--")]
        if len(shas) != 1:
            say(f"{cmd} takes one SHA")
            return 2
        e = find(pdir, shas[0])
        if cmd == "reject":
            reject(pdir, e)
            say(f"rejected {e['sha'][:12]}; the intake will decline it if offered again")
        elif cmd == "repair":
            g = repair(conn, pdir, e)
            say(f"repair offered: the sender will be given {g['path']} ({g['sha256'][:12]}) "
                f"for its copy {e['sha'][:12]}")
        else:
            if "--root" not in rest or rest.index("--root") + 1 >= len(rest):
                say("induct needs --root MUSIC")
                return 2
            placed = induct(pdir, e, rest[rest.index("--root") + 1])
            say(f"placed at {placed['path']}")
            print(json.dumps(placed, ensure_ascii=False))
        return 0
    say(f"unknown command {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
