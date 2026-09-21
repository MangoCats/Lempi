#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Apply synced edits to this installation `[SPEC006 §9]`.

Reads a `changes.json` from `tools/export_changes.py` and, for each record,
compares this installation's *current* value at the same portable identity
against the record's baseline and target -- the same three-way merge git
uses:

  * current == baseline   -> nothing has changed here since the source's
                              edit. FAST-FORWARD, applied automatically.
  * current == target      -> the same correction already landed. NO-OP.
  * current == neither      -> this installation changed it independently
                              since the shared baseline. CONFLICT: refused
                              until a person names a side.

Rehearse by default, like every other tool here. Run with the player
stopped, the same posture `[PI5-LIB-010]` used for the one real library
swap -- this writes to `passages`, `passage_recordings` and
`recording_artists` directly, with no dependency on Lempi being built with
`vipunen-support` at all.

    python tools/apply_changes.py /var/lempi/listener.db changes.json
    python tools/apply_changes.py /var/lempi/listener.db changes.json --commit
    python tools/apply_changes.py /var/lempi/listener.db changes.json --resolve 3=ours
    python tools/apply_changes.py /var/lempi/listener.db changes.json --resolve 3=theirs --commit

`ours` keeps what is already on this machine, discarding the incoming
change. `theirs` applies the incoming change, overwriting what diverged.
Both still need `--commit` to actually write; without it, a `--resolve` run
only previews what that resolution would do.

