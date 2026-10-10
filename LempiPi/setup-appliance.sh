#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Configure a Raspberry Pi Zero 2 W as a Lempi test appliance, per PI002.
#
# **Idempotent by construction.** Every step checks the state it intends to
# create and does nothing if it is already there, so this is safe to re-run
# after a partial failure, after a reboot, or simply to confirm the machine
# still matches the script. Re-running it is the supported way to find out
# what has drifted.
#
# Not the appliance of PI001: no read-only root, no three partitions, no
# overlay, no access point. Those are easier to add to a machine already known
# to play music.
#
# **Run it through LempiPi/setup-lempi02w.sh --go**, which applies what every
# appliance has (appliance/common.sh) from the development host, then stages
# this script and its files on the node and runs it there. Since 2026-10-02
# this script is the node's own part only: run alone, it leaves out the clock,
# the journal, swap and the recovery helpers.
#
# Options:
#   --speaker AA:BB:CC:DD:EE:FF   pair, trust and connect a Bluetooth sink
#   --no-boot-tune               skip config.txt changes (no reboot needed)

set -euo pipefail

SPEAKER=""
BOOT_TUNE=1
while [ $# -gt 0 ]; do
    case "$1" in
        --speaker) SPEAKER="${2:-}"; shift 2 ;;
        --no-boot-tune) BOOT_TUNE=0; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

RUN_USER="${SUDO_USER:-pi}"
RUN_UID="$(id -u "$RUN_USER")"
CHANGED=0
note() { printf '  %-46s %s\n' "$1" "$2"; }
did()  { CHANGED=1; note "$1" "CHANGED"; }
ok()   { note "$1" "ok"; }

echo "LempiPi setup — user $RUN_USER (uid $RUN_UID)"
echo

# ---------------------------------------------------------------- packages
echo "packages"
NEED=""
# pipewire-alsa is the one left out and then impossible to explain. Lempi
# reaches the sound card through cpal, which speaks ALSA; a Bluetooth speaker
# is a PipeWire sink with no ALSA device of its own. This package installs the
# plugin that routes ALSA's default PCM into PipeWire. Without it the player
# says "no audio device" beside a speaker that is paired, trusted, connected
# and visibly working for everything else -- ALSA error 524, which names
# nothing. With it the error becomes an honest "Host is down" when the link
# drops.
# upower is not optional here, whatever it looks like on a mains-powered box
# with no battery. WirePlumber asks it for a Bluetooth device's charge level,
# and when the D-Bus name has no owner it tears down and rebuilds every A2DP
# endpoint -- roughly every two and a half minutes, with the radio idle. A2DP
# does not survive its endpoints being withdrawn, so the speaker drops, and it
# sounds exactly like interference `[PI3-FOUND-030]`.
# dnsmasq and iw are for the Wi-Fi settings page `[SPEC034]`: dnsmasq is
# what NetworkManager spawns, scoped to its own interface, to answer
# `http://lempi:5720/` for anything joined to this appliance's own access
# point; iw is what confirmed live that this board's driver supports AP
# mode in the first place, and is worth keeping installed for the same
# diagnostic reason on every appliance, not just the one it was checked on.
# chrony, sqlite3 and python3 are every appliance's: appliance/common.sh
# installs them, with the fleet clock's configuration `[GDE-ECHO-300]`.
# No ffmpeg since 2026-09-27: it was here for relink's hash, which is
# in-process now `[SPEC-RLK-152]`. A node that plays through MPD gets ffmpeg's
# libraries as MPD's own dependency, not from this list.
# The Bluetooth stack above is appliance/bluetooth's since 2026-10-02
# [APP-BT-010]; the reasons stay here, where they were found. What is
# installed here is this node's own.
for p in libasound2 evtest dnsmasq iw; do
    dpkg -s "$p" >/dev/null 2>&1 || NEED="$NEED $p"
done
if [ -n "$NEED" ]; then
    apt-get update -qq
    # shellcheck disable=SC2086
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $NEED >/dev/null
    did "install:$NEED"
else
    ok "all present"
fi

