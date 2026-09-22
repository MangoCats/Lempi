# SPEC034: Wi-Fi Configuration and Access-Point Mode

**Implemented 2026-09-05**, on the Raspberry Pi appliance's real network
stack. Supersedes the mechanism (not the principles) of `[PI-SET-010..037]`
in `LempiPi/PI001-image-and-partitions.md` §5b — see the correction note
there.

## 1. Why, and what changed from the original design

Moving a Pi appliance into a new home meant either a keyboard and monitor
it doesn't have, or re-flashing the SD card. PI001 §5b designed this
problem once already, assuming hand-written `hostapd.conf`/
`wpa_supplicant.conf` files on specific partitions. Checked live before
building anything, not assumed: the real appliance runs Debian Bookworm's
actual default, **NetworkManager**, and `nmcli` is already the right tool
— so this is built on NetworkManager's own connection profiles, not
PI001's original file-based mechanism. The safety principles PI001 already
established (confirm-or-revert, a published non-secret AP password, a
closed-vocabulary privileged helper) carry over unchanged.

## 2. The confirm-or-revert safety mechanism

**`[SPEC-WIFI-010]` Switching the client network, or toggling access-point
mode, can and often does cut the very connection carrying the request
that asked for it** — one Wi-Fi radio, one job at a time, confirmed live
(AP and client mode are not concurrent on this chip). Two independent
layers:

1. **A hard, OS-level, unconditional revert**, scheduled via
   `systemd-run --unit=lempi-revert-<change-id> --on-active=<minutes>
   /usr/local/bin/lempi-wifi-revert <prev> <new>` *before* the change is
   ever applied — independent of Lempi's own process staying alive.
   `lempi-wifi-revert` restores exactly the connection active immediately
   before the change (or the appliance's own access point, if nothing
   was active at all), re-enabling its `autoconnect` and disabling
   (never deleting) the attempted new one.
2. **An explicit confirmation from the browser** — `POST /wifi/confirm/
   :change_id` cancels the scheduled revert via `systemctl stop
   lempi-revert-<change-id>.timer`. A page having loaded proves nothing
   about current reachability (it could be served from cache); this must
   be a live round trip the appliance actually answers.

Default timeout: 5 minutes, `[PI-SET-014]`'s own "generous" figure,
overridable via `LEMPI_WIFI_REVERT_MIN`.

Both mechanics (schedule, cancel, and that a cancelled timer never fires)
were verified live against harmless test units before ever being wired to
anything that could disconnect a real session.

## 3. Known networks: NetworkManager's own connection profiles

**`[SPEC-WIFI-020]` "A list of known networks, and whether to connect by
default" is exactly what a NetworkManager connection profile already
is.** No second database:
`nmcli -m multiline -f NAME,TYPE,AUTOCONNECT,ACTIVE connection show`,
filtered to `TYPE=wifi`, is the whole "known networks" list, and
`nmcli connection modify <name> autoconnect yes|no` is the whole
"connect by default" toggle.

## 4. `http://lempi:5720/` on the appliance's own access point

