# SPDX-License-Identifier: AGPL-3.0-or-later
"""A passage's permanent identity, the same on every node [SPEC-UID-030].

The first 128 bits of SHA-256 over the passage's birth key, as 32 lowercase
hex characters [IMPL-UID-030]. Being derived from what the passage is, two
nodes making the same cut get the same identity without talking. Being
*stored*, set once when the passage is created and never recalculated, it
survives every boundary edit afterwards: that is what the span itself, which
named a passage until now, could not do [SPEC-UID-003].

`lempi_core::db::passage_uid` is this function's twin. The vectors in
`fixtures/passage_uid/vectors.json` hold the two equal (`test_passage_uid.py`
writes and checks them; a Rust test checks them too).

    passage_uid("6e1f...", "radio", 0, 200000)        -> 32 hex characters
    passage_uid("6e1f...", "radio", 0, 200000, salt=1) -> another, for a second
                                                          passage born on that key
"""
from __future__ import annotations

import hashlib

# The version names the encoding, so a change to it is a new name rather
# than a silent difference between two builds.
DOMAIN = "lempi-passage-uid/1"
SEP = "\x1f"                 # unit separator: in no md5, kind or decimal


def birth_text(audio_md5: str, kind: str, start_ms: int, end_ms: int, salt: int = 0) -> str:
    """The exact text hashed. Refuses a value that could make two keys one text."""
    for name, v in (("audio_md5", audio_md5), ("kind", kind)):
        if not isinstance(v, str) or not v or SEP in v:
            raise ValueError(f"{name} must be a non-empty string without U+001F: {v!r}")
    for name, v in (("start_ms", start_ms), ("end_ms", end_ms), ("salt", salt)):
        # bool is an int in Python, and str(True) is not a number
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{name} must be an integer: {v!r}")
    if salt < 0:
        raise ValueError(f"salt must not be negative: {salt}")
    return SEP.join((DOMAIN, audio_md5, kind, str(start_ms), str(end_ms), str(salt)))


def passage_uid(audio_md5: str, kind: str, start_ms: int, end_ms: int, salt: int = 0) -> str:
    """The uid of a passage born as `(audio_md5, kind, start_ms, end_ms)`, with `salt`
    0 unless a collision rule of [SPEC-UID-050] gave it another."""
    text = birth_text(audio_md5, kind, start_ms, end_ms, salt)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
