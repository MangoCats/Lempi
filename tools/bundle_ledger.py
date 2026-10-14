# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which files a bundle send has touched on each node, since when `[SPEC-STAR-945]`.

A bundle writes a node's catalogue outside the star sync: a file it lacked
arrives with its passages, credits, recordings and release, and a file it
already held may still have its recordings, hold and release updated
(`bundle.rs`). Star sync patches against what it last *sent*, so it cannot see
either. The snapshot asks the node for its own rows about those files; a file
the node holds and the sent copy lacks shows in the node's summary, and this
ledger names the rest -- held files a later bundle touched.

The ledger is `sync/bundled.json` beside the hub's library, appended to by the
console's `send-bundle` job when an applied send succeeds, and read by
`star_sync.py snapshot`. An entry is `{node, at, bundle, files}`; it is never
rewritten, and a run reads the entries made since the node was last sent.
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import os


def ledger_path(library: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(library)), "sync", "bundled.json")


def files_of(bundle: str) -> list[str]:
    """Every `audio_md5` a bundle's payload names: in one `payload.json`, or in its parts."""
    out: set[str] = set()
    for p in sorted(glob.glob(os.path.join(bundle, "payload*.json"))):
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
        out.update(e["audio_md5"] for e in doc.get("encodings") or [] if e.get("audio_md5"))
    return sorted(out)


def record(library: str, node: str, bundle: str, at: str | None = None) -> int:
    """Append one applied send. Returns how many files it names."""
    path = ledger_path(library)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entries = read(library)
    files = files_of(bundle)
    entries.append({"node": node, "at": at or dt.datetime.now(dt.timezone.utc).isoformat(),
                    "bundle": bundle, "files": files})
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(entries, fh, indent=1)
    os.replace(tmp, path)
    return len(files)


def read(library: str) -> list[dict]:
    path = ledger_path(library)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def touched_since(library: str, node: str, since: str | None) -> set[str]:
    """The files sends to `node` have touched since `since` (an ISO time; None is ever)."""
    def when(s):
        t = dt.datetime.fromisoformat(s)
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    after = when(since) if since else None
    return {f for e in read(library) if e.get("node") == node and (after is None or when(e["at"]) > after)
            for f in e.get("files") or []}