# Installing the package is not enough: upower.service ships disabled and
# static, so D-Bus activation still finds no owner and the endpoint churn
# continues with the package sitting there installed. It has to be enabled.
#
# **And enabling it is not enough either `[PI3-FOUND-030]`.** `upower.service`
# ships `WantedBy=graphical.target`, and this appliance boots to
# `multi-user.target` with no display, so `enable` creates a want that is
# never reached. Measured 2026-09-08, weeks after the original fix was
# recorded as done: every boot since had come up with upower *enabled* and
# *inactive*, and WirePlumber logged `NameHasNoOwner` each time. `add-wants`
# is the verb that actually holds on a headless machine.
if [ "$(systemctl is-enabled upower 2>/dev/null)" != "enabled" ] \
   || ! systemctl is-active --quiet upower; then
    systemctl enable --now upower >/dev/null 2>&1
    did "enable upower"
else
    ok "upower running"
fi
if [ ! -e /etc/systemd/system/multi-user.target.wants/upower.service ]; then
    systemctl add-wants multi-user.target upower.service >/dev/null 2>&1
    did "upower wanted by multi-user.target (it is not, by default)"
else
    ok "upower starts at boot"
fi

# The mirror image of upower above: the `dnsmasq` PACKAGE ships its own
# system-wide service, enabled by default, bound to `0.0.0.0:53` --
# harmless in isolation, but it collides with the entirely different job
# `dnsmasq` is wanted for here `[SPEC034]`: NetworkManager's own
# per-connection instances, spawned and scoped to the access point's own
# interface alone, one of which needs port 53 free on that interface to
# answer `http://lempi:5720/` at all. Found live: installing the package
# silently left the system-wide service running and listening globally.
# `mask`, not just `disable`, so nothing -- another package, a future
# `apt upgrade` -- can silently re-enable it later.
if [ "$(systemctl is-enabled dnsmasq 2>/dev/null)" != "masked" ]; then
    systemctl disable --now dnsmasq >/dev/null 2>&1
    systemctl mask dnsmasq >/dev/null 2>&1
    did "masked the system-wide dnsmasq service"
else
    ok "dnsmasq service already masked"
fi

# ------------------------------------------------------------ audio session
# PipeWire runs as the LOGIN user, not as root and not as the lempi service
# user. Its linger, which keeps the session without a login, and its clock
# are appliance/bluetooth's since 2026-10-02; what is here is this node's
# own: WirePlumber 0.4, configured in Lua on bookworm.
echo "audio session"
sudo -u "$RUN_USER" XDG_RUNTIME_DIR="/run/user/$RUN_UID" \
    systemctl --user enable pipewire pipewire-pulse wireplumber >/dev/null 2>&1 || true
ok "user services enabled"

# `linger` keeps the audio graph alive across logouts, but it does NOT
# stop WirePlumber reacting to them. WirePlumber gates the entire BlueZ monitor
# on logind seat state, to arbitrate which of several logged-in users owns
# Bluetooth audio -- sensible on a desktop with GDM, ruinous here. Every ssh
# login and logout unregisters all nineteen A2DP endpoints, and A2DP does not
# survive that: the speaker drops the moment anyone connects to or leaves the
# box `[PI3-FOUND-040]`. There is only ever one user on an appliance.
WP_OVERRIDE=/etc/wireplumber/bluetooth.lua.d/51-lempi-no-logind.lua
if [ ! -f "$WP_OVERRIDE" ]; then
    mkdir -p "$(dirname "$WP_OVERRIDE")"
    cat > "$WP_OVERRIDE" <<'LUA'
-- Lempi: do not tie the BlueZ monitor to seat state [PI3-FOUND-040].
-- Every ssh login and logout otherwise withdraws all A2DP endpoints and
-- drops the speaker. One user, one seat, no arbitration needed.
bluez_monitor.properties["with-logind"] = false
LUA
    did "wireplumber: bluez monitor detached from seat state"
    sudo -u "$RUN_USER" XDG_RUNTIME_DIR="/run/user/$RUN_UID" \
        systemctl --user restart wireplumber >/dev/null 2>&1 || true
else
    ok "bluez monitor detached from seat state"
fi

# -------------------------------------------------------------- lempi user
# A service account with no shell and no home: it plays audio and writes one
# database. `audio` for ALSA, `bluetooth` so it may talk to BlueZ.
echo "service account"
if ! id lempi >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin lempi
    did "created user lempi"
