# REQ003: Audio Playback Requirements — `AUD`

**Requirements — what the player must do with sound**

Split from [REQ002](REQ002-functional-requirements.md) on 2026-09-10, which had reached 1,036 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [REQ002](REQ002-functional-requirements.md) is the index for the functional requirements

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1 in [REQ003](REQ003-audio-playback.md), §3 in [REQ004](REQ004-visibility-provenance.md), [REQ005](REQ005-visibility-listening-surface.md) and [REQ006](REQ006-visibility-words-and-the-rest.md), §2 and §4-8 in [REQ002](REQ002-functional-requirements.md).

## 1. Audio Playback — `AUD`

**`[REQ-AUD-100]`** Play the user's audio files with the **decoded audio stream unaltered** `[SPEC-DF-020]`. Verifiable, not merely asserted: `md5_encoded` before and after any Lempi operation must match.

**`[REQ-AUD-110]`** Decode by **streaming**, never whole-file. Bounded per-passage buffers `[GDE-ARC-050]`. The library contains a 244.9-minute file that needs ~2.6 GB decoded `[GDE-V1-030]`; this requirement is what makes it playable at all.

**`[REQ-AUD-120]`** Play any passage as a span of a larger file — a DAO file holds up to 40 `[GDE-BMK-020]` — without decoding the portions outside it.

**`[REQ-AUD-122]` Passage boundaries are sample-accurate, not packet-accurate.** A decoder seek lands on a container packet and reports where it actually landed; the remainder must be discarded so `start_ms` means `start_ms`. Measured drift when the reported landing was ignored: **648 frames, 14.7 ms**, on every passage with a non-zero start. It is inaudible in isolation, which is precisely the danger — it silently shifts every trim point, and because the passage length is measured from the *requested* start, it drags the end boundary along with it.

**`[REQ-AUD-130]`** Crossfade between consecutive passages, `lead_in_ms`/`lead_out_ms` timing when the overlap is permitted and `fade_in_ms`/`fade_out_ms` `[SPEC-SC-046]` actually ramping gain across it, together with gain `[SPEC-SC-040]`.

**`[REQ-AUD-140]`** Resume playback state across restart, including position within a passage `[SPEC-SC-098]`.

**`[REQ-AUD-142]` Playback has exactly two states: playing and paused.** There is no "stopped". Pausing halts only the *consumer*; decoders keep filling their buffers, so resuming is instant and the pipeline stays primed after the initial power-on fill. A brief underrun at first start is therefore expected and acceptable; if it proves audible, the remedy is to prime the output before commencing, not to add a third state.

> **Halting the consumer means stopping the output device, not merely declining to submit.** The ring holds ~14 s and the device callback drains it regardless, so a pause that only stops submission leaves the music playing for another fourteen seconds — observed directly: reported position climbed 49.7 s → 51.7 s while paused. Stopping the stream leaves the ring full, which is what makes resuming instant. Where a backend cannot pause, the caller must be told rather than assume silence.
>
> **Underruns are counted only while playing.** A paused player underruns continuously by design; counting those buries the fault the number exists to expose. Before this distinction the idle figure reached 479,232 samples (~5 s); measured during real playback it is 0.

**`[REQ-AUD-150]`** Audio output is **co-located with the server**. Remote devices control; they do not receive streams.

**`[REQ-AUD-152]` Master volume is applied at the output device, in the callback** — not to samples on their way into the ring. Anything applied before submission is heard only after everything already submitted has drained, so with a ~14 s ring the control appeared to lag by ten seconds or more: the knob was governing audio computed far ahead of the ear. Applying it in the callback means a change reaches samples that are already buffered but not yet heard, which is precisely the audio a listener expects a volume knob to affect.

> This is the same buffer-depth trap as pausing by declining to submit `[REQ-AUD-142]`, and it recurs for any control the listener expects to act *now*. The rule generalises: per-passage properties (gain `[SPEC-SC-040]`, crossfade) belong before the mixer, because each side of a crossfade carries its own level; listening controls belong at the device.
>
> The value crosses to the callback as an atomic, not behind the ring's mutex. The callback must never block, and must be able to change level even on a tick where it cannot take that lock.

