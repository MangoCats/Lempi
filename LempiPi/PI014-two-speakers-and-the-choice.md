# PI014: Two Speakers, and Whose Choice Wins

**Appliance Record — the policy, and the bugs that shaped it**

Split from [PI011](PI011-two-speakers-and-placement.md) on 2026-09-10, which had reached 1,645 lines against `[GOV-DOC-010]`'s 300-line limit by accumulating a day of dated findings under a single heading.

The policy is one sentence `[PI3-AIM-080]`:

> *There is at most one speaker connected at a time. It is chosen when
> none is connected -- the listener's speaker first, then any other known
> one -- and it is replaced only when it goes away.*

It reads as obvious. It replaced four rules that had grown separately and
had begun to fight each other; what follows is what that cost.

> **Related:** [PI011](PI011-two-speakers-and-placement.md) is the front door and lists the rest.

---

## 2. Two bugs that only appear with a second speaker

**`[PI3-FOUND-270]` "Use this one" used whichever speaker was listed first.**
The `use` verb set the default sink by taking the first non-dummy entry out of
`wpctl status`, which is right only while exactly one speaker is connected.
Connect a second and it points the default at whoever sorts first. Measured,
with both connected and the listener asking for the OontZ:

```
$ lempi-btctl use 08:EB:ED:26:14:12          # the OontZ
{"ok":true,"state":"connected","sink_node":"48"}   # node 48 is the MIDDLETON
```

It answered `ok:true` for doing the opposite of what it was asked, and the web
handler — which reopens the player's output on the strength of that `ok`
`[REQ-VIS-260]` — dutifully moved the audio to the speaker the listener had
just asked to leave. From the settings page it looked like the button did
nothing at all.

Now matched by the alias BlueZ holds for the requested address, which is what
WirePlumber names the node after — the same join `lempi-speaker` already uses
`[PI3-AIM-050]` — and it waits up to ten seconds for that sink, because
WirePlumber creates it a moment after BlueZ reports the connection. No match
is reported as `none` rather than papered over with somebody else's sink.

**`[PI3-FOUND-280]` The scripts were reading a database the player stopped
writing to.** `[IMPL-DBSPLIT-025]` moved everything the listener chooses into
`/var/lempi/listener.db`, leaving the catalog in `/srv/library/`. Both
`lempi-speaker` and `lempi-wait-sink` kept reading the pre-split
`/srv/library/library.db`, which still exists and still holds a stale
`speaker_address` row. **The two held the same address, so nothing looked
wrong for weeks.**

They stopped agreeing the instant a second speaker was chosen:

```
legacy /srv/library/library.db: 20:64:DE:CF:F3:AD     (MIDDLETON)
live   /var/lempi/listener.db: 08:EB:ED:26:14:12    (OontZ)
```

The keeper read MIDDLETON, saw the stream on the OontZ, concluded the routing
disagreed with the speaker "on record", and asked the player to reopen —
fighting the listener's own choice every thirty seconds on the authority of a
file nothing had written to in weeks. `lempi-wait-sink` had the same fault and
would have spent its boot waiting for the speaker they used to have.

Both now prefer `listener.db` when it exists and fall back to the pre-split
path when it does not, so one script serves a split appliance and an unsplit
one without being told which it is on. Verified: the keeper falls silent on a
tick after the speaker is changed, and the gate reports *"OontZ_Angle 3 U412
present after 0s"*.

**`[PI3-FOUND-290]` A second speaker does not fail to connect; it connects as
a headset.** The adapter carries one A2DP transport at a time. Ask for a
second speaker while the first is still connected and BlueZ does not refuse —
it negotiates HSP/HFP instead, which is mono over SCO and worthless for
music. Measured, having switched to the OontZ with the Middleton still
connected:

```
51. output_MONO  >  OontZ_Angle 3 U412:playback_MONO   [active]
55. OontZ_Angle 3 U412                                  ← a capture Source
$ busctl ... MediaTransport1 State        → no transport for the OontZ at all
```

The giveaway is the microphone: an A2DP sink has no source. Volume full,
nothing audible, and every layer reporting success — `[PI3-WHY-010]` wearing
another hat, and the same shape of fault as the very first symptom in this
document.