else
    ok "user lempi"
fi
for g in audio bluetooth; do
    getent group "$g" >/dev/null || continue
    if ! id -nG lempi | tr ' ' '\n' | grep -qx "$g"; then
        usermod -aG "$g" lempi
        did "lempi -> group $g"
    fi
done

# -------------------------------------------------------------- directories
echo "directories"
for d in /srv/library /var/lempi; do
    if [ ! -d "$d" ]; then
        install -d -o lempi -g lempi -m 0755 "$d"
        did "created $d"
    else
        ok "$d"
    fi
done

# ------------------------------------------------------------------ binary
# Installed only if one was staged beside this script; the script stays useful
# for re-configuring a machine whose binary is already in place.
echo "binary"
SRC=""
for c in ./lempi /home/"$RUN_USER"/lempi; do
    [ -f "$c" ] && SRC="$c" && break
done
if [ -n "$SRC" ]; then
    if ! cmp -s "$SRC" /usr/local/bin/lempi 2>/dev/null; then
        install -m 0755 "$SRC" /usr/local/bin/lempi
        did "installed $(/usr/local/bin/lempi --version 2>/dev/null || echo lempi)"
    else
        ok "binary current"
    fi
elif [ -x /usr/local/bin/lempi ]; then
    ok "binary present (none staged)"
else
    note "binary" "ABSENT — stage ./lempi beside this script"
fi

# A capability is a file attribute tied to the specific inode `[SPEC-WIFI-050]`
# -- it does not survive the `install` above replacing the file, so this is
# re-checked and re-applied every run, not just the first. Lets the
# otherwise-unprivileged player process also bind :80, for a plain
# `http://lempi/` alongside its usual :5720.
if [ -x /usr/local/bin/lempi ]; then
    case "$(/usr/sbin/getcap /usr/local/bin/lempi 2>/dev/null)" in
        *cap_net_bind_service*) ok "binary already has cap_net_bind_service" ;;
        *)
            /usr/sbin/setcap 'cap_net_bind_service=+ep' /usr/local/bin/lempi
            did "granted cap_net_bind_service to the binary (for :80)"
            ;;
    esac
fi

# ----------------------------------------------------------------- service
echo "service"
UNIT=/etc/systemd/system/lempi.service
read -r -d '' WANT_UNIT <<'EOF' || true
[Unit]
Description=Lempi
# Deliberately NOT network-online.target: audio depends on the library and the
# sound device, never on the network [REQ-HW-010B].
# After the user's own session, where PipeWire runs [PI3-FOUND-790]: started
# ahead of it, the player finds no audio service at all, not merely no sink.
Wants=user@RUNUID.service
After=local-fs.target sound.target user@RUNUID.service

[Service]
# Wait for a real sink before starting. PipeWire always offers a "Dummy
# Output" when no hardware is present, and the ALSA bridge binds a stream to
# whichever node was default when it opened -- so a player started before the
# speaker connects plays flawlessly into the dummy for ever, reports itself
# healthy, and leaves the speaker with no audio to hold A2DP open. That is the
# disconnect-a-few-seconds-in symptom, and this is where it is fixed.
# Roll back any hot SQLite journal first [PI3-FOUND-120]. Every power-down on
# this appliance is a power cut -- the speaker supplies the Pi -- and the
# journal that leaves behind cannot be recovered by the read-only attach the
# player uses, which turns Restart=always into a crash loop. Ordered before
# the sink wait because it is instant and must happen even when that wait
# runs its full timeout.
# First, and it repairs nothing: lempi-preflight names the tools the two
# steps below depend on and reports each one's version, so a boot log says
# whether recovery was ARMED rather than leaving it to be inferred from the
# absence of a complaint [PI-PRE-010]. Always exits 0.
ExecStartPre=/usr/local/bin/lempi-preflight
ExecStartPre=/usr/local/bin/lempi-db-recover
ExecStartPre=/usr/local/bin/lempi-wait-sink
# The node's whole command line, here and nowhere else, as on bose and
# lp3-wifi. Until 2026-10-02 this line was the pre-split single-file form and
# a drop-in, mpd-guest.conf, blanked it and supplied the real one -- so a
# rebuild that lost the drop-in would have run the catalogue as the listener
# store. Both halves are named [IMPL-DBSPLIT-025], and the player now refuses
# to start without either.
#
# --mpd attaches MPD as a guest backend [SPEC-BK-020]: idle until a switch
# asks for it, and the control is offered only because the flag is here.
# **There is no After=mpd.service, on purpose.** It would hold the player
# behind MPD's ~10 s start, on a machine whose priority is audio first, for a
# backend that is idle until somebody switches to it.
ExecStart=/usr/local/bin/lempi --listener /var/lempi/listener.db --library /srv/library/library.db --port 5720 --mpd 127.0.0.1:6600 --mpd-root /srv/library/audio
Restart=always
RestartSec=2
# Runs as the LOGIN user, not a service account. PipeWire is a per-user
# session bus and its socket is owned by that user; a separate `lempi` account
# cannot reach it, which presents as ALSA "Host is down" beside a speaker that
# is paired and connected -- and, because nothing then holds the A2DP stream,
# as a speaker that connects and disconnects a few seconds later.
#
# A dedicated account is the right shape for the appliance `[PI001]`, and it
# needs PipeWire running system-wide or a shared socket to work. That is a
# decision for the appliance image, not for the machine under test.
User=RUNUSER
SupplementaryGroups=input
Nice=-5
Environment=XDG_RUNTIME_DIR=/run/user/RUNUID
Environment=PULSE_SERVER=unix:/run/user/RUNUID/pulse/native

