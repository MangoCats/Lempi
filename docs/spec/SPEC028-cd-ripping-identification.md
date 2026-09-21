# SPEC028: CD Ripping — Disc ID, CD-TEXT, and MusicBrainz

**Design Specification — Tier 2 · Built**

How a ripped disc gets identified and named, once [SPEC025](SPEC025-cd-ripping.md)'s
TOC read (§3) has already run. Split into its own document once it and
[SPEC026](SPEC026-cd-ripping-passages.md) together pushed SPEC025 past
`[GOV-DOC-010]`'s 300-line hard limit — the identification question is
naturally separable from the rest of SPEC025's own topic, ripping
mechanics, not because it is unrelated. Read
[SPEC025 §1](SPEC025-cd-ripping.md#1-why-a-discs-own-toc-beats-inference)
and §3 first for the TOC read this depends on entirely.

> **Status.** Built 2026-09-04, per `[REQ-LIB-235]`, `[REQ-LIB-270]`,
> `[REQ-LIB-295]`, `[REQ-LIB-300]`. §§1-2's cascade is `tools/ingest_cd.py`
> (Disc ID exact/fuzzy lookup, CD-TEXT-as-default, AcoustID fallback). §3's
> down-select and freeform entry needed **no new page**: an ambiguous or
> unresolved track is written with the ordinary `local:audio:...`
> placeholder and an `id_checks` row carrying the real candidates, which
> the *existing* `/review` queue already renders as a down-select for any
> grade — see `tools/ingest_cd.py`'s own module docstring and this
> project's build-out plan for why. The one genuinely new surface is
> `GET /review/release-tracks/:mbid` (`player/src/web/musicbrainz.rs`) plus
> a Song/Album search selector in `review.js`, closing the release/track
> search gap `[SPEC-RIP-074]` names. Exercised end-to-end 2026-09-04
> against this session's own real EAC rip: Disc ID resolved it exactly to
> "The Essential Cyndi Lauper," all 14 tracks identified with real artist
> credits — not a synthetic fixture, an actual disc.

> **Related:** [SPEC025](SPEC025-cd-ripping.md) for the TOC read and
> ripping mechanics this depends on · [SPEC026 §2](SPEC026-cd-ripping-passages.md#2-multi-disc-sets--one-file-per-disc-one-release-passage-per-track-by-default)
> for how a multi-disc set's several Disc ID lookups combine into one
> library entry

---

## 1. Disc ID resolves the exact pressing

**`[SPEC-RIP-060]`** The TOC's track count and sector offsets, sent to
MusicBrainz's own Disc ID lookup (`GET /ws/2/discid/<disc-id>?toc=...`),
resolve the exact release when the disc is already catalogued, or a fuzzy
TOC-based match otherwise. When it resolves, that release's own track
metadata — titles, artists, positions — is used directly `[REQ-LIB-235]`,
in place of `[SPEC-SA-070]`'s ordinary per-track AcoustID fingerprint
lookup: a Disc ID match identifies the *pressing itself* from disc
geometry, a stronger answer than a fingerprint's per-track guess.

**`[SPEC-RIP-065]`** When Disc ID resolves nothing — a self-burned
compilation, an unreleased recording, a disc MusicBrainz has never seen and
whose fuzzy match also comes back empty — the ordinary per-track AcoustID
path (`identify_recording()` in `tools/segment_dao.py`) is the fallback,
unchanged. Ripping never blocks on an unresolved disc; it degrades to
exactly the path a file with no TOC already takes. How a multi-disc set's
own several TOCs and Disc ID lookups combine into one library entry is
designed in [SPEC026 §2](SPEC026-cd-ripping-passages.md#2-multi-disc-sets--one-file-per-disc-one-release-passage-per-track-by-default).

---

## 2. CD-TEXT versus MusicBrainz

**`[SPEC-RIP-066]` CD-TEXT, when the disc carries it, is the default
source for title/artist/track metadata; a resolved MusicBrainz match is
offered as an alternative the user may accept instead.** Both are read
where present — CD-TEXT from the TOC read itself
([SPEC025 §3](SPEC025-cd-ripping.md#3-reading-the-toc)), MusicBrainz from
Disc ID (`[SPEC-RIP-060]`) — and the two are not measured against each
other under `[GOV-SRC-020]` before this default is set: unlike
[SPEC025 §2](SPEC025-cd-ripping.md#2-the-ripping-tool--decided-per-platform)'s
platform check, no corpus of discs carrying both exists yet to measure
disagreement rate against. The default instead follows
[SPEC025 §1](SPEC025-cd-ripping.md#1-why-a-discs-own-toc-beats-inference)'s
own standing reason to prefer a disc's own data — CD-TEXT is burned onto
*this* pressing, where a Disc ID match, exact or fuzzy, still identifies a
*release* that may be a different edition, remaster, or regional pressing
of the same recordings. That reasoning does not make CD-TEXT more
*accurate* — a disc can carry misspelled or abbreviated CD-TEXT the same
way a file can carry a bad tag — only more *authoritative about this
specific object*, which is the same distinction `[SPEC-RIP-010]` already
draws for boundaries.

**`[SPEC-RIP-068]` Accepting the MusicBrainz alternative is a one-action
choice at review time, never automatic.** The same discover-then-confirm
shape `[SPEC-RIP-093]` and `[SPEC-RIP-100]` establish for a wider
passage: both readings are shown side by side when they disagree, and
picking either is a single click, recorded the same way a release match
already is (`[SPEC-RIP-075]`, in [SPEC025 §7](SPEC025-cd-ripping.md#7-provenance)).
A disc with no CD-TEXT at all simply shows the MusicBrainz result alone,
unchanged from today.

---

## 3. When automation isn't enough — the person with the disc decides

**`[SPEC-RIP-069]` A fuzzy Disc ID match returning more than one plausible
release is presented as a down-select, not resolved by silently picking
the top-ranked candidate.** `[REQ-LIB-295]`. The person ripping has the
disc itself in hand — the track listing on the sleeve, the pressing
information printed on the disc, sometimes a catalogue number — a
tiebreaker no ranking heuristic has access to. The same
discover-then-confirm shape `[SPEC-RIP-068]` already establishes for
CD-TEXT vs. MusicBrainz, extended from two readings to N candidates:
every plausible match is shown, picking one is a single click, and
nothing is written until they do.

**`[SPEC-RIP-072]` When nothing resolves at all — neither Disc ID, exact
or fuzzy, nor `[SPEC-RIP-065]`'s per-track AcoustID fallback — the person
ripping may search MusicBrainz directly or enter track/artist/album
metadata by hand, rather than accept the anonymous
`local:audio:<md5>:<start_ms>` placeholder `commit_segments()` already
falls back to for an unidentified span.** `[REQ-LIB-300]`. This is the
case `[SPEC-RIP-065]` names but does not fully answer — a self-burned
disc, an unreleased recording, or one MusicBrainz genuinely does not
carry — and it is exactly the moment a placeholder is the wrong default:
the disc is right there, and a person looking at it knows what it is even
when no database does.

**`[SPEC-RIP-074]` Reuses the recording/artist search Vipunen already
built** (`GET /api/musicbrainz/search`, `[SPEC-SUI-196]`, [SPEC010 §3](SPEC010-identification-review.md#3-searching-musicbrainz-directly))
**rather than a second search box, and is a second, independent reason to
finish the "release and track search" half of it that `[SPEC-SUI-197]`
already named as designed but not built.** Searching by *release* — an
album title, which is what is printed on a disc — is the natural way to
look something up here, more than searching by one track's recording
title alone; this document does not duplicate that gap, it adds a second
caller who needs it closed. Where a MusicBrainz search still finds
nothing, freeform entry — title, artist, album, per track — is the last
resort: written to the same `local:audio:<md5>:<start_ms>`-keyed
recording `commit_segments()` already creates for an unidentified span,
but with the person's own typed title/artist and `source='manual'` in
place of `'segment:unidentified'` — a real, non-nullable provenance value
`[SPEC-SC-025]` already requires of every write, not an unlabelled guess
wearing a placeholder's shape.

---

**Traceability:** `[SPEC-RIP-060..074]` · derives `[REQ-LIB-235]`,
`[REQ-LIB-270]`, `[REQ-LIB-295]`, `[REQ-LIB-300]` · depends entirely on
[SPEC025](SPEC025-cd-ripping.md) §§1, 3, 7 · read together with
[SPEC026 §2](SPEC026-cd-ripping-passages.md#2-multi-disc-sets--one-file-per-disc-one-release-passage-per-track-by-default)
for the multi-disc case, and [SPEC010 §3](SPEC010-identification-review.md#3-searching-musicbrainz-directly)
for the search endpoint this reuses