**`[REQ-AUD-154]` The master level is expressed in decibels, −72 dB to 0 dB**, `amplitude = 10^(dB/20)`. Loudness is perceived in ratios, so dB is the unit in which a listener's judgements are actually even; amplitude is not.

**The control is captioned with its own computed value** — "−32.0 dB", not "50 %". A percentage of travel is not a quantity the listener can act on. The figure displayed is the figure sent, so the caption cannot differ from the level in force.

| dB | amplitude | |
|---:|---:|:--|
| 0 | 1.000 | full scale |
| −6 | 0.501 | half amplitude |
| −20 | 0.100 | |
| −40 | 0.010 | |
| −72 | 0.00025 | bottom of travel |

> **There is no mute position.** An earlier revision reserved the very bottom of the travel for silence, following MuLibPlay, which closed hard below −8191 on its `-8192..=0` slider. Specifying the control's full range as −72…0 dB leaves no position for it. Nothing audible is lost: −72 dB is inaudible through any normal amplifier, and pause stops the output device outright `[REQ-AUD-142]`, which is the honest way to silence a player. If a detent below −72 is ever wanted, that is where it goes.
>
> The 72 dB span is the one figure here not inherited from MuLibPlay, which used 64 dB.
>
> **Amplitude is the internal representation; dB is the listener's.** The engine, the device and the saved resume point all speak amplitude — it is what multiplies samples. Only the control speaks in dB, converted at the edge of the HTTP layer.

**`[REQ-AUD-156]` The control's travel is quadratic in dB, flat where it meets full scale.** With `x` as travel from left (0) to right (1):

```
dB(x) = −72 × (1 − x)²
```

Zero slope at the top is what the curve is for: it spends most of the control's pixels on the top of the range, where listening actually happens, and compresses the bottom, which is inaudible anyway. On a 112-pixel control that is **0.007 dB per pixel** near full scale against **1.25 dB per pixel** at the far left. The control moves with single-pixel precision (`step="any"`) rather than in fixed increments.

| travel | dB | amplitude |
|---:|---:|---:|
| 0 | −72.0 | 0.00025 |
| 1/4 | −40.5 | 0.0094 |
| 1/2 | −18.0 | 0.126 |
| 2/3 | −8.0 | 0.398 |
| 3/4 | −4.5 | 0.596 |
| 1 | 0.0 | 1.000 |

> **The specification was over-determined, and the 2/3 point is what gave.** It asked for −72 dB at the left, −6 dB at 2/3, 0 dB at the right, and zero slope at the top — four conditions on a curve with three coefficients. Zero slope with `dB(1) = 0` forces the form `a(1−x)²`, and `dB(0) = −72` then fixes `a = −72`, putting 2/3 at `−72/9 = −8 dB`. Honouring −6 dB at 2/3 instead would raise the left end to −54 dB; fitting all three points exactly without the slope condition gives a curve that rises to +0.28 dB at x = 0.94 before falling back, which is non-monotonic and overshoots full scale. Both endpoints and the flat top are kept exact; the 2/3 point sits 2 dB low.
>
> **Displayed to a tenth of a dB, and sent as the displayed figure.** A tenth is far below what anyone can hear, so quantising to it loses nothing audible and guarantees the caption is the level in force. Near full scale the curve is flat enough that several adjacent pixels read the same figure — that is the zero slope behaving as specified, not a loss of precision.
>
> **This is the control's geometry, not audio, so it lives in the control.** The engine owns dB-to-amplitude and never sees a position; the browser owns position-to-dB and never sees an amplitude. The floor is sent to the browser rather than written there twice, so −72 exists in one place.

**`[REQ-AUD-164]` What is reported as playing is what is being *heard*.** A passage becomes current when its first sample leaves the ring for the device, not when the mixer starts on it — those are a ring's depth apart, so the display announced each track some fourteen seconds before it could be heard, cover art and all.

