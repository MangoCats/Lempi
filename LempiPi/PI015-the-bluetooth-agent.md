# PI015: The Bluetooth Agent, and Three Tries to Reach It

**Appliance Record — refusing a speaker before it takes the transport**

Split from [PI011](PI011-two-speakers-and-placement.md) on 2026-09-10, which had reached 1,645 lines against `[GOV-DOC-010]`'s 300-line limit by accumulating a day of dated findings under a single heading.

A keeper running every thirty seconds cannot win a race decided in five, so
the refusal had to move to where BlueZ makes the decision. Getting there
took three attempts, and **the first two looked acceptable from outside
while the mechanism did nothing** -- the backstop covered for both. That is
the lesson worth carrying: "the outcome was correct" is not "the mechanism
works", and only the journal told them apart.

> **Related:** [PI011](PI011-two-speakers-and-placement.md) is the front door and lists the rest.

---

### `[PI3-FOUND-640]` The rules inverted themselves, and the policy got shorter

**What happened.** The Middleton was playing. The Oontz was powered on at
18:24. The journal:

    18:24:08  'OontZ_Angle 3 U412' is holding a link while 'MIDDLETON' is
              playing -- disconnecting it
    18:24:15  bluetoothd: a2dp_select_capabilities() Unable to select SEP
    18:24:41  'MIDDLETON' is holding a link while 'OontZ_Angle 3 U412' is
              playing -- disconnecting it

The rule fired correctly first, kicking the intruder off a playing incumbent.
The Oontz -- trusted, so it reconnects unprompted -- came straight back, A2DP
negotiation failed, and the Middleton's sink died with it. On the next tick the
viability test read the dead sink as licence to move the audio to the Oontz,
and the same rule then kicked the Middleton. **Every rule did exactly what it
said, and together they did the opposite of the design.**

**The error, precisely.** Making *viability* the test for keeping audio where
it is hands victory to any intruder capable of breaking the incumbent -- which,
on one adapter with one A2DP transport, is all of them. The aggressor wins by
breaking the thing it is competing with. Incumbency is an identity, not a
condition: a speaker whose sink was knocked out has not become unavailable, it
has been attacked.

**`[PI3-AIM-080]` The policy is now one sentence.**

> There is at most one speaker connected at a time. It is chosen when none is
> connected -- the listener's speaker first, then any other known one -- and it
> is replaced only when it goes away.

That sentence replaced four rules which had grown separately and had begun to
fight: a stickiness test, a viability test, a startup preference with its
once-per-boot marker, and a disconnect-the-others sweep. It is not a weaker
statement -- it produces the same intended behaviour, and it removes the
interactions that produced the inversion:

| Old rule | Where it went |
| --- | --- |
| stickiness | falls out: there is only ever one candidate |
| viability as licence to switch | deleted: a missing sink is waited for |
| startup preference, once-per-boot marker | falls out: preference applies when picking, which only happens when none is connected |
| disconnect-the-others sweep | kept, but as *enforce exactly one*, not as a reaction to what is playing |
| interloper reporting | deleted: an interloper is disconnected, not narrated |

`lempi-speaker.sh` went from 503 lines to 366 with no loss of behaviour.

**`[PI3-FOUND-630]` The race is closed at the source.** A keeper running
every thirty seconds cannot win against damage that takes five, so the refusal
now happens before a transport is handed over. `lempi-bt-agent` answers BlueZ's
authorisation calls: accept when there is no incumbent, accept when the caller
*is* the incumbent, refuse otherwise. Without an agent BlueZ has only two
reflexes -- authorise a trusted device automatically, refuse an untrusted one
forever `[PI3-FOUND-610]` -- and neither is what this appliance wants.

**It guards every audio profile, not just A2DP.** *(Written before the first
real test, and wrong: it missed the umbrella UUID that BlueZ actually asks
about, and the table below is therefore incomplete rather than reassuring.
See `[PI3-FOUND-660]`.)* The first version refused
A2DP alone, which would have left open the door the last failure came through:
the headset and hands-free profiles open a *synchronous* eSCO link, and a
synchronous link pre-empts A2DP rather than sharing with it `[PI3-FOUND-600]`.
Caught by testing the decision rather than assuming it. Verified against the
deployed agent:

    incumbent, a2dp .............. ALLOWED
    intruder, a2dp ............... REFUSED
    intruder, hands-free ......... REFUSED
    intruder, headset ............ REFUSED
    intruder, remote control ..... ALLOWED

