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
    python3 star_patch.py check /srv/library/library.db node.patch.json   # read-only
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
    b.close()
    t.close()
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(patch, fh, separators=(",", ":"), sort_keys=True)
    for e in patch["tables"]:
        print(f"{e['name']}: {len(e['rows'])} row(s)" + (" (new table)" if "create" in e else "")
              + "".join(f" (adds {d})" for d in e.get("add_columns", [])))
    print(f"wrote {out}: {sum(len(e['rows']) for e in patch['tables'])} row(s) "
          f"in {len(patch['tables'])} table(s)")
    return 0


def make_member(baseline, target, tables):
    """A member's patch [SPEC-MTR-030]: for `tables` only, every row that
    differs, with its old and new values themselves -- not digests, which the
    phone's Rust could not reproduce for every float. Rows about a passage are
    left out both ways: a passage id is the node's own [SPEC-STAR-049]."""
    b, t = open_ro(baseline), open_ro(target)
    patch = {"tables": []}
    try:
        for name in tables:
            if name not in tables_of(t):
                continue
            cols = columns(t, name)
            key = key_of(t, name)
            local = "subject_kind" in cols
            base = load(b, name, cols, key) if name in tables_of(b) and columns(b, name) == cols else {}
            want = load(t, name, cols, key)
            rows = []
            for k in sorted(set(base) | set(want), key=repr):
                was, now = base.get(k), want.get(k)
                row = now or was
                if was != now and not (local and row[cols.index("subject_kind")] == "passage"):
                    rows.append({"key": [enc(v) for v in k],
                                 "was": None if was is None else [enc(v) for v in was],
                                 "now": None if now is None else [enc(v) for v in now]})
            if rows:
                patch["tables"].append({"name": name, "columns": cols, "key": key, "rows": rows})
    finally:
        b.close()
        t.close()
    return patch


def tables_of(c):
    return set(tables(c))


