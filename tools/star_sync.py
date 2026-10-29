#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The routine star sync, and the hub's own backup [SPEC-STAR-085..087].

One command per stage, each working in a dated run folder under the plan's
`runs`, so a stage can be read, re-run or stopped between. Every node is a
member player, reached by signed requests [SPEC-STAR-100]; ssh only starts and
stops a player kept on demand [SPEC-STAR-101], and carries the hub's backup to
its mirror.

    python tools/star_sync.py PLAN snapshot [--nodes A,B]
                                                      a new run: the hub's pair here, and the shared
                                                      tables and catalogue summary of the nodes chosen
                                                      (all, if none are); a node not chosen is left
                                                      alone, untouched [SPEC-STAR-130]
    python tools/star_sync.py PLAN merge              the hub's pair and each node's copy, and the
                                                      items for a verdict [SPEC-STAR-110]
    python tools/star_sync.py PLAN items              each item, and where its verdict stands
    python tools/star_sync.py PLAN verdict ID V       one verdict: approved | chose:NODE
    python tools/star_sync.py PLAN approve-all        every item still without one takes its default
    python tools/star_sync.py PLAN patch              every patch, each proven (merging again if a
                                                      verdict changed an outcome)
    python tools/star_sync.py PLAN rehearse [NODE..]  read-only, against the live files
    python tools/star_sync.py PLAN commit [NODE..]    for real: the hub, then the nodes; with names, only
                                                      those, and the hub (this machine) always takes part,
                                                      named or not [SPEC-STAR-134]
                                                      [--approve-all] [--console-pid N]
    python tools/star_sync.py PLAN backup             the hub's daily backup, and its mirror
    python tools/star_sync.py PLAN mirror             the mirror brought up to date, if it is reachable
    python tools/star_sync.py PLAN status             exits 1 when a backup has gone stale
    python tools/star_sync.py PLAN adopt NODE --library /path/on/node/library.db [--force] [--tmp DIR]
                                                      a node whose catalogue was seeded by hand: its own
                                                      catalogue, copied on it, becomes the copy last sent,
                                                      so the next sync can patch it [SPEC-STAR-946]

`merge`, `items`, `verdict`, `approve-all`, `patch`, `rehearse` and `commit` act
on the newest run unless `--run DIR` names one. Nothing is committed until a
person has read the run's SUMMARY.md and the merge's report.md, every item has
a verdict, the patches are proven and each node has been rehearsed clean
[SPEC-STAR-070, SPEC-STAR-116].

The plan is the fleet's real roster, so it lives untracked in `fleet/`;
`fleet-example/star-plan.json` shows its shape. The run's state -- what was
last sent to each node -- is `<runs>/state.json`.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import gc
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import star_distribute as sd  # noqa: E402  -- ssh, for a player's start and stop and the mirror
import star_merge as sm  # noqa: E402
import star_patch as sp  # noqa: E402
import mesh as meshmod  # noqa: E402  -- the signed transport, and phones by upload [REQ-AND-330]
import bundle_ledger  # noqa: E402  -- what a bundle send touched on a node [SPEC-STAR-945]
import transfer_hold  # noqa: E402  -- a shared radio pauses for a transfer [PI3-AIM-170]


SSH = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=5",
       "-o", "ServerAliveCountMax=6"]


def say(msg):
    enc = sys.stdout.encoding or "utf-8"
    print(str(msg).encode(enc, "replace").decode(enc), flush=True)


def at_root(p):
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def now():
    return dt.datetime.now(dt.timezone.utc)


def stamp(t=None):
    return (t or now()).strftime("%Y%m%dT%H%MZ")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def seal(path):
    os.chmod(path, 0o444)


def quiet_prints(db):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sp.fingerprint(db)
    return buf.getvalue()


