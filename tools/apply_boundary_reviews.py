#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fold reviewed boundary edits into the library `[REQ-LIB-175]`, `[SPEC021 §5]`.

The waveform editor records a draft and changes nothing else. This is the
step that acts on it -- deliberately separate, run by hand, and a rehearsal
by default, for the same reason `tools/apply_reviews.py` is: an edit changes
what a passage *is*, and the library is Vipunen's to write, not a web click's.

    python tools/apply_boundary_reviews.py data/library.db            REHEARSE
    python tools/apply_boundary_reviews.py data/library.db --commit   do it

No `--revert`. Unlike a recording reassignment, the automatic values an edit
overrides are always recoverable by re-running the amplitude/segmentation
pass that produced them `[SPEC021 §2]` -- there is nothing here to restore
from `previous_*` columns, because there are none; re-deriving is that
pass's job, not this tool's.
"""

import argparse
import json
import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--commit", action="store_true")
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
    if "boundary_reviews" not in have:
        say("no boundary edits recorded yet")
        if args.json:
            say(json.dumps({"pending": 0, "applied": 0, "span_moved": 0,
                            "cache_dropped": 0, "skipped": 0, "committed": args.commit}))
        return 1

    # `passages.fade_*` `[SPEC-SUI-226]` is a deliberate, separate migration
    # (`tools/add_fade_columns.py`), not something Lempi's own Rust
    # schema-ensure adds -- unlike `boundary_reviews` itself, which every
    # `vipunen-support` Lempi brings up to date on its own. A clear refusal
    # here beats the raw "no such column: p.fade_in_ms" the SELECT below
    # would otherwise fail with.
    passages_cols = {r[1] for r in conn.execute("PRAGMA table_info(passages)")}
    if "fade_in_ms" not in passages_cols:
        say("passages is missing the fade columns [SPEC-SUI-226] -- run "
            "tools/add_fade_columns.py --write against this database first")
        if args.json:
            say(json.dumps({"error": "passages is missing the fade columns"}))
        return 1

    # `b.fade_*` falls back to the passage's own current fade `[SPEC-SUI-226]`
    # only for a draft recorded before this column existed -- every draft
    # `record_boundary_review` writes going forward always carries a fade,
    # since it is a required part of the post, not an optional one like
    # lead/gain.
    pending = conn.execute(
        """SELECT b.passage_id, b.start_ms, b.end_ms, b.lead_in_ms, b.lead_out_ms,
                  b.gain_db, COALESCE(b.fade_in_ms, p.fade_in_ms),
                  COALESCE(b.fade_out_ms, p.fade_out_ms),
                  COALESCE(b.fade_in_curve, p.fade_in_curve),
                  COALESCE(b.fade_out_curve, p.fade_out_curve),
                  p.file_id, p.kind, p.start_ms, p.end_ms,
                  p.lead_in_ms, p.lead_out_ms, p.gain_db,
                  p.fade_in_ms, p.fade_out_ms, p.fade_in_curve, p.fade_out_curve,
                  f.audio_md5
             FROM boundary_reviews b
             JOIN passages p ON p.passage_id = b.passage_id
             JOIN files f ON f.file_id = p.file_id
            WHERE b.applied_at IS NULL
            ORDER BY b.passage_id""").fetchall()

    to_stamp: list[int] = []
    say(f"{len(pending)} boundary edit(s) to apply")
    if not pending:
        if args.json:
            say(json.dumps({"pending": 0, "applied": 0, "span_moved": 0,
                            "cache_dropped": 0, "skipped": 0, "committed": args.commit}))
        return 0

    applied = span_moved = cache_dropped = 0
    skipped: list[tuple[int, str]] = []
    if args.commit:
        conn.execute("BEGIN IMMEDIATE")

    for (passage_id, new_start, new_end, new_lead_in, new_lead_out, new_gain,
         new_fade_in, new_fade_out, new_fade_in_curve, new_fade_out_curve,
         file_id, kind, old_start, old_end, old_lead_in, old_lead_out,
         old_gain, old_fade_in, old_fade_out, old_fade_in_curve, old_fade_out_curve,
         audio_md5) in pending:

        moved = (new_start, new_end) != (old_start, old_end)
        say(f"  passage {passage_id}: {old_start}-{old_end} -> {new_start}-{new_end}"
            + ("" if moved else " (leads/gain/fade only)")
            + f", lead-in {old_lead_in}->{new_lead_in}, "
              f"lead-out {old_lead_out}->{new_lead_out}, gain {old_gain}->{new_gain}, "
              f"fade-in {old_fade_in}ms {old_fade_in_curve}->{new_fade_in}ms {new_fade_in_curve}, "
              f"fade-out {old_fade_out}ms {old_fade_out_curve}->{new_fade_out}ms {new_fade_out_curve}")

        # `passages_span` is UNIQUE on (file_id, kind, start_ms, end_ms) --
        # checked before writing, in both modes, so a rehearsal's count is the
        # count a real run would actually apply.
        if moved and conn.execute(
            """SELECT 1 FROM passages
                WHERE file_id = ?1 AND kind = ?2 AND start_ms = ?3 AND end_ms = ?4
                  AND passage_id != ?5""",
            (file_id, kind, new_start, new_end, passage_id)).fetchone():
            skipped.append((passage_id, "would collide with another passage's span on this file"))
            continue

        if args.commit:
            conn.execute(
                """UPDATE passages
                      SET start_ms = ?1, end_ms = ?2, lead_in_ms = ?3,
                          lead_out_ms = ?4, gain_db = ?5,
                          fade_in_ms = ?6, fade_out_ms = ?7,
                          fade_in_curve = ?8, fade_out_curve = ?9,
                          boundary_src = 'manual'
                    WHERE passage_id = ?10""",
                (new_start, new_end, new_lead_in, new_lead_out, new_gain,
                 new_fade_in, new_fade_out, new_fade_in_curve, new_fade_out_curve,
                 passage_id))

            if moved:
                # The old span's cached features describe audio that nothing
                # plays anymore. Deleted, not re-keyed, when no other passage
                # still uses that exact span `[SPEC021 §5]`: re-keying would
                # relabel features extracted for the OLD span as valid for the
                # new one, and a wrong answer that looks like a right one is
                # worse than an honest gap a later pass fills in.
                still_used = conn.execute(
                    """SELECT 1 FROM passages p2 JOIN files f2 ON f2.file_id = p2.file_id
                        WHERE f2.audio_md5 = ?1 AND p2.start_ms = ?2 AND p2.end_ms = ?3
                          AND p2.passage_id != ?4""",
                    (audio_md5, old_start, old_end, passage_id)).fetchone()
                if not still_used:
                    cache_dropped += conn.execute(
                        """DELETE FROM lowlevel_cache
                            WHERE audio_md5 = ?1 AND start_ms = ?2 AND end_ms = ?3""",
                        (audio_md5, old_start, old_end)).rowcount

            # Stamped so the decision is known to have reached the library.
            # `boundary_reviews`'s own page refuses to touch this row once it
            # is set, for the same reason `id_reviews.applied_at` does.
            #
            # **Collected, not written here** -- see the two-phase commit
            # below. The stamp is a listener-side write and the edit above is
            # a catalogue one, and on a split installation those are two
            # separate files `[IMPL-DBSPLIT-025]`.
            to_stamp.append(passage_id)
            if moved:
                span_moved += 1
        applied += 1

    if skipped:
        say(f"\n{len(skipped)} refused: the new span collides with another "
            f"passage on the same file")
        for passage_id, reason in skipped[:10]:
            say(f"  passage {passage_id}: {reason}")

    if args.commit:
        # **Two commits, catalogue first, and the order is the whole point.**
        # SQLite documents a transaction spanning attached databases as
        # atomic only when the journal mode is not WAL, and both halves of a
        # split made from this database are WAL. So one `commit()` across the
        # two is not indivisible, and the question becomes which half is
        # safer to have landed on its own.
        #
        # Catalogue first: the edit is applied but not stamped, so a re-run
        # finds it still pending and applies it again -- harmless, because
        # `moved` is computed against the *live* passage row, so the second
        # pass sees old == new, skips the `lowlevel_cache` delete, and
        # rewrites identical values. Read that path to confirm it rather than
        # assuming it.
        #
        # The other order loses work silently: stamped applied while the edit
        # never landed closes the review forever over a passage nothing
        # changed, and nothing would ever say so.
        #
        # Unsplit, both commits go to one file and this is simply two
        # transactions where there was one.
        conn.commit()
        for passage_id in to_stamp:
            conn.execute(
                "UPDATE boundary_reviews SET applied_at = datetime('now') WHERE passage_id = ?1",
                (passage_id,))
        conn.commit()
        say(f"\napplied {applied}; {span_moved} moved a span, "
            f"dropped {cache_dropped} stale lowlevel_cache row(s)")
    else:
        say(f"\nwould apply {applied}"
            + (f", refusing {len(skipped)}" if skipped else ""))
        say("nothing was written. Re-run with --commit to do it.")
    if args.json:
        say(json.dumps({"pending": len(pending), "applied": applied, "span_moved": span_moved,
                        "cache_dropped": cache_dropped, "skipped": len(skipped),
                        "committed": args.commit}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