Freeing the transport is not sufficient on its own. Disconnecting the
Middleton left the OontZ exactly where it was, still mono with no transport,
because nothing renegotiates a live connection. It took a disconnect and
reconnect of the OontZ itself, after which:

```
dev_08_EB_ED_26_14_12/sep1/fd1  →  "active"
55. output_FL > OontZ:playback_FL   56. output_FR > OontZ:playback_FR
```

`use` now disconnects any *other* connected audio device first — which is
what the listener asked for anyway; nobody presses "use this one" meaning
"and also keep the last" — and reconnects the requested device only when it
is connected without a transport, so a speaker already carrying A2DP is left
strictly alone rather than having the audio interrupted by the verb meant to
deliver it.

## 4. Two speakers, and who gets to decide

**`[PI3-FOUND-310]` A choice the listener made was being overwritten by
whatever turned up.** Measured: the Middleton was selected in the settings
panel and the appliance power-cycled with the speaker left on.

```
30.3s  lempi-speaker: connected 20:64:DE:CF:F3:AD after 10s, asked reopen
50.7s  lempi-speaker: adopted 08:EB:ED:26:14:12 as the speaker (was 20:64:DE:CF:F3:AD)
71.2s  resuming playback            -- on the OontZ
```

The keeper connected the chosen speaker correctly. The OontZ — still trusted,
so BlueZ reaches for it unprompted — connected behind it, took the one A2DP
transport `[PI3-FOUND-290]`, and the Middleton dropped. The listener heard a
connect tone and a disconnect tone seconds apart. Then `[PI3-AIM-040]`'s
adopt-what-is-connected rule promoted the interloper and **destroyed the
record of the choice**, and the boot finished playing through the speaker
they had just navigated away from.

Adoption was right when it was written: a stale address was being paged every
thirty seconds and stalling the speaker that was actually playing. But
`[PI3-AIM-060]` fixed that injury at its source — nothing is paged while
audio reaches a real sink — which left adoption doing only harm. It now
happens **only when no speaker has been chosen at all**; a recorded choice
stands until the listener changes it, and an uninvited device is reported
rather than promoted.

**The other half is trust.** Trust means "reconnect to this without being
asked", and on an adapter that carries one A2DP transport only one speaker
should hold that. Choosing a speaker now withdraws the standing invitation
from the others: they stay **paired**, so `use` brings any of them back in
seconds, they simply stop letting themselves in `[PI3-WHY-040]`.

That withdrawal runs **last** in the verb, and the reason is a race worth
recording. Done first, it was silently undone: `lempi-speaker` trusts
whichever address is *stored*, the store still names the old speaker until
the caller records the new one — which happens only after the verb returns
ok — so a keeper tick landing mid-connect re-trusted the speaker just
withdrawn, and it was still auto-connecting on the next boot. Observed once,
diagnosed from the persisted `Trusted=` flag disagreeing with what the verb
had just done. Moved to the end, the window is the microseconds between that
line and the caller's write.

> **Verified.** The OontZ was re-trusted by hand and `use` run against the
> Middleton: the OontZ came back `Trusted=false` on disk, the Middleton
> `Trusted=true`, connected, `transport: "active"`, stream on MIDDLETON. A
> keeper tick with the OontZ deliberately connected alongside left
> `speaker_address` untouched.

### `[PI3-AIM-070]` The design: audio sticks, and one speaker at a time

Stated by the listener on 2026-09-10, after a session with both speakers in
play:

> Once audio is established with one speaker, it should remain with that
> speaker even as other recognised speakers become available. Other speakers
> are only connected if the current one becomes unavailable.
>
> The user's selected speaker in the settings page would be the preferred
> choice when starting with multiple speakers available.

**What the keeper used to do instead.** The routing check compared the stream
against whichever speaker was *connected* and moved the stream whenever they
differed. So a chosen speaker returning mid-session would drag audio off a
speaker that was playing perfectly well. That is the right rule for deciding
where to send audio going nowhere, and the wrong one for audio already going
somewhere.

**Viability, not identity, is the question now.** A route is fine if it names a
sink PipeWire still offers; only when it stops being one does anything move.
Verified on the appliance: with the Middleton playing and the Oontz -- the
*chosen* speaker -- reconnected, the keeper ran silently and left the stream
where it was.

