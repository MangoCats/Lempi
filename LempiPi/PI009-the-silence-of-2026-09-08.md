# PI009: The Silence of 2026-09-08

**Appliance Record — a speaker that was connected, healthy, and inaudible**

Split from [PI004](PI004-speaker-operation.md) on 2026-09-08, which had reached
1,112 lines against `[GOV-DOC-010]`'s 300-line limit. PI004 remains the
operating record — what the speaker link does and how to work it. This and its
two siblings are the dated investigation behind it, an experiment record in the
sense of `[GOV-DOC-010]`'s `LOG` class: approach, measured result, and why each
line of enquiry ended.

**Read the marks.** Several findings here were later overturned; each says so
where it stands. A paragraph is evidence of what was believed on a date.

> **Related:** [PI004](PI004-speaker-operation.md) §0 for current understanding ·
> [PI010](PI010-startup-time-and-stutter.md) and
> [PI011](PI011-two-speakers-and-placement.md) continue this investigation ·
> [PI003](PI003-choosing-a-speaker.md) for the design

---

## 1. The silence of 2026-09-08, and what it was not

**`[PI3-FOUND-065]` The reported fault was "lempi02w cannot connect to
Middleton." Bluetooth was working the entire time.** Established by
measurement rather than by asking the stack whether it was happy: BlueZ's
own `MediaTransport1.State` read `active`, `hcitool con` showed an
authenticated encrypted ACL link, the PipeWire sink and the player's stream
were both in state `running` with their links `active`, and — the decisive
one — ten seconds captured off the sink's monitor with `pw-record` contained
real, non-silent music.

That last technique is the useful residue of this episode. Every layer in
this stack can be asked "are you all right?" and every layer will say yes;
the monitor capture asks instead "what did you actually send?", and it is
the only question whose answer cannot be a stale property:

```sh
pw-record --target <sink-id> -P '{ stream.capture.sink=true }' \
          --rate 44100 --channels 2 --format s16 /tmp/cap.wav
# then measure: rms/peak in dBFS. Music sits near -18 rms / -2 peak.
```

**`[PI3-FOUND-066]` Retired: the "split-brain" theory.** A previous pass read
`api.bluez5.connection = "disconnected"` and `bluez5.profile = "off"` out of
`wpctl inspect` on the bluez5 *device* node, saw them contradict BlueZ's own
`Connected: yes`, and concluded the sink was a correctly-named phantom with
no live profile behind it. **Those two properties are set when WirePlumber
creates the device object and are not maintained afterwards.** They contradict
live state routinely, on a perfectly working link, and they are not evidence
of anything. Recorded here because the theory was coherent, fitted the
symptom, and was wrong — and because the next person to run `wpctl inspect`
on a silent speaker will see exactly the same two lines.

**`[PI3-FOUND-090]` The speaker powers the Pi, so they can only power up
together — and the Pi always loses the race.** lempi02w is fed from the
Middleton's own USB port. Switching the speaker off cuts power to the
appliance; switching it on starts a boot. The speaker is connectable within
about two seconds and the Pi needs some thirty to reach a working Bluetooth
stack, so any already-awake phone in the room takes the speaker first, every
time. A speaker that is carrying another device's connection stops answering
pages altogether: the Pi's attempt returns `br-connection-page-timeout`,
which is indistinguishable from the speaker being switched off, and the old
one-attempt-per-tick keeper duly concluded exactly that. **This is the normal
state at every power-up on this appliance, not an exceptional one**, which is
what makes a single attempt the wrong policy — see `[PI3-AIM-060]` for what
replaced it, and why the replacement is still timid whenever audio is
actually playing.

**`[PI3-FOUND-110]` `lempi-wait-sink` never held, on any machine, in any
state.** Its first condition was

```sh
wpctl status | sed -n '/Sinks:/,/^ *├─ [A-Z]/p' | grep -qv 'Dummy Output'
```

read as "a sink that is not the dummy exists". `grep -v` succeeds when *any*
line fails to match, and the block it is handed opens with the `├─ Sinks:`
header, so the condition is true unconditionally. The gate reduced to its
third clause alone, which passes whenever no sink is marked default.

