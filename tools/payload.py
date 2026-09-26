#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the derived-data payload that travels between installations `[SPEC014]`.

`[SPEC-DF-065]` promises "one serializer, one parser, one schema version" across
all three transports. This is that serializer -- the only one. Stage 4's bundle
exporter imports it rather than growing a second copy `[GDE-FBD-040]`, and the
embedded-tag and sidecar transports `[SPEC-DF-060]` emit the same object into a
different envelope.

What travels is decided by scope, not by convenience `[SPEC-DF-040]`:

  * `encodings[]`  bind by `audio_md5` -- this exact rip. Passages, boundaries,
                   trim points and gain are meaningless against another encode.
  * `recordings[]` bind by `recording_mbid` -- this music, any encoding. Flavor,
                   titles, artists and releases are valid for anyone holding it.

They are separate arrays because a recording may be referenced by several
encodings, which is the schema's own shape and not a serialisation choice.

**What is deliberately absent is most of the database.** Measured 2026-08-20 on
the 1,072 MB library: `musicbrainz_cache` is 547 MB and `lowlevel_cache` 202 MB
-- 73% of the file with `identification_cache` -- and a Lempi has no use for any
of it. The caches exist so Vipunen need not re-query a rate-limited service or
re-decode audio; a player neither queries nor decodes. Class D never travels at
all `[SPEC-DF-055]`, and machine scope -- `path`, `mtime`, `size_bytes` -- is
supplied by the receiver from the file it actually has `[SPEC-DF-030]`.

    python tools/payload.py data/library.db --like '%Frisina%' -o out.json
