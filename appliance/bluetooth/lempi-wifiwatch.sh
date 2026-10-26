#!/bin/sh
# Notice a frozen Wi-Fi chip, and reboot at the next pause `[PI3-FOUND-830]`.
#
# **What it watches for.** lempi02w, 2026-10-15 21:50:28, mid-way through a
# 690 MB export: `mmc1: Controller never released inhibit bit(s)`, one
# brcmfmac SDIO read failure, and then silence. The driver reported no
# disconnect and NetworkManager logged nothing, so nothing else on the node
# noticed -- the travel failover included, since it believes NetworkManager.
# It was off the network for 45 minutes while Bluetooth, on its own UART,
# played on. The same signature is open upstream with no fix
# (raspberrypi/linux#5770), and there too a reboot is what cleared it.
#
# **It froze again the same night, with a different signature.** 23:27:48,
# after an export's copy had finished: no inhibit-bit line at all, but
# `brcmf_sdio_bus_rxctl: resumed on timeout` after three firmware commands
# had timed out (`-110`) over the previous fifty minutes -- and again nothing
# from NetworkManager. So the kernel's part of the test is any SDIO or
# brcmfmac failure in this boot's log, not one exact line.
#
# **A freeze is two things at once**, because either alone is not one. Such a
# line in this boot's log: two of that night's `-110`s came while the link was
# still working, and upstream reports the inhibit line on links that recovered
# by themselves. And a router that has not answered THRESHOLD checks running
# while NetworkManager still calls wlan0 connected: a router being restarted
# does that too. Disconnected, or on the access point, is the failover's
# business and not this.
#
# **Then a reboot, at the next pause** -- the listener's choice, 2026-10-15:
# never in the middle of a song. Played all night, it waits all night, and
# says once that it is waiting. Not within MIN_UPTIME of boot, so a freeze that
# comes straight back cannot become a reboot loop.
#
# **`[PI3-FOUND-835]` And a chip still receiving is not frozen, whatever the
# router says.** lempi02w, 2026-10-26 17:56:52, mid-way through an export:
# three checks unanswered and an SDIO error somewhere in the boot's log, so it
# rebooted -- while the copy was arriving at 606 kB/s and an ssh session
# worked, read at 17:56:08 between the second miss and the third. Likeliest,
# not measured: the pings waited behind the transfer's own queue past their
# 3 s. The transfer hold had paused the music, which is the pause this
# waits for. So every check records what wlan0 has received, and a link that
# took in RX_ALIVE since the last one is busy, not frozen: the slowest export
# measured brings in about 12 MB a minute, and a frozen chip brings in nothing.
# An idle link takes in less than RX_ALIVE, so there nothing changes.
#
# A soft repair was considered and not attempted: upstream, reloading the
# driver did not bring a hung controller back, and a failed unbind leaves the
# chip in a worse state than the one being repaired.
set -u
. "${LEMPI_COMMON:-/usr/local/lib/lempi-common.sh}" 2>/dev/null ||
    . "$(dirname "$0")/lempi-common.sh"

STATE_DIR="${LEMPI_RUN_DIR:-/run/lempi}"
MISSES_FILE="$STATE_DIR/wifiwatch-misses"
SAID_WAITING="$STATE_DIR/wifiwatch-waiting"
RX_FILE="$STATE_DIR/wifiwatch-rx"
SAID_BUSY="$STATE_DIR/wifiwatch-busy"
RX_ALIVE="${LEMPI_WIFIWATCH_RX_ALIVE:-1048576}"
SYS_WLAN_RX="${SYS_WLAN_RX:-/sys/class/net/wlan0/statistics/rx_bytes}"
THRESHOLD="${LEMPI_WIFIWATCH_MISSES:-3}"
MIN_UPTIME="${LEMPI_WIFIWATCH_MIN_UPTIME:-900}"
MAY_REBOOT="${LEMPI_WIFIWATCH_REBOOT:-1}"
PROC_ROUTE="${PROC_ROUTE:-/proc/net/route}"
PROC_UPTIME="${PROC_UPTIME:-/proc/uptime}"

# The default route's gateway on wlan0, dotted, or empty. /proc/net/route
# holds it as little-endian hex; parsed by hand because this appliance runs
# mawk, which has no `strtonum` [PI3-AIM-090].
gateway() {
    awk '$1 == "wlan0" && $2 == "00000000" {
        h = "0123456789ABCDEF"; g = toupper($3)
        for (i = 7; i >= 1; i -= 2)
            o = o (o == "" ? "" : ".") ((index(h, substr(g, i, 1)) - 1) * 16 + index(h, substr(g, i + 1, 1)) - 1)
        print o; exit
    }' "$PROC_ROUTE" 2>/dev/null
}

connected() {
    nmcli -t -f DEVICE,STATE dev 2>/dev/null | grep -qx 'wlan0:connected'
}

