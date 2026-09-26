#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Carry a star merge's result to a node as a patch, not a file [SPEC-STAR-080].

`make` compares a node's snapshot (the baseline) with the copy the merge
wrote for it (the target) and writes every row that differs, with both
values. `apply` runs on the node, against its live database, and applies
each row only where the node still holds the baseline -- the same three
ways `apply_changes.py` uses [SPEC006 §9]:

  current == baseline  -> nothing changed here since the snapshot: applied
  current == target    -> already there: nothing to do
  anything else        -> changed here since the snapshot: a CONFLICT

A single conflict and nothing is written. Rows the patch does not name --
the plays a node recorded after its snapshot [REQ-PD-113] -- are never
touched. Rehearses by default; `--commit` writes. Standard library only,
so the node needs nothing but python3.

    python tools/star_patch.py make SNAPSHOT.db TARGET.db -o node.patch.json
    python3 star_patch.py apply /var/lempi/listener.db node.patch.json
    python3 star_patch.py apply /var/lempi/listener.db node.patch.json --commit
    python3 star_patch.py backup /var/lempi/listener.db /var/lempi/pre-star/listener.db
    python3 star_patch.py fingerprint /var/lempi/listener.db
    python3 star_patch.py restore /var/lempi/pre-star/listener.db /var/lempi/listener.db

