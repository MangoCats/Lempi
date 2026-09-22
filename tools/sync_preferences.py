#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sync `listener_preferences` between two installations `[SPEC030]`.

`SPEC029` gave a listener a way to edit an artist's or a recording's own
`rotation`/`recovery`/`restraint` locally. This is what happens once two
installations -- a desktop and `lempi02w`, say -- have each done that
independently: last-write-wins by `updated_at`, not the three-way baseline
merge `apply_changes.py` uses for review decisions. There is no baseline
to merge against here -- `listener_preferences` carries only ever "the
current tuning," so the side with the newer `updated_at` for a given
`(subject_kind, subject_id)` simply wins, and its row is copied to the
other side.

**A subject syncs only if it is a real artist/recording on *both* sides.**
Tuned on only one side but the *other* side has never heard of that
artist/recording at all -- skipped, not pushed onto a library it does not
belong to.

**No database copy.** One `ssh ... sqlite3 -json ...` round trip
(`remote_peek.run_remote_sql`) reads the whole remote table -- the same
economy `remote_flags.py` already established for `listener_flags`, a
table of the same small scale (a row exists only once a subject has
actually been tuned; MuLibPlay's own migrated data was 36% of tracks,
`[GDE-BMK-020]`, so this was never going to be a large table). What
"transfer only the changed preferences" actually costs is on the *write*
side: only the rows that differ are ever patched, in either direction.

    python tools/sync_preferences.py <local_db> user@host:/path/to/listener.db
    python tools/sync_preferences.py <local_db> user@host:/path/to/listener.db --commit
    python tools/sync_preferences.py <local_db> user@host:/path/to/listener.db --commit --json

A **split** remote needs `--remote-listener /path/to/listener.db` as well.
The positional `remote` is the peer's **catalogue**, which is what
`[IMPL002 §7.4]` already established `sync_remote`/`sync_peers.remote` to
mean for every tool that reaches a peer -- `mesh_diff.py` wants only that
one. The listener half is the nullable second path, read as "the same file"
when omitted, which is true of every peer that has not split. Without it on
a peer that has, the existence check in `[SPEC-PREF-110]` asks the listener
database for `artists`/`recordings`, gets nothing, and every one-sided
subject is filed "missing on the other side" -- a clean "nothing to do"
while nothing syncs `[SPEC-PREF-155]`.

Rehearse by default: without `--commit`, nothing is written on either
side -- only counts are reported. The remote write, when there is one,
follows the identical `[PI5-LIB-010]` recipe `jobs.py::_remote_push` and
`push_file_tags.py` already use: stop the service, apply the patch,
restart it -- issued only when there is actually something to write.
The local write is advisory-only about the player, the same posture
`apply_changes.py` already takes for a local db; a best-effort
`POST /library/reload` afterward lets an already-running local Lempi pick
the change up without needing a restart.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time

# The player's port comes from one place `[GDE-CLI-100]`, and honours
# LEMPI_PORT there rather than being read again here.
import lempi_control
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import remote_peek as rp  # noqa: E402  -- run_remote_sql(), literal(): reused, not reinvented

MANIFEST_SQL = (
    "SELECT subject_kind, subject_id, rotation, recovery, restraint, updated_at "
    "FROM listener_preferences"
)

PATCH_TMP = "/tmp/lempi-preference-sync-patch.sql"
REMOTE_TABLE = "lempi_prefs_remote"


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


Row = tuple  # (rotation, recovery, restraint, updated_at)
Key = tuple  # (subject_kind, subject_id)


# ------------------------------------------------------------------- reading

def read_local_manifest(conn) -> dict[Key, Row]:
    """`listener_preferences`, local -- one plain `SELECT`, no I/O beyond it."""
    return {
        (r[0], r[1]): (r[2], r[3], r[4], r[5])
        for r in conn.execute(MANIFEST_SQL)
    }


def fetch_remote_manifest(remote: str) -> dict[Key, Row] | None:
    """The identical shape, over one `ssh ... sqlite3 -json` round trip.
    `None` -- not an exception -- on any transport failure, so a caller can
    report "could not reach `<remote>`" cleanly rather than crash midway.
    """
    result = rp.run_remote_sql(remote, MANIFEST_SQL)
    if not result["ok"]:
        return None
    return {
        (r["subject_kind"], r["subject_id"]): (r["rotation"], r["recovery"], r["restraint"], r["updated_at"])
        for r in result["rows"]
    }