def local_backup(src, dst):
    """The backup API, here: a consistent copy of a file something may be using."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    s = sp.open_current(src)
    d = sqlite3.connect(dst)
    s.backup(d)
    ok = d.execute("PRAGMA integrity_check").fetchone()[0]
    d.close()
    s.close()
    if ok != "ok":
        raise SystemExit(f"the copy of {src} fails integrity_check: {ok}")


# ------------------------------------------------------------------ state --
def load_state(plan):
    p = os.path.join(at_root(plan["runs"]), "state.json")
    if not os.path.exists(p):
        raise SystemExit(f"{p} missing: it names what each node was last sent, and the "
                         "first routine sync needs it seeded from the last distribution")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def save_state(plan, state):
    p = os.path.join(at_root(plan["runs"]), "state.json")
    tmp = p + ".new"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.replace(tmp, p)


RUN_NAME = re.compile(r"^\d{8}T\d{4}Z$")


def run_names(runs):
    """The runs under `runs`, oldest first: the folders named by a run's stamp. Others
    live there too -- `adopted/` [SPEC-STAR-946] -- and are never the newest run."""
    if not os.path.isdir(runs):
        return []
    return sorted(d for d in os.listdir(runs) if RUN_NAME.match(d) and os.path.isdir(os.path.join(runs, d)))


def newest_run(plan, given=None):
    if given:
        return at_root(given)
    runs = at_root(plan["runs"])
    found = run_names(runs)
    if not found:
        raise SystemExit(f"no run under {runs}: `snapshot` makes one")
    return os.path.join(runs, found[-1])



# --------------------------------------------------------------- the players --
# [SPEC-STAR-100]: every node is a member player, reached by signed requests.
# What the merge shares, and what a node keeps for itself, as lempi-core names
# them (held equal by test_star_merge).
SIGNED_TABLES = sorted(t for t, s in sm.TABLES.items() if s["rule"] in (sm.LWW, sm.UNION))
# [SPEC-NKP-050]: a node's own columns. `file_tags.scanned_at` is when *this* machine's tag scan
# ran: the hub and a node scan the same audio independently, so two rows alike in every tag differ
# in it, and the strict check refused a row the node already held (found 2026-10-05).
SIGNED_EXCLUDE = {"files": sorted(sm.MACHINE_SCOPE["files"]), "file_tags": ["scanned_at"]}
# [SPEC-NKP-082]: the hub's own bookkeeping, which no node needs and which travels in neither direction.
# `caa_asked_at` is when Vipunen last asked the Cover Art Archive; only `fetch_cover_art.py` reads it.
SIGNED_OMIT = {"cover_art": ["caa_asked_at"]}
NATURAL_KEYS_NEEDED = 1    # [SPEC-NKP-075]: the catalogue patch by natural key
CATALOGUE_WINDOW_NEEDED = 1  # [SPEC-STAR-104]: a player that opens a read-only catalogue's partition to rehearse a patch too
CATALOGUE_PATCH_MAX = 16 << 20   # [SPEC-NKP-920]: the most a player is sent in one request, in bytes of JSON
PART_MAX = 8 << 20               # [SPEC-NKP-930]: the most JSON in one part of a patch sent in parts
NATURAL_PART_MAX = 32 << 20      # the natural entries stay whole in the first part; the player takes no more than this
PARTS_MAX = 400                  # and no more parts than this
CATALOGUE_TOTAL_MAX = 1 << 30    # a patch past this is not a patch: say so
START_WAIT_S = 90          # [SPEC-STAR-101]: how long an on-demand player has to answer


def mesh_of(plan):
    return meshmod.mesh_dir(at_root(plan["hub"]["library"]))


def db_of(export, path):
    """A database from `mesh_sync::export`'s shape. A table the export gives
    no creating statement for -- the catalogue summary's -- is made from its
    columns, keyed by its first."""
    c = sqlite3.connect(path)
    try:
        for t in export["tables"]:
            for s in t.get("create") or [f"CREATE TABLE {t['name']} ({', '.join(t['columns'])}, "
                                         f"PRIMARY KEY ({', '.join(t['key'])}))"]:
                c.execute(s)
            c.executemany(f"INSERT INTO {t['name']} ({', '.join(t['columns'])}) VALUES "
                          f"({', '.join('?' * len(t['columns']))})",
                          [[sp.dec(v) for v in r] for r in t["rows"]])
        c.commit()
    finally:
        c.close()


# What a node's catalogue summary covers [SPEC-NSH-060]: every file and passage, by
# the node's own ids and the columns that identify them. The natural keys are the
# ones that cross installations [SPEC-DF-035]: a file by its `audio_md5`, a passage
# by (`audio_md5`, kind, start, end).
SUMMARY_COLS = {"files": ["file_id", "audio_md5"], "passages": ["passage_id", "file_id", "kind", "start_ms", "end_ms"]}


def not_whole(ic):
    """The files a node found not whole: damaged, missing or unreadable --
    withheld there. A re-tag is a finding too, but the audio is intact and it
    plays [GDE-INT-135], so it is shown apart (`retagged`) and not counted."""
    return [x for x in ic.get("not_ok", []) if x.get("outcome") != "retagged"]


def retagged(ic):
    return [x for x in ic.get("not_ok", []) if x.get("outcome") == "retagged"]


def integrity_words(ic):
    """A node's file checks in one line [GDE-INT-145]: how much of its
    catalogue it has read back, since when, how many are not whole --
    withheld from playing there, and sent again by the Export page -- and how
    many were re-tagged. A report the node could not read is said as that
    [GDE-INT-020]; a count it could not make, as unknown."""
    if ic.get("error"):
        return f"its file checks could not be read ({ic['error']})"
    bad, tags = not_whole(ic), retagged(ic)
    if ic.get("rate_kib_per_s") == 0:
        head = "it does not read its files back (switched off)"
    else:
        of = f" of {ic['files']}" if ic.get("files") is not None else " (its catalogue could not be counted)"
        head = f"{ic.get('checked', 0)}{of} files read back" + (
            f" since {str(ic['oldest'])[:10]}" if ic.get("oldest") else "")
    head += (f"; {len(bad)} NOT WHOLE, withheld there -- send them again from the Export page" if bad
             else "; all whole")
    head += f"; {len(tags)} re-tagged (audio intact, still plays)" if tags else ""
    # [REQ-VIS-180]: files with no tags, so no album name there. Said, not stale:
    # the node fills them at its next import, sync or start that can write.
    n = ic.get("untagged")
    return head + (f"; {n} without tags (no album name there until its catalogue is next written)" if n else "")


def summary_rows(db):
    """(rows by id, natural keys) of the files and passages a summary or a catalogue holds."""
    c = sp.open_ro(db)
    try:
        by_id = {t: {tuple(r) for r in c.execute(f"SELECT {', '.join(cols)} FROM {t}")}
                 for t, cols in SUMMARY_COLS.items()}
        nat = {"files": {r[0] for r in c.execute("SELECT audio_md5 FROM files")},
               "passages": {tuple(r) for r in c.execute(
                   "SELECT f.audio_md5, p.kind, p.start_ms, p.end_ms FROM passages p JOIN files f USING (file_id)")}}
    finally:
        c.close()
    return by_id, nat


CATALOGUE_STATES = {
    "as-sent": "as last sent",
    "holds-hub-rows": "holds the hub's new rows already",
    "other-ids": "the hub's music under other ids",
    "changed": "changed on the node",
    "no-copy": "no copy was last sent",
}


def catalogue_state(sent_lib, summary_db, hub_lib):
    """[SPEC-STAR-940]: where a node's catalogue stands against what the hub last
    sent it and what the hub holds now, as far as the summary shows.

    `as-sent` and `holds-hub-rows` can take the strict patch: every row the node
    has is one the patch expects or one it already makes. `other-ids` cannot, and
    is the common case (found 2026-10-05): the node holds the hub's own music,
    which a bundle send imported under the node's own ids. `changed` is a real
    difference. The player's own rehearsal is the check that decides; this says
    what to expect, and why."""
    if not sent_lib:
        return {"state": "no-copy"}
    node_id, node_nat = summary_rows(summary_db)
    sent_id, sent_nat = summary_rows(sent_lib)
    if node_id == sent_id:
        return {"state": "as-sent"}
    hub_id, hub_nat = summary_rows(hub_lib)
    if all(node_id[t] <= sent_id[t] | hub_id[t] for t in SUMMARY_COLS):
        return {"state": "holds-hub-rows"}
    added = {t: node_nat[t] - sent_nat[t] for t in node_nat}
    gone = {t: sent_nat[t] - node_nat[t] for t in node_nat}
    if all(added[t] <= hub_nat[t] for t in added) and not any(gone.values()):
        # What differs is how the node numbers music the hub also holds: the rows it holds that neither
        # the copy last sent nor the hub's own has, by id -- the 49 files and 338 passages of a bundle send.
        return {"state": "other-ids", **{t: len(node_id[t] - sent_id[t] - hub_id[t]) for t in node_id}}
    detail = {"files": len(added["files"]), "passages": len(added["passages"])}
    return {"state": "changed", **detail, "gone_files": len(gone["files"]), "gone_passages": len(gone["passages"]),
            "not_the_hubs": sum(len(added[t] - hub_nat[t]) for t in added)}


# -------------------------------------------- the node's own rows [SPEC-STAR-945] --
#
# A bundle send writes a node's catalogue outside the sync, so the last-sent copy
# the patch is made against does not hold what it wrote. A row the hub then changes
# reaches the node as an insert it refuses; a row the hub removes does not reach it
# at all -- found 2026-10-14, when two re-linked discs would have kept their wrong
# credits beside the right ones [SPEC-STAR-940]. So the snapshot asks the node for
# its own rows about the files a send touched, and the baseline holds those instead
# of what the hub predicted. The node's own strict rehearsal is still the proof.

# Each catalogue table in the scope, and how its rows are chosen: by file, by
# passage, by recording, by release or by artist. Parents before children.
ROW_SCOPE = [("files", "file", "audio_md5"), ("passages", "file_id", "file_id"), ("file_tags", "file_id", "file_id"),
             ("passage_recordings", "passage", "passage_id"), ("id_checks", "passage", "passage_id"),
             ("file_releases", "file", "audio_md5"), ("file_art", "file", "audio_md5"),
             ("recordings", "recording", "mbid"), ("recording_artists", "recording", "mbid"),
             ("release_recordings", "recording", "mbid"), ("flavor", "recording", "subject_id"),
             ("releases", "release", "mbid"), ("cover_art", "release", "release_mbid"), ("artists", "artist", "mbid")]


def _has(c, t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (t,)).fetchone() is not None


def _in(c, sql, values):
    """`sql` with one `({})` filled by `values`, in chunks; every first column."""
    values, out = list(values), []
    for i in range(0, len(values), 500):
        chunk = values[i:i + 500]
        out += [r[0] for r in c.execute(sql.format(",".join("?" * len(chunk))), chunk)]
    return out


def files_to_ask(summary_db, sent_lib, touched):
    """The files whose rows the node is asked for: the ones it holds that the copy last
    sent to it does not -- a bundle brought them -- the ones a send has touched since
    (`bundle_ledger`), held or not, and the ones both hold whose passages differ.

    The last is a boundary decision the node applied itself, from the shared review
    tables, as the hub did: no bundle carried it, so neither of the others names the
    file. Found 2026-10-29: five such passages, the same edit on hub and node, held
    bose, lp3-wifi and smartboardpc at *changed on the node* for two weeks, and with
    them every cover the hub corrected [SPEC-STAR-945]."""
    span = ("SELECT f.audio_md5, p.kind, p.start_ms, p.end_ms "
            "FROM passages p JOIN files f USING (file_id)")
    s = sqlite3.connect(f"file:{summary_db}?mode=ro", uri=True)
    b = sqlite3.connect(f"file:{sent_lib}?mode=ro", uri=True)
    try:
        held = {r[0] for r in s.execute("SELECT audio_md5 FROM files")}
        sent = {r[0] for r in b.execute("SELECT audio_md5 FROM files")}
        moved = {r[0] for r in set(s.execute(span)) ^ set(b.execute(span))} & held & sent
    finally:
        s.close()
        b.close()
    return sorted((held - sent) | moved | set(touched))


def rows_wanted(files, *libs):
    """What to ask about `files`: them, and the recordings, releases and artists any of
    `libs` -- the copy last sent, and the hub -- links to them. The node widens it by
    what it links itself, so the scope covers a row on either side."""
    rec, rel, art = set(), set(), set()
    conns = [sqlite3.connect(f"file:{p}?mode=ro", uri=True) for p in libs]
    try:
        for c in conns:
            fids = _in(c, "SELECT file_id FROM files WHERE audio_md5 IN ({})", files)
            pids = _in(c, "SELECT passage_id FROM passages WHERE file_id IN ({})", fids)
            rec |= set(_in(c, "SELECT DISTINCT mbid FROM passage_recordings WHERE passage_id IN ({})", pids))
        for c in conns:
            if _has(c, "file_releases"):
                rel |= set(_in(c, "SELECT release_mbid FROM file_releases WHERE audio_md5 IN ({})", files))
            if _has(c, "release_recordings"):
                rel |= set(_in(c, "SELECT DISTINCT release_mbid FROM release_recordings WHERE chosen = 1 "
                                  "AND mbid IN ({})", rec))
            if _has(c, "recording_artists"):
                art |= set(_in(c, "SELECT DISTINCT artist_mbid FROM recording_artists WHERE mbid IN ({})", rec))
    finally:
        for c in conns:
            c.close()
    return {"files": sorted(files), "recordings": sorted(rec), "releases": sorted(rel), "artists": sorted(art)}


def baseline_from_rows(sent_lib, hub_lib, answer, out):
    """The baseline a node is patched against: the copy last sent to it, with every row
    in `answer`'s scope replaced by the node's own (`mesh_sync::catalogue_rows`), each id
    named as the hub names it -- a file by its `audio_md5`, a passage by its span
    [SPEC-DF-035] -- so the patch pairs it with the hub's row. A blob comes as its
    SHA-256 and is matched to the hub's or the copy's value; one that matches neither is
    left as the copy had it, and counted. Returns what it did."""
    shutil.copyfile(sent_lib, out)
    os.chmod(out, 0o644)
    scope = {k: set(v) for k, v in answer["scope"].items()}
    node = {t["name"]: t for t in answer["tables"]}
    b = sqlite3.connect(out)
    h = sqlite3.connect(f"file:{hub_lib}?mode=ro", uri=True)
    report = {"files": len(scope["files"]), "rows": {}, "blobs_unmatched": 0, "tables_absent": []}
    try:
        def rows_of(t):
            if t not in node:
                return []
            return [dict(zip(node[t]["columns"], r)) for r in node[t]["rows"]]
        nfile = {d["file_id"]: d["audio_md5"] for d in rows_of("files")}
        npass = {d["passage_id"]: (nfile[d["file_id"]], d["kind"], d["start_ms"], d["end_ms"]) for d in rows_of("passages")}

        def ids(c):
            f = {m: i for i, m in c.execute("SELECT file_id, audio_md5 FROM files")}
            p = {(m, k, s, e): i for i, m, k, s, e in c.execute(
                "SELECT p.passage_id, f.audio_md5, p.kind, p.start_ms, p.end_ms FROM passages p JOIN files f USING (file_id)")}
            return f, p
        hf, hp = ids(h)
        bf, bp = ids(b)
        nxt = {"file": max(list(hf.values()) + list(bf.values()) + [0]) + 1,
               "passage": max(list(hp.values()) + list(bp.values()) + [0]) + 1}

        def name(kind, key, hub, base):
            if key in hub:
                return hub[key]
            if key in base:
                return base[key]
            hub[key] = nxt[kind]                 # a row neither holds: a new id, the same each time it is named
            nxt[kind] += 1
            return hub[key]

        # The copy's own rows in scope, kept for a blob that matches nothing the node sent.
        bfids = [bf[m] for m in scope["files"] if m in bf]
        bpids = _in(b, "SELECT passage_id FROM passages WHERE file_id IN ({})", bfids)
        by = {"file": scope["files"], "file_id": bfids, "passage": bpids, "recording": scope["recordings"],
              "release": scope["releases"], "artist": scope["artists"]}
        kept = {}
        for t, kind, col in ROW_SCOPE:
            if not _has(b, t):
                continue
            extra = " AND subject_kind = 'recording'" if t == "flavor" else ""
            cols = sp.columns(b, t)
            if t in ("cover_art", "file_art"):
                for chunk in [list(by[kind])[i:i + 500] for i in range(0, len(by[kind]), 500)]:
                    for r in b.execute(f"SELECT {', '.join(cols)} FROM {t} WHERE {col} IN ({','.join('?' * len(chunk))})",
                                       chunk):
                        kept[(t, r[cols.index(col)])] = dict(zip(cols, r))
            vals = list(by[kind])
            for i in range(0, len(vals), 500):
                chunk = vals[i:i + 500]
                b.execute(f"DELETE FROM {t} WHERE {col} IN ({','.join('?' * len(chunk))}){extra}", chunk)

        def blob(t, col, d, v):
            if t not in ROW_KEY:
                return None                      # no candidates known for this table: left as the copy had it
            want = v["sha256"]
            cands = []
            hrow = h.execute(f"SELECT {col} FROM {t} WHERE {ROW_KEY[t]} = ?", (d[ROW_KEY[t]],)).fetchone() if _has(h, t) else None
            if hrow and hrow[0] is not None:
                cands.append(hrow[0])
            krow = kept.get((t, d[ROW_KEY[t]]))
            if krow and krow.get(col) is not None:
                cands.append(krow[col])
            return next((c for c in cands if hashlib.sha256(c).hexdigest() == want), None)

        for t, _kind, _col in ROW_SCOPE:
            rows = rows_of(t)
            if t not in node:
                continue
            if not _has(b, t):
                report["tables_absent"].append(t)
                continue
            have = set(sp.columns(b, t))
            n = 0
            for d in rows:
                if t == "files":
                    d["file_id"] = name("file", d["audio_md5"], hf, bf)
                elif t in ("passages",):
                    d["passage_id"] = name("passage", npass[d["passage_id"]], hp, bp)
                    d["file_id"] = name("file", nfile[d["file_id"]], hf, bf)
                elif t == "file_tags":
                    d["file_id"] = name("file", nfile[d["file_id"]], hf, bf)
                elif t in ("passage_recordings", "id_checks"):
                    d["passage_id"] = name("passage", npass[d["passage_id"]], hp, bp)
                unmatched = False
                for c, v in list(d.items()):
                    if isinstance(v, dict) and "sha256" in v:
                        d[c] = blob(t, c, d, v)
                        unmatched = unmatched or d[c] is None
                if unmatched:
                    report["blobs_unmatched"] += 1
                    d = kept.get((t, d.get(ROW_KEY.get(t))))   # as the copy had it: the rehearsal decides
                    if d is None:
                        continue
                cols = [c for c in d if c in have]
                b.execute(f"INSERT OR REPLACE INTO {t} ({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                          [sp.dec(d[c]) for c in cols])
                n += 1
            report["rows"][t] = n
        b.commit()
        ok = b.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise SystemExit(f"the baseline built from the node's rows fails integrity_check: {ok}")
    finally:
        b.close()
        h.close()
    return report


ROW_KEY = {"cover_art": "release_mbid", "file_art": "audio_md5"}
CATALOGUE_ROWS_NEEDED = 1    # [SPEC-STAR-945]: a player that answers `rows`


def node_rows(mdir, player, run, name, snap, sent_lib, nd, hub_copy, hub_library, last_sent_at):
    """[SPEC-STAR-945]: the baseline `name` is patched against -- the copy last sent to
    it, corrected by its own rows for the files a bundle send touched -- and a line to
    say what was done. `(None, line)` keeps the copy as it is; the line says why, and a
    case that could make the rehearsal refuse is said as a warning [GDE-DEP-060]."""
    if not sent_lib:
        return None, None
    touched = bundle_ledger.touched_since(hub_library, name, last_sent_at)
    files = files_to_ask(os.path.join(nd, "summary.db"), sent_lib, touched)
    if not files:
        return None, None
    if (snap.get("catalogue_rows", 0) or 0) < CATALOGUE_ROWS_NEEDED:
        return None, (f"WARNING: {len(files)} file(s) here came or changed by bundle since the last sync, and this "
                      "build cannot report its own rows for them: patched against what was last sent, so a row "
                      "the hub has changed since may refuse, and one it removed is not removed -- deploy a current "
                      "build [SPEC-STAR-945]")
    want = rows_wanted(files, sent_lib, hub_copy)
    try:
        answer = meshmod.sync_step(mdir, player, "rows", stamp_of(run), extra={"want": want}, timeout=600.0)
        out = os.path.join(nd, "library.baseline.db")
        rep = baseline_from_rows(sent_lib, hub_copy, answer, out)
    except (ValueError, OSError, KeyError) as err:
        return None, (f"WARNING: asked for its own rows for {len(files)} file(s) and could not use them ({err}): "
                      "patched against what was last sent [SPEC-STAR-945]")
    seal(out)
    rows = sum(rep["rows"].values())
    line = (f"its own rows for {len(files)} file(s) a bundle brought or touched -- {rows} row(s), "
            f"{len(want['recordings'])}+ recording(s) -- are the baseline for them [SPEC-STAR-945]")
    if rep["blobs_unmatched"]:
        line += f"; {rep['blobs_unmatched']} cover(s) matched neither the hub's nor the last sent, and are left as sent"
    if rep["tables_absent"]:
        line += f"; not in the last-sent copy, so not corrected: {', '.join(rep['tables_absent'])}"
    return out, line


def catalogue_words(st):
    """A catalogue state as a person reads it."""
    text = CATALOGUE_STATES[st["state"]]
    if st["state"] in ("other-ids", "changed"):
        text += f" ({st['files']} files, {st['passages']} passages)"
    return text


class PartsUnsupported(Exception):
    """A catalogue patch the hub will not send, even in parts, and why."""


def json_size(x):
    return len(json.dumps(x, separators=(",", ":")))


def split_catalogue(cat, limit=None):
    """[SPEC-NKP-930]: a catalogue patch cut into parts of at most `limit` bytes of JSON.

    The natural entries stay whole in the first part, so the node's check, which sees
    the database as it was before the patch, still sees every one of them. Every other
    table is cut by rows, in table order, and packed; a table's `add_columns` and `create`
    ride only in the first piece that names it, which the node applies before the rest."""
    limit = limit or PART_MAX
    natural = [e for e in cat["tables"] if "natural" in e]
    plain = [e for e in cat["tables"] if "natural" not in e]
    nat_bytes = sum(json_size(e) for e in natural)
    if nat_bytes > NATURAL_PART_MAX:
        raise PartsUnsupported(f"the tables that hold ids come to {nat_bytes / 1048576:.0f} MB, more than the "
                               f"{NATURAL_PART_MAX >> 20} MB one part may be, and cannot be cut: the node checks them together")
    pieces = []
    for e in plain:
        head = {k: v for k, v in e.items() if k != "rows"}
        budget = max(limit - json_size(head), 1)       # the table's header rides in every piece, so it counts
        chunk, chunk_bytes, first = [], 0, True

        def piece(rows, first):
            d = dict(head, rows=rows)
            if not first:
                d.pop("add_columns", None)
                d.pop("create", None)
            return d

        for r in e["rows"]:
            rb = json_size(r)
            if rb > NATURAL_PART_MAX:
                raise PartsUnsupported(f"one row of {e['name']} is {rb / 1048576:.0f} MB, more than a part may be")
            if chunk and chunk_bytes + rb > budget:
                pieces.append(piece(chunk, first))
                chunk, chunk_bytes, first = [], 0, False
            chunk.append(r)
            chunk_bytes += rb
        if chunk or first:                      # a table with only a schema change is still one piece
            pieces.append(piece(chunk, first))
    parts = [{"tables": natural}] if natural else []
    cur, cur_bytes = [], 0
    for pc in pieces:
        pb = json_size(pc)
        if cur and cur_bytes + pb > limit:
            parts.append({"tables": cur})
            cur, cur_bytes = [], 0
        cur.append(pc)
        cur_bytes += pb
    if cur:
        parts.append({"tables": cur})
    if len(parts) > PARTS_MAX:
        raise PartsUnsupported(f"{len(parts)} parts, more than the {PARTS_MAX} a node takes")
    return parts


def write_parts(pd, parts):
    """The parts as files beside the patch, and the manifest that names them with their digests."""
    for old in os.listdir(pd):
        if old.startswith("catalogue."):
            os.remove(os.path.join(pd, old))
    metas, total = [], 0
    for i, part in enumerate(parts, 1):
        name = f"catalogue.part-{i:03d}.json"
        write_json(os.path.join(pd, name), part)
        with open(os.path.join(pd, name), "rb") as fh:
            data = fh.read()
        total += len(data)
        metas.append({"file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    manifest = {"of": len(parts), "total_bytes": total, "parts": metas}
    write_json(os.path.join(pd, "catalogue.parts.json"), manifest)
    return manifest


def stage_parts(plan, run, name, player, manifest, pd):
    """[SPEC-NKP-925]: send the parts the node does not hold, and only those. What a rehearsal
    staged is on the node still, so the commit sends nothing twice."""
    mdir = mesh_of(plan)
    have = (meshmod.sync_step(mdir, player, "staged", stamp_of(run)) or {}).get("catalogue", {})
    sent = 0
    for i, meta in enumerate(manifest["parts"], 1):
        if have.get(str(i)) == meta["sha256"]:
            continue
        with open(os.path.join(pd, meta["file"]), "rb") as fh:
            text = fh.read().decode("utf-8")
        say(f"  {name}: staging part {i} of {manifest['of']} ({len(text) / 1048576:.1f} MB)")
        meshmod.sync_step(mdir, player, "stage", stamp_of(run), timeout=1800.0, extra={"part": {
            "half": "catalogue", "n": i, "of": manifest["of"], "sha256": meta["sha256"],
            "total_bytes": manifest["total_bytes"], "text": text}})
        sent += 1
    return sent


def players_up(plan, names, started):
    """[SPEC-STAR-101]: each named node's player, as the members' query finds
    it, or None. A node planned `on_demand` whose player is down is started,
    over ssh, and waited for; `started` records which, so that only those are
    stopped afterwards."""
    mdir = mesh_of(plan)
    found = meshmod.sync_players(mdir)
    # Asked again, up to twice, while a node this run wants has not answered:
    # a Wi-Fi node's answer is lost now and then however often one query
    # repeats itself [GDE-NDS-910]. lp3-wifi went MISSING from a snapshot on
    # 2026-10-15 and answered three queries in a row a minute later; lempi02w
    # missed the first of those three. Every answer heard is kept.
    wanted = {plan["nodes"][n].get("member") for n in names} - {None}
    for _ in range(2):
        if wanted <= set(found):
            break
        found = {**meshmod.sync_players(mdir), **found}
    got, waiting = {}, []
    for name in names:
        n = plan["nodes"][name]
        fp = n.get("member")
        if fp and fp in found:
            got[name] = found[fp]
        elif fp and n.get("on_demand"):
            rc, out = sd.ssh(n["host"], n["on_demand"]["start"], quiet=True)
            if rc == 0:
                say(f"  {name}: its player started, on demand")
                started[name] = True
                waiting.append(name)
            else:
                say(f"  {name}: its player could not be started: {out}")
                got[name] = None
        else:
            got[name] = None
    deadline = time.time() + START_WAIT_S
    while waiting and time.time() < deadline:
        time.sleep(5)
        found = meshmod.sync_players(mdir)
        for name in list(waiting):
            fp = plan["nodes"][name]["member"]
            if fp in found:
                got[name] = found[fp]
                waiting.remove(name)
    for name in waiting:
        got[name] = None
    return got


def old_player_banner(old):
    """[SPEC-NKP-075]: an old player is said loudly, wherever a sync meets one.
    It still syncs its listener edits; what it cannot take is a catalogue patch."""
    if not old:
        return
    say("")
    say("!" * 78)
    say(f"!!!  OLD PLAYER{'S' if len(old) > 1 else ''}: {', '.join(sorted(old))}")
    for name, why in sorted(old.items()):
        say(f"!!!    {name}: {why}")
    say("!!!  Their listener edits sync as usual. Their catalogue is sent NO patch until they run")
    say("!!!  a current build [SPEC-NKP-075]: deploy one, then take a new snapshot.")
    say("!" * 78)
    say("")


def stop_started(plan, started):
    """Stop what this stage started, and only that [SPEC-STAR-101]."""
    for name in sorted(started):
        n = plan["nodes"][name]
        rc, out = sd.ssh(n["host"], n["on_demand"]["stop"], quiet=True)
        say(f"  {name}: its player stopped" if rc == 0 else
            f"  {name}: its player could NOT be stopped ({out}); stop it by hand")


# --------------------------------------------------------------- snapshot --
def history_of(sent, at, folder):
    """[SPEC-STAR-085]: what a node was last sent is what it held then -- the
    evidence of anything it has removed since [SPEC-STAR-050]."""
    os.makedirs(folder, exist_ok=True)
    if sent and os.path.exists(at_root(sent)):
        epoch = int(dt.datetime.fromisoformat(at).timestamp())
        dst = os.path.join(folder, f"listener-{epoch}.db")
        shutil.copyfile(at_root(sent), dst)
        seal(dst)
    return folder


def sent_path(entry, key):
    """The copy a node was last sent, if the state names one and it is still there."""
    p = (entry or {}).get(key)
    return at_root(p) if p and os.path.exists(at_root(p)) else None


def chosen(plan, only):
    """[SPEC-STAR-130]: the nodes a run is to take part with, validated; None is all.
    A node not chosen is not contacted, not started and not written to."""
    if only is None:
        return None
    only = set(only) - {plan["hub"]["name"]}      # the hub always takes part; it is never "chosen"
    known = set(plan["nodes"]) | {m["node"] for m in meshmod.sync_members(mesh_of(plan))}
    if not only:
        raise SystemExit("no node chosen: the hub always takes part, but a run needs at least one other node")
    unknown = sorted(only - known)
    if unknown:
        raise SystemExit(f"not nodes of this fleet: {unknown}")
    return only


def snapshot(plan, only=None):
    state = load_state(plan)
    only = chosen(plan, only)
    run = os.path.join(at_root(plan["runs"]), stamp())
    os.makedirs(run)
    say(f"run {run}")
    everyone = sorted(set(plan["nodes"]) | {m["node"] for m in meshmod.sync_members(mesh_of(plan))})
    take_part = everyone if only is None else sorted(only)
    write_json(os.path.join(run, "selected.json"), {"nodes": take_part,
                                                    "left_alone": [n for n in everyone if n not in take_part]})
    if only is not None:
        say(f"  taking part: {', '.join(take_part)}; left alone, untouched: "
            f"{', '.join(n for n in everyone if n not in take_part) or 'none'}")
    hub = plan["hub"]
    # The hub, here.
    hd = os.path.join(run, hub["name"])
    for half in ("listener", "library"):
        local_backup(at_root(hub[half]), os.path.join(hd, f"{half}.db"))
        seal(os.path.join(hd, f"{half}.db"))
    hs = state.get("hub", {})
    manifest = {"hub_library": os.path.join(hd, "library.db"), "hub_state_from": hub["name"],
                "nodes": [dict(name=hub["name"], listener=os.path.join(hd, "listener.db"),
                               taken_at=now().isoformat(), sent=sent_path(hs, "listener_sent"),
                               backups=history_of(hs.get("listener_sent"), hs.get("at", ""), os.path.join(hd, "history")))]}
    baselines = {hub["name"]: {"listener": os.path.join(hd, "listener.db"), "library": os.path.join(hd, "library.db")}}
    say(f"  {hub['name']}: both halves copied here")
    missing = {}
    # [SPEC-STAR-100]: every node, by signed request. An on-demand player is
    # started for this stage and stopped after it.
    mdir = mesh_of(plan)
    names = [n for n in sorted(plan["nodes"]) if only is None or n in only]
    started = {}
    old = {}
    try:
        up = players_up(plan, names, started)
        for name in names:
            n = plan["nodes"][name]
            ns = state["nodes"].get(name, {})
            player = up.get(name)
            if not n.get("member"):
                missing[name] = "no `member` fingerprint in the plan: it is not enrolled"
            elif player is None:
                missing[name] = "its player did not answer the members' query"
            else:
                try:
                    snap = meshmod.sync_step(mdir, player, "snapshot", stamp_of(run))
                except (ValueError, OSError) as err:
                    missing[name] = f"snapshot refused: {err} (a player too old for the sync routes answers like this)"
            if name in missing:
                say(f"  {name}: MISSING from this run -- {missing[name]}")
                continue
            nd = os.path.join(run, name)
            os.makedirs(nd)
            for part, fname in (("listener", "listener.db"), ("catalogue", "summary.db")):
                db_of(snap[part], os.path.join(nd, fname))
                seal(os.path.join(nd, fname))
            # [SPEC-STAR-940]: the catalogue is patched strictly, against what the
            # hub last sent. Whether it can be is read from the node's summary.
            cap = snap.get("natural_keys", 0) or 0
            if cap < NATURAL_KEYS_NEEDED:
                old[name] = "reports no natural_keys: a build older than the catalogue patch by natural key"
            # [SPEC-STAR-104]: a catalogue on a read-only partition (bose's, by design) is opened by
            # the player itself, for the check and the commit. A build that does not say it can
            # would be sent 14 parts and refuse at the first, so it is said now instead.
            blocked = None
            if snap.get("library_readonly"):
                if (snap.get("catalogue_window", 0) or 0) < CATALOGUE_WINDOW_NEEDED:
                    blocked = ("its catalogue is on a read-only partition and this build cannot open it to check a patch "
                               "(it reports no catalogue_window): deploy a current build")
                    old[name] = blocked
                else:
                    say(f"  {name}: its catalogue is on a read-only partition: the player opens it for the check "
                        f"and the commit, and closes it again [SPEC-STAR-104]")
            sent_lib = sent_path(ns, "library_sent")
            baseline, rows_note = node_rows(mdir, player, run, name, snap, sent_lib, nd,
                                            os.path.join(hd, "library.db"), at_root(hub["library"]), ns.get("at"))
            # The state is judged against what the node is patched against: the copy last
            # sent, corrected by its own rows where it gave them. Judged against the copy
            # alone, a node that had made the hub's own edits read *changed* and was sent
            # no catalogue at all, its rows already in hand (found 2026-10-29).
            as_sent = catalogue_state(sent_lib, os.path.join(nd, "summary.db"), os.path.join(hd, "library.db"))
            cat = (catalogue_state(baseline, os.path.join(nd, "summary.db"), os.path.join(hd, "library.db"))
                   if baseline else as_sent)
            say(f"  {name}: {sum(len(t['rows']) for t in snap['listener']['tables'])} shared row(s), "
                f"catalogue {catalogue_words(cat)}"
                + (f" (against the copy last sent alone: {catalogue_words(as_sent)})" if cat != as_sent else ""))
            if rows_note:
                say(f"  {name}: {rows_note}")
            # [GDE-INT-145]: what the node found reading its own files back.
            ic = snap.get("file_checks")
            if ic is not None:
                say(f"  {name}: {integrity_words(ic)} [GDE-INT-145]")
            manifest["nodes"].append(dict(
                name=name, listener=os.path.join(nd, "listener.db"), taken_at=now().isoformat(),
                sent=sent_path(ns, "listener_sent"),
                backups=history_of(ns.get("listener_sent"), ns.get("at", ""), os.path.join(nd, "history")),
                catalogue=os.path.join(nd, "summary.db"), files=os.path.join(nd, "summary.db")))
            baselines[name] = {"listener": os.path.join(nd, "listener.db"), "signed": n["member"],
                               "library": baseline or sent_lib, "library_sent": sent_lib,
                               "catalogue": cat, "catalogue_as_sent": as_sent, "natural_keys": cap,
                               "catalogue_blocked": blocked, "integrity": snap.get("file_checks"),
                               "catalogue_in_step": not blocked and (cat["state"] in ("as-sent", "holds-hub-rows")
                               or (cat["state"] == "other-ids" and cap >= NATURAL_KEYS_NEEDED))}
    finally:
        stop_started(plan, started)
    # [REQ-AND-330]: enrolled phones, by what each last uploaded over the
    # members' channel -- a node reached by upload rather than by request. Only
    # the shared tables travel, so only they are merged for it.
    for m in meshmod.sync_members(mdir):
        name, fp = m["node"], m["fingerprint"]
        if only is not None and name not in only:
            say(f"  {name}: left alone, as chosen")
            continue
        up = meshmod.upload_of(mdir, fp)
        if up is None:
            missing[name] = "enrolled, but has not uploaded its edits yet"
            say(f"  {name} ({m['name'] or 'unnamed'}): MISSING from this run -- nothing uploaded yet")
            continue
        nd = os.path.join(run, name)
        os.makedirs(nd)
        shutil.copyfile(up["path"], os.path.join(nd, "listener.db"))
        seal(os.path.join(nd, "listener.db"))
        # Its history is its own earlier uploads, never the merged copy it was
        # last sent: that holds tables and passage rows it never uploads, which
        # would read as rows it had removed [SPEC-STAR-050].
        hist = os.path.join(nd, "history")
        os.makedirs(hist)
        for f in sorted(os.listdir(up["uploads"])):
            if f != f"listener-{up['epoch']}.db":     # earlier uploads: what it held then
                shutil.copyfile(os.path.join(up["uploads"], f), os.path.join(hist, f))
        manifest["nodes"].append(dict(name=name, listener=os.path.join(nd, "listener.db"),
                                      taken_at=up["uploaded_at"], backups=hist,
                                      sent=sent_path(state["nodes"].get(name), "listener_sent")))
        baselines[name] = {"listener": os.path.join(nd, "listener.db"), "member": fp}
        say(f"  {name} ({m['name'] or 'unnamed'}): its upload of {up['uploaded_at']}, "
            f"clock {up['clock_offset_ms']:+d} ms, {up['rows']}")
    for name, path in (("manifest.json", manifest), ("baselines.json", baselines),
                       ("missing.json", missing), ("old_players.json", old)):
        with open(os.path.join(run, name), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(path, fh, indent=1, sort_keys=True)
    old_player_banner(old)
    say(f"RESULT snapshot: {len(baselines)} taken" + (f", MISSING {sorted(missing)}" if missing else "")
        + (f", OLD PLAYER(S) {sorted(old)}" if old else ""))
    return 0 if not missing else 3


# ------------------------------------------------------------ merge, review --
def rm(path):
    if os.path.isdir(path):
        shutil.rmtree(path, onerror=lambda f, p, e: (os.chmod(p, 0o644), f(p)))
    elif os.path.exists(path):
        os.chmod(path, 0o644)
        os.remove(path)


def void_merge(run):
    """[SPEC-STAR-114]: a merge made void by a verdict is kept beside the run,
    and with it goes everything made from it."""
    m = os.path.join(run, "merge")
    if os.path.isdir(m):
        vd = os.path.join(run, "void")
        os.makedirs(vd, exist_ok=True)
        n = 1
        while os.path.exists(os.path.join(vd, f"merge-{n}")):
            n += 1
        gc.collect()           # Windows will not move a folder holding a file something still has open
        try:
            os.replace(m, os.path.join(vd, f"merge-{n}"))
        except PermissionError:
            shutil.copytree(m, os.path.join(vd, f"merge-{n}"))
            rm(m)
    for f in ("patches", "SUMMARY.md", "distribute-plan.json", "proven.json", "rehearsal.json"):
        rm(os.path.join(run, f))


def merge(plan, run):
    with open(os.path.join(run, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    void_merge(run)
    if os.path.exists(verdicts_path(run)):
        manifest["verdicts"] = verdicts_path(run)
    return sm.run(manifest, os.path.join(run, "merge"))


def verdicts_path(run):
    return os.path.join(run, "verdicts.json")


def load_verdicts(run):
    if not os.path.exists(verdicts_path(run)):
        return {}
    with open(verdicts_path(run), encoding="utf-8") as fh:
        return json.load(fh)


def save_verdicts(run, verdicts):
    tmp = verdicts_path(run) + ".new"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(verdicts, fh, indent=1, sort_keys=True)
    os.replace(tmp, verdicts_path(run))


def merge_items(run):
    p = os.path.join(run, "merge", "items.json")
    if not os.path.exists(p):
        raise SystemExit(f"no merge in {run}: `merge` makes one")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def verdict_node(x, v):
    """The node whose value a verdict takes for item `x`."""
    return x["default"] if v["verdict"] == "approved" else v["verdict"][6:]


def review(run):
    """[SPEC-STAR-116]: where each item of the run's merge stands. `pending`
    has no verdict; `stale` has one the merge does not yet reflect; `void` are
    verdicts naming no item of this merge."""
    items = merge_items(run)["items"]
    verdicts = load_verdicts(run)
    ids = {x["id"] for x in items}
    return dict(items=items, verdicts=verdicts,
                pending=[x for x in items if x["id"] not in verdicts],
                stale=[x for x in items if x["id"] in verdicts and verdict_node(x, verdicts[x["id"]]) != x["chosen"]],
                void=sorted(set(verdicts) - ids))


def give_verdict(run, ident, value, by="item"):
    """[SPEC-STAR-114]: `approved`, or `chose:<node>` naming one of the item's candidates."""
    items = {x["id"]: x for x in merge_items(run)["items"]}
    if ident not in items:
        raise SystemExit(f"{ident} is not an item of this merge")
    x = items[ident]
    if value != "approved" and not (value.startswith("chose:") and value[6:] in {c["node"] for c in x["candidates"]}):
        raise SystemExit(f"a verdict is `approved` or `chose:NODE`, NODE one of "
                         f"{[c['node'] for c in x['candidates']]}: {value!r} is neither")
    v = load_verdicts(run)
    v[ident] = {"verdict": value, "at": now().isoformat(), "by": by}
    save_verdicts(run, v)
    return x