[Install]
WantedBy=multi-user.target
EOF
WANT_UNIT="${WANT_UNIT//RUNUID/$RUN_UID}"
WANT_UNIT="${WANT_UNIT//RUNUSER/$RUN_USER}"
if [ ! -f "$UNIT" ] || [ "$(cat "$UNIT")" != "$WANT_UNIT" ]; then
    printf '%s\n' "$WANT_UNIT" > "$UNIT"
    systemctl daemon-reload
    did "wrote lempi.service"
else
    ok "lempi.service"
fi
if ! systemctl is-enabled --quiet lempi 2>/dev/null; then
    systemctl enable lempi >/dev/null 2>&1 && did "enabled lempi" || note "enable lempi" "deferred"
else
    ok "enabled"
fi

# ----------------------------------------------------------------- helpers
# This node's own instruments. The Bluetooth helpers, the shared shell
# library and lempi-btctl's sudoers rule are appliance/bluetooth's since
# 2026-10-02 [APP-BT-010], and lempi-preflight and lempi-db-recover are
# appliance/common.sh's.
echo "helpers"
HERE="$(cd "$(dirname "$0")" && pwd)"
for f in lempi-underruns lempi-led-boot \
         lempi-wifi-revert lempi-wifi-failover lempi-radio-test lempi-startup-sample \
         lempi-hci-capture lempi-linkstate lempi-afh-seed lempi-vitals; do
    if [ -f "$HERE/$f" ]; then
        if ! cmp -s "$HERE/$f" "/usr/local/bin/$f"; then
            install -m755 "$HERE/$f" "/usr/local/bin/$f" && did "installed $f"
        else
            ok "$f current"
        fi
    elif [ -x "/usr/local/bin/$f" ]; then
        ok "$f present (none staged)"
    else
        note "$f" "ABSENT — stage it beside this script"
    fi
done

# ------------------------------------------------------------------ rocker
# Native AVRCP transport control handling is integrated directly into the
# Lempi player binary [IMPL-BT-010], replacing the standalone lempi-rocker.
if [ -e /usr/local/bin/lempi-rocker ]; then
    rm -f /usr/local/bin/lempi-rocker && did "retired lempi-rocker"
fi