# ------------------------------------------------------------------- deciding

def decide(local: dict[Key, Row], remote: dict[Key, Row],
           local_exists, remote_exists,
           when=lambda row: row[3], subject_of=lambda key: key) -> dict[str, list]:
    """The whole decision, pure -- no I/O. `local_exists`/`remote_exists`
    are `(kind, id) -> bool` callables so this is testable against
    synthetic manifests with no database and no network at all.

    Buckets every subject in either manifest:
      * `pull`  -- remote's row is newer (or local has never tuned it, and
                   local's own library does carry that artist/recording);
                   apply locally.
      * `push`  -- the mirror case, applied to the remote.
      * `skip_missing` -- one-sided, and the *other* side's library simply
                   does not have that artist/recording at all.
      * `tie`   -- equal `updated_at`, differing values; reported, not
                   guessed at -- there is no clock left to decide with.
      * (absent from every list) -- already equal, or both sides silent.

    `when` and `subject_of` are what let `listener_characteristics` reuse
    this whole function rather than carry a near-copy of it
    `[SPEC-PREF-140]`. Its key holds two more fields (the characteristic and
    its class) and its row keeps the timestamp somewhere else, but the
    decision is the same one -- newer wins; a one-sided row needs the other
    side to actually know the subject; equal timestamps are a tie nobody
    guesses at -- and it stays the same by being the same code. The defaults
    are `listener_preferences`'s own shape, so every existing caller and
    test is untouched.
    """
    out: dict[str, list] = {"pull": [], "push": [], "skip_missing": [], "tie": []}
    for key in sorted(set(local) | set(remote)):
        kind, subject_id = subject_of(key)
        loc = local.get(key)
        rem = remote.get(key)
        if loc is not None and rem is not None:
            if loc == rem:
                continue
            if when(loc) == when(rem):
                out["tie"].append(key)
            elif when(loc) > when(rem):
                out["push"].append(key)
            else:
                out["pull"].append(key)
            continue
        if loc is not None:
            # Tuned locally only. Pushing it needs the remote to actually
            # carry this artist/recording -- checked by the caller and
            # passed in via `remote_exists`, never inferred.
            (out["push"] if remote_exists(kind, subject_id) else out["skip_missing"]).append(key)
        else:
            (out["pull"] if local_exists(kind, subject_id) else out["skip_missing"]).append(key)
    return out


# -------------------------------------------------------------- existence

def local_exists_fn(conn):
    table = {"artist": "artists", "recording": "recordings"}

    def check(kind: str, subject_id: str) -> bool:
        row = conn.execute(
            f"SELECT 1 FROM {table[kind]} WHERE mbid = ?1", (subject_id,)).fetchone()
        return row is not None
    return check


def remote_exists_batch(remote: str, kind: str, ids: list[str]) -> set[str]:
    """Which of `ids` are a real artist/recording on the remote -- one
    `IN (...)` round trip per kind actually needed, never the whole
    catalogue. Empty `ids` costs nothing (no round trip at all).

    `remote` here is the remote's **catalogue**, which on a split
    installation is not the file the rest of this tool talks to
    `[SPEC-PREF-155]`. `run_remote_sql` opens exactly one path, so asking
    `listener.db` for `recordings` fails, `{"ok": False}` collapses to an
    empty set, and every one-sided subject is then filed `skip_missing` --
    a sync that reports "nothing to do" while doing nothing. Found live
    against `pi@lempi02w` on 2026-09-10, after that installation was split.
    """
    if not ids:
        return set()
    table = {"artist": "artists", "recording": "recordings"}[kind]
    in_list = ", ".join(rp.literal(i) for i in ids)
    result = rp.run_remote_sql(remote, f"SELECT mbid FROM {table} WHERE mbid IN ({in_list})")
    if not result["ok"]:
        return set()
    return {r["mbid"] for r in result["rows"]}


# ------------------------------------------------------------------- applying

