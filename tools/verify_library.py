#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verify the hub's library: every catalogued file re-hashed against the
SHA-256 its row records `[GDE-INT-115]`.

The audit of 2026-10-14 found every file hashed once, when it arrived, and
almost never again (`reports/integrity-audit-2026-10-14.md`): a file could rot
on the hub's disk and the first sign would be a send refusing it, or a passage
that would not play. This is the check that makes that a morning's message
instead. Its first run, by hand, took 117 s for 48.8 GB.

Each file is one of:

  ok          its bytes are the ones recorded
  retagged    its bytes differ but its audio does not -- `audio_md5`, the
              identity hash, still matches: a tag was rewritten. Said, not
              alarming; the record is brought up to date by the next send
  damaged     its bytes and its audio both differ from the record
  missing     no file at the recorded path
  unreadable  there, and could not be read
  unrecorded  no SHA-256 on its row to check against

With the audio, the files a CD rip left beside it `[GDE-INT-175]`: each rip
log, AccurateRip report and CUE sheet `sidecars.py` recorded is read again --
ok, changed or missing -- and the sidecars no record names are counted. And
the music folder is walked for audio no row claims, for a person to induct or
remove: the strays. The CD inbox, `_Rips`, is skipped and said to be: what
waits there is not lost, it is not added yet. So is `_Retired`, where a disc
ripped again keeps the earlier rip's files it no longer plays `[SPEC-AGN-030]`.

Read-only: the library is opened `mode=ro` and nothing in it is written. The
result goes to `<library's folder>/verify/<date>.json`, the newest kept 14
deep, where `star_sync.py ... status` reads its age and its findings.

    python tools/verify_library.py data/library.db [--json]

Exit 0 when nothing is damaged, missing or unreadable and no recorded sidecar
has changed or gone; 1 otherwise; 2 when it could not run at all. A stray is
said, not failed: it is something to tidy, not something lost.
"""

import argparse
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db                   # noqa: E402
import sidecars                   # noqa: E402  -- [GDE-INT-175]
from byte_hash import sha256_file  # noqa: E402

KEEP = 14
BAD = ("damaged", "missing", "unreadable")
AUDIO = (".mp3", ".flac", ".wav", ".aif", ".aiff", ".m4a", ".ogg", ".opus", ".wma", ".ape", ".wv")
INBOX = "_Rips"
RETIRED = "_Retired"


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def verify_dir(library: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(library)), "verify")


def audio_md5(path: str) -> str | None:
    import ingest_folder
    return ingest_folder.audio_md5(path)


def check_sidecars(records: list[dict], unrecorded: int) -> dict:
    """Each recorded sidecar read again: ok, changed, or missing; `orphaned` when
    no catalogued file has its audio any more, so there is no folder to look in."""
    out = {"ok": 0, "changed": [], "missing": [], "orphaned": 0, "unrecorded": unrecorded}
    for r in records:
        if r["outcome"] in ("renamed", "retired"):
            continue        # its latest word is that it is gone under that name [SPEC-AGN-030]
        if not r["folder"]:
            out["orphaned"] += 1
            continue
        path = os.path.join(r["folder"], r["name"])
        if not os.path.isfile(path):
            out["missing"].append({"path": path, "recorded_at": r["decided_at"]})
            continue
        try:
            got = sha256_file(path)
        except OSError as e:
            out["missing"].append({"path": path, "recorded_at": r["decided_at"], "error": str(e)})
            continue
        if got == r["sha256"]:
            out["ok"] += 1
        else:
            out["changed"].append({"path": path, "recorded": r["sha256"], "found": got,
                                   "recorded_at": r["decided_at"], "outcome": r["outcome"]})
    return out


def strays(paths: list[str]) -> dict:
    """Audio under the music root that no row claims. The root is the folder
    every catalogued file lies under, as the console takes it; a FLAC beside a
    catalogued MP3 of the same name is its lossless copy, counted, not a stray."""
    try:
        root = os.path.commonpath([p for p in paths if p]) if paths else ""
    except ValueError:
        root = ""
    out = {"root": root, "audio": [], "lossless_copies": 0, "inbox": None, "inbox_audio": 0,
           "retired": None, "retired_audio": 0}
    if not root or not os.path.isdir(root):
        return out
    claimed = {os.path.normcase(os.path.normpath(p)) for p in paths if p}
    stems = {os.path.splitext(p)[0] for p in claimed}
    for d, dirs, files in os.walk(root):
        if d == root and INBOX in dirs:
            out["inbox"] = os.path.join(root, INBOX)
        if d == root and RETIRED in dirs:
            out["retired"] = os.path.join(root, RETIRED)
        for n in files:
            if not n.lower().endswith(AUDIO):
                continue
            p = os.path.join(d, n)
            key = os.path.normcase(os.path.normpath(p))
            if key in claimed:
                continue
            if out["inbox"] and key.startswith(os.path.normcase(out["inbox"]) + os.sep):
                out["inbox_audio"] += 1
            elif out["retired"] and key.startswith(os.path.normcase(out["retired"]) + os.sep):
                out["retired_audio"] += 1
            elif n.lower().endswith(".flac") and os.path.splitext(key)[0] in stems:
                out["lossless_copies"] += 1
            else:
                out["audio"].append(p)
    out["audio"].sort()
    return out


def verify(library: str, progress=say) -> dict:
    conn = lempi_db.connect(library, lempi_db.ROLE_LIBRARY)
    try:
        rows = conn.execute("SELECT file_id, audio_md5, path, sha256 FROM files ORDER BY file_id").fetchall()
        side_records = sidecars.recorded(conn)
        side_unrecorded = sum(len(names) for _f, _m, names in sidecars.unrecorded(conn))
    finally:
        conn.close()
    out = {"checked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "library": os.path.abspath(library),
           "files": len(rows), "ok": 0, "bytes": 0, "unrecorded": 0,
           **{k: [] for k in ("retagged",) + BAD}}
    t0 = last = time.time()
    for i, (fid, md5, path, want) in enumerate(rows, 1):
        if not path or not os.path.isfile(path):
            out["missing"].append({"file_id": fid, "path": path})
        elif not want:
            out["unrecorded"] += 1
        else:
            try:
                got = sha256_file(path)
                out["bytes"] += os.path.getsize(path)
            except OSError as e:
                out["unreadable"].append({"file_id": fid, "path": path, "error": str(e)})
                continue
            if got == want:
                out["ok"] += 1
            else:
                now_md5 = audio_md5(path)
                kind = "retagged" if now_md5 == md5 else "damaged"
                out[kind].append({"file_id": fid, "path": path, "recorded": want, "found": got,
                                  "audio_md5": md5, "audio_md5_now": now_md5})
        if time.time() - last >= 30:
            last = time.time()
            progress(f"  {i} of {len(rows)} files, {out['bytes'] / 1e9:.1f} GB")
    out["sidecars"] = check_sidecars(side_records, side_unrecorded)
    out["strays"] = strays([r[2] for r in rows])
    out["seconds"] = round(time.time() - t0, 1)
    out["ok_whole"] = not any(out[k] for k in BAD)
    out["sidecars_whole"] = not (out["sidecars"]["changed"] or out["sidecars"]["missing"])
    return out


def keep(library: str, result: dict) -> str:
    """The result, under today's date; older ones pruned to the newest KEEP."""
    d = verify_dir(library)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, result["checked_at"][:10] + ".json")
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(result, fh, indent=1)
    os.replace(tmp, path)
    for old in sorted(f for f in os.listdir(d) if f.endswith(".json"))[:-KEEP]:
        os.remove(os.path.join(d, old))
    return path