# The node's own units, written here: its diagnostics and its experiment.
# The keeper, the watchdog and the agent are appliance/bluetooth's.
install_unit() {   # install_unit <name> <<'EOF' ... EOF
    local name="$1" tmp
    tmp="$(mktemp)"
    cat > "$tmp"
    if ! cmp -s "$tmp" "/etc/systemd/system/$name"; then
        install -m644 "$tmp" "/etc/systemd/system/$name" && did "unit $name"
        NEED_RELOAD=1
    else
        ok "unit $name"
    fi
    rm -f "$tmp"
}
# Diagnostic, installed but NOT enabled: it costs a subprocess a second and an
# idle appliance should not pay for an instrument nobody is reading
# `[PI3-FOUND-240]`. Turn it on when something needs measuring.
install_unit lempi-startup-sample.service <<'EOF'
[Unit]
Description=Sample load and the player's disk reads after boot (diagnostic)
After=lempi.service
[Service]
Type=simple
ExecStart=/usr/local/bin/lempi-startup-sample
Nice=19
IOSchedulingClass=idle
[Install]
WantedBy=multi-user.target
EOF

# Diagnostic, installed but NOT enabled `[PI3-FOUND-610]`. Samples vital signs
# to a file rather than the journal, because on 2026-09-10 the journal was the
# record that died first: the appliance stopped answering TCP for thirteen
# minutes while still replying to ping, and journald stopped with the rest of
# userspace. Forks nothing; five /proc reads per sample.
install_unit lempi-vitals.service <<'EOF'
[Unit]
Description=Sample vital signs to a file that survives a wedge (diagnostic)
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/local/bin/lempi-vitals
Nice=19
IOSchedulingClass=idle
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# Experiment, installed and NOT enabled `[PI3-FOUND-520]`. Hands the
# controller a channel classification saved from a settled link, so a mode A
# boot starts adapted instead of learning this room again from scratch. It is
# a claim about one room: seeded somewhere else, or after the interference
# moves, it excludes channels that were fine. Turn it on for the experiment,
# and off again if the experiment fails.
install_unit lempi-afh-seed.service <<'EOF'
[Unit]
Description=Seed the controller with this room's channel classification (experiment)
After=bluetooth.service
Wants=bluetooth.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/lempi-afh-seed boot
# Seven passes at ten-second intervals is about a minute by design, so this
# clears it comfortably. Finite because `Type=oneshot` defaults to infinity
# `[PI3-FOUND-670]`, and this one talks to `hcitool`.
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOF

# Diagnostic, installed but NOT enabled: it costs a `btmon` for its window and
# is meant to be started by hand for one boot. It is root because `btmon` needs
# the management socket, and it is the only instrument that sees the layer
# between the SBC encoder and the antenna `[PI3-FOUND-420]`.
install_unit lempi-hci-capture.service <<'EOF'
[Unit]
Description=Count HCI audio packets reaching the air (diagnostic)
After=bluetooth.target

[Service]
Type=simple
ExecStart=/usr/local/bin/lempi-hci-capture
Nice=10

[Install]
WantedBy=multi-user.target
EOF

# Retired 2026-10-02: the drop-in that carried the node's real command line,
# and the copy kept beside it at the CLI migration. The base unit carries the
# line now; left in place, the drop-in would go on overriding it.
for old in /etc/systemd/system/lempi.service.d/mpd-guest.conf \
           /etc/systemd/system/lempi.service.d/mpd-guest.conf.pre-cli-migration; do
    if [ -e "$old" ]; then
        rm -f "$old" && did "removed retired $(basename "$old")"
        NEED_RELOAD=1
    fi
done

# Drop-ins and card tuning, all staged beside this script.
for pair in \
    "lempi-io-priority.conf:/etc/systemd/system/lempi.service.d/20-lempi-io.conf" \
    "mpd-polite.conf:/etc/systemd/system/mpd.service.d/10-lempi-polite.conf" \
    "sd-tuning.conf:/etc/tmpfiles.d/lempi-readahead.conf" ; do
    src="$HERE/${pair%%:*}"; dst="${pair#*:}"
    [ -f "$src" ] || { note "${pair%%:*}" "ABSENT — stage it beside this script"; continue; }
    mkdir -p "$(dirname "$dst")"
    if ! cmp -s "$src" "$dst"; then
        install -m644 "$src" "$dst" && did "installed $(basename "$dst")"
        NEED_RELOAD=1
    else
        ok "$(basename "$dst") current"
    fi
done

# `bfq` and the readahead take effect through tmpfiles at boot; apply them now
# so a fresh install does not need a reboot to behave like a settled one.
systemd-tmpfiles --create /etc/tmpfiles.d/lempi-readahead.conf >/dev/null 2>&1 || true