**`--emit-sql OUT.sql` `[SPEC-DF-111]`**, for a target with no Python at all
(lempipi, per `[SPEC-DF-108]`): runs the identical merge against `db` as a
**read-only** comparison -- nothing is ever committed to `db` itself when
this is given, regardless of `--commit` -- and captures the literal SQL
actually executed into `OUT.sql`, ready to hand to a target's own `sqlite3`
CLI:

    python tools/apply_changes.py /tmp/lempipi-copy.db changes.json --commit --emit-sql patch.sql
    scp patch.sql pi@lempipi:/tmp/patch.sql
    ssh pi@lempipi 'systemctl stop lempi && { echo "ATTACH DATABASE '''/srv/library/library.db''' AS lib;"; cat /tmp/patch.sql; } | sqlite3 /var/lempi/listener.db && systemctl start lempi'

**`--clear-flags`**, combined with either write mode: for each change
actually applied, also deletes the `listener_flags` row(s) that plausibly
named it `[SPEC-DF-112]` -- closing the loop `tools/import_flags.py` opened,
the same checkbox semantics either way, just cleared by the sync instead of
by hand.
"""

import argparse
import json
import socket
import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]

SOURCE_PREFIX = "synced"


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


# The bare, pre-`[SPEC-DF-104]`/`[SPEC-SUI-226]` shape of each review table
# -- module-level so `remote_snapshot.py` can create exactly this much
# without also running the `ALTER TABLE` loop below `[SPEC-DF-120]`. That
# loop is only ever a no-op-and-untraced on a table these already exist on,
# so a caller that ran it first would silently swallow the very statements
# a real push needs to *capture* and ship to a remote missing them --
# reused here, not duplicated, so the two can never drift out of sync.
ID_REVIEWS_TABLE = (
    "CREATE TABLE IF NOT EXISTS id_reviews (passage_id INTEGER PRIMARY KEY, "
    "decision TEXT NOT NULL, chosen_mbid TEXT, decided_at TEXT NOT NULL, "
    "chosen_release_mbid TEXT, previous_mbid TEXT, applied_at TEXT)")
BOUNDARY_REVIEWS_TABLE = (
    "CREATE TABLE IF NOT EXISTS boundary_reviews (passage_id INTEGER PRIMARY KEY, "
    "start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, lead_in_ms INTEGER, "
    "lead_out_ms INTEGER, gain_db REAL, decided_at TEXT NOT NULL, applied_at TEXT)")
ARTIST_REVIEWS_TABLE = (
    "CREATE TABLE IF NOT EXISTS artist_reviews (recording_mbid TEXT PRIMARY KEY, "
    "passage_id INTEGER, artist_mbid TEXT NOT NULL, artist_name TEXT NOT NULL, "
    "previous_artist_mbid TEXT, previous_artist_name TEXT, previous_artist_weight REAL, "
    "decided_at TEXT NOT NULL, applied_at TEXT)")


# The `ALTER TABLE ADD COLUMN` migrations layered onto the bare tables
# above, over time -- module-level for the identical reason those are
# `[SPEC-DF-120]`: `remote_snapshot.py` needs this exact list to know which
# of them a *real* remote already has (so its own snapshot pre-adds only
# those, and leaves a genuinely missing one for `ensure_review_tables()`'s
# own later run to add fresh and capture), not just "bare or fully migrated."
REVIEW_TABLE_MIGRATIONS = [
    ("boundary_reviews", "audio_md5", "TEXT"),
    ("boundary_reviews", "orig_kind", "TEXT"),
    ("boundary_reviews", "orig_start_ms", "INTEGER"),
    ("boundary_reviews", "orig_end_ms", "INTEGER"),
    ("boundary_reviews", "orig_lead_in_ms", "INTEGER"),
    ("boundary_reviews", "orig_lead_out_ms", "INTEGER"),
    ("boundary_reviews", "orig_gain_db", "REAL"),
    ("id_reviews", "origin", "TEXT"),
    ("boundary_reviews", "origin", "TEXT"),
    ("artist_reviews", "origin", "TEXT"),
    # `[SPEC-SUI-226]`, same shape as the `orig_*` block above.
    ("boundary_reviews", "fade_in_ms", "INTEGER"),
    ("boundary_reviews", "fade_out_ms", "INTEGER"),
    ("boundary_reviews", "fade_in_curve", "TEXT"),
    ("boundary_reviews", "fade_out_curve", "TEXT"),
    ("boundary_reviews", "orig_fade_in_ms", "INTEGER"),
    ("boundary_reviews", "orig_fade_out_ms", "INTEGER"),
    ("boundary_reviews", "orig_fade_in_curve", "TEXT"),
    ("boundary_reviews", "orig_fade_out_curve", "TEXT"),
]


def ensure_review_tables(conn: sqlite3.Connection) -> None:
    """This tool has no dependency on Lempi ever having run against the
    target file at all -- it writes to the SQLite path directly, the same as
    `apply_reviews.py`, and needs no `vipunen-support` build on the target.
    A library a `vipunen-support` Lempi has genuinely never touched has none
    of these three tables yet; one that has but predates `[SPEC-DF-104]`'s
    `origin` column is missing only that. Both gaps are closed here, the
    schema exactly matching what `PlayerStore::open`'s own
    `ensure_review_table` and its two siblings create.
    """
    conn.execute(ID_REVIEWS_TABLE)
    conn.execute(BOUNDARY_REVIEWS_TABLE)
    conn.execute(ARTIST_REVIEWS_TABLE)
    for table, column, coltype in REVIEW_TABLE_MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        except sqlite3.OperationalError:
            pass  # already has it


def ensure_passages_fade_columns(conn: sqlite3.Connection) -> None:
    """`fade_in_ms`/`fade_out_ms`/`fade_in_curve`/`fade_out_curve` `[SPEC-SUI-226]`
    on `passages` itself -- the same idempotent `ALTER TABLE ... DEFAULT`
    migration `tools/add_fade_columns.py` runs standalone, run here too so a
    target that has never had that run by hand still has somewhere to land
    an incoming fade edit, rather than this tool crashing on "no such
    column" the first time one arrives, or silently dropping it.
    """
    for column, ddl in [
        ("fade_in_ms", "INTEGER NOT NULL DEFAULT 20"),
        ("fade_out_ms", "INTEGER NOT NULL DEFAULT 20"),
        ("fade_in_curve", "TEXT NOT NULL DEFAULT 'exponential'"),
        ("fade_out_curve", "TEXT NOT NULL DEFAULT 'exponential'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE passages ADD COLUMN {column} {ddl}")
        except sqlite3.OperationalError:
            pass  # already has it


def resolve_passage(conn: sqlite3.Connection, anchor: dict):
    row = conn.execute(
        """SELECT p.passage_id FROM passages p JOIN files f ON f.file_id = p.file_id
            WHERE f.audio_md5 = ?1 AND p.kind = ?2 AND p.start_ms = ?3 AND p.end_ms = ?4""",
        (anchor["audio_md5"], anchor["passage_kind"], anchor["start_ms"], anchor["end_ms"]),
    ).fetchone()
    return row[0] if row else None


def resolve_boundary_passage(conn: sqlite3.Connection, change: dict):
    """`resolve_passage` against the anchor (the pre-edit span), falling back
    to the *target* span if that finds nothing.

    A boundary edit changes the very field its own anchor is keyed on, so a
    second run against a receiver that already landed it once can no longer
    find the passage at its pre-edit span -- it now sits at the target span,
    because the first run put it there. Without this fallback, re-sending
    the same `changes.json` (`[SPEC-DF-105]`'s whole idempotency argument)
    would report "not present" on the second run instead of "already in
    sync", for the one kind of change that moves its own identity.
    """
    found = resolve_passage(conn, change["anchor"])
    if found is not None:
        return found
    target_anchor = {**change["anchor"], "start_ms": change["target"]["start_ms"],
                      "end_ms": change["target"]["end_ms"]}
    return resolve_passage(conn, target_anchor)


def current_recording(conn: sqlite3.Connection, passage_id: int):
    row = conn.execute(
        "SELECT mbid FROM passage_recordings WHERE passage_id=?1 ORDER BY weight DESC, mbid LIMIT 1",
        (passage_id,)).fetchone()
    return row[0] if row else None


def current_boundary(conn: sqlite3.Connection, passage_id: int):
    row = conn.execute(
        """SELECT start_ms, end_ms, lead_in_ms, lead_out_ms, gain_db,
                  fade_in_ms, fade_out_ms, fade_in_curve, fade_out_curve
             FROM passages WHERE passage_id=?1""",
        (passage_id,)).fetchone()
    if not row:
        return None
    keys = ["start_ms", "end_ms", "lead_in_ms", "lead_out_ms", "gain_db",
            "fade_in_ms", "fade_out_ms", "fade_in_curve", "fade_out_curve"]
    return dict(zip(keys, row))


def current_artist(conn: sqlite3.Connection, recording_mbid: str):
    row = conn.execute(
        """SELECT a.mbid, a.name FROM recording_artists ra JOIN artists a ON a.mbid = ra.artist_mbid
            WHERE ra.mbid=?1 ORDER BY ra.weight DESC, a.name LIMIT 1""", (recording_mbid,)).fetchone()
    return {"artist_mbid": row[0], "artist_name": row[1]} if row else {"artist_mbid": None, "artist_name": None}


def classify(current, baseline: dict, target: dict, keys: list) -> str:
    """`[SPEC-DF-101]`'s three-way merge, generic over which fields matter."""
    if current is None:
        return "missing"
    cur = tuple(current.get(k) for k in keys)
    if cur == tuple(target.get(k) for k in keys):
        return "noop"
    if cur == tuple(baseline.get(k) for k in keys):
        return "fastforward"
    return "conflict"


def history_for(conn: sqlite3.Connection, table: str, where: str, params: tuple):
    """The target's own recorded reason its current value is what it is, if
    any `[SPEC-DF-106]` -- distinct from "no correction history recorded
    here", which is itself informative: it means whatever is here came from
    ordinary ingest, not a considered decision.
    """
    row = conn.execute(
        f"SELECT decided_at, origin FROM {table} WHERE {where} AND applied_at IS NOT NULL",
        params).fetchone()
    if not row:
        return None
    decided_at, origin = row
    return {"decided_at": decided_at, "origin": origin or "here"}


def human(change: dict, fallback: str) -> str:
    """What a person calls this change `[SPEC-DF-108]`.

    `subject` used to be `audio_md5[:12]` and nothing else, which is a
    correct identity and an unusable label: a report of four conflicts read
    as four hex strings, and the only way to find out which songs they were
    was to go and query for them by hand. `export_changes.py` now carries a
    title and artist with every change, from the library that has them.

    The technical identifier is kept, never replaced -- it is what actually
    resolves the change, and it is what anyone debugging this needs. It just
    no longer travels alone.
    """
    label = change.get("label") or {}
    title, artist = label.get("title"), label.get("artist")
    if title and artist:
        return f'"{title}" by {artist}  [{fallback}]'
    if title:
        return f'"{title}"  [{fallback}]'
    return fallback


def report_conflict(n: int, kind: str, subject: str, change: dict, current_desc: str, history) -> None:
    say(f"\n#{n} CONFLICT  {kind}: {subject}")
    say(f"   incoming ({change['origin']}, decided {change['decided_at']}): {describe(change['target'], kind)}")
    say(f"   baseline (what {change['origin']} saw before its edit):    {describe(change['baseline'], kind)}")
    say(f"   here now:                                                 {current_desc}")
    if history:
        say(f"     decided here {history['decided_at']} ({history['origin']}) -- diverged independently")
    else:
        say("     no correction history recorded here -- from ordinary ingest")
    say(f"   --resolve {n}=ours    keep what is here, discard the incoming change")
    say(f"   --resolve {n}=theirs  apply the incoming change, overwriting what is here")
    say(f"   --resolve {n}=skip    leave it for next time")


def describe(values: dict, kind: str) -> str:
    if kind == "id_review":
        return values.get("mbid") or "(none)"
    if kind == "artist_review":
        return values.get("artist_name") or values.get("artist_mbid") or "(none)"
    if kind == "boundary_review":
        fade = (f", fade-in {values['fade_in_ms']}ms {values.get('fade_in_curve')}, "
                f"fade-out {values['fade_out_ms']}ms {values.get('fade_out_curve')}"
                if "fade_in_ms" in values else "")
        return (f"{values.get('start_ms')}-{values.get('end_ms')}, "
                f"lead-in {values.get('lead_in_ms')}, lead-out {values.get('lead_out_ms')}, "
                f"gain {values.get('gain_db')}{fade}")
    return str(values)


def apply_id_review(conn: sqlite3.Connection, passage_id: int, change: dict) -> None:
    target = change["target"]
    mbid = target["mbid"]
    if not conn.execute("SELECT 1 FROM recordings WHERE mbid=?1", (mbid,)).fetchone():
        if not target.get("title"):
            raise ValueError(f"recording {mbid} is not known here and the change carries no title "
                              f"to create it with -- bundle this music here first")
        conn.execute(
            "INSERT INTO recordings (mbid, title, source) VALUES (?1, ?2, ?3)",
            (mbid, target["title"], f"{SOURCE_PREFIX}:{change['origin']}"))
        for a in target.get("artists") or []:
            if not a.get("name"):
                continue
            conn.execute(
                "INSERT OR IGNORE INTO artists (mbid, name, source) VALUES (?1, ?2, ?3)",
                (a["mbid"], a["name"], f"{SOURCE_PREFIX}:{change['origin']}"))
            conn.execute(
                "INSERT OR IGNORE INTO recording_artists (mbid, artist_mbid, weight, source) "
                "VALUES (?1, ?2, 1.0, ?3)", (mbid, a["mbid"], f"{SOURCE_PREFIX}:{change['origin']}"))
    conn.execute("DELETE FROM passage_recordings WHERE passage_id=?1", (passage_id,))
    conn.execute(
        "INSERT INTO passage_recordings (passage_id, mbid, weight, source) VALUES (?1, ?2, 1.0, ?3)",
        (passage_id, mbid, f"{SOURCE_PREFIX}:{change['origin']}"))
    conn.execute(
        """INSERT INTO id_reviews (passage_id, decision, chosen_mbid, previous_mbid, decided_at,
                                    applied_at, origin)
           VALUES (?1, 'reassigned', ?2, ?3, ?4, datetime('now'), ?5)
           ON CONFLICT(passage_id) DO UPDATE SET
               decision='reassigned', chosen_mbid=excluded.chosen_mbid,
               previous_mbid=excluded.previous_mbid, decided_at=excluded.decided_at,
               applied_at=excluded.applied_at, origin=excluded.origin""",
        (passage_id, mbid, change["baseline"].get("mbid"), change["decided_at"], change["origin"]))


def apply_boundary_review(conn: sqlite3.Connection, passage_id: int, change: dict) -> None:
    t = change["target"]
    b = change["baseline"]
    # Fade `[SPEC-SUI-226]` only if the incoming change actually carries an
    # opinion on it -- a `changes.json` from a pre-fade `export_changes.py`
    # has none, and neither writing NULL into `passages`'s `NOT NULL` fade
    # columns nor asserting a value never measured is right; leaving them
    # untouched is the same "do not assert what was not there" rule
    # `[SPEC-PL-030]` already applies to lead/gain.
    has_fade = "fade_in_ms" in t
    if has_fade:
        conn.execute(
            """UPDATE passages SET start_ms=?1, end_ms=?2, lead_in_ms=?3, lead_out_ms=?4,
                                    gain_db=?5, fade_in_ms=?6, fade_out_ms=?7,
                                    fade_in_curve=?8, fade_out_curve=?9,
                                    boundary_src='manual' WHERE passage_id=?10""",
            (t["start_ms"], t["end_ms"], t["lead_in_ms"], t["lead_out_ms"], t["gain_db"],
             t["fade_in_ms"], t["fade_out_ms"], t["fade_in_curve"], t["fade_out_curve"],
             passage_id))
    else:
        conn.execute(
            """UPDATE passages SET start_ms=?1, end_ms=?2, lead_in_ms=?3, lead_out_ms=?4,
                                    gain_db=?5, boundary_src='manual' WHERE passage_id=?6""",
            (t["start_ms"], t["end_ms"], t["lead_in_ms"], t["lead_out_ms"], t["gain_db"], passage_id))
    anchor = change["anchor"]
    if (t["start_ms"], t["end_ms"]) != (anchor["start_ms"], anchor["end_ms"]):
        audio_md5 = anchor["audio_md5"]
        still_used = conn.execute(
            """SELECT 1 FROM passages p2 JOIN files f2 ON f2.file_id=p2.file_id
                WHERE f2.audio_md5=?1 AND p2.start_ms=?2 AND p2.end_ms=?3 AND p2.passage_id != ?4""",
            (audio_md5, anchor["start_ms"], anchor["end_ms"], passage_id)).fetchone()
        if not still_used:
            conn.execute(
                "DELETE FROM lowlevel_cache WHERE audio_md5=?1 AND start_ms=?2 AND end_ms=?3",
                (audio_md5, anchor["start_ms"], anchor["end_ms"]))
    if has_fade:
        conn.execute(
            """INSERT INTO boundary_reviews
                   (passage_id, start_ms, end_ms, lead_in_ms, lead_out_ms, gain_db,
                    fade_in_ms, fade_out_ms, fade_in_curve, fade_out_curve,
                    audio_md5, orig_kind, orig_start_ms, orig_end_ms, orig_lead_in_ms,
                    orig_lead_out_ms, orig_gain_db, orig_fade_in_ms, orig_fade_out_ms,
                    orig_fade_in_curve, orig_fade_out_curve, decided_at, applied_at, origin)
               VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17,?18,?19,?20,?21,?22,
                       datetime('now'),?23)
               ON CONFLICT(passage_id) DO UPDATE SET
                   start_ms=excluded.start_ms, end_ms=excluded.end_ms,
                   lead_in_ms=excluded.lead_in_ms, lead_out_ms=excluded.lead_out_ms,
                   gain_db=excluded.gain_db,
                   fade_in_ms=excluded.fade_in_ms, fade_out_ms=excluded.fade_out_ms,
                   fade_in_curve=excluded.fade_in_curve, fade_out_curve=excluded.fade_out_curve,
                   decided_at=excluded.decided_at,
                   applied_at=excluded.applied_at, origin=excluded.origin""",
            (passage_id, t["start_ms"], t["end_ms"], t["lead_in_ms"], t["lead_out_ms"], t["gain_db"],
             t["fade_in_ms"], t["fade_out_ms"], t["fade_in_curve"], t["fade_out_curve"],
             anchor["audio_md5"], anchor["passage_kind"], anchor["start_ms"], anchor["end_ms"],
             b["lead_in_ms"], b["lead_out_ms"], b["gain_db"],
             b.get("fade_in_ms"), b.get("fade_out_ms"), b.get("fade_in_curve"), b.get("fade_out_curve"),
             change["decided_at"], change["origin"]))
    else:
        conn.execute(
            """INSERT INTO boundary_reviews
                   (passage_id, start_ms, end_ms, lead_in_ms, lead_out_ms, gain_db,
                    audio_md5, orig_kind, orig_start_ms, orig_end_ms, orig_lead_in_ms,
                    orig_lead_out_ms, orig_gain_db, decided_at, applied_at, origin)
               VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,datetime('now'),?15)
               ON CONFLICT(passage_id) DO UPDATE SET
                   start_ms=excluded.start_ms, end_ms=excluded.end_ms,
                   lead_in_ms=excluded.lead_in_ms, lead_out_ms=excluded.lead_out_ms,
                   gain_db=excluded.gain_db, decided_at=excluded.decided_at,
                   applied_at=excluded.applied_at, origin=excluded.origin""",
            (passage_id, t["start_ms"], t["end_ms"], t["lead_in_ms"], t["lead_out_ms"], t["gain_db"],
             anchor["audio_md5"], anchor["passage_kind"], anchor["start_ms"], anchor["end_ms"],
             b["lead_in_ms"], b["lead_out_ms"], b["gain_db"],
             change["decided_at"], change["origin"]))


def apply_artist_review(conn: sqlite3.Connection, recording_mbid: str, change: dict) -> None:
    # Keyed by `recording_mbid` -- a synced correction has no originating
    # passage on this machine at all, which is exactly why the table is not
    # keyed by one `[SPEC-DF-103]`.
    t = change["target"]
    conn.execute(
        "INSERT OR IGNORE INTO artists (mbid, name, source) VALUES (?1, ?2, ?3)",
        (t["artist_mbid"], t["artist_name"], f"{SOURCE_PREFIX}:{change['origin']}"))
    conn.execute("DELETE FROM recording_artists WHERE mbid=?1", (recording_mbid,))
    conn.execute(
        "INSERT INTO recording_artists (mbid, artist_mbid, weight, source) VALUES (?1, ?2, 1.0, ?3)",
        (recording_mbid, t["artist_mbid"], f"{SOURCE_PREFIX}:{change['origin']}"))
    b = change["baseline"]
    conn.execute(
        """INSERT INTO artist_reviews
               (recording_mbid, artist_mbid, artist_name,
                previous_artist_mbid, previous_artist_name, previous_artist_weight,
                decided_at, applied_at, origin)
           VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, datetime('now'), ?8)
           ON CONFLICT(recording_mbid) DO UPDATE SET
               artist_mbid=excluded.artist_mbid, artist_name=excluded.artist_name,
               previous_artist_mbid=excluded.previous_artist_mbid,
               previous_artist_name=excluded.previous_artist_name,
               previous_artist_weight=excluded.previous_artist_weight,
               decided_at=excluded.decided_at, applied_at=excluded.applied_at,
               origin=excluded.origin""",
        (recording_mbid, t["artist_mbid"], t["artist_name"],
         b.get("artist_mbid"), b.get("artist_name"), b.get("weight"),
         change["decided_at"], change["origin"]))


def clear_flags_for(conn: sqlite3.Connection, kind: str, passage_id, change: dict) -> None:
    """`--clear-flags` `[SPEC-DF-112]`: a change landing for exactly the
    subject a flag named is the looking-at `[REQ-VIS-265]`'s checkbox asked
    for, having happened. Clears every subject the change plausibly *was*
    flagged under, not only the one it turned out to be -- an `id_review`'s
    passage may have been flagged before it had a recording at all, or by
    the recording it is being moved away from.
    """
    subjects = []
    if passage_id is not None:
        subjects.append(("passage", str(passage_id)))
    if kind == "id_review":
        # Both ends: a listener most often flags a *misidentification* while
        # it still carries the wrong id, so the baseline -- what it was
        # flagged as -- is at least as likely to be the flagged subject as
        # the corrected target is.
        subjects.append(("recording", change["target"]["mbid"]))
        if change["baseline"].get("mbid"):
            subjects.append(("recording", change["baseline"]["mbid"]))
    elif kind == "artist_review":
        subjects.append(("recording", change["anchor"]["recording_mbid"]))
    for subject_kind, subject_id in subjects:
        conn.execute(
            "DELETE FROM listener_flags WHERE subject_kind=?1 AND subject_id=?2",
            (subject_kind, subject_id))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("changes", help="changes.json from export_changes.py")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--resolve", action="append", default=[], metavar="N=ours|theirs|skip")
    ap.add_argument("--emit-sql", metavar="OUT.sql",
                     help="write the literal SQL a commit would run to this file instead "
                          "of writing to `db` `[SPEC-DF-111]`; `db` is never modified when "
                          "this is given, whether or not --commit is also passed")
    ap.add_argument("--clear-flags", action="store_true",
                     help="delete the listener_flags row(s) a successfully-applied change "
                          "plausibly named `[SPEC-DF-112]`")
    ap.add_argument("--json", action="store_true",
                     help="also print one final JSON summary line, for a caller "
                          "(the Vipunen console's remote-push job) rather than a person")
    args = ap.parse_args()

    resolutions = {}
    for spec in args.resolve:
        n, _, verdict = spec.partition("=")
        if verdict not in ("ours", "theirs", "skip"):
            say(f"--resolve {spec!r}: verdict must be ours, theirs or skip")
            return 2
        resolutions[int(n)] = verdict

    with open(args.changes, encoding="utf-8") as f:
        doc = json.load(f)
    changes = doc.get("changes", [])

    # Writes the catalogue (passages/recordings/artists) and stamps a
    # listener-side review table to say the decision landed. Catalogue
    # as `main` because that is what it creates and rewrites;
    # `peer_writable` because the stamp is a genuine second-half write,
    # and saying so at the call site is the point `[IMPL-DBSPLIT-025]`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY,
                            writable=True, peer_writable=True, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA foreign_keys = ON")

    # `--emit-sql` `[SPEC-DF-111]`: capture the literal, fully-quoted SQL
    # SQLite actually executes -- placeholders already expanded to safe
    # literals, the same text `.trace` in the `sqlite3` CLI would show --
    # instead of ever committing it to `db`. Attached before schema
    # readiness below so a target missing the review tables entirely still
    # gets the same `CREATE TABLE IF NOT EXISTS` statements in the script.
    sql_log = [] if args.emit_sql else None
    if sql_log is not None:
        conn.set_trace_callback(sql_log.append)
    writing = args.commit or bool(args.emit_sql)

    # Schema readiness, not a decision -- run even in rehearsal, the same as
    # `ensure_review_table` and its siblings run unconditionally on every
    # `PlayerStore::open` regardless of whether anything gets written after.
    # Traced too `[SPEC-DF-111]`: a target missing the review tables
    # entirely -- every real appliance today -- still needs them in the
    # emitted script, not just in this disposable compare copy.
    #
    # Explicit `BEGIN IMMEDIATE` first, always `[SPEC-SUI-226]` -- Python's
    # `sqlite3` module never opens its own implicit transaction for a DDL
    # statement (`ALTER TABLE`/`CREATE TABLE`), so left to the module's own
    # heuristics, a later `conn.rollback()` (for `--emit-sql`) silently does
    # NOT undo them; verified directly against this module's actual
    # behaviour, not assumed. Issuing `BEGIN IMMEDIATE` ourselves first
    # forces the DDL into a real, rollback-able transaction regardless.
    # NOT committed here when `--emit-sql` is given: that mode's own promise
    # is "nothing is ever committed to `db` itself," and
    # `ensure_passages_fade_columns` -- unlike `ensure_review_tables`, whose
    # statements are almost always no-ops against an already-migrated review
    # table -- can be a genuine, first-time mutation of `passages` on a
    # target that has never run `add_fade_columns.py`. Left inside the open
    # transaction, the later `conn.rollback()` for `--emit-sql` now correctly
    # undoes it, and the trace callback (already attached above) still
    # captures it for the emitted script either way.
    conn.execute("BEGIN IMMEDIATE")
    ensure_review_tables(conn)
    ensure_passages_fade_columns(conn)
    if not args.emit_sql:
        conn.commit()

    # A compare copy predating `[REQ-VIS-265]` entirely has no `listener_flags`
    # at all -- `--clear-flags` is then simply nothing to do, not an error
    # that would otherwise mask the change it was attached to having landed.
    have = lempi_db.tables(conn)  # both halves, not just `main` [IMPL-DBSPLIT-025]
    clear_flags_ok = args.clear_flags and "listener_flags" in have

    say(f"{len(changes)} change(s) in {args.changes}")
    # Per-change detail for whatever is *not* routine `[SPEC-DF-108]`. The
    # counts alone answer "did it work"; they cannot answer "which song is
    # stuck, and why", which is the only question a person has once the
    # answer to the first one is no. `noop` and plain fast-forwards are
    # deliberately left out: a list of everything that went fine is how the
    # two entries that did not get lost.
    details: list = []
    counts = {"fastforward": 0, "noop": 0, "conflict": 0, "missing": 0, "resolved": 0, "error": 0,
              "cleared": 0}
    # `--emit-sql` already has an open transaction from schema readiness
    # above (never committed, on purpose) -- a second `BEGIN IMMEDIATE` here
    # would fail against it, so this only opens a fresh one for the plain
    # `--commit` path, which committed schema readiness immediately above
    # and so has none open yet.
    if writing and not args.emit_sql:
        conn.execute("BEGIN IMMEDIATE")

    for i, change in enumerate(changes, 1):
        kind = change["kind"]
        anchor = change["anchor"]

        passage_id = None
        if kind == "id_review":
            passage_id = resolve_passage(conn, anchor)
            current = {"mbid": current_recording(conn, passage_id)} if passage_id else None
            keys = ["mbid"]
            subject = human(change, anchor["audio_md5"][:12] + "…")
            history = history_for(conn, "id_reviews", "passage_id=?1", (passage_id,)) if passage_id else None
        elif kind == "boundary_review":
            passage_id = resolve_boundary_passage(conn, change)
            current = current_boundary(conn, passage_id) if passage_id else None
            keys = ["start_ms", "end_ms", "lead_in_ms", "lead_out_ms", "gain_db"]
            # Only compare fade if the exported change actually carries an
            # opinion on it `[SPEC-SUI-226]` -- a `changes.json` from a
            # pre-fade `export_changes.py` has none, and comparing against
            # it as if it did would misreport every such change as a
            # conflict over fields it never meant to touch.
            if "fade_in_ms" in change["target"]:
                keys += ["fade_in_ms", "fade_out_ms", "fade_in_curve", "fade_out_curve"]
            subject = human(change, anchor["audio_md5"][:12] + "…")
            history = history_for(conn, "boundary_reviews", "passage_id=?1", (passage_id,)) if passage_id else None
        elif kind == "artist_review":
            recording_mbid = anchor["recording_mbid"]
            has_recording = conn.execute(
                "SELECT 1 FROM recordings WHERE mbid=?1", (recording_mbid,)).fetchone()
            current = current_artist(conn, recording_mbid) if has_recording else None
            keys = ["artist_mbid"]
            subject = human(change, f"recording {recording_mbid}")
            history = history_for(conn, "artist_reviews", "recording_mbid=?1", (recording_mbid,))
        else:
            say(f"#{i}: unknown change kind {kind!r}, skipped")
            counts["error"] += 1
            continue

        verdict = classify(current, change["baseline"], change["target"], keys)
        if verdict == "missing":
            say(f"#{i} {kind}: not present here ({subject}) -- nothing to resolve this against")
            details.append({"n": i, "kind": kind, "verdict": "missing", "subject": subject,
                            "label": change.get("label") or {}, "anchor": change["anchor"],
                            "incoming": describe(change["target"], kind),
                            "origin": change.get("origin"),
                            "decided_at": change.get("decided_at")})
            counts["missing"] += 1
            continue
        if verdict == "noop":
            counts["noop"] += 1
            continue
        if verdict == "conflict":
            resolved = resolutions.get(i)
            if resolved == "skip" or resolved is None:
                report_conflict(i, kind, subject, change, describe(current, kind), history)
                details.append({"n": i, "kind": kind, "verdict": "conflict", "subject": subject,
                                "label": change.get("label") or {}, "anchor": change["anchor"],
                                "incoming": describe(change["target"], kind),
                                "baseline": describe(change["baseline"], kind),
                                "here": describe(current, kind),
                                "origin": change.get("origin"),
                                "decided_at": change.get("decided_at"),
                                "diverged": bool(history)})
                counts["conflict"] += 1
                continue
            counts["resolved"] += 1
            if resolved == "ours":
                say(f"#{i} {kind}: keeping what is here ({subject})")
                continue
            # resolved == "theirs": falls through to apply, same as a fast-forward.
            say(f"#{i} {kind}: applying the incoming change over a local divergence ({subject})")
        else:
            counts["fastforward"] += 1
            say(f"#{i} {kind}: {subject} -> {describe(change['target'], kind)}")

        if not writing:
            continue
        try:
            if kind == "id_review":
                apply_id_review(conn, passage_id, change)
            elif kind == "boundary_review":
                apply_boundary_review(conn, passage_id, change)
            else:
                apply_artist_review(conn, recording_mbid, change)
            if clear_flags_ok:
                before = conn.total_changes
                clear_flags_for(conn, kind, passage_id, change)
                counts["cleared"] += conn.total_changes - before
        except (ValueError, sqlite3.Error) as e:
            say(f"    refused: {e}")
            counts["error"] += 1

    say(f"\n{counts['fastforward']} fast-forward, {counts['resolved']} resolved, "
        f"{counts['noop']} already in sync, {counts['conflict']} conflict(s) unresolved, "
        f"{counts['missing']} not present here, {counts['error']} refused"
        + (f", {counts['cleared']} flag(s) cleared" if args.clear_flags else ""))

    landed = counts["fastforward"] or counts["resolved"]
    patch_statements = None
    if args.emit_sql:
        # `db` is never the destination here `[SPEC-DF-111]` -- the schema
        # setup above was deliberately left uncommitted for exactly this
        # `[SPEC-SUI-226]`, so one rollback here undoes it along with every
        # merge write below, leaving only the trace to act on.
        conn.rollback()
        # Only the writes: every comparison above issues its own `SELECT`s
        # (`resolve_passage`, `current_recording`, `history_for`, the
        # `have`/existence checks...) and the trace callback sees those too --
        # harmless to replay but pure noise in what is meant to be a short,
        # readable "here is exactly what changed" script. Transaction control
        # is re-issued at write time instead of preserved verbatim, since
        # `BEGIN`/`COMMIT` were traced as well (Python's own implicit
        # transaction handling, on top of the explicit `BEGIN IMMEDIATE`
        # above), and collapsing to one pair is simpler than reasoning about
        # which of those to keep.
        WRITE_KEYWORDS = ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "REPLACE")
        patch = [s for s in sql_log if s.lstrip().upper().startswith(WRITE_KEYWORDS)]
        patch_statements = len(patch)
        with open(args.emit_sql, "w", encoding="utf-8") as f:
            f.write("BEGIN IMMEDIATE;\n")
            for stmt in patch:
                f.write(stmt.rstrip().rstrip(";") + ";\n")
            f.write("COMMIT;\n")
        say(f"{len(patch)} statement(s) written to {args.emit_sql} -- {args.db} was not modified")
    elif args.commit:
        conn.commit()
        say("committed" if landed else "nothing to write")
    else:
        say("nothing was written. Re-run with --commit to do it.")
    if args.json:
        # `landed` is the one figure that answers "is there anything a
        # remote actually needs" -- `patch_statements` alone is not: it
        # always includes `ensure_review_tables`'s own schema-setup
        # statements, so it is never zero even when nothing changed.
        say(json.dumps({**counts, "patch_statements": patch_statements,
                        "landed": bool(landed), "details": details}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
