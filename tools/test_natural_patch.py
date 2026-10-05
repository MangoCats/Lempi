#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The catalogue patch by natural key, builder side [SPEC-NKP-040..090].

`fixtures/natural_patch/` holds a hub's catalogue before and after, and a node
that numbers the same music differently. This builds the patch from the first
two and holds it to `patch.json`; applies it, by a reference implementation of
what `lempi-core`'s `mesh_sync.rs` does, to the node; and holds the result to
`expected.json`. The Rust test reads the same three files, so the builder and
the applier cannot drift apart.

    python tools/test_natural_patch.py            check
    python tools/test_natural_patch.py --write    regenerate patch.json and expected.json
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(os.path.dirname(HERE), "fixtures", "natural_patch")
sys.path.insert(0, HERE)
import star_patch as sp  # noqa: E402

FAILED = []
MACHINE = {"files": ["path", "first_seen"]}


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def build_db(path, *scripts):
    c = sqlite3.connect(path)
    for name in scripts:
        with open(os.path.join(FIX, name), encoding="utf-8") as fh:
            c.executescript(fh.read())
    c.commit()
    c.close()


def dump(path):
    """The catalogue named without ids, as the Rust test dumps it."""
    c = sqlite3.connect(path)
    out = [f"file {r[0]}" for r in c.execute("SELECT audio_md5 FROM files")]
    out += [f"passage {a} {s}-{e}" for a, s, e in c.execute(
        "SELECT f.audio_md5, p.start_ms, p.end_ms FROM passages p JOIN files f USING (file_id)")]
    out += [f"rec {a} {s}-{e} {m} {w}" for a, s, e, m, w in c.execute(
        "SELECT f.audio_md5, p.start_ms, p.end_ms, r.mbid, r.weight FROM passage_recordings r "
        "JOIN passages p USING (passage_id) JOIN files f USING (file_id)")]
    c.close()
    return sorted(out)


# ----------------------------------------------------- the applier, in Python --
def to_local(c, kind, nat):
    if nat is None:
        return None
    if kind == "file":
        r = c.execute("SELECT file_id FROM files WHERE audio_md5 = ?", (nat,)).fetchone()
    else:
        md5, k, s, e = json.loads(nat) if isinstance(nat, str) else nat
        r = c.execute("SELECT p.passage_id FROM passages p JOIN files f USING (file_id) "
                      "WHERE f.audio_md5 = ? AND p.kind = ? AND p.start_ms = ? AND p.end_ms = ?", (md5, k, s, e)).fetchone()
    return r[0] if r else None


def to_natural(c, kind, local):
    if kind == "file":
        r = c.execute("SELECT audio_md5 FROM files WHERE file_id = ?", (local,)).fetchone()
        return r[0] if r else None
    r = c.execute("SELECT f.audio_md5, p.kind, p.start_ms, p.end_ms FROM passages p JOIN files f USING (file_id) "
                  "WHERE p.passage_id = ?", (local,)).fetchone()
    return list(r) if r else None


def ref_of(entry, col, row_by_col):
    for r in entry["natural"]["refs"]:
        if r["col"] == col and (not r.get("when") or row_by_col.get(r["when"][0]) == r["when"][1]):
            return r
    return None


