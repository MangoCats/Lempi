# SPDX-License-Identifier: AGPL-3.0-or-later
"""Load MuLibPlay's four seasonal curves as data `[SPEC-DIR-134]`.

MuLibPlay hardcoded `[C]`, `[W]`, `[S]` and `[K]` into a `switch`, which is why
it had exactly four. Lempi reads curves from `listener_occasions` and
`listener_occasion_points`, so these are rows, and a fifth occasion needs no
code `[SPEC-DIR-130]`.

The values below are transcribed from `occasionWeight()` in the inherited
`musicdirector.cpp`. The library already carries the characteristic values —
41 christmasy recordings, 140 for_children, and a handful of wintry/summery —
inherited from six years of tagging, so loading the curves is what makes that
tagging act again.

Each row also carries the name the preference panel offers it under
`[SPEC-PREF-080]`. The label tracks the characteristic rather than departing
from it -- "Christmas" for `user.christmas` -- so that a person reading the
panel and a person reading `listener_occasions` are looking at the same word.
The column exists because the two *can* differ ("Children's" for
`user.childrens`), not because they should.

Usage:
  python tools/load_occasions.py <listener.db> [--write] [--kids WEIGHT]
                                            [--profanity WEIGHT] [--spiritual WEIGHT]
                                            [--library library.db]

`--library` is for a split installation `[IMPL-DBSPLIT-025]`: the curves are
written to the listener database given first, while the "reach" count that
reports how many passages each occasion touches is read from the catalogue.
Omitted, the one database given is both.
"""

from __future__ import annotations

import sqlite3
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
from pathlib import Path

# MuLibPlay's `kidSongWeight`, whose shipped default is 0.000001 — an effective
# ban rather than a de-emphasis. It reaches 149 radio passages here, so it is a
# parameter rather than a constant: `--kids 0.5` merely damps them.
KIDS_DEFAULT = 0.000001

# `[C]` — a formula in the original, sampled here at the points where its shape
# changes. November climbs as (25/days)^3; December as 5/sqrt(days); the 25th
# spikes to 10 and the tail decays as -1/days. Interpolated in log space, which
# is what `[SPEC-DIR-132]` specifies for ratios.
CHRISTMAS = [
    (1, 1, 0.000001), (10, 31, 0.000001),
    (11, 1, 0.0992), (11, 15, 0.2441), (11, 30, 1.0),
    (12, 10, 1.2910), (12, 20, 2.2361), (12, 24, 5.0), (12, 25, 10.0),
    (12, 26, 1.0), (12, 31, 0.1667),
]

# `[W]` and `[S]` were whole-month constants, so they are step curves.
WINTER = [(1, 1, 1.5), (2, 1, 1.0), (3, 1, 0.25), (4, 1, 0.000001),
          (11, 1, 0.5), (12, 1, 2.0)]
SUMMER = [(1, 1, 0.2), (5, 1, 0.5), (6, 1, 2.0), (7, 1, 1.5), (8, 1, 1.0),
          (9, 1, 0.2)]


# MuLibPlay's `profanity` was a 0-1000 slider on its own track editor, beside
# rotation/recovery/restraint -- and nothing in `musicdirector.cpp` ever read
# it. It arrives here as a one-point curve at exactly 1.0, which is that same
# behaviour stated rather than inherited by accident: recorded, editable, and
# changing nothing about selection until someone chooses a number.
# `--profanity 0.25` is then the whole of "play the explicit ones a quarter as
# often" -- no code, which is the test `[SPEC-DIR-134]` sets for a curve being
# data.
#
# `migrate_mulib.py` lists it in `DEAD_FIELDS`, so nothing is inherited for it
# today -- but that listing is wrong about this field, and was until audited on
# 2026-09-10: 69 MuLibPlay tracks carry a real non-zero profanity value. Until
# those are backfilled the slider starts from nothing on every recording
# `[SPEC-PREF-082]`.
PROFANITY_DEFAULT = 1.0

