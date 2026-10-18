#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rewrite a node's review tables from the hub's, in the node's own passage
numbers [SPEC-STAR-051].

Until 2026-10-18 the star merge wrote every node's listener copy in the
hub's passage numbers. A node's `id_reviews` and `boundary_reviews` are
therefore the hub's rows under the hub's numbers, which on a node can name
other music -- and the fixed merge, which reads a node's rows as its own
numbers, would misread them once more. Measured that day: each node held
exactly the hub's rows (before `repair_review_copies.py`), 216 and 17, every
(decided_at, passage_id) pair the hub's, nothing of its own.

So each node's two tables become the hub's, translated by `star_merge`'s
own `passage_unmap` -- the inverse of how the next merge will read them. A
row for a passage the node does not hold is left out and counted. A hub row
with no origin gets the hub's name, as `export_changes.py` gives it.

Rehearses by default: the rewrite runs on the node inside a transaction,
its counts are read, and it is rolled back. With --commit: says what it finds
about the node first [GDE-DEP-060], stops the player if the node has one,
backs up the listener beside itself, applies, checks the counts, and starts
the player again whatever happened.

    python tools/repair_node_reviews.py data/library.db pi@bose
    python tools/repair_node_reviews.py data/library.db pi@bose --commit
"""
import argparse
import datetime as dt
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import star_merge  # noqa: E402

TABLES = ("id_reviews", "boundary_reviews")


def say(text: str = "") -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def ssh(host: str, cmd: str, stdin: str | None = None) -> tuple[int, str]:
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=15", host, cmd], input=stdin,
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode, (r.stdout + r.stderr).strip()


def must(host: str, cmd: str, what: str, stdin: str | None = None) -> str:
    rc, out = ssh(host, cmd, stdin)
    if rc != 0:
        raise SystemExit(f"{what} failed on {host} (exit {rc}): {out}")
    return out


def lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def node_catalogue(host: str, library: str, into: str) -> None:
    """The node's passages and files, enough for `passage_remap`/`passage_unmap`."""
    out = must(host, f"sqlite3 -readonly -json {library} \"SELECT p.passage_id, p.file_id, p.kind, "
                     f"p.start_ms, f.audio_md5 FROM passages p JOIN files f USING(file_id)\"",
               "reading its catalogue")
    rows = json.loads(out or "[]")
    c = sqlite3.connect(into)
    c.execute("CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT)")
    c.execute("CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT, start_ms INTEGER)")
    c.executemany("INSERT OR IGNORE INTO files VALUES (?,?)", [(r["file_id"], r["audio_md5"]) for r in rows])
    c.executemany("INSERT INTO passages VALUES (?,?,?,?)",
                  [(r["passage_id"], r["file_id"], r["kind"], r["start_ms"]) for r in rows])
    c.commit()
    c.close()
    say(f"  its catalogue: {len(rows)} passages")


def natural(conn: sqlite3.Connection) -> dict:
    return {r[0]: tuple(r[1:]) for r in conn.execute(
        "SELECT p.passage_id, f.audio_md5, p.kind, p.start_ms FROM passages p JOIN files f USING(file_id)")}


def mismatches(hub_library: str, node_cat: str, hub_listener: str, unmap: dict) -> list[str]:
    """Every row about to be written, checked by its music rather than its
    number: the hub's passage and the node's it is written under must be the
    same file, kind and start. `passage_unmap` matches by file and position,
    which a node holding a cut the hub does not would defeat."""
    h, n = sqlite3.connect(f"file:{hub_library}?mode=ro", uri=True), sqlite3.connect(node_cat)
    hub, node = natural(h), natural(n)
    alias = star_merge.aliases(hub_library)
    h.close()
    n.close()
    c = sqlite3.connect(f"file:{hub_listener}?mode=ro", uri=True)
    bad = []
    for t in TABLES:
        for (pid,) in c.execute(f"SELECT passage_id FROM {t}"):
            if pid not in unmap:
                continue
            want, got = hub.get(pid), node.get(unmap[pid])
            if got:
                got = (alias.get(got[0], got[0]),) + got[1:]
            if want != got:
                bad.append(f"{t}: hub passage {pid} {want} would be written as the node's {unmap[pid]} {got}")
    c.close()
    return bad


