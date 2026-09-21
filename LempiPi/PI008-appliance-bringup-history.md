# PI008: Appliance Bring-up History

**Appliance Record — dated findings from bringing lempipi up, 2026-08-16 to 2026-08-20**

Three incidents from the appliance's bring-up, each originally narrated inline
in the current-state document it interrupted: [PI002](PI002-test-image-setup.md)
(the first hardware run), [PI003](PI003-choosing-a-speaker.md) (the reopened
stream), and [PI005](PI005-appliance-library.md) (the real-library swap). Per
`[GOV-DOC-050]` that narrative doesn't belong in a document describing the
present system, so it lives here instead — one section per source, with a
one-line pointer left behind in each. Nothing here is a plan; everything is
dated and measured, same discipline as [PI004](PI004-speaker-operation.md).

> **Related:** [PI002](PI002-test-image-setup.md) ·
> [PI003](PI003-choosing-a-speaker.md) · [PI004](PI004-speaker-operation.md) ·
> [PI005](PI005-appliance-library.md)

---

## 1. The dummy sink that played into nothing

From [PI002](PI002-test-image-setup.md)'s first hardware run, 2026-08-16.

**`[PI2-RUN-010]` It plays.** Music reached a Marshall Middleton over A2DP
from a Pi Zero 2 W. Measured alongside: **26.5 MB RSS** against the 30 MB of
`[REQ-HW-010A]`, **15.2 s** boot (4.4 kernel + 10.8 userspace), **59-62 °C**
bare-board with `throttled=0x0` -- no heatsink needed, and no undervoltage.

**`[PI2-RUN-020]` SBC at 44,100 Hz, and no resampling.** The sink negotiated
SBC; Lempi logged `output: default @ 44100 Hz, 2 ch`. Source and sink match,
so `rubato` never runs `[PI2-RATE-010]`.

**`[PI2-RUN-030]` The audio stream fails with EIO a few seconds in, and the
player does not recover.** This is the open problem, and it is not what the
first diagnosis said.

    output stream error: ALSA function 'snd_pcm_poll_descriptors_revents'
    failed with error 'Unknown errno (-5)'

-5 is EIO on the stream itself. What a listener hears is a couple of seconds
of music, a second of silence, then the speaker announcing a disconnection --
and that order is the evidence: **the stream dies first and the link drops
because nothing is holding it**, not the other way round.

The earlier reading -- that `cpal` opens the default sink once and never
follows a later change -- was wrong, or at least not the whole story. It was
tested directly: connect the speaker, set it default, *then* start the player.
The stream attached to MIDDLETON on both channels, played, and still failed.
Fifty samples with ssh idle afterwards: **0 connected, 0 attached.**

So the fault is in sustaining the stream, not in selecting it. Two things
followed for the player, and both were its work rather than the image's:

- **An output error must be recovered from, not merely logged.** The engine
  reported it once and continued silently for ever; on an appliance the only
  correct response is to reopen the device and carry on.
- The proximate cause was still unknown at this point. EIO from the
  ALSA-to-PipeWire bridge over Bluetooth was worth isolating with a plain
  `aplay` to the same sink: if that also failed, it was the bridge and not
  Lempi.

Ruled out by measurement: Wi-Fi/Bluetooth coexistence on the shared radio (it
fails with ssh idle), thermal throttling and undervoltage (59-62 C,
`throttled=0x0`), decode headroom (no underruns), the pairing (paired,
trusted, connected), and sink selection (the stream demonstrably attached to
MIDDLETON before failing).

**`[PI2-RUN-040]` Resolved.** The cause was `[PI3-WHY-010]`: PipeWire offers a
`Dummy Output` when no sink is present, and the ALSA bridge binds a stream to
whichever node was default when it opened. A player started before the speaker
connects plays perfectly into that dummy, reports itself healthy, and leaves
the speaker with no audio to hold A2DP open -- which is heard as a drop a few
seconds in, and sends anyone investigating straight to Bluetooth.

Fixed by `lempi-wait-sink` (the unit will not start against a dummy), a
`--device` flag, and output recovery in the engine. Measured with audio
genuinely flowing, confirmed by `pw-top` and by ear across two tracks:
**40/40 samples connected over two minutes, no errors, no recoveries.**

