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
from byte_hash import sha256_file  # noqa: E402


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
    ap.add_argument("--present-md5-file",
                    help="file of audio_md5 to include in the payload WITHOUT audio -- the target "
                         "holds them, and is sent only what changed about them: holds, credits, "
                         "albums, sort names, covers [SPEC-HOLD-080]")
    ap.add_argument("--slim-md5-file",
                    help="file of audio_md5 (of --present-md5-file) sent only for what is about the file -- "
                         "its release: their passages go without their recordings [SPEC-SC-125]")
    ap.add_argument("--cover-releases",
                    help="file of `release [front] [back]` lines: the cover sides the target lacks; with "
                         "it, a release named only for a file the target holds sends only those "
                         "[SPEC-COV-060]")
    ap.add_argument("--gzip", action="store_true", help="write payload.json.gz as well")
    ap.add_argument("--zip", action="store_true",
                    help="also write <out>.zip: the whole bundle as one file, for a phone's "
                         "share or open sheet [REQ-AND-230]")
    ap.add_argument("--sha256-file",
                    help="file of byte hashes to include, one per line -- what a phone found "
                         "in its own storage [REQ-AND-260]")
    ap.add_argument("--payload-only", action="store_true",
                    help="no audio: what Vipunen knows about files the receiver already "
                         "holds, named by their recorded byte hash [SPEC-PL-095]")
    ap.add_argument("--no-covers", action="store_true",
                    help="send no cover images: the releases travel, without their pictures "
                         "[SPEC-PL-105]")
    ap.add_argument("--part-size", type=int, default=0,
                    help="with --payload-only: encodings per payload part, so a phone "
                         "parses a few MB at a time")
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
    if args.sha256_file:
        with open(args.sha256_file, encoding="utf-8") as fh:
            wanted = {ln.strip() for ln in fh if ln.strip()}
        hits = [r for r in conn.execute("SELECT audio_md5, sha256 FROM files WHERE sha256 IS NOT NULL")
                if r[1] in wanted]
        md5s += [r[0] for r in hits]
        print(f"by byte hash: {len(wanted)} listed, {len(hits)} held here")
    present = set()
    if args.present_md5_file:
        with open(args.present_md5_file, encoding="utf-8") as fh:
            present = {ln.strip() for ln in fh if ln.strip()}
        md5s += sorted(present)
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
    if args.payload_only:
        return payload_only(conn, md5s, roots, args)
    slim = set()
    if args.slim_md5_file:
        with open(args.slim_md5_file, encoding="utf-8") as fh:
            slim = {ln.strip() for ln in fh if ln.strip()} & present
    doc = payloadmod.build(conn, md5s, roots, slim)

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
    # The catalogue's own record of each file's bytes `[REQ-AND-960]`, where
    # it has one: the copy must match it, or the source has changed since it
    # was inducted and every derived fact in the payload may be about other
    # bytes. Caught here, not on a phone that cannot recompute audio_md5.
    recorded_col = "sha256" in {r[1] for r in conn.execute("PRAGMA table_info(files)")}
    agree = unrecorded = 0
    differ = []
    bytes_out = 0
    for e in doc["encodings"]:
        if e["audio_md5"] in present:
            continue    # held there already: the payload is the whole of it
        src = conn.execute("SELECT path FROM files WHERE audio_md5 = ?",
                           (e["audio_md5"],)).fetchone()[0]
        dest = os.path.join(audio_dir, *e["bundle_path"].split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if not os.path.isfile(src):
            print(f"  MISSING AUDIO {src}", file=sys.stderr)
            missing += 1
            continue
        shutil.copy2(src, dest)
        # Of the copy, not the source: what ships is what the receiver checks
        # `[SPEC-PL-087]`. A phone has no ffmpeg to recompute audio_md5, so
        # this is its only proof the file arrived whole `[REQ-AND-230]`.
        e["sha256"] = sha256_file(dest)
        recorded = conn.execute("SELECT sha256 FROM files WHERE audio_md5 = ?",
                                (e["audio_md5"],)).fetchone()[0] if recorded_col else None
        if recorded is None:
            unrecorded += 1
        elif recorded == e["sha256"]:
            agree += 1
        else:
            print(f"  BYTES DIFFER from the catalogue's record: {src}", file=sys.stderr)
            differ.append(src)
        bytes_out += os.path.getsize(dest)
        copied += 1

    trimmed = 0
    if args.cover_releases:
        trimmed = trim_covers(doc, present, read_cover_releases(args.cover_releases))
    cover_bytes = write_covers(conn, doc, args.out, args, set())
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
    if present:
        print(f"  no audio    {len(present & {e['audio_md5'] for e in doc['encodings']})} "
              "already on the target, sent without audio for what changed about them")
    print(f"  releases    {len(doc.get('releases', []))}, covers {cover_bytes/1e6:.1f} MB"
          + (f"   ({trimmed} side(s) the target has already, not sent)" if trimmed else ""))
    print(f"  payload     {len(text.encode())/1024:.1f} KB"
          + (f"  ({gz_len/1024:.1f} KB gzipped)" if gz_len else ""))
    print(f"  byte hashes {agree} match the catalogue's record, {unrecorded} not recorded there"
          + (f", {len(differ)} DIFFER" if differ else ""))
    if missing or differ:
        # A bundle that is short of audio, or carries bytes the catalogue does
        # not describe, is not a bundle; say so with a non-zero exit so a
        # script cannot ship it as complete.
        return 1
    if args.zip:
        # One file, since a share sheet hands over files and not folders.
        # Stored, not deflated: audio is already compressed, and deflating it
        # costs time on both ends to save almost nothing. payload.json first,
        # so a receiver can read what is coming before the audio arrives.
        import zipfile
        dest = args.out.rstrip("/\\") + ".zip"
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED) as z:
            z.write(os.path.join(args.out, "payload.json"), "payload.json")
            for dirpath, _, files in sorted(os.walk(audio_dir)):
                for f in sorted(files):
                    full = os.path.join(dirpath, f)
                    z.write(full, "audio/" + os.path.relpath(full, audio_dir).replace(os.sep, "/"))
            zip_covers(z, args.out)
        print(f"  zip         {dest}, {os.path.getsize(dest)/1e6:.1f} MB")
    return 0


