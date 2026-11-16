#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage B's window, measured: each listener recorded before it converts, and
checked after, from each machine's own file [IMPL-UID-068].

    python tools/listener_window.py record DIR [--only a,b]
    python tools/listener_window.py verify DIR [--only a,b] [--lyrics-uids]

`record` writes DIR/<machine>.json; `verify` reads it, measures again, and says,
per machine, what is the same and what is not, exiting 1 on any difference.
Both only read. The machines and their files are `fleet/listener-paths.json`,
FLEET001's paths as {machine: {host?, listener, library}} -- fleet-private and
untracked, as `fleet/` is but for its named files [SPEC-FCP-050]. A machine with a
host is measured there, over ssh, by a script that needs only Python 3 and SQLite,
so no tree is needed on it.

What is measured:
  - each of the eight tables that named a passage `[IMPL-UID-060]`: its shape
    (by number, by uid) and its rows -- the same after, but the queue entries
    the conversion leaves out for naming nothing;
  - the copy of the whole file the conversion takes first (`listener.pre-uid.db`);
  - the shared rows that name a passage -- reviews and flags -- each by its uid,
    a row still by number read through this machine's own catalogue: the same
    after, and the same as the hub's;
  - `passage_lyrics` `[IMPL-UID-063]`: its rows, a digest over every column but
    `passage_uid`, and the row each passage shows by the player's rule, by
    segment for a row with no uid and by uid for one with -- the same after.
    `--lyrics-uids` also requires every row the hub placed to carry its uid.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import staged  # noqa: E402  -- utf8_output

PATHS = os.path.join(ROOT, "fleet", "listener-paths.json")

# Run on each machine, alone: argv is listener, library. Read-only throughout.
CENSUS = r'''
import hashlib, json, os, sqlite3, sys
lis, lib = sys.argv[1], sys.argv[2]
TABLES = ["listener_play_history", "listener_rejections", "selection_decisions", "player_state",
          "player_queue", "id_reviews", "boundary_reviews", "artist_reviews"]
SHARED = {"id_reviews": ("decided_at",), "boundary_reviews": ("decided_at",), "artist_reviews": ("recording_mbid",)}
def uri(p): return "file:" + p.replace("%", "%25").replace("?", "%3f").replace("#", "%23") + "?mode=ro"
c = sqlite3.connect(uri(lis), uri=True)
c.execute("ATTACH ? AS lib", (uri(lib),))
def cols(t, schema="main"):
    return [r[1] for r in c.execute(f"PRAGMA {schema}.table_info({t})")]
uids = {}
if "passage_uid" in cols("passages", "lib"):
    uids = {p: u for p, u in c.execute("SELECT passage_id, passage_uid FROM lib.passages") if u}
out = {"tables": {}, "shared": {}, "pre_uid_copy": os.path.exists(os.path.splitext(lis)[0] + ".pre-uid.db")}
for t in TABLES:
    cs = cols(t)
    if not cs:
        out["tables"][t] = {"shape": "absent"}
        continue
    shape = "by_number" if "passage_id" in cs else "by_uid" if "passage_uid" in cs else "neither"
    out["tables"][t] = {"shape": shape, "rows": c.execute(f"SELECT count(*) FROM main.{t}").fetchone()[0]}
    if t in SHARED:
        col = "passage_id" if shape == "by_number" else "passage_uid"
        rows = []
        for r in c.execute(f"SELECT {col}, {', '.join(SHARED[t])} FROM main.{t}"):
            uid = uids.get(r[0]) if shape == "by_number" else r[0]
            rows.append([uid] + list(r[1:]))
        out["shared"][t] = sorted(rows, key=lambda r: json.dumps(r))
flags = []
if cols("listener_flags"):
    for k, s in c.execute("SELECT subject_kind, subject_id FROM main.listener_flags"):
        if k == "passage" and not (isinstance(s, str) and len(s) == 32):
            s = uids.get(int(s)) if str(s).isdigit() else None
        flags.append([k, s])
out["shared"]["listener_flags"] = sorted(flags)
L = cols("passage_lyrics")
if L:
    base = ["audio_md5", "kind", "start_ms", "end_ms", "mbid", "text", "no_words", "updated_at", "origin"]
    has_uid = "passage_uid" in L
    rows = [dict(zip(base + ["passage_uid"], r)) for r in c.execute(
        f"SELECT {', '.join(base)}, {'passage_uid' if has_uid else 'NULL'} FROM main.passage_lyrics")]
    h = hashlib.sha256()
    for r in sorted(rows, key=lambda r: (r["audio_md5"], r["kind"], r["start_ms"])):
        h.update(repr(tuple(r[k] for k in base)).encode("utf-8"))
    alias = {}
    if cols("audio_md5_aliases", "lib"):
        alias = dict(c.execute("SELECT old_md5, new_md5 FROM lib.audio_md5_aliases"))
    def same(a, b):
        sh = min(a[1], b[1]) - max(a[0], b[0])
        return sh > 0 and sh * 2 >= max(a[1] - a[0], b[1] - b[0])
    order = sorted(rows, key=lambda r: (r["updated_at"], r["audio_md5"], r["kind"], r["start_ms"]), reverse=True)
    shown = {}
    if uids:
        for md5, kind, s, e, u in c.execute("SELECT f.audio_md5, p.kind, p.start_ms, p.end_ms, p.passage_uid "
                                            "FROM lib.passages p JOIN lib.files f ON f.file_id = p.file_id"):
            if not u:
                continue
            for r in order:
                if r["kind"] != kind:
                    continue
                if r["passage_uid"]:
                    hit = r["passage_uid"] == u
                else:
                    hit = alias.get(r["audio_md5"], r["audio_md5"]) == md5 and same((r["start_ms"], r["end_ms"]), (s, e))
                if hit:
                    shown[u] = [r["audio_md5"], r["kind"], r["start_ms"]]
                    break
    out["lyrics"] = {"rows": len(rows), "digest": h.hexdigest(), "with_uid": sum(1 for r in rows if r["passage_uid"]),
                     "shown": shown}
print(json.dumps(out))
'''


