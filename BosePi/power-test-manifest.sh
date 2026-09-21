#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# power-test-manifest.sh -- the comparable state of `bose`, for the hard
# power-loss test `[PI-FS-050]`.
#
# **Read-only. It never writes to bose.** Run once before the cut and once
# after each reboot, then diff the two logs under BosePi/logs/.
#
# The output is split into two kinds of fact, because a diff is only useful
# if you know which lines are allowed to move:
#
#   INVARIANT -- must be identical across a power cut. Schema, page size,
#                user_version, integrity. Any change here IS the finding.
#   MOVES     -- boot id, uptime, pids, frame counters, and the play counts,
#                which keep growing because the appliance keeps playing. A
#                change is expected; a change in the WRONG DIRECTION (fewer
#                rows than before) is the loss being measured.
#
# `synchronous=FULL` is set on this database, so a committed transaction has
# already fsynced its WAL. Losing committed rows would therefore be a real
# finding, not the expected cost of a cut -- which is the whole reason to
# record counts beforehand rather than shrug at them afterwards.
#
# f2fs on C is mounted `errors=continue`: it will NOT remount read-only or
# panic on damage. Silence after a cut is not evidence of success, so §2
# asks the filesystem and the database explicitly instead of inferring from
# the fact that the machine came back.
#
# Deliberately does NOT use lib.sh's `run`, which dies on a failed step. A
# damaged appliance is exactly when this has to still produce a manifest, so
# `probe` below logs failure and carries on. Nothing here may abort the run.
#
# Quoting discipline, so this keeps working when edited: the remote command
# is a bash DOUBLE-quoted string (so $DB expands), and the Python inside it
# uses ONLY single quotes. SQL identifiers are [bracketed] and SQL values are
# passed as ? parameters, so no nested quote ever appears.
set -u

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

DB=/var/lempi/listener.db

# probe <description> <command...> -- logged, and never fatal.
probe() {
    local desc="$1"; shift
    step "$desc"
    "$@" 2>&1 | tee -a "$LOG_FILE"
    local rc=${PIPESTATUS[0]}
    [ "$rc" -eq 0 ] || say "(probe exited $rc -- recorded, continuing)"
}

caveat "This script has not been exercised across an actual power cut." \
       "It is read-only, so a wrong answer here is a wrong answer, not damage." \
       "Read the sections; do not trust a clean exit."

log "power-test-manifest -- $(date -u +%Y-%m-%dT%H:%M:%SZ) -- host ${HOST:-pi@bose}"

if ! ssh -o ConnectTimeout=10 "${HOST:-pi@bose}" true 2>/dev/null; then
    log ""
    log "  bose is NOT reachable over ssh."
    log "  If this is the run after a cut, that is itself the result: the"
    log "  appliance did not come back to a state that serves ssh. Use the"
    log "  serial console (console=serial0,115200 is set in cmdline.txt), or"
    log "  read partition C in a card reader on the dev host."
    exit 1
fi