**Not yet exercised in anger.** The refusal path is unit-tested against the
deployed agent and correct in all five cases, but no speaker has actually
knocked since it was installed -- the Middleton was powered down. The first
time two speakers are powered together is the real test.

*(It was, and it failed `[PI3-FOUND-660]`. The five cases were correct and
incomplete: each was fed a UUID drawn from the same list the code checks, so
the test shared the implementation's blind spot. The agent now carries
`lempi-bt-agent selftest`, whose UUIDs are transcribed by hand rather than read
from the code, and which was itself verified by reintroducing the bug and
watching it fail.)*

### `[PI3-FOUND-610]` An untrusted speaker knocks forever, and the machine wedged

The journal survived this one, because `Storage=persistent` had been left on
from an earlier investigation. It is the first time in this whole record that
a fault could be read after the fact rather than reconstructed.

**What the Middleton was doing.** From the moment it was powered on, every nine
seconds:

    17:53:10  bluetoothd: Authentication attempt without agent
    17:53:10  a2dp.c:auth_cb() Access denied: org.bluez.Error.Rejected
    17:53:19  (same)   17:53:28  (same)   17:53:38  (same)

**The mechanism is this appliance's own doing.** A trusted device is
authorised automatically; an untrusted one needs an agent to approve it, and no
agent is registered here. The Middleton read `Trusted: no` because the listener
had switched to the Oontz and `withdraw_others` untrusts everything but the
chosen speaker `[PI3-FOUND-130]`. So a powered-on, paired, untrusted speaker
knocks on the door every nine seconds and is refused every nine seconds,
indefinitely. Untrusting is not neutral: it converts a nearby speaker into a
permanent source of connection attempts.

**The one-speaker rule fired correctly, and could not win.** At 17:53:28 it did
exactly what it was built to do -- and the device it disconnected simply came
back on the next attempt.

**Then the machine wedged.** The journal ends at 17:53:38, three seconds before
the listener reported the Oontz going silent, and the appliance ran for
thirteen minutes answering ping while refusing every TCP connection -- ssh and
the web interface alike -- until it was power-cycled.

**No cause was established, and none is claimed.** The kernel logged no I/O
error, no OOM kill, no hung-task warning and no panic; ICMP is handled in the
kernel and kept working, which places the fault in userspace. Three guesses
were made during the incident -- WiFi interference, self-inflicted paging, and
a marginal radio link -- and all three were wrong: the loss cleared to 0% while
TCP stayed dead. **What actually stopped is unknown.**

**Which is the point of what was built next.** Whatever happened, the record
that would have shown the run-up was journald, and journald is userspace and
died with everything else. `lempi-vitals` `[PI3-FOUND-620]` writes elsewhere.

### `[PI3-FOUND-660]` The agent guarded the UUIDs nobody uses

First real test, 2026-09-10 19:11. The Oontz held the audio; the Middleton was
powered on. The agent was consulted and **allowed it**:

    19:11:09  authorise 20:64:DE:CF:F3:AD for 0000110d (not audio, allowing)
    19:11:31  keeper: MIDDLETON connected while OontZ_Angle 3 U412 holds the
              audio -- disconnecting it

`0000110d` is Advanced Audio Distribution -- the umbrella profile UUID, and the
one BlueZ actually authorises against. The guard listed `...110b` (A2DP sink)
and `...110a` (A2DP source), which are the names nobody asks for. **The agent
was useless in precisely the case it was written for**, and the keeper's
backstop cleaned up twenty seconds later.

The outcome was still correct -- the Oontz kept the audio and the listener
heard no interruption -- but by the slow path the agent exists to remove.

**Why the unit test did not catch it.** Every case was fed a UUID chosen from
the same list the code checks, so the test agreed with the code about which
names matter and both were wrong together. The five green results at
`[PI3-FOUND-640]` were real and meaningless. What caught it was one line of
journal from a speaker actually knocking, which is why *"not yet exercised in
anger"* was worth writing down rather than glossing.

Fixed by adding the umbrella UUID, retested, and the refusal path now covers
umbrella, sink, headset and hands-free while leaving remote control alone.
**Still not exercised in anger** -- the corrected agent has not yet met a real
knock either.

