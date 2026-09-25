# PI020: The Appliance's Diagnostic Tools

**Appliance Record — what to switch on when something needs measuring**

Split from [PI010](PI010-startup-time-and-stutter.md) on 2026-09-10, which had reached 687 lines against `[GOV-DOC-010]`'s 300-line limit. This is a **current-state catalogue**: what each tool does, what
it costs, and how to turn it on. The investigations that produced them are
in [PI011](PI011-two-speakers-and-placement.md) and its siblings, per
`[GOV-DOC-050]`.

> **Related:** [PI010](PI010-startup-time-and-stutter.md) for the startup investigation these came out of · [PI011](PI011-two-speakers-and-placement.md) for the stutter

---

## 3. Diagnostic tools, and how to switch them back on

Six tools were built during the 2026-09-08 investigation and the stutter hunt
that followed -- five instruments (`lempi-underruns`, `lempi-startup-sample`,
`lempi-hci-capture`, `lempi-linkstate`, `lempi-vitals`) and one intervention
(`lempi-afh-seed`). Four are **off by default** because they cost something to
run or because they change behaviour; all six stay installed, because the
expensive part was working out what to measure, not writing it.

**`lempi-underruns` — always available, costs nothing.** Prints the player's
own `underrun_samples`: how many samples the output ring failed to supply.
The single most useful number here, and the one that finally separated "the
player could not keep its buffer fed" from "the radio dropped packets" —
PipeWire reports zero xruns for the first case, because from its side nothing
went wrong `[PI3-FOUND-200]`. Run it twice a few seconds apart: a counter that
is still climbing is a live fault, one that has stopped is a startup
transient.

**`lempi-startup-sample` — installed, disabled.** A boot service that reads
`/proc` once a second into `/var/log/lempi-startup.log`: uptime, load, the
player's CPU, and its `read_bytes`/`write_bytes`/`rchar` — block-device
traffic separated from reads the page cache served. Built because diagnosing
over ssh perturbs what it measures `[PI3-FOUND-170]`, and it answered two
questions: that the startup stutter was not memory, swap or CPU, and that
after the first ninety seconds the decoder touches the card not at all.

**`[PI3-FOUND-240]` Its first version was a suspect in its own
measurements.** It walked every process in `/proc` on each pass to find the
player, which cost about 5% of a core, so it could not be ruled out of the
stutters it was watching for. Disabling it left them unchanged at their
steady fifteen-second spacing, which cleared it — but an instrument that has
to be alibied is a bad instrument. It now finds the player once and spends
one subprocess a second, so its cost is constant rather than periodic and
cannot produce a periodic symptom.

Still disabled by default — an idle appliance should not be paying for an
instrument nobody is reading.

    sudo systemctl enable --now lempi-startup-sample    # on
    sudo systemctl disable --now lempi-startup-sample   # off

**`lempi-hci-capture` — installed, disabled, and not yet read.** Counts what
reaches the air. `btmon` is filtered down to two line types -- the ACL data
packets handed to the controller, and the `Number of Completed Packets` flow
control coming back -- and the result is one line a second: monotonic clock,
packets out, completions in, bytes. Healthy playback measures about 75
packets/s.

It exists because nothing else here detects the symptom `[PI3-FOUND-420]`.
The underrun counter does not correlate, PipeWire reports zero xruns either
way, and a capture off the sink monitor is taken before the SBC encoder and is
always continuous. Every other instrument sits before the loss; HCI is the one
layer left between the encoder and the antenna. A dip or a gap during an
audible stutter means the packets are not getting out. A rate that stays flat
means the loss is past the controller, inside the speaker.

Bounded on purpose: a fixed window (`LEMPI_HCI_SECS`, default 180 s), grep
applied before a byte is stored, the raw stream held in tmpfs so the card sees
none of it, and the per-second aggregation done only once the window has
closed, so no analysis competes with the audio it is watching. This
investigation has been misled by its own instruments twice
`[PI3-FOUND-170]`, `[PI3-FOUND-240]`, and `btmon` is the most invasive one
yet.

**Verified on the appliance, against settled playback, 2026-09-10.** Twenty
seconds of healthy audio to the Middleton measured 75-77 packets/s at 612
bytes each, dead flat, with completions running at half the transmit rate --
dead flat. That is the baseline a stuttering boot has to be compared against,
and on the same day it was compared: a stutter train measured 61 pkt/s against
it, a 17% shortfall, ending at the second the listener said the stutters
stopped `[PI3-FOUND-430]`. The completions column has an unexplained anomaly
in that run and should not be relied on yet.

    # boot ... -- hci capture, 20s window
    # mono acl_tx completed bytes
    861.34 75 38 45900
    862.34 75 38 45900
    863.35 76 37 46512

    sudo systemctl start lempi-hci-capture              # one window, this boot
    sudo systemctl enable lempi-hci-capture             # and on the next boot
    cat /var/log/lempi-hci.log                          # after the window closes

**`lempi-linkstate` — always available, read-only, about a second.** One shot
of everything Bluetooth will say about the link and the speaker at the far end
of it: ACL role and direction as *separate* fields, the AFH hop map with a
channel count, link quality, RSSI, transmit power, supervision timeout, and
the transport's state, AVRCP volume and SBC configuration. Run it right after
a boot in each power-cycle mode and diff the two.

It exists because `lempi-hci-capture` says only whether audio is getting out,
not what the link looks like while it is not. The two fields it was built for
are `role`, which had never been read as distinct from direction, and `afh`,
which had never been read at all `[PI3-FOUND-490]`.

    sudo lempi-linkstate