def apply_natural(db, patch):
    """`[SPEC-NKP-060]`: check every row against the database before the patch, then write
    parents first. Refuses the whole patch on one row not as expected; returns the counts."""
    c = sqlite3.connect(db)
    counts = {"applied": 0, "already": 0}
    plans = []
    try:
        for entry in patch["tables"]:
            name, cols, key = entry["name"], entry["columns"], entry["key"]
            if "natural" not in entry:
                raise SystemExit("this reference applier takes natural entries only")
            have = [r[1] for r in c.execute(f"PRAGMA table_info({name})")]
            for decl in entry.get("add_columns", []):
                if decl.split()[0] not in have:
                    c.execute(f"ALTER TABLE {name} ADD COLUMN {decl}")

            def naturalize(vals):
                by = dict(zip(cols, vals))
                out = []
                for col, v in zip(cols, vals):
                    r = ref_of(entry, col, by)
                    out.append(to_natural(c, r["kind"], int(v)) if r and v is not None else v)
                return out

            def resolve(k):
                by = dict(zip(key, k))
                out = []
                for col, v in zip(key, k):
                    r = ref_of(entry, col, by)
                    if r and v is not None:
                        lv = to_local(c, r["kind"], v)
                        if lv is None:
                            return None
                        v = str(lv) if r.get("when") else lv
                    out.append(v)
                return out

            def row_at(lk):
                where = " AND ".join(f"{k} IS ?" for k in key)
                r = c.execute(f"SELECT {', '.join(cols)} FROM {name} WHERE {where} LIMIT 1", lk).fetchone()
                return None if r is None else naturalize(list(r))

            rows = []
            for r in entry["rows"]:
                lk = resolve(r["key"])
                cur = row_at(lk) if lk is not None else None
                if cur == r["now"]:
                    rows.append((lk, True))
                    continue
                if r["now"] is not None:
                    nk = [r["now"][cols.index(k)] for k in key]
                    nlk = resolve(nk)
                    if nk != r["key"] and nlk is not None and row_at(nlk) == r["now"]:
                        rows.append((nlk, True))
                        continue
                if cur != r["was"]:
                    raise SystemExit(f"{name}: the row keyed {r['key']} is not as the patch expects")
                rows.append((lk, False))
            plans.append(rows)
        for entry, rows in zip(patch["tables"], plans):
            name, cols, key = entry["name"], entry["columns"], entry["key"]

            def localize(vals):
                by = dict(zip(cols, vals))
                out = []
                for col, v in zip(cols, vals):
                    r = ref_of(entry, col, by)
                    if r and v is not None:
                        lv = to_local(c, r["kind"], v)
                        if lv is None:
                            raise SystemExit(f"{name}: {col} names a {r['kind']} this node does not hold")
                        v = str(lv) if r.get("when") else lv
                    out.append(v)
                return out

            where = " AND ".join(f"{k} IS ?" for k in key)
            for r, (lk, already) in zip(entry["rows"], rows):
                if already:
                    counts["already"] += 1
                    continue
                if r["was"] is None:
                    names, vals = list(cols), localize(r["now"])
                    for col, v in (r.get("also") or {}).items():
                        names.append(col)
                        vals.append(v)
                    c.execute(f"INSERT INTO {name} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})", vals)
                elif r["now"] is None:
                    c.execute(f"DELETE FROM {name} WHERE {where}", lk)
                else:
                    c.execute(f"UPDATE {name} SET {', '.join(f'{col} = ?' for col in cols)} WHERE {where}",
                              localize(r["now"]) + lk)
                counts["applied"] += 1
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()
    return counts


# --------------------------------------------------------------------- the test --
def canon(p):
    return json.dumps(p, indent=1, sort_keys=True) + "\n"


