# SPDX-License-Identifier: MIT
#
# Sourced, not run. What every appliance that plays to a Bluetooth speaker
# has [APP-BT-010] -- lempi02w and lp3-wifi; bose plays to its DAC and has
# none of it. Needs appliance/setup-lib.sh sourced and `setup_target` run.
#
# Until 2026-10-02 lempi02w's copy was heredocs in setup-appliance.sh and
# lp3-wifi's was files in LempiPlay3/, copied from it; they matched but for
# one start timeout, which lempi02w carried in a drop-in. Read off both
# before this was written. What stays per node: WirePlumber's configuration
# (0.4's Lua on lempi02w's bookworm, 0.5's conf on lp3-wifi's trixie), and
# lp3-wifi's radio, which sits on a UART the image saved blocked.

B=appliance/bluetooth

bluetooth_items() {
    say ""
    say "Bluetooth output (appliance/bluetooth)"

    # The audio stack and why each is there is in LempiPi/setup-appliance.sh's
    # history: PipeWire with its ALSA and Bluetooth plugins, WirePlumber,
    # upower (whose absence makes WirePlumber tear down every A2DP endpoint
    # [PI3-FOUND-030]), and the D-Bus bindings lempi-bt-agent is written in.
    for p in bluez alsa-utils pipewire pipewire-pulse pipewire-alsa wireplumber \
             libspa-0.2-bluetooth upower python3-dbus python3-gi; do
        item "package $p" "dpkg --admindir=$P/var/lib/dpkg -s $p" \
             "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $p"
    done

    # The helpers. The shared library first, which the others source: where
    # the database lives, how to read the chosen speaker, how to parse a sink
    # name [PI3-AIM-090]. The keeper and the watchdog are `.sh` here and bare
    # on the machine, as their units have always called them.
    file_item "lempi-common.sh" $B/lempi-common.sh /usr/local/lib/lempi-common.sh 644
    file_item "lempi-wait-sink" $B/lempi-wait-sink /usr/local/bin/lempi-wait-sink 755
    file_item "lempi-btctl" $B/lempi-btctl /usr/local/bin/lempi-btctl 755
    file_item "lempi-speaker" $B/lempi-speaker.sh /usr/local/bin/lempi-speaker 755
    file_item "lempi-btwatch" $B/lempi-btwatch.sh /usr/local/bin/lempi-btwatch 755
    file_item "lempi-bt-agent" $B/lempi-bt-agent /usr/local/bin/lempi-bt-agent 755

    # The privileged helper's one rule [PI-SET-030]: the web process gets these
    # verbs and no more. lp3-wifi had none until 2026-10-02 -- pi's blanket
    # rule covered it, which is why nothing failed. A malformed sudoers file
    # can take sudo away altogether, so it is validated before it is placed.
    file_item "sudoers: lempi-btctl" $B/lempi-btctl.sudoers /etc/sudoers.d/lempi-btctl 440 visudo

    # The keeper, the watchdog and the agent [PI3-FOUND-090], [PI3-FOUND-750],
    # [PI3-FOUND-630]; /run/lempi for the agent; and the keeper's and the
    # watchdog's routine ticks kept out of the journal [PI3-FOUND-760], which
    # lp3-wifi lacked.
    for u in lempi-speaker.service lempi-speaker.timer lempi-btwatch.service \
             lempi-btwatch.timer lempi-bt-agent.service; do
        file_item "$u" $B/$u /etc/systemd/system/$u 644
    done
    file_item "/run/lempi for the agent" $B/lempi-tmpfiles.conf /etc/tmpfiles.d/lempi.conf 644
    for s in lempi-speaker lempi-btwatch; do
        file_item "$s: routine ticks kept quiet" $B/lempi-quiet-tick.conf \
            /etc/systemd/system/$s.service.d/10-lempi-quiet.conf 644
    done
    # lempi02w's drop-in for the keeper's start timeout: the unit carries it.
    retired_item "lempi-speaker: timeout.conf drop-in (in the unit now)" \
        /etc/systemd/system/lempi-speaker.service.d/timeout.conf

    item "bluetooth enabled" "test -L $P/etc/systemd/system/bluetooth.target.wants/bluetooth.service" \
         "sudo systemctl enable bluetooth.service"
    # upower ships wanted only by graphical.target, never reached here
    # [PI3-FOUND-030]; multi-user.target is told to want it.
    item "upower wanted by multi-user.target" \
         "test -L $P/etc/systemd/system/multi-user.target.wants/upower.service" \
         "sudo systemctl enable upower.service && sudo systemctl add-wants multi-user.target upower.service"
    item "lempi-bt-agent enabled" "test -L $P/etc/systemd/system/multi-user.target.wants/lempi-bt-agent.service" \
         "sudo systemctl enable lempi-bt-agent.service"
    for t in lempi-speaker lempi-btwatch; do
        item "$t.timer enabled" "test -L $P/etc/systemd/system/timers.target.wants/$t.timer" \
             "sudo systemctl enable $t.timer"
    done

    # pi's PipeWire session, kept without a login: the player and the keeper
    # both reach it as pi.
    item "linger for pi" "test -f $P/var/lib/systemd/linger/pi" "sudo loginctl enable-linger pi"

    # The clock: 44.1 kHz and the quantum, in one file [PI2-RATE-010],
    # [PI3-FOUND-190]. lempi02w's two files for the same five values retire.
    file_item "PipeWire: 44.1 kHz and the quantum" $B/pipewire-lempi.conf \
        /etc/pipewire/pipewire.conf.d/10-lempi.conf 644
    retired_item "PipeWire: lempi02w's 10-lempi-quantum.conf (in 10-lempi.conf now)" \
        /etc/pipewire/pipewire.conf.d/10-lempi-quantum.conf
    # Live, not under $P: on an overlay node /home/pi is STATE.
    state_item "PipeWire: pi's own 10-rate.conf retired (in 10-lempi.conf now)" \
         "! test -e /home/pi/.config/pipewire/pipewire.conf.d/10-rate.conf" \
         "rm -f /home/pi/.config/pipewire/pipewire.conf.d/10-rate.conf" "retired, still present"
}