def approve_all(run, by="approve-all"):
    """[SPEC-STAR-118]: every item still without a verdict takes its default,
    the most recent change, and says it was approved in bulk. A verdict a
    person has given is never replaced."""
    rv = review(run)
    v = load_verdicts(run)
    for x in rv["pending"]:
        v[x["id"]] = {"verdict": "approved", "at": now().isoformat(), "by": by}
    save_verdicts(run, v)
    kinds = {}
    for x in rv["pending"]:
        kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    return kinds


def show_items(run):
    rv = review(run)
    say(f"run {os.path.basename(run)}: {len(rv['items'])} item(s), {len(rv['pending'])} without a verdict, "
        f"{len(rv['stale'])} awaiting a new merge, {len(rv['void'])} void verdict(s)")
    for x in rv["items"]:
        v = rv["verdicts"].get(x["id"])
        col = f".{x['column']}" if x.get("column") else ""
        say(f"  {x['id']} {x['kind']:14} {x['table']} {x['key']}{col}: default {x['default']}"
            + (f" -> {v['verdict']} ({v['by']})" if v else " -- NO VERDICT"))
        for c in x["candidates"]:
            say(f"      {c['node']:15} {c['stamp'] or ''}  "
                + ("removed" if c["value"] is None else json.dumps(c["value"], default=str)[:160]))
    prop = merge_items(run).get("propagated", {})
    if prop:
        say("  carried without question: " + ", ".join(f"{t} {n}" for t, n in sorted(prop.items())))
    return 0


