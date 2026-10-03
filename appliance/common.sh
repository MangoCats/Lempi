# SPDX-License-Identifier: MIT
#
# Sourced, not run. What every appliance has, whatever else it is
# [APP-SET-020]: one list, so a fix made here reaches all three. Before
# 2026-10-02 each node's script carried its own copy, and the fixes of one
# fortnight -- sqlite3 on lp3-wifi, the timezone, the journal -- were each
# made once per node, or found missing on the node nobody had touched.
#
# Read off all three on 2026-10-02 before it was written: everything here
# was already true of at least one node, and is now the record for all of
# them. Needs setup-lib.sh sourced and `setup_target` run, for P, CODENAME
# and MODE.

TIMEZONE="${TIMEZONE:-America/New_York}"
WIFI_COUNTRY="${WIFI_COUNTRY:-US}"

# common_items -- all of it, for a node already standing. A script building a
# new card calls the two halves itself: the base before STATE is populated
# (pi's key must exist before /home/pi is copied there), the data items after
# the binds are up, since they are about what lives on STATE and LIBRARY.
common_items() {
    common_base_items
    common_data_items
}

common_base_items() {
    say ""
    say "common to every appliance (appliance/common.sh)"

    # chrony for the fleet clock [GDE-ECHO-300]; python3 for lempi-db-recover
    # [PI-PRE-030]; sqlite3 for Vipunen's remote tooling (`ssh <node> sqlite3
    # -json ...`), missing on lp3-wifi until 2026-10-02 [BOS-IMG-020].
    for p in chrony python3 sqlite3; do
        item "package $p" "dpkg --admindir=$P/var/lib/dpkg -s $p" \
             "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $p"
    done

    # The radio's regulatory country, which the imager writes to cmdline.txt
    # and raspi-config owns; stated so the script is the record [PI3-FOUND-770].
    item "Wi-Fi country $WIFI_COUNTRY" \
         "grep -q 'cfg80211.ieee80211_regdom=$WIFI_COUNTRY' /boot/firmware/cmdline.txt" \
         "sudo raspi-config nonint do_wifi_country $WIFI_COUNTRY"

    # The household's zone. lp3-wifi came up in Europe/London and recorded
    # +60 minutes where the others recorded -240 [FLT-ISS-030]; lempi02w's
    # script never set it at all.
    item "timezone $TIMEZONE" \
         "test \"\$(readlink $P/etc/localtime)\" = /usr/share/zoneinfo/$TIMEZONE && test \"\$(cat $P/etc/timezone)\" = $TIMEZONE" \
         "sudo timedatectl set-timezone $TIMEZONE && echo $TIMEZONE | sudo tee /etc/timezone"

    # pi's own SSH key, for reaching other machines -- not the host keys. The
    # maintainer asked that every node's script make one (2026-09-25). Made
    # once, never replaced: a new key would silently undo wherever the old
    # one was authorised. Checked live: on an overlay node /home/pi is STATE.
    state_item "pi's ed25519 SSH key" "test -f /home/pi/.ssh/id_ed25519 && test -f /home/pi/.ssh/id_ed25519.pub" \
         "ssh-keygen -q -t ed25519 -N '' -C pi@\$(hostname) -f /home/pi/.ssh/id_ed25519"

    # The fleet clock [GDE-ECHO-300]: the fleet's own reference, rendered here
    # from the template and `fleet/targets.env` [SPEC-FCP-030], and Debian's
    # pool left on as one more fallback every node shares (the maintainer's
    # choice, 2026-09-25).
    local sources
    sources=$(mktemp)
    build/render-fleet-sources.sh "$sources" >/dev/null || die "the fleet sources could not be rendered"
    file_item "chrony fleet sources [GDE-ECHO-300]" "$sources" \
        /etc/chrony/sources.d/lempi-fleet.sources 644
    rm -f "$sources"
    item "chrony distribution pool on" \
         "grep -q '^pool 2\.debian\.pool\.ntp\.org' $P/etc/chrony/chrony.conf" \
         "sudo sed -i 's/^#.*\(pool 2\.debian\.pool\.ntp\.org\)/\1/' /etc/chrony/chrony.conf"
    item "chrony enabled" "test -L $P/etc/systemd/system/multi-user.target.wants/chrony.service" \
         "sudo systemctl enable chrony.service"

    # The journal: kept, and bounded [PI3-FOUND-770]. One file since
    # 2026-10-02, where lempi02w had one and the other two another, each
    # carrying half of the bounds. **Named `lempi.conf` for its sort order**:
    # drop-ins apply by filename, and trixie's image ships
    # `40-rpi-volatile-storage.conf` -- a `10-` name sorts before it and its
    # `Storage=volatile` wins. lempi02w's `10-lempi.conf` (bookworm has no
    # such file) is the one retired, or both would take effect.
    file_item "journal: kept, bounded" appliance/journald-lempi.conf \
        /etc/systemd/journald.conf.d/lempi.conf 644
    retired_item "journal: lempi02w's old name, 10-lempi.conf" /etc/systemd/journald.conf.d/10-lempi.conf
    # journald reads its configuration only when it starts, and daemon-reload
    # does not reach it; the restart is also what creates /var/log/journal on
    # a fresh card. Checked live: on an overlay node /var/log is STATE.
    item "journal: persistent now" "test -d /var/log/journal" \
         "sudo systemctl restart systemd-journald"

    # Swap, by what the OS offers. trixie's rpi-swap, zram only
    # [IMPL-BOS-170]; bookworm has no rpi-swap, and lempi02w keeps the 512 MB
    # file dphys-swapfile gives it, recorded as it is [SD-RISK-150].
    if [ "$CODENAME" = bookworm ]; then
        item "swap: 512 MB (dphys-swapfile, bookworm)" \
             "grep -qx 'CONF_SWAPSIZE=512' $P/etc/dphys-swapfile" \
             "sudo sed -i 's/^#\{0,1\}CONF_SWAPSIZE=.*/CONF_SWAPSIZE=512/' /etc/dphys-swapfile && sudo dphys-swapfile setup && sudo dphys-swapfile swapon"
    else
        file_item "swap: zram, 1x RAM, <= 2 GiB" appliance/rpi-swap-lempi.conf \
            /etc/rpi/swap.conf.d/10-lempi.conf 644
    fi

    # cloud-init runs the imager's datasource on every boot unless told not
    # to. lp3-wifi was told on 2026-09-08; bose never was; lempi02w's image
    # had none.
    item "cloud-init absent or disabled" \
         "! dpkg --admindir=$P/var/lib/dpkg -s cloud-init || test -e $P/etc/cloud/cloud-init.disabled" \
         "sudo touch /etc/cloud/cloud-init.disabled"

    # The two steps before every player start: a report of the tools recovery
    # depends on [PI-PRE-010], and the hot-journal rollback that keeps a power
    # cut from becoming a crash loop [PI3-FOUND-120].
    file_item "lempi-preflight" appliance/lempi-preflight /usr/local/bin/lempi-preflight 755
    file_item "lempi-db-recover" appliance/lempi-db-recover /usr/local/bin/lempi-db-recover 755
}

