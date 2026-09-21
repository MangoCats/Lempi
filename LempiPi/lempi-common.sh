# Shared by the appliance's shell helpers. Sourced, never executed.
#
# **Why this exists `[PI3-AIM-090]`.** The same five things were written out
# four and five times over: where the listener's database lives, how to read
# the chosen speaker out of it, how to parse a sink name out of `wpctl status`,
# how to get a device's alias, and how to count bits in an AFH map. Each copy
# was correct when written and they had already begun to drift -- the sink
# parser was fixed once in `lempi-wait-sink` `[PI3-FOUND-110]` and the fix had
# to be carried by hand into everything that had copied it.
#
# A helper that reads the wrong database tells the listener about the speaker
# they used to have `[PI3-FOUND-280]`. That failure has happened here before,
# and it is the kind that only happens to duplicated knowledge.
#
# Sourced with:
#     . "${LEMPI_COMMON:-/usr/local/lib/lempi-common.sh}" 2>/dev/null ||
#         . "$(dirname "$0")/lempi-common.sh"

# **Where the listener's settings live.** The listener-side file, not the
# pre-split one `[PI3-FOUND-280]`: the speaker is whichever the listener last
# chose, and that choice has been recorded in `listener.db` since
# `[IMPL-DBSPLIT-025]`.
lempi_db() {
    if [ -n "${LEMPI_DB:-}" ]; then
        printf '%s\n' "$LEMPI_DB"
    elif [ -f /var/lempi/listener.db ]; then
        printf '%s\n' /var/lempi/listener.db
    else
        printf '%s\n' /srv/library/library.db
    fi
}

# The speaker the listener chose, by address, or empty.
lempi_speaker() {
    [ -n "${LEMPI_SPEAKER:-}" ] && { printf '%s\n' "$LEMPI_SPEAKER"; return; }
    sqlite3 "$(lempi_db)" \
        "SELECT value FROM player_settings WHERE key = 'speaker_address'" 2>/dev/null
}

# **Every sink PipeWire currently offers**, one name per line, dummy excluded.
# Parsed from the numbered rows only, so no header or box-drawing line can be
# mistaken for a sink -- the whole of `[PI3-FOUND-110]`, which is why this
# lives in one place now.
lempi_sinks() {
    wpctl status 2>/dev/null |
        sed -n '/Sinks:/,/Sink endpoints/p' |
        sed -n 's/^[^0-9]*[0-9][0-9]*\.[[:space:]]*\(.*\)$/\1/p' |
        sed 's/[[:space:]]*\[vol.*$//; s/[[:space:]]*$//' |
        grep -v '^Dummy Output$'
}

# Does PipeWire offer a sink by this exact name? `Connected: yes` means BlueZ
# has a link and says nothing about whether there is anywhere to send audio
# `[PI3-FOUND-590]`.
lempi_sink_present() {
    [ -n "${1:-}" ] || return 1
    lempi_sinks | grep -qxF "$1"
}

# A device's human name, as PipeWire will also name its sink `[PI3-AIM-020]`.
lempi_alias() {
    bluetoothctl info "$1" 2>/dev/null | sed -n 's/^[[:space:]]*Alias: //p'
}

# Every connected device that can actually play music, one address per line.
lempi_connected_speakers() {
    for _addr in $(bluetoothctl devices Connected 2>/dev/null | awk '{print $2}'); do
        bluetoothctl info "$_addr" 2>/dev/null | grep -q 'UUID: Audio Sink' &&
            printf '%s\n' "$_addr"
    done
}

# Where the player says its stream is, or empty.
lempi_routed() {
    curl -s "http://localhost:${LEMPI_PORT:-5720}/audio/sink" 2>/dev/null |
        sed -n 's/.*"sink":"\([^"]*\)".*/\1/p'
}

# Ask the player to reopen its output. The stream does not dependably follow a
# change of default sink `[PI3-WHY-020]`, so it is told explicitly.
lempi_reopen() {
    curl -s -o /dev/null -X POST \
        "http://localhost:${LEMPI_PORT:-5720}/command/reopen-output"
}

# How many channels an AFH map leaves usable. Hex parsed by hand because
# `strtonum` is a gawk extension and this appliance runs mawk, where it returns
# nothing at all rather than failing loudly.
lempi_afh_channels() {
    printf '%s' "$1" | awk '{
        h = "0123456789abcdef"
        n = 0
        for (i = 1; i <= 20; i += 2) {
            b = (index(h, substr($0, i, 1)) - 1) * 16 \
              + (index(h, substr($0, i + 1, 1)) - 1)
            for (k = 0; k < 8; k++) if (int(b / 2^k) % 2) n++
        }
        print n
    }'
}

# The same map as bytes `hcitool cmd` will accept. Octet 9's top bit is
# reserved and must be zero, so it is cleared here rather than trusted to have
# been zero in whatever was saved.
lempi_afh_bytes() {
    printf '%s' "$1" | awk '{
        h = "0123456789abcdef"
        for (i = 1; i <= 20; i += 2) {
            b = (index(h, substr($0, i, 1)) - 1) * 16 \
              + (index(h, substr($0, i + 1, 1)) - 1)
            if (i == 19) b = b % 128
            printf "0x%02x ", b
        }
    }'
}

# The map read out loud: which bands it refuses, in MHz. Bluetooth channel k
# sits at 2402 + k MHz.
lempi_afh_excluded() {
    printf '%s' "$1" | awk '{
        h = "0123456789abcdef"
        for (i = 1; i <= 20; i += 2) {
            b = (index(h, substr($0, i, 1)) - 1) * 16 \
              + (index(h, substr($0, i + 1, 1)) - 1)
            for (k = 0; k < 8; k++) ch[(i - 1) / 2 * 8 + k] = int(b / 2^k) % 2
        }
        start = -1
        for (c = 0; c < 79; c++) {
            if (!ch[c] && start < 0) start = c
            if ((ch[c] || c == 78) && start >= 0) {
                last = ch[c] ? c - 1 : c
                printf "  excluded ch %d-%d = %d-%d MHz\n", start, last, 2402 + start, 2402 + last
                start = -1
            }
        }
    }'
}
