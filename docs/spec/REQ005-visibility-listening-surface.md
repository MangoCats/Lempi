# REQ005: Visibility — The Listening Surface

**Requirements — the speaker, the clock, the history and the controls**

Split from [REQ002](REQ002-functional-requirements.md) on 2026-09-10, which had reached 1,036 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [REQ002](REQ002-functional-requirements.md) is the index for the functional requirements

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1 in [REQ003](REQ003-audio-playback.md), §3 in [REQ004](REQ004-visibility-provenance.md), [REQ005](REQ005-visibility-listening-surface.md) and [REQ006](REQ006-visibility-words-and-the-rest.md), §2 and §4-8 in [REQ002](REQ002-functional-requirements.md).

**`[REQ-VIS-268]` A scan that did not happen is never presented as a scan that
found nothing.** *(Fixed 2026-09-20.)* *Find a Speaker* must distinguish three
answers the listener cannot otherwise tell apart: the radio looked and found
nothing new, the radio was never asked, and the adapter is not working. Until
this was fixed it gave all three the same reply — the remembered speakers, and
*"put yours in pairing mode, then look."*

The mechanism was a discarded output stream: `lempi-btctl scan` sent
`bluetoothctl` to `/dev/null` and then listed BlueZ's *cache*, so a refused
discovery still returned `{"ok":true, …}` with plausible content
`[PI3-FOUND-740]`. Reported when a new speaker four feet away in pairing mode
could not be found; the adapter had in fact been wedged for two days
`[PI3-FOUND-730]`, and BlueZ was refusing every request with
`org.bluez.Error.InProgress` before anything reached the air. See
[PI027](../../LempiPi/PI027-the-controller-wedge.md) for the measurement.

So `Discovery started` is now required as proof that the radio actually
looked. Without it the helper answers `ok:false` and carries a reason, the
remembered speakers are labelled as remembered rather than found, and a
controller that is not answering HCI commands is named as such — because
"the adapter needs a reboot" is something the listener can act on and a raw
D-Bus error is not. **This is a required property, not a nicety:** an
appliance whose diagnostic surface reports a cache as a measurement sends
every future investigation after the wrong problem.

