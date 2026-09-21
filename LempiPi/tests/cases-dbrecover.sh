# lempi-db-recover: it runs on every boot of a machine that is power-cut by
# design `[PI3-FOUND-120]`, and if it is wrong the appliance does not start at
# all. Run against real SQLite, not a stub -- the behaviour under test is
# SQLite's recovery, so faking it would test nothing.
group dbrecover || return 0
printf '\ndbrecover\n'

recover() { PATH="$REALPATH" LEMPI_DB="$1" LEMPI_LIBRARY_DB="$2" LEMPI_LISTENER_DB="$3" \
    sh "$PI/lempi-db-recover" 2>&1; }

setup
REALPATH="$ORIG_PATH"
D="$VT_STATE/dbs"; mkdir -p "$D"
if ! PATH="$REALPATH" command -v sqlite3 >/dev/null 2>&1; then
    printf '  skip sqlite3 not installed, cannot test recovery\n'
    teardown
    return 0
fi

PATH="$REALPATH" sqlite3 "$D/good.db" "CREATE TABLE t (x); INSERT INTO t VALUES (1);"
OUT=$(recover "$D/good.db" "$D/absent.db" "$D/absent2.db")
assert_eq "$OUT" "" "says nothing about a healthy database"

# A database that is simply not there is skipped, not an error: a fresh card
# has no listener store until the player makes one.
OUT=$(recover "$D/absent.db" "$D/absent2.db" "$D/absent3.db")
assert_eq "$OUT" "" "skips databases that do not exist"