def newest(library: str) -> dict | None:
    """The latest kept result, for `status`; None if there is none."""
    d = verify_dir(library)
    days = sorted(f for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []
    if not days:
        return None
    with open(os.path.join(d, days[-1]), encoding="utf-8") as fh:
        return json.load(fh)


def summary(r: dict) -> str:
    parts = [f"{r['ok']} of {r['files']} files match their record"]
    for k in ("retagged",) + BAD:
        if r[k]:
            parts.append(f"{len(r[k])} {k}")
    if r["unrecorded"]:
        parts.append(f"{r['unrecorded']} with no hash recorded")
    s = r.get("sidecars")                      # absent from a result kept before [GDE-INT-175]
    if s:
        n = s["ok"] + len(s["changed"]) + len(s["missing"])
        side = f"rip sidecars: {s['ok']} of {n} match"
        side += "".join(f", {len(s[k])} {k}" for k in ("changed", "missing") if s[k])
        if s["unrecorded"]:
            side += f", {s['unrecorded']} never recorded"
        parts.append(side)
    st = r.get("strays")
    if st and st["audio"]:
        parts.append(f"{len(st['audio'])} audio file(s) no row claims")
    return "; ".join(parts) + f" ({r['bytes'] / 1e9:.1f} GB in {r['seconds']:.0f} s)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("library")
    ap.add_argument("--json", action="store_true", help="end with the result as one JSON line")
    args = ap.parse_args()
    try:
        say(f"verifying {args.library} ...")
        r = verify(args.library)
        where = keep(args.library, r)
    except Exception as e:                                   # noqa: BLE001
        say(f"ERROR: could not verify: {e}")
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    say(summary(r))
    for k in BAD + ("retagged",):
        for x in r[k][:20]:
            say(f"  {k.upper()}: {x['path']}" + (f" ({x['error']})" if x.get("error") else ""))
    for k in ("changed", "missing"):
        for x in r["sidecars"][k][:20]:
            say(f"  SIDECAR {k.upper()}: {x['path']}")
    st = r["strays"]
    for p in st["audio"][:20]:
        say(f"  STRAY: {p}")
    if st["inbox"]:
        say(f"  (the CD inbox {st['inbox']} was not counted: {st['inbox_audio']} audio file(s) wait there)")
    if st.get("retired"):
        say(f"  (nor {st['retired']}: {st['retired_audio']} audio file(s) an earlier rip no longer plays, kept)")
    say(f"kept in {where}")
    whole = r["ok_whole"] and r["sidecars_whole"]
    if args.json:
        print(json.dumps({"ok": whole, "files": r["files"], "matched": r["ok"],
                          **{k: len(r[k]) for k in ("retagged",) + BAD}, "unrecorded": r["unrecorded"],
                          "sidecars_changed": len(r["sidecars"]["changed"]),
                          "sidecars_missing": len(r["sidecars"]["missing"]),
                          "strays": len(st["audio"]), "seconds": r["seconds"], "kept": where}))
    return 0 if whole else 1


if __name__ == "__main__":
    sys.exit(main())