### `[PI3-FOUND-680]` The agent was unreachable, because trust is the enforcement

Second real test, 2026-09-10 19:22. The Middleton held the audio; the Oontz
was powered on. It connected at 19:22:18, **both speakers went silent**, and
the Middleton recovered at 19:22:53 when the keeper's backstop disconnected the
intruder -- thirty-five seconds of silence.

**The agent was never called.** Not refused, not consulted: BlueZ asks an agent
only about an **untrusted** device. A trusted one is authorised without anybody
being asked, and both speakers read `Trusted: yes`.

So the agent could never have worked as built, and the reasoning that removed
`withdraw_others` was backwards. `[PI3-FOUND-650]` called it redundant because
"the agent now refuses any audio profile from anything that is not holding the
audio". It cannot. **Trust is the enforcement; the agent only supplements it,
for the devices trust has already excluded.** Removing the untrusting removed
the mechanism and left the supplement addressing nobody.

**The correction is not simply to put it back.** The old version untrusted
everything but the *chosen* speaker, which is how a Mode A boot could find the
Middleton untrusted while it was the one playing. Trust now follows the
**audio**:

- the incumbent is trusted, so it may let itself back in;
- every other known speaker is untrusted, so its attempts reach the agent,
  which refuses them because it is not the incumbent;
- when nothing holds the audio, the listener's chosen speaker is trusted
  instead -- the one path that does not have to win the power-up race
  `[PI3-FOUND-130]`.

Verified on the appliance immediately after deploying: with the Middleton
playing, the tick logged *"untrusted OontZ_Angle 3 U412 -- only the speaker
holding the audio may let itself in"*, the two devices then read `Trusted: no`
and `Trusted: yes` respectively, and audio was undisturbed at 375 packets per
five seconds.

**Still unproven where it counts.** The agent has yet to refuse anything: the
first test found the wrong UUID list `[PI3-FOUND-660]`, the second found it was
never consulted, and both times the keeper's backstop did the work. A third
test -- powering a non-incumbent speaker on while another plays -- should now
show `REFUSE` in the agent's log within a second and no silence at all. Two
mechanisms have been fixed on the way to that, so it would be premature to
assume the third attempt is the one that works.

### `[PI3-FOUND-690]` The agent refused, and the audio did not move

Third real test, 2026-09-10 19:33, and the first that the agent decided. The
Oontz held the audio and was trusted; the Middleton was untrusted; the
Middleton was powered on.

    19:33:26  lempi-bt-agent: REFUSE 20:64:DE:CF:F3:AD:
                              '08:EB:ED:26:14:12' already has the audio
    19:33:26  bluetoothd: auth_cb() Access denied:
                          another speaker already has the audio

The appliance's own sentence, handed to BlueZ and logged back by it. **The
Oontz held throughout at 312 packets per five seconds -- its full baseline rate
`[PI3-FOUND-570]`, with no dip and no silence.** Against the same test three
attempts earlier, which cost thirty-five seconds of silence from both speakers
`[PI3-FOUND-680]`.

It took three tries to get here, and the first two failed in ways worth
keeping: the guard listed UUIDs nobody asks for `[PI3-FOUND-660]`, and then
the agent was never consulted at all because trust had already answered for it
`[PI3-FOUND-680]`. Both times the keeper's backstop cleaned up and the outcome
looked acceptable from the outside, which is exactly why "the outcome was
correct" is not the same as "the mechanism works".

**What is left is churn, not silence.** The Middleton keeps an ACL link and
re-offers its audio profiles every ten seconds for as long as it is powered
on, each offer cleanly refused. The keeper's backstop also disconnects the
baseband link when it sees it, and the speaker reconnects. Nothing is audible
and the packet rate is untouched, but a device is knocking on a door that will
never open, indefinitely -- the shape of `[PI3-FOUND-610]` with a polite answer
instead of a blank one.

`Blocked` is the quieter instrument: BlueZ will refuse the connection at the
adapter rather than at the profile, so the knocking stops instead of being
answered. It is persistent state, and a speaker left blocked by a crash stays
blocked, so it wants care rather than a quick edit. **Not done, and recorded
as the next thing rather than a defect** -- the audio is protected either way.