Run `apply --commit` with the player stopped, as every write to a live
database here is `[PI5-LIB-010]`.
"""
import base64
import hashlib
import json
import os
import pathlib
import sqlite3
import sys


def open_ro(path):
    uri = pathlib.Path(path).resolve().as_uri() + "?immutable=1"
    return sqlite3.connect(uri, uri=True)


def open_current(path):
    """A live database as it stands, WAL included. `immutable` reads the main
    file alone: exact only where no WAL exists -- such as `bose`'s catalogue,
    on a read-only mount where `mode=ro` cannot make its -shm. Found
    2026-09-26: fingerprints taken immutable missed a 375 KB WAL on lp3-wifi."""
    uri = pathlib.Path(path).resolve().as_uri()
    if os.path.exists(path + "-wal") and os.path.getsize(path + "-wal") > 0:
        return sqlite3.connect(uri + "?mode=ro", uri=True)
    try:
        c = sqlite3.connect(uri + "?mode=ro", uri=True)
        c.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return c
    except sqlite3.OperationalError:
        return open_ro(path)


def enc(v):
    """A value JSON can carry exactly: a blob as base64."""
    return {"b64": base64.b64encode(v).decode()} if isinstance(v, (bytes, bytearray)) else v


def dec(v):
    return base64.b64decode(v["b64"]) if isinstance(v, dict) else v


def tables(c):
    return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                    "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def columns(c, t):
    return [r[1] for r in c.execute(f"PRAGMA table_info({t})")]


def key_of(c, t):
    """The primary key; a table without one is keyed by the whole row."""
    pk = [r[1] for r in sorted(c.execute(f"PRAGMA table_info({t})"), key=lambda r: r[5]) if r[5]]
    return pk or columns(c, t)


def load(c, t, cols, key):
    idx = [cols.index(k) for k in key]
    return {tuple(r[i] for i in idx): r for r in c.execute(f"SELECT {', '.join(cols)} FROM {t}")}


def make(baseline, target, out):
    b, t = open_ro(baseline), open_ro(target)
    patch = {"baseline": sha256(baseline), "target": sha256(target), "tables": []}
    extra = sorted(set(tables(b)) - set(tables(t)))
    if extra:
        raise SystemExit(f"the target lacks table(s) {extra}: a patch adds and changes, it never drops a table")
    for name in tables(t):
        cols = columns(t, name)
        entry = {"name": name, "columns": cols, "key": key_of(t, name), "rows": []}
        if name in tables(b):
            have = columns(b, name)
            if set(have) - set(cols):
                raise SystemExit(f"{name}: the target lacks column(s) {sorted(set(have) - set(cols))} "
                                 "-- a patch never drops a column")
            # A column the node's copy lacks is added as the player adds it
            # on open (`md5_generator`, `ensure_md5_generator_column`), and
            # the node's rows read with its default meanwhile.
            added = [r for r in t.execute(f"PRAGMA table_info({name})") if r[1] not in have]
            if added:
                entry["add_columns"] = [column_decl(r) for r in added]
            base = {}
            fill = {r[1]: default_of(r) for r in added}
            for row in b.execute(f"SELECT {', '.join(have)} FROM {name}"):
                full = dict(zip(have, row), **fill)
                base[tuple(full[c] for c in entry["key"])] = tuple(full[c] for c in cols)
        else:
            entry["create"] = [r[0] for r in t.execute(
                "SELECT sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL "
                "ORDER BY type DESC, name", (name,))]      # the table, then its indexes
            base = {}
        want = load(t, name, cols, entry["key"])
        for k in sorted(set(base) | set(want), key=lambda k: repr(k)):
            was, now = base.get(k), want.get(k)
            if was != now:
                # The baseline travels as a digest: enough to recognise it,
                # and a cover-art row whose `fetched_at` moved does not carry
                # its image twice. Only the columns that change are sent.
                rec = {"key": [enc(v) for v in k], "was": digest(was), "now": digest(now)}
                if now is not None:
                    rec["set"] = {c: enc(v) for i, (c, v) in enumerate(zip(cols, now))
                                  if was is None or was[i] != v}
                entry["rows"].append(rec)
        if entry["rows"] or "create" in entry or "add_columns" in entry:
            patch["tables"].append(entry)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(patch, fh, separators=(",", ":"), sort_keys=True)
    for e in patch["tables"]:
        print(f"{e['name']}: {len(e['rows'])} row(s)" + (" (new table)" if "create" in e else "")
              + "".join(f" (adds {d})" for d in e.get("add_columns", [])))
    print(f"wrote {out}: {sum(len(e['rows']) for e in patch['tables'])} row(s) "
          f"in {len(patch['tables'])} table(s)")
    return 0


def column_decl(info):
    """`ALTER TABLE ... ADD COLUMN` text for a PRAGMA table_info row -- only
    what SQLite can add to a table that already has rows."""
    _, name, typ, notnull, dflt, pk = info
    if pk or (notnull and dflt is None):
        raise SystemExit(f"column {name} cannot be added to a table with rows "
                         "(a key, or NOT NULL without a default)")
    return f"{name} {typ}".strip() + (" NOT NULL" if notnull else "") + (
        f" DEFAULT {dflt}" if dflt is not None else "")


def default_of(info):
    dflt = info[4]
    return None if dflt is None else sqlite3.connect(":memory:").execute(f"SELECT {dflt}").fetchone()[0]


def digest(row):
    """A row's identity for comparison, the same on every Python and platform."""
    if row is None:
        return None
    text = json.dumps([enc(v) for v in row], separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def show(row):
    return None if row is None else [f"<{len(v)} bytes>" if isinstance(v, bytes) else v for v in row]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def apply(db, patch_path, commit):
    with open(patch_path, encoding="utf-8") as fh:
        patch = json.load(fh)
    c = sqlite3.connect(db, isolation_level=None)
    c.execute("BEGIN IMMEDIATE")
    counts = {"applied": 0, "already": 0, "conflict": 0}
    conflicts, deletes, inserts = [], [], []
    for e in patch["tables"]:
        name, cols, key = e["name"], e["columns"], e["key"]
        if "create" in e and name not in tables(c):
            for sql in e["create"]:
                c.execute(sql)
        for decl in e.get("add_columns", []):
            if decl.split()[0] not in columns(c, name):
                c.execute(f"ALTER TABLE {name} ADD COLUMN {decl}")
        if sorted(columns(c, name)) != sorted(cols):
            c.execute("ROLLBACK")
            raise SystemExit(f"{name}: this database's columns {columns(c, name)} are not the "
                             f"patch's {cols}; nothing written")
        where = " AND ".join(f"{k} IS ?" for k in key)
        # By its key where it has one (a WITHOUT ROWID table has no rowid);
        # one instance of a keyless table's row by rowid.
        keyed = any(r[5] for r in c.execute(f"PRAGMA table_info({name})"))
        delete = (f"DELETE FROM {name} WHERE {where}" if keyed else
                  f"DELETE FROM {name} WHERE rowid = (SELECT rowid FROM {name} WHERE {where} LIMIT 1)")
        for r in e["rows"]:
            k = [dec(v) for v in r["key"]]
            cur = c.execute(f"SELECT {', '.join(cols)} FROM {name} WHERE {where} LIMIT 1", k).fetchone()
            held = digest(cur)
            if held == r["now"]:
                counts["already"] += 1
            elif held == r["was"]:
                counts["applied"] += 1
                if cur is not None:
                    deletes.append((delete, k))
                if r["now"] is not None:
                    full = dict(zip(cols, cur or [None] * len(cols)))
                    full.update({col: dec(v) for col, v in r["set"].items()})
                    now = [full[col] for col in cols]
                    if digest(now) != r["now"]:
                        c.execute("ROLLBACK")
                        raise SystemExit(f"{name} {k}: the patch does not rebuild its own target; "
                                         "nothing written")
                    inserts.append((f"INSERT INTO {name} ({', '.join(cols)}) "
                                    f"VALUES ({', '.join('?' * len(cols))})", now))
            else:
                counts["conflict"] += 1
                conflicts.append((name, k, show(cur), {col: show([dec(v)])[0] for col, v in r.get("set", {}).items()}))
    # Every delete before any insert: two rows that swap a UNIQUE value --
    # two files inducted in the other order [SPEC-STAR-049] -- collide
    # if each is replaced in turn.
    if not conflicts:
        try:
            for sql, args in deletes + inserts:
                c.execute(sql, args)
        except sqlite3.Error as err:
            c.execute("ROLLBACK")
            print(f"RESULT=refused: {err}; nothing written")
            return 1
    print(f"{db}: {counts['applied']} to apply, {counts['already']} already there, "
          f"{counts['conflict']} conflict(s)")
    for name, k, cur, now in conflicts[:50]:
        print(f"  CONFLICT {name} {k}: holds {cur!r}, not its snapshot; the patch would set {now!r}")
    if conflicts:
        c.execute("ROLLBACK")
        print("RESULT=conflict: nothing written -- take a fresh snapshot and merge again")
        return 1
    if not commit:
        c.execute("ROLLBACK")
        print("RESULT=rehearsed: nothing written; --commit to apply")
        return 0
    c.execute("COMMIT")
    check = c.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"RESULT=committed: integrity {check}")
    return 0 if check == "ok" else 1


