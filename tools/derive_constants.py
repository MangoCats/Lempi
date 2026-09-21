# SPDX-License-Identifier: AGPL-3.0-or-later
"""Re-derive the flavor constants on locally extracted values `[SPEC-FD-090]`.

`β_c` and `w_c` are properties of *a corpus scored by a particular pipeline*,
not universal. The constants in use were measured on AcousticBrainz dump values
`[SPEC-FD-052]`; applying them to locally extracted values scales every
characteristic by a figure from a different corpus, which is the caveat
blocking `[SPEC-FD-083]`.

  β_c = mean between-recording total variation
  w_c = 1 − (mean within-recording TV) / (mean between-recording TV)

The within-recording term needs the same recording measured twice. The dump had
multiple submissions; locally we have **recordings that appear in more than one
passage** — an album track that also sits inside a compilation — extracted
independently from different files. That is the same test–retest structure, at
a smaller n.

Usage:
  python tools/derive_constants.py <listener.db> [--write] [--pairs N]
"""

from __future__ import annotations

import json
import random
import sqlite3
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lempi_db  # noqa: E402  -- split-aware open [IMPL-DBSPLIT-025]
import gaia_classify as gc  # noqa: E402

BETWEEN_PAIRS = 20_000


def tv(a: dict[str, float], b: dict[str, float]) -> float:
    """Total variation between two class distributions `[SPEC-FD-030]`."""
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in set(a) | set(b))


def load_flavor(con: sqlite3.Connection, source_like: str) -> dict[str, dict[str, dict[str, float]]]:
    out: dict[str, dict[str, dict[str, float]]] = {}
    for sid, ch, cl, v in con.execute(
        "SELECT subject_id, characteristic, class, value FROM flavor "
        "WHERE subject_kind='recording' AND source LIKE ?", (source_like,)
    ):
        out.setdefault(sid, {}).setdefault(ch, {})[cl] = v
    return out


def between(vecs: dict, pairs: int, rng: random.Random) -> dict[str, list[float]]:
    """Mean TV per characteristic over random distinct recordings."""
    ids = list(vecs)
    acc: dict[str, list[float]] = {}
    for _ in range(pairs):
        a, b = rng.sample(ids, 2)
        A, B = vecs[a], vecs[b]
        for ch in A.keys() & B.keys():
            acc.setdefault(ch, []).append(tv(A[ch], B[ch]))
    return acc


def within(con: sqlite3.Connection) -> dict[str, list[float]]:
    """Mean TV per characteristic between passages of the SAME recording.

    Re-classified from `lowlevel_cache` rather than read from `flavor`, because
    `flavor` keeps one row per recording — the duplicates overwrote each other,
    which is correct for playback and useless for reliability.
    """
    dupes = con.execute(
        "SELECT pr.mbid, lc.features FROM lowlevel_cache lc "
        "JOIN files f ON f.audio_md5 = lc.audio_md5 "
        "JOIN passages p ON p.file_id = f.file_id AND p.start_ms = lc.start_ms "
        "                AND p.end_ms = lc.end_ms "
        "JOIN passage_recordings pr USING (passage_id) "
        "WHERE p.kind = 'radio' AND pr.mbid IN ("
        "  SELECT pr2.mbid FROM lowlevel_cache lc2 "
        "  JOIN files f2 ON f2.audio_md5 = lc2.audio_md5 "
        "  JOIN passages p2 ON p2.file_id = f2.file_id AND p2.start_ms = lc2.start_ms "
        "                   AND p2.end_ms = lc2.end_ms "
        "  JOIN passage_recordings pr2 USING (passage_id) "
        "  WHERE p2.kind = 'radio' GROUP BY pr2.mbid HAVING COUNT(*) > 1)"
    ).fetchall()

    by_rec: dict[str, list[dict]] = {}
    for mbid, blob in dupes:
        by_rec.setdefault(mbid, []).append(json.loads(zlib.decompress(blob)))
    print(f"  {len(by_rec)} recordings with repeat measurements", flush=True)

    clf = gc.Classifier()
    acc: dict[str, list[float]] = {}
    for i, (mbid, docs) in enumerate(by_rec.items(), 1):
        vecs = [clf.classify(d) for d in docs]
        for j in range(len(vecs)):
            for k in range(j + 1, len(vecs)):
                for ch in vecs[j].keys() & vecs[k].keys():
                    acc.setdefault(ch, []).append(tv(vecs[j][ch], vecs[k][ch]))
        if i % 40 == 0:
            print(f"    {i}/{len(by_rec)}", flush=True)
    return acc


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    db = Path(args[0])
    write = "--write" in args
    pairs = int(args[args.index("--pairs") + 1]) if "--pairs" in args else BETWEEN_PAIRS

    # Catalogue-only, and it writes -- library half as `main`.
    con = lempi_db.connect(db, lempi_db.ROLE_LIBRARY, writable=True)
    vecs = load_flavor(con, "local:%")
    print(f"local flavor: {len(vecs)} recordings")
    rng = random.Random(11)

    print("between-recording distances...", flush=True)
    b = between(vecs, pairs, rng)
    print("within-recording distances...", flush=True)
    w = within(con)

    old = {r[0]: (r[1], r[2]) for r in
           con.execute("SELECT characteristic, beta, reliability FROM flavor_constants")}

    print(f"\n{'characteristic':<22} {'beta':>8} {'was':>8} {'w_c':>8} {'was':>8} {'n_within':>9}")
    rows = []
    for ch in sorted(b):
        beta = sum(b[ch]) / len(b[ch])
        wn = w.get(ch, [])
        rel = max(0.0, 1.0 - (sum(wn) / len(wn)) / beta) if wn and beta > 0 else None
        ob, orl = old.get(ch, (None, None))
        rows.append((ch, beta, rel, len(wn)))
        print(f"{ch:<22} {beta:>8.4f} {ob if ob is None else f'{ob:>8.4f}'} "
              f"{'   n/a  ' if rel is None else f'{rel:>8.4f}'} "
              f"{orl if orl is None else f'{orl:>8.4f}'} {len(wn):>9}")

    if write:
        for ch, beta, rel, _ in rows:
            if rel is None:
                continue
            con.execute(
                "INSERT OR REPLACE INTO flavor_constants VALUES (?,?,?,?,datetime('now'))",
                (ch, beta, rel, "local:essentia-2.1-beta2+gaia-beta1"),
            )
        con.commit()
        print(f"\nwrote {sum(1 for r in rows if r[2] is not None)} constants")
    else:
        print("\n(dry run -- pass --write to store)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