def build_sql(hub_listener: str, unmap: dict, node_cols: dict, hub_name: str) -> tuple[str, dict]:
    c = sqlite3.connect(f"file:{hub_listener}?mode=ro", uri=True)
    sql = []
    counts = {}
    for t in TABLES:
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
        rows = [dict(zip(cols, r)) for r in c.execute(f"SELECT * FROM {t}")]
        keep = [col for col in cols if col in node_cols[t]]
        written = dropped = renumbered = 0
        sql.append(f"DELETE FROM {t};")
        for r in rows:
            pid = r["passage_id"]
            if pid not in unmap:
                dropped += 1
                continue
            if unmap[pid] != pid:
                renumbered += 1
            r["passage_id"] = unmap[pid]
            if "origin" in r and r["origin"] is None:
                r["origin"] = hub_name
            sql.append(f"INSERT INTO {t} ({', '.join(keep)}) VALUES ({', '.join(lit(r[k]) for k in keep)});")
            written += 1
        counts[t] = dict(hub=len(rows), written=written, renumbered=renumbered, not_on_node=dropped,
                         columns_not_on_node=[col for col in cols if col not in node_cols[t]])
    c.close()
    return "\n".join(sql) + "\n", counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("hub_library", help="the hub's library.db; its listener.db is beside it")
    ap.add_argument("host", help="the node, e.g. pi@bose")
    ap.add_argument("--node-library", default="/srv/library/library.db")
    ap.add_argument("--node-listener", default="/var/lempi/listener.db")
    ap.add_argument("--hub-name", default=socket.gethostname(),
                    help="the origin a hub row with none is given (default: this machine's name)")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    hub_library = os.path.abspath(a.hub_library)
    hub_listener = os.path.join(os.path.dirname(hub_library), "listener.db")
    host, lis = a.host, a.node_listener

    say(f"== {host}: {'COMMIT' if a.commit else 'rehearsal -- rolled back, nothing kept'}")
    mnt = must(host, f"findmnt -no TARGET,FSTYPE,OPTIONS -T {lis}", "findmnt")
    say(f"  listener {lis}: {' '.join(mnt.split())}")
    if " overlay " in f" {mnt} ":
        raise SystemExit("the listener is on an overlay: a write there vanishes at reboot -- CLAUDE.md section 4")
    node_cols = {}
    for t in TABLES:
        out = must(host, f"sqlite3 -readonly -json {lis} \"PRAGMA table_info({t})\"", f"reading {t}'s columns")
        node_cols[t] = [r["name"] for r in json.loads(out or "[]")]
        if not node_cols[t]:
            raise SystemExit(f"{host} has no {t} table")
    before = must(host, f"sqlite3 -readonly {lis} \"SELECT (SELECT COUNT(*) FROM id_reviews), "
                        f"(SELECT COUNT(*) FROM boundary_reviews)\"", "counting")
    say(f"  holds now: id_reviews|boundary_reviews = {before}")

    with tempfile.TemporaryDirectory() as tmp:
        cat = os.path.join(tmp, "node.db")
        node_catalogue(host, a.node_library, cat)
        remap, gone = star_merge.passage_remap(cat, hub_library)
        unmap, clashes = star_merge.passage_unmap(cat, hub_library, remap)
        bad = mismatches(hub_library, cat, hub_listener, unmap)
    say(f"  numbering: {len(remap)} of its passages are numbered differently from the hub's, "
        f"{len(gone)} match nothing there, {clashes} clash(es)")
    say(f"  every row checked by file, kind and start on both sides: {len(bad)} mismatch(es)")
    for b in bad:
        say(f"    {b}")
    if bad and a.commit:
        raise SystemExit("refused: a row would be written under the wrong music")
    sql, counts = build_sql(hub_listener, unmap, node_cols, a.hub_name)
    for t, c in counts.items():
        say(f"  {t}: the hub's {c['hub']} rows -> {c['written']} written, {c['renumbered']} under "
            f"another number than the hub's, {c['not_on_node']} for passages it does not hold"
            + (f"; columns it lacks, left out: {c['columns_not_on_node']}" if c["columns_not_on_node"] else ""))
    check = ("SELECT 'after: ' || (SELECT COUNT(*) FROM id_reviews) || '|' || "
             "(SELECT COUNT(*) FROM boundary_reviews) || ' groups>1: ' || "
             "(SELECT COUNT(*) FROM (SELECT decided_at FROM id_reviews GROUP BY decided_at HAVING COUNT(*)>1));")
    want = f"after: {counts['id_reviews']['written']}|{counts['boundary_reviews']['written']} groups>1: 0"

    if not a.commit:
        out = must(host, f"sqlite3 -bail {lis}", "the rehearsal",
                   stdin="BEGIN IMMEDIATE;\n" + sql + check + "\nROLLBACK;\n")
        say(f"  rehearsal on the live file: {out.splitlines()[-1] if out else '(no answer)'}"
            f" -- {'as expected' if out.endswith(want) else 'NOT AS EXPECTED, want ' + want}")
        after = must(host, f"sqlite3 -readonly {lis} \"SELECT (SELECT COUNT(*) FROM id_reviews), "
                           f"(SELECT COUNT(*) FROM boundary_reviews)\"", "counting")
        say(f"  rolled back: holds {after} (was {before})")
        return 0 if out.endswith(want) and after == before else 1

    have = ssh(host, "systemctl list-unit-files lempi.service 2>/dev/null | grep -c '^lempi.service'")[1].strip()
    has_unit = have not in ("", "0")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%MZ")
    backup = f"{os.path.dirname(lis)}/listener-pre-review-repair-{stamp}.db"
    rc = 0
    try:
        if has_unit:
            must(host, "sudo systemctl stop lempi", "stopping the player")
            say("  player stopped")
        must(host, f"sqlite3 {lis} \".backup {backup}\"", "the backup")
        ok = must(host, f"sqlite3 -readonly {backup} 'PRAGMA integrity_check'", "checking the backup")
        if ok != "ok":
            raise SystemExit(f"the backup failed its integrity check ({ok}): nothing written")
        say(f"  backed up to {backup}, integrity ok")
        out = must(host, f"sqlite3 -bail {lis}", "the rewrite", stdin="BEGIN IMMEDIATE;\n" + sql + "COMMIT;\n" + check + "\n")
        say(f"  written: {out.splitlines()[-1]} -- {'as expected' if out.endswith(want) else 'NOT AS EXPECTED, want ' + want}")
        rc = 0 if out.endswith(want) else 1
    finally:
        if has_unit:
            r, o = ssh(host, "sudo systemctl start lempi && sleep 3 && systemctl is-active lempi")
            say(f"  player started again: {o.splitlines()[-1] if o else '?'}")
            rc = rc or (0 if r == 0 else 1)
    return rc


if __name__ == "__main__":
    sys.exit(main())