**`lempi-btwatch` — installed, enabled, and it acts.** Every minute it asks
the controller one question the chip cannot answer from anybody else's cache
— `hciconfig hci0 name`, Read Local Name — because on 2026-09-18 the adapter
stopped acknowledging HCI commands and `bluetoothctl show`, `btmgmt info` and
`hcitool con` all went on answering correctly for two days `[PI3-FOUND-730]`.
After three failures in a row it reloads the controller's firmware by
unbinding and rebinding the serdev driver, then asks again — success is the
second probe answering, never the reset returning.

Silent while healthy, and it costs the audio nothing: measured across 200 s
of live playback, four runs, `underrun_samples` unchanged and the transport
still `active`. **It will not reset while audio is flowing** — a dead control
path stops anything new from starting but does not stop what is already
playing, and the repair begins by destroying that stream, so it waits for the
next silence. **It will not reboot**, ever, on its own: a watcher that
reboots can reboot in a loop. See [PI027](PI027-the-controller-wedge.md)
`[PI3-FOUND-750]`.

    sudo lempi-btwatch                              # one check, by hand
    sudo systemctl disable --now lempi-btwatch.timer   # off
    LEMPI_BTWATCH_RESET=0                           # watch, never reset

**`lempi-afh-seed` — installed, disabled, and the only other one that changes
behaviour.** Not an instrument: it hands the controller a channel
classification saved from a settled link, so a boot starts adapted instead of
learning the room again. `save` captures the live map, `show` decodes it into
MHz, `apply` writes it, `boot` applies it seven times across the window in
which the link comes up. Built to test `[PI3-FOUND-520]`, and it comes back
out if that test fails.

Everything else here only watches. This one acts, and it encodes an assumption
about one room, so it is the one to disable first when something is behaving
strangely.

    sudo lempi-afh-seed save                       # while it sounds right
    sudo systemctl enable --now lempi-afh-seed     # on
    sudo systemctl disable --now lempi-afh-seed    # off

**`[PI3-FOUND-620]` `lempi-vitals` — installed, disabled, and the answer to a
record that died with the fault.** On 2026-09-10 the appliance stopped
answering TCP for thirteen minutes while still replying to ping. The journal
simply ends mid-sequence: no I/O error, no OOM, no hung task, no panic --
because journald is userspace too and stopped with everything else
`[PI3-FOUND-610]`.

So this samples to a plain file on a fixed cadence, each line complete in
itself, and **the gap where the lines stop is as informative as the lines**:

    # mono load1 load5 run/total memavail_kB swapfree_kB iowait_d listen22 listen5720
    212.42 0.26 0.35 1/190 270192 516928 11 1 1
    217.44 0.24 0.34 1/190 271188 516928  2 1 1

`load` rising against an idle CPU means blocked rather than busy; `iowait_d` is
jiffies waiting on I/O since the last sample, which separates a stalled card
from a busy one; and `listen22`/`listen5720` going 1 to 0 while the processes
still exist would *be* the wedge, named. That last field is precisely what was
not known during the incident.

**It forks nothing.** Five small `/proc` reads per sample, in shell. The
earlier sampler walked every process each pass, cost about 5% of a core, and
had to be alibied out of the symptoms it was watching `[PI3-FOUND-240]`; this
cannot produce a periodic symptom and never needs an alibi.

Off by default and meant to be switched off for production -- it writes to the
card every ten seconds, which an appliance that plays music should not do
forever.

    sudo systemctl enable --now lempi-vitals    # on
    sudo systemctl disable --now lempi-vitals   # off
    cat /var/log/lempi-vitals.log

**Persistent journal — off, restored to `Storage=volatile`.** The appliance
ships volatile deliberately: it is power-cut on every shutdown
`[PI3-FOUND-120]` and SD writes are not free. But volatile means a power
cycle destroys the evidence of what just went wrong, which is exactly the
class of fault this machine has. Three power cycles were investigated blind
before it was turned on, and it immediately paid for itself. Turn it on for
any mystery that survives a reboot, and off again afterwards:

    sudo sed -i 's/^Storage=volatile/Storage=persistent/' /etc/systemd/journald.conf
    sudo systemctl restart systemd-journald
    # and to revert, the same substitution the other way round

It also captures the *user* journal, where WirePlumber logs live — a blind
spot named in `[PI3-FOUND-030]`'s original investigation and not closed until
now.

*Measured 2026-09-25, and not what the paragraph above says:* `lempi02w` runs
`Storage=persistent`, `SystemMaxUse=64M`, `MaxRetentionSec=1week`, set by hand
in `/etc/systemd/journald.conf` itself — no script in this repository writes
those lines. Left as found. So is its `/usr/local/bin/lempi-btwatch`
(2026-09-21 08:37), which predates the repository's copy by one change: the
`LEMPI_HCICONFIG` test override `[PI3-AIM-100]` and two comment renames. With
the variable unset the two behave the same.

**`[PI3-FOUND-760]` The journal was full of timer ticks, not of news.** On
that date 76% of its 21,327 entries were systemd's own "Starting / Finished /
Deactivated successfully" for `lempi-speaker` (every 30 s) and
`lempi-btwatch` (every minute) — three lines a run whether or not anything
happened. The journal costs about 3 KB an entry, so they, not the player,
filled the 64 MB, which held 2.7 days of the week asked for.
[`lempi-quiet-tick.conf`](lempi-quiet-tick.conf), a drop-in on both units,
sets `LogLevelMax=notice` — on systemd 252 that also silences the service
manager's lines about the unit, while failure still logs at notice, warning
and err — and `SyslogLevel=notice`, so what the scripts themselves print
survives the filter. Both halves were checked with a throwaway unit before
touching the real ones. After install: both units ran and succeeded with no
systemd lines, and the whole journal took 4 entries in five minutes against
a three-day average of about 28.