def apply_local(conn, keys: list[Key], source: dict[Key, Row]) -> None:
    for kind, subject_id in keys:
        rotation, recovery, restraint, updated_at = source[(kind, subject_id)]
        conn.execute(
            "INSERT INTO listener_preferences (subject_kind, subject_id, rotation, "
            "recovery, restraint, updated_at) VALUES (?1,?2,?3,?4,?5,?6) "
            "ON CONFLICT(subject_kind, subject_id) DO UPDATE SET "
            "rotation=excluded.rotation, recovery=excluded.recovery, "
            "restraint=excluded.restraint, updated_at=excluded.updated_at",
            (kind, subject_id, rotation, recovery, restraint, updated_at))


def patch_sql_for(keys: list[Key], source: dict[Key, Row]) -> str:
    """The preference half of the remote patch -- one `INSERT OR REPLACE`
    per row, `remote_peek.literal`-quoted, no bind parameters (none cross an
    `ssh` boundary, the same reason `remote_peek.py`'s own queries are built
    this way). A pure function so the exact SQL text is checkable without an
    ssh call.

    Statements, not a finished script: `combined_patch` wraps these together
    with the specials' own so one push covers every table that moved.
    """
    lines = []
    for kind, subject_id in keys:
        rotation, recovery, restraint, updated_at = source[(kind, subject_id)]
        lines.append(
            "INSERT OR REPLACE INTO listener_preferences "
            "(subject_kind, subject_id, rotation, recovery, restraint, updated_at) VALUES "
            f"({rp.literal(kind)}, {rp.literal(subject_id)}, {rp.literal(rotation)}, "
            f"{rp.literal(recovery)}, {rp.literal(restraint)}, {rp.literal(updated_at)});")
    return lines


# --------------------------------------------------------------- specials

# `listener_characteristics` -- a listener's own "special" tagging
# `[SPEC-PREF-085]`, `[SPEC-PREF-140]`. The same shape of table as
# `listener_preferences`: one row per thing a person decided, its own
# `updated_at`, no merge history, so §2's last-write-wins carries over
# unchanged rather than needing an argument of its own.
SPECIALS_SQL = (
    "SELECT subject_kind, subject_id, characteristic, class, value, updated_at "
    "FROM listener_characteristics"
)

# The registry those values are meaningful against `[SPEC-PREF-145]`. Read
# with `label` where the far side has that column, and without where it does
# not -- an installation whose player has not started since the column was
# added still syncs, it just carries no names across.
DEFINITIONS_SQL = "SELECT characteristic, class, interp, label FROM listener_occasions"
DEFINITIONS_SQL_NO_LABEL = "SELECT characteristic, class, interp FROM listener_occasions"
POINTS_SQL = (
    "SELECT characteristic, class, month, day, multiplier FROM listener_occasion_points"
)

# Created if absent before anything is inserted into it: a remote carrying
# this build but whose player has not started since the upgrade has no such
# table yet, and one `CREATE TABLE IF NOT EXISTS` is cheaper than a push that
# fails on a first sync. Matches `player_store.rs`'s own
# `CHARACTERISTICS_TABLE` exactly.
SPECIALS_DDL = (
    "CREATE TABLE IF NOT EXISTS listener_characteristics ("
    "subject_kind TEXT NOT NULL CHECK (subject_kind IN ('recording')), "
    "subject_id TEXT NOT NULL, characteristic TEXT NOT NULL, class TEXT NOT NULL, "
    "value REAL NOT NULL CHECK (value >= 0.0 AND value <= 1.0), "
    "updated_at TEXT NOT NULL, "
    "PRIMARY KEY (subject_kind, subject_id, characteristic, class)) WITHOUT ROWID;"
)


def read_local_specials(conn) -> dict[Key, Row]:
    """`listener_characteristics`, local. A database predating the table --
    an installation not yet running this build -- reads as empty rather than
    raising: nothing has been tagged there, which is the honest answer and
    the same one an empty table gives."""
    try:
        rows = list(conn.execute(SPECIALS_SQL))
    except sqlite3.OperationalError:
        return {}
    return {(r[0], r[1], r[2], r[3]): (r[4], r[5]) for r in rows}


def absent_table(result: dict) -> bool:
    """Whether a failed `run_remote_sql` failed *because the table is not
    there* `[SPEC-PREF-150]`.

    `run_remote_sql` deliberately collapses every failure to `{"ok": False}`
    -- `[SPEC-DF-118]` only ever needed "did this work". That is exactly
    wrong here: "this installation does not run that build yet" must read as
    an empty table, while "the database was locked" must not, or a transient
    lock becomes "the remote has no specials" and this tool pushes stale
    local values over newer remote ones. Observed live on 2026-09-10 --
    a count against `pi@lempi02w` failed once, seconds after the service
    restarted, and succeeded four times running immediately after.
    """
    return "no such table" in (result.get("error") or "").lower()