**The preference gets exactly one chance.** Only while the chosen speaker is
the connected one, only inside the first two minutes of uptime, and only once
per boot. The chase already implements most of the preference by going after
the chosen speaker first and falling back only once that fails
`[PI3-FOUND-560]`; this covers the gap where another speaker's sink appears
first and stickiness would then hold audio there all session. A preference
able to fire at any time would be the mid-track switch stickiness exists to
prevent.

### `[PI3-FOUND-600]` Two connected speakers is not degraded audio, it is none

Measured the same evening, with both powered and connected. Three five-second
samples of `ACL Data TX` with the Oontz holding a link alongside the playing
Middleton:

    0   0   0

Disconnect the Oontz, and the same measurement on the same link:

    374   375   374

Not degraded. **Stopped.** The listener's account matches to the second:
*"Oontz is connected, but silent"*, then *"now Middleton is audible again"*.

The link list says why:

    < ACL  20:64:DE:CF:F3:AD   (Middleton, playing)
    < ACL  08:EB:ED:26:14:12   (Oontz)
    < eSCO 08:EB:ED:26:14:12   (Oontz -- HSP/HFP)

The second device had taken an **eSCO** link as well as an ACL one -- the
HSP/HFP headset profile `[PI3-FOUND-290]` -- and a synchronous link does not
share the radio with A2DP, it pre-empts it.

So the keeper now disconnects any speaker holding a link while another is
playing, which `[PI3-AIM-070]` authorises directly. Guarded on the stream
actually playing on a real sink, so it cannot fire during startup while sinks
are still appearing, and it never targets the device the audio is going to --
verified by dry run against the live adapter, which selected the playing
Middleton as **KEEP** and nothing for disconnection.

**Not yet exercised on a real second connection.** The Oontz declined to
reconnect when the rule was deployed, so the disconnect branch has not fired
in anger. The half that could do damage -- picking the wrong device -- is the
half that was tested.

**This also explains the earlier interference report.** At 5:32 the listener
powered the Middleton on while the Oontz played, and heard four stutters over
two minutes `[PI3-FOUND-590]`. A second device negotiating profiles in the
band is not free, and at its worst -- a synchronous link -- it is total.

### `[PI3-FOUND-700]` "Nobody chose" and "I could not ask" are different answers

From the appliance's own log, 2026-09-10 19:35:14:

    adopted 08:EB:ED:26:14:12 as the speaker (none was chosen)

One very much was. Adoption was guarded on `SPEAKER` being empty, and that
conflates two different facts: a query matching no rows returns an empty
string, and **so does a query that could not run** -- a locked database, a
moment's contention with the player writing listener state. The appliance then
concluded nobody had ever chosen a speaker and wrote whatever happened to be
connected over the listener's choice.

This is the `[PI3-FOUND-310]` failure occurring *through* the guard built to
prevent it, and it is quiet: nothing is audible, nothing fails, and the only
symptom is that the appliance prefers a different speaker at every future boot.
It explains an earlier puzzle in this session -- `speaker_address` changing
from the Oontz to the Middleton with nobody having touched the settings panel.

`sqlite3` returns 0 for a query that matches no rows and non-zero when it could
not ask, which is exactly the distinction needed, so the read's exit status is
now kept. Adoption requires a read that succeeded **and** came back empty. A
failed read also skips the trust handback, since handing the standing
invitation to a guess is the same mistake in a quieter form.

Verified by pointing the keeper at a database that does not exist: no adoption,
no trust changes, and the recorded choice untouched.

**And that fix shipped with a regression that disabled the appliance.** The
edit appended an `exit 0` after the trust handback, which returns before the
chase and the fallback ever run -- so with nothing connected, nothing could
ever connect. Found 78 seconds after the listener powered their speaker on and
nothing happened.

The verification run had already shown it and it was read as success. With both
speakers off, the tick printed only the two trust lines and no *"did not answer
in 15s"*; that missing line was the whole symptom. **A tick that does less than
expected looks exactly like a tick with nothing to do**, which is why the check
should have been "did the chase run", not "did anything look wrong".

**The listener's choice is still wrong on this appliance** -- it reads the
Oontz because of the overwrite above, and only the listener knows which speaker
they meant. Picking one in the settings panel now sticks.