**`[PI2-RUN-050]` Interference was not the cause, and is not being designed
around.** `radio-silence-test.sh` can take the shared radio out of the
measurement entirely -- one antenna serves Wi-Fi and Bluetooth, so a reading
taken over ssh competes with what it measures -- and its `KEEP_WIFI=1` control
arm measures the same thing with the radio up. The control arm is clean, so the
dark arm has not been run. It is the tool to reach for if unexplained
connection problems appear later `[PI3-NOT-010]`.

---

## 2. A reopened stream dies, a fresh one does not

From [PI003](PI003-choosing-a-speaker.md), reproduced repeatedly on
2026-08-16. Whether this is now fully closed, or merely improved, is tracked
as an open question in [ROADMAP §4](../docs/ROADMAP.md#4-the-appliances-still-open-speaker-questions)
rather than settled here.

**`[PI3-OPEN-010]`** A stream created by `recover()` against an
already-running player loses the speaker **about twenty-two seconds later**,
every time. A stream created fresh at startup, with the speaker connected
first, holds indefinitely -- two separate runs of ninety seconds and two
minutes, connected on every sample, no dummy detections, no errors.

Ruled out by measurement, not argument: CPU (4% during the failures), the
`wpctl` call on the engine thread (moved off it, and the failures continue),
battery (60-70%), pause (link held 8/8 across forty seconds paused), radio
coexistence (the control arm is clean), and range (80 cm, and the adapter sees
fourteen other devices).

So something about *rebuilding* the stream leaves it fragile in a way opening
it once does not -- the old handle not fully released, or the device reopened
while PipeWire is still settling. This matters because reopening is the
mechanism the whole speaker panel rests on `[PI3-WHY-020]`.

**A first verification attempt was itself wrong, and the way it was wrong is
worth recording.** A two-minute test reported eight samples out of eight and
was called verified; the drop actually came twenty seconds after the window
had closed, at about two and a half minutes rather than the original twenty
-two seconds. Every clean result of that day shared the same flaw: **no test
ran longer than the failure it was measuring.** The lesson taken forward: a
verification run needs to go longer than any failure interval already seen,
with the underrun counter watched throughout, since that is what produced the
real signal both times it mattered.

**The immediate cause turned out to be the obvious one nobody had tested.**
`recover()` released the device and reopened it in the same breath. Giving
PipeWire 700 ms to finish tearing the old stream down before opening the new
one made a reopened stream hold as well as a fresh one: **two minutes,
connected on every sample, from the worst case** -- player already running, no
speaker, then connect and select.

Startup had been stable only because it never reopened anything in flight. The
gap was the whole difference, and the connect-then-restart workaround this had
motivated is no longer needed.

---

## 3. What the real library swap exposed

From [PI005](PI005-appliance-library.md), 2026-08-20.

**`[PI5-DEP-010]` The deploy script's health check had rotted, and rolled back
a good binary.** `deploy-player.sh` (renamed to `build/install-player.sh` on
2026-09-11 `[GDE-DEP-030]`) waited `sleep 8` then asked the running
player to identify itself. That was true against the 31-file test library and
false the moment the appliance held the real one: the Program Director is
built at startup and takes **9.86 s** over 8,330 passages, so the web server
binds at about **15 s**. The check failed a perfectly good build and rolled it
back, reporting *"new build did not answer"* — which invites diagnosing the
build rather than the deadline. It now **polls** to a bounded deadline instead
of guessing one.

This is the same shape as the quadratic browse in `[REQ-LIB-165]`: a number
tuned against small data, correct when written, silently wrong once the data
grew, and with no commit to bisect because nothing changed but the library.

**`[PI5-PRIV-010]` The narrow sudoers rule was not, at the time, narrowing
anything.** `[PI3-PRIV-*]` describes reaching BlueZ "through a sudoers rule
naming that one binary rather than by granting the player broader rights".
Measured on the appliance, `/etc/sudoers.d/` also contained the Raspberry Pi OS
default:

    pi ALL=(ALL) NOPASSWD: ALL

So the player already had passwordless root for everything, and the closed
verb set was defence that was not presently defending. The design was still
judged the right one — it is what makes the helper safe *if* the blanket rule
is ever removed — but the document had been claiming a property the machine
did not have. **Settled 2026-08-20: it stays.** This is a development machine
and passwordless sudo is appropriate to it. A final appliance design may want
a tighter model; this one does not, and the narrow verb set remains the right
shape for whenever that happens.
