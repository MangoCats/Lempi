# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repair `files.duration_ms` from the decoded length `[REQ-LIB-145]`.

`[SPEC-SC-030]` specifies `duration_ms` as **decoded, not header-claimed**, and
the migrated library violates it: 29.2% of files differ from their decoded
length by more than 5 s, and one overstates by 38.4 minutes `[GDE-FEX-106]`.

Not cosmetic. Segmentation used an inflated value to invent a **phantom
passage** in a tail that does not exist, and the player uses `duration_ms` for
lead-out timing. So this repairs the passages too:

  * `files.duration_ms`  → the decoded value, where it disagrees
  * `passages.end_ms`    → clamped to the real end, where it overruns
  * `lowlevel_cache`     → the orphaned row for the old span deleted, not
                            left pointing at audio nothing plays anymore
  * phantom passages     → reported; deleted only on explicit request

Repair only where it disagrees. Rewriting a correct value is churn, and the
`updated` counts below are the evidence of what was actually wrong.

**Second pass, 2026-08-30 — the first version of this tool could not see any
of this.** It probed with `ffprobe -show_entries format=duration`, the exact
metadata-estimate method that produces a wrong answer for a VBR file with no
valid Xing/Info header in the first place. Checked directly against the real
library: of 1,695 files wrong against an actual decode, ffprobe's own
re-check agreed with the (wrong) stored value in all 1,695 -- a tool that
compares a number against the method that generated it can never disagree
with it. Now uses `audio_duration.probe_duration_ms`, which actually decodes.

Usage:
  python tools/repair_durations.py <listener.db> [--write] [--delete-phantoms]