# Both freezes' lines: the controller's own complaint, the bus giving up on a
# read, and any brcmfmac command or transfer that failed with -5 (I/O) or
# -110 (timed out).
froze() {
    [ "$(dmesg 2>/dev/null | grep -cE \
        'mmc1: Controller never released inhibit bit|brcmf_sdio_bus_rxctl: resumed on timeout|brcmf.*(failed|err)[^0-9-]*-(5|110)\b')" -gt 0 ]
}

# `yes`, `no`, or `unknown`. A player that does not answer is not playing; one
# that answers without saying -- a build older than the `playing` field
# `[PI3-AIM-130]` -- is unknown, and unknown is waited on, never rebooted under.
playing() {
    _s=$(curl -s -m 5 "http://localhost:${LEMPI_PORT:-5720}/audio/sink" 2>/dev/null)
    case "$_s" in
        *'"playing":true'*)  echo yes ;;
        *'"playing":false'*) echo no ;;
        '')                  echo no ;;
        *)                   echo unknown ;;
    esac
}

count() { cat "$MISSES_FILE" 2>/dev/null | tr -dc '0-9' | head -c 4; }

GW=$(gateway)
if [ -z "$GW" ] || ! connected; then
    rm -f "$MISSES_FILE" "$SAID_WAITING" "$SAID_BUSY" 2>/dev/null
    exit 0
fi

# What wlan0 has received since the last check `[PI3-FOUND-835]`, recorded on
# every check so it always covers one interval and never the traffic of an
# hour ago. Empty when either reading is missing, or the counter went back
# (a reboot, a reloaded driver): unknown, and unknown exempts nothing.
mkdir -p "$STATE_DIR" 2>/dev/null
RX=$(cat "$SYS_WLAN_RX" 2>/dev/null | tr -dc '0-9')
RX_LAST=$(cat "$RX_FILE" 2>/dev/null | tr -dc '0-9')
[ -n "$RX" ] && printf '%s\n' "$RX" > "$RX_FILE" 2>/dev/null
RX_GOT=""
if [ -n "$RX" ] && [ -n "$RX_LAST" ] && [ "$RX" -ge "$RX_LAST" ]; then
    RX_GOT=$(( RX - RX_LAST ))
fi

if ping -c 2 -W 3 "$GW" >/dev/null 2>&1; then
    n=$(count)
    [ "${n:-0}" -gt 0 ] 2>/dev/null &&
        echo "the router at $GW is answering again after $n failed checks"
    rm -f "$MISSES_FILE" "$SAID_WAITING" "$SAID_BUSY" 2>/dev/null
    exit 0
fi

if [ -n "$RX_GOT" ] && [ "$RX_GOT" -ge "$RX_ALIVE" ]; then
    if [ ! -e "$SAID_BUSY" ]; then
        echo "the router at $GW did not answer, but wlan0 received $(( RX_GOT / 1024 )) KiB since the last check: busy, not frozen [PI3-FOUND-835]"
        : > "$SAID_BUSY" 2>/dev/null
    fi
    rm -f "$MISSES_FILE" "$SAID_WAITING" 2>/dev/null
    exit 0
fi
rm -f "$SAID_BUSY" 2>/dev/null

n=$(count); n=$(( ${n:-0} + 1 ))
mkdir -p "$STATE_DIR" 2>/dev/null
printf '%s\n' "$n" > "$MISSES_FILE" 2>/dev/null
if [ "$n" -lt "$THRESHOLD" ]; then
    echo "the router at $GW did not answer while wlan0 is connected (check $n of $THRESHOLD)"
    exit 0
fi

# Past the threshold, every minute repeats the same facts; they are said once.
if ! froze; then
    [ "$n" -eq "$THRESHOLD" ] &&
        echo "the router at $GW has not answered $n checks, but the kernel shows no Wi-Fi freeze; not rebooting"
    exit 0
fi
[ "$n" -eq "$THRESHOLD" ] &&
    echo "the Wi-Fi chip has frozen: SDIO errors in the kernel log and no answer from $GW in $n checks [PI3-FOUND-830]"

UP=$(cut -d. -f1 "$PROC_UPTIME" 2>/dev/null || echo 0)
if [ "${UP:-0}" -lt "$MIN_UPTIME" ]; then
    [ "$n" -eq "$THRESHOLD" ] &&
        echo "up only ${UP}s, under ${MIN_UPTIME}s: not rebooting, so a freeze at boot cannot loop"
    exit 0
fi

case "$(playing)" in
    yes|unknown)
        if [ ! -e "$SAID_WAITING" ]; then
            echo "music is playing (or the player cannot say); rebooting at the next pause"
            : > "$SAID_WAITING" 2>/dev/null
        fi
        exit 0
        ;;
esac

if [ "$MAY_REBOOT" != "1" ]; then
    echo "automatic reboot is switched off (LEMPI_WIFIWATCH_REBOOT); the Wi-Fi needs a reboot"
    exit 0
fi
echo "rebooting to clear the frozen Wi-Fi chip, during a pause [PI3-FOUND-830]"
systemctl reboot
