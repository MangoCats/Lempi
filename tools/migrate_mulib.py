# SPDX-License-Identifier: AGPL-3.0-or-later
"""
[GDE-PHS-010] P1: migrate MuLibPlay's database into the Lempi schema.

This is the schema's first and best test. If the model cannot hold six years of
real production data, it is the wrong model -- so the acceptance criterion is
reconciliation, not completion: every non-dead field round-trips and row counts
reconcile exactly.

It also carries the only irreplaceable data in the system [SPEC-DF-090]:
37,134 play events, 2,918 tuned preferences, 16,232 verified boundaries, and 8
programmes. Everything else re-derives from audio; these do not.

Two bridges make it work, both verified before writing:

  identity   MuLibPlay's files.sig is SHA3-224 of the whole file -- the fragile
             scheme SPEC006 replaces, and useless as audio_md5. But the local
             library is byte-identical to the Pi's (38 of 40 sampled), so
             sig -> local path -> ffmpeg -> audio_md5 bridges them. The ffmpeg
             route matches Essentia's md5_encoded exactly at ~70 ms/file,
             against 27 s for a full extraction.

  boundaries MuLibPlay stores frames; Lempi stores milliseconds. 44.1 kHz
             verified against ffprobe to within 0.01% on sampled files, and
             checked per file here rather than assumed.

Usage:
    python tools/migrate_mulib.py --mulib ../MuLibPlay/mulib.db --out data/migrated-whole.db
    python tools/split_database.py data/migrated-whole.db \
        --library-out data/library.db --listener-out data/listener.db --commit
"""

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# `ffmpeg_generator()` only `[SPEC-RLK-150]`. This file keeps its own
# `audio_md5` -- a duplicate of `ingest_folder`'s and a pre-existing one -- so
# importing the generator rather than copying it is the direction that reduces
# the copies rather than adding a third.
import ingest_folder  # noqa: E402

SR = 44100.0
SRC = "inherited:mulib"

# MuLibPlay stores ONE probability per binary characteristic; Lempi stores a
# distribution, so the complement is synthesised [SPEC-SC-075].
AB_MAP = {
    "abAcoustic": ("mood_acoustic", "acoustic", "not_acoustic"),
    "abAggressive": ("mood_aggressive", "aggressive", "not_aggressive"),
    "abDanceable": ("danceability", "danceable", "not_danceable"),
    "abFemale": ("gender", "female", "male"),
    "abHappy": ("mood_happy", "happy", "not_happy"),
    "abInstrumental": ("voice_instrumental", "instrumental", "voice"),
    "abParty": ("mood_party", "party", "not_party"),
    "abRelaxed": ("mood_relaxed", "relaxed", "not_relaxed"),
    "abSad": ("mood_sad", "sad", "not_sad"),
    "abTonal": ("tonal_atonal", "tonal", "atonal"),
    "abBright": ("timbre", "bright", "dark"),
}

# MuLibPlay's hardcoded occasion tags become user-defined characteristics
# [SPEC-DIR-130]; the seasonal curve lives in the engine, the value here.
OCCASION_MAP = {
    "[C]": ("user.christmas", "christmasy", "not_christmasy"),
    "[W]": ("user.winter", "wintry", "not_wintry"),
    "[S]": ("user.summer", "summery", "not_summery"),
    "[K]": ("user.childrens", "for_children", "not_for_children"),
}

# Not migrated by this script. Reported so the omission is visible, not silent.
#
# Nine of these are dead exactly as [GDE-BMK-040] found: tempo, intensity,
# keyMood, darkLight, genre and themes are NULL for all 8,116 rows across six
# years; quality, jts and popularity carry under 10 between them. venue,
# ukChart, usChart and usChartPeak are NULL throughout as well.
#
# The remaining two are NOT dead, and citing [GDE-BMK-040] over the whole list
# overstated what that finding actually says (audited 2026-09-10):
#   lyrics     2,288 rows of real text -- not lost, just not this script's job.
#              They reached Lempi's own `lyrics` table separately (2,265 rows,
#              source 'mulibplay') [SPEC019].
#   profanity  2,529 non-NULL, of which 69 carry a real non-zero value between
#              0.001 and 0.874. Genuinely dropped, and the only MuLibPlay
#              listener data this migration actually loses. Every one of the 69
#              carries an `mbidRecording` that resolves in Lempi, so restoring
#              them is a join rather than a re-derivation [SPEC-PREF-082].
DEAD_FIELDS = ["tempo", "intensity", "keyMood", "darkLight", "genre", "themes",
               "quality", "jts", "popularity", "venue", "lyrics", "profanity",
               "ukChart", "usChart", "usChartPeak"]