def trim_covers(doc, present: set, wanted: dict) -> int:
    """Keep a release's cover only where the target needs it `[SPEC-COV-060]`:
    the sides `wanted` names for it (the ones it lacks), or the whole cover of
    a release carrying a recording whose audio is being sent (it has nothing
    of that album). A release sent only to name an album for a file the
    target holds goes without its cover, and the target keeps the one it has.
    Returns how many sides were dropped.

    A covers send to lempi02w on 2026-10-02 carried 84.8 MB of covers --
    every cover of the 676 releases it named -- for a few hundred sides new
    to it."""
    sent = {c["mbid"] for e in doc.get("encodings", []) if e["audio_md5"] not in present
            for p in e.get("passages", []) for c in p.get("recordings", [])}
    dropped = 0
    for rel in doc.get("releases", []):
        cover = rel.get("cover")
        if not cover or any(t.get("recording") in sent for t in rel.get("tracks", [])):
            continue
        keep = wanted.get(rel["mbid"], set())
        for side in ("front", "back"):
            if side in cover and side not in keep:
                del cover[side]
                dropped += 1
        if "front" not in cover and "back" not in cover:
            del rel["cover"]
    return dropped


def read_cover_releases(path: str) -> dict:
    """`release [front] [back]` a line -- the sides the target lacks; a
    release alone on its line lacks both."""
    out = {}
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            parts = ln.split()
            if parts:
                out[parts[0]] = set(parts[1:]) or {"front", "back"}
    return out


def write_covers(conn, doc, out, args, written: set) -> int:
    """`[SPEC-PL-105]`: each cover the payload names, written beside it as
    `covers/...`, once however many parts name it. With --no-covers the
    payload stops naming them, so a receiver expects none. Returns bytes
    written."""
    if args.no_covers:
        for item in doc.get("releases", []) + doc.get("encodings", []):
            item.pop("cover", None)
        return 0
    n = 0
    for name, blob in payloadmod.cover_files(conn, doc).items():
        if name in written:
            continue
        dest = os.path.join(out, *name.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(blob)
        written.add(name)
        n += len(blob)
    return n


def zip_covers(z, out):
    """Every file under `covers/`, into an open zip."""
    covers = os.path.join(out, "covers")
    for dirpath, _, files in sorted(os.walk(covers)):
        for f in sorted(files):
            full = os.path.join(dirpath, f)
            z.write(full, "covers/" + os.path.relpath(full, covers).replace(os.sep, "/"))


def payload_only(conn, md5s, roots, args) -> int:
    """[SPEC-PL-095]: what Vipunen knows about files the receiver already
    holds, and no audio. Each encoding carries its recorded byte hash, which
    is how the receiver finds its own copy [REQ-AND-260]; one with none
    recorded cannot be found, and is left out and counted. In parts of
    --part-size encodings, so a phone parses a few MB at a time: parts are
    payload-001.json, payload-002.json, ... and one part is payload.json."""
    recorded = dict(conn.execute("SELECT audio_md5, sha256 FROM files WHERE sha256 IS NOT NULL"))
    named = [m for m in md5s if m in recorded]
    if len(named) < len(md5s):
        print(f"  {len(md5s) - len(named)} encoding(s) have no recorded sha256 and are left out")
    if not named:
        print("nothing to send", file=sys.stderr)
        return 1
    size = args.part_size if args.part_size > 0 else len(named)
    chunks = [named[i:i + size] for i in range(0, len(named), size)]
    os.makedirs(args.out, exist_ok=True)
    names = []
    written: set = set()
    cover_bytes = 0
    for n, chunk in enumerate(chunks, 1):
        doc = payloadmod.build(conn, chunk, roots)
        bad = payloadmod.compatible(doc)
        if bad:
            for b in bad:
                print(f"  REFUSING: {b}", file=sys.stderr)
            return 1
        for e in doc["encodings"]:
            e["sha256"] = recorded[e["audio_md5"]]
        cover_bytes += write_covers(conn, doc, args.out, args, written)
        name = "payload.json" if len(chunks) == 1 else f"payload-{n:03d}.json"
        with open(os.path.join(args.out, name), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, separators=(",", ":"), ensure_ascii=False)
        names.append(name)
    total = sum(os.path.getsize(os.path.join(args.out, n)) for n in names)
    print(f"bundle: {args.out} (payload only)")
    print(f"  encodings   {len(named)}, in {len(names)} part(s)")
    print(f"  payload     {total / 1e6:.1f} MB")
    print(f"  covers      {len(written)} files, {cover_bytes / 1e6:.1f} MB")
    if args.zip:
        import zipfile
        dest = args.out.rstrip("/\\") + ".zip"
        # Deflated, unlike an audio bundle: JSON compresses about tenfold.
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for name in names:
                z.write(os.path.join(args.out, name), name)
            zip_covers(z, args.out)
        print(f"  zip         {dest}, {os.path.getsize(dest) / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