def fetch_remote_specials(remote: str) -> dict[Key, Row] | None:
    result = rp.run_remote_sql(remote, SPECIALS_SQL)
    if not result["ok"]:
        # Absent is empty; anything else is unknown, and unknown must stop
        # the sync rather than be guessed at as empty.
        return {} if absent_table(result) else None
    return {
        (r["subject_kind"], r["subject_id"], r["characteristic"], r["class"]):
            (r["value"], r["updated_at"])
        for r in result["rows"]
    }


def read_local_definitions(conn) -> tuple[dict, bool]:
    """Every registered occasion with its own curve, keyed
    `(characteristic, class)`. Returns `(definitions, has_label)` so a caller
    building a patch knows whether the far side can be sent one."""
    has_label = True
    try:
        rows = list(conn.execute(DEFINITIONS_SQL))
    except sqlite3.OperationalError:
        has_label = False
        try:
            rows = [(r[0], r[1], r[2], None) for r in conn.execute(DEFINITIONS_SQL_NO_LABEL)]
        except sqlite3.OperationalError:
            return {}, False
    points: dict[tuple, list] = {}
    try:
        for ch, cl, month, day, mult in conn.execute(POINTS_SQL):
            points.setdefault((ch, cl), []).append((month, day, mult))
    except sqlite3.OperationalError:
        pass
    return ({(r[0], r[1]): (r[2], r[3], tuple(sorted(points.get((r[0], r[1]), []))))
             for r in rows}, has_label)


def fetch_remote_definitions(remote: str) -> tuple[dict | None, bool]:
    """`(definitions, has_label)`, or `(None, False)` where the far side
    could not be read for any reason other than the table being absent
    `[SPEC-PREF-150]`.

    A failed *label* read is the one failure that is not fatal: it is
    retried without the column, since a remote whose `listener_occasions`
    predates `label` is expected and the name is the least important thing
    being carried. Everything else -- including a failed read of the control
    points, which would silently turn every curve into a bare registry row
    and report the difference as a conflict -- stops the sync.
    """
    has_label = True
    result = rp.run_remote_sql(remote, DEFINITIONS_SQL)
    if not result["ok"]:
        has_label = False
        result = rp.run_remote_sql(remote, DEFINITIONS_SQL_NO_LABEL)
        if not result["ok"]:
            return ({} if absent_table(result) else None), False
    rows = [(r["characteristic"], r["class"], r["interp"], r.get("label")) for r in result["rows"]]
    points: dict[tuple, list] = {}
    pts = rp.run_remote_sql(remote, POINTS_SQL)
    if not pts["ok"] and not absent_table(pts):
        return None, has_label
    if pts["ok"]:
        for r in pts["rows"]:
            points.setdefault((r["characteristic"], r["class"]), []).append(
                (r["month"], r["day"], r["multiplier"]))
    return ({(c, k): (i, lb, tuple(sorted(points.get((c, k), []))))
             for c, k, i, lb in rows}, has_label)


def decide_definitions(local: dict, remote: dict) -> dict[str, list]:
    """Registry sync is **additive**, and deliberately not last-write-wins
    `[SPEC-PREF-145]`.

    `listener_occasions` carries no `updated_at`, so there is no clock to
    decide with -- but more to the point, a curve is a setting a person
    tuned (`--kids 0.5`), not a fact about one recording. A definition the
    other side has never heard of is carried over, which is what makes a
    special defined here usable there at all. A definition both sides have
    and disagree about is **reported and left alone**, on both sides: the
    alternative is a sync that silently retunes somebody's seasons.
    """
    out: dict[str, list] = {"pull": [], "push": [], "conflict": []}
    for key in sorted(set(local) | set(remote)):
        loc, rem = local.get(key), remote.get(key)
        if loc is not None and rem is not None:
            if loc != rem:
                out["conflict"].append(key)
        elif loc is not None:
            out["push"].append(key)
        else:
            out["pull"].append(key)
    return out