[ "${NEED_RELOAD:-0}" = 1 ] && systemctl daemon-reload

# ---------------------------------------------------------------- act led
# The green ACT LED, under listener control [PI3-LED-010].
#
# This used to hard-code the LED to track the Wi-Fi radio (rfkill1 is
# phy0) -- a real, deliberate, still-available choice ([PI3-ROCKER-020] has
# playback take Wi-Fi down deliberately, and an unreachable appliance
# otherwise just looks broken), but a hard-coded one, unreachable from the
# settings panel. It is now one of four modes a listener picks
# (`on`/`wifi`/`off`/`default`), stored in `player_settings` the same way
# the chosen speaker is, and reapplied at every boot by `lempi-led-boot`
# (staged and installed above, "bluetooth helper") -- /sys does not survive
# a reboot on its own, hence a unit rather than a one-off write.
#
# This section only ever ensures the *mechanism* exists and is enabled. It
# never forces a mode: the stored setting is the source of truth, and
# `lempi-led-boot` already defaults sensibly (solid on) when nothing has
# been chosen yet, including on a brand-new appliance with no library yet.
echo "act led"
if [ ! -d /sys/class/leds/ACT ]; then
    note "ACT led" "absent on this board"
else
    cat > /etc/systemd/system/lempi-led.service <<'UNIT'
[Unit]
Description=Apply the stored Lempi LED preference at boot
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/lempi-led-boot
# Short work, but a oneshot without a ceiling is a boot that can hang forever
# `[PI3-FOUND-670]`.
TimeoutStartSec=30

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    if [ "$(systemctl is-enabled lempi-led 2>/dev/null)" != "enabled" ]; then
        systemctl enable --now lempi-led >/dev/null 2>&1 && did "led unit enabled"
    else
        ok "led unit enabled"
    fi
fi

# ---------------------------------------------------------------- platform
# The Wi-Fi country, pi's SSH key, root's ownership of /etc/ssh and /var/log,
# swap, the fleet clock, the timezone and the journal are every appliance's,
# and since 2026-10-02 are appliance/common.sh's, which
# LempiPi/setup-lempi02w.sh runs from the development host. They were here
# until then, and each fix to one of them was made once per node.

# -------------------------------------------------------------- boot tuning
# Safe, reversible settings only. The riskier work -- initramfs trimming, unit
# parallelisation -- waits for a boot-time baseline.
if [ "$BOOT_TUNE" -eq 1 ]; then
    echo "boot tuning (needs a reboot to take effect)"
    CFG=/boot/firmware/config.txt
    [ -f "$CFG" ] || CFG=/boot/config.txt
    add_cfg() {
        if ! grep -qxF "$1" "$CFG"; then
            printf '%s\n' "$1" >> "$CFG"
            did "config.txt: $1"
        else
            ok "config.txt: $1"
        fi
    }
    # 16 MB to the GPU on a machine with no display. Measured 416 MB usable of
    # 512 before this; the split is the largest single reclaim available.
    add_cfg "gpu_mem=16"
    add_cfg "disable_splash=1"
    add_cfg "boot_delay=0"
    add_cfg "dtoverlay=disable-bt-led"

    for svc in triggerhappy avahi-daemon ModemManager; do
        if systemctl list-unit-files "$svc.service" >/dev/null 2>&1 \
           && systemctl is-enabled --quiet "$svc" 2>/dev/null; then
            systemctl disable --now "$svc" >/dev/null 2>&1
            did "disabled $svc"
        fi
    done
fi

