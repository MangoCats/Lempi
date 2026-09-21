# REQ004: Visibility — Provenance, Trust and Browsing

**Requirements — why this passage, and where its facts came from**

Split from [REQ002](REQ002-functional-requirements.md) on 2026-09-10, which had reached 1,036 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [REQ002](REQ002-functional-requirements.md) is the index for the functional requirements

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1 in [REQ003](REQ003-audio-playback.md), §3 in [REQ004](REQ004-visibility-provenance.md), [REQ005](REQ005-visibility-listening-surface.md) and [REQ006](REQ006-visibility-words-and-the-rest.md), §2 and §4-8 in [REQ002](REQ002-functional-requirements.md).

## 3. Visibility — `VIS`

Lempi's headline requirement `[GDE-CHT-030]`. Previously specified nowhere.

**`[REQ-VIS-100]` Why this passage?** *(Renamed from "Why this track?" — [SPEC023](SPEC023-domain-vocabulary.md).)* Every automatic selection exposes its full weight decomposition — artist weight, rotation block state, position on the recovery ramp, occasion multiplier, length bonus, distance to each seed, final rank, roulette position — **and the runners-up that lost**.

> **Status: delivered, both halves.** Every Director-chosen passage carries its full Stage-A decomposition — each term separately, never just the product — plus the five heaviest runners-up it beat, its share of the pool, and the pool's size and total weight. Written durably to `selection_decisions` and shown in the web UI. Terms sitting at ×1.0000 are dimmed rather than hidden: "this did not apply" is part of the answer.
>
> **The stages B–D decomposition is now delivered too** `[SPEC-DIR-190]`: distance to each seed, flow distance, rank, and roulette position/target are all recorded, alongside the runners-up that lost and their weights. **Still missing:** the occasion multiplier is recorded as its combined scalar only, not broken out by the characteristic/curve that produced it, and Taste's effect is not yet a distinct field — it is folded into `seed_distances` undifferentiated from programme seeds. Each stored record states which stages ran, so a decision recorded now cannot later be mistaken for a shaped one. A passage the Director did not choose — a resumed one, or one queued before the log began — reports that plainly rather than borrowing another passage's reasoning.

**`[REQ-VIS-110]` How was this identified?** Every ingest decision is a durable record: which stage matched, at what confidence, which candidates were rejected `[SPEC-SA-085]`. This is what converts an undocumented ritual `[GDE-BMK-050]` into a reviewable process.

**`[REQ-VIS-120]` Is this data trustworthy?** Every flavor value displays its provenance and measured accuracy `[SPEC-SC-070]`. A user must be able to see whether a value came from the dump, was locally computed at a stated error, or was entered by hand.

> **Names carry their provenance visibly, in every skin** *(2026-08-15)*. It began as a tooltip in one skin of three, which is no use at all on a phone — the device this interface is mostly read from, and one with no hover. `named()` and `badge()` live in `core.js` so all three skins mark names the same way and each styles `.src` in its own idiom: gold for MuLibPlay, where gold already means "this is the one"; full-brightness LCD green for WinAmp, against half-lit for everything less certain.
>
> Only names that are *shown* are marked. WinAmp has no album line, so an album badge there would qualify something invisible, and its badges live in the stat row rather than the marquee — the marquee scrolls and is cloned to make the wrap seamless, so a badge inside it would drift off the panel and then appear twice.
>
> The badge is a separate element, so anything reading the name still gets the bare name. The check asserts the badges render, that at least two distinct sources appear, and that the name remains a text node of its own — and it was confirmed to fail when the display is reverted, which is the only way to know an assertion is doing anything.

**`[REQ-VIS-122]` MuLibPlay shows names bare** *(2026-08-17)*. The provenance
marks are removed from title, artist and album in that skin only. It is a
reproduction of the original's face, and `MB` / `tag` / `file` are a Lempi idea
the original never had.

This narrows `[REQ-VIS-120]`'s "in every skin", which was itself a deliberate
widening on 2026-08-15, so the reversal is recorded rather than quietly made.
The claim is not abandoned: Lempi and WinAmp still mark every name they show,
and the verifier now asserts the exemption *positively* — MuLibPlay must render
**no** badges, the others must still render theirs. A skin that is supposed to
mark names and silently stops therefore still fails.