probe "1. INVARIANT -- database identity" on "sudo python3 -c '
import sqlite3, hashlib
try:
    c = sqlite3.connect(\"file:$DB?mode=ro\", uri=True)
    s = chr(10).join(r[0] or chr(32) for r in c.execute(\"select sql from sqlite_master order by name\"))
    print(\"  schema sha256       \", hashlib.sha256(s.encode()).hexdigest())
    print(\"  objects in schema   \", c.execute(\"select count(*) from sqlite_master\").fetchone()[0])
    for p in (\"page_size\",\"user_version\",\"journal_mode\",\"synchronous\",\"wal_autocheckpoint\"):
        print(\"  %-20s\" % p, c.execute(\"pragma \" + p).fetchone()[0])
except Exception as e:
    print(\"  UNREADABLE:\", e)
'"

probe "2. INVARIANT -- integrity, asked rather than assumed" on "sudo python3 -c '
import sqlite3
try:
    c = sqlite3.connect(\"file:$DB?mode=ro\", uri=True)
    print(\"  quick_check         \", c.execute(\"pragma quick_check\").fetchone()[0])
except Exception as e:
    print(\"  quick_check          FAILED:\", e)
'"
say "kernel log, filesystem and card (errors=continue hides these otherwise):"
probe "2b. kernel complaints this boot" on "sudo dmesg -T | grep -iE 'f2fs|ext4-fs error|i/o error|mmcblk' | tail -15"
say "a clean boot shows only mount/recovery lines -- no 'error', no 'corrupt'"

probe "3. MOVES -- content, and the direction that matters" on "sudo python3 -c '
import sqlite3
try:
    c = sqlite3.connect(\"file:$DB?mode=ro\", uri=True)
    names = [r[0] for r in c.execute(\"select name from sqlite_master where type=? order by name\", (\"table\",))]
    for n in names:
        try:
            print(\"  %-34s %10d\" % (n, c.execute(\"select count(*) from [%s]\" % n).fetchone()[0]))
        except Exception as e:
            print(\"  %-34s ERR %s\" % (n, e))
    print()
    print(\"  page_count %s   freelist %s\" % (c.execute(\"pragma page_count\").fetchone()[0],
                                              c.execute(\"pragma freelist_count\").fetchone()[0]))
except Exception as e:
    print(\"  UNREADABLE:\", e)
'"
say "AFTER should be >= BEFORE on every row count. Fewer rows is the loss."

probe "4. MOVES -- the tail of the listening record" on "sudo python3 -c '
import sqlite3, datetime
try:
    c = sqlite3.connect(\"file:$DB?mode=ro\", uri=True)
    print(\"  max(play_id)        \", c.execute(\"select max(play_id) from listener_play_history\").fetchone()[0])
    for r in c.execute(\"select play_id, played_at, passage_id, heard_ms, selected_by from listener_play_history order by play_id desc limit 5\"):
        print(\"    play_id=%s  %s  passage=%s  heard=%.0fs  by=%s\"
              % (r[0], datetime.datetime.fromtimestamp(r[1]).strftime(\"%Y-%m-%d %H:%M:%S\"), r[2], (r[3] or 0)/1000.0, r[4]))
    print(\"  player_state        \", c.execute(\"select * from player_state\").fetchone())
except Exception as e:
    print(\"  UNREADABLE:\", e)
'"
say "these exact play_ids must still exist afterwards; a missing tail is the cut"

probe "5. MOVES -- the writable partition and what it holds" on "df -h /var/lempi; findmnt -no OPTIONS /var/lempi; sudo ls -l --time-style=full-iso $DB $DB-wal $DB-shm"
# NOT a lifetime figure: /proc/diskstats counts from boot and resets with it.
# The 2026-09-10 cut proved this the blunt way -- 2.050 GB before, 0.007 GB
# after. It is a RATE source (divide by uptime), never a wear total, and this
# card exposes no wear attribute at all.
probe "5b. bytes written to C THIS BOOT (resets at boot -- a rate, not a total)" on "awk '/mmcblk0p3/ { printf \"  %.3f GB written, %.3f GB read\\n\", \$10*512/1e9, \$6*512/1e9 }' /proc/diskstats; echo \"  over: \$(uptime -p)\""

probe "6. MOVES -- boot, service, and the [BOS-OPS-090] confound" on "cat /proc/sys/kernel/random/boot_id; uptime -p; cat /proc/swaps"
probe "6b. service state" on "systemctl show lempi.service -p NRestarts -p ActiveEnterTimestamp -p MainPID -p Result; systemctl is-active lempi.service mpd.service; systemctl --failed --no-pager"
say "swap present here answers whether [IMPL-BOS-170]'s fix survives a boot."
say "A cut taken without a graceful reboot first tests that AND the cut at once."
say "NRestarts>0 after a boot is the crash loop [PI3-FOUND-120] warned about."
say "lempi-db-recover IS deployed since 2026-09-11 and runs at every start,"
say "so recovery should be automatic; the boot log says whether it happened:"
say "  journalctl -u lempi -b | grep -E 'preflight|db-recover'"
say "lempi-preflight runs before it and names the tools it depends on, so an"
say "'armed via' line means recovery was possible [PI-PRE-010]. By hand, as pi:"
say "  sudo -u pi python3 -c \"import sqlite3; sqlite3.connect('$DB').execute('pragma user_version')\""
say "  sudo systemctl reset-failed lempi.service && sudo systemctl start lempi.service"
say "NOTE: the catalogue at /srv/library is read-only in normal operation, so"
say "a dirty library.db-wal needs an attended window to repair [BOS-RUN-090]."

# The card is found BY NAME, never by number. The 2026-09-10 power cut moved
# the HiFiBerry from card 2 to card 1 (vc4hdmi1 and it swapped places), which
# is `[IMPL-BOS-140]`'s hazard recurring on a live machine -- the first version
# of this script hard-coded card2 and reported `closed` for a device that was
# playing perfectly. `--device hifiberry` in the unit is a name substring match
# and rode it out; this probe now does the same.
probe "7. MOVES -- is audio actually flowing" on "
CARD=\$(sed -n 's/^ *\\([0-9]\\+\\) \\[sndrpihifiberry.*/\\1/p' /proc/asound/cards)
echo \"  hifiberry is card \$CARD (BY NAME -- the number is not stable across boots)\"
cat /proc/asound/card\$CARD/pcm0p/sub0/status
sudo fuser -v /dev/snd/* 2>&1 | head -5"
say "state: RUNNING owned by lempi on the hifiberry card is the only"
say "non-self-reported proof. A number here is not evidence; the name is."

step "Done"
log ""
log "  Manifest written to $LOG_FILE"
log "  Compare with:"
log "    diff BosePi/logs/power-test-manifest-<before>.log \\"
log "         BosePi/logs/power-test-manifest-<after>.log"