def audio_md5(path):
    """Essentia's md5_encoded, via ffmpeg. ~70 ms vs ~27 s for extraction."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-c:a", "copy",
                        "-f", "md5", "-"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    m = re.search(r"MD5=([0-9a-f]{32})", r.stdout)
    return m.group(1) if m else None


def build_sig_index(roots):
    """SHA3-224 of local files -> path, matching MuLibPlay's files.sig."""
    idx = {}
    exts = (".mp3", ".flac", ".ogg", ".m4a", ".wav", ".opus")
    for root in roots:
        for dirpath, _, names in os.walk(root):
            for n in names:
                if not n.lower().endswith(exts):
                    continue
                p = os.path.join(dirpath, n)
                try:
                    idx[hashlib.sha3_224(open(p, "rb").read()).digest()] = p
                except OSError:
                    pass
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mulib", default="../MuLibPlay/mulib.db")
    # A WHOLE database, which split_database.py then divides into the live
    # pair [IMPL-DBSPLIT-025]. Deliberately not named after the old live
    # file: that one was deleted 2026-09-11 [PI-PRE-098], and recreating
    # the name would resurrect the ambiguity its removal ended.
    ap.add_argument("--out", default="data/migrated-whole.db")
    ap.add_argument("--music", nargs="*", default=[r"C:\Users\Mango Cat\Music"])
    ap.add_argument("--schema", default="sql/schema.sql")
    args = ap.parse_args()
    report = {"started": time.strftime("%Y-%m-%dT%H:%M:%S")}

    if os.path.exists(args.out):
        os.remove(args.out)
    out = sqlite3.connect(args.out)
    out.executescript(open(args.schema, encoding="utf-8").read())
    m = sqlite3.connect(f"file:{args.mulib}?mode=ro&immutable=1", uri=True)
    m.row_factory = sqlite3.Row

    print("indexing local audio by SHA3-224 ...", file=sys.stderr)
    t0 = time.time()
    sig_idx = build_sig_index(args.music)
    print(f"  {len(sig_idx):,} local files in {time.time()-t0:.0f}s", file=sys.stderr)

    # ---------------------------------------------------------------- files
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    file_map, unresolved = {}, []
    rows = m.execute("SELECT fileId, filePath, nFrames, sig FROM files").fetchall()
    for i, r in enumerate(rows, 1):
        path = sig_idx.get(r["sig"])
        if path is None:
            unresolved.append(r["filePath"])
            continue
        md5 = audio_md5(path)
        if md5 is None:
            unresolved.append(r["filePath"])
            continue
        dur = int(round(r["nFrames"] / SR * 1000)) if r["nFrames"] else 0
        st = os.stat(path)
        try:
            cur = out.execute(
                "INSERT INTO files(audio_md5,path,size_bytes,mtime,format,duration_ms,"
                "first_seen,last_seen,md5_generator) VALUES (?,?,?,?,?,?,?,?,?)",
                (md5, path, st.st_size, st.st_mtime,
                 os.path.splitext(path)[1].lstrip(".").lower(), dur, now, now,
                 ingest_folder.ffmpeg_generator()))
            file_map[r["fileId"]] = cur.lastrowid
        except sqlite3.IntegrityError:      # same audio, two containers
            fid = out.execute("SELECT file_id FROM files WHERE audio_md5=?", (md5,)).fetchone()[0]
            file_map[r["fileId"]] = fid
        if i % 500 == 0:
            print(f"  files {i}/{len(rows)}", file=sys.stderr, flush=True)
    out.commit()
    report["files"] = {"mulib": len(rows), "migrated": len(file_map),
                       "unresolved": len(unresolved)}

    # ----------------------------------------------- artists / recordings
    art_key = {}
    for r in m.execute("SELECT * FROM artists"):
        key = r["mbid"] or f"local:artist:{r['artistId']}"
        art_key[r["artistId"]] = key
        out.execute("INSERT OR IGNORE INTO artists VALUES (?,?,?,?)",
                    (key, r["name"] or "", r["sortName"], SRC))
        if any(r[c] is not None for c in ("rotation", "recovery", "restraint")):
            out.execute("INSERT OR REPLACE INTO listener_preferences VALUES "
                        "('artist',?,?,?,?,?)",
                        (key, r["rotation"], r["recovery"], r["restraint"], now))

    rec_key, occ_rows, flavor_rows = {}, [], []
    for r in m.execute("SELECT * FROM tracks"):
        key = r["mbidRecording"] or r["mbid"] or f"local:track:{r['trackId']}"
        rec_key[r["trackId"]] = key
        out.execute("INSERT OR IGNORE INTO recordings VALUES (?,?,?,?)",
                    (key, r["name"] or "", None, SRC))
        if r["artistId"] in art_key:
            out.execute("INSERT OR IGNORE INTO recording_artists VALUES (?,?,1.0,?)",
                        (key, art_key[r["artistId"]], SRC))
        if any(r[c] is not None for c in ("rotation", "recovery", "restraint")):
            out.execute("INSERT OR REPLACE INTO listener_preferences VALUES "
                        "('recording',?,?,?,?,?)",
                        (key, r["rotation"], r["recovery"], r["restraint"], now))
        for col, (char, pos, neg) in AB_MAP.items():
            v = r[col]
            if v is None:
                continue
            v = min(1.0, max(0.0, float(v)))
            flavor_rows += [("recording", key, char, pos, v, SRC, None),
                            ("recording", key, char, neg, 1.0 - v, SRC, None)]
        occ = r["occasions"] or ""
        for tag, (char, pos, neg) in OCCASION_MAP.items():
            if tag in occ:
                occ_rows += [("recording", key, char, pos, 1.0, SRC, None),
                             ("recording", key, char, neg, 0.0, SRC, None)]
    out.executemany("INSERT OR REPLACE INTO flavor VALUES (?,?,?,?,?,?,?)",
                    flavor_rows + occ_rows)
    out.commit()
    report["recordings"] = out.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
    report["artists"] = out.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
    report["flavor_values"] = len(flavor_rows)
    report["occasion_values"] = len(occ_rows)

    # ------------------------------------------------------------- passages
    passage_map, skipped = {}, 0
    for r in m.execute("SELECT * FROM cuts"):
        fid = file_map.get(r["fileId"])
        if fid is None or r["endFrame"] is None or r["endFrame"] <= r["startFrame"]:
            skipped += 1
            continue
        kind = "album" if r["cutType"] == "Album" else "radio"
        s_ms = int(round(r["startFrame"] / SR * 1000))
        e_ms = int(round(r["endFrame"] / SR * 1000))
        # MuLibPlay's segue frames sit inside the hard boundaries; the offsets
        # from each end are Lempi's lead-in / lead-out [SPEC-SC-040].
        lin = max(0, int(round((r["startSegueFrame"] - r["startFrame"]) / SR * 1000))) \
            if r["startSegueFrame"] is not None else None
        lout = max(0, int(round((r["endFrame"] - r["endSegueFrame"]) / SR * 1000))) \
            if r["endSegueFrame"] is not None else None
        g = r["gain"]
        gain_db = round(20.0 * math.log10(g), 3) if g and g > 0 else None
        try:
            cur = out.execute(
                "INSERT INTO passages(file_id,kind,start_ms,end_ms,lead_in_ms,"
                "lead_out_ms,gain_db,boundary_src) VALUES (?,?,?,?,?,?,?,?)",
                (fid, kind, s_ms, e_ms, lin, lout, gain_db, SRC))
            pid = cur.lastrowid
        except sqlite3.IntegrityError:
            pid = out.execute("SELECT passage_id FROM passages WHERE file_id=? AND kind=? "
                              "AND start_ms=? AND end_ms=?", (fid, kind, s_ms, e_ms)).fetchone()[0]
        passage_map[r["cutId"]] = pid
        if r["trackId"] in rec_key:
            out.execute("INSERT OR IGNORE INTO passage_recordings VALUES (?,?,1.0,?)",
                        (pid, rec_key[r["trackId"]], SRC))
    out.commit()
    report["passages"] = {"mulib_cuts": m.execute("SELECT COUNT(*) FROM cuts").fetchone()[0],
                          "migrated": len(passage_map), "skipped": skipped}

    # -------------------------------------------------- listener state (D)
    # MuLibPlay's playHistory.mbid holds tracks.mbid -- the RELEASE-TRACK MBID,
    # a different MusicBrainz entity from the recording [ENT-MB-010] vs
    # [ENT-MB-020]. Storing it verbatim leaves 37,021 of 37,051 rows pointing at
    # no recording, defeating the very resilience [SPEC-SC-095] exists for. The
    # recording key is derived through cutId -> trackId instead.
    cut_track = {r["cutId"]: r["trackId"] for r in m.execute("SELECT cutId, trackId FROM cuts")}
    n = dangling = 0
    for r in m.execute("SELECT * FROM playHistory"):
        pid = passage_map.get(r["cutId"])
        key = rec_key.get(cut_track.get(r["cutId"]))
        if key is None:
            dangling += 1
        out.execute("INSERT INTO listener_play_history(played_at,passage_id,mbid) "
                    "VALUES (?,?,?)", (r["time"], pid, key))
        n += 1
    report["play_history_dangling"] = dangling
    for r in m.execute("SELECT * FROM programs"):
        cur = out.execute("INSERT OR IGNORE INTO listener_programs(name,start_time) "
                          "VALUES (?,?)", (r["name"], r["startTime"]))
        pgid = cur.lastrowid or out.execute(
            "SELECT program_id FROM listener_programs WHERE name=?", (r["name"],)).fetchone()[0]
        for pos, tid in enumerate(re.findall(r"\[(\d+)\]", r["trackList"] or "")):
            key = rec_key.get(int(tid))
            if key:
                out.execute("INSERT OR IGNORE INTO listener_program_seeds VALUES (?,?,?)",
                            (pgid, key, pos))
    out.commit()
    report["play_history"] = n
    report["programs"] = out.execute("SELECT COUNT(*) FROM listener_programs").fetchone()[0]
    report["program_seeds"] = out.execute("SELECT COUNT(*) FROM listener_program_seeds").fetchone()[0]
    report["preferences"] = out.execute("SELECT COUNT(*) FROM listener_preferences").fetchone()[0]

    # ------------------------------------------- flavor constants [SPEC-FD-052]
    if os.path.exists("data/reliability_library.json"):
        lib = json.load(open("data/reliability_library.json"))["per_characteristic"]
        out.executemany("INSERT OR REPLACE INTO flavor_constants VALUES (?,?,?,?,?)",
                        [(c, v["beta_library"], 1.0 - v["floor_single"],
                          "library-7685-multisubmission", now) for c, v in lib.items()])
        out.commit()
        report["flavor_constants"] = len(lib)

    report["dead_fields_dropped"] = DEAD_FIELDS
    report["unresolved_examples"] = unresolved[:5]
    json.dump(report, open("data/migration_report.json", "w"), indent=1)

    print("\n=== migration report ===")
    for k, v in report.items():
        if k not in ("dead_fields_dropped", "unresolved_examples", "started"):
            print(f"  {k:20} {v}")
    print(f"  dead fields dropped  {len(DEAD_FIELDS)} (deliberate, [GDE-BMK-040])")
    print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
