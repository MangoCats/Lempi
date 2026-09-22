# SPEC023: Domain Vocabulary

**Design Specification — Tier 2**

File, passage, recording, release, album and track name overlapping, easily-conflated things. This is the one place that says what each word means and what it does not — every other document should link here rather than re-explain, per `[GOV-DOC-020]`.

> **Related:** [SPEC008 §2–3b](SPEC008-database-schema.md) for the tables these terms name · [SPEC010 §3](SPEC010-identification-review.md#3-searching-musicbrainz-directly) for release identification in practice · inherited [MCR-REQ002](../inherited/mcrhythm/MCR-REQ002-entity_definitions.md) for the McRhythm-era entity tags this extends, not replaces

---

## Why this exists

Two collisions kept recurring across the docs before this was written: **"album"** was used for a passage's playback style, for a MusicBrainz Release, and for "whatever collection of passages a listener would call an album" — three unrelated things, none implying the other two. **"Track"** was used, inconsistently, both for its actual MusicBrainz sense (a recording's position on a release) and as a loose stand-in for "passage" or "recording" in normative spec prose describing what state is actually read, written or scored. Neither collision was ever wrong in any single sentence — each was locally clear — but nothing stated the boundaries, so they drifted.

`[SPEC-VOC-010]` **The rule going forward:** normative prose — anything describing what the schema stores, the engine plays, or the Director scores — uses exactly one of the terms below, in the sense defined here. "Track" and "album" in their loose, everyday senses stay legitimate in UI copy and informal narration ("browse by album", the seek bar's "click to move within the track"), never as a substitute for "passage" or "recording" in text that describes actual state — the `[REQ-VIS-100]` panel itself was renamed from "Why this track?" to "Why this passage?" for exactly this reason once its `track_restraint`/`track_ramp` fields turned out to mean *recording* restraint and ramp specifically.

---

## The terms

| term | is | is not |
| :--- | :--- | :--- |
| **File** | one `files` row: one exact audio encoding, `audio_md5`-keyed | one album, one artist, or one passage — a file may hold many passages (a DAO capture) |
| **Passage** | a span of one file with its own playback metadata; `kind` selects `radio` or `album` treatment of the same span | a file, a recording, or a claim about release/artist identity |
| **Recording** | a MusicBrainz Recording, or a synthetic `local:audio:` id before identification | a release, or a position within one |
| **Release** | a MusicBrainz Release: one published edition | the only, or a uniquely identifying, name for "the album" |
| **Album** *(informal)* | situational — see below, always say which sense | a table, a single identity, or a synonym for `kind='album'` |
| **Artist** | a MusicBrainz Artist, credited to a recording by a weighted link | a single value — a recording's credits are a weighted set |
| **Track** | a recording's position in a release's running order | a stand-in for "passage" or "recording" in normative prose |

### File

`[ENT-FILE-010]` One `files` row — one exact audio encoding (`[SPEC-SC-030]`). May hold one passage (the common case) or many: a DAO/live capture is one file with a passage per song. **A file's relationship to one album or one artist is never guaranteed by anything structural** — folder placement is a ripping/tagging convention Vipunen may use as a hint (browse fallback, relink candidates), never as identity, for the same reason path is never a key (`[SPEC-SC-035]`, `[SPEC-RLK-025]`).

### Passage

`[ENT-PASSAGE-010]` A span of one file with its own playback metadata — start/end, lead-in/out, fade-in/out, gain. Full definition, including what `kind='radio'` vs `kind='album'` actually change about playback, lives in [SPEC008 §3](SPEC008-database-schema.md#3-passages--the-albumradio-duality) (`[SPEC-SC-040]`, `[SPEC-SC-047]`) — this entry exists only to place Passage against its neighbours: it is **not** a file (a file may hold several), and it is **not** an identity claim about a release or artist. A `radio`-kind passage can belong to a well-catalogued release exactly as often as an `album`-kind one; the kind describes a playback treatment, nothing else.

### Recording

`[ENT-RECORDING-010]` A MusicBrainz Recording — one particular piece of recorded audio — identified by `recordings.mbid`, or a synthetic `local:audio:<md5>` id before or absent identification (`[SPEC-SC-030]`). A passage names a weighted set of recordings (`passage_recordings`, `[SPEC-SC-050]`) — a medley, or none at all if unidentified; an unidentified passage is still legal and playable (`[ENT-MP-035]`).

### Release

`[ENT-RELEASE-010]` A MusicBrainz Release — a specific published edition ("this pressing, this cover, this catalogue number"). Its title is ordinarily what "the album name" is (`[REQ-VIS-170]`). Schema: [SPEC008 §3b](SPEC008-database-schema.md#3b-artists-releases-and-credits).

**Not guaranteed, and not unique.** Most albums correspond to a release MBID once identified, but not all do — MusicBrainz simply has no entry for some — and an album with none falls back to naming from the file's own tag, permanently, not as an interim state (`[REQ-VIS-170]`). Conversely, one physical or logical album routinely has **several** release MBIDs — a US pressing, a European one, a digital reissue, a remaster — that are functionally indistinguishable for every purpose Lempi has: same tracklist, same title, nothing about playback, naming or selection differs between them. **Differing release MBIDs therefore do not, by themselves, mean two distinguishable albums.** Choosing one (`release_recordings.chosen`, [SPEC010 §3](SPEC010-identification-review.md#3-searching-musicbrainz-directly)) selects a catalogue entry to link and name from — it is not a claim that the underlying album differs from its siblings, and a person choosing between two such releases is picking a citation, not resolving a real ambiguity.

### Album *(informal — not a table, not one identity)*

`[ENT-ALBUM-010]` Used in at least three genuinely different senses. State which one is meant, or link here instead of using the bare word:

1. **`passages.kind = 'album'`** — a playback style (see Passage). Unrelated to senses 2 and 3: a `radio`-kind passage can come from a well-catalogued release, and an `album`-kind passage can come from a file with no release at all.
2. **A release's identity** (see Release) — the everyday sense, backed by a chosen release MBID where one exists, the file's own tag otherwise.
3. **A collection of passages a listener would call "an album."** Usually, never guaranteed, one DAO-capture file — a capture is equally often an unrelated compilation or mixtape, and nothing enforces "one file, one album." Usually, not 1:1, one folder of files — folder layout is a convention Vipunen may use as a hint, never as identity (see File). Usually, not always, one primary artist's work — a various-artists record is exactly the case where that breaks (see Artist).

None of the three implies either of the others.

### Artist

`[ENT-ARTIST-010]` A MusicBrainz Artist (`artists.mbid`), credited to a recording through a weighted `recording_artists` link (`[SPEC-SC-050]`-adjacent, [SPEC008 §3b](SPEC008-database-schema.md#3b-artists-releases-and-credits)) — a collaboration or featured credit is a genuine multi-row weighted set, not a single "the" artist forced into one field. A folder or file is commonly, not 1:1, organized by what a listener would call the primary artist; that grouping is a convention, never an identity, for the same reason Album sense 3 is not either.

### Track

`[ENT-TRACK-010]` Not a Lempi table. Lempi keeps McRhythm's own MusicBrainz-harmonized definition (`[ENT-MB-010]`): **a recording's appearance at a specific position on a specific release.** Realized in Lempi's schema as one `release_recordings` row (`recording × release`, with `position`/`disc`, [SPEC008 §3b](SPEC008-database-schema.md#3b-artists-releases-and-credits)). Before or absent identification, the same idea exists without a release MBID behind it yet, as the file's own `track_no`/`disc_no` tag.

**Not interchangeable with passage or recording in normative prose.** A passage is a playback span of a file; a recording is a piece of recorded audio; a track is a recording's *position in a release's running order* — a fact about sequence, not about audio or playback. Text describing what the engine plays, the Director scores, or the schema stores must say "passage" or "recording." "Track" belongs in tracklist/browse context (`track_no`, running order, "browse by album") and in informal narration or UI copy, e.g. the seek bar's "click to move within the track."

**The `[REQ-VIS-100]` panel was corrected, not carved out.** It was "Why this track?" until this review found `track_restraint`/`track_ramp` were computed per *recording* (rotation state keyed by `mbid`, shared by every passage of the same recording) while the panel's own anchor (`passage_id`) is per *passage* — two different scopes wearing one word. Fixed by splitting them: the panel is "Why this passage?" (it explains one passage's selection), and the two rotation terms are "Recording restraint"/"Recording recovery" (they are recording-scoped, and two passages of the same recording show identical values for both). The rename touches the `/why/:id` JSON shape (`Explanation` in `player/core/src/director/library.rs`) and `skin.js`'s `TERMS` array together; historical `selection_decisions.detail` rows written before it keep the old field names, since that column is freeform archived JSON, not a versioned schema.

---

**Traceability:** `[SPEC-VOC-010]` · `[ENT-FILE-010]`, `[ENT-PASSAGE-010]`, `[ENT-RECORDING-010]`, `[ENT-RELEASE-010]`, `[ENT-ALBUM-010]`, `[ENT-ARTIST-010]`, `[ENT-TRACK-010]` · extends inherited `[ENT-MB-010..040]`, `[ENT-MP-010..035]`