def apply_local_specials(conn, keys: list[Key], source: dict[Key, Row]) -> None:
    conn.execute(SPECIALS_DDL)
    for key in keys:
        subject_kind, subject_id, characteristic, class_ = key
        value, updated_at = source[key]
        conn.execute(
            "INSERT INTO listener_characteristics (subject_kind, subject_id, "
            "characteristic, class, value, updated_at) VALUES (?1,?2,?3,?4,?5,?6) "
            "ON CONFLICT(subject_kind, subject_id, characteristic, class) DO UPDATE SET "
            "value=excluded.value, updated_at=excluded.updated_at",
            (subject_kind, subject_id, characteristic, class_, value, updated_at))


def apply_local_definitions(conn, keys: list, source: dict, has_label: bool) -> None:
    for key in keys:
        characteristic, class_ = key
        interp, label, points = source[key]
        if has_label:
            conn.execute("INSERT OR REPLACE INTO listener_occasions "
                         "(characteristic, class, interp, label) VALUES (?1,?2,?3,?4)",
                         (characteristic, class_, interp, label))
        else:
            conn.execute("INSERT OR REPLACE INTO listener_occasions "
                         "(characteristic, class, interp) VALUES (?1,?2,?3)",
                         (characteristic, class_, interp))
        conn.execute("DELETE FROM listener_occasion_points WHERE characteristic=?1 AND class=?2",
                     (characteristic, class_))
        for month, day, multiplier in points:
            conn.execute("INSERT INTO listener_occasion_points "
                         "(characteristic, class, month, day, multiplier) VALUES (?1,?2,?3,?4,?5)",
                         (characteristic, class_, month, day, multiplier))


def specials_patch_sql_for(keys: list[Key], source: dict[Key, Row]) -> list[str]:
    """The statements, not a whole script -- `combined_patch` below wraps
    every table's lines in one transaction, so the remote takes one stop and
    one restart however many tables actually moved."""
    lines = [SPECIALS_DDL]
    for key in keys:
        subject_kind, subject_id, characteristic, class_ = key
        value, updated_at = source[key]
        lines.append(
            "INSERT OR REPLACE INTO listener_characteristics "
            "(subject_kind, subject_id, characteristic, class, value, updated_at) VALUES "
            f"({rp.literal(subject_kind)}, {rp.literal(subject_id)}, "
            f"{rp.literal(characteristic)}, {rp.literal(class_)}, "
            f"{rp.literal(value)}, {rp.literal(updated_at)});")
    return lines


def definitions_patch_sql_for(keys: list, source: dict, remote_has_label: bool) -> list[str]:
    """`remote_has_label` decides the column list: naming `label` against a
    remote whose `listener_occasions` predates the column is a hard error on
    a statement that would otherwise have worked, and the name is the least
    important thing being carried."""
    lines = []
    for key in keys:
        characteristic, class_ = key
        interp, label, points = source[key]
        if remote_has_label:
            lines.append(
                "INSERT OR REPLACE INTO listener_occasions "
                "(characteristic, class, interp, label) VALUES "
                f"({rp.literal(characteristic)}, {rp.literal(class_)}, "
                f"{rp.literal(interp)}, {rp.literal(label)});")
        else:
            lines.append(
                "INSERT OR REPLACE INTO listener_occasions "
                "(characteristic, class, interp) VALUES "
                f"({rp.literal(characteristic)}, {rp.literal(class_)}, {rp.literal(interp)});")
        lines.append(
            "DELETE FROM listener_occasion_points WHERE "
            f"characteristic = {rp.literal(characteristic)} AND class = {rp.literal(class_)};")
        for month, day, multiplier in points:
            lines.append(
                "INSERT INTO listener_occasion_points "
                "(characteristic, class, month, day, multiplier) VALUES "
                f"({rp.literal(characteristic)}, {rp.literal(class_)}, {rp.literal(month)}, "
                f"{rp.literal(day)}, {rp.literal(multiplier)});")
    return lines


def combined_patch(*statement_lists: list[str]) -> str:
    """One transaction over every table that actually moved, or `""` when
    none did. Returning empty rather than an empty transaction is what lets
    the caller skip the stop/restart entirely -- a service interruption for
    a push with nothing in it is the thing worth avoiding here."""
    lines = [line for group in statement_lists for line in group]
    if not lines:
        return ""
    return "BEGIN IMMEDIATE;\n" + "\n".join(lines) + "\nCOMMIT;\n"