"""

import argparse
import json
import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]

# The payload's own version, independent of `schema_meta.schema_version` --
# a database schema and a wire format are free to move separately, and
# conflating them would tie a receiver's acceptance to a table it never sees.
PAYLOAD_VERSION = 1
GENERATOR = "vipunen-payload@0.1"

# Fields a receiver must have to build a legal row, read off SPEC008's NOT NULL
# constraints rather than chosen. `[SPEC-SUI-165]` defines "compatible" as the
# receiver being able to construct what it requires, so this list is normative:
# it is the question a receiver asks of a payload, not a description of one.
REQUIRED = {
    "encoding": ("audio_md5", "bundle_path", "format", "duration_ms"),
    "passage": ("kind", "start_ms", "end_ms", "boundary_src"),
    "credit": ("mbid", "weight", "source"),
    "recording": ("mbid", "title", "source"),
    "flavor": ("characteristic", "class", "value", "source"),
    # Optional as a whole -- most recordings have no words -- but a `lyrics`
    # object that is present must be a whole row `[SPEC-LYR-025]`.
    "lyrics": ("text", "source", "fetched_at"),
}


def has_table(conn: sqlite3.Connection, table: str) -> bool:
    """`lyrics` is created by `import_lyrics.py` on first use, so a library
    that never imported any has no such table -- and no words to send."""
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Same reasoning as `export_changes.py`'s own `has_column`: this tool opens
    `mode=ro`, so it cannot `ALTER TABLE` a source database still short a
    column `[SPEC-SUI-226]` added after it was created. It reads around the
    gap instead of refusing to run against an older library.
    """
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def build(conn: sqlite3.Connection, md5s: list[str], roots: str = "") -> dict:
    """The payload for a set of encodings, plus everything they reference."""
    conn.row_factory = sqlite3.Row
    q = ",".join("?" * len(md5s))
    # `fade_*` `[SPEC-SUI-226]` predate this on any source database still on a
    # pre-fade schema -- read around the gap the same way `export_changes.py`
    # already does for `boundary_reviews`. Unlike `lead_in_ms`/`gain_db`,
    # there is no "not analysed" state to preserve here: a migrated database
    # always has a real value, so the only reason to omit the keys is that
    # this source predates the columns entirely, and a receiver seeing them
    # absent falls back to `passages`' own schema default `[SPEC008 §3]`.
    have_fade = has_column(conn, "passages", "fade_in_ms")

    encodings, wanted = [], set()
    for f in conn.execute(
            f"SELECT * FROM files WHERE audio_md5 IN ({q}) ORDER BY audio_md5", md5s):
        passages = []
        for p in conn.execute(
                "SELECT * FROM passages WHERE file_id = ? ORDER BY kind, start_ms",
                (f["file_id"],)):
            credits = [
                {"mbid": r["mbid"], "weight": r["weight"], "source": r["source"]}
                for r in conn.execute(
                    "SELECT * FROM passage_recordings WHERE passage_id = ? ORDER BY mbid",
                    (p["passage_id"],))]
            wanted.update(c["mbid"] for c in credits)
            passage = {
                "kind": p["kind"],
                "start_ms": p["start_ms"],
                "end_ms": p["end_ms"],
                # NULL means "not analysed", which is not the same as zero and
                # must survive the round trip as null rather than as 0 ms.
                "lead_in_ms": p["lead_in_ms"],
                "lead_out_ms": p["lead_out_ms"],
                "gain_db": p["gain_db"],
                "boundary_src": p["boundary_src"],
            }
            if have_fade:
                # This passage's own volume envelope, orthogonal to lead and
                # travelling for the same reason gain does: it is meaningless
                # against another encoding, and a receiver constructing this
                # passage for the first time has nothing else to build it
                # from `[SPEC-SC-046]`.
                passage.update(
                    fade_in_ms=p["fade_in_ms"], fade_out_ms=p["fade_out_ms"],
                    fade_in_curve=p["fade_in_curve"], fade_out_curve=p["fade_out_curve"])
            passage["recordings"] = credits
            passages.append(passage)
        # The file's own tags travel, though they are cheap to re-derive from
        # audio that is arriving anyway. Without them an unidentified passage
        # lands with no artist at all: the player resolves a name MusicBrainz ->
        # tag -> filename, and these four have no MusicBrainz entry, so the tag
        # is the only place "Gerardo Frisina" exists. Making the receiver run a
        # probe pass to recover what the sender already knew is a worse trade
        # than the bytes.
        t = conn.execute("SELECT * FROM file_tags WHERE file_id = ?", (f["file_id"],)).fetchone()
        encodings.append({
            "audio_md5": f["audio_md5"],
            # Where the audio sits INSIDE the bundle. Bundle scope, not machine
            # scope: it tells the receiver which file this describes, and the
            # hash -- not this string -- is what proves the answer.
            "bundle_path": bundle_path(f["path"], roots),
            "format": f["format"],
            "duration_ms": f["duration_ms"],
            "tags": None if t is None else {
                "title": t["title"], "artist": t["artist"], "album": t["album"],
                "track_no": t["track_no"], "disc_no": t["disc_no"],
                "has_art": t["has_art"],
            },
            "passages": passages,
        })

    have_lyrics = has_table(conn, "lyrics")
    recordings = []
    for mbid in sorted(wanted):
        r = conn.execute("SELECT * FROM recordings WHERE mbid = ?", (mbid,)).fetchone()
        if r is None:
            # A credit naming a recording the library does not hold is a defect
            # in the source, not something to paper over on the way out.
            print(f"  WARN no recordings row for {mbid}", file=sys.stderr)
            continue
        rec = {
            "mbid": r["mbid"],
            "title": r["title"],
            "length_ms": r["length_ms"],
            "source": r["source"],
            "artists": [
                {"mbid": a["artist_mbid"], "name": a["name"],
                 "sort_name": a["sort_name"], "weight": a["weight"], "source": a["source"]}
                for a in conn.execute(
                    "SELECT ra.artist_mbid, ra.weight, ra.source, ar.name, ar.sort_name "
                    "FROM recording_artists ra LEFT JOIN artists ar ON ar.mbid = ra.artist_mbid "
                    "WHERE ra.mbid = ? ORDER BY ra.artist_mbid", (mbid,))],
            "flavor": [
                {"characteristic": v["characteristic"], "class": v["class"],
                 "value": v["value"], "source": v["source"], "accuracy": v["accuracy"]}
                for v in conn.execute(
                    "SELECT * FROM flavor WHERE subject_kind = 'recording' AND subject_id = ? "
                    "ORDER BY characteristic, class", (mbid,))],
        }
        # The words, on the recording they belong to `[SPEC-LYR-025]`: class C,
        # so they travel, and a phone keeps them in its private database
        # rather than beside the audio `[REQ-AND-202]`. Omitted, not null, when
        # there are none -- absent is the common case, not a gap.
        w = conn.execute("SELECT text, source, fetched_at FROM lyrics WHERE mbid = ?",
                         (mbid,)).fetchone() if have_lyrics else None
        if w is not None:
            rec["lyrics"] = {"text": w["text"], "source": w["source"],
                             "fetched_at": w["fetched_at"]}
        recordings.append(rec)

    return {
        "payload_version": PAYLOAD_VERSION,
        "generator": GENERATOR,
        "encodings": encodings,
        "recordings": recordings,
    }


def bundle_path(path: str, roots: str) -> str:
    """The audio's place within the bundle, as forward slashes.

    Separators are normalised because the bundle is written on Windows and read
    on Linux `[SPEC-RLK-020]`, and a backslash is a legal filename character on
    the receiving side -- so shipping one would not merely look wrong, it would
    name a different file.
    """
    p = path.replace("\\", "/")
    for root in (r.replace("\\", "/").rstrip("/") for r in roots.split(";") if r):
        if p.lower().startswith(root.lower() + "/"):
            return p[len(root) + 1:]
    return p.rsplit("/", 1)[-1]