def apply_member(db, patch, tables):
    """The phone's rule, here, for proving a member patch before it leaves and
    for tests: a row changes only where it still holds `was`; one changed
    since is kept. Mirrors `lempi_core::mesh_sync::apply`."""
    c = sqlite3.connect(db)
    counts = {"applied": 0, "already": 0, "kept": 0}
    try:
        for e in patch["tables"]:
            if e["name"] not in tables:
                raise SystemExit(f"{e['name']} is not a shared table")
            cols, key, name = e["columns"], e["key"], e["name"]
            where = " AND ".join(f"{k} IS ?" for k in key)
            for r in e["rows"]:
                k = [dec(v) for v in r["key"]]
                cur = c.execute(f"SELECT {', '.join(cols)} FROM {name} WHERE {where} LIMIT 1", k).fetchone()
                now = None if r["now"] is None else tuple(dec(v) for v in r["now"])
                was = None if r["was"] is None else tuple(dec(v) for v in r["was"])
                if cur == now:
                    counts["already"] += 1
                elif cur == was:
                    c.execute(f"DELETE FROM {name} WHERE {where}", k)
                    if now is not None:
                        c.execute(f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", now)
                    counts["applied"] += 1
                else:
                    counts["kept"] += 1
        c.commit()
    finally:
        c.close()
    return counts


# ---------------------------------------------------------------- natural keys --
# [SPEC-NKP-020..045]: the tables that hold ids, and how each is named without
# them. `lempi-core`'s `mesh_sync.rs` applies what this builds, and a fixture
# both read holds the two to the same meaning (`fixtures/natural_patch/`).
#
#   surrogate  the table's own id, which a patch never carries
#   refs       (column, "file" | "passage"[, (when_column, when_value)])
#   key        what names a row in the patch, as the node finds it
NATURAL = {
    "files": dict(surrogate="file_id", refs=[], key=["audio_md5"]),
    "passages": dict(surrogate="passage_id", refs=[("file_id", "file")],
                     key=["file_id", "kind", "start_ms", "end_ms"]),
    "file_tags": dict(surrogate=None, refs=[("file_id", "file")], key=["file_id"]),
    "passage_recordings": dict(surrogate=None, refs=[("passage_id", "passage")], key=["passage_id", "mbid"]),
    "id_checks": dict(surrogate=None, refs=[("passage_id", "passage")], key=["passage_id"]),
    "flavor": dict(surrogate=None, refs=[("subject_id", "passage", ("subject_kind", "passage"))],
                   key=["subject_kind", "subject_id", "characteristic", "class"]),
}
# Parents before the rows that name them: the node writes in this order.
NATURAL_ORDER = ["files", "passages", "file_tags", "passage_recordings", "id_checks", "flavor"]


class NaturalUnsupported(Exception):
    """A catalogue pair this builder will not make a natural patch for, and why."""


def id_maps(c):
    """Each local id's natural name: a file's audio_md5, a passage's [md5, kind, start, end]."""
    files = {r[0]: r[1] for r in c.execute("SELECT file_id, audio_md5 FROM files")}
    passages = {r[0]: [r[1], r[2], r[3], r[4]] for r in c.execute(
        "SELECT p.passage_id, f.audio_md5, p.kind, p.start_ms, p.end_ms FROM passages p JOIN files f USING (file_id)")}
    return files, passages


def natural_values(name, d, cols, files, passages):
    """A row's carried values with each reference replaced by what it names."""
    out = []
    for c in cols:
        v = d[c]
        for ref in NATURAL[name]["refs"]:
            if ref[0] != c or v is None:
                continue
            if len(ref) == 3 and d[ref[2][0]] != ref[2][1]:
                continue
            maps = files if ref[1] == "file" else passages
            try:
                key = int(v)
            except (TypeError, ValueError):
                raise NaturalUnsupported(f"{name}.{c}: {v!r} is not an id")
            if key not in maps:
                raise NaturalUnsupported(f"{name}.{c}: {v!r} names a {ref[1]} the catalogue does not hold")
            v = maps[key]
            break
        out.append(enc(v))
    return out


def natural_entry(b, t, name, skip):
    """The patch entry for one id-bearing table, or None where nothing changed
    [SPEC-NKP-040]. Rows are paired by the hub's own key, which both catalogues
    share, so a row whose natural key changed -- a boundary edited, a file
    re-keyed -- is one change, not a delete and an add [SPEC-NKP-045]."""
    spec = NATURAL[name]
    if name not in tables_of(t):
        return None
    if name not in tables_of(b):
        raise NaturalUnsupported(f"{name}: the baseline lacks the table")
    full = columns(t, name)
    bcols = columns(b, name)
    if set(bcols) - set(full):
        raise NaturalUnsupported(f"{name}: the target lacks column(s) {sorted(set(bcols) - set(full))}: a patch never drops one")
    # A column the node's copy lacks is added as the player adds it on open, and the
    # rows read with its default meanwhile, as `make_values` does for the rest.
    added = [r for r in t.execute(f"PRAGMA table_info({name})") if r[1] not in bcols]
    try:
        decls = [column_decl(r) for r in added]
    except SystemExit as err:
        raise NaturalUnsupported(f"{name}: {err}")
    fill = {r[1]: default_of(r) for r in added}
    cols = [c for c in full if c != spec["surrogate"] and c not in skip]
    if any(k not in cols for k in spec["key"]):
        raise NaturalUnsupported(f"{name}: its natural key is not among the columns carried")
    pk = key_of(t, name)

    def load(c, have, extra):
        files, passages = id_maps(c)
        rows = {}
        for row in c.execute(f"SELECT {', '.join(have)} FROM {name}"):
            d = dict(zip(have, row), **extra)
            rows[tuple(d[k] for k in pk)] = (d, natural_values(name, d, cols, files, passages))
        return rows

    old, new = load(b, bcols, fill), load(t, full, {})
    rows = []
    for ident in sorted(set(old) | set(new), key=repr):
        bd, bn = old.get(ident, (None, None))
        td, tn = new.get(ident, (None, None))
        carried = [c for c in full if c not in skip]
        if bd is not None and td is not None and all(bd[c] == td[c] for c in carried):
            continue                       # the same row in the hub's own terms, whatever it is now called
        named = dict(zip(cols, bn if bn is not None else tn))
        rec = {"key": [named[k] for k in spec["key"]], "was": bn, "now": tn}
        if bd is None and skip:
            rec["also"] = {c: enc(td[c]) for c in full if c in skip}
        rows.append(rec)
    if not rows and not decls:
        return None
    refs = []
    for ref in spec["refs"]:
        r = {"col": ref[0], "kind": ref[1]}
        if len(ref) == 3:
            r["when"] = list(ref[2])
        refs.append(r)
    entry = {"name": name, "natural": {"surrogate": spec["surrogate"], "refs": refs},
             "columns": cols, "key": spec["key"], "rows": rows}
    if decls:
        entry["add_columns"] = decls
    return entry


def make_values(baseline, target, tables=None, exclude=None, natural=False, omit=None):
    """A patch as values, for the player to apply itself over the signed
    transport [SPEC-NSH-070] -- `lempi_core::mesh_sync::apply_with` reads it.

    Unlike `make_member` it keeps rows about a passage: an appliance's target
    is already in its own passage ids [SPEC-NSH-060]. Unlike `make` it carries
    values, not digests, since Rust cannot reproduce the digest. `tables`
    limits it (the listener's shared tables); `exclude` maps a table to
    columns that travel in neither direction -- a node's own machine-scope
    `files` columns [SPEC-DF-030] -- which a row the node lacks takes from the
    target, as `also`. A table or column the baseline lacks is made, as
    `make` makes it.

    `natural` names the tables that hold ids by what identifies them on every
    installation instead, for a player that can apply that `[SPEC-NKP-040]`;
    they follow the rest, parents first. It raises `NaturalUnsupported` for a
    pair it will not make one for.

    `omit` maps a table to columns that are the hub's own bookkeeping: they travel in
    neither direction and, unlike `exclude`, a row the node lacks is not given the
    hub's value either -- the node need not even have the column `[SPEC-NKP-082]`."""
    exclude = exclude or {}
    omit = omit or {}
    b, t = open_ro(baseline), open_ro(target)
    patch = {"tables": []}
    try:
        extra = sorted((tables_of(b) - tables_of(t)) & set(tables or tables_of(b)))
        if extra:
            raise SystemExit(f"the target lacks table(s) {extra}: a patch never drops a table")
        for name in sorted(tables_of(t) & set(tables or tables_of(t))):
            if natural and name in NATURAL:
                continue
            dropped = set(omit.get(name, ()))
            full = [c for c in columns(t, name) if c not in dropped]
            skip = set(exclude.get(name, ()))
            cols = [c for c in full if c not in skip]
            key = key_of(t, name)
            if set(key) & skip:
                raise SystemExit(f"{name}: a key column cannot be excluded")
            entry = {"name": name, "columns": cols, "key": key, "rows": []}
            base = {}
            if name in tables_of(b):
                have = [c for c in columns(b, name) if c not in dropped]
                if set(have) - set(full):
                    raise SystemExit(f"{name}: the target lacks column(s) {sorted(set(have) - set(full))}")
                added = [r for r in t.execute(f"PRAGMA table_info({name})") if r[1] not in have and r[1] not in dropped]
                if added:
                    entry["add_columns"] = [column_decl(r) for r in added]
                fill = {r[1]: default_of(r) for r in added}
                for row in b.execute(f"SELECT {', '.join(have)} FROM {name}"):
                    d = dict(zip(have, row), **fill)
                    base[tuple(d[c] for c in key)] = tuple(d[c] for c in cols)
            else:
                entry["create"] = [r[0] for r in t.execute(
                    "SELECT sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL "
                    "ORDER BY type DESC, name", (name,))]
            want, also = {}, {}
            for row in t.execute(f"SELECT {', '.join(full)} FROM {name}"):
                d = dict(zip(full, row))
                k = tuple(d[c] for c in key)
                want[k] = tuple(d[c] for c in cols)
                also[k] = {c: enc(d[c]) for c in full if c in skip}
            for k in sorted(set(base) | set(want), key=repr):
                was, now = base.get(k), want.get(k)
                if was != now:
                    r = {"key": [enc(v) for v in k],
                         "was": None if was is None else [enc(v) for v in was],
                         "now": None if now is None else [enc(v) for v in now]}
                    if was is None and also.get(k):
                        r["also"] = also[k]
                    entry["rows"].append(r)
            if entry["rows"] or "create" in entry or "add_columns" in entry:
                patch["tables"].append(entry)
        if natural:
            for name in NATURAL_ORDER:
                if tables is not None and name not in tables:
                    continue
                entry = natural_entry(b, t, name, set(exclude.get(name, ())))
                if entry:
                    patch["tables"].append(entry)
    finally:
        b.close()
        t.close()
    return patch


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
    c = sqlite3.connect(db, isolation_level=None)
    try:
        return _apply(c, db, patch_path, commit)
    finally:
        c.close()   # on Windows an open handle keeps the file from being removed


def _apply(c, db, patch_path, commit):
    with open(patch_path, encoding="utf-8") as fh:
        patch = json.load(fh)
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
    c.close()
    return 0


def check(db, patch_path):
    """What `apply` would do, read-only: no transaction, no copy, nothing
    written -- so it runs against a read-only mount, and needs no room for a
    1.2 GB scratch copy. A table or column the node lacks reads as the patch
    would make it: absent rows, and the column's default."""
    c = open_current(db)
    try:
        return _check(c, db, patch_path)
    finally:
        c.close()


def _check(c, db, patch_path):
    with open(patch_path, encoding="utf-8") as fh:
        patch = json.load(fh)
    counts = {"apply": 0, "already": 0, "conflict": 0}
    have_tables = set(tables(c))
    for e in patch["tables"]:
        name, cols, key = e["name"], e["columns"], e["key"]
        if name not in have_tables:
            if "create" not in e:
                raise SystemExit(f"{name}: not in {db}, and the patch does not create it")
            counts["apply"] += len(e["rows"])
            continue
        have = columns(c, name)
        fill = {}
        for decl in e.get("add_columns", []):
            col = decl.split()[0]
            if col not in have:
                d = decl.split(" DEFAULT ", 1)
                fill[col] = None if len(d) == 1 else \
                    sqlite3.connect(":memory:").execute(f"SELECT {d[1]}").fetchone()[0]
        if sorted(set(have) | set(fill)) != sorted(cols):
            raise SystemExit(f"{name}: columns {have} are not the patch's {cols}")
        where = " AND ".join(f"{k} IS ?" for k in key)
        for r in e["rows"]:
            k = [dec(v) for v in r["key"]]
            cur = c.execute(f"SELECT {', '.join(have)} FROM {name} WHERE {where} LIMIT 1", k).fetchone()
            held = None if cur is None else digest([dict(zip(have, cur), **fill)[col] for col in cols])
            if held == r["now"]:
                counts["already"] += 1
            elif held == r["was"]:
                counts["apply"] += 1
            else:
                counts["conflict"] += 1
                print(f"  CONFLICT {name} {k}: changed here since the snapshot")
    print(f"{db}: {counts['apply']} to apply, {counts['already']} already there, "
          f"{counts['conflict']} conflict(s)")
    print("RESULT=" + ("conflict" if counts["conflict"] else "clean") + ": checked read-only, nothing written")
    return 1 if counts["conflict"] else 0


def main(argv):
    if len(argv) == 3 and argv[0] == "check":
        return check(argv[1], argv[2])
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