def backup(src, dst):
    """A consistent copy of a live database, through SQLite's backup API
    [SPEC-STAR-075] -- never a file copy, which misses what the WAL holds."""
    if os.path.exists(dst):
        raise SystemExit(f"{dst} exists: a backup is never written over")
    s = open_current(src)
    d = sqlite3.connect(dst)
    s.backup(d)
    check = d.execute("PRAGMA integrity_check").fetchone()[0]
    d.close()
    s.close()
    print(f"BACKUP {dst} {os.path.getsize(dst)} {sha256(dst)} integrity {check}")
    return 0 if check == "ok" else 1


def restore(src, dst):
    """Put a backup back over a database, through the same API: every page
    replaced, so a patch that landed is undone whole."""
    s = open_ro(src)
    d = sqlite3.connect(dst)
    s.backup(d)
    check = d.execute("PRAGMA integrity_check").fetchone()[0]
    d.close()
    print(f"RESTORED {dst} from {src}: integrity {check}")
    return 0 if check == "ok" else 1


def fingerprint(db):
    """One line per table: name, rows, and a digest of every row over every
    column, independent of order -- two copies print the same line exactly
    when they hold the same rows."""
    c = open_current(db)
    for t in tables(c):
        cols = sorted(columns(c, t))
        acc, n = 0, 0
        for r in c.execute(f"SELECT {', '.join(cols)} FROM {t}"):
            acc ^= int.from_bytes(hashlib.sha256(repr(r).encode()).digest()[:16], "big")
            n += 1
        print(f"{t} {n} {acc:032x} {','.join(cols)}")
    return 0


def main(argv):
    if len(argv) == 3 and argv[0] == "backup":
        return backup(argv[1], argv[2])
    if len(argv) == 3 and argv[0] == "restore":
        return restore(argv[1], argv[2])
    if len(argv) == 2 and argv[0] == "fingerprint":
        return fingerprint(argv[1])
    if len(argv) >= 4 and argv[0] == "make" and argv[3] == "-o" and len(argv) == 5:
        return make(argv[1], argv[2], argv[4])
    if len(argv) in (3, 4) and argv[0] == "apply" and argv[3:] in ([], ["--commit"]):
        return apply(argv[1], argv[2], argv[3:] == ["--commit"])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
