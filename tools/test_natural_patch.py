#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The catalogue patch by natural key, builder side [SPEC-NKP-040..090].

`fixtures/natural_patch/` holds a hub's catalogue before and after, and a node
that numbers the same music differently. Each is given its passages' uids as
Stage A's window gave them, a passage kept since keeping the uid it was born
with. This builds the patch from the first two and holds it to `patch.json`;
applies it, by a reference implementation of what `lempi-core`'s
`mesh_sync.rs` does, to the node; and holds the result to `expected.json`. The
Rust test reads the same files, so the builder and the applier cannot drift
apart. A passage is named by its uid alone since Stage D [IMPL-UID-080].

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
    elif kind == "passage_uid":
        r = c.execute("SELECT passage_id FROM passages WHERE passage_uid = ?", (nat,)).fetchone()
    else:
        raise SystemExit(f"{kind!r} is not a kind of reference: a passage is named by its uid [IMPL-UID-080]")
    return r[0] if r else None


def to_natural(c, kind, local):
    if kind == "file":
        r = c.execute("SELECT audio_md5 FROM files WHERE file_id = ?", (local,)).fetchone()
        return r[0] if r else None
    if kind == "passage_uid":
        r = c.execute("SELECT passage_uid FROM passages WHERE passage_id = ?", (local,)).fetchone()
        return r[0] if r else None
    raise SystemExit(f"{kind!r} is not a kind of reference: a passage is named by its uid [IMPL-UID-080]")


def ref_of(entry, col, row_by_col):
    for r in entry["natural"]["refs"]:
        if r["col"] == col and (not r.get("when") or row_by_col.get(r["when"][0]) == r["when"][1]):
            return r
    return None


def apply_natural(db, patch):
    """`[SPEC-NKP-060]`: check every row against the database before the patch, then write
    parents first. Refuses the whole patch on one row not as expected; returns the counts."""
    c = sqlite3.connect(db)
    # As a node's is: rusqlite's bundled SQLite enforces foreign keys, so a
    # removed file takes its passages with it. Python's default does not, and
    # this applier passed a patch the nodes refused, 2026-10-31.
    c.execute("PRAGMA foreign_keys = ON")
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


UID_COLUMNS = ("passage_uid TEXT", "birth_md5 TEXT", "birth_kind TEXT", "birth_start_ms INTEGER",
               "birth_end_ms INTEGER", "birth_salt INTEGER")


def give_uids(db, born=None):
    """Every passage its uid, as Stage A's window gave them: from its span now, or,
    for a passage `born` names by number, the uid it was born with."""
    import passage_uid as pu
    c = sqlite3.connect(db)
    for col in UID_COLUMNS:
        c.execute(f"ALTER TABLE passages ADD COLUMN {col}")
    for pid, md5, kind, s, e in c.execute("SELECT p.passage_id, f.audio_md5, p.kind, p.start_ms, p.end_ms "
                                         "FROM passages p JOIN files f USING (file_id)").fetchall():
        uid, birth = (born or {}).get(pid) or (pu.passage_uid(md5, kind, s, e), (md5, kind, s, e))
        c.execute("UPDATE passages SET passage_uid = ?, birth_md5 = ?, birth_kind = ?, birth_start_ms = ?, "
                  "birth_end_ms = ?, birth_salt = 0 WHERE passage_id = ?", (uid, *birth, pid))
    c.commit()
    c.close()


def uids_of(db):
    c = sqlite3.connect(db)
    try:
        return {(m, s, e): u for m, s, e, u in c.execute(
            "SELECT f.audio_md5, p.start_ms, p.end_ms, p.passage_uid FROM passages p JOIN files f USING (file_id)")}
    finally:
        c.close()


def catalogues(d):
    """The fixture's three catalogues, built in `d`, each passage given its uid as
    Stage A's window gave them: the baseline's and the node's from their spans (the
    node is in step, so the same uids), the target's the uid each passage kept since
    it was born, a new one's from its span. Returns (base, target, node, born)."""
    os.makedirs(d)
    base, target, node = (os.path.join(d, n) for n in ("base.db", "target.db", "node.db"))
    build_db(base, "schema.sql", "baseline.sql")
    build_db(target, "schema.sql", "target.sql")
    build_db(node, "schema.sql", "node.sql")
    give_uids(base)
    give_uids(node)
    c = sqlite3.connect(base)
    born = {pid: (uid, (m, k, s, e)) for pid, uid, m, k, s, e in c.execute(
        "SELECT passage_id, passage_uid, birth_md5, birth_kind, birth_start_ms, birth_end_ms FROM passages")}
    c.close()
    give_uids(target, born)
    return base, target, node, born