**Known cost, accepted.** With `[REQ-VIS-124]` making MuLibPlay the default, a
browser that has never chosen a skin sees no provenance at all — which is the
state `[REQ-VIS-120]` was widened to escape. The name shown may be wrong, and
in that skin the interface no longer says so. Anyone wanting the answer changes
skin; the information is one selection away rather than absent.

**`[REQ-VIS-124]` A browser keeps the skin it chose; a new one gets MuLibPlay**
*(2026-08-17)*. The choice is per browser, not per player: two people on two
phones may want different skins of the same radio, and neither should restyle
the other.

Stored in `localStorage`, not a cookie. The server never needs to know which
skin a browser wears — the shell fetches it — so a cookie would ride on every
request, including the WebSocket upgrade and every art fetch, to carry
something the server does not read. Reads and writes are wrapped because
storage *throws* rather than returning null when a browser is in a private mode
or has site data blocked; unwrapped, that exception lands before any skin loads
and the page is blank rather than merely forgetful.

`?skin=` still overrides, and is remembered when used, so a link can hand
someone a skin and it sticks.

**`[REQ-VIS-130]`** Automatically computed boundaries, lead-in/lead-out points and gain are **reviewable and overridable** through a waveform view `[SPEC-SA-080]`. Fade-in/fade-out and their curves `[SPEC-SC-046]` are reviewable and overridable there too, though not automatically computed — every passage starts from the same fixed default. Manual edits outrank computed values permanently and are never silently recomputed.

**`[REQ-VIS-150]` The listening surface shows what is coming and what is in force.** The queue in play order, the active programme with a manual override `[SPEC-DIR-185]`, and master volume.

> Delivered 2026-08-13. Two implementation notes worth keeping:
>
> **Per-passage `gain_db` was read from the library and never applied to the audio** — it reached `QueueEntry`, was printed by `station`, and was silently dropped before the mixer. The library carries real values (median −3.0 dB), so tracks were playing at whatever level they were mastered at. It is now applied **per passage, before mixing**, so each side of a crossfade carries its own level; applying it after the mix would level the blend rather than the tracks, and the point is that they meet at a matched loudness. Master volume is applied last, over the mixed signal, because it is a listening level and not a property of any passage.
>
> **The Director is not `Sync`,** so the browser cannot reach it. Programme choice is written to a small shared cell that the engine reads on its next refill — an override therefore changes what is selected *next* rather than interrupting what is playing, which is the wanted behaviour. An unknown programme id is a 404 rather than a silent no-op.

**`[REQ-VIS-127]` The cover slot keeps its space, always** *(2026-08-17)*. The
element that holds cover art is a fixed box that is never empty, and never
leaves the layout.

It used to toggle `hidden` — `display: none` — around the load, so at every
track change the art left the flow, the page reflowed shorter, and reflowed
back when the next cover decoded. On MuLibPlay's 200&nbsp;px sleeves that threw
the controls beneath up and down the screen twice per track, and twice again
when the back cover followed. A passage with no picture simply stayed short, so
the resting height differed between tracks as well.

The cost is deliberate: a fixed box means a non-square cover letterboxes inside
it rather than the layout adapting to the image. Reserving space and adapting to
content are incompatible, and the jumping is the fault worth removing.

**`[REQ-VIS-128]` Covers cross-fade, and a missing one shows the kantele**
*(2026-08-17)*. Changing track fades between the outgoing and incoming sleeve
over one second. Two stacked layers, because swapping one element's `src` is
instantaneous and cannot be faded; the swap happens only once the incoming
image has **decoded**, since fading toward an image that has not arrived shows
an empty box for the length of the fade — the artefact being removed.

Where a passage has no embedded picture — roughly a third of this library, so
the ordinary case rather than an error — the box shows a **kantele**: the
instrument of the Kalevala, the same source `Vipunen` takes its name from.
It is drawn from the instrument rather than from an illustration of one: the
strings terminate **on** the varras, the bar at the *narrow* end they are
knotted around, and run to tuning pins at the wide end. Traditional five-string
kanteles have no sound hole, the body being hollowed from beneath, so none is
drawn.

The mark is **inlined into the page, not set as an image source**, because a
data URI is an isolated document and cannot see `currentColor`. Inlined, one
mark takes each skin's own text colour — gold in MuLibPlay, LCD green in
WinAmp, dim grey in Lempi — and is correct in light mode without a second
asset. It also sits *under* both layers permanently, which is what satisfies
`[REQ-VIS-127]`: the box has something in it even before the first cover loads.