def missing_required(payload: dict) -> list[str]:
    """What a receiver would find absent. The importer asks the same question."""
    out = []

    def check(kind, obj, where):
        for k in REQUIRED[kind]:
            if obj.get(k) is None:
                out.append(f"{where}: missing {kind}.{k}")

    for e in payload.get("encodings", []):
        where = e.get("audio_md5", "<no audio_md5>")
        check("encoding", e, where)
        # Present is not usable. A duration that is a string, a float or zero
        # satisfies `is None` and then lands as a zero length on the receiver,
        # against which every play/skip judgement is meaningless
        # `[SPEC-MPD-092]`. The importer refuses these, so the builder must not
        # emit them -- a sender that ships a payload it knows to be rejectable
        # has moved the failure to the far end of a slow transfer.
        d = e.get("duration_ms")
        if d is not None and not (isinstance(d, int) and not isinstance(d, bool) and d > 0):
            out.append(f"{where}: encoding.duration_ms is not a positive integer")
        # Optional `[SPEC-PL-087]` -- only a bundle carries it -- but present
        # and unusable would fail every file at the receiver, as corrupt.
        h = e.get("sha256")
        if h is not None and not (isinstance(h, str) and len(h) == 64
                                  and all(c in "0123456789abcdef" for c in h)):
            out.append(f"{where}: encoding.sha256 is not 64 lower-case hex digits")
        if not e.get("passages"):
            out.append(f"{where}: no passages")
        for p in e.get("passages", []):
            check("passage", p, where)
            for c in p.get("recordings", []):
                check("credit", c, where)
    for r in payload.get("recordings", []):
        check("recording", r, r.get("mbid", "<no mbid>"))
        for v in r.get("flavor", []):
            check("flavor", v, r.get("mbid", "<no mbid>"))
        if r.get("lyrics") is not None:
            check("lyrics", r["lyrics"], r.get("mbid", "<no mbid>"))
    return out


def conflicts(payload: dict) -> list[str]:
    """Contradictions no rule resolves -- the other half of `[SPEC-SUI-165]`.

    `[SPEC-DF-070]` ranks a payload against the receiver's own values, by
    provenance then recency. It says nothing about a payload disagreeing with
    *itself*, because there is nothing to rank: two titles for one mbid have
    equal claim and picking either would be a guess recorded as a fact.

    The CHECK constraints are here for the same reason. A row SQLite would
    refuse is not a value to reconcile; it is a payload that cannot be
    constructed, and finding that out at INSERT time means finding out
    half way through `[SPEC-SUI-165]`.
    """
    out = []

    def dupes(items, key, what):
        seen = {}
        for it in items:
            k = it.get(key)
            if k in seen and seen[k] != it:
                out.append(f"{what} {k}: two entries, and they differ")
            seen[k] = it

    dupes(payload.get("encodings", []), "audio_md5", "encoding")
    dupes(payload.get("recordings", []), "mbid", "recording")

    for e in payload.get("encodings", []):
        where = e.get("audio_md5", "<no audio_md5>")
        for p in e.get("passages", []):
            s, x = p.get("start_ms"), p.get("end_ms")
            if isinstance(s, int) and isinstance(x, int) and x <= s:
                out.append(f"{where}: passage end_ms {x} <= start_ms {s}")
    for r in payload.get("recordings", []):
        seen = set()
        for v in r.get("flavor", []):
            k = (v.get("characteristic"), v.get("class"))
            if k in seen:
                out.append(f"{r.get('mbid')}: flavor {k[0]}/{k[1]} appears twice")
            seen.add(k)
            val = v.get("value")
            if isinstance(val, (int, float)) and not 0.0 <= val <= 1.0:
                out.append(f"{r.get('mbid')}: flavor {k[0]}/{k[1]} value {val} outside 0..1")
    return out


def compatible(payload: dict) -> list[str]:
    """Everything that makes this payload unacceptable. Empty means import it.

    Deliberately not a version comparison `[SPEC-SUI-165]`: a NEWER payload that
    dropped a required field is incompatible, and an older one may be perfectly
    usable. `payload_version` is recorded, never consulted for acceptance.
    """
    return missing_required(payload) + conflicts(payload)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--like", help="path LIKE pattern selecting the encodings")
    ap.add_argument("--md5", action="append", default=[], help="an audio_md5; repeatable")
    ap.add_argument("--roots", default="", help="';'-separated audio roots to strip")
    ap.add_argument("-o", "--out", default="-")
    args = ap.parse_args()

    # Catalogue-only, read-only.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY)
    md5s = list(args.md5)
    if args.like:
        md5s += [r[0] for r in conn.execute(
            "SELECT audio_md5 FROM files WHERE path LIKE ?", (args.like,))]
    if not md5s:
        print("nothing selected", file=sys.stderr)
        return 1

    payload = build(conn, sorted(set(md5s)), args.roots)
    bad = compatible(payload)
    for b in bad:
        print(f"  INCOMPATIBLE {b}", file=sys.stderr)

    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)
    if args.out == "-":
        print(text)
    else:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
        print(f"{len(payload['encodings'])} encoding(s), "
              f"{len(payload['recordings'])} recording(s), "
              f"{len(text):,} bytes -> {args.out}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
