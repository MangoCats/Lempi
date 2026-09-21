# PI013: Theories Withdrawn

**Appliance Record — what was believed, and what refuted it**

Split from [PI011](PI011-two-speakers-and-placement.md) on 2026-09-10, which had reached 1,645 lines against `[GOV-DOC-010]`'s 300-line limit by accumulating a day of dated findings under a single heading.

**Everything in this file is wrong.** It is kept because each entry was
coherent, fitted the symptom, and rested on real measurements -- and
because the next person here will reach for the same explanations. An
investigation that records only its successes teaches nobody where the
ground is soft. The hop-map theory has its own file,
[PI017](PI017-the-hop-map.md), because it ran longest.

What they share matters more than any of them: **each was argued from a
handful of expensive, ear-measured points**, before any instrument could
see the symptom at all `[PI3-FOUND-430]`.

> **Related:** [PI011](PI011-two-speakers-and-placement.md) is the front door and lists the rest.

---

## 3. A wrong answer, by substitution

**`[PI3-FOUND-300]` The periodic stutters were the Middleton, not the
appliance.** Every Pi-side instrument had been saying so for a while — the
player's underrun counter stopped climbing while the listener still heard
stutters, PipeWire reported zero xruns, the A2DP transport stayed `active`,
and Wi-Fi settled to three scans a boot — but "everything I can measure looks
fine" is not a diagnosis. Swapping the speaker is.

The OontZ was made the chosen speaker and the appliance power-cycled: audio
at **24.5 s**, and ninety seconds of listening with no audible stutter at all.
That boot also exercised most of what had been built — `lempi-db-recover`
rolled back a hot journal at 17.5 s, `lempi-wait-sink` found the right sink in
2 s, `session open` took 182 ms.

The decisive number is the comparison, not the absolute: **2.68 s of underrun
sounding clean**, against **1.64 s on a Middleton boot that stuttered
throughout**. More missing samples, less audible trouble. What the listener
heard on the Middleton was therefore not Lempi's underruns — those are a
couple of seconds around the Director rebuild, on any speaker, and inaudible.

The appliance's own faults were real and are fixed: 88-95 s to audio became
24.5 s, a power cut that wedged the player into 23 restarts now heals itself,
and a speaker that could not reconnect after a power cycle now does.

> **The conclusion drawn here was wrong; see `[PI3-FOUND-320]`.** This section
> ended "what remained belongs to the speaker, and no amount of work on this
> side will reach it." It did not belong to the speaker. The OontZ was clean
> because it was not being used as a shelf: the Pi was resting on the
> Middleton, and its shared Wi-Fi/Bluetooth antenna was being detuned and
> desensitised by the chassis and the speaker's own radio inches away. Two
> variables were changed — the speaker *and* the geometry — while one was
> believed to have been. Substitution answered the question it was asked; the
> inference drawn from it did not survive the next observation.

> **Method worth keeping.** Four causes were proposed and measured away —
> memory, swap, CPU starvation, SD contention — before substitution answered
> it in one power cycle. The instrument that mattered was `lempi-underruns`,
> not because its number was high but because it went *static while the
> symptom continued*, which is what put the fault downstream of the player. A
> counter that stops moving can be as informative as one that climbs.

## 5. The stutters were where the appliance was sitting

**`[PI3-FOUND-320]` The Pi was on top of the speaker.** Moving it about
eighteen inches away — same cable, same speaker, same everything else —
ended the periodic stuttering. Two power cycles, 240+ seconds of listening,
none heard at all. The radio numbers say why:

| | Pi resting on the speaker | Pi 18" away |
|---|---|---|
| Wi-Fi signal | -61 to -66 dBm | **-50 dBm** |
| Wi-Fi retry discards | 69, later 267 | **8** |
| audible stutter | every ~15 s | **none** |

Eleven to sixteen decibels, and retries down roughly thirty-fold. The Zero 2W
carries one PCB antenna shared by Wi-Fi and Bluetooth `[PI3-FOUND-010]`, and
resting it on the speaker put that antenna against a magnet, a driver, a
metal grille, and the speaker's own Bluetooth radio a couple of inches away.
Two transmitters in each other's near field desensitise each other; the
chassis detunes what is left.