def gate(run, names):
    """[SPEC-STAR-116]: no commit until every item has a verdict the merge
    reflects, the patches are proven and each node has been rehearsed clean."""
    problems = gate_problems(run, names)
    if problems:
        raise SystemExit("refusing to commit [SPEC-STAR-116]:\n  " + "\n  ".join(problems))


def gate_problems(run, names):
    """What stands between this run and a commit, in words; empty when nothing does."""
    problems = []
    try:
        rv = review(run)
    except SystemExit as err:
        return [str(err)]
    if rv["pending"]:
        problems.append(f"{len(rv['pending'])} item(s) have no verdict: `items`, then `verdict` or `approve-all`")
    if rv["stale"]:
        problems.append(f"{len(rv['stale'])} verdict(s) change an outcome the merge does not reflect: `patch` merges again")
    pr = os.path.join(run, "proven.json")
    if not os.path.exists(pr) or not json.load(open(pr, encoding="utf-8")).get("ok"):
        problems.append("the patches are not proven: run `patch`")
    rh = os.path.join(run, "rehearsal.json")
    done = json.load(open(rh, encoding="utf-8")) if os.path.exists(rh) else {}
    for name in names:
        if done.get(name) != 0:
            problems.append(f"{name} has no clean rehearsal: run `rehearse {name}`")
    return problems