# **A genuine hot journal, made the way the appliance makes them**: a write in
# flight and the power removed. An empty `-journal` file will not do -- SQLite
# reads its header to decide whether there is anything to roll back, so a
# zero-length one is not a hot journal and tests nothing.
PATH="$REALPATH" sqlite3 "$D/good.db" "PRAGMA journal_mode=delete;" >/dev/null
{ printf 'BEGIN IMMEDIATE;
INSERT INTO t VALUES (2);
'; sleep 9; } |
    PATH="$REALPATH" sqlite3 "$D/good.db" >/dev/null 2>&1 &
WRITER=$!
i=0
while [ "$i" -lt 40 ] && [ ! -s "$D/good.db-journal" ]; do i=$((i + 1)); sleep 0.1; done
kill -9 "$WRITER" 2>/dev/null
wait "$WRITER" 2>/dev/null

if [ -s "$D/good.db-journal" ]; then
    ok "the fixture produced a real hot journal"
    OUT=$(recover "$D/good.db" "$D/absent.db" "$D/absent2.db")
    assert_in "$OUT" "hot journal" "announces a hot journal"
    # **Deliberately not asserting that the journal file disappears.**
    # Measured on the appliance 2026-09-10: after a write killed mid
    # transaction, the journal survives `PRAGMA user_version`, a real `SELECT`,
    # `PRAGMA integrity_check` and `BEGIN IMMEDIATE` alike -- 4616 bytes every
    # time -- while the data reads back correctly. The transaction never
    # reached the main database, so SQLite does not consider that journal hot
    # and leaves it where it lies. Asserting on its removal would be testing
    # SQLite's housekeeping, not this script's contract.
    #
    # What the script promises is to say so when a journal outlives the
    # attempt, and that is what is checked.
    assert_in "$OUT" "still journalled" "says so when the journal outlives the attempt"
    # The row from the interrupted transaction must be gone: rolled back, not
    # applied. This is the whole purpose -- a half-written database is what
    # left the player restarting 23 times `[PI3-FOUND-120]`.
    ROWS=$(PATH="$REALPATH" sqlite3 "$D/good.db" "SELECT count(*) FROM t;" 2>/dev/null)
    assert_eq "$ROWS" "1" "the interrupted write was rolled back, not applied"
else
    printf '  skip could not manufacture a hot journal on this filesystem
'
    : > "$D/good.db-journal"
    OUT=$(recover "$D/good.db" "$D/absent.db" "$D/absent2.db")
    assert_in "$OUT" "hot journal" "announces a journal file beside the database"
    rm -f "$D/good.db-journal"
fi

# **WAL, which is what these databases actually are since `[PI-OWE-030]`.**
# The recovery act is identical -- one read-write open -- but the evidence
# is not, and the script's reporting is the half that had to change. Without
# these cases the WAL fix is a claim.
PATH="$REALPATH" sqlite3 "$D/wal.db" \
    "PRAGMA journal_mode=WAL; CREATE TABLE t (x); INSERT INTO t VALUES (1);" >/dev/null

# A WAL database at rest, cleanly closed: its `-wal` is gone or empty, and
# that is ORDINARY. Announcing it would cry wolf on every single boot, which
# is worse than silence because it trains the reader to ignore the log.
OUT=$(recover "$D/wal.db" "$D/absent.db" "$D/absent2.db")
assert_eq "$OUT" "" "says nothing about a cleanly-closed WAL database"

# An EMPTY `-wal` beside it is still ordinary -- a WAL database in use has
# one, and after a clean close it may be left behind at zero length. This is
# the case a naive `[ -f "$db-wal" ]` would have got wrong, announcing an
# unclean stop on a healthy machine.
: > "$D/wal.db-wal"
OUT=$(recover "$D/wal.db" "$D/absent.db" "$D/absent2.db")
assert_eq "$OUT" "" "says nothing about an EMPTY -wal, which is not evidence of anything"
rm -f "$D/wal.db-wal"

# A NON-EMPTY `-wal` at this point in boot, with nothing holding the database
# open, means frames that were never checkpointed -- the WAL equivalent of a
# hot journal, and worth saying out loud.
printf 'not really a wal, but it is not empty\n' > "$D/wal.db-wal"
OUT=$(recover "$D/wal.db" "$D/absent.db" "$D/absent2.db")
assert_in "$OUT" "un-checkpointed WAL" "announces a non-empty -wal as an unclean stop"
rm -f "$D/wal.db-wal" "$D/wal.db-shm"

# Opening read-write is the whole point: the read-only attach that this
# replaced could not roll anything back `[PI3-FOUND-120]`.
printf 'this is not a database at all\n' > "$D/broken.db"
OUT=$(recover "$D/broken.db" "$D/absent.db" "$D/absent2.db")
assert_in "$OUT" "could not open" "reports a database it cannot open"

# It must never stop the boot, whatever it finds.
PATH="$REALPATH" LEMPI_DB="$D/broken.db" LEMPI_LIBRARY_DB="$D/absent.db" \
    LEMPI_LISTENER_DB="$D/absent2.db" sh "$PI/lempi-db-recover" >/dev/null 2>&1
assert_eq "$?" "0" "always exits 0, so a bad database never blocks the boot"

# **`[PI-PRE-090]` The pre-split whole database, once the halves exist.**
# The danger in this rule is one-sided: recovering a database nobody opens
# costs a little time, while SKIPPING one the player does open is a crash
# loop. So the cases below pin both directions, and the not-skipped one
# matters more.
SPLIT="$D/split"; mkdir -p "$SPLIT"
PATH="$REALPATH" sqlite3 "$SPLIT/whole.db" "CREATE TABLE t (x);" >/dev/null

# No halves beside it: this is an unsplit appliance, the whole database is the
# live one, and nothing may change about how it is treated.
printf 'not a database
' > "$SPLIT/whole.db.broken"
OUT=$(recover "$SPLIT/whole.db" "$SPLIT/no-library.db" "$SPLIT/no-listener.db")
assert_eq "$OUT" "" "recovers the whole database when no split halves exist"

# One half only -- a half-finished split, or a half-restored rollback. Still
# not evidence that the player has moved on, so still recovered.
PATH="$REALPATH" sqlite3 "$SPLIT/library.db" "CREATE TABLE t (x);" >/dev/null
OUT=$(recover "$SPLIT/whole.db" "$SPLIT/library.db" "$SPLIT/no-listener.db")
assert_eq "$OUT" "" "still recovers the whole database when only one half exists"

# Both halves: the player opens those and has not touched the whole file since
# the split. Skipped, and said out loud, because a gigabyte of superseded
# database is worth mentioning until somebody deletes it.
PATH="$REALPATH" sqlite3 "$SPLIT/listener.db" "CREATE TABLE t (x);" >/dev/null
OUT=$(recover "$SPLIT/whole.db" "$SPLIT/library.db" "$SPLIT/listener.db")
assert_in "$OUT" "superseded by the split halves" "names the whole database it is skipping"

# And the skip must be confined to the whole database: a broken HALF still has
# to be reported, or the rule would have silenced the databases that matter.
printf 'this is not a database at all
' > "$SPLIT/listener.db"
OUT=$(recover "$SPLIT/whole.db" "$SPLIT/library.db" "$SPLIT/listener.db")
assert_in "$OUT" "could not open" "still reports a broken half while skipping the whole database"

# **`[PI-PRE-030]` Which command performs the recovery, and what happens when
# it is not there.** The script used to name `sqlite3`. Being never-fatal, it
# would then have reported "could not open" -- the same words it uses for a
# genuinely broken database -- and recovery would have stopped happening with
# nothing in the log to say so. It now resolves a runner instead, and these
# cases are what stop that quietly regressing.
#
# The PATH is sanitised rather than emptied. `command -v` is a builtin, but
# the script is INVOKED as `sh <path>`, so a bare `PATH=` hides the
# interpreter itself and every case fails with `sh: not found` -- which is
# what the first version of this did. Both directories therefore carry `sh`,
# and differ only in which database tool they offer.
SANE="$D/bin-python-only"; mkdir -p "$SANE"
NONE="$D/bin-empty"; mkdir -p "$NONE"
SH="$(PATH="$REALPATH" command -v sh 2>/dev/null || echo /bin/sh)"
ln -sf "$SH" "$SANE/sh"
ln -sf "$SH" "$NONE/sh"
PY="$(PATH="$REALPATH" command -v python3 2>/dev/null || true)"
[ -n "$PY" ] && ln -sf "$PY" "$SANE/python3"

recover_pathless() { PATH="$1" LEMPI_DB="$2" LEMPI_LIBRARY_DB="$D/absent.db"     LEMPI_LISTENER_DB="$D/absent2.db" sh "$PI/lempi-db-recover" 2>&1; }

if [ -n "$PY" ]; then
    # A healthy WAL database, recovered by python3 alone. Silence proves the
    # fallback opened it without complaint -- had no runner been resolved, the
    # script would have said "could not open" here.
    OUT=$(recover_pathless "$SANE" "$D/wal.db")
    assert_eq "$OUT" "" "with no sqlite3, python3 opens a healthy database silently"

    # And it really is python3 doing the work rather than nothing at all: a
    # file that is not a database must still be reported. A resolver that
    # silently did nothing would pass the case above and fail this one.
    OUT=$(recover_pathless "$SANE" "$D/broken.db")
    assert_in "$OUT" "could not open" "with no sqlite3, python3 still reports an unopenable database"
else
    printf '  skip python3 not installed, cannot test the fallback runner
'
fi

# Neither runner. The failure that matters is a SILENT one, so the message
# must name the cause rather than blaming the database -- and the boot must
# still proceed, because refusing to start over a missing helper is the
# outcome this whole mechanism exists to avoid `[PI-PRE-020]`.
OUT=$(recover_pathless "$NONE" "$D/wal.db")
assert_in "$OUT" "no sqlite3 and no python3" "names the missing tools instead of blaming the database"

PATH="$NONE" LEMPI_DB="$D/wal.db" LEMPI_LIBRARY_DB="$D/absent.db"     LEMPI_LISTENER_DB="$D/absent2.db" sh "$PI/lempi-db-recover" >/dev/null 2>&1
assert_eq "$?" "0" "still exits 0 when no runner exists at all"
teardown