**`[REQ-VIS-260]` The chosen speaker is remembered by Lempi, not guessed by a
script.** *(Fixed 2026-08-27.)* `use`/`pair` now write the address to
`player_settings`; the appliance's own reconnect timer (`lempi-speaker`,
`[PI3-AIM-020]`) reads it back instead of carrying one compiled into the
script. Reported as "playback is skippy" and traced from there: the timer had
no durable record of which speaker was current, so it kept paging a device
left over from early testing every 30 s. Paging a device the shared Bluetooth
radio cannot reach ties the radio up for several seconds, stalling whatever
*is* playing — audible as a skip, the on-screen position frozen for the
duration, and invisible to the player's own underrun counter, since the stall
never reaches the output ring at all. See [PI003 §1a](../../LempiPi/PI003-choosing-a-speaker.md#1a-what-the-listener-should-experience)
for the full account.

**`[REQ-VIS-255]` A programme is chosen against the listener's own clock, and
the control that reverts to automatic actually reverts.** *(Fixed
2026-08-24.)* Two faults reported together, both in service of
`[SPEC-DIR-185]`.

**The wrong programme was engaged.** `listener_settings.utc_offset_minutes`
governs what "local" means to `Programs::active` `[SPEC-DIR-180]`, and
nothing had ever written it — every library sat at the column's own default
of 0, so every programme was chosen against raw UTC clock time. Reported at
local 11:34 (UTC 15:34): Groove, which starts at 15:00, was on; Light, which
starts at 10:00 and should have run until Cool's 12:00, was not. The player
now asks the OS for its real offset once at startup, before the Director
reads it, and writes it back only when it disagrees with what is stored, so
a DST change self-corrects on the next restart instead of running an hour
off until someone notices. Not re-asked on an explicit library reload
mid-session -- rare enough, and startup already close enough behind it, that
the gap was left rather than opening a second writable connection to close it.

> **Not folded into `Programs::load` itself.** That function is exercised
> directly by a great many fixture-backed tests, every one of them relying on
> an absent `listener_settings` row reading as offset 0 to keep their
> time-of-day assertions independent of whichever timezone happens to run
> the suite. The OS ask lives in `PlayerStore::sync_utc_offset`, called once
> from `Session::open` -- deliberately not `Library::director()`, which was
> the first version's mistake: `Library`'s connection is opened read-only
> ("the player must not be able to corrupt the library"), so a write
> attempted there fails silently every time. It looked finished, ran on
> schedule, and never once reached the disk. `PlayerStore` is the one
> connection this process holds that can actually write.

**"Autoselect by clock time" re-checked itself the moment it was
unchecked.** The MuLibPlay skin only ever sent a command when the box
*became* checked (`Lempi.program('auto')`); unchecking sent nothing, so the
next snapshot — twice a second — read `program_manual` as still false and
put the tick back. Manual mode is a specific programme id, not a bare flag,
so there was never anything for a bare uncheck to mean. It now freezes on
whichever programme is engaged at the moment of unchecking, giving the
control a real, stable off-state instead of a dead end.

**`[REQ-VIS-250]` A play-history page, pageable and scrollable.** *(Built
2026-08-23.)* A third panel in the Lempi skin, opened the same way as
Settings and mutually exclusive with it: the most recently played and
skipped passages, newest first, each showing **title**, **artist**,
**album**, **what percentage of the passage was heard**, and **whether it
counted as a play or a skip** `[SPEC-PLAY-030]`. Paged at **10, 100 or
1000** rows, default **100**, with Prev/Next and a "page N of M" readout.

**Its own fetch, not the socket's snapshot.** A page of what has already
happened is not "what is true right now", and teaching the wire format to
paginate would serve nowhere else it is used. `GET /history?page=&size=`
reads `listener_play_history` and `listener_rejections` off the engine
entirely, the same way `/browse` does, so a long scroll back through
history cannot get in the way of playing the next track. A `kind='dequeue'`
rejection never appears here: it never sounded, so it is not a *play*
history `[SPEC-PLAY-050]`.

**The percentage is corrected, not frozen at the threshold.** `record_play`
still writes the moment half the passage (or four minutes) is crossed
`[SPEC-PLAY-030]`, so a crash right after still counts the play — but the
figure written then is only the threshold just reached, not what was
actually heard. The engine corrects that row once the passage is actually
done sounding. A skip writes its final figure directly, since a skip never
leaves anything draining behind it to wait for.

> **Corrected again, 2026-08-24: "departs" is not "is heard".** The first
> version wrote the correction the instant the passage left `live`, which is
> the moment its DECODER is exhausted — up to a ring's depth (`BUFFER_FRAMES`,
> ~15 s here) before its last sample reaches the speaker `[REQ-VIS-240]`. A
> track played all the way through therefore read as ~94%, never 100%,
> however completely it was actually listened to: the figure was frozen at
> "decoded", the same mistake the ring's-depth fix already corrected for the
> position display, made again in the one place that fix did not reach.
>
> The correction is now **held until the clock says the drain is done** — the
> same `(position, instant)` pair `draining` already carries for the display,
> read again for this. A skip or a seek that wipes the ring out from under a
> still-draining correction takes whatever it had reached as final, rather
> than leaving it waiting for a tail that will never arrive.

> **Absent, not zero.** `heard_ms`/`span_ms` are new columns on both tables,
> migrated onto an existing library the same way `id_reviews` gains its
> columns — `ALTER TABLE ... ADD COLUMN`, ignored where it already exists.
> A row written before this shipped has neither, and reads as an absent
> percentage (`—`) rather than a claimed 0%, the same distinction
> `counts_as_play` already draws for an unknown span `[GOV-SRC-040]`.

**`[REQ-VIS-240]` The position runs to the end of the track, not to the end of
the mixing.** *(Fixed 2026-08-23.)* The elapsed time and the progress bar stopped
about fifteen seconds short of every track and sat there until the next one
began.

A passage leaves the mixer when its decoder is exhausted, which is a ring's
depth — about 15 s here — before its last sample reaches the speaker. The
display already knew it had to cover that window and kept the *title*; it looked
the position up in the list of passages being mixed, which is the one place the
passage had just been removed from, so the number stopped.

**It is advanced by the clock, and that is the point.** The obvious measure —
what was mixed, less what is still buffered — is wrong during a crossfade,
because the incoming passage is filling that same ring and its depth says
nothing about how much of the outgoing one is left. What is left is simply time,
and audio plays at one second per second. Capped at the passage's own end, so
the clock cannot run past the music however long it sits there.

> Present since 2026-08-14 and reported by a listener, not by a test — the kind
> of fault that is invisible from a terminal and obvious from a chair.

**`[REQ-VIS-235]` The Lempi skin shows where the sound is actually coming
from.** *(Requested 2026-08-23; built.)* Immediately below the State and
Underruns display: the **system path and filename** of the file being played,
with the passage's **start offset**, **end offset**, and the **total audio
length of the file**.