# ------------------------------------------------------------ merge, patch --
def targets(plan, run, name):
    m = os.path.join(run, "merge")
    d = m if name == plan["hub"]["name"] else os.path.join(m, "nodes", name)
    return {"listener": os.path.join(d, "listener.db"), "library": os.path.join(d, "library.db")}


def prove(base, patch, target):
    """baseline + patch == target, every table, before the patch leaves here."""
    with tempfile.TemporaryDirectory() as t:
        copy = os.path.join(t, "proof.db")
        shutil.copyfile(base, copy)
        os.chmod(copy, 0o644)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = sp.apply(copy, patch, commit=True)
        return rc == 0 and quiet_prints(copy) == quiet_prints(target)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, separators=(",", ":"), sort_keys=True)


def values_patch(base, target):
    """A player's listener patch [SPEC-NSH-070]: the shared tables as values,
    proven here -- the member rule applied to a copy of what it sent must give
    the merge's shared tables -- before it is offered."""
    p = sp.make_values(base, target, tables=SIGNED_TABLES)
    with tempfile.TemporaryDirectory() as t:
        copy = os.path.join(t, "proof.db")
        shutil.copyfile(base, copy)
        os.chmod(copy, 0o644)
        sp.apply_member(copy, p, set(SIGNED_TABLES))
        good = sp.make_values(copy, target, tables=SIGNED_TABLES)["tables"] == []
    return p, good


