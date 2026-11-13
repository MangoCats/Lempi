#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The journal mode of a pair, said and set `[SPEC-STAR-106]`.

`sql/schema.sql` asks for WAL, and a pair the player makes for itself is WAL
(`pair.rs`). The hub's was not: it was rebuilt on 2026-09-26 by a merge that
wrote its output with SQLite's default, the rollback journal, and nothing
since looked. In that mode a reader shuts a writer's commit out and a writer
shuts readers out, each for as long as the other takes, and the player gives
up after five seconds. On 2026-11-12 a slow start read the library for 15 s,
a queue save's commit was refused, and the hub's listener stayed locked to the
console and to the player itself for five hours. In WAL neither waits on the
other.

    python tools/pair_journal.py data/library.db          # says each half's mode
    python tools/pair_journal.py data/library.db --wal    # backs both up, then sets WAL

Either half names the pair; `lempi_db` finds the other beside it.

Setting the mode needs the file to itself: **stop the player first**, and any
tool that keeps the pair open. Both halves are backed up before either is
touched, and a half already WAL is left alone. A half another process holds
is named and left exactly as it was -- SQLite changes the mode only when no
other connection has the file -- and the run says so and exits 2; the other
half is still set. The backup goes beside the pair, in
`backups/pre-wal-<date>/`, which the nightly backup removes after 30 days
`[SPEC-STAR-084]`.

**Not for a catalogue on a read-only mount** -- `bose`'s. There WAL is the
outage `[BOS-RUN-092]`: the player cannot make the `-shm` it needs to read.
A file this cannot write is refused.

Exit: 0 both halves are WAL; 1 one is not; 2 it could not run.
"""

import argparse
import datetime as dt
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lempi_db         # noqa: E402

WAL = "wal"


def say(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


def mode_of(path: str, timeout: float = 5.0) -> str:
    """`wal`, `delete` (or another rollback mode), `absent`, or `unreadable:
    <why>`. Asked of SQLite, never read from the file's header: a process
    that holds connections to a database must not open it any other way
    (CLAUDE.md §12), and callers of this hold them."""
    if not os.path.exists(path):
        return "absent"
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)
        try:
            return str(c.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        finally:
            c.close()
    except sqlite3.Error as e:
        return f"unreadable: {e}"


def halves(path: str) -> dict:
    """`{"library": path, "listener": path}` for the pair `path` is half of."""
    role = lempi_db.shape(path)
    if role not in (lempi_db.ROLE_LIBRARY, lempi_db.ROLE_LISTENER):
        raise SystemExit(f"{path} is not one half of a pair (it is {role}): name its library.db or listener.db")
    peer = lempi_db.find_peer(path, role)
    if not peer:
        raise SystemExit(f"the other half of {path} was not found beside it")
    other = lempi_db.ROLE_LISTENER if role == lempi_db.ROLE_LIBRARY else lempi_db.ROLE_LIBRARY
    return {role: path, other: peer}


def back_up(pair: dict, today: str | None = None) -> str:
    """Both halves through the backup API, into a folder the nightly backup
    knows how to expire. Never written over."""
    root = os.path.join(os.path.dirname(os.path.abspath(pair["library"])), "backups")
    folder = os.path.join(root, f"pre-wal-{today or dt.date.today().strftime('%Y%m%d')}")
    if os.path.exists(folder):
        raise SystemExit(f"{folder} exists: a backup is never written over. Move it, or give --date")
    os.makedirs(folder)
    for half, path in sorted(pair.items()):
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        dst = sqlite3.connect(os.path.join(folder, f"{half}.db"))
        try:
            src.backup(dst)
            ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
            src.close()
        if ok != "ok":
            raise SystemExit(f"the copy of {path} fails integrity_check: {ok}")
        say(f"  backed up {half}: {os.path.join(folder, half + '.db')}")
    return folder


def set_wal(path: str, timeout: float = 5.0) -> str | None:
    """Put `path` in WAL mode. `None`, or why it could not be done -- in
    which case the file is as it was: SQLite changes the mode only when no
    other connection has the file."""
    if not os.access(path, os.W_OK):
        return "the file cannot be written here; on a read-only mount WAL is the outage [BOS-RUN-092]"
    try:
        c = sqlite3.connect(path, timeout=timeout)
        try:
            got = str(c.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
        finally:
            c.close()
    except sqlite3.Error as e:
        return f"{e}: another process has it open -- stop the player, and any tool holding the pair"
    if got != WAL:
        return f"SQLite left it in {got} mode: another process has it open -- stop the player"
    now = mode_of(path)
    if now != WAL:
        return f"read back as {now}"
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        check = c.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        c.close()
    return None if check == "ok" else f"quick_check says {check}"


def report(pair: dict) -> dict:
    modes = {half: mode_of(path) for half, path in sorted(pair.items())}
    for half, mode in modes.items():
        say(f"  {half}: {mode}  ({pair[half]})")
    return modes


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("db", help="either half of the pair")
    ap.add_argument("--wal", action="store_true", help="back both halves up, then set WAL on each that is not")
    ap.add_argument("--date", help="the backup folder's date, YYYYMMDD (default today)")
    args = ap.parse_args(argv)
    pair = halves(args.db)
    say("journal mode [SPEC-STAR-106]:")
    modes = report(pair)
    todo = [half for half, mode in modes.items() if mode != WAL]
    if not todo:
        say("RESULT pair_journal: both halves are WAL")
        return 0
    if not args.wal:
        say(f"RESULT pair_journal: NOT WAL -- {', '.join(todo)}. Stop the player, then run this with --wal")
        return 1
    bad = [half for half in todo if modes[half].startswith("unreadable") or modes[half] == "absent"]
    if bad:
        say(f"RESULT pair_journal: could not read {', '.join(bad)}; nothing changed")
        return 2
    say("backing both halves up first:")
    folder = back_up(pair, args.date)
    failed = []
    for half in todo:
        why = set_wal(pair[half])
        if why:
            failed.append(half)
            say(f"  {half}: NOT CHANGED -- {why}")
        else:
            say(f"  {half}: now WAL, and quick_check ok")
    say("as it stands:")
    report(pair)
    if failed:
        say(f"RESULT pair_journal: NOT WAL -- {', '.join(failed)}. The backup is {folder}")
        return 2
    say(f"RESULT pair_journal: both halves are WAL. As they were before: {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
