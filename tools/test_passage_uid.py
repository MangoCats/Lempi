#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `passage_uid.py` [IMPL-UID-032], and the vectors both languages check.

    python tools/test_passage_uid.py            check
    python tools/test_passage_uid.py --write    rewrite fixtures/passage_uid/vectors.json

`--write` is for a change to the inputs below, never to the encoding: a
vector whose uid changes is a different identity for every passage there is.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import passage_uid as pu  # noqa: E402

VECTORS = os.path.join(os.path.dirname(HERE), "fixtures", "passage_uid", "vectors.json")
FAILED = []

# Each is a case the encoding must keep apart or keep equal.
INPUTS = [
    ("an ordinary single track", "6e1f3c0a9b8d7e6f5a4b3c2d1e0f9a8b", "radio", 0, 200000, 0),
    ("the album cut of the same file", "6e1f3c0a9b8d7e6f5a4b3c2d1e0f9a8b", "album", 0, 200000, 0),
    ("a second passage born on that key", "6e1f3c0a9b8d7e6f5a4b3c2d1e0f9a8b", "radio", 0, 200000, 1),
    ("a cut inside a whole-album file", "f5500d078a0d9a6c63f41a9205a0402f", "radio", 4337680, 4602680, 0),
    ("a span past 2^31 ms", "f5500d078a0d9a6c63f41a9205a0402f", "album", 2147483000, 2147484000, 0),
    ("start and end swapped are another key", "6e1f3c0a9b8d7e6f5a4b3c2d1e0f9a8b", "radio", 200000, 0, 0),
    ("digits that would join if unseparated", "ab", "radio", 12, 3, 0),
    ("the same digits split elsewhere", "ab", "radio", 1, 23, 0),
]


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def vectors():
    return [dict(case=c, audio_md5=m, kind=k, start_ms=s, end_ms=e, salt=t, uid=pu.passage_uid(m, k, s, e, t))
            for c, m, k, s, e, t in INPUTS]


def main(argv) -> int:
    if "--write" in argv:
        os.makedirs(os.path.dirname(VECTORS), exist_ok=True)
        with open(VECTORS, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"encoding": pu.DOMAIN, "vectors": vectors()}, fh, indent=1)
            fh.write("\n")
        print(f"wrote {VECTORS}")
        return 0
    on_disk = json.load(open(VECTORS, encoding="utf-8"))
    check(on_disk["encoding"] == pu.DOMAIN, f"the fixture is for this encoding: {on_disk['encoding']}")
    for v in on_disk["vectors"]:
        got = pu.passage_uid(v["audio_md5"], v["kind"], v["start_ms"], v["end_ms"], v["salt"])
        check(got == v["uid"], f"{v['case']}: {got} != {v['uid']}")
    uids = [v["uid"] for v in on_disk["vectors"]]
    check(len(set(uids)) == len(uids), "every case is a different identity")
    check(all(len(u) == 32 and u == u.lower() and all(c in "0123456789abcdef" for c in u) for u in uids),
          "each is 32 lowercase hex characters")
    check([v["uid"] for v in vectors()] == uids, "and the inputs here are the fixture's")
    for bad in [("", "radio", 0, 1, 0), ("ab\x1fc", "radio", 0, 1, 0), ("ab", "", 0, 1, 0),
                ("ab", "radio", True, 1, 0), ("ab", "radio", 0, 1.0, 0), ("ab", "radio", 0, 1, -1),
                ("ab", "radio", "0", 1, 0)]:
        try:
            pu.passage_uid(*bad)
            check(False, f"refused: {bad!r}")
        except ValueError:
            pass
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("passage_uid: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