common_data_items() {
    # Root's files stay root's. bose's provisioning once handed pi its whole
    # /etc/ssh and /var/log with a recursive chown (fixed 2026-09-25).
    state_item "/etc/ssh all owned by root" "test -z \"\$(sudo find /etc/ssh -not -user root)\"" \
         "sudo chown -R root:root /etc/ssh"
    state_item "/var/log owned by root" "test \"\$(stat -c %U /var/log)\" = root" \
         "sudo chown root:root /var/log"
    # Every sudoers drop-in root's and 440, whatever it is called -- the
    # image names pi's `010_pi-nopasswd` on bookworm and `010-pi-nopasswd`
    # where it was typed by hand, which on bose came out 644 [IMPL-BOS-090b].
    item "sudoers drop-ins all root, mode 440" \
         "test -z \"\$(sudo find $P/etc/sudoers.d -type f \\( -not -user root -o -not -perm 440 \\))\"" \
         "sudo find /etc/sudoers.d -type f -exec chown root:root {} + -exec chmod 440 {} +"

    # The two data trees: pi's, and nothing in them writable by everyone. A
    # copy from a Windows drive arrives 0777 under `rsync -a`, which is how two
    # libraries ended up (2026-09-25); symlinks and sticky directories excepted.
    state_item "/var/lempi owned by pi" "test \"\$(stat -c %U /var/lempi)\" = pi" "sudo chown pi:pi /var/lempi"
    state_item "/srv/library owned by pi" "test \"\$(stat -c %U /srv/library)\" = pi" "sudo chown pi:pi /srv/library"
    state_item "nothing world-writable on /var/lempi or /srv/library" \
         "test -z \"\$(sudo find /var/lempi /srv/library -xdev -perm -0002 ! -type l ! -perm -1000 -print -quit)\"" \
         "sudo find /var/lempi /srv/library -xdev -type d -perm -0002 ! -perm -1000 -exec chmod 755 {} + ; sudo find /var/lempi /srv/library -xdev -type f -perm -0002 -exec chmod 644 {} +"
}