"""

from __future__ import annotations

import concurrent.futures as futures
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import audio_duration  # noqa: E402
import passage_orphans  # noqa: E402  -- [IMPL009 §7.7]'s cascade replacement

# Below this, a difference is rounding rather than error. MP3 frame duration is
# ~26 ms, so a second of slack is comfortably clear of encoder granularity.
TOLERANCE_MS = 1000


def probe(row: tuple) -> tuple[int, str, int, float | None]:
    fid, path, dur = row
    return fid, path, dur, audio_duration.probe_duration_ms(path)


def main() -> int:
    args = sys.argv[1:]
    if not args or not audio_duration.FFMPEG:
        print(__doc__ if args else "ffmpeg not found")
        return 2
    db = Path(args[0])
    write = "--write" in args
    delete_phantoms = "--delete-phantoms" in args

    # Catalogue-only, and it writes -- library half as `main`.
    con = lempi_db.connect(db, lempi_db.ROLE_LIBRARY, writable=True)
    files = con.execute("SELECT file_id, path, duration_ms FROM files").fetchall()
    print(f"probing {len(files)} files (real decode, not a header estimate)...", flush=True)

    real: dict[int, float] = {}
    unreadable = 0
    with futures.ThreadPoolExecutor(max_workers=12) as pool:
        for i, (fid, _p, _d, rv) in enumerate(pool.map(probe, files), 1):
            if rv is None:
                unreadable += 1
            else:
                real[fid] = rv
            if i % 1000 == 0:
                print(f"  {i}/{len(files)}", flush=True)

    wrong = [(fid, p, d) for fid, p, d in files
             if fid in real and abs(d - real[fid]) > TOLERANCE_MS]
    over = [x for x in wrong if x[2] - real[x[0]] > TOLERANCE_MS]
    print(f"\nfiles probed {len(real)}, unreadable {unreadable}")
    print(f"  duration wrong by >{TOLERANCE_MS} ms: {len(wrong)} ({100*len(wrong)/max(len(real),1):.1f}%)")
    print(f"    of which OVER-state the file: {len(over)}")
    if wrong:
        diffs = sorted(abs(d - real[fid]) / 1000 for fid, _p, d in wrong)
        print(f"    error seconds: median {diffs[len(diffs)//2]:.1f}  p95 "
              f"{diffs[int(len(diffs)*0.95)]:.1f}  max {diffs[-1]:.1f}")

    # Passages measured against a wrong duration. `audio_md5` travels with
    # each row too -- needed to find the `lowlevel_cache` entry an end_ms
    # clamp is about to orphan, keyed `(audio_md5, start_ms, end_ms)`
    # `[SPEC-SC-080]`, exactly as SPEC021 §5 already names for a boundary
    # edit and this tool never closed for its own.
    rows = con.execute(
        "SELECT p.passage_id, p.file_id, p.start_ms, p.end_ms, p.kind, f.audio_md5 "
        "FROM passages p JOIN files f USING (file_id)"
    ).fetchall()
    phantom = [r for r in rows if r[1] in real and r[2] >= real[r[1]] - TOLERANCE_MS]
    overrun = [r for r in rows if r[1] in real and r[2] < real[r[1]] - TOLERANCE_MS
               and r[3] > real[r[1]] + TOLERANCE_MS]
    print(f"\npassages {len(rows)}")
    print(f"  PHANTOM  (start at/past the real end, unplayable): {len(phantom)}")
    print(f"  overrun  (end past the real end, clamp to it):     {len(overrun)}")
    for pid, fid, s, e, kind, _md5 in phantom[:6]:
        print(f"     phantom {pid} [{kind}] {s/60000:.1f}-{e/60000:.1f} min, "
              f"real end {real[fid]/60000:.1f}")

    if not write:
        print("\n(dry run -- pass --write to apply)")
        return 0

    for fid, _p, _d in wrong:
        con.execute("UPDATE files SET duration_ms = ? WHERE file_id = ?",
                    (int(round(real[fid])), fid))
    # Catalogue-side, so `main` would answer correctly today -- asked across
    # both halves anyway, because "which half is main" is exactly the kind of
    # assumption that stops being true quietly `[IMPL-DBSPLIT-025]`.
    has_cache = lempi_db.has_table(con, "lowlevel_cache")
    orphaned_cache = 0
    for pid, fid, _s, e, _k, md5 in overrun:
        new_end = int(round(real[fid]))
        con.execute("UPDATE passages SET end_ms = ? WHERE passage_id = ?", (new_end, pid))
        if has_cache:
            cur = con.execute(
                "DELETE FROM lowlevel_cache WHERE audio_md5 = ? AND end_ms = ?", (md5, e))
            orphaned_cache += cur.rowcount
    print(f"\nupdated {len(wrong)} durations, clamped {len(overrun)} passage ends, "
          f"deleted {orphaned_cache} orphaned lowlevel_cache row(s)")

    if delete_phantoms and phantom:
        ids = [p[0] for p in phantom]
        q = ",".join("?" * len(ids))
        con.execute(f"DELETE FROM passage_recordings WHERE passage_id IN ({q})", ids)
        con.execute(f"DELETE FROM passages WHERE passage_id IN ({q})", ids)
        # This tool never set `PRAGMA foreign_keys = ON`, so the
        # `ON DELETE SET NULL` cascade on the three listener references to
        # `passages` has never fired here -- deleting a phantom passage has
        # been leaving orphaned ids behind in one file too, not only after a
        # split `[IMPL009 §7.7]`. Fixed for both shapes at once.
        cleared = passage_orphans.clear(con)
        if sum(cleared.values()):
            print("cleared orphaned listener references: " +
                  ", ".join(f"{t} {n}" for t, n in cleared.items() if n))
        print(f"deleted {len(ids)} phantom passages")
    elif phantom:
        print(f"{len(phantom)} phantom passages left in place "
              f"(pass --delete-phantoms to remove)")
    con.commit()

    bad = con.execute("SELECT COUNT(*) FROM passages WHERE end_ms <= start_ms").fetchone()[0]
    print(f"integrity: {con.execute('PRAGMA integrity_check').fetchone()[0]}, "
          f"passages violating end>start: {bad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