**`[REQ-VIS-170]` A passage is named by MusicBrainz where MusicBrainz has an answer.** Three fields, three fallbacks, and every one of them says which source it came from `[REQ-VIS-120]`:

| shown | first choice | fallback | last resort |
|---|---|---|---|
| track | **Recording** title | file tag | filename |
| artist | **Artist** name, by credit | file tag | — absent |
| album | **Release** title | file tag | — absent |

**Recording and Release are different levels of the MusicBrainz model, and the distinction is the reason album is the hard one.** A Recording is a particular piece of recorded audio; its title names that performance. A Release is a published product — this pressing, this edition, this cover — and *its* title is what an album name is. One recording appears on many releases and one release holds many recordings, so the link is a join table rather than a column, and naming an album means choosing *which* release to name. That choice is ingest work, not playback work — and it is frequently a choice with no wrong answer, since several release MBIDs often name the functionally same album; see [SPEC023](SPEC023-domain-vocabulary.md) and [SPEC010 §3](SPEC010-identification-review.md#3-searching-musicbrainz-directly).

**"Album" is not the passage `kind='album'` value `[SPEC-SC-040]`.** Same word, unrelated concept — a passage's `kind` is a playback-style choice (trimmed for rotation vs. full boundaries), never a claim about release identity. `[SPEC023]`'s "Album" entry is precise about the difference.

Artist and album have **no filename fallback**. Guessing a performer out of a path is how a library comes to believe in a band called "02"; absent is the honest answer.

> **Play counts are per recording, not per passage or per file.** The same recording reached through two files is the same thing heard twice, which is also how rotation already counts it `[SPEC-SC-095]`.
>
> **Measured on this library:** recording titles and artist names are present for the whole identified set — 7,912 recordings, 7,924 artist credits. `releases` and `release_recordings` are **empty**, so *every* album name today comes from the file's own tag. A sample of 40 files carried album on 40, artist on 40, title on 38, and embedded cover art on 28. The release tables are queried correctly regardless, so MusicBrainz album names take precedence the moment Vipunen populates them, without a code change.
>
> **Cover art is read from the audio file, never fetched.** Playback must not depend on a live external service `[REQ-NEG-100]`, and the Cover Art Archive is exactly the dependency that forbids. It is served per passage at `/art/{id}` and cached for a day; a file with no picture is a plain 404, which is what lets a skin ask unconditionally and hide the element on failure. Roughly a third of this library has no embedded cover, so that path is the common case, not the exception.
>
> **Naming is not part of selection.** The Director loads the whole radio pool — 8,078 rows — and putting these five correlated subqueries in those columns would run them eight thousand times to answer a question that weighting does not ask. They are fetched for the dozen passages actually on screen instead, once each on the way into the queue, at under a millisecond apiece.

**`[REQ-VIS-180]` The library can be browsed by artist, by album and by track.** MuLibPlay's three "Browse by" pages, which were the one part of its interface Lempi had no answer for. Artist and album are ways *in* to tracks rather than destinations — an artist narrows to their albums, an album to its tracks, a track queues itself **next** — with a crumb trail so the narrowing is reversible.

**Browsing groups by the *displayed* name**, resolved exactly as `[REQ-VIS-170]` resolves it: MusicBrainz where it has an answer, the file's tag where it does not. What you can browse by is therefore precisely what you can see, rather than a second naming scheme that disagrees with the player.

> **The player builds its own tag index, in the background, on first run.** Album names come from the files' own tags and reading them takes ~18 s for 5,590 files — fine once, impossible per request. Doing it at startup on a spare thread is the difference between a feature that works and one that waits for someone to remember a command: the browse pages first shipped needing a manual scan, and came up empty for exactly that reason. It is incremental, so every later start is a no-op, and it is off the audio path entirely. `tagscan` remains for libraries prepared before they are ever played, and for `--all` after files are re-tagged.
>
> **Browsing never dead-ends on an artist.** An artist with no album names yet shows their tracks instead, with a note saying why. "No albums" is a useless answer to "show me this artist", and while the background scan is still running it is a temporary one as well.
>
> **The index is a cost worth naming.** Album has no source but the file's own tag, and reading it means opening and probing every file — 18 seconds for 5,590 of them, fine once and impossible per request. Re-scanning is safe and costs only the files added since.
>
> **Two handles write to the library, and neither is the audio path.** `PlayerStore` creates `file_tags` at startup alongside the resume row, and the background scan opens its own writable connection; `Library::open` stays read-only so the *reading* path cannot corrupt anything. The earlier claim that `tagscan` held "the only writable handle" stopped being true when the player learned to scan for itself.
>
> **Measured on this library:** 5,589 of 5,590 files carry tags and 3,604 carry cover art. Browsing yields 463 artists in 75 ms, 660 albums in 36 ms, and tracks in 80 ms — on demand rather than per tick, so a query is the right answer and a cache would be premature. Tracks are capped at 2,000 rows per response.
>
> **Built for a phone**, which is how these pages were actually used: an alphabet bar rather than a scrollbar, because 463 artists is a long way to drag with a thumb and one tap to the letter is the whole difference. Nothing depends on hover, no tap target is smaller than a fingertip, and the listing is rows rather than a table — a table on a narrow screen either scrolls sideways or crushes the name, which is the thing being looked for.
>
> **Letter headings are the jump targets**, so the bar cannot fall out of step with the list. They are derived exactly as the server sorts: strip a leading "The" for the heading while the `ORDER BY` does not, and "The Beatles" emits a stray B in the middle of the Ts.
>
> **A missing `file_tags` table is a failed query, not an empty result.** The first version shipped without creating it, so every browse page came up blank on a library that had never been scanned — and a blank page is indistinguishable from an empty library, which sent the fault-finding in the wrong direction entirely. The player now creates it at startup with its own writable handle, and the page reports a failure as a failure rather than rendering nothing.
>
> Without a scan, browsing by **artist** and **track** still works from MusicBrainz alone — 463 artists on this library. Only **album** is empty, and says why.
>
> **One page for every skin**, wearing the chosen skin's stylesheet. Three browse implementations would rot at different rates; this way a new skin gets browsing for free and can still restyle every part of it. MuLibPlay's three separate buttons still work, via `?kind=`.
>
> **Browsing runs off the engine entirely.** The page queries the database directly, so listing ten thousand tracks cannot interfere with playing one. Only the queueing action touches the player, and it inserts **next** rather than last: browsing to something and then waiting five passages for it is indistinguishable from the button not working. It does not interrupt what is playing — that is what Skip is for.

**`[REQ-VIS-195]` Tracks are selected, then acted on together.** A checkbox on each row and **one** set of Now / Next / Last for the list, disabled until something is ticked — a button that does nothing when pressed teaches nothing. Tapping anywhere on a row ticks it, because a 16-pixel checkbox is not a phone target and the whole row is.

Several tracks go in **exactly as one would**, and in the order they appear in the listing — so an album queues in its running order `[REQ-VIS-190]` in a single action.

> **They must arrive together.** Sent as separate requests, three passages inserted one at a time at the same place come out **backwards**; a whole album queued in reverse looks like a UI fault and is not. So the list travels as one request and is inserted by one command, and `Queue::insert_at` is the single place that knows how to keep an order. Both are tested, including insertion past the end.
>
> **A passage that cannot be read is dropped rather than failing the batch:** nineteen tracks queued beats none.
>
> Measured: all seven tracks of *Aja*, queued Next in one action, landed after the current passage as Black Cow → Aja → Deacon Blues → Peg → Home at Last → I Got the News → Josie.

**`[REQ-VIS-190]` An album opens in its own running order.** An album is a sequence, not an index: opened as one, its tracks belong in the order they were put on the record. Alphabetical remains right everywhere else, where a long list has to be findable rather than faithful.

Ordering is by disc, then track number, then title. **Unnumbered tracks sort after the numbered ones**, not ahead of them, which is where a bare `NULL` would put them.

> **The numbers come from the files.** MusicBrainz keeps position on the Release, in `release_recordings.position`, and those tables are empty `[REQ-VIS-170]` — so the file's own `TRACKNUMBER` is the only thing that knows an album's order. It is parsed for the forms tags actually use: `7`, `07`, and the `7/12` that ID3 writes and a naive parse drops, silently sorting a whole album alphabetically instead. Zero means absent, not first.
>
> **The tag index migrates itself.** An index built before track numbers existed has the rows but not the columns; adding a column succeeds exactly once, and on that run the stored tags are dropped so the background scan reads the numbers. Cheaper than a version table for one migration, and it cannot half-apply. Measured: an already-scanned library rebuilt itself in 18.7 s on the next start, with no manual step.
>
> **In album order the number leads the title and the alphabet bar disappears.** Letter headings over a running order would be neither monotonic nor meaningful, and an A–Z index over twelve tracks is furniture.

**`[REQ-VIS-185]` A found passage can be heard three ways, and the queue can be edited.** Wanting to hear something is not the same as wanting to hear it *instead* of what is playing, and one action has to guess which was meant:

| verb | what it does |
|---|---|
| **Now** | to the front of the queue, then skip into it — the only one that interrupts |
| **Next** | position 1 of the queue — the top of "Coming up" |
| **Last** | behind everything already waiting |
| **↑ / ↓** | one place sooner or later, clamped at the ends |
| **×** | out of the queue |

> **The queue holds only what is still to come.** The sounding passage is in `live` and is not in the queue at all, so index 0 *is* the next thing heard. Next shipped inserting at index 1 — one place too late — on the belief that the head of the queue was the playing passage. A test asserted that belief in as many words ("playing passage must stay at the head"), which is how it survived; that test now asserts the opposite and says why.
>
> **Now means the front, not "after the current".** Skip reaches for the front of the queue, so anything less would play whatever was already next instead — the passage the listener did not ask for.
>
> **A press has to arrive, and has to be seen to arrive** *(2026-09-11)*. These controls were intermittent for as long as they existed, and not because of anything on the engine side. The queue list was emptied and rebuilt from every snapshot — twice a second, whether or not it had changed — and a browser dispatches `click` only when mousedown and mouseup both resolve to the same live element, so **any press that spanned a rebuild was never delivered at all**. It registered visually and did nothing. WinAmp made the symptom plainest: its bevel flips while a button is held, which is the browser reporting a finger rather than the player reporting a command, and the two were indistinguishable. The list is now redrawn only when it has actually changed, and the handler lives on the document rather than on buttons that get replaced.
>
> Three smaller things followed from the same complaint. A press is acknowledged the moment the pointer lands, in each skin's own palette, and clears when the snapshot carrying the change arrives — so the flash ends on the engine agreeing rather than on a timer, and a refused request says so instead of fading out like a success. A queue edit no longer waits out the snapshot throttle at either end, which takes press-to-visible from an average of ~250 ms to ~50 ms. And **↑ on the first entry and ↓ on the last come up disabled**: the engine clamps and answers `204` either way, so those two were indistinguishable from controls that had failed — which, at the time, most of them had.

**`[REQ-VIS-186]` A queue entry is not a passage** *(2026-08-17)*. The edit
verbs name the **entry**, never the passage it plays.

A passage may sit in the queue more than once, deliberately, as a repeat. Those
are two entries that happen to name the same audio — and while the queue was
addressed by `passage_id` they were indistinguishable: **removing one removed
both**, and moving one moved whichever came first. Inherited from MuLibPlay,
where it behaved the same way, and reproduced here by carrying the same
identifier across.

Each entry is now stamped with a `qid` on its way into the queue, monotonic and
never reused. Never reused is the load-bearing part: an identifier a browser is
holding can then only be **stale** — naming an entry that has since played or
been removed, which is a quiet no-op — and never **ambiguous**, silently
addressing whatever took its place.

Two identifier spaces meet on `/queue/:ids/:action`, and conflating them was the
bug. `now` / `next` / `last` name **passages** in the library, because they add
something that is not there yet. `remove` / `sooner` / `later` name **entries**,
because they act on something already queued. Selection follows the entry too,
so two copies are separately pickable; the *explanation* is still fetched per
passage, since why a recording was chosen is the same for both copies.
>
> **Shifting clamps rather than wraps.** Nudging the first passage "sooner" does nothing, which is what is expected; wrapping it to last would be a surprise indistinguishable from a bug.
>
> **The three edits touch no database.** A queued passage is already in hand, so rearranging is a message to the engine and nothing more. Only the three library verbs read a passage in.
>
> **The controls sit to the left of the title, in fixed-width columns**, ordered × then ↑ then ↓. A column of identical buttons is one target to learn; buttons that shift with the length of a title are three. The list markers went with them — a number in front of the controls would put two unrelated things in the same column.
>
> **The controls are built in `core.js`, not in each skin.** All three want the same verbs on the same object; three copies would drift. A skin styles them through `.qedit` and decides where they go — it does not decide what they do. This replaces MuLibPlay's checkboxes and "Remove Checked" button, which took three taps to do what one now does.

