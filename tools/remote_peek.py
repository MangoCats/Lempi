#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read exactly one row from a remote installation, over ssh -- no database
copy `[SPEC-DF-116]`.

lempipi already carries the `sqlite3` CLI for its own maintenance
`[SPEC-DF-108]`; this runs one `SELECT`, over
`ssh <host> sqlite3 -json <path> "..."`, built from the identical anchors
`apply_changes.py` already resolves locally -- the same *shape* of "current
value" per kind (`current_recording`/`current_boundary`/`current_artist`,
mirrored here as `sql_for`), just fetched across the network instead of from
an open connection. A handful of bytes, not the ~1.16 GB `[SPEC-DF-114]`
measured a full copy at.

    python tools/remote_peek.py pi@lempipi:/srv/library/library.db \
        --kind boundary_review --audio-md5 <md5> --passage-kind radio \
        --start-ms 1000 --end-ms 200000

    python tools/remote_peek.py pi@lempipi:/srv/library/library.db \
        --kind id_review --audio-md5 <md5> --passage-kind radio \
        --start-ms 1000 --end-ms 200000

    python tools/remote_peek.py pi@lempipi:/srv/library/library.db \
        --kind artist_review --recording-mbid <mbid>

    python tools/remote_peek.py pi@lempipi:/srv/library/library.db \
        --kind passage_flag --audio-md5 <md5> --passage-kind radio \
        --start-ms 1000 --end-ms 200000

**`[SPEC-DF-124]` `passage_flag` does not work against a split peer, and
every installation is now split.** Its query joins `passages`, which lives in
the catalogue half, with `listener_flags`, which lives in the listener half,
and one path cannot serve both `[IMPL002 7.4]`. Measured against `pi@lempipi`
2026-09-11: pointed at the catalogue it fails on `listener_flags`, pointed at
the listener half it fails on `passages`. The other three kinds are pure
catalogue and are unaffected.

`remote_flags.py` already solved exactly this, with an `attached()` helper
that prepends `ATTACH DATABASE <listener> AS lis;` and a `--remote-listener`
option to name it. This tool needs the same and does not have it yet, so
`console.py`'s flag peek is the one caller currently affected.

