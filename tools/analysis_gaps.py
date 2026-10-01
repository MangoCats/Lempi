#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the library has not analysed yet, by album folder -- the Jobs page's
*Not analysed* list, and the measure of what *Analyse what's missing* does.

Two analyses, each counted by the rule of the tool that fills it, so the list
names only what the button can clear:

- **flavor** -- `extract_library.py`: a radio passage with an identified
  recording and no `lowlevel_cache` row for its file and span. A passage with
  no recording is never extracted (its flavor hangs off the recording), so it
  is counted apart, as *not identified*;
- **amplitude** -- `analyze_amplitude.py`: a radio passage whose
  `lead_in_ms` is NULL. A boundary a person set (`boundary_src='manual'`) is
  never recomputed `[SPEC-SA-080]`, so it is counted apart, as *set by hand*.

`gain_db` is not here: no analyser writes it. It is a person's setting, from
the editor or a boundary review, and NULL means only that none was made.

    python tools/analysis_gaps.py data/library.db
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def folder_of(path: str) -> str:
    """The album folder of a library path -- `\\` or `/`, on whatever machine
    this runs, since library paths are Windows-real."""
    return re.sub(r"[\\/][^\\/]*$", "", path or "")


def missing(con: sqlite3.Connection) -> dict:
    """`{"folders": [...], "totals": {...}}`, newest folder first. Each folder:
    `folder`, `passages` (radio passages in it with anything missing),
    `flavor`, `amplitude` (what the button will do), `unidentified`,
    `manual` (what it cannot), and `first` -- a passage to open."""
    cached = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lowlevel_cache'").fetchone()
    has_flavor = ("EXISTS (SELECT 1 FROM lowlevel_cache c WHERE c.audio_md5=f.audio_md5 "
                  "AND c.start_ms=p.start_ms AND c.end_ms=p.end_ms)") if cached else "0"
    rows = con.execute(f"""
        SELECT p.passage_id, f.file_id, f.path,
               {has_flavor} AS flavored,
               EXISTS (SELECT 1 FROM passage_recordings pr WHERE pr.passage_id=p.passage_id) AS identified,
               p.lead_in_ms IS NULL AS no_amp,
               COALESCE(p.boundary_src = 'manual', 0) AS manual
          FROM passages p JOIN files f USING (file_id)
         WHERE p.kind = 'radio'""").fetchall()
    by: dict[str, dict] = {}
    for pid, file_id, path, flavored, identified, no_amp, manual in rows:
        flavor = not flavored and identified
        unidentified = not flavored and not identified
        amplitude = no_amp and not manual
        by_hand = no_amp and manual
        if not (flavor or unidentified or amplitude or by_hand):
            continue
        d = by.setdefault(folder_of(path), {
            "folder": folder_of(path), "passages": 0, "flavor": 0, "amplitude": 0,
            "unidentified": 0, "manual": 0, "first": pid, "newest": file_id})
        d["passages"] += 1
        d["flavor"] += flavor
        d["amplitude"] += amplitude
        d["unidentified"] += unidentified
        d["manual"] += by_hand
        d["first"] = min(d["first"], pid)
        d["newest"] = max(d["newest"], file_id)
    folders = sorted(by.values(), key=lambda d: -d["newest"])
    totals = {k: sum(d[k] for d in folders) for k in ("passages", "flavor", "amplitude", "unidentified", "manual")}
    totals["folders"] = len(folders)
    totals["fixable"] = sum(1 for d in folders if d["flavor"] or d["amplitude"])
    return {"folders": folders, "totals": totals}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    import lempi_db
    con = lempi_db.connect(sys.argv[1], lempi_db.ROLE_LIBRARY)
    got = missing(con)
    t = got["totals"]
    print(f"{t['folders']} folder(s): {t['flavor']} passage(s) without flavor, {t['amplitude']} without "
          f"amplitude; {t['unidentified']} not identified, {t['manual']} with a boundary set by hand")
    for d in got["folders"]:
        print(f"  {d['flavor']:>4} {d['amplitude']:>4} {d['unidentified']:>4} {d['manual']:>4}  {d['folder']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