The test is `frames_mixed` against the ring depth, deliberately in **frames rather than position**: a resumed passage starts at a non-zero position and would otherwise announce itself the instant it was admitted. Skip is the exception and hands the display over at once, because it cuts the ring to the fade and the incoming passage really is audible within a second.

> **Measured:** resumed 40 s from the end of a passage, the title changed at **40.0 s** — exactly when the outgoing passage stopped reaching the device.
>
> **"Coming up" follows the same clock.** A passage leaves the queue when the mixer admits it, which is up to a ring's depth before anyone hears it — so the next track used to vanish from the list while the current one was still playing. Anything admitted but not yet audible now sits at the top of the list rather than being gone from it. Measured across a real handover: the next track stayed listed throughout, became uneditable 15 s before the change — the ring depth — and left the list at the moment it was heard.
>
> **What the mixer holds cannot be edited**, and says so `[REQ-VIS-185]`: its audio is already partly in the ring, so removing it from the queue would change nothing anyone could hear. The controls are disabled rather than absent, because a control that vanishes teaches less than one that explains itself.
>
> The reported passage outlives `live`, because a passage stays audible for a ring's depth after the mixer has finished with it. Blanking the display at that point would be the same fault mirrored.
>
> **This is the fourth instance of one fault** `[REQ-AUD-142]`, `[REQ-AUD-152]`, `[REQ-AUD-158]`. Pause had to stop the device, volume had to move into the callback, skip had to cut the ring, and now the display has to lag the mixer. The rule has earned its generality: **anything the listener perceives is downstream of a 14 s buffer, and anything measured upstream of it is measuring the wrong moment.**

**`[REQ-AUD-158]` Skip cuts the output ring short and fades what remains.** Dropping the passage upstream is not enough, and the code claimed otherwise: it discards the *decoder's* buffer, but the output ring still holds every sample already mixed. Measured, that was **14.0 s from button to new music** — the ring's full depth. The reported title changed in 0.5 s, so the display said one thing while the speakers said another for fourteen seconds.

**`[REQ-AUD-160]` The next passage is opened and decoded before anyone asks for it.** It is held in a prepared slot outside the mixer's `live` set — fed by the same decoder top-up, but not summed, so it is ready without sounding. Promotion is a move. Skip used to pay for a file open, a seek and a resampler build at the moment the button was pressed. The same slot serves ordinary crossfade admission, so the prepared path is the normal path and cannot rot from disuse.

**`[REQ-AUD-162]` A skip is a crossfade, not a stop followed by a start.** The outgoing passage falls away over `skip_fade_ms`; the incoming one begins its normal fade-in at `skip_lead_ms`; for the difference between them the two are **summed**. Both are adjustable while playing:

| | default | range | |
|---|---:|---|---|
| `skip_fade_ms` | 2.0 s | 0 – 10.0 s | outgoing fade-out |
| `skip_lead_ms` | 0.5 s | 0.1 – 2.0 s | when the incoming starts |
| *overlap* | *1.5 s* | | *the difference* |

A lead longer than the fade is legal and leaves silence between the two; the UI reports which of the three it is rather than letting a gap come as a surprise. The engine clamps, and the browser is sent the limits rather than keeping its own copy.