Prints one JSON line: `{"ok": true, "current": {...} | null}` on a
successful round trip (a `null` current means the remote answered but has
nothing at that identity -- itself informative, not a failure), or
`{"ok": false, "error": "..."}` -- unreachable, timed out, or malformed --
with a non-zero exit. `[SPEC-DF-118]` depends on this failing cleanly and
fast, never hanging, so a caller can degrade to "editing against the local
baseline only" without blocking on it.
"""

import argparse
import json
import shlex
import subprocess
import sys

CONNECT_TIMEOUT = 5     # seconds given to ssh itself to establish a session
TOTAL_TIMEOUT = 10.0    # backstop on the whole round trip, ssh included


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def literal(value) -> str:
    """A safe SQL literal for a value going into a hand-built `SELECT` --
    there is no place to bind a parameter across an `ssh` round trip, so the
    query is fully rendered, with values escaped, before it ever leaves this
    machine.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):  # bool is an int subclass -- check first
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def sql_for(kind: str, anchor: dict) -> str:
    """The identical `SELECT`s `apply_changes.py`'s own `current_recording()`
    / `current_boundary()` / `current_artist()` run locally against an
    already-resolved `passage_id`, rewritten as one query against the anchor
    directly -- there is no local `passage_id` to hand a remote a second
    query to look up, and no round trip to spare for one.
    """
    if kind == "id_review":
        return (
            "SELECT pr.mbid FROM passages p "
            "JOIN files f ON f.file_id = p.file_id "
            "JOIN passage_recordings pr ON pr.passage_id = p.passage_id "
            f"WHERE f.audio_md5 = {literal(anchor['audio_md5'])} "
            f"AND p.kind = {literal(anchor['passage_kind'])} "
            f"AND p.start_ms = {literal(anchor['start_ms'])} "
            f"AND p.end_ms = {literal(anchor['end_ms'])} "
            "ORDER BY pr.weight DESC, pr.mbid LIMIT 1")
    if kind in ("boundary_review", "boundary_review_no_fade"):
        # `boundary_review_no_fade` `[SPEC-SUI-226]`: the fallback `peek()`
        # retries with, exactly once, against a remote whose `passages` has
        # never run `tools/add_fade_columns.py` and so lacks the four fade
        # columns entirely -- see `peek()`'s own comment for why a retry
        # beats either failing the whole peek or never asking for fade at all.
        fade_cols = (", p.fade_in_ms, p.fade_out_ms, p.fade_in_curve, p.fade_out_curve"
                     if kind == "boundary_review" else "")
        return (
            f"SELECT p.start_ms, p.end_ms, p.lead_in_ms, p.lead_out_ms, p.gain_db{fade_cols} "
            "FROM passages p JOIN files f ON f.file_id = p.file_id "
            f"WHERE f.audio_md5 = {literal(anchor['audio_md5'])} "
            f"AND p.kind = {literal(anchor['passage_kind'])} "
            f"AND p.start_ms = {literal(anchor['start_ms'])} "
            f"AND p.end_ms = {literal(anchor['end_ms'])}")
    if kind == "artist_review":
        return (
            "SELECT a.mbid AS artist_mbid, a.name AS artist_name "
            "FROM recording_artists ra JOIN artists a ON a.mbid = ra.artist_mbid "
            f"WHERE ra.mbid = {literal(anchor['recording_mbid'])} "
            "ORDER BY ra.weight DESC, a.name LIMIT 1")
    if kind == "passage_flag":
        # Whether the remote's OWN copy of this passage is flagged, under
        # either shape a flag can take `[SPEC-DF-107]` -- keyed by the
        # remote's own local passage_id (never portable on its own,
        # `[SPEC-DF-103]`, which is why it is resolved here rather than
        # trusted from the caller) or by any recording currently linked to
        # it. `remote_passage_id` rides along in the same round trip: the
        # one thing a caller wanting to *clear* that flag needs next, and
        # NULL, not a second query, is how "the remote has no such passage
        # at all" is told apart from "it has it, unflagged."
        #
        # `remote_mbids` rides along too -- found live, not anticipated: an
        # id correction accepted locally but never yet pushed left the
        # remote linked to the *old* recording, so a caller resolving
        # subjects only from its own current link cleared nothing where
        # the flag actually was, `_lempi_set_flag`-style success reported
        # regardless (a DELETE matching zero rows is not an error). The
        # same reasoning `[SPEC-DF-112]`'s `clear_flags_for()` already
        # applies for an `id_review`'s own target+baseline, generalized:
        # ask what the OTHER side currently thinks too, clear the union.
        return (
            "WITH target AS (SELECT p.passage_id FROM passages p "
            "JOIN files f ON f.file_id = p.file_id "
            f"WHERE f.audio_md5 = {literal(anchor['audio_md5'])} "
            f"AND p.kind = {literal(anchor['passage_kind'])} "
            f"AND p.start_ms = {literal(anchor['start_ms'])} "
            f"AND p.end_ms = {literal(anchor['end_ms'])}) "
            "SELECT (SELECT passage_id FROM target) AS remote_passage_id, "
            "EXISTS (SELECT 1 FROM listener_flags lf, target t "
            "WHERE (lf.subject_kind='passage' AND lf.subject_id = CAST(t.passage_id AS TEXT)) "
            "OR (lf.subject_kind='recording' AND lf.subject_id IN "
            "(SELECT mbid FROM passage_recordings WHERE passage_id = t.passage_id))) AS flagged, "
            "(SELECT json_group_array(mbid) FROM passage_recordings "
            "WHERE passage_id = (SELECT passage_id FROM target)) AS remote_mbids")
    raise ValueError(f"unknown kind {kind!r}")


def parse_rows(output: str) -> list:
    """`sqlite3 -json` prints a JSON array for a `SELECT`, or nothing at all
    for zero rows -- both are "no row", not a parse error.
    """
    text = output.strip()
    if not text:
        return []
    return json.loads(text)


# The same query, run by python3 instead, emitting exactly what
# `sqlite3 -json` would. This exists because `bose` carried no `sqlite3`
# CLI -- a minimal appliance image has no reason to -- and every Lempi
# appliance has python3 with the `sqlite3` module, because the tooling
# already assumes it. `bose` has been given `sqlite3` since `[BOS-IMG-020]`,
# so nothing reaches this path today; it is kept because the next appliance
# built from a minimal image will arrive without the command again.
#
# Kept to one expression passed with `-c` so nothing has to be installed,
# copied or left behind on the far side. `mode=ro` and deliberately NOT
# `immutable=1`, which is a different thing than it sounds: `immutable`
# tells SQLite the file cannot change and so it may skip the WAL entirely,
# which against an appliance whose catalogue carries un-checkpointed frames
# returns the stale base file with no error at all `[BOS-RUN-090]`.
# `mode=ro` reads the frames correctly. This file never writes a remote,
# which is the posture meant here -- not the `immutable` flag.
_PY_FALLBACK = (
    "import sqlite3,json,sys;"
    "c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True);"
    "c.row_factory=sqlite3.Row;"
    "r=[dict(x) for x in c.execute(sys.argv[2])];"
    "print(json.dumps(r) if r else '')"
)


def _python_cmd(path: str, sql: str) -> str:
    return (f"python3 -c {shlex.quote(_PY_FALLBACK)} "
            f"{shlex.quote(path)} {shlex.quote(sql)}")