**This is why every instrument said the machine was healthy.** Loss on the
air is invisible to all of them: the player's ring never missed a deadline
(`underrun_samples` static while the listener heard stutters), PipeWire
reported zero xruns, `MediaTransport1` stayed `active`, and the A2DP packets
were being handed to a controller that was duly transmitting them into a
degraded link. Nothing in software can see a packet that was sent and not
heard.

It also explains the substitution result in section 3 honestly. The OontZ
was clean not because the Middleton's electronics are at fault, but because
the OontZ was not being used as a shelf. **The conclusion in section 3 —
"what remains belongs to the speaker" — is wrong, and this supersedes it.**
What remained belonged to the geometry.

> **A caution for whoever measures this next.** The appliance is powered from
> the speaker's own USB port `[PI3-FOUND-090]`, by a cable short enough that
> setting the Pi on the speaker is the obvious tidy thing to do. It is the
> single worst place to put it. If stutters return, check where the box is
> before touching anything in this document.

**Underrun counts do not predict audibility, and should not be read as if
they do.** This boot recorded 11.58 s of `underrun_samples` and sounded
perfect; an earlier Middleton boot recorded 1.64 s and stuttered throughout.
The counter is honest about the ring running dry -- which happens during a
reopen, and around the Director rebuild -- but a dry ring during a routing
change is inaudible, while a healthy ring feeding a degraded radio is not.
Use it to tell "the player is starving" from "the player is fine", which is
what it settled `[PI3-FOUND-200]`, and not as a measure of quality.

## 6. Link quality is the predictor, and it is a number

**`[PI3-FOUND-340]` Moving the box was never the fix; improving the link
was.** Stuttering returned on 2026-09-09 with the appliance still eighteen
inches clear of the speaker. Everything else was eliminated by measurement:
the binary differed from the clean runs by a doc comment alone, the SBC
negotiation was byte-identical (`ay 4 17 21 2 53`), the transport was
`active`, AVRCP and sink volumes were at unity, the underrun counter was
**static**, and audio captured off the sink was continuous.

What differed was the radio, and it differs by a number worth writing down:

| | signal | stutters |
|---|---|---|
| Appliance resting on the speaker | -61 to -66 dBm | yes |
| Eighteen inches clear, clean runs | **-50 dBm** | none |
| Eighteen inches clear, stuttering | **-59 to -66 dBm** | yes |

Three points, and the symptom tracks the link rather than the distance. On
the stuttering boot the appliance had also associated with the *other* access
point of the pair `[PI3-FOUND-310]`'s roam-flap describes — same SSID,
different radio, worse result — which is one way the same eighteen inches can
buy a good link on Monday and a poor one on Tuesday.

**So `[PI3-FOUND-320]` is refined, not withdrawn.** Sitting on the speaker
reliably ruins the link and moving off it reliably helps, but neither is the
mechanism: what matters is how much of the one shared antenna Bluetooth gets,
and Wi-Fi conditions move that on their own.

**Retry counts were offered as half of this and are withdrawn.** They looked
discriminating — 8 on a clean run against 55 on a stuttering one — but
`/proc/net/wireless` reports retry discards as a **counter cumulative since
boot**, and those two numbers were read at different uptimes. Compared
properly as a rate, a stuttering boot on 2026-09-09 ran at 0.03 retries/s,
which is nothing. The figure never discriminated; it only looked as though it
did because the clock underneath it was moving.

> **Check it before listening.** `awk 'NR==3{print $4}' /proc/net/wireless`
> gives the signal level, which is instantaneous and therefore comparable
> between boots. **Around -50 dBm has sounded clean here; -59 to -66 has
> stuttered**, on four boots. A working threshold from few points, not a
> specification — but it is one number, it needs no audio, and it beats moving
> the box and hoping.

## 10. Both explanations refuted, and the experiment that remains

**`[PI3-FOUND-390]` The next mode A boot came up outbound, collision-free, and
stuttered anyway.** That is both hypotheses of section 9 gone in one
observation:

| | Mode B (clean) | Mode A, boot 1 | Mode A, boot 2 |
|---|---|---|---|
| ACL direction | `<` outbound | `>` inbound | **`<` outbound** |
| AVDTP collisions | 0 | 1 | **0** |
| Configuration | `ay 4 17 21 2 53` | same | same |
| Stuttered | no | yes | **yes** |

The keeper had connected it outbound itself at 36 s, so the redial built in
section 9 — conditioned on finding an inbound link — never even fired.

