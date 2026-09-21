#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One way for a `tools/` script to open a Lempi database, split or not.

The player got `QualifyingConn` and the `__LIB__` placeholder when lempipi
split `[IMPL-DBSPLIT-035]`, so Rust genuinely does not care which shape it
is talking to. `tools/` never got the equivalent, because lempipi runs no
Vipunen. Splitting the *desktop* is what makes that gap matter: 63 scripts
live here, 40 touching catalogue tables, 17 touching listener tables and 12
touching both.

This is that equivalent, and it leans on one SQLite behaviour: **an
unqualified table name resolves through the attach chain** -- `main` first,
then each attached database in attach order. So a query saying
`SELECT ... FROM recordings` finds the catalogue whichever half is `main`,
and `INSERT`/`UPDATE`/`DELETE` land there too. Measured, not assumed
(`test_lempi_db.py`). Most scripts therefore need one line changed, not a
rewritten query.

## The hazard this exists to eliminate

`CREATE TABLE` does **not** follow the resolution chain. It always targets
`main`, and `IF NOT EXISTS` checks only `main` -- so a script that
bootstraps a catalogue table while the listener half is `main` creates an
empty shadow, and from that moment every unqualified read of that name
returns nothing. No error. No warning. The real table is still there, still
full, and completely masked.

Measured, both forms, and the masking is worse than a clean break:

    CREATE TABLE recordings(...)                -> shadow, silently
    CREATE TABLE IF NOT EXISTS recordings(...)  -> shadow, silently
    SELECT ... FROM recordings                  -> the empty shadow; main wins

A statement whose exact SQL text was already prepared before the shadow
appeared keeps resolving to the real table, because the prepared statement
is cached and reused. So a long-running process does not fail over
cleanly -- queries it has run before keep returning real rows while new
ones return nothing, in the same process, against the same connection.
That is the failure mode this file exists to make impossible, and
`test_lempi_db.py` pins it in both halves.

That is not a hazard to be careful about; care does not survive 63 scripts
and six years. It is eliminated here, three ways, in descending order of
how much they rely on anybody being careful:

1. **`role` decides which half is `main`.** A script that creates catalogue
   tables declares `ROLE_LIBRARY` and gets the catalogue as `main`, so its
   `CREATE` lands where it meant. This alone removes the hazard for every
   script that only ever bootstraps its own side.
2. **An authorizer refuses to create a shadow at all.** Any `CREATE TABLE`
   or `CREATE INDEX` against `main` whose name already exists in the
   attached half is denied by SQLite itself, before it runs. This is the
   one that does not depend on a person getting `role` right.
3. **An existing shadow is refused at open.** If a previous run (or a
   hand-run `sqlite3`) already left one, opening reports it by name rather
   than quietly reading zeroes out of it.

## What this does not fix