def apply_remote(remote: str, sql_text: str) -> bool:
    """Ships and applies an already-rendered patch, stopping/restarting the
    service the same `[PI5-LIB-010]` recipe `_remote_push`/`push_file_tags.py`
    already use -- only called when `sql_text` is non-empty; the caller never
    invokes this for an empty push.

    Takes the SQL rather than building it, so that a push moving preferences
    **and** specials **and** a newly-defined occasion is still one stop and
    one restart `[SPEC-PREF-140]`. Three separate pushes would be three gaps
    in the music, to sync three tables nobody edited separately in the first
    place.
    """
    host, sep, path = remote.partition(":")
    if not sep or not path:
        raise ValueError(f"remote must be user@host:/path, got {remote!r}")
    local_tmp = PATCH_TMP.rsplit("/", 1)[-1]
    with open(local_tmp, "w", encoding="utf-8") as f:
        f.write(sql_text)
    try:
        r = subprocess.run(["scp", "-q", local_tmp, f"{host}:{PATCH_TMP}"], timeout=30)
        if r.returncode != 0:
            return False
        r = subprocess.run(
            ["ssh", host,
             f"sudo systemctl stop lempi && sqlite3 {path} < {PATCH_TMP} "
             f"&& sudo systemctl start lempi"],
            timeout=60)
        return r.returncode == 0
    finally:
        try:
            os.remove(local_tmp)
        except OSError:
            pass


def reload_local(port: int) -> None:
    """Best-effort: an already-running local Lempi picks the change up
    without a restart. Silently skipped if nothing answers -- the same
    "no Lempi running locally" case every other local-write tool already
    tolerates without treating it as a failure.
    """
    try:
        req = urllib.request.Request(f"http://localhost:{port}/library/reload", method="POST")
        urllib.request.urlopen(req, timeout=3)
    except (urllib.error.URLError, OSError, TimeoutError):
        pass


