# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the hub retired, and what each node has done with it `[SPEC-RTF-015]`, `[SPEC-RTF-035]`.

A merge retires the files it no longer plays `[SPEC-AGN-030]`, and says so
here: one `ingest_decisions` row a file, stage `retire`, written in the
merge's own transaction (`record_retired`). A node retires a file only on that
word, matched by its bytes `[SPEC-RTF-010]`; star sync tells it which
(`retire_entries`), and what each node answered is kept in `sync/retired.json`
beside the hub's library, so a file settled on a node is not asked about again.

Small and free of `ingest_cd`'s imports, so `star_sync.py` can read it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import posixpath
import sqlite3

import lempi_db

STAGE = "retire"
# A node's answers that settle a file there: moved, already gone, or someone
# else's bytes at that path. "still catalogued" and "refused" are asked again.
SETTLED = ("moved", "not there", "bytes differ")


def record_retired(conn, file_ids, into: str, moved: dict, replaced_by: str | None, now: str) -> int:
    """The hub's word that it retired each of these files, before their rows
    go: `detail` holds `sha256` and `size_bytes`, the bytes; `rel_path`, its
    place under the music folder -- what a node's own path for it ends in;
    `retired_to`, where the hub moved it (`moved`, `{path: destination}`);
    `replaced_by`, the audio that plays it now. A decision row and not a
    table of its own: the catalogue merge takes every table the hub holds to
    every node, and a patch never drops one, so a hub-only table could be
    neither kept home nor left out. The caller commits."""
    ids = list(file_ids)
    if not ids:
        return 0
    have = {r[1] for r in conn.execute("PRAGMA table_info(files)")}
    extra = [c for c in ("sha256", "size_bytes") if c in have]
    root = os.path.dirname(os.path.dirname(os.path.abspath(into)))
    n = 0
    q = ",".join("?" * len(ids))
    for row in conn.execute(f"SELECT audio_md5, path{''.join(', ' + c for c in extra)} FROM files "
                            f"WHERE file_id IN ({q})", ids).fetchall():
        md5, path = row[0], row[1]
        got = dict(zip(extra, row[2:]))
        detail = {"sha256": got.get("sha256"), "size_bytes": got.get("size_bytes"),
                  "rel_path": os.path.relpath(path, root).replace(os.sep, "/"),
                  "retired_to": moved.get(path), "replaced_by": replaced_by, "why": "merge"}
        conn.execute("INSERT INTO ingest_decisions (audio_md5,stage,outcome,confidence,detail,decided_at) "
                     "VALUES (?1,?2,'retired',1.0,?3,?4)", (md5, STAGE, json.dumps(detail, sort_keys=True), now))
        n += 1
    return n


def retired_files(conn) -> dict[str, dict]:
    """Every audio the hub has retired, `{audio_md5: detail}` with its date as
    `at`, the newest word on each -- unless the hub catalogues it again since,
    when it is not retired whatever was once said."""
    out = {}
    try:
        rows = conn.execute("SELECT audio_md5, detail, decided_at FROM ingest_decisions WHERE stage = ?1 "
                            "AND outcome = 'retired' ORDER BY decision_id", (STAGE,)).fetchall()
    except sqlite3.OperationalError:
        return {}
    for md5, detail, at in rows:
        out[md5] = dict(json.loads(detail or "{}"), at=at)
    if out:
        q = ",".join("?" * len(out))
        for (md5,) in conn.execute(f"SELECT audio_md5 FROM files WHERE audio_md5 IN ({q})", list(out)).fetchall():
            out.pop(md5, None)
    return out


def _paths(db: str) -> dict[str, str]:
    c = sqlite3.connect(lempi_db.ro_uri(db), uri=True)
    try:
        return {m: p for m, p in c.execute("SELECT audio_md5, path FROM files") if p}
    except sqlite3.OperationalError:
        return {}
    finally:
        c.close()


def retire_entries(hub_library: str, baseline: str, target: str, settled=frozenset()) -> list[dict]:
    """`[SPEC-RTF-020]` What a node is told to retire in this run: each audio
    the hub retired that this patch leaves the node no row for, and not yet
    settled there. Its path is the one the node's own row gave it, where the
    node had one; else the replacement's folder on the node -- a merge files
    the disc in the album's folder -- so files whose rows an earlier run
    removed, before a node could retire anything, are found too. A file
    neither of whose rows the node ever had is not asked about.

    `replacement_here`: whether this patch leaves the node the audio that
    replaced it `[SPEC-RTF-050]`."""
    hub = sqlite3.connect(lempi_db.ro_uri(hub_library), uri=True)
    try:
        gone = retired_files(hub)
    finally:
        hub.close()
    if not gone:
        return []
    base, tgt = _paths(baseline), _paths(target)
    out = []
    for md5, d in sorted(gone.items()):
        if md5 in settled or md5 in tgt or not d.get("rel_path"):
            continue
        rel = d["rel_path"]
        path, how = base.get(md5), "its row"
        if path is None:
            q = tgt.get(d.get("replaced_by") or "") or base.get(d.get("replaced_by") or "")
            if not q:
                continue
            folder = posixpath.dirname(q.replace("\\", "/"))
            album = posixpath.dirname(rel)
            if not (folder == album or folder.endswith("/" + album)):
                continue        # the node files that album elsewhere: nothing to say where this one is
            path, how = posixpath.join(folder, posixpath.basename(rel)), "beside its replacement"
        out.append({"audio_md5": md5, "sha256": d.get("sha256"), "size_bytes": d.get("size_bytes"),
                    "path": path, "found_by": how, "rel_path": rel, "retired_at": (d.get("at") or "")[:10],
                    "replaced_by": d.get("replaced_by"),
                    "replacement_here": bool(d.get("replaced_by")) and d["replaced_by"] in tgt})
    return out


def ledger_path(library: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(library)), "sync", "retired.json")


def ledger(library: str) -> list[dict]:
    try:
        with open(ledger_path(library), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return []


def settled_on(library: str, node: str) -> set[str]:
    return {e["audio_md5"] for e in ledger(library) if e.get("node") == node and e.get("outcome") in SETTLED}


def record_answers(library: str, node: str, run: str, files: list[dict], at: str | None = None) -> int:
    """What a node answered a commit, file by file, appended -- never rewritten."""
    if not files:
        return 0
    rows = ledger(library)
    at = at or dt.datetime.now(dt.timezone.utc).isoformat()
    rows += [{"node": node, "run": run, "at": at, "audio_md5": f.get("audio_md5"), "outcome": f.get("outcome"),
              "from": f.get("from"), "to": f.get("to")} for f in files]
    os.makedirs(os.path.dirname(ledger_path(library)), exist_ok=True)
    tmp = ledger_path(library) + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rows, fh, indent=1)
    os.replace(tmp, ledger_path(library))
    return len(files)