**Cross-half writes are not atomic under WAL.** SQLite documents a
transaction spanning attached databases as atomic only when the journal
mode is not WAL, and the desktop's database is WAL (lempipi's halves are
`delete`). A script that must write both halves in one indivisible step
cannot get that from the database here; it has to be ordered so that a
crash between the two writes leaves a recoverable state. `peer_writable`
is therefore opt-in and explicit, so "this script writes both halves" is
visible at its call site rather than discovered later.
"""

from __future__ import annotations

import os
import sqlite3
import sys

ROLE_LIBRARY = "library"
ROLE_LISTENER = "listener"

# A SET per side, not one table apiece. The first version of this used a
# single marker each and got it wrong: `test_jobs_remote_pull.py` builds a
# perfectly whole fixture that happens to have no `listener_play_history`,
# and one marker read that as "the library half of a split pair", went
# looking for a peer, and refused to open a database that was fine.
#
# With sets, a half is recognised by having **none** of the other side's
# tables, which is what a real half actually looks like -- `split_database.py`
# puts every listener table on one side and every catalogue table on the
# other. A database merely missing some tables still shows the ones it has,
# and reads as whole.
#
# Chosen to be tables no bootstrap path ever creates on the wrong side:
# notably NOT `file_tags` or `cover_art`, which lempipi has empty copies of
# in its listener half `[PI-OWE-040]` and which would therefore make that
# half look like a catalogue.
LIBRARY_MARKERS = {"recordings", "files", "passages", "flavor"}
LISTENER_MARKERS = {"listener_play_history", "listener_flags",
                    "listener_preferences", "listener_settings", "player_state"}

# The alias each half is attached under. Named for the half rather than
# something positional, so a query that *does* qualify reads the same
# whichever role opened the connection -- and `lib.` matches the player's
# own `__LIB__` alias `[IMPL-DBSPLIT-035]`.
ALIAS = {ROLE_LIBRARY: "lib", ROLE_LISTENER: "listener"}

# Tables that are in both halves on purpose -- `split_database.py`'s own
# `BOTH` list. `schema_meta` is two rows describing *the schema*, which is
# ambiguous the moment there are two files, so the split copies it to each
# side rather than picking one. A duplicate here is therefore expected and
# is not a shadow; every other duplicate is.
SHARED_TABLES = {"schema_meta"}

WHOLE = "whole"
EMPTY = "empty"


class SplitError(RuntimeError):
    """Raised when the two halves cannot be resolved, or one is unsafe."""


def _tables(conn, schema="main") -> set[str]:
    return {r[0] for r in conn.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type='table'")}


def markers(path: str) -> tuple[bool, bool]:
    """`(has_any_library_table, has_any_listener_table)` for one file.

    A path that does not exist yet has neither, and says so rather than
    raising. `sqlite3.connect(path)` *creates* a missing database, and
    several scripts rely on that -- inspecting the file first must not take
    that away, or a tool that used to bootstrap an empty library starts
    failing with "unable to open database file". Found by running every
    migrated script rather than by reading them.
    """
    if not os.path.exists(path):
        return False, False
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = _tables(conn)
    finally:
        conn.close()
    return bool(names & LIBRARY_MARKERS), bool(names & LISTENER_MARKERS)


def shape(path: str) -> str:
    """`WHOLE`, `ROLE_LIBRARY`, `ROLE_LISTENER`, or `EMPTY`.

    Decided by what the file actually contains, never by its name: a file
    called `lempi.db` may be either shape, and `[IMPL011]` is emphatic that
    the deployed schema and the source's idea of it are not the same thing.
    `EMPTY` is the bootstrap case -- a database just created from
    `schema.sql` has tables from both sides, so only a file with nothing
    recognisable from either lands here.

    **A half carrying a shadow reports `WHOLE` here, and that is why
    `connect()` does not decide on this alone.** A listener half with a
    stray `recordings` table has both markers and is indistinguishable from
    a whole database by content; only the existence of a peer file tells
    them apart. Kept as a plain content question so it stays simple and
    testable, with the ambiguity resolved one level up where the peer is
    known.
    """
    has_lib, has_lis = markers(path)
    if has_lib and has_lis:
        return WHOLE
    if has_lib:
        return ROLE_LIBRARY
    if has_lis:
        return ROLE_LISTENER
    return EMPTY


def find_peer(path: str, this_shape: str, explicit: str | None = None) -> str | None:
    """Where the other half is, or `None` if it cannot be located.

    `this_shape` is the role of *this* file, so the half looked for is the
    other one.

    In order: an explicit path (a `--library`/`--listener` flag), then the
    environment (`LEMPI_LIBRARY`/`LEMPI_LISTENER`), then a sibling beside
    this file. The sibling convention covers a desktop split, whose two
    halves sit in one directory; it deliberately does **not** cover
    lempipi, whose halves are on different mounts (`/srv/library` and
    `/var/lempi`) because they are on different partitions for reasons
    `[PI001]` explains -- that installation names them explicitly.
    """
    if this_shape == EMPTY:
        return None
    want = ROLE_LIBRARY if this_shape == ROLE_LISTENER else ROLE_LISTENER
    if explicit:
        return explicit
    env = os.environ.get(f"LEMPI_{want.upper()}")
    if env:
        return env
    # Sibling discovery is the one place a *name* is allowed to decide
    # anything, and only for a file already named by the convention. A
    # whole `lempi.db` that happens to share a directory with a split pair
    # is not part of it -- without this guard it gets adopted as the
    # listener half of its neighbour's library, which is exactly the
    # misdiagnosis `test_lempi_db.py` caught.
    if os.path.basename(path) not in (f"{ROLE_LIBRARY}.db", f"{ROLE_LISTENER}.db"):
        return None
    sibling = os.path.join(os.path.dirname(os.path.abspath(path)), f"{want}.db")
    if os.path.exists(sibling):
        return sibling
    return None


def _deny_shadows(conn, peer_alias: str):
    """Install the authorizer that makes a masking shadow impossible.

    SQLite asks before it acts, so this refuses the statement rather than
    detecting the damage afterwards. Scoped as narrowly as it can be: only
    `CREATE TABLE`/`CREATE INDEX`, only against `main`, only for a name the
    attached half already has. Everything else -- including creating a
    table that genuinely belongs to this half -- is untouched.
    """
    peer_names = _tables(conn, peer_alias) - SHARED_TABLES

    def guard(action, arg1, arg2, dbname, _source):
        if dbname == "main" and action in (sqlite3.SQLITE_CREATE_TABLE,
                                           sqlite3.SQLITE_CREATE_INDEX):
            # For an index, `arg1` is the index and `arg2` the table it is
            # on. Both are checked: indexing a table that lives in the
            # other half is meaningless, and an index *named* after a table
            # over there collides in the same namespace tables use, which
            # is the same masking bug wearing a different hat.
            if arg1 in peer_names or (arg2 and arg2 in peer_names):
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(guard)


def tables(conn) -> set[str]:
    """Every table visible on this connection, across **all** attached
    schemas.

    The second hazard, after the shadow one. `sqlite_master` is per-schema,
    so the near-universal existence check

        have = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    silently answers only for `main` -- on a split pair it reports half the
    database missing, and the caller then does whatever it does when a
    table is genuinely absent. Ten scripts in `tools/` use that shape,
    `console.py` five times in one file, and every one of them means "is
    this table anywhere I can see". This is that question, asked properly.
    """
    names: set[str] = set()
    for _seq, schema, _file in conn.execute("PRAGMA database_list"):
        names |= {r[0] for r in conn.execute(
            f"SELECT name FROM \"{schema}\".sqlite_master WHERE type='table'")}
    return names


def has_table(conn, name: str) -> bool:
    """Whether `name` is reachable on this connection, in either half."""
    return name in tables(conn)


def shadows(conn, peer_alias: str) -> set[str]:
    """Table names present in both halves and not meant to be.

    `SHARED_TABLES` is subtracted: those are duplicated deliberately. Found
    the first time this ran against a real split pair, which is exactly the
    kind of thing a synthetic fixture would never have shown.
    """
    return (_tables(conn, "main") & _tables(conn, peer_alias)) - SHARED_TABLES


def connect(path: str, role: str, *, writable: bool = False,
            peer: str | None = None, peer_writable: bool = False,
            check_same_thread: bool = True, timeout: float | None = None):
    """Open `path`, attaching the other half when there is one.

    `role` is the half this script owns -- the one it writes and creates
    tables in -- and becomes `main`. `path` may name either half (or a
    whole database); the file's own contents decide, and the peer is looked
    up per `find_peer`. A whole database is opened exactly as before, with
    no attach and no authorizer, so an unsplit installation carries none of
    this.

    Raises `SplitError` rather than guessing when the peer cannot be found:
    a script that silently ran against half a database would produce
    answers that look fine and are wrong.

    `timeout` is passed through where given -- the `apply_*` scripts use
    60 seconds because they run while a player may hold the database.

    `check_same_thread` is passed straight through, defaulting to
    `sqlite3`'s own `True`. `console.py` serves from a thread pool and
    needs `False`; that had been on its own `sqlite3.connect` call and was
    lost in the first migration here, which is the kind of thing only
    actually starting the server finds.
    """
    if role not in (ROLE_LIBRARY, ROLE_LISTENER):
        raise ValueError(f"role must be {ROLE_LIBRARY!r} or {ROLE_LISTENER!r}, got {role!r}")

    this = shape(path)
    if this == EMPTY:
        return sqlite3.connect(path if writable else f"file:{path}?mode=ro",
                               uri=not writable, check_same_thread=check_same_thread,
                               **({} if timeout is None else {"timeout": timeout}))

    if this == WHOLE:
        # Both markers: either a genuine single-file installation, or a
        # half that a shadow has made look like one. Only the presence of a
        # peer file distinguishes them, so look before concluding.
        candidate = (find_peer(path, ROLE_LISTENER, peer)
                     or find_peer(path, ROLE_LIBRARY, peer))
        if not candidate or os.path.samefile(candidate, path):
            return sqlite3.connect(path if writable else f"file:{path}?mode=ro",
                                   uri=not writable, check_same_thread=check_same_thread,
                                   **({} if timeout is None else {"timeout": timeout}))
        # A peer exists AND this file carries both markers -- one of them is
        # a shadow. Fall through so the shadow check below names it.
        peer_lib, _ = markers(candidate)
        this = ROLE_LISTENER if peer_lib else ROLE_LIBRARY
        peer_path = candidate
    else:
        peer_path = find_peer(path, this, peer)

    if not peer_path:
        # **No peer: open it plainly, exactly as the script did before.**
        #
        # This used to raise, on the reasoning that half a database gives
        # answers that look fine and are wrong. Running the suites proved
        # that wrong in the other direction: a catalogue-only database is a
        # perfectly ordinary thing -- `remote_snapshot.py` builds one,
        # `accept_remote_basis.py` and the `jobs.py` tests use one, a
        # library ingested but never played is one -- and refusing to open
        # it broke four suites that had nothing to do with splitting.
        #
        # By content, a catalogue-only file and the library half of a pair
        # are indistinguishable. The peer is the only evidence, and without
        # it the safe answer is the old behaviour: this helper must never
        # leave a script worse off than the `sqlite3.connect` it replaced.
        # A genuine half with a genuinely missing peer then fails the way it
        # always would have -- on the first query naming a table from the
        # other side, which is a clean error and not a quiet wrong answer.
        #
        # Said out loud only when the filename follows the split convention,
        # which is the one case where "half a pair, peer missing" is the
        # likely reading rather than a guess.
        if os.path.basename(path) in (f"{ROLE_LIBRARY}.db", f"{ROLE_LISTENER}.db"):
            want = ROLE_LIBRARY if this == ROLE_LISTENER else ROLE_LISTENER
            print(f"lempi_db: {path} looks like the {this} half of a split database "
                  f"but no {want} half was found; opening it alone. Pass it "
                  f"explicitly, or set LEMPI_{want.upper()}.", file=sys.stderr)
        return sqlite3.connect(path if writable else f"file:{path}?mode=ro",
                               uri=not writable, check_same_thread=check_same_thread,
                               **({} if timeout is None else {"timeout": timeout}))

    # The half this script owns is `main`, whichever one was named.
    main_path, attach_path = (path, peer_path) if this == role else (peer_path, path)
    # `writable` describes the half the script **owns** -- the `role` half,
    # which is `main` -- and never whichever file happened to be named.
    #
    # This used to swap the two flags when the caller named the peer, on the
    # reasoning that `writable` belonged to `path`. That reading contradicts
    # `role`'s own definition three paragraphs up ("the half this script owns
    # -- the one it writes and creates tables in") and `peer_writable`'s
    # ("this script writes both halves"), and it fails in the worst possible
    # direction: the half the script means to write is opened read-only, and
    # the half it must not touch is opened writable.
    #
    # Found live 2026-09-11 by `import_flags.py`, which asks for
    # `ROLE_LISTENER, writable=True` and was handed the *catalogue* path by
    # Vipunen's job runner -- `attempt to write a readonly database` on its
    # `listener_flags` INSERT, while `library.db` sat open for writing behind
    # it. Symmetric, and proven both ways on synthetic halves: a
    # `ROLE_LIBRARY` writer named with the listener path lost its catalogue
    # write the same way. It only bites a caller that names the peer *and*
    # writes, which is why the split's own migration never hit it.
    main_writable = writable
    attach_writable = peer_writable

    conn = sqlite3.connect(
        f"file:{main_path}" + ("" if main_writable else "?mode=ro"), uri=True,
        check_same_thread=check_same_thread,
        **({} if timeout is None else {"timeout": timeout}))
    other = ROLE_LIBRARY if role == ROLE_LISTENER else ROLE_LISTENER
    alias = ALIAS[other]
    conn.execute(
        "ATTACH DATABASE ? AS " + alias,
        (f"file:{attach_path}" + ("" if attach_writable else "?mode=ro"),))

    found = shadows(conn, alias)
    if found:
        conn.close()
        raise SplitError(
            f"{main_path} and {attach_path} both contain {sorted(found)}. "
            f"One of them is a shadow masking the other; nothing should read "
            f"this pair until it is resolved.")
    _deny_shadows(conn, alias)
    return conn