> **The fade is applied on the mixer thread, not in the callback** — the opposite of volume `[REQ-AUD-152]`, and for a reason worth stating: the incoming passage is summed into those same samples, so a fade-out evaluated in the callback would drag the newcomer down with the passage it is replacing. Cut, fade and overlay happen under one lock, so no callback can observe a ring that is cut but not yet faded.
>
> **The overlap is affordable only because the passage is already decoded** `[REQ-AUD-160]`. The 1.5 s laid over the outgoing tail is lifted straight from its prepared buffer, with its fade-in already applied on the way in `[XFD-ORTH-020]`, and through `mix` rather than by reading the ring directly so the accounting is identical to an ordinary tick.
>
> **Measured, on desktop hardware:**
>
> | | button to new music | underruns |
> |---|---:|---:|
> | drop the passage upstream only | 14.0 s | 0 |
> | cut the ring, 2 s fade, cold open | 2.5 s | 0 |
> | cut and overlay, 2 s fade / 0.5 s lead | **0.6 – 1.0 s** | 0 |
>
> Each figure includes up to 500 ms of snapshot-push granularity in the measurement itself. Clamping verified at both ends: 20 s → 10 s, 50 ms → 100 ms. HTTP stayed responsive throughout — median 12 ms, maximum 28 ms across ~2,700 requests spanning six skips.
>
> **Unverified on a Pi Zero 2W**, where the margin is far thinner: cutting to the fade length leaves the mixer only that long to refill ~13 s of ring.
>
> **Not persisted.** Both settings return to their defaults on restart. The resume row `[SPEC-SC-098]` carries volume but not these.
>
> **A passage part-way through an ordinary crossfade is discarded, not carried over.** It is already mixed into the ring alongside the outgoing one and is faded out with it; its decode ran a ring's depth ahead of the ear, so what the listener actually heard of it is nothing. The prepared passage takes its place.
>
> **The curve is `Exponential`, i.e. linear in dB** `[XFD-EXP-020]`. `Linear` and `Cosine` are equally available in [`fade.rs`](../../player/core/src/fade.rs) and the choice is one word; it has not yet been listened to.
>
> This was the third instance of one fault `[REQ-AUD-142]`, `[REQ-AUD-152]`: **a control the listener expects to act now cannot be implemented upstream of a 14 s buffer.** Pause had to stop the device, volume had to move into the callback, and skip has to reach into the ring. Any future control of this kind should be assumed to need the same treatment until shown otherwise.

> **Verification:** `[REQ-AUD-110]` is gated by [`memcheck`](../../player/src/bin/memcheck.rs), which decodes a passage of any length through the fixed-capacity buffer and **fails** above 150 MB peak RSS `[REQ-HW-100]`. It needs a long file from a real library, so `verify-targets.sh` runs it only when `LEMPI_LONG_FILE` names one and reports **SKIPPED**, never passed, when it does not.
>
> This previously read "gated by an automated test playing the 244.9-minute file at ≤150 MB RSS and ≤500 ms skip latency". Two parts of that were untrue: nothing invoked the gate at all, and **no test measures skip latency** — `memcheck` does not, and the word now means the Skip control `[REQ-AUD-158]`, which is 0.6–1.0 s by design. `[REQ-AUD-120]` — playing a passage as a span of a larger file without decoding the rest — is exercised by the decoder's own tests and by every DAO passage the player opens, not by a dedicated gate.
>
> `[REQ-AUD-140]` verified end-to-end on desktop hardware (48 kHz device, 44.1 kHz sources, 8,079-passage library): a run interrupted at ~16 s saved 15.01 s, and the next run resumed the same passage at 15.0 s and went on to save 25.01 s — position advancing *from* the resume point, not restarting. The 15.01 s figure is also the check on audible-versus-mixed position: had the mixed figure been saved it would have read ~29 s.