The consequence needed a machine with no hardware sink to become visible, and
lempi02w is one: a Zero 2 W has no analog jack, its only ALSA card is `vc4hdmi`,
and with nothing plugged into HDMI that card yields no sink — so until
Bluetooth arrives it genuinely has nowhere audible to send audio. (The
`snd_bcm2835.enable_hdmi=0` / `enable_headphones=0` on its kernel command line
are the firmware describing that hardware, not a choice anyone made; the
profile is set out in [IMPL001 `[IMPL-AUD-005]`](IMPL001-appliance-setup.md).) Measured on the 15:37 boot — *"real sink
present after 3s"*, thirty seconds before the speaker existed, releasing the
player to open its output onto a dummy and stay there. Every layer above
reported success, which is `[PI3-WHY-010]` arriving by a new road.

Rewritten to parse only the numbered sink rows, so no header or box-drawing
line can be counted as a sink, and to give the *chosen* speaker first refusal
before accepting any other real sink — a grace period rather than a
preference, so an appliance with no speaker still boots promptly.

**`[PI3-FOUND-100]` The `scan` verb could not see a speaker.**
`pair_sequence` has set `transport bredr` since it was written, for a reason
recorded in its own comment: the default discovery filter returns LE only,
and a speaker is a classic BR/EDR device. `scan` never did. So the one verb
whose entire purpose is finding a speaker was structurally incapable of
returning one — confirmed live, a scan returning nine devices, eight of them
nameless LE random addresses, and not the Middleton sitting a metre away.
It reads exactly like the speaker being switched off.

Fixed, and nameless devices are now dropped from the listing: bluetoothctl
renders a device it has no name for as its own address with dashes, a real
speaker always advertises a name, and a dozen identical-looking hex rows are
how the one row that matters gets missed. The panel went from nine junk
entries to the two real speakers.

**`[PI3-FOUND-070]` `Connected: yes` is not proof of audio, and now nothing
claims it is.** The device-level ACL link survives perfectly well while the
A2DP media profile underneath it is idle or absent. `MediaTransport1.State`
is the only honest answer — `active` means a stream endpoint is acquired and
carrying audio — and the helper's reports now carry it, alongside a `reach`
field separating "never answered, so held by another device or asleep" from
"answered and refused". Both used to render as plain failure, which sent the
listener to the wrong problem.

**`[PI3-FOUND-120]` Every power-down on this appliance is a power cut, and
one of them stopped the player from ever starting again.** Because the
speaker supplies the Pi `[PI3-FOUND-090]`, switching the speaker off yanks
the supply from a running system with open databases. There is no shutdown
and no opportunity for one, so it is a supported event rather than misuse.

What it leaves is a hot rollback journal beside the database — measured,
`/var/lempi/listener.db-journal`, 8720 bytes. SQLite's contract is that the
next connection rolls it back, but rolling back is a *write*, and Lempi
attaches deliberately `mode=ro` so the player cannot corrupt the library
`[IMPL-DBSPLIT-025]`. A read-only connection cannot perform the recovery it
is required to perform. It therefore fails, permanently:

```
$ sqlite3 lempi.db "ATTACH 'file:/var/lempi/listener.db?mode=ro' AS lsn; ..."
Error: stepping, attempt to write a readonly database (8)
```

`Restart=always` then turns that into a crash loop — observed at 23 restarts
and still climbing, with no web interface, no audio, and nothing in the
failure naming a journal file. Note the misdirection in the log: the fatal
line reads `attach library /srv/library/library.db`, and `library.db` was
provably fine the whole time; it was `listener.db` that could not be
recovered. Chasing the name in the error message leads to the wrong file.

Recovery took one read-write open, which is all SQLite needs to notice the
journal and roll it back. `lempi-db-recover` now does exactly that for all
three databases as the first `ExecStartPre`, so the appliance heals itself
instead of needing someone with an ssh key. It costs 30 ms and prints
nothing when there is nothing to recover. `PRAGMA user_version` rather than
`integrity_check`: the rollback happens on first read by a read-write
connection, so the cheapest read suffices, and a real check of a 1.1 GB
library on a Pi Zero 2W would add minutes to every boot to answer a question
nobody asked.

> **Verified 2026-09-08** on the real failure, not a simulation: the hot
> journal was present, the read-only attach failed with the error above, a
> single read-write open removed the journal, and the same attach then
> returned its row count. The player started, resumed, and reached the
> speaker. Attempts to manufacture a synthetic hot journal afterwards were
> abandoned — SQLite optimises the no-op transaction away — which is worth
> knowing before anyone tries to write a regression test for this.