def say(*a):
    print(*a, flush=True)


def machines(only=None) -> dict:
    with open(PATHS, encoding="utf-8") as fh:
        m = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    if only:
        missing = sorted(set(only) - set(m))
        if missing:
            raise SystemExit(f"not in {PATHS}: {missing}")
        m = {k: v for k, v in m.items() if k in only}
    return m


def census(name: str, spec: dict) -> dict:
    if spec.get("host"):
        cmd = ["ssh", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes", spec["host"],
               "python3", "-", spec["listener"], spec["library"]]
    else:
        cmd = [sys.executable, "-", os.path.join(ROOT, spec["listener"]), os.path.join(ROOT, spec["library"])]
    r = subprocess.run(cmd, input=CENSUS, capture_output=True, text=True, encoding="utf-8", timeout=600)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"{name}: the census did not run (exit {r.returncode}): {r.stderr.strip()[-400:]}")
    return json.loads(r.stdout)


def compare(name: str, before: dict, now: dict, hub_now: dict | None, lyrics_uids: bool) -> list:
    """(ok, what) for each check on one machine."""
    out = []
    for t, b in before["tables"].items():
        n = now["tables"].get(t, {"shape": "absent"})
        if b["shape"] == "absent":
            out.append((n["shape"] in ("absent", "by_uid"), f"{t}: absent before, {n['shape']} now"))
            continue
        out.append((n["shape"] == "by_uid", f"{t}: by uid now (was {b['shape']})"))
        if t == "player_queue":
            out.append((n.get("rows", 0) <= b["rows"], f"{t}: {b['rows']} row(s) before, {n.get('rows')} now"))
        else:
            out.append((n.get("rows") == b["rows"], f"{t}: {b['rows']} row(s) before, {n.get('rows')} now"))
    out.append((now["pre_uid_copy"], "the whole-file copy taken before converting is there"))
    for t, rows in before["shared"].items():
        out.append((now["shared"].get(t) == rows, f"{t}: the same {len(rows)} row(s) by uid as before"))
        if hub_now is not None:
            out.append((now["shared"].get(t) == hub_now["shared"].get(t), f"{t}: the same as the hub's"))
    bl, nl = before.get("lyrics"), now.get("lyrics")
    if bl:
        out.append((nl is not None and nl["rows"] == bl["rows"], f"passage_lyrics: {bl['rows']} row(s) before, "
                                                                f"{nl and nl['rows']} now"))
        out.append((nl is not None and nl["digest"] == bl["digest"], "passage_lyrics: every column but passage_uid as before"))
        out.append((nl is not None and nl["shown"] == bl["shown"],
                    f"passage_lyrics: every passage shows the same row ({len(bl['shown'])} with words)"))
        if lyrics_uids:
            out.append((nl is not None and nl["with_uid"] >= len(bl["shown"]),
                        f"passage_lyrics: {nl and nl['with_uid']} row(s) carry their passage's uid"))
    return out


def main(argv=None) -> int:
    staged.utf8_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", choices=("record", "verify"))
    ap.add_argument("dir")
    ap.add_argument("--only", help="machines, comma-separated")
    ap.add_argument("--lyrics-uids", action="store_true")
    a = ap.parse_args(argv)
    ms = machines(a.only.split(",") if a.only else None)
    os.makedirs(a.dir, exist_ok=True)
    if a.what == "record":
        bad = 0
        for name, spec in ms.items():
            try:
                got = census(name, spec)
            except (RuntimeError, subprocess.TimeoutExpired) as e:
                say(f"{name}: NOT RECORDED -- {e}")
                bad += 1
                continue
            with open(os.path.join(a.dir, f"{name}.json"), "w", encoding="utf-8") as fh:
                json.dump(got, fh, indent=1, sort_keys=True)
            shapes = {t: v["shape"] for t, v in got["tables"].items()}
            say(f"{name}: recorded -- {sum(v.get('rows', 0) for v in got['tables'].values())} row(s) in the eight tables, "
                f"{len(set(shapes.values()))} shape(s) {sorted(set(shapes.values()))}; "
                f"lyrics {got.get('lyrics', {}).get('rows')} row(s), {len(got.get('lyrics', {}).get('shown', {}))} shown")
        return 1 if bad else 0
    nows, failed = {}, 0
    for name, spec in ms.items():
        try:
            nows[name] = census(name, spec)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            say(f"{name}: NOT VERIFIED -- {e}")
            failed += 1
    hub = nows.get("desktop")
    for name, now in nows.items():
        path = os.path.join(a.dir, f"{name}.json")
        if not os.path.exists(path):
            say(f"{name}: NOT VERIFIED -- nothing recorded before")
            failed += 1
            continue
        with open(path, encoding="utf-8") as fh:
            before = json.load(fh)
        checks = compare(name, before, now, hub if name != "desktop" else None, a.lyrics_uids)
        bad = [w for ok, w in checks if not ok]
        say(f"{name}: {'ALL SAME' if not bad else f'{len(bad)} DIFFERENCE(S)'} -- {len(checks)} check(s)")
        for w in bad:
            say(f"   DIFFERENT  {w}")
        failed += bool(bad)
        with open(os.path.join(a.dir, f"{name}.after.json"), "w", encoding="utf-8") as fh:
            json.dump(now, fh, indent=1, sort_keys=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
