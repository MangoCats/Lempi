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

Read-only: the library is opened `mode=ro` and nothing in it is written. The
result goes to `<library's folder>/verify/<date>.json`, the newest kept 14
deep, where `star_sync.py ... status` reads its age and its findings.

    python tools/verify_library.py data/library.db [--json]

Exit 0 when nothing is damaged, missing or unreadable; 1 otherwise; 2 when it
could not run at all.
"""

import argparse
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db                   # noqa: E402
from byte_hash import sha256_file  # noqa: E402

KEEP = 14
BAD = ("damaged", "missing", "unreadable")


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def verify_dir(library: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(library)), "verify")


def audio_md5(path: str) -> str | None:
    import ingest_folder
    return ingest_folder.audio_md5(path)


def verify(library: str, progress=say) -> dict:
    conn = lempi_db.connect(library, lempi_db.ROLE_LIBRARY)
    try:
        rows = conn.execute("SELECT file_id, audio_md5, path, sha256 FROM files ORDER BY file_id").fetchall()
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
    out["seconds"] = round(time.time() - t0, 1)
    out["ok_whole"] = not any(out[k] for k in BAD)
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
    say(f"kept in {where}")
    if args.json:
        print(json.dumps({"ok": r["ok_whole"], "files": r["files"], "matched": r["ok"],
                          **{k: len(r[k]) for k in ("retagged",) + BAD}, "unrecorded": r["unrecorded"],
                          "seconds": r["seconds"], "kept": where}))
    return 0 if r["ok_whole"] else 1


if __name__ == "__main__":
    sys.exit(main())