**Why it is worth the space.** Everything else on that page names what Lempi
*believes* it is playing — title, artist, album, all resolved through
MusicBrainz. None of it names the bytes. When a listener hears something other
than what the page says, the first question is whether the player is wrong about
the metadata or about the file, and there is currently nothing on screen that
separates the two. That question cost an hour on 2026-08-23 `[PI-CHR-080]`:
the audio was Genesis exactly as displayed, and the speaker was listening to
another device entirely — a fact a path on screen would not have proved, but a
start offset inside a 244-minute capture would have made the alternative
explanations checkable in seconds.

**Start and end matter as much as the name.** A third of this library is
passages inside long captures `[SPEC-MPD-052]`, where "playing Aqualung.mp3" is
almost no information: the same file holds fourteen passages across 64 minutes.
`2552 s → 2710 s of 3840 s` says where in it the needle is, and makes a
mis-trimmed span visible as a number rather than as a listening complaint.

**All four are on `QueueEntry`.** `path`, `start_ms` and `end_ms` were already
there; the file's own length is carried too, as `file_ms` — distinct from
`QueueEntry::duration_ms()`, which is the passage span, not the file's. It is
widened where `Library::passage` builds the entry, the honest place: the queue
entry is what crosses to a backend `[SPEC-BK-030]`, and a length it did not
carry would be a length the far side could not show either.

> **The path names the listener's filesystem**, and it should stay as local as
> the browser it is drawn in. It belongs in the snapshot the local UI reads and
> nowhere that travels `[SPEC-DF-055]` — a file path is already the weakest of
> the three identities `[SPEC-DF-030]` and the only one that is nobody else's
> business.

**`[REQ-VIS-230]` The underrun count can be restarted, and says what it counts
from.** *(Requested 2026-08-23; built.)* A **Restart** button beside the
Underruns label in the Lempi skin, and **"since {date time}"** to the right of
the count. Clicking it zeroes the displayed count and captures the moment.

**Why it is wanted.** The count is cumulative for the life of the process, so it
answers "has this ever glitched" and cannot answer "is it glitching *now*". A
number that only ever grows stops being read: after a rough hour, a clean day
looks identical to another rough one. Restarting it turns the display into a
question about the present, which is the question a listener actually has.

Three things fall out of the shape, and were reasoned through before this was
built rather than discovered during:

* **The underlying counter is not reset.** It keeps running, per process, as it
  does today — the button moves a **baseline** (`underrun_baseline` in
  `player/src/engine/mod.rs`), and the display shows the difference
  (`underruns_since_reset`). Resetting the real counter would throw away the
  one number that answers the other question, and would mean the diagnostic
  lied to whoever was not looking at the button.
* **"Since" has an answer before anyone clicks.** It starts as the moment the
  process began, so a fresh player reads *since 09:14* rather than *since
  never*. That is also the honest label for the count it is showing.
* **A baseline cannot outlive its process.** The counter starts at zero on every
  start, so a baseline persisted across a restart would subtract a number that
  no longer exists and show a negative or absurd figure. It is therefore held in
  memory and re-seeded at startup — deliberately *not* in `player_settings`
  `[SPEC-SC-099]`, which is where a setting would otherwise go.

> **Startup is not a fault.** The count includes the ring filling for the first
> time as the device opens — 346,674 samples within seconds of launch, measured
> on the appliance `[PI-CHR-050]`. A restart button is also the cure for that:
> one click after startup and the display describes listening rather than
> booting.

**`[REQ-VIS-225]` The progress bar is a control, not only a display.** Clicking
anywhere along it moves to that point in the passage — the one thing a listener
reaches for that MuLibPlay never had, and that the Lempi skin drew but did not
answer.

**Click only, deliberately.** Dragging would mean a continuous stream of seeks,
and each one on the local engine costs a file open, a seek and a resampler
build. A click is one of those; a drag is one every frame.

**It lands alone.** Mid-crossfade both passages go and the sought one returns by
itself, because resuming an overlap the listener has just left behind is not
what they asked for.

**Offered only where the live backend can honour it** `[SPEC-BK-040]`. The bar
is marked as a control from the backend's own `seek` capability, so a side that
cannot seek shows a plain display rather than a control that does nothing.

**And seeking is not listening** `[SPEC-PLAY-012]`. The distance travelled earns
no credit toward a play — the accounting had to change from position to time
heard before this could be built at all.

> Both skins get it: the Lempi skin's existing bar becomes clickable, and the
> MuLibPlay skin gains one full width under the volume reading. The arithmetic
> lives in `core.js`, so the two cannot drift.

