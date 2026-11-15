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


def has_uids(conn) -> bool:
    """Whether this catalogue's passages carry a uid yet [IMPL-UID-034]."""
    return any(r[1] == "passage_uid" for r in conn.execute("PRAGMA table_info(passages)"))


def insert_passage(conn, file_id: int, kind: str, start_ms: int, end_ms: int, boundary_src: str,
                   **columns) -> int:
    """Create one passage and return its local number: the one place a tool
    under `tools/` does [IMPL-UID-038].

    Where the catalogue's passages carry uids, the new one is born with its
    own: its birth key is the file's `audio_md5`, its kind and its span, and
    the salt is the lowest not already born on that key here [SPEC-UID-054].
    A cut made again after its passage was removed finds no living key, and
    so is born with the uid it had, as [SPEC-UID-056] asks. Where they do not
    yet, the row is exactly what the tool wrote before this existed.

    `columns` are the rest of the row, `lead_in_ms` and the like."""
    row = {"file_id": file_id, "kind": kind, "start_ms": start_ms, "end_ms": end_ms,
           "boundary_src": boundary_src, **columns}
    if has_uids(conn):
        f = conn.execute("SELECT audio_md5 FROM files WHERE file_id = ?", (file_id,)).fetchone()
        if f is None:
            raise ValueError(f"no file {file_id}: a passage's uid is born of its file's audio_md5")
        salt = 0
        while conn.execute("SELECT 1 FROM passages WHERE birth_md5 = ? AND birth_kind = ? AND birth_start_ms = ? "
                           "AND birth_end_ms = ? AND birth_salt = ?", (f[0], kind, start_ms, end_ms, salt)).fetchone():
            salt += 1
        row.update(passage_uid=passage_uid(f[0], kind, start_ms, end_ms, salt), birth_md5=f[0], birth_kind=kind,
                   birth_start_ms=start_ms, birth_end_ms=end_ms, birth_salt=salt)
    cols = list(row)
    return conn.execute(f"INSERT INTO passages ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [row[c] for c in cols]).lastrowid