> ### Known, accepted, and deferred
>
> Recorded from the review of 2026-08-14 so they are not rediscovered from scratch. Each was judged, not missed.
>
> **Audited 2026-08-15**, because a debt list that reports finished work as outstanding is worse than no list: it spends the reader's attention on nothing and teaches them to distrust the rest of it. Every entry was checked against the code rather than against memory. Two had been resolved and are struck through with what settles them; two carried line counts that had drifted; one — the display-name rule — was found to be exactly right as written and left alone.
>
> **Position freezes briefly at a handover** *(cosmetic, accepted)*. When the passage being displayed leaves `live` before the next becomes audible, its reported position holds its last value instead of advancing. Bounded by the ring depth and invisible unless watched closely `[REQ-AUD-164]`.
>
> ~~**A passage that fails to open is dropped by the engine but still counted as queued by the Director**~~ *(correctness, resolved)*. `prepare_next` advanced past it while `note_queued` had already recorded it, so rotation history counted a passage that never played. The engine now collects them — `Engine::dropped`, pushed at both drop sites — and `take_dropped()` is drained in `session.rs`, which tells the Director to forget them. **Verified 2026-08-15**, by following the value from where it is pushed to where it is consumed.
>
> **The display-name rule is stated twice** *(SSOT, outstanding)*. `QueueEntry::title` resolves MusicBrainz → tag → **filename**; the browse SQL resolves MusicBrainz → tag and then filters the rest out. An untitled, unidentified passage therefore plays under its filename but is absent from Browse. Measured at **0 passages** on the present library, so it is latent rather than active `[REQ-VIS-170]`, `[REQ-VIS-180]`.
>
> ~~**The three skins each carry the same behaviour**~~ *(DRY, resolved)*. Volume drag handling, queue rendering and the fader conversion appeared in all three. They now live behind binders in `core.js` — `bindVolume`, `bindQueue`, `bindProgram`, `queueRow`, `showArt`, `named`, `badge` — and the three skins are 79, 166 and 85 lines, each calling into core ten to twelve times. **Verified 2026-08-15.** WinAmp remains the proof the contract is real: a fixed-width appliance with its own geometry and a scrolling title, needing nothing the document-shaped skins did not `[REQ-VIS-160]`.
>
> **`publish()` makes presentation policy inside the audio engine** *(maintainability, outstanding)*. Which passage the listener is on, and how much queue a display gets, are display decisions living in `engine/mod.rs` (formerly `engine.rs`, split 2026-09-02 — file reorganization only, this concern moved with it unaddressed) — **1,088 lines** as of 2026-08-15, not the 722 first recorded.
>
> ~~**`db.rs` and `web.rs` each mix several concerns**~~ *(maintainability, resolved differently than predicted)*. `web.rs` was named first at **837 lines** — routing, serialisation, browse, art and queue verbs. `db.rs` had become the larger problem at 1,635 lines here, and grew further to 4,149 by the time it was addressed — holding the read path (`Library`), the write path (`PlayerStore`), the browse SQL and the identification-review logic. Both were split on 2026-09-01/02, but not along the seams predicted here: `db.rs` fell to `db/{mod,library,player_store}.rs` (the load-bearing read/write distinction, not `db/browse.rs`/`db/review.rs`/`db/naming.rs` as this entry once guessed) and `web.rs` to `web/{mod,browse,review,musicbrainz,media,edit,settings,bluetooth,skins,control}.rs` — ten topic files, not the three or four implied above. `[SPEC-APS-110]` in [SPEC011](SPEC011-audio-path-supervisor.md#4-migration-order) records the same reversal from its side (it had called this explicitly out of scope) with the measured line counts that justified doing it after all.
>
> ### Recorded 2026-08-15: recording ids are not trustworthy
>
> **A sample of 2,000 radio passages, checked against the files' own tags** — evidence the ids did not come from:
>
> | | | |
> |---|---:|---|
> | agree on title and artist | 61.0 % | |
> | right artist, title differs | **33.9 %** | a mixture, see below |
> | right title, artist differs | 2.4 % | usually a credit difference |
> | agree on neither | **2.8 %** | plainly wrong |
>
> **2.8 % are simply the wrong song** — ~220 passages. Two examples sit next to each other: passages tagged *Magic Man* and *How Can I Refuse* by Heart carry MBIDs for *Breakdown* and *Learning to Fly* by Tom Petty. A whole album mis-assigned.
>
> **The 33.9 % is a mixture, and its shape is the diagnosis.** Some is legitimate naming variation — `Stoned Immaculate` against `Angels and Sailors / Stoned Immaculate`, `Karn Evil 9: 1st Impression, Part 2` against `Karn Evil 9`. But much of it is the **wrong track of the right album**: `Suffragette City` against `Ziggy Stardust`, `Woman` against `Happy Xmas (War Is Over)`, `Gemini Dream` against `Dr. Livingstone, I Presume`. Adjacent tracks, same artist.
>
> That pattern says the ids were assigned **by position within a matched album**. Any offset — a bonus track, a hidden track, an edition whose running order differs — shifts every id after it, which is exactly what MuLibPlay's migration would produce and what MCR-SPEC033's cascade `[AM-STG5-010]` exists to absorb.
>
> **Why this cannot be fixed by better metadata matching.** The ids may have been *derived* from metadata; checking them against metadata is checking a claim against its own source. Tags agreeing proves the derivation was self-consistent, not that it was right.
>
> **The reliable scheme is the audio itself** `[SPEC-SA-035]`, `[SPEC-SA-060]`. Chromaprint over the passage's decoded samples, looked up through AcoustID, returns the recordings that actually sound like this audio. It is independent of every tag, every filename and every album match, and it is the only evidence that is. The shape:
>
> 1. `fpcalc` over each passage's span — not the file, since a DAO rip is forty passages in one file.
> 2. AcoustID lookup, rate-limited and cached like the release fetch, keyed on `audio_md5` so a re-run asks nothing twice.
> 3. **Agrees** → the id is confirmed by evidence it did not come from. **Disagrees** → record both and the confidence, and prefer the fingerprint. **No match** → leave the id alone and mark it unverified; absence of a fingerprint is not evidence against one.
> 4. Every outcome to `ingest_decisions` `[REQ-VIS-110]`, because the whole point is that a listener can see which names are known and which are merely believed.
>
> Needs `fpcalc` (not installed here; ARM64 builds exist `[SPEC-SA-018]`) and an AcoustID application key. At one lookup per second the library is about two hours — the same order as the release fetch, and resumable the same way.
>
> **Until then, `[REQ-VIS-120]` matters more than it looked.** A name Lempi shows may be wrong, and the interface says nothing about how confident it is. Provenance display was already required; this makes it urgent.
>
> *Both done 2026-08-15. The fingerprint pass is `[REQ-LIB-165]` above — built on ffmpeg's chromaprint muxer rather than `fpcalc`, so the extra binary never became a prerequisite. Provenance is now shown in all three skins rather than as a tooltip in one, which is the same point made below.*

> ### Recorded 2026-08-14, from the "what next" review
>
**`[REQ-LIB-160]` The listening is backed up; the library is not.** The library file holds two kinds of thing with opposite recovery stories. The **library** — files, passages, recordings, flavor — is derived from the audio on disk, and Vipunen can grind it out again from nothing but time. The **listening** — 37,206 plays, 3,261 preferences, the programmes and their seeds — comes from years of a person using the thing, and nothing can reproduce it. Lose it and the Program Director is a random shuffle with opinions it can no longer justify. Design decision and rationale in `[SPEC-DF-094]`; what follows is what was built to meet it, and how it was measured.

Only the second is copied, and that choice is what makes the scheme work: **2.4 MB against a 553 MB library, 0.4%**. A backup small enough to take hourly is a backup that gets taken.

> **A copy, not a dump.** The output is a real SQLite file — openable, queryable, restorable by attaching it. A schema-and-INSERTs text dump needs a working player to be useful, and the moment a backup matters is the moment there isn't one.
>
> **Written under a temporary name and renamed.** Rename is atomic; a copy interrupted half way leaves a `.part` nobody will trust rather than a truncated file that looks fine.
>
> **The snapshot owns the connection and the library is attached `mode=ro`.** Two reasons, the second being the one that matters: `ATTACH` cannot create a database from a read-only connection, and this way a mistake in the copy cannot write to the thing being protected.
>
> **Grandfather-father-son retention**, because the value of an old snapshot is not that it is old but that it *predates whatever went wrong*. Damage noticed the same afternoon needs yesterday; damage noticed at Christmas needs March; a preference quietly corrupted two years ago needs a copy from before it. So: **one per day for seven days, one per month for twelve months, one per year indefinitely**, and always the newest whatever else happens. Within a period the latest is kept — it holds the most listening.
>
> Three years of *hourly* snapshots, unpruned, would be 26,280 files — thinned by the ladder to **20**: ≈63 GB to 48 MB. *(Corrected 2026-08-30: this previously read "six-hourly snapshots... 4,380 files... 10.5 GB" — a stale figure from an earlier draft of the cadence, left uncorrected when the shipped schedule tightened to hourly below. The retained count, 20, is unaffected: it is set by the calendar ladder — 7 daily + 12 monthly + 1/year — not by how often a raw snapshot is taken.)* The yearly tier is unbounded on purpose; a decade of them is ten files.
>
> The date arithmetic is written out rather than imported — Howard Hinnant's civil-from-days, exact for every date this will see. Approximating a year as 365.25 days drifts a day a century and would silently file a snapshot under the wrong year, which is how the only copy of a year goes missing.
>
> **Never fatal.** A player that stops playing because it could not write a backup has turned a precaution into the fault. Failures are reported and playback continues.
>
> **A backup nobody has restored is a file of unknown value.** `restore_listener` puts one back, and **rehearsal is the default**: it reports exactly what a real restore would do and writes nothing until `--commit`. The numbers are the same either way, being measured from the same query before anything is written.
>
> **Passage ids are not stable; recording MBIDs are.** A Vipunen rebuild renumbers passages, so restoring a history by its stored `passage_id` would silently reattribute years of listening to whatever songs hold those numbers now. Every play is re-pointed through its recording instead. Plays whose recording has left the library are **kept as they are** — a play that happened still happened, and discarding it to satisfy a foreign key would lose the only record of it.
>
> The whole restore is one transaction: half-applied would leave the listening in a state that never existed, which is worse than either version.
>
> **A safety copy is taken before committing, and is exempt from rotation.** The first version was not, and it very nearly destroyed what it was protecting: the safety copy and the snapshot being restored fell on the same day, the ladder keeps only the newest of a day, and the source was pruned out from under the restore. Safety copies now carry their own prefix and `prune` never looks at them.
>
> Verified end to end against a copy of the real library: 37,206 plays, damaged to 34,429, restored to 37,206, with 2 orphaned plays kept.
>
> Taken once at startup and hourly thereafter, on its own thread, off the audio path. `cargo run --example backup_now` takes one by hand — before a migration, or to check the thing works before trusting it to. Verified against the live 553 MB library **while Vipunen was writing to it**: 37,206 plays copied, and the derived library correctly absent.

**`[REQ-LIB-165]` Recording ids are checked against the audio, and a person settles the disputes.** Every recording MBID in this library arrived by one route: `source` on all 16,157 rows of `passage_recordings` reads `inherited:mulib`. They are therefore all exactly as good as one migration, and a wrong one is invisible — the player shows a real title by a real artist, and it is simply the wrong song. Nothing downstream can catch it, because everything downstream trusts the id.

> The design, the grading, the review interface and the measured results of the first full pass are in **[SPEC010](SPEC010-identification-review.md)**. Kept there rather than here because this register states *what must be true*, and that one records how it was established — 82.0% of the migrated ids confirmed by evidence they did not come from, and a review queue of 114 cards where a naive reading would have produced 1,433.
> ~~**Listener state has no backup, and it is not reproducible**~~ *(resolved by `[REQ-LIB-160]`)*. The library file holds 37,206 plays, 3,261 preferences, 8 programmes, 49 seeds and 24 occasion points, and the player writes to it continuously. Vipunen can rebuild the library from the audio files; it cannot rebuild the listening history, and the Director is worthless without it. One interrupted write on a Pi takes all of it. The fix is small — a periodic snapshot through the SQLite backup API to a rotating file, the same mechanism the test copies already use.
>
> **Taste is implemented but unexercised** `[REQ-PD-150]` *(feature)*. `listener_likes` holds nothing, so `load_taste`'s Like/Dislike-Taste centroids and Stage B's dislike-filter/like-seed logic (`player/core/src/director/library.rs`, `shape.rs`) have unit tests and no field data behind them `[SPEC-DIR-150]`. `[SPEC-DIR-210/215/220]` are open design rather than settled, so *how* Taste is exposed to a listener remains a design conversation even though the selection-side mechanism is built. Browse is its natural home: that is where a listener is looking at a track when they form an opinion about it.
>
> ~~**Vipunen is specified as a separate project but ships inside this repository under Lempi's licence**~~ *(resolved 2026-08-15: relicensed in place)*. `tools/` now carries AGPL-3.0-or-later — `tools/LICENSE` verbatim from gnu.org, an `SPDX-License-Identifier` line in all 30 Python files so the terms travel with each file, and the root `LICENSE` scoped to say what it covers rather than implying everything. [`LICENSING.md`](../../LICENSING.md) sets out the arrangement and why the direction only works one way. Splitting the repository as `[SPEC-SA-010]` describes can still happen later without changing anyone's terms. Two things noted there and still true: `tools/` mixes Vipunen's pipeline with research scripts and dev utilities, so the line to cut along is undrawn, and `check_docs.py` is AGPL only because of where it sits. The original entry follows.
>
> **Vipunen is specified as a separate project but shipped inside this repository under Lempi's licence** *(licensing, was: decision needed)*. `[SPEC-SA-010]` says Vipunen is *"a separate project: own repository, own licence, own platform envelope"*, and `[GDE-ARC-018]` sets the direction deliberately — Lempi MIT, Vipunen AGPL-3.0, because Essentia is AGPL and MIT code may be incorporated into an AGPL work while the reverse is not true. In the tree as it stands there is **one `LICENSE`, and it says MIT**, covering `tools/` along with everything else.
>
> The *architectural* separation is real and holds: different languages, separate binaries, and `[SPEC-SA-015]`'s single channel — the shared SQLite file — with no linked code in either direction. What has not happened is the legal separation the specs describe. `tools/` also mixes Vipunen's pipeline with research scripts and dev utilities, so the line to cut along is not yet drawn.
>
> Worth settling before anything is published rather than after: a licence is far cheaper to arrange than to re-arrange, and contributors who send patches to an MIT tree have been told something about the terms. The decision is genuinely open — split the repository as specified, relicense `tools/` in place, or record that the spec overreached and Vipunen stays MIT because nothing AGPL is actually distributed here. Any of the three is fine; the current silent mismatch is not.
>
> **Nothing starts the player on boot** *(deployment)*. The Dockerfiles are build targets, not deployment.
>
> **Errors are invisible on an appliance** *(operability)*. Seventeen `eprintln!` sites across engine, session, output and tags (sixteen when first counted; the settings writer added one) — decode failures, dropped passages, unstorable tag rows — all to stderr, on a headless machine with no terminal. Underruns and lock failures now reach the UI; the same treatment for recent faults would make them findable without a shell.
>
> **The HTTP surface has no authentication of any kind** *(security, decision needed)*. Anyone on the network can play, skip, reorder and browse the library. That may be right for a home LAN, but it should be a recorded decision rather than an accident.
>
> ~~**`skip_fade_ms` and `skip_lead_ms` do not survive a restart**~~ *(resolved by `[REQ-VIS-155]`, which found volume did not persist either)*.
>
> **Several audible choices have never been listened to** *(needs ears, not development)*: the skip fade curve `Exponential` against `Cosine` and `Linear`; whether 72 dB of fader travel gives enough resolution where the listening actually happens; whether 1.5 s of crossfade overlap on a skip reads as a transition or a muddle; and whether losing the mute detent at the bottom of the fader matters. Each is a one-word or one-constant change.
>
> **`session.rs` and `tags::backfill` have no tests, and nothing boots the server** *(test coverage)*. The absent integration test is the one that would have caught the queue-insertion and browse faults found by hand.