**`[PI3-FOUND-130]` Trust is what lets the speaker reconnect to *us*, and a
hand recovery throws it away silently.** BlueZ auto-authorises an incoming
service connection only from a trusted device; untrusted, it asks an agent,
and this appliance registers one only for the duration of a `pair`
`[PI3-WHY-060]`. At every other moment there is nobody to ask, so the request
is refused outright.

Measured on the boot after the Middleton was hard-reset and re-paired by
hand. The speaker had an ACL link to the Pi by 20.8 s, then tried three times
to bring up A2DP and was refused each time:

```
[20.836] lempi-speaker: ...is connected but the stream was on 'nothing'
[28.952] bluetoothd: Authentication attempt without agent
[28.953] bluetoothd: a2dp.c:auth_cb() Access denied: org.bluez.Error.Rejected
[37.798] ... Rejected      [46.693] ... Rejected
[55.719] lempi-speaker: connected 20:64:DE:CF:F3:AD after 5s
```

Its persisted record read `Trusted=false` beside a perfectly good link key.
Thirty-five seconds were spent refusing the speaker's own offers to connect,
until this script's outbound connect finally won.

The delay is the smaller half. **The speaker reaching out to us is the one
path that does not have to win the power-up race `[PI3-FOUND-090]`** — it
costs no paging and no shared-radio time, and it begins the moment the
speaker is awake, which on this appliance is also the moment the Pi is
plugged in. Losing trust disables that path entirely and leaves only the
race, which is the path the Pi loses by design.

`use` has always trusted `[PI3-WHY-040]`, but a listener recovering by
forgetting the device and reconnecting through any other path lands on a
bonded, untrusted speaker, and nothing ever put it back. `lempi-speaker` now
asserts it every tick: checked before it is set, so a healthy appliance
spends nothing, and loud when it actually repaired something. Verified by
untrusting the speaker and watching the next tick restore `Trusted=true` to
disk.

**`[PI3-FOUND-140]` The chase collided with the connection it was hurrying.**
`[PI3-AIM-060]`'s persistent chase was written against a speaker that does
not answer at all, and it read "absent" from the D-Bus `Connected` property.
That property goes true when a *profile* connects, so it reads false through
the whole of A2DP negotiation — and the chase therefore paged again every
two seconds while a negotiation was already in flight. Each page collided
with it:

```
[50.6] avdtp_connect_cb() connect to ...: Operation already in progress (114)
[52.7] [54.8] [56.9] [59.0] [59.0]  ... the same, six more times
```

Eight collisions in one boot, and A2DP that had completed at 75 s on the
previous boot did not finish until 88 s. The measurable effect of making the
keeper more determined was to make audio arrive thirteen seconds later.

`hcitool con` is the honest question: it asks the controller whether a
baseband link exists, rather than asking BlueZ whether a profile has
finished. A link present means a connection is up or coming up, and the only
useful thing to do is keep out of its way. The chase now skips paging
entirely while a link exists, and waits 3 s rather than 2 s between pages so
that two unanswered pages cannot overlap each other's timeouts.

**`[PI3-FOUND-150]` The boot gate was serialising a wait the library load
would have covered for free.** The player does not touch its output when it
starts — it backs up listener state, then builds the library index, 8330
passages off a 1.1 GB database on a Pi Zero 2W, about twenty seconds — and
only then opens a device. Those twenty seconds are twenty seconds in which
Bluetooth may finish settling at no cost, so a second spent waiting *before*
starting the player is a second added to the total rather than hidden inside
it. Measured on a power cycle: the gate waited its full 45 s, gave up, and
only then let the twenty-second load begin. Ninety-five seconds to audio, of
which about forty-five were spent deliberately doing nothing.

The deadline is now 15 s. It still catches what it was written for — a warm
restart, where the sink is already present and the answer is instant — and
still gives the chosen speaker first refusal. A sink that turns up late is no
longer the dead end it was when this gate was written: the path supervisor
notices a dummy and reopens `[SPEC-APS-060]`, and `lempi-speaker` notices the
routing disagreeing with the connected device `[PI3-AIM-050]`. Two mechanisms
that did not exist then now cover the case this was holding the entire boot
still to prevent.

