#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fold reviewed id decisions into the library `[REQ-LIB-165]`.

The review page records judgements and changes nothing else. This is the step
that acts on them, and it is deliberately separate, run by hand, and a rehearsal
by default -- for the same reason `restore_listener` is.

Reassigning a recording id changes what a passage *is*. Play history is keyed by
recording, so re-pointing a passage silently re-attributes every past play of
it, and the naming, the rotation and the "played 12 times" all move with it.
That is a migration. Migrations belong to Vipunen and to a moment someone chose,
not to a web click.

    python tools/apply_reviews.py data/library.db            REHEARSE
    python tools/apply_reviews.py data/library.db --commit   do it

The numbers a rehearsal prints are the ones a real run produces: both are
measured from the same queries before anything is written.

Extended, not duplicated, with a second, independent decision `[SPEC-SUI-197]`:
a recording id can be exactly right while its credited artist is wrong, which
today's fingerprint check has no way to notice. Every run folds in pending
artist corrections too, whether or not there is a recording reassignment
pending -- a different table (`recording_artists`), a different key, and a
passage whose recording was never in question can still have one waiting.
`--revert-artist RECORDING_MBID` undoes an applied one the same way `--revert`
undoes a reassignment -- keyed by the recording, not a passage, since the
credit belongs to the recording and a passage is only ever how the
correction was reached.
"""

import argparse
import json
import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]

# Where a link that came from a person's judgement is marked, as against the
# `inherited:mulib` that every other row in this table carries.
SOURCE = "review:acoustid"


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def cached_names(conn: sqlite3.Connection, passage_id: int) -> dict:
    """Titles and artists for the recordings AcoustID named for this passage.

    Read back out of the cached response rather than re-queried: the answer is
    already on disk, and a tool that needs the network to apply a decision
    someone already made is a tool that fails at the wrong moment.
    """
    row = conn.execute(
        """SELECT c.response FROM identification_cache c
             JOIN passages p ON p.passage_id = ?1
             JOIN files f ON f.file_id = p.file_id AND f.audio_md5 = c.audio_md5
            WHERE c.service = 'acoustid'
              AND c.request_key LIKE 'chromaprint:' || p.start_ms || '-' || p.end_ms || '%'
            LIMIT 1""",
        (passage_id,),
    ).fetchone()
    if not row or not row[0]:
        return {}
    blob = row[0].decode() if isinstance(row[0], bytes) else row[0]
    try:
        results = json.loads(blob)
    except json.JSONDecodeError:
        return {}
    out = {}
    for r in results:
        for rec in r.get("recordings") or []:
            if rec.get("id"):
                out[rec["id"]] = {
                    "title": rec.get("title"),
                    "artists": [(a.get("id"), a.get("name"))
                                for a in rec.get("artists") or [] if a.get("id")],
                }
    return out


def revert(conn: sqlite3.Connection, passage_id: int, commit: bool) -> int:
    """Put back what a reassignment replaced `[REQ-LIB-165]`.

    The page can withdraw a decision that was only recorded; it refuses once
    the decision has been applied, because deleting the row would leave the
    library changed with nothing left saying what it used to be or why. This
    is that other half: restore `previous_mbid`, then clear the record, in one
    transaction so it cannot half-happen.
    """
    row = conn.execute(
        "SELECT decision, chosen_mbid, previous_mbid, applied_at "
        "  FROM id_reviews WHERE passage_id = ?1", (passage_id,)).fetchone()
    if not row:
        say(f"passage {passage_id}: no decision recorded")
        return 1
    decision, chosen, previous, applied_at = row
    if decision != "reassigned" or not applied_at:
        # Nothing was written, so there is nothing to put back -- and the page
        # can withdraw this one itself.
        say(f"passage {passage_id}: '{decision}'"
            + ("" if applied_at else ", never applied")
            + " -- withdraw it on the review page instead")
        return 1
    if not previous:
        say(f"passage {passage_id}: no previous id was recorded, so there is "
            f"nothing to restore. The current id is {chosen}.")
        return 1

    say(f"passage {passage_id}: {chosen} -> {previous} (restoring)")
    if not commit:
        say("\nnothing was written. Re-run with --commit to do it.")
        return 0
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM passage_recordings WHERE passage_id = ?1", (passage_id,))
    conn.execute(
        "INSERT INTO passage_recordings (passage_id, mbid, weight, source) "
        "VALUES (?1, ?2, 1.0, 'inherited:mulib')", (passage_id, previous))
    conn.execute("DELETE FROM id_reviews WHERE passage_id = ?1", (passage_id,))
    conn.commit()
    say("reverted; the passage is back in the review queue")
    return 0


def revert_artist(conn: sqlite3.Connection, recording_mbid: str, commit: bool) -> int:
    """Put back the artist credit an applied correction replaced `[SPEC-SUI-197]`.

    Keyed by `recording_mbid`, not a passage: the credit belongs to the
    recording, and a passage is only ever how the correction was reached.

    Unlike a recording reassignment, `artist_reviews` carries the ONE previous
    credit `record_artist_review` captured at decision time -- the heaviest
    `recording_artists` row for that recording, or nothing if it had none.
    That is what gets restored; a recording could in principle carry more
    than one credited artist, but this correction only ever replaced one.
    """
    row = conn.execute(
        "SELECT artist_mbid, previous_artist_mbid, "
        "       previous_artist_name, previous_artist_weight, applied_at "
        "  FROM artist_reviews WHERE recording_mbid = ?1", (recording_mbid,)).fetchone()
    if not row:
        say(f"recording {recording_mbid}: no artist correction recorded")
        return 1
    artist_mbid, prev_mbid, prev_name, prev_weight, applied_at = row
    if not applied_at:
        say(f"recording {recording_mbid}: not yet applied -- withdraw it on the review page instead")
        return 1

    if prev_mbid:
        say(f"recording {recording_mbid}: credit {artist_mbid} -> {prev_mbid} ({prev_name}) (restoring)")
    else:
        say(f"recording {recording_mbid}: credit {artist_mbid} -> no credit at all (restoring)")
    if not commit:
        say("\nnothing was written. Re-run with --commit to do it.")
        return 0
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM recording_artists WHERE mbid = ?1", (recording_mbid,))
    if prev_mbid:
        # `inherited:mulib` on the way back, same as `revert()` above uses for
        # a reassignment -- the row's own original `source` was never
        # captured, only what it named, so this is a re-tagging, not a claim
        # about where the credit first came from.
        conn.execute(
            "INSERT OR IGNORE INTO artists (mbid, name, source) VALUES (?1, ?2, 'inherited:mulib')",
            (prev_mbid, prev_name))
        conn.execute(
            "INSERT INTO recording_artists (mbid, artist_mbid, weight, source) "
            "VALUES (?1, ?2, ?3, 'inherited:mulib')", (recording_mbid, prev_mbid, prev_weight))
    conn.execute("DELETE FROM artist_reviews WHERE recording_mbid = ?1", (recording_mbid,))
    conn.commit()
    say("reverted")
    return 0


def apply_artist_corrections(conn: sqlite3.Connection, commit: bool) -> int:
    """Fold accepted artist corrections into `recording_artists` `[SPEC-SUI-197]`.

    Independent of the recording reassignment loop in `main` -- a different
    table, a different key, and a passage whose recording id was never in
    question can still have a pending correction here.
    """
    pending = conn.execute(
        """SELECT recording_mbid, artist_mbid, artist_name
             FROM artist_reviews WHERE applied_at IS NULL
            ORDER BY recording_mbid""").fetchall()
    say(f"{len(pending)} artist correction(s) to apply")
    if not pending:
        return 0

    if commit:
        conn.execute("BEGIN IMMEDIATE")
    for recording_mbid, artist_mbid, artist_name in pending:
        say(f"  recording {recording_mbid} -> credited to {artist_name}")
        if commit:
            conn.execute(
                "INSERT OR IGNORE INTO artists (mbid, name, source) VALUES (?1, ?2, ?3)",
                (artist_mbid, artist_name, SOURCE))
            # Replace the credit rather than adding to it: this is a
            # correction, not a second correct answer sitting beside a wrong
            # one -- the same reasoning the recording reassignment above
            # applies to `passage_recordings`.
            conn.execute("DELETE FROM recording_artists WHERE mbid = ?1", (recording_mbid,))
            conn.execute(
                "INSERT INTO recording_artists (mbid, artist_mbid, weight, source) "
                "VALUES (?1, ?2, 1.0, ?3)", (recording_mbid, artist_mbid, SOURCE))
            conn.execute(
                "UPDATE artist_reviews SET applied_at = datetime('now') WHERE recording_mbid = ?1",
                (recording_mbid,))
    if commit:
        conn.commit()
        say(f"applied {len(pending)} artist correction(s)")
    else:
        say("nothing was written. Re-run with --commit to do it.")
    return len(pending)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--revert", type=int, metavar="PASSAGE_ID",
                    help="undo an applied reassignment and re-open it for review")
    ap.add_argument("--revert-artist", metavar="RECORDING_MBID",
                    help="undo an applied artist-credit correction `[SPEC-SUI-197]`")
    ap.add_argument("--json", action="store_true",
                     help="also print one final JSON summary line, for a caller "
                          "(the Vipunen console's apply-reviews job) rather than a person")
    args = ap.parse_args()

    # Writes the catalogue (passages/recordings/artists) and stamps a
    # listener-side review table to say the decision landed. Catalogue
    # as `main` because that is what it creates and rewrites;
    # `peer_writable` because the stamp is a genuine second-half write,
    # and saying so at the call site is the point `[IMPL-DBSPLIT-025]`.
    conn = lempi_db.connect(args.db, lempi_db.ROLE_LIBRARY,
                            writable=True, peer_writable=True, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA foreign_keys = ON")

    have = lempi_db.tables(conn)  # both halves, not just `main` [IMPL-DBSPLIT-025]
    if "id_reviews" not in have:
        say("no reviews recorded yet")
        if args.json:
            say(json.dumps({"applied": 0, "artist_applied": 0, "committed": args.commit}))
        return 1

    if args.revert is not None:
        return revert(conn, args.revert, args.commit)
    if args.revert_artist is not None:
        if "artist_reviews" not in have:
            say("no artist corrections recorded yet")
            return 1
        return revert_artist(conn, args.revert_artist, args.commit)

    pending = conn.execute(
        """SELECT r.passage_id, r.chosen_mbid, pr.mbid, r.chosen_release_mbid
             FROM id_reviews r
             JOIN passage_recordings pr ON pr.passage_id = r.passage_id
            WHERE r.decision = 'reassigned' AND r.chosen_mbid IS NOT NULL
              AND r.applied_at IS NULL
              AND pr.mbid <> r.chosen_mbid
            ORDER BY r.passage_id""").fetchall()

    kept = conn.execute(
        "SELECT COUNT(*) FROM id_reviews WHERE decision = 'kept'").fetchone()[0]
    deferred = conn.execute(
        "SELECT COUNT(*) FROM id_reviews WHERE decision = 'deferred'").fetchone()[0]

    say(f"{len(pending)} reassignment(s) to apply; "
        f"{kept} kept as they were; {deferred} deferred")

    applied = new_recordings = new_artists = unnamed = albums_set = 0
    skipped: list[tuple[int, str]] = []
    if args.commit and pending:
        conn.execute("BEGIN IMMEDIATE")

    for passage_id, new_mbid, old_mbid, release_mbid in pending:
        names = cached_names(conn, passage_id)
        info = names.get(new_mbid)
        title = (info or {}).get("title")

        # `recordings.title` and `recordings.source` are both NOT NULL, and
        # `passage_recordings.mbid` has a foreign key to it. So a reassignment
        # to a recording we can put no name to cannot be made at all -- and it
        # must be REFUSED rather than papered over with a placeholder title,
        # which would put a nameless row in the library and display as blank.
        #
        # The first version of this used INSERT OR IGNORE and omitted `source`,
        # so every insert was silently skipped and the foreign key then failed
        # on the row after. `OR IGNORE` turns a constraint violation into
        # nothing happening, which is exactly what you do not want when the
        # next statement depends on it having happened.
        if title is None:
            unnamed += 1
            skipped.append((passage_id, new_mbid))
            continue

        if args.commit:
            existed = conn.execute(
                "SELECT 1 FROM recordings WHERE mbid = ?1", (new_mbid,)).fetchone()
            if not existed:
                conn.execute(
                    "INSERT INTO recordings (mbid, title, source) VALUES (?1, ?2, ?3)",
                    (new_mbid, title, SOURCE))
                new_recordings += 1
            for artist_mbid, artist_name in (info or {}).get("artists", []):
                if not artist_name:
                    continue        # `artists.name` is NOT NULL too
                conn.execute(
                    "INSERT OR IGNORE INTO artists (mbid, name, source) VALUES (?1, ?2, ?3)",
                    (artist_mbid, artist_name, SOURCE))
                new_artists += conn.execute(
                    "INSERT OR IGNORE INTO recording_artists (mbid, artist_mbid, weight, source) "
                    "VALUES (?1, ?2, 1.0, ?3)", (new_mbid, artist_mbid, SOURCE)).rowcount
            # Replace the link rather than adding one: a passage with two
            # recordings is a medley, and this is not that -- it is one song
            # whose identity was wrong.
            conn.execute(
                "DELETE FROM passage_recordings WHERE passage_id = ?1", (passage_id,))
            conn.execute(
                "INSERT INTO passage_recordings (passage_id, mbid, weight, source) "
                "VALUES (?1, ?2, 1.0, ?3)", (passage_id, new_mbid, SOURCE))

            # The preferred album, if one was named. `ALBUM_EXPR` orders by
            # `chosen DESC` then release date, so marking one release chosen
            # is how a person's answer beats the by-date guess. Exactly one
            # per recording, hence clearing the others first.
            if release_mbid:
                conn.execute(
                    "UPDATE release_recordings SET chosen = 0 WHERE mbid = ?1",
                    (new_mbid,))
                if conn.execute(
                    "UPDATE release_recordings SET chosen = 1 "
                    " WHERE mbid = ?1 AND release_mbid = ?2",
                    (new_mbid, release_mbid)).rowcount:
                    albums_set += 1
                else:
                    say(f"    (release {release_mbid} is not linked to this "
                        f"recording; album left as it was)")

            # Stamped so the decision is known to have reached the library.
            # Undo on the page refuses once this is set: withdrawing the record
            # would strand the change with nothing saying why it was made.
            conn.execute(
                "UPDATE id_reviews SET applied_at = datetime('now') WHERE passage_id = ?1",
                (passage_id,))
        applied += 1
        if applied <= 10:
            say(f"  passage {passage_id}: {old_mbid} -> {new_mbid}  ({title})")

    if skipped:
        say(f"\n{len(skipped)} refused: no cached name for the chosen recording, "
            f"and a nameless one cannot be written")
        for passage_id, mbid in skipped[:10]:
            say(f"  passage {passage_id} -> {mbid}")
        say("  Re-run tools/fingerprint_ids.py for these passages, or pick a "
            "different candidate on the review page.")

    if args.commit:
        conn.commit()
        say(f"\napplied {applied}; added {new_recordings} recording(s), "
            f"{new_artists} artist link(s), set {albums_set} preferred album(s)")
        say("Play history keyed to the OLD recording stays with the old id: "
            "those plays did happen, and to what the passage was then.")
    else:
        say(f"\nwould apply {applied}"
            + (f", refusing {unnamed}" if unnamed else ""))
        say("nothing was written. Re-run with --commit to do it.")

    # Independent of everything above `[SPEC-SUI-197]`: a different table, a
    # different key, and a passage's recording id being fine is exactly the
    # case this exists for.
    artist_count = 0
    if "artist_reviews" in have:
        say("")
        artist_count = apply_artist_corrections(conn, args.commit)
    if args.json:
        say(json.dumps({"applied": applied, "artist_applied": artist_count,
                        "committed": args.commit}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