**And the trial that appeared to prove the redial was confounded.** It ran at
57 s, and a second attempt at 180 s; mode A settles on its own by about 180 s.
Neither trial separated "the redial fixed it" from "the speaker was going to
settle anyway". The second could not have: it was run at the settling point.

So nothing at the Bluetooth layer now distinguishes a stuttering boot from a
clean one. Configuration, codec, transport state, volume, direction and
collision count are all identical. The difference remains the one thing this
machine cannot observe: whether the speaker was cold-booted.

**What is left is an experiment, and it is written as one.** The redial now
fires once per boot unconditionally at around 50 s, well before the settling
point, because that is the only arrangement that can tell the two apart: if
stuttering stops at ~50 s rather than running to ~180 s, the redial does
something. If it runs to 180 s regardless, it does not, and the honest answer
becomes the listener's own workaround — power-cycle the Pi, not the speaker.

> **This costs a mode B boot a gap it did not previously pay**, and that is a
> real regression if the redial turns out to do nothing. It is deliberate, and
> it should be reverted the moment the experiment answers either way.

### `[PI3-FOUND-490]` Role and the hop map, the two things never read

The question that produced this was the listener's: what is *this appliance*
doing to the state of the speaker? Three things, all read off the live link on
2026-09-10 with `lempi-linkstate`:

| | Healthy outbound link |
| --- | --- |
| `role` | **CENTRAL** |
| `direction` | outbound (`<`) |
| `afh` | `000000f47ffcffffff3f` -- 48 of 79 channels |
| `link_quality` / `rssi` / `tx_power` | 255 (max) / 0 (golden range) / 12 |
| `supervision_timeout` | 20000 ms |
| transport `State` / `Volume` / `Configuration` | active / **106** of 127 / `4 17 21 2 53` |
| wifi | **channel 1, 2412 MHz, 20 MHz wide** |

**What this appliance writes into the speaker.** The AVRCP absolute volume --
106 here -- which the speaker keeps across a reboot of the Pi, and which is
the first thing to read on a connected-but-silent fault `[PI3-FOUND-480]`. The
SBC configuration, long since exonerated as identical in both modes. And, when
it is CENTRAL, the AFH channel map.

**`role` is not `direction`, and only `role` was never checked.** Section 9's
mode comparison recorded `<` against `>` and section 8 called that "ACL role
CENTRAL (`<` outgoing -- the Pi initiated)". Those are two different fields.
Whoever initiates *starts* as CENTRAL, but either end may request a role
switch afterwards and BlueZ commonly permits one, so a Mode A link may have
been running with this Pi as PERIPHERAL on every boot with nobody looking.
`[PI3-FOUND-390]` refuted *direction* as the mechanism; it did not touch role.

**The hop map excludes exactly this Pi's WiFi channel.** Channels 0-25 are
disabled, and at 2402 + k MHz that is a refusal to hop anywhere in 2402-2427.
This Pi's WiFi sits on channel 1: 2412 MHz, 20 MHz wide, spanning 2402-2422.
The map was also observed to change between two reads minutes apart (52
channels, then 48), so the adaptation is live rather than fixed at connection.

> **The hypothesis, stated so it can be killed.** The CENTRAL sets the hop
> map. This Pi knows where its own WiFi radio is; the Middleton does not. So
> when the Pi is CENTRAL the speaker is handed a map that avoids a transmitter
> sitting inches away, and when the speaker is CENTRAL that protection does
> not exist. That would predict mode dependence, a deficit present in the
> first packets and never drifting `[PI3-FOUND-470]`, roughly three minutes of
> settling as the speaker's own blind assessment learns the bad channels, no
> correlation with any Pi-side metric, and physical separation appearing to
> help before it was ruled confounded `[PI3-FOUND-320]`.
>
> **It is a hypothesis. Five have been withdrawn from this document.** Its
> prediction is sharp: a stuttering Mode A boot reads `PERIPHERAL`, or shows
> the low channels enabled, or both. **A stuttering boot that reads CENTRAL
> with a 48-channel map excluding 2402-2427 kills it outright**, and that is
> the outcome to hope for as much as any, because it is the first prediction
> here that costs one command to test.

Run `sudo lempi-linkstate` immediately after a boot in each mode and diff.

**Withdrawn the same evening, by the test it named.** See
`[PI3-FOUND-500]` below.