def run_remote_sql(remote: str, sql: str, timeout: float = TOTAL_TIMEOUT) -> dict:
    """The one round trip everything in this file, and `remote_flags.py`,
    is built on: `ssh <host> sqlite3 -json <path> "<sql>"`, `sql` already
    fully rendered (no bind parameters cross this boundary).

    Every failure mode -- no route to the host, a closed port, `sqlite3`
    missing, malformed JSON -- collapses to the same `{"ok": False}` shape.
    `[SPEC-DF-118]` only needs "did this work", never which of the many ways
    it did not. `BatchMode=yes` is load-bearing: without it, a host that
    cannot authenticate blocks on a password prompt instead of failing fast.

    Returns `{"ok": True, "rows": [...]}"` (an empty list for zero matches,
    itself informative, not a failure) or `{"ok": False, "error": "..."}`.

    **A host with no `sqlite3` CLI is retried through python3**, which emits
    the identical JSON. `bose` was such a host -- a minimal image has no
    reason to carry the command-line shell for a library it never uses from
    the shell -- and without this every remote tool reported that appliance
    unreachable when it was merely differently equipped. `bose` has carried
    `sqlite3` since `[BOS-IMG-020]`, so no deployed host exercises this
    now `[BOS-RUN-075]`. Only that one
    failure is retried; every other stays what it was, so a genuinely
    unreachable host still fails fast.
    """
    host, sep, path = remote.partition(":")
    if not sep or not path:
        return {"ok": False, "error": f"remote must be user@host:/path, got {remote!r}"}
    remote_cmd = f"sqlite3 -json {shlex.quote(path)} {shlex.quote(sql)}"
    argv = ["ssh", "-o", f"ConnectTimeout={CONNECT_TIMEOUT}", "-o", "BatchMode=yes",
            host, remote_cmd]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"no answer from {host} within {timeout}s"}
    except OSError as e:
        return {"ok": False, "error": f"could not run ssh: {e}"}
    if r.returncode != 0:
        err = (r.stderr or r.stdout or f"ssh exited {r.returncode}").strip()
        if "sqlite3: command not found" in err or "sqlite3: not found" in err:
            argv_py = ["ssh", "-o", f"ConnectTimeout={CONNECT_TIMEOUT}", "-o", "BatchMode=yes",
                       host, _python_cmd(path, sql)]
            try:
                r = subprocess.run(argv_py, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": f"no answer from {host} within {timeout}s"}
            except OSError as e:
                return {"ok": False, "error": f"could not run ssh: {e}"}
            if r.returncode != 0:
                return {"ok": False,
                        "error": (r.stderr or r.stdout or f"ssh exited {r.returncode}").strip()[:300]}
        else:
            return {"ok": False, "error": err[:300]}
    try:
        rows = parse_rows(r.stdout)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"unparseable reply from {host}: {e}"}
    return {"ok": True, "rows": rows}


def peek(remote: str, kind: str, anchor: dict, timeout: float = TOTAL_TIMEOUT) -> dict:
    """One anchor in, the row at it (or `None`) out -- `run_remote_sql()`
    plus the one `kind`-specific `SELECT` `sql_for()` builds.

    `boundary_review` retries once, without the four fade columns
    `[SPEC-SUI-226]`, on exactly the failure a remote that has never run
    `tools/add_fade_columns.py` would produce -- reporting that as
    `{"ok": False}` would misread an older schema as "unreachable," the
    same distinction this file's own docstring already draws for a `null`
    current vs. a real failure. The common case (an already-migrated
    remote) still costs the one round trip `[SPEC-DF-116]` promises;
    only this one case costs two.
    """
    result = run_remote_sql(remote, sql_for(kind, anchor), timeout=timeout)
    if (not result["ok"] and kind == "boundary_review"
            and "fade_in_ms" in (result.get("error") or "")):
        result = run_remote_sql(remote, sql_for("boundary_review_no_fade", anchor), timeout=timeout)
    if not result["ok"]:
        return result
    rows = result["rows"]
    return {"ok": True, "current": rows[0] if rows else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("remote",
                help="user@host:/path/to/library.db -- the peer's CATALOGUE half")
    ap.add_argument("--kind", required=True,
                     choices=["id_review", "boundary_review", "artist_review", "passage_flag"])
    ap.add_argument("--audio-md5")
    ap.add_argument("--passage-kind")
    ap.add_argument("--start-ms", type=int)
    ap.add_argument("--end-ms", type=int)
    ap.add_argument("--recording-mbid")
    ap.add_argument("--timeout", type=float, default=TOTAL_TIMEOUT)
    args = ap.parse_args()

    if args.kind == "artist_review":
        if not args.recording_mbid:
            ap.error("--kind artist_review needs --recording-mbid")
        anchor = {"recording_mbid": args.recording_mbid}
    else:
        needed = [("--audio-md5", args.audio_md5), ("--passage-kind", args.passage_kind),
                  ("--start-ms", args.start_ms), ("--end-ms", args.end_ms)]
        missing = [name for name, value in needed if value is None]
        if missing:
            ap.error(f"--kind {args.kind} needs {', '.join(missing)}")
        anchor = {"audio_md5": args.audio_md5, "passage_kind": args.passage_kind,
                  "start_ms": args.start_ms, "end_ms": args.end_ms}

    result = peek(args.remote, args.kind, anchor, timeout=args.timeout)
    say(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