# `user.spiritual` has no MuLibPlay ancestor at all -- it is the first special
# defined here rather than inherited, and the test of whether that is really
# only rows `[SPEC-PREF-082]`. Registered the same way profanity is: a
# one-point curve at 1.0, neutral until someone chooses otherwise.
#
# Every existing recording reads 0.0 for it, and does so by carrying **no row
# at all** rather than 8,116 explicit zeros `[SPEC-PREF-083]`. An absent value
# already reads as 0.0 everywhere it is consulted -- `occasion.rs`'s multiplier
# is `1 + value x (curve - 1)`, so 0.0 ignores the curve exactly -- and writing
# the zeros would cost a row per recording while destroying the one distinction
# the panel is built on: "nobody has an opinion" against "somebody said no".
SPIRITUAL_DEFAULT = 1.0


def curves(kids: float, profanity: float, spiritual: float) -> list[tuple[str, str, str, str, list]]:
    return [
        ("user.christmas", "christmasy", "Christmas", "linear", CHRISTMAS),
        ("user.winter", "wintry", "Winter", "step", WINTER),
        ("user.summer", "summery", "Summer", "step", SUMMER),
        # `[K]` was never seasonal — a single flat multiplier all year. It
        # expresses as a one-point curve, which is a fair test of whether
        # "curves are data" actually holds `[SPEC-DIR-134]`.
        ("user.childrens", "for_children", "Children's", "step", [(1, 1, kids)]),
        ("user.profanity", "profane", "Profanity", "step", [(1, 1, profanity)]),
        ("user.spiritual", "spiritual", "Spiritual", "step", [(1, 1, spiritual)]),
    ]


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    db = Path(args[0])
    write = "--write" in args
    library = Path(args[args.index("--library") + 1]) if "--library" in args else db
    kids = float(args[args.index("--kids") + 1]) if "--kids" in args else KIDS_DEFAULT
    profanity = (float(args[args.index("--profanity") + 1]) if "--profanity" in args
                 else PROFANITY_DEFAULT)
    spiritual = (float(args[args.index("--spiritual") + 1]) if "--spiritual" in args
                 else SPIRITUAL_DEFAULT)

    # Writes the occasion registry, which is listener-side, and counts
    # reach over `passages`/`flavor`, which are not. One connection sees
    # both `[IMPL-DBSPLIT-025]`; `--library` still names the catalogue
    # explicitly for an installation whose halves are not siblings, which
    # is how lempipi is laid out.
    con = lempi_db.connect(db, lempi_db.ROLE_LISTENER, writable=True,
                           peer=(str(library) if library != db else None))
    reach = con
    # The label column post-dates the table `[SPEC-PREF-080]`. The player adds
    # it too, at startup, but this tool has to be able to run against a
    # database the player has not opened since.
    try:
        con.execute("ALTER TABLE listener_occasions ADD COLUMN label TEXT")
    except sqlite3.OperationalError:
        pass  # already there, which is every run after the first
    print(f"{'occasion':<22} {'class':<16} {'label':<12} {'interp':<7} {'points':>6}  reach")
    for ch, cl, label, interp, pts in curves(kids, profanity, spiritual):
        n = reach.execute(
            "SELECT COUNT(*) FROM passages p JOIN passage_recordings pr USING (passage_id) "
            "JOIN flavor f ON f.subject_id = pr.mbid AND f.characteristic = ? "
            "AND f.class = ? AND f.value > 0 WHERE p.kind = 'radio'",
            (ch, cl),
        ).fetchone()[0]
        print(f"{ch:<22} {cl:<16} {label:<12} {interp:<7} {len(pts):>6}  {n} radio passages")

    if not write:
        print("\n(dry run -- pass --write to store)")
        return 0

    for ch, cl, label, interp, pts in curves(kids, profanity, spiritual):
        con.execute("INSERT OR REPLACE INTO listener_occasions "
                    "(characteristic,class,interp,label) VALUES (?,?,?,?)",
                    (ch, cl, interp, label))
        con.execute("DELETE FROM listener_occasion_points WHERE characteristic=? AND class=?",
                    (ch, cl))
        for m, d, v in pts:
            con.execute("INSERT INTO listener_occasion_points VALUES (?,?,?,?,?)",
                        (ch, cl, m, d, v))
    con.commit()
    print(f"\nloaded {len(curves(kids, profanity, spiritual))} curves, "
          f"kids weight {kids}, profanity weight {profanity}, "
          f"spiritual weight {spiritual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