def main() -> int:
    write = "--write" in sys.argv
    tmp = tempfile.mkdtemp()
    base, target, node = (os.path.join(tmp, n) for n in ("base.db", "target.db", "node.db"))
    build_db(base, "schema.sql", "baseline.sql")
    build_db(target, "schema.sql", "target.sql")
    build_db(node, "schema.sql", "node.sql")
    tables = list(sp.NATURAL)
    patch = sp.make_values(base, target, tables=tables, exclude=MACHINE, natural=True)
    if write:
        with open(os.path.join(FIX, "patch.json"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(canon(patch))
        with open(os.path.join(FIX, "expected.json"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(canon(dump(target)))
        print("fixtures written")
    with open(os.path.join(FIX, "patch.json"), encoding="utf-8") as fh:
        held = json.load(fh)
    check(canon(patch) == canon(held), "the builder's patch is the fixture's patch.json (--write regenerates it)")
    with open(os.path.join(FIX, "expected.json"), encoding="utf-8") as fh:
        expected = json.load(fh)
    check(dump(target) == expected, "and expected.json is the target, named without ids")

    names = [e["name"] for e in patch["tables"]]
    check(names == ["files", "passages", "passage_recordings"], f"parents before children, only what changed: {names}")
    files = next(e for e in patch["tables"] if e["name"] == "files")
    check(files["columns"] == ["audio_md5"] and "file_id" not in files["columns"],
          f"the surrogate and the machine-scope columns are not carried: {files['columns']}")
    check(not any(r["key"] == ["A"] for r in files["rows"]), "a file whose only change is a machine-scope column is not sent")
    rekey = next(r for r in files["rows"] if r["key"] == ["C"])
    check(rekey["was"] == ["C"] and rekey["now"] == ["C2"], f"a re-keyed file is one change, under its old key: {rekey}")
    new = next(r for r in files["rows"] if r["key"] == ["D"])
    check(new["was"] is None and new["also"] == {"path": "/hub/d", "first_seen": "hub-time"},
          f"a new file brings its machine-scope values as `also`: {new}")
    pas = next(e for e in patch["tables"] if e["name"] == "passages")
    check([r["key"] for r in pas["rows"]] == [["B", "radio", 0, 500], ["D", "radio", 0, 900]],
          f"a passage whose boundary moved is sent under its old span, and a child naming only a re-keyed file is not: "
          f"{[r['key'] for r in pas['rows']]}")
    kids = next(e for e in patch["tables"] if e["name"] == "passage_recordings")
    check(len(kids["rows"]) == 3 and any(r["now"] is None for r in kids["rows"]), "a child's delete, change and insert")

    # The reference applier takes the node to the target, whatever the node numbers.
    got = apply_natural(node, patch)
    check(dump(node) == expected, f"applied to a node numbered differently, it gives the target: {dump(node)}")
    check(got["applied"] > 0 and got["already"] == 0, f"{got}")
    c = sqlite3.connect(node)
    rows = dict(c.execute("SELECT audio_md5, file_id FROM files"))
    c.close()
    check(rows["D"] == 4 and rows["C2"] == 2 and rows["B"] == 3,
          f"the node numbered the new file itself and kept its own id for the re-keyed one: {rows}")
    again = apply_natural(node, patch)
    check(again == {"applied": 0, "already": got["applied"]}, f"a second apply finds everything already there: {again}")

    # Refusal is all or nothing.
    n2 = os.path.join(tmp, "node2.db")
    build_db(n2, "schema.sql", "node.sql")
    before = dump(n2)
    bad = json.loads(canon(patch))
    next(e for e in bad["tables"] if e["name"] == "passages")["rows"][0]["was"][3] = 499
    try:
        apply_natural(n2, bad)
        check(False, "a row not as expected must refuse the patch")
    except SystemExit as err:
        check("not as the patch expects" in str(err) and dump(n2) == before, f"refused, and nothing written: {err}")

    # A column the hub gained since the last sync is added, and a row reads the default until the patch says otherwise.
    wide = os.path.join(tmp, "wide.db")
    shutil.copyfile(target, wide)
    c = sqlite3.connect(wide)
    c.execute("ALTER TABLE passages ADD COLUMN held INTEGER DEFAULT 0")
    c.execute("UPDATE passages SET held = 1 WHERE passage_id = 13")
    c.commit()
    c.close()
    p2 = sp.make_values(base, wide, tables=tables, exclude=MACHINE, natural=True)
    pe = next(e for e in p2["tables"] if e["name"] == "passages")
    check(pe["add_columns"] == ["held INTEGER DEFAULT 0"] and "held" in pe["columns"], f"the new column is declared and carried: {pe['add_columns']}")
    n3 = os.path.join(tmp, "node3.db")
    build_db(n3, "schema.sql", "node.sql")
    apply_natural(n3, p2)
    c = sqlite3.connect(n3)
    held = dict(c.execute("SELECT f.audio_md5, p.held FROM passages p JOIN files f USING (file_id)"))
    c.close()
    check(held == {"A": 0, "B": 0, "C2": 0, "D": 1} and dump(n3) == expected, f"applied, the column is there with the right values: {held}")

    # [SPEC-NKP-082]: a column that is the hub's own bookkeeping travels in neither direction: not as a change, not as
    # a new column, and not as `also` on a row the node lacks. (Found live: a node refused a cover_art row identical to
    # the hub's but for the hub's `caa_asked_at`, a column the node does not have.)
    ob, ot = os.path.join(tmp, "ob.db"), os.path.join(tmp, "ot.db")
    for db_, extra in ((ob, ""), (ot, ", note TEXT")):
        c = sqlite3.connect(db_)
        c.execute(f"CREATE TABLE cover_art (release_mbid TEXT PRIMARY KEY, front BLOB{extra})")
        c.commit()
        c.close()
    c = sqlite3.connect(ob)
    c.execute("INSERT INTO cover_art VALUES ('r1', x'01')")
    c.commit()
    c.close()
    c = sqlite3.connect(ot)
    c.execute("INSERT INTO cover_art VALUES ('r1', x'01', 'asked on the 2nd')")
    c.execute("INSERT INTO cover_art VALUES ('r2', x'02', 'asked on the 3rd')")
    c.commit()
    c.close()
    with_note = sp.make_values(ob, ot, tables=["cover_art"], exclude={})
    check(with_note["tables"][0].get("add_columns") == ["note TEXT"] and len(with_note["tables"][0]["rows"]) == 2,
          "without `omit`, the column is added and both rows differ (what refused the real node)")
    quiet = sp.make_values(ob, ot, tables=["cover_art"], exclude={}, omit={"cover_art": ["note"]})
    rows = quiet["tables"][0]["rows"]
    check("add_columns" not in quiet["tables"][0] and quiet["tables"][0]["columns"] == ["release_mbid", "front"],
          f"with it, no column is added and none is carried: {quiet['tables'][0]}")
    check([r["key"] for r in rows] == [["r2"]] and "also" not in rows[0], f"only the genuinely new row is sent, and with no `also`: {rows}")

    # [SPEC-NKP-050]: a node's own column. `file_tags.scanned_at` is when this machine's tag scan ran.
    # A row rescanned on the hub, alike in every tag, is not a change; a new row is sent, with the
    # hub's `scanned_at` as `also`, and the node's own is never compared.
    sa, sb = os.path.join(tmp, "sa.db"), os.path.join(tmp, "sb.db")
    schema = ("CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, format TEXT);"
              "CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT, start_ms INTEGER, end_ms INTEGER);"
              "CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, title TEXT, scanned_at INTEGER);"
              "INSERT INTO files VALUES (1,'m1','flac');INSERT INTO file_tags VALUES (1,'One',100);")
    for path in (sa, sb):
        c = sqlite3.connect(path)
        c.executescript(schema)
        c.commit()
        c.close()
    c = sqlite3.connect(sb)
    c.executescript("UPDATE file_tags SET scanned_at=200 WHERE file_id=1;"
                    "INSERT INTO files VALUES (2,'m2','flac');INSERT INTO file_tags VALUES (2,'Two',300);")
    c.commit()
    c.close()
    ft = sp.make_values(sa, sb, tables=["files", "file_tags"], exclude={"file_tags": ["scanned_at"]}, natural=True)
    entry = [t for t in ft["tables"] if t["name"] == "file_tags"][0]
    check(entry["columns"] == ["file_id", "title"], f"scanned_at is not carried: {entry['columns']}")
    check([r["key"] for r in entry["rows"]] == [["m2"]] and entry["rows"][0]["also"] == {"scanned_at": 300},
          f"the rescanned row is no change; the new one goes with the hub's scan time as `also`: {entry['rows']}")

    # Nothing to send when nothing changed.
    same = sp.make_values(target, target, tables=tables, exclude=MACHINE, natural=True)
    check(same["tables"] == [], "an unchanged catalogue gives an empty patch")
    wider = os.path.join(tmp, "wider.db")
    shutil.copyfile(target, wider)
    c = sqlite3.connect(wider)
    c.execute("CREATE TABLE id_checks (passage_id INTEGER PRIMARY KEY, verdict TEXT)")
    c.execute("INSERT INTO id_checks VALUES (10, 'ok')")
    c.commit()
    c.close()
    try:
        sp.make_values(base, wider, tables=tables, exclude=MACHINE, natural=True)
    except sp.NaturalUnsupported as err:
        check("baseline lacks the table" in str(err), f"{err}")
    else:
        check(False, "a catalogue pair the builder cannot name is refused, loudly")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("natural patch: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