def patch(plan, run):
    rv = review(run)
    if rv["stale"]:
        say(f"  {len(rv['stale'])} verdict(s) change an outcome: merging again")
        if merge(plan, run):
            return 1
    with open(os.path.join(run, "baselines.json"), encoding="utf-8") as fh:
        baselines = json.load(fh)
    for f in ("proven.json", "rehearsal.json"):
        rm(os.path.join(run, f))
    rv = review(run)
    summary = ["# Sync summary", "", f"Run `{os.path.basename(run)}`. Read this and `merge/report.md` "
               "before `commit` [SPEC-STAR-070].", ""]
    summary += [f"**Items for a verdict:** {len(rv['items'])}, {len(rv['pending'])} without one "
                "[SPEC-STAR-116].", ""]
    old = read_json(os.path.join(run, "old_players.json")) or {}
    if old:
        summary += ["**WARNING -- OLD PLAYER" + ("S" if len(old) > 1 else "") + ": " + ", ".join(sorted(old))
                    + ".** Their listener edits sync as usual. Their catalogue is sent no patch until they "
                    "run a current build [SPEC-NKP-075].", ""]
    left = (read_json(os.path.join(run, "selected.json")) or {}).get("left_alone", [])
    if left:
        summary += ["**Left alone, as chosen** -- not read, not written, not started: " + ", ".join(left)
                    + " [SPEC-STAR-130].", ""]
    with open(os.path.join(run, "missing.json"), encoding="utf-8") as fh:
        missing = json.load(fh)
    if missing:
        summary += ["**Missing from this run** -- they receive nothing until the next:", ""]
        summary += [f"- {n}: {why}" for n, why in sorted(missing.items())] + [""]
    ok = True
    members, signed = {}, {}
    hub = plan["hub"]["name"]
    for name in sorted(baselines):
        b = baselines[name]
        pd = os.path.join(run, "patches", name)
        os.makedirs(pd, exist_ok=True)
        t = targets(plan, run, name)
        summary += [f"## {name}", ""]
        if b.get("integrity") is not None:
            # [GDE-INT-145]: every file the node found not whole, named.
            ic = b["integrity"]
            summary.append(f"- its own files: {integrity_words(ic)}")
            summary += [f"  - **{x['outcome']}**: `{x.get('path') or x['audio_md5']}`"
                        for x in not_whole(ic) + retagged(ic)][:25]
        if "member" in b:
            good, line = member_patch(b["listener"], t["listener"], os.path.join(pd, "member.patch.json"))
            ok = ok and good
            summary += [line, ""]
            members[name] = b["member"]
            say(f"  {name} {line[2:]}")
        elif "signed" in b:
            lis, good = values_patch(b["listener"], t["listener"])
            ok = ok and good
            write_json(os.path.join(pd, "listener.values.json"), lis)
            rows = {e["name"]: len(e["rows"]) for e in lis["tables"]}
            summary.append("- shared edits: " + (", ".join(f"`{k}` {v}" for k, v in sorted(rows.items())) or "nothing to send")
                           + ("" if good else " -- **NOT PROVEN: do not commit**"))
            cat, unsupported, parts = None, None, None
            for old in os.listdir(pd):                # nothing of an earlier patch lingers
                if old.startswith("catalogue."):
                    os.remove(os.path.join(pd, old))
            if b.get("catalogue_in_step"):
                # A player that can apply it gets the tables that hold ids named by
                # what identifies them everywhere [SPEC-NKP-075]; any other, the
                # strict id-keyed patch, which only reaches a catalogue numbered as ours.
                natural = (b.get("natural_keys", 0) or 0) >= NATURAL_KEYS_NEEDED
                try:
                    cat = sp.make_values(b["library"], t["library"], exclude=SIGNED_EXCLUDE, natural=natural, omit=SIGNED_OMIT)
                except sp.NaturalUnsupported as err:
                    unsupported = f"the hub cannot name what changed: {err} [SPEC-NKP-080]"
            if cat is not None:
                size = json_size(cat)
                if size > CATALOGUE_PATCH_MAX:
                    # A Pi has little memory to parse a request in, and the player's own limit is 64 MB. The first
                    # patch after a long gap is mostly cover art, so it goes in parts, staged on the node and
                    # committed there in one transaction [SPEC-NKP-920].
                    try:
                        if size > CATALOGUE_TOTAL_MAX:
                            raise PartsUnsupported(f"the patch is {size / 1048576:.0f} MB, more than the "
                                                   f"{CATALOGUE_TOTAL_MAX >> 30} GB that will be sent")
                        parts = split_catalogue(cat)
                    except PartsUnsupported as err:
                        unsupported = f"the patch is {size / 1048576:.0f} MB and cannot be sent in parts: {err} [SPEC-NKP-930]"
                        cat = None
            if cat is not None and parts is not None:
                man = write_parts(pd, parts)
                crow = {e["name"]: len(e["rows"]) for e in cat["tables"]}
                summary.append("- catalogue: " + ", ".join(f"`{k}` {v}" for k, v in sorted(crow.items()))
                               + (" by natural key" if any("natural" in e for e in cat["tables"]) else "")
                               + f", {size / 1048576:.0f} MB in {man['of']} parts, staged on the node and committed there in one "
                                 "transaction [SPEC-NKP-920] (proven by the player's own rehearsal, strictly)")
            elif cat is not None:
                write_json(os.path.join(pd, "catalogue.values.json"), cat)
                crow = {e["name"]: len(e["rows"]) for e in cat["tables"]}
                summary.append("- catalogue: " + (", ".join(f"`{k}` {v}" for k, v in sorted(crow.items())) or "nothing to send")
                               + (" by natural key" if any("natural" in e for e in cat["tables"]) else "")
                               + " (proven by the player's own rehearsal, strictly)")
            elif unsupported:
                summary.append(f"- catalogue: **none sent** -- {unsupported}")
                say(f"  {name}: NO CATALOGUE PATCH -- {unsupported}")
            else:
                cs = b.get("catalogue") or {"state": "no-copy"}
                why = b.get("catalogue_blocked") or catalogue_words(cs)
                if cs["state"] == "other-ids" and not b.get("catalogue_blocked"):
                    why += (": the same music as the hub, numbered differently, which only a patch by natural key "
                            "reaches, and this player is OLD: it cannot take one" if b.get("natural_keys", 0) < NATURAL_KEYS_NEEDED
                            else ": the same music as the hub, numbered differently")
                summary.append(f"- catalogue: **none sent** -- {why} [SPEC-STAR-940]")
            say(f"  {name}: {sum(rows.values())} shared row(s), {'proven' if good else 'NOT PROVEN'}")
            signed[name] = b["signed"]
        else:                                    # the hub, whose pair is here
            for half in ("listener", "library"):
                out = os.path.join(pd, f"{half}.patch.json")
                with contextlib.redirect_stdout(io.StringIO()):
                    sp.make(b[half], t[half], out)
                with open(out, encoding="utf-8") as fh:
                    p = json.load(fh)
                rows = {e["name"]: len(e["rows"]) for e in p["tables"]}
                if not p["tables"]:
                    summary.append(f"- {half}: nothing to send")
                    continue
                good = prove(b[half], out, t[half])
                ok = ok and good
                summary.append(f"- {half}: " + ", ".join(f"`{k}` {v}" for k, v in sorted(rows.items()))
                               + ("" if good else " -- **NOT PROVEN: do not commit**"))
                say(f"  {name} {half}: {sum(rows.values())} row(s), {'proven' if good else 'NOT PROVEN'}")
        summary.append("")
    with open(os.path.join(run, "SUMMARY.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(summary) + "\n")
    write_json(os.path.join(run, "proven.json"), {"ok": ok, "at": now().isoformat()})
    dplan = {"hub": hub, "signed": signed, "members": members, "prune": plan.get("prune")}
    with open(os.path.join(run, "distribute-plan.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(dplan, fh, indent=1, sort_keys=True)
    say(f"RESULT patch: {'every patch proven' if ok else 'a patch is NOT PROVEN'}; read {run}/SUMMARY.md")
    return 0 if ok else 1


def member_patch(base, target, out):
    """A member's patch [SPEC-MTR-030]: the shared tables only, as values, and
    proven here -- the phone's own rule applied to a copy of what it uploaded
    must give the merge's shared tables -- before it is offered."""
    p = sp.make_member(base, target, meshmod.SHARED)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(p, fh, separators=(",", ":"), sort_keys=True)
    rows = {e["name"]: len(e["rows"]) for e in p["tables"]}
    if not rows:
        return True, "- shared edits: nothing to send"
    with tempfile.TemporaryDirectory() as t:
        copy = os.path.join(t, "proof.db")
        shutil.copyfile(base, copy)
        os.chmod(copy, 0o644)
        sp.apply_member(copy, p, meshmod.SHARED)
        good = sp.make_member(copy, target, meshmod.SHARED)["tables"] == []
    return good, ("- shared edits: " + ", ".join(f"`{t}` {k}" for t, k in sorted(rows.items()))
                  + ("" if good else " -- **NOT PROVEN: do not commit**"))


def member_offer(plan, run, name, fp, commit):
    """[REQ-AND-330]: a member is not reached from here. Its patch waits in
    its outbox on the members' channel until it fetches it."""
    say(f"== {name} (a phone, by its next contact) -- {'COMMIT' if commit else 'rehearsal'}")
    with open(os.path.join(run, "patches", name, "member.patch.json"), encoding="utf-8") as fh:
        p = json.load(fh)
    n = sum(len(e["rows"]) for e in p["tables"])
    if not commit:
        say(f"RESULT {name}: {n} row(s) to offer; checked by the phone's own rule when it applies them")
        return 0
    meshmod.put_outbox(meshmod.mesh_dir(at_root(plan["hub"]["library"])), fp, stamp_of(run), p)
    say(f"RESULT {name}: OFFERED -- {n} row(s) wait for it on the members' channel")
    return 0


def stamp_of(run):
    return os.path.basename(os.path.normpath(run))


# ----------------------------------------------------------- a player, signed --
def read_json(path):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def has_rows(p):
    return bool(p and p.get("tables"))


def player_step(plan, run, name, player, commit):
    """[SPEC-STAR-100]: one player's rehearsal or commit, by signed request.
    The player applies its own patch, and is not stopped."""
    mdir = mesh_of(plan)
    pd = os.path.join(run, "patches", name)
    lis = read_json(os.path.join(pd, "listener.values.json"))
    cat = read_json(os.path.join(pd, "catalogue.values.json"))
    parts = read_json(os.path.join(pd, "catalogue.parts.json"))
    say(f"== {name} (signed) -- {'COMMIT' if commit else 'rehearsal: nothing is kept'}")
    if player is None:
        say(f"RESULT {name}: REFUSED -- its player did not answer the members' query")
        return 1
    if not has_rows(lis) and not has_rows(cat) and not parts:
        say(f"RESULT {name}: nothing to send")
        return 0
    # The catalogue's parts are the bulk of a sync, megabytes to a part: with
    # them, the node's playback is held for the step [PI3-AIM-170]. Rows alone
    # are a few kilobytes, and are sent without.
    # As long as the step itself may take: its own timeout, below.
    hold = (transfer_hold.held(player["address"], player.get("web_port", transfer_hold.LEMPI_DEFAULT_PORT),
                               say=say, seconds=3600)
            if parts else contextlib.nullcontext())
    try:
        with hold:
            extra = None
            if parts:
                sent = stage_parts(plan, run, name, player, parts, pd)
                extra = {"catalogue_parts": {"of": parts["of"], "sha256": [p["sha256"] for p in parts["parts"]]}}
                say(f"  {name}: the catalogue's {parts['of']} parts are staged ({sent} sent now, "
                    f"{parts['of'] - sent} already there)")
            got = meshmod.sync_step(mdir, player, "commit" if commit else "rehearse", stamp_of(run),
                                    cat if has_rows(cat) else None, lis if has_rows(lis) else None,
                                    timeout=3600.0 if parts else 600.0, extra=extra)
    except (ValueError, OSError) as err:
        say(f"RESULT {name}: REFUSED -- {err}")
        return 1
    kept = (got.get("listener") or {}).get("kept", 0)
    note = f"; {kept} row(s) it had changed since its snapshot were kept, and travel next time" if kept else ""
    warn = got.get("warning")
    say(f"RESULT {name}: " + (f"COMMITTED {got}" if commit else f"rehearsal CLEAN {got}") + note
        + (f"; WARNING {warn}" if warn else ""))
    return 0


# ---------------------------------------------------------- the hub, here --
_INTERPRETERS = ("python", "pythonw", "py")


def hub_writers(procs, me, excuse=None):
    """Which of `procs` -- `(pid, parent_pid, name, command_line)` -- can write
    the hub's pair: a Python running `console.py`, or a `lempi` player
    [SPEC-STAR-127]. A shell or launcher whose command line merely *names*
    `console.py` cannot, and is not counted. `excuse` is the pid of a console
    that has paused its player and will reload it [SPEC-STAR-126]: it, the
    chain of processes that launched it, and the co-resident `lempi` are not
    counted; any other still is.

    Until 2026-10-14 every process whose command line matched counted. The
    console the Sync page runs from had been started by an agent's Git Bash
    shell, `bash -c "... python tools/console.py ..."`, which waits on it for
    as long as it runs -- so from 2026-10-07 every hub commit refused with
    "process(es) ['16752'] may be using the hub's pair", naming that shell."""
    parent = {pid: ppid for pid, ppid, _n, _c in procs}
    excused = set()
    if excuse:
        p = int(excuse)
        while p and p not in excused:
            excused.add(p)
            p = parent.get(p)
    found = []
    for pid, _ppid, name, cmd in procs:
        if pid == me or pid in excused:
            continue
        stem = os.path.splitext((name or "").lower())[0]
        if stem.startswith("lempi"):
            if excuse:
                continue
        elif not (stem in _INTERPRETERS and re.search(r"console\.py\b", cmd or "")):
            continue
        found.append(str(pid))
    return found


def console_running(excuse=None):
    """The Vipunen console writes to the hub's pair, and so does a player beside
    it; neither must be running while the hub is patched (`hub_writers`).
    None when this cannot be told -- said so."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, "
                            "CommandLine | ConvertTo-Json -Compress"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        rows = json.loads(r.stdout or "null")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not rows:
        return None
    rows = rows if isinstance(rows, list) else [rows]
    procs = [(int(p["ProcessId"]), int(p.get("ParentProcessId") or 0), p.get("Name") or "", p.get("CommandLine") or "")
             for p in rows if p.get("ProcessId") is not None]
    return hub_writers(procs, os.getpid(), excuse)


def hub_apply(plan, run, commit, excuse=None):
    hub = plan["hub"]
    pd = os.path.join(run, "patches", hub["name"])
    halves = [h for h in ("listener", "library") if sd.patch_rows(os.path.join(pd, f"{h}.patch.json"))]
    say(f"== {hub['name']} (here) -- {'COMMIT' if commit else 'rehearsal'}")
    if not halves:
        say(f"RESULT {hub['name']}: nothing to send")
        return 0
    if not commit:
        rc = 0
        for h in halves:
            rc |= sp.check(at_root(hub[h]), os.path.join(pd, f"{h}.patch.json"))
        say(f"RESULT {hub['name']}: rehearsal {'CLEAN' if rc == 0 else 'NOT CLEAN'}")
        return rc
    busy = console_running(excuse)
    if busy is None:
        raise SystemExit("cannot tell whether the Vipunen console is running: refusing to patch the hub")
    if busy:
        raise SystemExit(f"process(es) {busy} may be using the hub's pair: close the console first")
    if excuse:
        say(f"  run from the console (pid {excuse}), which has paused its player [SPEC-STAR-126]")
    bk = os.path.join(at_root(plan["backups"]["dir"]), f"pre-sync-{stamp_of(run)}")
    if os.path.exists(bk):
        raise SystemExit(f"{bk} exists: a backup is never written over")
    for h in halves:
        local_backup(at_root(hub[h]), os.path.join(bk, f"{h}.db"))
    say(f"  the hub's pair before this sync: {bk}")
    for h in halves:
        if sp.apply(at_root(hub[h]), os.path.join(pd, f"{h}.patch.json"), commit=False):
            raise SystemExit(f"the hub's {h} patch does not rehearse cleanly; nothing written")
    landed = []
    for h in halves:
        if sp.apply(at_root(hub[h]), os.path.join(pd, f"{h}.patch.json"), commit=True):
            for done in landed:
                sp.restore(os.path.join(bk, f"{done}.db"), at_root(hub[done]))
            raise SystemExit(f"the hub's {h} patch failed; {landed or 'nothing'} restored")
        landed.append(h)
    ok = True
    for h in ("listener", "library"):
        got = {l.split()[0]: l for l in quiet_prints(at_root(hub[h])).splitlines()}
        want = {l.split()[0]: l for l in quiet_prints(targets(plan, run, hub["name"])[h]).splitlines()}
        bad = [t for t in want if got.get(t) != want[t] and not (h == "listener" and t in sd.OWN)]
        if bad and h in halves:
            # As on a node: changed here since the snapshot, in rows the
            # patch never named, is kept and reported, not a failure.
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                sp.check(at_root(hub[h]), os.path.join(pd, f"{h}.patch.json"))
            if " 0 to apply" in buf.getvalue() or ": 0 to apply" in buf.getvalue():
                say(f"  {h}: every row the patch names holds its target; {bad} changed here "
                    "since the snapshot, and were kept")
                continue
        ok = ok and not bad
        say(f"  {h}: " + ("every governed table identical to the target" if not bad else f"DIFFERS {bad}"))
    if ok and plan.get("prune"):
        parent, keep = os.path.dirname(bk), plan["prune"]["keep"]
        mine = sorted(d for d in os.listdir(parent) if d.startswith("pre-sync-"))
        for old in mine[:-keep]:
            shutil.rmtree(os.path.join(parent, old), onerror=lambda f, p, e: (os.chmod(p, 0o644), f(p)))
            say(f"  pruned {old}")
    say(f"RESULT {hub['name']}: {'COMMITTED and verified' if ok else 'COMMITTED but a table DIFFERS'}")
    return 0 if ok else 1


def distribute(plan, run, names, commit, excuse=None, bulk=False):
    with open(os.path.join(run, "distribute-plan.json"), encoding="utf-8") as fh:
        dplan = json.load(fh)
    hub = plan["hub"]["name"]
    signed, members = dplan.get("signed", {}), dplan.get("members", {})
    names = [hub] + [n for n in (names or sorted(signed) + sorted(members)) if n != hub]
    if len(names) < 2:
        raise SystemExit("no node chosen: the hub always takes part, but a run needs at least one other node")
    old_player_banner({n: w for n, w in (read_json(os.path.join(run, "old_players.json")) or {}).items() if n in names})
    if commit:
        if bulk:
            kinds = approve_all(run)
            say("  approve-all: " + (", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "no item was waiting"))
        gate(run, names)
    state = load_state(plan) if commit else None
    done = read_json(os.path.join(run, "rehearsal.json")) or {}
    started = {}
    worst = 0
    try:
        up = players_up(plan, [n for n in names if n in signed], started) if any(n in signed for n in names) else {}
        for name in names:
            if name == hub:
                rc = hub_apply(plan, run, commit, excuse)
            elif name in members:
                rc = member_offer(plan, run, name, members[name], commit)
            elif name in signed:
                rc = player_step(plan, run, name, up.get(name), commit)
            else:
                say(f"RESULT {name}: not in this run")
                rc = 1
            if not commit:
                done[name] = rc
            if commit and rc == 0:
                t = targets(plan, run, name)
                old = state["nodes"].get(name, {}) if name != hub else state.get("hub", {})
                lib_sent = old.get("library_sent")
                pdn = os.path.join(run, "patches", name)
                if name == hub or (name in signed and (os.path.exists(os.path.join(pdn, "catalogue.values.json"))
                                                       or os.path.exists(os.path.join(pdn, "catalogue.parts.json")))):
                    lib_sent = os.path.relpath(t["library"], ROOT)
                entry = {"listener_sent": os.path.relpath(t["listener"], ROOT), "library_sent": lib_sent,
                         "at": now().isoformat(), "run": stamp_of(run)}
                if name == hub:
                    state["hub"] = entry
                else:
                    state["nodes"][name] = entry
                save_state(plan, state)
            worst = max(worst, rc)
    finally:
        stop_started(plan, started)
        if not commit:
            write_json(os.path.join(run, "rehearsal.json"), done)
    return worst


# ----------------------------------------------------------------- backup --
def retained(dates, keep_daily, keep_monthly):
    """[SPEC-STAR-086]: the newest `keep_daily` days, and the earliest day of
    each of the newest `keep_monthly` months. `dates` are YYYY-MM-DD."""
    ds = sorted(set(dates))
    keep = set(ds[-keep_daily:]) if keep_daily > 0 else set()
    firsts = {}
    for d in ds:
        firsts.setdefault(d[:7], d)
    for m in sorted(firsts)[-keep_monthly:] if keep_monthly > 0 else []:
        keep.add(firsts[m])
    return keep


MESH_FILES = ("node.key", "node.pem", "mesh.key", "roster.json")


def mesh_archive(mdir: str, dest: str) -> None:
    """The hub's mesh keys and roster [SPEC-MTR-110], as an SQLite archive:
    `sqlite3 FILE -Ax` extracts it. An SQLite file so that it is stored,
    mirrored, kept and pruned exactly as the two databases are. Deterministic,
    so an unchanged mesh is the same object and is stored once. Enrolments in
    progress are not kept: they last minutes."""
    c = sqlite3.connect(dest)
    c.execute("CREATE TABLE sqlar(name TEXT PRIMARY KEY, mode INT, mtime INT, sz INT, data BLOB)")
    for name in MESH_FILES:
        with open(os.path.join(mdir, name), "rb") as fh:
            data = fh.read()
        c.execute("INSERT INTO sqlar VALUES (?, ?, 0, ?, ?)", (name, 0o100600, len(data), data))
    c.commit()
    c.close()


# [GDE-INT-120]: each kept object re-read at least this often, here and on the
# mirror. Named by its own SHA-256, so the proof is a re-hash; until 2026-10-15
# none was ever made after the object was stored.
PROVE_DAYS = 6


def proofs(root):
    """When each object was last proven whole, here and on the mirror, and any
    that were not: `<backups>/proven.json`."""
    path = os.path.join(root, "proven.json")
    try:
        with open(path, encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        st = {}
    for k in ("local", "mirror"):
        st.setdefault(k, {})
        st.setdefault(f"{k}_bad", [])
    return st


def save_proofs(root, st):
    path = os.path.join(root, "proven.json")
    with open(path + ".part", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(st, fh, indent=1, sort_keys=True)
    os.replace(path + ".part", path)


def due(st, where, wanted):
    limit = now() - dt.timedelta(days=PROVE_DAYS)
    return sorted(o for o in wanted
                  if not st[where].get(o) or dt.datetime.fromisoformat(st[where][o]) < limit)


def prove_local(root, wanted, st):
    """Re-hash the kept objects whose proof is older than PROVE_DAYS."""
    st["local"] = {o: t for o, t in st["local"].items() if o in wanted}
    st["local_bad"] = [o for o in st["local_bad"] if o in wanted]
    todo = due(st, "local", wanted)
    for o in todo:
        f = os.path.join(root, "objects", f"{o}.db")
        got = sha256(f) if os.path.isfile(f) else None
        if got == o:
            st["local"][o] = now().isoformat()
            if o in st["local_bad"]:
                st["local_bad"].remove(o)
        elif o not in st["local_bad"]:
            st["local_bad"].append(o)
    bad = st["local_bad"]
    say(f"  proven here: {len(todo)} object(s) re-read" + (f"; {len(bad)} NOT WHOLE: "
        + ", ".join(b[:12] for b in bad) if bad else ", all whole"))


def kept_objects(plan):
    """The days kept, and every object one of them names."""
    b = plan["backups"]
    days = os.path.join(at_root(b["dir"]), "days")
    have = sorted(f[:-5] for f in os.listdir(days) if f.endswith(".json")) if os.path.isdir(days) else []
    keep = retained(have, b["keep_daily"], b["keep_monthly"])
    wanted = set()
    for d in keep:
        with open(os.path.join(days, f"{d}.json"), encoding="utf-8") as fh:
            rec = json.load(fh)
        wanted |= {rec["listener"], rec["library"]} | ({rec["mesh"]} if rec.get("mesh") else set())
    return keep, wanted


def mirror_now(plan):
    """The `mirror` command: bring the mirror up to date if it can be reached,
    and say nothing alarming if it cannot [SPEC-STAR-088]. Run every quarter
    hour by the task *Lempi hub mirror*, so a mirror that is off at 03:30 -- as
    `teacherslounge` usually is -- is written when it is next on, not never."""
    keep, wanted = kept_objects(plan)
    if not keep:
        say("  no backup to mirror yet")
        return 0
    return mirror(plan, keep, wanted)


def backup(plan):
    b = plan["backups"]
    root = at_root(b["dir"])
    objs = os.path.join(root, "objects")
    days = os.path.join(root, "days")
    os.makedirs(objs, exist_ok=True)
    os.makedirs(days, exist_ok=True)
    today = now().strftime("%Y-%m-%d")
    entry = os.path.join(days, f"{today}.json")
    if os.path.exists(entry):
        say(f"  today's backup exists: {entry}")
    else:
        rec = {"taken_at": now().isoformat()}
        for h in ("listener", "library"):
            with tempfile.TemporaryDirectory(dir=root) as t:
                copy = os.path.join(t, f"{h}.db")
                local_backup(at_root(plan["hub"][h]), copy)
                h_sha = sha256(copy)
                dst = os.path.join(objs, f"{h_sha}.db")
                if not os.path.exists(dst):
                    shutil.move(copy, dst)
                    seal(dst)
                    say(f"  {h}: stored {h_sha[:12]}, {os.path.getsize(dst) >> 20} MB")
                else:
                    say(f"  {h}: unchanged, {h_sha[:12]} already held")
                rec[h] = h_sha
        # [SPEC-MTR-110]: losing the mesh key means enrolling every member
        # again, so it is kept with the pair it belongs beside.
        mdir = os.path.join(os.path.dirname(at_root(plan["hub"]["library"])), "mesh")
        if os.path.isfile(os.path.join(mdir, "roster.json")):
            with tempfile.TemporaryDirectory(dir=root) as t:
                copy = os.path.join(t, "mesh.db")
                mesh_archive(mdir, copy)
                m_sha = sha256(copy)
                dst = os.path.join(objs, f"{m_sha}.db")
                if not os.path.exists(dst):
                    shutil.move(copy, dst)
                    seal(dst)
                    say(f"  mesh keys and roster: stored {m_sha[:12]}")
                else:
                    say(f"  mesh keys and roster: unchanged, {m_sha[:12]} already held")
                rec["mesh"] = m_sha
        with open(entry, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(rec, fh, indent=1, sort_keys=True)
    # Retention, then the objects nothing refers to any more.
    have = sorted(f[:-5] for f in os.listdir(days) if f.endswith(".json"))
    keep = retained(have, b["keep_daily"], b["keep_monthly"])
    for d in have:
        if d not in keep:
            os.remove(os.path.join(days, f"{d}.json"))
            say(f"  retired the backup of {d}")
    keep, wanted = kept_objects(plan)
    for f in os.listdir(objs):
        if f.endswith(".db") and f[:-3] not in wanted:
            os.chmod(os.path.join(objs, f), 0o644)
            os.remove(os.path.join(objs, f))
    st = proofs(root)
    prove_local(root, wanted, st)
    save_proofs(root, st)
    rc = mirror(plan, keep, wanted)
    return max(rc, status(plan))


# Every mirror step has a limit, so a host that stops answering costs one
# quarter-hour's run, not the night: the run is said FAILED and the next one
# starts over -- an object is sent whole or not at all (`.part`, then `mv`).
# A short step gets two minutes. A copy or a hash gets two minutes and its
# size at a floor rate, well under what was measured: 1.27 GB went to
# teacherslounge over Wi-Fi in under 3.6 minutes, 2026-10-15 -- 5.9 MB/s or
# better -- so 1 MB/s for copies; and sha256sum reads far faster than that.
MIRROR_SHORT_S = 120
MIRROR_COPY_BPS = 1_000_000
MIRROR_HASH_BPS = 20_000_000


def mirror_limit(nbytes, bps):
    """Seconds allowed for a step over `nbytes` at the floor rate `bps`."""
    return MIRROR_SHORT_S + nbytes / bps


def mirror_copy(src, dest, nbytes):
    """`scp` one file, within a limit set by its size; empty standard input,
    as `star_distribute.ssh` has, for the same reason. Raises sd.Failed."""
    limit = mirror_limit(nbytes, MIRROR_COPY_BPS)
    try:
        r = subprocess.run(["scp", "-q", *SSH, src, dest], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=limit)
    except subprocess.TimeoutExpired:
        raise sd.Failed(f"copying {os.path.basename(src)} took longer than {limit:.0f} s")
    if r.returncode != 0:
        raise sd.Failed(f"copying {os.path.basename(src)}: exit {r.returncode} "
                        f"{(r.stdout + r.stderr).strip()[-200:]}")


def mirror(plan, keep, wanted):
    """The same days and objects on the mirror host; an object is sent once,
    so an unchanged catalogue costs nothing after its first night."""
    m = plan["backups"].get("mirror")
    if not m:
        say("  no mirror in the plan")
        return 0
    host, rd = m["host"], m["dir"]
    root = at_root(plan["backups"]["dir"])
    # [SPEC-STAR-088]: a mirror that is off is not a failure, only not yet; it
    # is written when next reachable, and status says when a day is missed.
    # Until 2026-10-15 it was tried at 03:30 alone, when teacherslounge is
    # usually off, and was not written for nearly three weeks.
    rc, _ = sd.ssh(host, "true", quiet=True, timeout=MIRROR_SHORT_S)
    if rc != 0:
        st = proofs(root)
        say(f"  mirror: {host} is not reachable now; it is brought up to date when it next is"
            + (f" (it holds the backup of {st['mirror_newest']})" if st.get("mirror_newest") else ""))
        return 0
    try:
        sd.must(host, f"mkdir -p {rd}/objects {rd}/days", "reach the mirror", quiet=True, timeout=MIRROR_SHORT_S)
        there = set(sd.must(host, f"ls -1 {rd}/objects {rd}/days", "list the mirror", quiet=True,
                            timeout=MIRROR_SHORT_S).split())
        for o in sorted(wanted):
            if f"{o}.db" in there:
                continue
            src = os.path.join(root, "objects", f"{o}.db")
            size = os.path.getsize(src)
            mirror_copy(src, f"{host}:{rd}/objects/{o}.db.part", size)
            got = sd.must(host, f"sha256sum {rd}/objects/{o}.db.part", "hash", quiet=True,
                          timeout=mirror_limit(size, MIRROR_HASH_BPS)).split()[0]
            if got != o:
                raise sd.Failed(f"object {o[:12]} did not arrive intact")
            sd.must(host, f"mv {rd}/objects/{o}.db.part {rd}/objects/{o}.db", "place", quiet=True,
                    timeout=MIRROR_SHORT_S)
            say(f"  mirror: sent {o[:12]}")
        for d in sorted(keep):
            src = os.path.join(root, "days", f"{d}.json")
            mirror_copy(src, f"{host}:{rd}/days/{d}.json", os.path.getsize(src))
        keep_names = " ".join(f"{d}.json" for d in keep)
        want_names = " ".join(f"{o}.db" for o in wanted)
        sd.must(host, f"cd {rd}/days && for f in *.json; do case \" {keep_names} \" in *\" $f \"*) ;; "
                      f"*) rm -f \"$f\" ;; esac; done; cd {rd}/objects && for f in *.db; do "
                      f"case \" {want_names} \" in *\" $f \"*) ;; *) rm -f \"$f\" ;; esac; done; true",
                "prune the mirror", quiet=True, timeout=MIRROR_SHORT_S)
        say(f"  mirror {host}:{rd} holds the same {len(keep)} day(s)")
        # [GDE-INT-120]: its objects re-hashed there, the ones not proven for
        # PROVE_DAYS, in one command; an object whose hash is not its name is
        # said, and stays said until a later run finds it whole.
        st = proofs(root)
        st["mirror"] = {o: t for o, t in st["mirror"].items() if o in wanted}
        st["mirror_bad"] = [o for o in st["mirror_bad"] if o in wanted]
        todo = due(st, "mirror", wanted)
        if todo:
            total = sum(os.path.getsize(os.path.join(root, "objects", f"{o}.db")) for o in todo)
            out = sd.must(host, f"cd {rd}/objects && sha256sum -- " + " ".join(f"{o}.db" for o in todo),
                          "re-read the mirror's objects", quiet=True,
                          timeout=mirror_limit(total, MIRROR_HASH_BPS))
            seen = {}
            for line in out.splitlines():
                sha, _, name = line.partition("  ")
                seen[name.strip()] = sha
            for o in todo:
                if seen.get(f"{o}.db") == o:
                    st["mirror"][o] = now().isoformat()
                    if o in st["mirror_bad"]:
                        st["mirror_bad"].remove(o)
                elif o not in st["mirror_bad"]:
                    st["mirror_bad"].append(o)
        st["mirror_newest"], st["mirror_at"] = max(keep), now().isoformat()
        save_proofs(root, st)
        bad = st["mirror_bad"]
        say(f"  proven on the mirror: {len(todo)} object(s) re-read" + (f"; {len(bad)} NOT WHOLE: "
            + ", ".join(b[:12] for b in bad) if bad else ", all whole"))
        return 1 if bad else 0
    except (sd.Failed, subprocess.SubprocessError, OSError) as err:
        say(f"  MIRROR FAILED: {err}")
        return 1


PASS_DAYS = 30     # [GDE-INT-145]: a node whose oldest read is older has not finished a pass


def node_integrity(plan, depth=30):
    """[GDE-INT-145]: each node's file checks as its newest snapshot carried them,
    from the newest `depth` runs: {name: (run, the summary or None)}. A node
    missed by the newest run -- off, or skipped -- is read from the one before."""
    runs = at_root(plan["runs"])
    found = run_names(runs)[::-1][:depth]
    out = {}
    for d in found:
        p = os.path.join(runs, d, "baselines.json")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as fh:
            for name, b in json.load(fh).items():
                out.setdefault(name, (d, b.get("integrity")))
    return out


def integrity_stale(name, ic, at):
    """What about a node's file checks makes `status` STALE: a file not whole, a
    pass not finished in PASS_DAYS, or checks that could not be read -- then the
    hub does not know [GDE-INT-020]. A node switched off is said, not stale; a
    re-tag is said by `integrity_words`, not stale."""
    if ic.get("error"):
        return [f"{name}'s file checks could not be read in its last sync ({ic['error']})"]
    bad = not_whole(ic)
    out = []
    if bad:
        out.append(f"{name} holds {len(bad)} file(s) not whole -- send them again from the Export page")
    if ic.get("rate_kib_per_s") != 0 and ic.get("oldest"):
        t = dt.datetime.fromisoformat(str(ic["oldest"]).replace("Z", "+00:00"))
        age = at - (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc))   # the player writes UTC
        if age > dt.timedelta(days=PASS_DAYS):
            out.append(f"{name} has not read all its files back in {PASS_DAYS} days "
                       f"(the oldest read {str(ic['oldest'])[:10]})")
    return out


def status(plan):
    """[SPEC-STAR-086]: loud when the backup or its mirror has stopped."""
    b = plan["backups"]
    limit = dt.timedelta(hours=b.get("max_age_hours", 30))
    days = os.path.join(at_root(b["dir"]), "days")
    bad = []
    newest = sorted(f[:-5] for f in os.listdir(days) if f.endswith(".json")) if os.path.isdir(days) else []
    if not newest:
        bad.append("the hub has never been backed up")
    else:
        with open(os.path.join(days, f"{newest[-1]}.json"), encoding="utf-8") as fh:
            age = now() - dt.datetime.fromisoformat(json.load(fh)["taken_at"])
        say(f"  hub backup: newest {newest[-1]}, {age.total_seconds() / 3600:.0f} h old")
        if age > limit:
            bad.append(f"the newest hub backup is {age.days} day(s) old")
    m = b.get("mirror")
    if m:
        # [SPEC-STAR-088]: from what the last successful mirror run recorded,
        # not by asking a host that may be off. Behind is normal for hours --
        # the mirror is written when it is next reachable -- and STALE once the
        # backup it holds is more than `max_age_hours` (48) old: a day missed.
        st = proofs(at_root(b["dir"]))
        held = st.get("mirror_newest")
        mlimit = dt.timedelta(hours=m.get("max_age_hours", 48))
        if not held:
            say("  mirror: never brought up to date since its run was recorded (2026-10-15)")
            bad.append("the mirror has not been brought up to date")
        else:
            try:
                with open(os.path.join(days, f"{held}.json"), encoding="utf-8") as fh:
                    taken = dt.datetime.fromisoformat(json.load(fh)["taken_at"])
            except (OSError, ValueError, KeyError):
                taken = dt.datetime.fromisoformat(held + "T00:00:00+00:00")
            mage = now() - taken
            say(f"  mirror: holds the backup of {held} ({mage.total_seconds() / 3600:.0f} h old), last written "
                f"{st['mirror_at'][:16].replace('T', ' ')} UTC")
            if mage > mlimit:
                bad.append(f"the mirror's newest backup is {mage.days} day(s) old -- {m['host']} has not been "
                           "reachable to take a newer one")
    # [GDE-INT-120]: how recently every kept object was proven whole.
    root = at_root(b["dir"])
    pr = proofs(root)
    for where in ("local", "mirror") if m else ("local",):
        if pr[f"{where}_bad"]:
            bad.append(f"{len(pr[where + '_bad'])} backup object(s) {'here' if where == 'local' else 'on the mirror'} "
                       "are not what they were stored as")
        if pr[where]:
            oldest = min(dt.datetime.fromisoformat(t) for t in pr[where].values())
            say(f"  backups proven {'here' if where == 'local' else 'on the mirror'}: {len(pr[where])} object(s), "
                f"the oldest proof {(now() - oldest).days} day(s) old")
    # [GDE-INT-115]: the library's own files against their records.
    import verify_library
    v = verify_library.newest(at_root(plan["hub"]["library"]))
    if v is None:
        bad.append("the library has never been verified")
    else:
        vt = dt.datetime.fromisoformat(v["checked_at"])
        vage = now() - (vt if vt.tzinfo else vt.astimezone())    # a time with no offset is this PC's
        say(f"  library verified: {v['checked_at'][:16].replace('T', ' ')}, {vage.total_seconds() / 3600:.0f} h ago: "
            + verify_library.summary(v))
        if vage > limit:
            bad.append(f"the library was last verified {vage.days} day(s) ago")
        if not v.get("ok_whole", True):
            bad.append("the library's last verification found damaged, missing or unreadable files")
        if not v.get("sidecars_whole", True):     # [GDE-INT-175]
            bad.append("the library's last verification found a rip log, AccurateRip report or CUE "
                       "changed or missing")
    # [GDE-INT-145]: each speaker's reading of its own files, as its last sync
    # brought it. A build that does not report it is said and not counted
    # stale: during a rollout most nodes are on one, and backup's exit is this.
    for name, (run, ic) in sorted(node_integrity(plan).items()):
        when = f"{run[:4]}-{run[4:6]}-{run[6:8]} {run[9:11]}:{run[11:13]}Z" if len(run) >= 13 else run
        if ic is None:
            say(f"  {name}: does not report reading its files back (sync of {when})")
            continue
        say(f"  {name}: {integrity_words(ic)} (sync of {when})")
        bad += integrity_stale(name, ic, now())
    runs = at_root(plan["runs"])
    if os.path.exists(os.path.join(runs, "state.json")):
        st = load_state(plan)
        for name, e in sorted(st.get("nodes", {}).items()):
            age = now() - dt.datetime.fromisoformat(e["at"])
            say(f"  {name}: last synced {age.days} day(s) ago")
    for x in bad:
        say(f"  STALE: {x}")
    say("RESULT status: " + ("STALE -- " + "; ".join(bad) if bad else "backups current"))
    return 1 if bad else 0



# ------------------------------------------------------------------ adopt --
# [SPEC-STAR-946]: a node whose catalogue was seeded by hand has no copy last sent,
# and a node with none is never sent a catalogue patch [SPEC-STAR-940] -- so its
# covers, credits and boundaries stay as seeded, whatever the hub corrects. Found
# 2026-10-29 on lrhtpc, seeded the evening before. Adopting takes the node's own
# catalogue, copied on the node through the backup API, as the copy last sent: the
# first patch is then made against exactly what it holds, and its rehearsal proves it.

ADOPT_SCRIPT = """
import hashlib, sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect("file:" + src + "?mode=ro", uri=True)
d = sqlite3.connect(dst)
s.backup(d)
ok = d.execute("PRAGMA integrity_check").fetchone()[0]
d.close()
s.close()
h = hashlib.sha256()
with open(dst, "rb") as fh:
    for chunk in iter(lambda: fh.read(1 << 20), b""):
        h.update(chunk)
print(ok, h.hexdigest())
"""


def adopt(plan, name, library, force=False, tmp="/var/tmp"):
    """Copy `name`'s catalogue `library` (a path on the node) here and record it as the
    copy last sent to it. Refuses a node that already has one unless `force`."""
    node = plan["nodes"].get(name)
    if node is None:
        raise SystemExit(f"{name} is not a node of this plan")
    if not library or not library.startswith("/"):
        raise SystemExit("--library takes the catalogue's absolute path on the node, e.g. /srv/library/library.db")
    state = load_state(plan)
    old = state["nodes"].get(name, {})
    if sent_path(old, "library_sent") and not force:
        raise SystemExit(f"{name} already has a copy last sent ({old['library_sent']}): a sync patches it "
                         "routinely. --force replaces it with what the node holds now")
    host = node["host"]
    t = stamp()
    remote = f"{tmp}/lempi-adopt-{t}.db"
    say(f"{name}: adopting its catalogue {library} as the copy last sent [SPEC-STAR-946]")
    # [GDE-DEP-060]: what is assumed of the node, said before acting on it.
    size = subprocess.run(["ssh", *SSH, host, f"stat -c %s {shq(library)} && df -P -B1 {shq(tmp)} | tail -1"],
                          capture_output=True, text=True)
    if size.returncode != 0:
        raise SystemExit(f"cannot read {library} or {tmp} on {host}: {size.stderr.strip() or size.stdout.strip()}")
    lines = size.stdout.split("\n")
    need, free = int(lines[0]), int(lines[1].split()[3])
    if free < need * 1.2:
        raise SystemExit(f"{host}:{tmp} has {free >> 20} MB free, and the copy needs {need >> 20} MB: "
                         "name another folder with --tmp")
    say(f"  {host}: catalogue {need >> 20} MB; copied through the backup API into {tmp}, {free >> 20} MB free")
    try:
        r = subprocess.run(["ssh", *SSH, host, f"python3 - {shq(library)} {shq(remote)}"],
                           input=ADOPT_SCRIPT, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise SystemExit(f"the copy on {host} failed: {r.stderr.strip()}")
        ok, want = r.stdout.split()
        if ok != "ok":
            raise SystemExit(f"the copy on {host} fails integrity_check: {ok}")
        folder = os.path.join(at_root(plan["runs"]), "adopted", f"{name}-{t}")
        os.makedirs(folder)
        here = os.path.join(folder, "library.db")
        say(f"  integrity_check ok on {host}; fetching it (sha256 {want[:12]})")
        got = subprocess.run(["scp", "-q", *SSH, f"{host}:{remote}", here], capture_output=True, text=True)
        if got.returncode != 0:
            raise SystemExit(f"fetching the copy failed: {got.stderr.strip()}")
        if sha256(here) != want:
            raise SystemExit(f"the copy fetched is not the one made on {host}: its hash differs. Nothing recorded")
        seal(here)
    finally:
        subprocess.run(["ssh", *SSH, host, f"rm -f {shq(remote)}"], capture_output=True)
    # `at` is kept where there is one: a bundle sent since it is then asked about as
    # well, which costs a question and can never miss a row [SPEC-STAR-945].
    state["nodes"][name] = {**old, "library_sent": os.path.relpath(here, ROOT), "adopted": t,
                            "at": old.get("at") or now().isoformat(), "run": old.get("run") or f"adopt-{t}"}
    save_state(plan, state)
    say(f"RESULT {name}: adopted -- {os.path.relpath(here, ROOT)} is the copy last sent; "
        "the next sync's snapshot patches against it, and its rehearsal proves it")
    return 0


def shq(s):
    return "'" + s.replace("'", "'\\''") + "'"


def take(rest, flag, valued=True):
    """Remove `flag` (and its value) from `rest`: its value, or whether it was there."""
    if flag not in rest:
        return (None if valued else False), rest
    i = rest.index(flag)
    if not valued:
        return True, rest[:i] + rest[i + 1:]
    if i + 1 >= len(rest):
        raise SystemExit(f"{flag} takes a value")
    return rest[i + 1], rest[:i] + rest[i + 2:]


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    with open(at_root(argv[0]), encoding="utf-8") as fh:
        plan = json.load(fh)
    cmd, rest = argv[1], argv[2:]
    given, rest = take(rest, "--run")
    excuse, rest = take(rest, "--console-pid")
    bulk, rest = take(rest, "--approve-all", valued=False)
    by, rest = take(rest, "--by")
    only, rest = take(rest, "--nodes")
    if only is not None:
        only = [n for n in only.split(",") if n]
    if cmd == "snapshot":
        return snapshot(plan, only)
    if cmd == "backup":
        return backup(plan)
    if cmd == "mirror":
        return mirror_now(plan)
    if cmd == "status":
        return status(plan)
    if cmd == "adopt":
        library, rest = take(rest, "--library")
        tmp, rest = take(rest, "--tmp")
        force, rest = take(rest, "--force", valued=False)
        if len(rest) != 1:
            raise SystemExit("adopt takes one node: adopt NODE --library /path/on/node/library.db [--force] [--tmp DIR]")
        return adopt(plan, rest[0], library, force, tmp or "/var/tmp")
    if cmd not in ("merge", "items", "verdict", "approve-all", "patch", "rehearse", "commit"):
        print(__doc__)
        return 2
    run = newest_run(plan, given)
    if cmd == "merge":
        return merge(plan, run)
    if cmd == "items":
        return show_items(run)
    if cmd == "verdict":
        if len(rest) != 2:
            raise SystemExit("verdict takes an item id and `approved` or `chose:NODE`")
        x = give_verdict(run, rest[0], rest[1], by or "item")
        say(f"{x['table']} {x['key']}: {rest[1]}")
        return 0
    if cmd == "approve-all":
        kinds = approve_all(run, by or "approve-all")
        say("approved: " + (", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "nothing was waiting"))
        return 0
    if cmd == "patch":
        return patch(plan, run)
    return distribute(plan, run, rest, cmd == "commit", excuse, bulk)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
