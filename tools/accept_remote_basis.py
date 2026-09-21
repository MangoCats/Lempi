#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Accept a remote's current value as this library's new local value for one
row `[SPEC-DF-116..117]`.

Not a review decision -- `id_reviews`/`boundary_reviews` stay untouched, on
purpose. This is a working-copy refresh made *before* editing begins, the
same kind of act as `git fetch && git checkout origin/main -- one/file`, not
a correction a person is recorded as having made. When Lempi's own editor
later captures its own baseline for an actual edit, it reads what this wrote
-- which is exactly what makes `[SPEC-DF-117]`'s claim true: a push made
after accepting a remote basis and then editing classifies as fast-forward,
because the local baseline the edit recorded already matches what the
remote had at the moment editing began.

Small and boring on purpose -- it exists only so `console.py` itself never
opens the library for writing `[IMPL-SUI-055]`. The anchor identifies the
row exactly the way `apply_changes.py` already does (`resolve_passage`,
reused here rather than reimplemented); `--value` is the JSON
`remote_peek.py` already printed as `current`, passed through unchanged.

    python tools/accept_remote_basis.py data/library.db \
        --kind boundary_review --audio-md5 <md5> --passage-kind radio \
        --start-ms 1000 --end-ms 200000 \
        --value '{"start_ms":2000,"end_ms":190000,"lead_in_ms":250,"lead_out_ms":1200,"gain_db":-2.0}' \
        --commit

    python tools/accept_remote_basis.py data/library.db \
        --kind id_review --audio-md5 <md5> --passage-kind radio \
        --start-ms 1000 --end-ms 200000 --value '{"mbid":"..."}' --commit

Rehearse by default, like every other tool here.
"""

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import apply_changes as ac  # noqa: E402  -- resolve_passage, reused not reinvented


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def accept_boundary(conn: sqlite3.Connection, passage_id: int, value: dict) -> None:
    # `fade_*` `[SPEC-SUI-226]` only if `--value` actually carries it -- it
    # is absent from a `remote_peek.py` reply that fell back to
    # `boundary_review_no_fade` against a remote never migrated for it, and
    # from any `--value` copied out of an older `remote_peek.py` run. Not
    # writing it then is the same "do not assert what was not there" rule
    # `apply_boundary_review`'s own fade guard already follows.
    if "fade_in_ms" in value:
        conn.execute(
            "UPDATE passages SET start_ms=?1, end_ms=?2, lead_in_ms=?3, lead_out_ms=?4, "
            "gain_db=?5, fade_in_ms=?6, fade_out_ms=?7, fade_in_curve=?8, fade_out_curve=?9 "
            "WHERE passage_id=?10",
            (value["start_ms"], value["end_ms"], value["lead_in_ms"], value["lead_out_ms"],
             value["gain_db"], value["fade_in_ms"], value["fade_out_ms"],
             value["fade_in_curve"], value["fade_out_curve"], passage_id))
    else:
        conn.execute(
            "UPDATE passages SET start_ms=?1, end_ms=?2, lead_in_ms=?3, lead_out_ms=?4, "
            "gain_db=?5 WHERE passage_id=?6",
            (value["start_ms"], value["end_ms"], value["lead_in_ms"], value["lead_out_ms"],
             value["gain_db"], passage_id))


def accept_id(conn: sqlite3.Connection, passage_id: int, value: dict) -> None:
    mbid = value.get("mbid")
    if not mbid:
        raise ValueError("the remote has no recording assigned there -- nothing to accept")
    if not conn.execute("SELECT 1 FROM recordings WHERE mbid=?1", (mbid,)).fetchone():
        # remote_peek only ever carries the id, not a title or artists -- unlike
        # a synced change `[SPEC-DF-109]`, there is nothing here to construct an
        # unseen recording from.
        raise ValueError(f"recording {mbid} is not known here -- remote_peek carries only "
                          f"an id, not enough to create one; bundle this recording here first")
    conn.execute("DELETE FROM passage_recordings WHERE passage_id=?1", (passage_id,))
    conn.execute(
        "INSERT INTO passage_recordings (passage_id, mbid, weight, source) VALUES (?1,?2,1.0,?3)",
        (passage_id, mbid, "remote-basis"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--kind", required=True, choices=["id_review", "boundary_review"])
    ap.add_argument("--audio-md5", required=True)
    ap.add_argument("--passage-kind", required=True)
    ap.add_argument("--start-ms", type=int, required=True)
    ap.add_argument("--end-ms", type=int, required=True)
    ap.add_argument("--value", required=True,
                     help="the remote's current value, exactly as remote_peek.py printed it")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        value = json.loads(args.value)
    except json.JSONDecodeError as e:
        say(f"--value is not valid JSON: {e}")
        return 2

    anchor = {"audio_md5": args.audio_md5, "passage_kind": args.passage_kind,
              "start_ms": args.start_ms, "end_ms": args.end_ms}
    # Catalogue-only, and it writes -- library half as `main`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY, writable=True, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA foreign_keys = ON")

    passage_id = ac.resolve_passage(conn, anchor)
    if passage_id is None:
        say(f"no passage here matches {anchor} -- nothing to accept a basis for")
        if args.json:
            say(json.dumps({"ok": False, "error": "passage not found"}))
        return 1

    say(f"passage {passage_id}: accepting the remote's {args.kind} value -- {value}")
    if args.commit:
        # Only inside `--commit` -- unlike `apply_changes.py`'s "schema
        # readiness is not a decision," this tool's own rehearsal explicitly
        # promises "nothing was written," and that must stay true even for
        # the one case (a local `passages` never migrated for fade) this
        # would otherwise need to touch.
        if args.kind == "boundary_review" and "fade_in_ms" in value:
            ac.ensure_passages_fade_columns(conn)
            conn.commit()  # closed out on its own, before the real write's own transaction
        conn.execute("BEGIN IMMEDIATE")
        try:
            if args.kind == "boundary_review":
                accept_boundary(conn, passage_id, value)
            else:
                accept_id(conn, passage_id, value)
        except (ValueError, KeyError, sqlite3.Error) as e:
            conn.rollback()
            say(f"refused: {e}")
            if args.json:
                say(json.dumps({"ok": False, "error": str(e)}))
            return 1
        conn.commit()
        say("committed")
    else:
        say("nothing was written. Re-run with --commit to do it.")

    if args.json:
        say(json.dumps({"ok": True, "passage_id": passage_id, "committed": args.commit}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
