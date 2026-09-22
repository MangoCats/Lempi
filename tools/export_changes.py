#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Export applied edits for a remote installation `[SPEC006 §9]`.

The bundle transport (`[SPEC-SUI-095]`) carries new music. This carries a
*correction* to music both installations already have -- the case
`import_bundle` explicitly does nothing for, since it treats a held
`audio_md5` as fully present.

The unit exported is the reviewed decision, not the row it wrote:
`id_reviews`, `boundary_reviews` and `artist_reviews` `[SPEC021 §2]` are
already small journals of what changed, when, and (mostly) what it replaced.
Each applied row becomes one portable JSON record, keyed so a *different*
installation can find the same fact without ever seeing this one's
`passage_id` `[SPEC-DF-035]`.

    python tools/export_changes.py data/library.db -o changes.json
    rsync changes.json pi@lempi02w:/srv/library/incoming/

Read-only: nothing here writes to the database it reads from. The write half
is `tools/apply_changes.py`, run against the *receiving* installation.
"""

import argparse
import json
import socket
import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """This tool opens `mode=ro` `[SPEC013 Stage 2]`'s own reasoning applied
    here -- a read need not risk anything, so it cannot `ALTER TABLE` to add
    a column a review table predating `origin` `[SPEC-DF-104]` lacks. It
    reads around the gap instead: a `NULL` origin here means the same thing
    it always has, "made on this machine", just from a table too old to have
    a column that says so explicitly.
    """
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def label_for_recording(conn: sqlite3.Connection, mbid: str) -> dict:
    """Title and artist, as *this* library knows them `[SPEC-DF-108]`.

    Carried with every change because the receiving side frequently cannot
    work it out. "Not present here" is precisely the verdict that means the
    target has never seen this recording, and a conflict is reported against
    `remote_snapshot.py`'s reconstruction, which holds only the handful of
    rows the merge itself needed -- neither has a title to show. So the
    exporting side, which does know, says it once, and every later report
    can name the song instead of twelve hex characters.

    Advisory, never an identity: `apply_changes.py` still resolves by
    `audio_md5` and `recording_mbid`, exactly as before. This only decides
    what a person reads.
    """
    if not mbid:
        return {}
    # A label is advisory, so it must never be able to fail an export.
    # `recordings`/`artists`/`releases` are all tables a minimal or
    # partial library can legitimately lack -- several of this project's
    # own fixtures do -- and a missing title is simply a change reported
    # by its hash, exactly as before this existed.
    try:
        return _label(conn, mbid)
    except sqlite3.Error:
        return {}


def _label(conn: sqlite3.Connection, mbid: str) -> dict:
    row = conn.execute("SELECT title FROM recordings WHERE mbid = ?1", (mbid,)).fetchone()
    artist = conn.execute(
        """SELECT a.name FROM recording_artists ra JOIN artists a ON a.mbid = ra.artist_mbid
            WHERE ra.mbid = ?1 ORDER BY ra.weight DESC LIMIT 1""", (mbid,)).fetchone()
    out = {}
    if row and row[0]:
        out["title"] = row[0]
    if artist and artist[0]:
        out["artist"] = artist[0]
    return out


def label_for_passage(conn: sqlite3.Connection, passage_id) -> dict:
    """The same, for a passage: whatever recording it currently points at."""
    if passage_id is None:
        return {}
    try:
        return _passage_label(conn, passage_id)
    except sqlite3.Error:
        return {}


def _passage_label(conn: sqlite3.Connection, passage_id) -> dict:
    row = conn.execute(
        """SELECT pr.mbid FROM passage_recordings pr WHERE pr.passage_id = ?1
            ORDER BY pr.weight DESC, pr.mbid LIMIT 1""", (passage_id,)).fetchone()
    label = label_for_recording(conn, row[0]) if row else {}
    album = conn.execute(
        """SELECT rel.title FROM release_recordings rr JOIN releases rel ON rel.mbid = rr.release_mbid
            WHERE rr.mbid = ?1 ORDER BY rr.chosen DESC LIMIT 1""",
        (row[0],)).fetchone() if row else None
    if album and album[0]:
        label["album"] = album[0]
    return label


def export_id_reviews(conn: sqlite3.Connection, hostname: str) -> list:
    changes = []
    origin_expr = "r.origin" if has_column(conn, "id_reviews", "origin") else "NULL"
    for (passage_id, chosen_mbid, previous_mbid, decided_at, origin,
         audio_md5, kind, start_ms, end_ms, title) in conn.execute(
        f"""SELECT r.passage_id, r.chosen_mbid, r.previous_mbid, r.decided_at, {origin_expr},
                  f.audio_md5, p.kind, p.start_ms, p.end_ms, rec.title
             FROM id_reviews r
             JOIN passages p ON p.passage_id = r.passage_id
             JOIN files f ON f.file_id = p.file_id
             LEFT JOIN recordings rec ON rec.mbid = r.chosen_mbid
            WHERE r.applied_at IS NOT NULL AND r.decision = 'reassigned'
              AND r.chosen_mbid IS NOT NULL"""):
        artists = [
            {"mbid": a_mbid, "name": a_name}
            for a_mbid, a_name in conn.execute(
                """SELECT a.mbid, a.name FROM recording_artists ra JOIN artists a ON a.mbid = ra.artist_mbid
                    WHERE ra.mbid = ?1 ORDER BY ra.weight DESC""", (chosen_mbid,))
        ]
        changes.append({
            "kind": "id_review",
            "anchor": {"audio_md5": audio_md5, "passage_kind": kind,
                       "start_ms": start_ms, "end_ms": end_ms},
            "baseline": {"mbid": previous_mbid},
            # `title`/`artists` are carried so a receiver that has never seen
            # this recording before can still construct it -- the same
            # NOT NULL constraints that made the first `apply_reviews.py`
            # unable to apply anything apply here too `[REQ-LIB-165]`.
            "target": {"mbid": chosen_mbid, "title": title, "artists": artists},
            "decided_at": decided_at,
            "origin": origin or hostname,
            "label": label_for_recording(conn, chosen_mbid),
        })
    return changes


def export_boundary_reviews(conn: sqlite3.Connection, hostname: str) -> list:
    changes = []
    if not has_column(conn, "boundary_reviews", "audio_md5"):
        # A table from before `[SPEC-DF-102]` added the baseline columns at
        # all -- nothing here can be anchored on another machine, so there is
        # nothing to export, the same as any one row missing `audio_md5`.
        return changes
    origin_expr = "origin" if has_column(conn, "boundary_reviews", "origin") else "NULL"
    # `fade_*`/`orig_fade_*` `[SPEC-SUI-226]` predate this export on any
    # installation still running a pre-fade Lempi -- read around the gap the
    # same way `origin_expr` already does for `[SPEC-DF-104]`. Unlike
    # `origin_expr`, a missing fade column means OMITTING the keys entirely
    # below, not sending them as `null`: `apply_changes.py` tells "no
    # opinion on fade" apart from "silence the fade ramp" by whether the
    # key is present at all, and a `null` here would read as the latter.
    have_fade = has_column(conn, "boundary_reviews", "fade_in_ms")
    fade_cols = ("fade_in_ms, fade_out_ms, fade_in_curve, fade_out_curve"
                 if have_fade else "NULL, NULL, NULL, NULL")
    orig_fade_cols = ("orig_fade_in_ms, orig_fade_out_ms, orig_fade_in_curve, orig_fade_out_curve"
                       if have_fade else "NULL, NULL, NULL, NULL")
    for (passage_id, start_ms, end_ms, lead_in_ms, lead_out_ms, gain_db, fade_in_ms, fade_out_ms,
         fade_in_curve, fade_out_curve,
         audio_md5, orig_kind, orig_start_ms, orig_end_ms,
         orig_lead_in_ms, orig_lead_out_ms, orig_gain_db,
         orig_fade_in_ms, orig_fade_out_ms, orig_fade_in_curve, orig_fade_out_curve,
         decided_at, origin) in conn.execute(
        f"""SELECT passage_id, start_ms, end_ms, lead_in_ms, lead_out_ms, gain_db, {fade_cols},
                  audio_md5, orig_kind, orig_start_ms, orig_end_ms,
                  orig_lead_in_ms, orig_lead_out_ms, orig_gain_db, {orig_fade_cols},
                  decided_at, {origin_expr}
             FROM boundary_reviews WHERE applied_at IS NOT NULL"""):
        if audio_md5 is None:
            # Applied before `[SPEC-DF-102]` added the baseline columns --
            # nothing to resolve this against on another machine, so it
            # cannot be exported. Not an error: it just predates sync.
            continue
        baseline = {"start_ms": orig_start_ms, "end_ms": orig_end_ms,
                    "lead_in_ms": orig_lead_in_ms, "lead_out_ms": orig_lead_out_ms,
                    "gain_db": orig_gain_db}
        target = {"start_ms": start_ms, "end_ms": end_ms,
                  "lead_in_ms": lead_in_ms, "lead_out_ms": lead_out_ms,
                  "gain_db": gain_db}
        # The column existing is not the same as the ROW having an opinion.
        # A `boundary_reviews` row decided before `[SPEC-SUI-226]` sits in a
        # table that has since been migrated, so `have_fade` is true while
        # every fade value in it is NULL -- and exporting those nulls is
        # exactly what the comment above forbids, by the same reasoning: the
        # receiver reads a present key as "silence the fade ramp". Found
        # live 2026-09-12 pushing to `teacherslounge`: one such row ("Slow
        # Ride", passage 16212) reported a conflict on every push whose two
        # sides had identical spans and differed only in fade, and resolving
        # it changed nothing, so it came back the next time.
        if have_fade and (fade_in_ms is not None or fade_out_ms is not None):
            baseline.update(fade_in_ms=orig_fade_in_ms, fade_out_ms=orig_fade_out_ms,
                             fade_in_curve=orig_fade_in_curve, fade_out_curve=orig_fade_out_curve)
            target.update(fade_in_ms=fade_in_ms, fade_out_ms=fade_out_ms,
                           fade_in_curve=fade_in_curve, fade_out_curve=fade_out_curve)
        changes.append({
            "kind": "boundary_review",
            "anchor": {"audio_md5": audio_md5, "passage_kind": orig_kind,
                       "start_ms": orig_start_ms, "end_ms": orig_end_ms},
            "baseline": baseline,
            "target": target,
            "decided_at": decided_at,
            "origin": origin or hostname,
            "label": label_for_passage(conn, passage_id),
        })
    return changes


def export_artist_reviews(conn: sqlite3.Connection, hostname: str) -> list:
    changes = []
    origin_expr = "origin" if has_column(conn, "artist_reviews", "origin") else "NULL"
    for (recording_mbid, artist_mbid, artist_name,
         previous_artist_mbid, previous_artist_name, previous_artist_weight,
         decided_at, origin) in conn.execute(
        f"""SELECT recording_mbid, artist_mbid, artist_name,
                  previous_artist_mbid, previous_artist_name, previous_artist_weight,
                  decided_at, {origin_expr}
             FROM artist_reviews WHERE applied_at IS NOT NULL"""):
        changes.append({
            "kind": "artist_review",
            "anchor": {"recording_mbid": recording_mbid},
            "baseline": {"artist_mbid": previous_artist_mbid, "artist_name": previous_artist_name,
                         "weight": previous_artist_weight},
            "target": {"artist_mbid": artist_mbid, "artist_name": artist_name},
            "decided_at": decided_at,
            "origin": origin or hostname,
            "label": label_for_recording(conn, recording_mbid),
        })
    return changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    # Read-only across both halves; catalogue as `main`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY)
    hostname = socket.gethostname()
    have = lempi_db.tables(conn)  # both halves, not just `main` [IMPL-DBSPLIT-025]

    changes = []
    by_kind = {}
    if "id_reviews" in have:
        by_kind["id_review"] = export_id_reviews(conn, hostname)
    if "boundary_reviews" in have:
        by_kind["boundary_review"] = export_boundary_reviews(conn, hostname)
    if "artist_reviews" in have:
        by_kind["artist_review"] = export_artist_reviews(conn, hostname)
    for kind_changes in by_kind.values():
        changes.extend(kind_changes)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"format_version": 1, "changes": changes}, f, indent=2)

    say(f"{len(changes)} applied change(s) exported to {args.out}")
    for kind, kind_changes in by_kind.items():
        say(f"  {kind}: {len(kind_changes)}")
    if not changes:
        say("nothing to sync yet -- no applied review decisions found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
