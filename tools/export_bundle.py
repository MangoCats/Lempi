#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build a bundle for a remote Lempi `[SPEC-SUI-095]`, `[SPEC014]`.

Audio, plus the derived facts for exactly the encodings it carries. **Not the
database**: measured on this library, `musicbrainz_cache` is 547 MB and
`lowlevel_cache` 202 MB of a 1,072 MB file `[SPEC-PL-040]`, and a player has no
use for either -- the caches exist so Vipunen need not re-query a rate-limited
service or re-decode audio, and a player does neither. Shipping the database
would also carry class D over the appliance's own play history `[SPEC-DF-090]`,
which is the only irreplaceable data in the system.

The payload is built by `payload.py`, which is the one serializer
`[SPEC-DF-065]`. Nothing here re-implements it.

    python tools/export_bundle.py data/library.db --like '%Frisina%' \\
           --root "C:/Users/Mango Cat/Music" -o out/frisina
    rsync -a --chmod=D755,F644 out/frisina/ pi@lempi02w:/srv/library/incoming/frisina/
"""

import argparse
import gzip
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import payload as payloadmod  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--like", help="path LIKE pattern selecting encodings")
    ap.add_argument("--md5", action="append", default=[])
    ap.add_argument("--md5-file",
                     help="file of audio_md5 to include, one per line -- the human-reviewed "
                          "output of a mesh_diff.py report `[SPEC-MESH-040]`, rather than "
                          "each one typed on the command line")
    ap.add_argument("--root", action="append", default=[],
                    help="audio root to make bundle_path relative to; repeatable")
    ap.add_argument("-o", "--out", required=True, help="bundle directory to write")
    ap.add_argument("--have", help="file of audio_md5 the target already holds, one per line")
    ap.add_argument("--gzip", action="store_true", help="write payload.json.gz as well")
    args = ap.parse_args()

    # Catalogue-only, read-only.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY)
    md5s = list(args.md5)
    if args.md5_file:
        with open(args.md5_file, encoding="utf-8") as fh:
            md5s += [ln.strip() for ln in fh if ln.strip()]
    if args.like:
        md5s += [r[0] for r in conn.execute(
            "SELECT audio_md5 FROM files WHERE path LIKE ?", (args.like,))]
    md5s = sorted(set(md5s))
    if not md5s:
        print("nothing selected", file=sys.stderr)
        return 1

    # The delta, when the target has said what it holds. Idempotence makes this
    # an optimisation rather than a correctness requirement: re-sending an
    # encoding the target already has is a no-op on import `[SPEC-SUI-180]`,
    # so a missing --have costs bytes and never correctness.
    if args.have:
        with open(args.have, encoding="utf-8") as fh:
            have = {ln.strip() for ln in fh if ln.strip()}
        before = len(md5s)
        md5s = [m for m in md5s if m not in have]
        print(f"delta: {before} selected, {before - len(md5s)} already there, {len(md5s)} to send")
        if not md5s:
            print("nothing to send.")
            return 0

    roots = ";".join(args.root)
    doc = payloadmod.build(conn, md5s, roots)

    bad = payloadmod.compatible(doc)
    if bad:
        # Refuse to ship what the receiver would have to reject. Finding this
        # out here costs a moment; finding it out after an eleven-hour transfer
        # costs the transfer.
        for b in bad:
            print(f"  REFUSING: {b}", file=sys.stderr)
        return 1

    os.makedirs(args.out, exist_ok=True)
    audio_dir = os.path.join(args.out, "audio")
    copied = missing = 0
    bytes_out = 0
    for e in doc["encodings"]:
        src = conn.execute("SELECT path FROM files WHERE audio_md5 = ?",
                           (e["audio_md5"],)).fetchone()[0]
        dest = os.path.join(audio_dir, *e["bundle_path"].split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if not os.path.isfile(src):
            print(f"  MISSING AUDIO {src}", file=sys.stderr)
            missing += 1
            continue
        shutil.copy2(src, dest)
        bytes_out += os.path.getsize(dest)
        copied += 1

    text = json.dumps(doc, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "payload.json"), "w",
              encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")
    gz_len = 0
    if args.gzip:
        blob = gzip.compress(json.dumps(doc, separators=(",", ":"),
                                        ensure_ascii=False).encode())
        with open(os.path.join(args.out, "payload.json.gz"), "wb") as fh:
            fh.write(blob)
        gz_len = len(blob)

    print(f"bundle: {args.out}")
    print(f"  encodings   {len(doc['encodings'])}")
    print(f"  recordings  {len(doc['recordings'])}")
    print(f"  audio       {copied} files, {bytes_out/1e6:.1f} MB"
          + (f"   ({missing} MISSING)" if missing else ""))
    print(f"  payload     {len(text.encode())/1024:.1f} KB"
          + (f"  ({gz_len/1024:.1f} KB gzipped)" if gz_len else ""))
    if missing:
        # A bundle that is short of audio is not a bundle; say so with a
        # non-zero exit so a script cannot ship it as complete.
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
