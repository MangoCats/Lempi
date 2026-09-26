# SPDX-License-Identifier: AGPL-3.0-or-later
"""The SHA-256 of a file's bytes, and the column Vipunen keeps it in.

Two uses, one routine:

* **In a bundle** `[SPEC-PL-087]` -- `export_bundle.py` hashes the copy it
  ships, so a receiver without ffmpeg can prove the file arrived whole.
* **In the catalogue** `[REQ-AND-960]` -- `files.sha256`, so a phone
  describing its music by byte hash can be told exactly which files Vipunen
  holds already, before anything is sent `[REQ-AND-288]`.

It is not identity: a rewritten tag changes it and leaves `audio_md5` alone.
It is the one fingerprint a phone can compute.
"""
import hashlib
import sqlite3


def sha256_file(path: str) -> str:
    """The SHA-256 of a file's bytes, lower-case hex."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ensure_sha256_column(conn: sqlite3.Connection) -> None:
    """Bring a `files` table predating `sha256` up to date, and index it.

    Already-present is the expected path on every run after the first, so the
    ALTER's failure is swallowed, the same shape as `md5_generator`'s. The
    index is what lets a negotiation look up hundreds of phone files at once.
    Existing rows get NULL until `add_byte_hashes.py` fills them.
    """
    try:
        conn.execute("ALTER TABLE files ADD COLUMN sha256 TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS files_sha256 ON files(sha256)")
