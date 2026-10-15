# SPEC026: CD Ripping — Passage Representation for Hidden Audio and Multi-Disc Sets

**Design Specification — Tier 2 · built for image rips, but for §1's folded-in passage**

Two cases where a rip is not simply one passage per TOC track: real audio
hidden in a pregap or at `INDEX 00`, and a box set ripped disc-by-disc.
Both build directly on [SPEC025](SPEC025-cd-ripping.md)'s TOC read (§3)
and [SPEC028](SPEC028-cd-ripping-identification.md)'s Disc ID lookup —
split into its own document (alongside SPEC028) once together they pushed
SPEC025 past `[GOV-DOC-010]`'s 300-line hard limit, not because the
subject is unrelated. Read SPEC025 first, then SPEC028; §1 and most of §2
below cover what happens to the audio once those have already run for a
given disc — §2 also covers the multi-disc session itself, since that is
squarely part of what a box set is, not a separate topic.

> **Status, 2026-10-14.** Built in `tools/ingest_cd.py` and the import page.
> A set ripped a disc at a time is one release, each disc known by its own
> Disc ID (`[SPEC-RIP-094]`, `[SPEC-RIP-096]`, `[SPEC-RIP-106]`, by way of the
> importer's `[SPEC-CDI-099]`), filed in one folder under names that keep the
> discs apart, previewed as *disc N of M*, and a set short of a disc said
> loudly, before the add and after (`[SPEC-RIP-108]`, `[SPEC-RIP-110]`, and
> the banner of `[SPEC-RIP-112]`). A disc that is one piece is one passage pair, on a
> person's tick (`[SPEC-RIP-114]`). Hidden audio is found and made its own
> passages (`[SPEC-RIP-090]`, as `[SPEC-RIP-116]` measures it), for an image
> rip. Not built: the folded-in passage of `[SPEC-RIP-092]`; hidden audio in
> a tracks-mode rip, where a gap stays inside a track's own file and is
> played with it, not lost.

> **Related:** [SPEC025](SPEC025-cd-ripping.md) for the ripping mechanics
> this extends · [SPEC028](SPEC028-cd-ripping-identification.md) for the
> Disc ID lookup this depends on · [SPEC023](SPEC023-domain-vocabulary.md)
> for what a passage is and is not ·
> [SPEC008 §3](SPEC008-database-schema.md#3-passages--the-albumradio-duality)
> for the passages schema, §3b for `release_recordings.disc`

---

## 1. Hidden and pregap-only audio — its own passage, optionally folded in too

**`[SPEC-RIP-090]` Real audio inside a `PREGAP` or at `INDEX 00` gets its
own passage, never a silent drop.** `[ENT-PASSAGE-010]` already defines a
passage as a span of one file with its own playback metadata, and
`[ENT-FILE-010]` already allows a file to hold many passages — a DAO
capture is the case the vocabulary was written for. A naive split at
`INDEX 01` boundaries alone would discard hidden audio as if it were the
silence a pregap ordinarily is; instead, when SPEC025 §3's read finds real
audio in that span (not silence), it becomes its own passage with
`boundary_src` set the same as its neighbours (`[SPEC-RIP-070]`),
addressable, playable, and reviewable exactly like any other.

**`[SPEC-RIP-092]` A second, folded-in passage may additionally span the
hidden audio together with the adjacent track, when that pairing is what
a listener would actually want played.** Nothing in the schema forces one
passage per span — two passages already may cover overlapping or adjacent
ranges of the same file with no conflict, so "the hidden intro alone" and
"the hidden intro plus the track it leads into, as one continuous
listen" coexist as two separate rows rather than competing
representations. The first is what makes the hidden audio findable and
playable on its own; the second is what makes it play the way the disc
evidently intended when skipping straight to the visible track would clip
a deliberate segue. Both, either, or neither may exist for a given hidden
span — nothing requires the second passage to be created.

**`[SPEC-RIP-093]` Creating the folded-in passage is the user's decision,
never an automatic heuristic.** No threshold reliably tells a short
hidden intro that leads into the next track apart from a long,
self-contained hidden bonus track — rather than guess, Vipunen detects the
hidden span (`[SPEC-RIP-090]`) and surfaces it as a one-action offer at
review time ("also create a passage combining this with the next track?"),
the same discover-then-let-a-person-confirm shape `[SPEC-SUI-215]`'s
release-suggestion flow already takes: the tool finds the candidate and
writes nothing until the operator picks it. The console makes the choice
easy; it does not make the choice.

---

## 2. Multi-disc sets — one file per disc, one Release, passage-per-track by default

**`[SPEC-RIP-094]` A box set ripped disc-by-disc is one file per disc, not
one file per track and not one concatenated file for the whole set.** Each
disc rips through SPEC025's own pipeline (§§2-4) independently; a
five-disc box is five `files` rows, each a DAO capture in exactly the
sense `[ENT-FILE-010]` already describes.

**`[SPEC-RIP-096]` All of a set's discs resolve to one MusicBrainz
Release — a multi-medium release, not N unrelated ones.** Each disc's own
Disc ID lookup ([SPEC028 §1](SPEC028-cd-ripping-identification.md#1-disc-id-resolves-the-exact-pressing))
is still performed per disc, but where it
resolves, every disc lands on the same `releases` row;
`release_recordings.disc` `[SPEC-SC-048]` — already present in the schema
for exactly this — carries which medium each track belongs to. No new
column or table is needed: a box set is the ordinary multi-disc shape
`release_recordings` already supports, not a special case.

**`[SPEC-RIP-098]` Default segmentation is unchanged: one passage per
track, per disc.** Each disc-file is split by its own TOC exactly as a
single-disc rip is (SPEC025 §3) — a box set changes how many files a rip
produces and how they resolve on MusicBrainz, not how a single disc's own
tracks become passages. Additional passages spanning a whole disc, or a
conventional "side" grouping some box sets still carry (a vinyl-style A/B
convention on an otherwise plain CD), may also be created — the same
wider-span-alongside-the-per-track-ones pattern §1 already established for
hidden audio folded into a neighbour, applied at a larger granularity
rather than a new mechanism.

**`[SPEC-RIP-100]` Which wider passage to create, if any, is the user's
call, made easy rather than made automatically.** Whole-disc and per-side
groupings are offered as one-action choices once a disc's tracks are
segmented (§2) — not inferred from disc length, track count, or any other
heuristic, for the same reason `[SPEC-RIP-093]` keeps the folded-in-passage
decision with the person ripping rather than a threshold: nothing about
the audio itself says whether a listener wants a whole disc played as one
continuous passage. Zero, one, or several wider passages may exist per
disc; declining every offer leaves the ordinary per-track passages
exactly as segmented.

**`[SPEC-RIP-104]` A multi-disc rip session prompts to continue with the
next disc after each one finishes, but never blocks on absence or
sequence.** The prompt offers "insert disc N+1" — an invitation, not a
gate: the operator may swap in a disc out of the set's expected order,
skip one that isn't at hand, or end the session early at any point.
Whatever was actually ripped is what gets recorded (`[SPEC-RIP-096]`), not
what a session plan assumed would be — a set missing disc 3, or ripped
3-1-2, is exactly as valid a library entry as one ripped straight
through.

**`[SPEC-RIP-106]` Which disc a physical disc actually is comes from its
own Disc ID lookup ([SPEC028 §1](SPEC028-cd-ripping-identification.md#1-disc-id-resolves-the-exact-pressing)),
not from session sequence.** Every disc's
TOC is independently resolved against the shared Release's per-medium
track list regardless of when in the session it was ripped, so an
operator who inserts the wrong disc — a duplicate of one already ripped,
or one belonging to an unrelated release entirely — is told so directly
rather than having the session assume it must be whichever disc came
next. Identification, not sequencing, is what a disc's medium number and
set membership rest on; this is the same per-disc Disc ID lookup §2
already performs, not a second mechanism built for the session case.

**`[SPEC-RIP-108]` A set's discs share one album folder, and every file a
disc brings is named with its disc.** Once the edition is settled — one
release, its disc known — a track file becomes `2-01. Title`, anything else
`… (Disc 2)`, in place of whatever disc the ripper wrote; a cover keeps its
name unless the folder already has one by it, which is never replaced. The
ripper's names cannot be trusted for this: CUERipper writes `(Disc 1)` on a
single disc, and *An American Treasure*'s second disc arrived with no disc
in its names at all, so its first would have collided name for name. The
move still refuses to write over any file `[SPEC-NAM-050]`; this is what
keeps the refusal for a real clash.

**`[SPEC-RIP-110]` The import page says which disc of how many a rip is,
and what of the rest of the set the library holds** — *disc 1 is not in the
library yet* — before anything is added. A set added a disc at a time is
otherwise one album that never says it is short a disc, and a wrong disc of
the set is caught as the page opens rather than after the add `[SPEC-RIP-106]`.

---

**`[SPEC-RIP-112]` A set short of a disc is said loudly, and what is here is
added anyway.** Asked for 2026-10-14. Before the add, the chosen edition's
banner names each of the set's discs the library does not hold -- *disc 2 of 3:
disc 1 and disc 3 are not in the library* -- in red, larger than the page
around it; after it, the add's result says the same from what the library
then holds, or that the set is complete. Nothing waits on the missing disc:
it is added whenever it turns up, into the same folder under its own names
`[SPEC-RIP-108]`, and the album is whole once the last is in. This is
`[SPEC-RIP-104]`'s prompt, in the form the import page can give it: the page
cannot know a disc is in the drive, so it says what is missing instead.

**`[SPEC-RIP-114]` A disc that is one piece is one radio and one album passage
for the whole disc, in place of a pair per track.** Asked for 2026-10-14, for
a disc like *Thick as a Brick*. A tick on the import page, *One piece*,
offered and never inferred `[SPEC-RIP-100]`. The radio passage runs from
track 1's start to the end, the album passage over the whole file. Every
track is still identified and linked to its release, so the album is named
and its tracks counted as for any disc; the two passages carry every track's
recording, weighted by its share of the disc. This narrows
`[SPEC-RIP-098]`, which had wider passages *alongside* the per-track ones:
for a disc that is one work, the maintainer wants the one passage, not
both. An image rip only -- a passage is a span of one file, and a tracks-mode
rip is a file a track -- and refused before anything is touched otherwise.

**`[SPEC-RIP-116]` Hidden audio is measured, never guessed.** For an image
rip, three places no track holds are measured on the WAV with ffmpeg: before
track 1's `INDEX 01` (a pregap, where MusicBrainz may name it as the medium's
track 0), each gap between one track's `INDEX 00` and its `INDEX 01`, and
after a silence of at least 8 s below -50 dB inside the last track, past its
first half minute (each stretch of music after it, a piece). A span is hidden
audio when it is at least 4 s long and averages above -45 dB: a pressed gap is
digital silence, about -91 dB, and a hidden track is music. Each becomes a
radio and an album passage of its own, named by MusicBrainz's pregap track,
else AcoustID's answer, else by where it lies -- *Hidden track, after track
12 ('...')* -- and the last track ends where its silence begins. The preview
lists them; with *One piece* ticked nothing is hidden. Calibrated 2026-10-14
on the 45 image rips then filed: none found, *50 First Dates*' 18.8 s pregap
measured -91.0 dB and rightly left out. None of those discs has a known
hidden track, so the finding side is proven on a made disc
(`tools/test_ingest_cd.py`), not yet on a real one.

---

## 3. Open

**`[SPEC-RIP-102]`** Both wider-passage triggers (§1, §2) are now decided
— user's choice, offered rather than inferred (`[SPEC-RIP-093]`,
`[SPEC-RIP-100]`) — so nothing about *when* a wider passage gets created
remains open here. What the offer's own interaction looks like (a
checkbox at review time, a prompt during the rip itself, something else)
is first-build UI detail, not a design question this document exists to
settle.

[SPEC025 §8](SPEC025-cd-ripping.md#8-open) — updated 2026-09-03 after
real-hardware testing found one genuine open item of its own, how Vipunen
drives a GUI-only ripping tool — confirms nothing *else* remains open in
the ripping mechanics this document does not touch either —
drive/hardware failure modes, the last item there, is now decided too.

---

**Traceability:** `[SPEC-RIP-090..116]` · derives `[REQ-LIB-255..275]` ·
extends [SPEC025](SPEC025-cd-ripping.md), `[SPEC008]` §3's
`release_recordings.disc` and `passages` schema, and
[SPEC023](SPEC023-domain-vocabulary.md)'s Passage/File definitions.