def main() -> int:
    write = "--write" in sys.argv
    tmp = tempfile.mkdtemp()
    base, target, node, born = catalogues(os.path.join(tmp, "fixture"))
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

    # A passage by its uid, and nothing by its span [IMPL-UID-080].
    pas = [e for e in patch["tables"] if e["name"] == "passages"]
    check(all(e["key"] == ["passage_uid"] for e in pas), f"passages are keyed by uid: {[e['key'] for e in pas]}")
    kinds = {r["kind"] for e in patch["tables"] for r in e["natural"]["refs"]}
    check(kinds <= {"file", "passage_uid"}, f"no reference names a passage by its span: {kinds}")
    b_uid = born[11][0]
    moved = [r for e in pas for r in e["rows"] if r["key"] == [b_uid]]
    check(len(moved) == 1 and moved[0]["was"][2:4] == [0, 500] and moved[0]["now"][2:4] == [0, 450],
          f"B's moved boundary is one change under B's own uid: {moved}")

    # Removals first, children before parents, then the rest, parents first -- a
    # node's cascade takes a removed file's passages with it, the refusal on bose and
    # smartboardpc, 2026-10-31 -- and a file's removal last of all, after a passage
    # that moved off it has moved [IMPL-UID-052] step 6.
    names = [(e["name"], all(r["now"] is None for r in e["rows"])) for e in patch["tables"]]
    check(names == [("passage_recordings", True), ("passages", True), ("files", False), ("passages", False),
                    ("passage_recordings", False), ("files", True)],
          f"removals children first, the rest parents first, files' removals last: {names}")
    files = next(e for e in patch["tables"] if e["name"] == "files" and any(r["now"] is not None for r in e["rows"]))
    check(files["columns"] == ["audio_md5"] and "file_id" not in files["columns"],
          f"the surrogate and the machine-scope columns are not carried: {files['columns']}")
    check(not any(r["key"] == ["A"] for r in files["rows"]), "a file whose only change is a machine-scope column is not sent")
    new = next(r for r in files["rows"] if r["key"] == ["D"])
    check(new["was"] is None and new["also"] == {"path": "/hub/d", "first_seen": "hub-time"},
          f"a new file brings its machine-scope values as `also`: {new}")
    refs = {e["name"]: e["natural"]["refs"] for e in patch["tables"]}
    check(refs.get("passage_recordings") == [{"col": "passage_id", "kind": "passage_uid"}],
          f"and its credits name it by uid: {refs.get('passage_recordings')}")

    # The reference applier takes the node to the target, whatever the node numbers.
    got = apply_natural(node, patch)
    check(dump(node) == expected, f"applied to a node numbered differently, it gives the target: {dump(node)}")
    check(uids_of(node) == uids_of(target), f"and every passage there holds the target's uid: {uids_of(node)}")
    check(got["applied"] > 0 and got["already"] == 0, f"{got}")
    again = apply_natural(node, patch)
    check(again == {"applied": 0, "already": got["applied"]}, f"a second apply finds everything already there: {again}")

    # [SPEC-NKP-046]: a baseline adopted from a node holds the node's numbers. Paired by
    # id, every file once came out as a change into another's md5: teacherslounge refused
    # one, 2026-11-05. By what each row is, how the baseline numbers them changes nothing:
    # the patch is the same one.
    adopted = os.path.join(tmp, "adopted.db")
    shutil.copyfile(base, adopted)
    c = sqlite3.connect(adopted)
    c.execute("PRAGMA foreign_keys=OFF")
    ids = [r[0] for r in c.execute("SELECT file_id FROM files ORDER BY file_id")]
    rotated = dict(zip(ids, ids[1:] + ids[:1]))
    for table in ("passages", "files"):
        for old_id, new_id in rotated.items():
            c.execute(f"UPDATE {table} SET file_id = ?1 WHERE file_id = ?2", (new_id + 1000, old_id))
        c.execute(f"UPDATE {table} SET file_id = file_id - 1000")
    for table in ("passage_recordings", "passages"):
        c.execute(f"UPDATE {table} SET passage_id = passage_id + 500")
    c.commit()
    c.close()
    ap = sp.make_values(adopted, target, tables=tables, exclude=MACHINE, natural=True)
    check(canon(ap) == canon(patch), "a baseline numbered by the node gives the very same patch")
    check(sp.make_values(target, target, tables=tables, exclude=MACHINE, natural=True)["tables"] == [], "unchanged is still empty")

    # Refusal is all or nothing.
    n2 = catalogues(os.path.join(tmp, "refusal"))[2]
    before = dump(n2)
    bad = json.loads(canon(patch))
    next(e for e in bad["tables"] if e["name"] == "passages" and any(r["was"] for r in e["rows"]))["rows"][0]["was"][3] = 499
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
    pe = next(e for e in p2["tables"] if e["name"] == "passages" and "add_columns" in e)
    check(pe["add_columns"] == ["held INTEGER DEFAULT 0"] and "held" in pe["columns"], f"the new column is declared and carried: {pe['add_columns']}")
    n3 = catalogues(os.path.join(tmp, "wide-node"))[2]
    apply_natural(n3, p2)
    c = sqlite3.connect(n3)
    held = dict(c.execute("SELECT f.audio_md5, p.held FROM passages p JOIN files f USING (file_id)"))
    c.close()
    check(held == {"A": 0, "B": 0, "C2": 0, "D": 1} and dump(n3) == expected, f"applied, the column is there with the right values: {held}")

    # A catalogue with a passage that has no uid is refused, never patched by span [IMPL-UID-080].
    c = sqlite3.connect(wide)
    c.execute("UPDATE passages SET passage_uid = NULL WHERE passage_id = 13")
    c.commit()
    c.close()
    try:
        sp.make_values(base, wide, tables=tables, exclude=MACHINE, natural=True)
    except sp.NaturalUnsupported as err:
        check("without its uid" in str(err), f"said by name: {err}")
    else:
        check(False, "a catalogue with a passage lacking its uid must be refused")

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
    # hub's `scanned_at` as `also`, and the node's own is never compared. A patch of files and
    # their tags names no passage, so passages without uids do not refuse it.
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