**`[SPEC-WIFI-030]`** `.local`/mDNS is deliberately disabled on this appliance already (see
`SPEC032`'s guide work) and isn't reliable enough to build on regardless
(patchy Bonjour support on Android). Instead: `ap-start` gives the AP
connection a fixed static address (`ipv4.method shared`, `ipv4.addresses
10.42.0.1/24`) and writes `/etc/NetworkManager/dnsmasq-shared.d/lempi.conf`
(`address=/lempi/10.42.0.1`, and `/lempi.lan/` too for a browser that
treats a bare word as a search query) — NetworkManager's own per-connection
`dnsmasq` instance, scoped to that interface alone, reads every file in
that directory automatically. Every device joined to the appliance's own
SSID gets this resolver through ordinary DHCP; no client-side mDNS support
needed at all. Requires `dnsmasq` installed — added to `setup-appliance.sh`'s
package list (alongside `iw`, used to confirm AP-mode support), so a
future fresh appliance build acquires it automatically.

**Found live on the first real phone test, not assumed:** joining "Lempi"
successfully (DHCP lease granted, confirmed in `journalctl`) was not enough
for a phone's browser to reach `10.42.0.1:5720` at all — not a DNS
failure, since the raw IP failed identically to the hostname. The AP
segment has no uplink by design (`wlan0` *is* the AP; there is nothing to
share it with), and Android detects that and, unless told otherwise,
keeps routing actual app/browser traffic over mobile data instead of a
Wi-Fi it still shows as "connected" — confirmed live: turning mobile data
off made the same phone, on the same network, reach the page immediately.
The fix is entirely phone-side (turn off mobile data during setup, or use
the Wi-Fi network's own "connect without internet" toggle if Android
offers one instead) — nothing on the appliance can fix a client choosing
not to route through it. Worth knowing before troubleshooting the
appliance over this: if the phone associates and gets an address but
still can't load the page, this is the first thing to check, not a
regression in the dnsmasq setup above.

**Found live installing it, not assumed:** the `dnsmasq` *package* ships
its own system-wide service, enabled by default, bound to `0.0.0.0:53` —
a different job than the one it's wanted for here, and one that would
collide with it: NetworkManager's own per-connection instance needs port
53 free on the AP's own interface. `setup-appliance.sh` now `disable --now`
and `mask`s the system-wide service right after installing the package
(mirroring, in reverse, the existing `upower.service`-ships-disabled
fix-up already in that script), so only NetworkManager's own scoped
instances ever run.

## 5. The privileged helper: `lempi-btctl`'s `wifi-*`/`ap-*` verbs

**`[SPEC-WIFI-040]`** Extended, not duplicated into a second helper — the same already-
sudoers-allowed binary this session's LED and radio work already used.
New verbs: `wifi-scan`, `wifi-known`, `wifi-connect <ssid> <password>`,
`wifi-forget <name>` (refused for the active connection), `wifi-
autoconnect <name> on|off`, `wifi-confirm <change-id>`, `ap-start [ssid]
[password]` (defaults `Lempi`/`Lempi321`, `[PI-SET-030]`'s own published,
not-secret credential; password enforced ≥8 characters, never empty —
an open AP into a device with no login of its own is a different order
of exposure than a known one), `ap-stop` (returns to whichever known
network has `autoconnect` set).

`wifi-scan`/`wifi-known` deliberately do not emit this helper's usual
JSON: an SSID is arbitrary bytes chosen by whoever runs a nearby network,
and hand-escaping arbitrary content into JSON in `awk`/`printf` is a
correctness risk not worth taking. They relay `nmcli -m multiline`'s
output verbatim (framed by field name and line, not by an inline
delimiter that could collide with content) on success, signalled by exit
status; `player/src/bluetooth.rs::parse_multiline` builds the actual,
correctly-escaped JSON on the Rust side, the one place that can promise
it.

## 6. Rust and UI

`player/src/bluetooth.rs` (`wifi_scan`/`wifi_known`/`wifi_connect`/
`wifi_confirm`/`wifi_forget`/`wifi_autoconnect`/`ap_start`/`ap_stop`) →
`player/src/web/wifi.rs` (routes, ungated by `vipunen-support`, the same
posture `bluetooth.rs`'s existing routes take) → a **Wi-Fi** section in
the Lempi skin's Settings panel: current connection, known-networks list
(autoconnect toggle, forget, active badge), scan-and-connect flow, an
access-point start/stop toggle with optional SSID/password override, and
a prominent "Keep this network?" banner with a live countdown, shown the
moment any of `connect`/`ap start`/`ap stop` returns a `change_id`.

## 7. Plain `http://lempi/`, port 80, alongside `:5720`

**`[SPEC-WIFI-050]`** A phone that just joined the appliance's own SSID
still has to type `:5720` after the hostname the AP already made
resolvable in §4 — worth removing once that hostname existed at all.
`iptables` and `nftables` are both **not installed** on this appliance
(checked live, not assumed, before choosing a mechanism) — ruled out
rather than adding either as a new dependency for a one-line redirect.
Instead the player's own `axum` server binds a **second**, best-effort
listener directly on port 80, serving the identical `Router` (cloned) the
main listener on the configured port already serves, in a second spawned
task. Binding port 80 needs `CAP_NET_BIND_SERVICE`, since the process
otherwise runs unprivileged (`User=lempi`, never root) — granted via
`setcap 'cap_net_bind_service=+ep'` on the binary itself, not a
capability the process could otherwise claim on its own. `libcap2-bin`
(providing `setcap`/`getcap`) was already installed on this appliance;
nothing new to add for that part.

Failing to bind :80 is logged and never fatal — the configured port is
what everything else (the systemd unit, `install-player.sh`'s own
reachability check, every bookmark already saved) depends on, and must
never wait on or be brought down by a convenience for a phone that would
rather not type a port number. A desktop build, or a fresh appliance
before its first capability-aware deploy, is expected to simply log the
failure and carry on serving its configured port exactly as before.

**A capability is a file attribute on the specific inode, not the
binary's name or path** — it does not survive the file being replaced,
which is what every deploy does. Both `install-player.sh` (the primary
install *and* its rollback-to-`.prev` path — either can leave a freshly
written file with no capability of its own) and `setup-appliance.sh` (a
fresh appliance build, checked idempotently via `getcap` so a re-run
doesn't re-announce it) re-apply `setcap` immediately after every single
`install`.

## 8. What isn't built

A returning/reloaded browser has no way to see a *pending* confirmation
it didn't itself just trigger — the countdown is client-side state, not
persisted or queryable. This does not weaken the safety property (the
server-side revert always fires regardless), only the convenience of
confirming from a second page load. A `wifi-pending` verb surfacing
`systemctl list-timers 'lempi-revert-*'` would close this; deferred as a
real but non-blocking gap.