# ---------------------------------------------------------------- bluetooth
if [ -n "$SPEAKER" ]; then
    echo "bluetooth $SPEAKER"
    systemctl is-active --quiet bluetooth || systemctl start bluetooth

    # Unblock the radio through sysfs, not through `rfkill`.
    #
    # This line was `rfkill unblock bluetooth 2>/dev/null || true`, and on this
    # image `rfkill` is not installed -- so it reported nothing, changed
    # nothing, and was written so that it could never say so. Measured
    # 2026-08-20: hci0 sat soft-blocked, `bluetoothctl power on` answered
    # `org.bluez.Error.Failed`, and the settings screen's Connect button did
    # nothing at all, because there was no radio to connect through.
    #
    # sysfs is always present, needs no package, and the state persists across
    # reboots via systemd-rfkill -- so clearing it here fixes the next boot too.
    for r in /sys/class/rfkill/rfkill*; do
        [ -e "$r/type" ] || continue
        [ "$(cat "$r/type")" = bluetooth ] || continue
        if [ "$(cat "$r/soft" 2>/dev/null)" = 1 ]; then
            if echo 0 > "$r/soft" 2>/dev/null; then
                did "unblocked bluetooth radio ($(cat "$r/name" 2>/dev/null))"
            else
                note "could NOT unblock $(cat "$r/name" 2>/dev/null)" "FAILED"
            fi
        fi
    done

    bt_is() { bluetoothctl info "$SPEAKER" 2>/dev/null | grep -q "$1: yes"; }

    # A device can be trusted but not paired -- BlueZ keeps `trust` as a
    # standalone policy, so a pairing that failed or was later dropped leaves
    # a half-state that looks reassuring and cannot connect. Clear it before
    # trying again, or `pair` fails against the stale record for ever.
    if ! bt_is Paired && bluetoothctl info "$SPEAKER" >/dev/null 2>&1; then
        bluetoothctl remove "$SPEAKER" >/dev/null 2>&1 || true
        did "cleared stale record (trusted but not paired)"
    fi

    if bt_is Paired; then
        ok "paired"
    else
        # An AGENT must be registered or nothing answers the pairing
        # request: `pair` returns, the device never completes, and BlueZ is
        # left holding a trust policy for a pairing that does not exist.
        #
        # Fed as discrete commands with pauses, NOT as one heredoc. A heredoc
        # delivers everything before bluetoothctl has finished connecting to
        # bluetoothd, and the log then reads "Failed to register agent object"
        # followed by "Agent registered" arriving after the pair attempt has
        # already failed. NoInputNoOutput is right for a headless box: it
        # accepts the "just works" pairing a speaker offers.
        {
            printf 'power on
';           sleep 2
            printf 'agent NoInputNoOutput
'; sleep 1
            printf 'default-agent
';      sleep 1
            printf 'scan on
';            sleep 20
            printf 'pair %s
' "$SPEAKER"; sleep 12
            printf 'scan off
quit
'
        } | bluetoothctl >/dev/null 2>&1 || true
        # The RESULT is checked, not the exit code: `bluetoothctl pair` reports
        # success for a pairing that does not persist, which is how this script
        # once announced "paired CHANGED" for a device left unpaired.
        if bt_is Paired; then
            did "paired"
        else
            note "pair" "FAILED — hold the speaker's Bluetooth button until it flashes, then re-run"
        fi
    fi

    # `trust` is the step people miss: without it the speaker pairs, works,
    # and never reconnects after a reboot. [PI2-BT-010] Only meaningful once
    # paired, so it is not claimed before that.
    if bt_is Paired; then
        if bt_is Trusted; then ok "trusted"
        else bluetoothctl trust "$SPEAKER" >/dev/null 2>&1 && did "trusted" || true
        fi
        if bt_is Connected; then
            ok "connected"
        else
            bluetoothctl connect "$SPEAKER" >/dev/null 2>&1 || true
            bt_is Connected && did "connected"                 || note "connect" "not connected — is the speaker powered on?"
        fi
    fi

    # The sink is what the player actually needs; pairing is only the means.
    if sudo -u "$RUN_USER" XDG_RUNTIME_DIR="/run/user/$RUN_UID"          pactl list short sinks 2>/dev/null | grep -qi bluez; then
        ok "PipeWire sink present"
    else
        note "sink" "no bluez sink yet — the player will report no audio device"
    fi
fi

echo
if [ "$CHANGED" -eq 0 ]; then
    echo "No changes: the machine already matches this script."
else
    echo "Done. Re-run to confirm it settles with no further changes."
    # An `if`, not `[ ] && echo`: as the script's last command, a false test
    # made its exit status 1, and under --no-boot-tune a run that changed
    # anything reported itself failed -- found 2026-10-02 by
    # setup-lempi02w.sh --go, the first caller to read the status.
    if [ "$BOOT_TUNE" -eq 1 ]; then
        echo "A reboot is needed for the config.txt changes."
    fi
fi
exit 0