# ---------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("remote", help="user@host:/path/to/listener.db (the peer's catalogue)")
    # The peer's listener half, where it has one `[IMPL002 §7.4]`,
    # `[SPEC-PREF-155]`. Path only: the host is the `remote` argument's own.
    ap.add_argument("--remote-listener",
                    help="path to the remote's listener.db, if it is split")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--port", type=int, default=lempi_control.LEMPI_PORT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    # Listener half as `main`: everything this writes is listener-side.
    # The catalogue rides along read-only for `[SPEC-PREF-110]`'s existence
    # check, which is the one thing here that asks about `recordings`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LISTENER,
                            writable=args.commit,
                            **({"timeout": 60} if args.commit else {}))
    if args.commit:
        conn.execute("PRAGMA busy_timeout = 60000")

    # `remote` is the catalogue; the listener half is the same file unless
    # this peer has split `[IMPL002 §7.4]`. Everything this tool reads and
    # writes is listener-side except the existence check, which is the one
    # thing that genuinely wants the catalogue.
    catalogue = args.remote
    listener = args.remote
    if args.remote_listener:
        listener = f"{args.remote.partition(':')[0]}:{args.remote_listener}"

    local = read_local_manifest(conn)
    local_specials = read_local_specials(conn)
    local_defs, local_has_label = read_local_definitions(conn)
    remote = fetch_remote_manifest(listener)
    if remote is None:
        result = {"ok": False, "error": f"could not reach {listener}"}
        if args.json:
            print(json.dumps(result))
        else:
            say(f"error: {result['error']}")
        return 1

    remote_specials = fetch_remote_specials(listener)
    remote_defs, remote_has_label = fetch_remote_definitions(listener)
    # Unknown is not empty `[SPEC-PREF-150]`: syncing against a remote whose
    # specials could not be read would push local values over whatever is
    # actually there.
    if remote_specials is None or remote_defs is None:
        which = "specials" if remote_specials is None else "occasion definitions"
        result = {"ok": False, "error": f"could not read the remote's {which}; nothing written"}
        if args.json:
            print(json.dumps(result))
        else:
            say(f"error: {result['error']}")
        return 1

    # One-sided subjects need an existence check on the *other* side --
    # collected first so the batches below cost exactly one round trip
    # per kind actually needed, never a query per subject. The specials'
    # own one-sided subjects join the same batch rather than starting a
    # second one: they are recordings too, and a recording tuned on one
    # side and tagged on the other would otherwise be asked about twice.
    local_only = [k for k in local if k not in remote]
    remote_ids_by_kind: dict[str, list[str]] = {"artist": [], "recording": []}
    for kind, subject_id in local_only:
        remote_ids_by_kind[kind].append(subject_id)
    for key in local_specials:
        if key not in remote_specials:
            remote_ids_by_kind["recording"].append(key[1])
    remote_ids_by_kind = {k: sorted(set(v)) for k, v in remote_ids_by_kind.items()}
    remote_known = {
        kind: remote_exists_batch(catalogue, kind, ids)
        for kind, ids in remote_ids_by_kind.items() if ids
    }
    remote_exists = lambda kind, sid: sid in remote_known.get(kind, ())  # noqa: E731
    local_check = local_exists_fn(conn)

    plan = decide(local, remote, local_check, remote_exists)
    # The same decision, over a key carrying two more fields and a row
    # keeping its timestamp elsewhere `[SPEC-PREF-140]`.
    splan = decide(local_specials, remote_specials, local_check, remote_exists,
                   when=lambda row: row[1],
                   subject_of=lambda key: (key[0], key[1]))
    dplan = decide_definitions(local_defs, remote_defs)

    # Fixed field names regardless of `--commit` -- `committed` says whether
    # `pull`/`push` already happened or are only a preview, so a caller
    # (the console's own job result) never has to branch on which keys a
    # response happens to carry.
    result = {
        "ok": True,
        "committed": args.commit,
        "pull": len(plan["pull"]),
        "push": len(plan["push"]),
        "skipped_missing": len(plan["skip_missing"]) + len(splan["skip_missing"]),
        "ties": len(plan["tie"]) + len(splan["tie"]),
        # Reported separately from the tuning counts, not folded into them:
        # "3 preferences and 40 tags" and "43 things" are different answers
        # to "what did this just do to my library" `[SPEC-PREF-140]`.
        "specials_pull": len(splan["pull"]),
        "specials_push": len(splan["push"]),
        "definitions_pull": len(dplan["pull"]),
        "definitions_push": len(dplan["push"]),
        "definition_conflicts": len(dplan["conflict"]),
    }

    if args.commit:
        local_writes = plan["pull"] or splan["pull"] or dplan["pull"]
        if local_writes:
            conn.execute("BEGIN IMMEDIATE")
            apply_local(conn, plan["pull"], remote)
            apply_local_specials(conn, splan["pull"], remote_specials)
            # `local_has_label` and not the remote's: this writes the local
            # database, so what matters is whether *it* can hold a label.
            apply_local_definitions(conn, dplan["pull"], remote_defs, local_has_label)
            conn.commit()
            reload_local(args.port)
        # One patch, one stop, one restart, however many of the three tables
        # actually moved.
        patch = combined_patch(
            patch_sql_for(plan["push"], local),
            specials_patch_sql_for(splan["push"], local_specials),
            definitions_patch_sql_for(dplan["push"], local_defs, remote_has_label),
        )
        if patch:
            # The patch is all listener tables, so it lands on the
            # listener half -- and `apply_remote` restarts the service
            # around it either way.
            ok = apply_remote(listener, patch)
            if not ok:
                result["ok"] = False
                result["error"] = "remote push failed -- see stderr above"

    conn.close()

    if args.json:
        print(json.dumps(result))
    else:
        would = "would " if not args.commit else ""
        say(f"preferences: {would}pull {len(plan['pull'])}, {would}push {len(plan['push'])}")
        say(f"specials:    {would}pull {len(splan['pull'])}, {would}push {len(splan['push'])}")
        say(f"definitions: {would}pull {len(dplan['pull'])}, {would}push {len(dplan['push'])}, "
            f"{len(dplan['conflict'])} differing on both sides (left alone)")
        say(f"{result['skipped_missing']} skipped (subject missing on the other side), "
            f"{result['ties']} tied (equal timestamp, differing values)")
        for characteristic, class_ in dplan["conflict"]:
            say(f"  ! {characteristic}/{class_} is defined differently on each side; "
                f"neither was changed")
        moved = any(plan[k] or splan[k] for k in ("pull", "push")) or             dplan["pull"] or dplan["push"]
        if not args.commit and moved:
            say("dry run -- nothing written. Re-run with --commit to apply.")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
