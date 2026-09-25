# SPEC045: The Identity Hash

**Design Specification — what `audio_md5` is, which tool defines it, and the deferred move of that definition into the binary**

Split from [SPEC012](SPEC012-library-relink.md) on 2026-09-24, when that document reached 297 of its 300 lines `[GOV-DOC-010]`. Relink *uses* the hash to bind paths; this document is about the hash itself, which is the subject expected to grow — a transfer hash for payloads, the move to Symphonia, a phone that cannot shell out to ffmpeg `[GDE-HST-080]`. The tags keep their `SPEC-RLK-*` names, so every citation in code, schema and other documents still resolves.

> **Related:** [SPEC012](SPEC012-library-relink.md) (relink, which uses the hash) · [SPEC008](SPEC008-database-schema.md) `[SPEC-SC-038]` (`md5_generator`, the provenance column) · [SPEC014](SPEC014-payload-schema.md) (the payload that carries the hash between installations)

---

## 1. Not a standard, but a demuxer's opinion

**`[SPEC-RLK-080]` Hash with ffmpeg — because ffmpeg wrote the values we
hold, not because it is more correct.** *(Revised 2026-08-17, then corrected
the same day.)*

An earlier draft rejected Symphonia on a 1% disagreement, as though it had
lost on merit. That was wrong, and the correction matters more than the
conclusion.

`audio_md5` is **Essentia's `md5_encoded`**, and Essentia's audio I/O is built
on FFmpeg/libav. The 5,705 stored values are therefore an ffmpeg-family
artefact. Measuring ffmpeg against them — 68 of 68 — is close to measuring a
tool against its own output. **It is evidence of shared lineage, not of
correctness.** Had Symphonia generated the references, Symphonia would score
100% and ffmpeg would fail on the same ~60 files.

**`[SPEC-RLK-085]` The two disagree about where the stream ends.** Measured:
both begin identically, immediately after the ID3v2 tag. The disputed files
end in stray `0xFF` bytes — the start of an MPEG sync word with no frame behind
it. Symphonia stops at the last complete decodable frame; ffmpeg includes the
remainder. ID3v1 handling is not uniform within ffmpeg either: one file's valid
`TAG` trailer was included in the hash, another's was stripped.

An attempt to write the rule down — *everything after ID3v2, to EOF, less an
ID3v1 trailer if present* — reproduced **36%** of the stored hashes. That failure
is the finding: there is no simple specification of what ffmpeg does, only
accumulated heuristics.

Neither reading is more valid; they answer different questions. Symphonia's is
the cleaner **identity** — only decodable audio, so two copies differing solely
in trailing junk are the same recording. ffmpeg's is the stricter **integrity**
check, noticing damage Symphonia ignores by design. `[SPEC-RLK-140]` made
relink an integrity check, so ffmpeg suits — a reason found after the fact.

**`[SPEC-RLK-086]` The identity key is implementation-defined, and that is a
latent risk.** Both hashers are deterministic (three runs each, identical) and
neither involves floats or endianness, so both are stable across x86_64 and
aarch64. Platform is not the hazard.

**Version is.** Neither implements a standard. Ours is ffmpeg 8.0; Essentia's
bundled libav is considerably older. That they agree today is fortunate rather
than guaranteed, and `[SPEC-DF-030]` treats `audio_md5` as a stable identity
key when it is really "whatever the extractor's demuxer did". An ffmpeg upgrade
could in principle orphan rows, and nothing would report it as anything but
missing music.

**`[SPEC-RLK-088]` Tested 2026-08-20, and it did not fire.** ffmpeg 5.1.9 on
aarch64 against values written by 8.0 on x86_64 — three major versions and a
change of architecture — agreed on **238 of 238** sampled files
([IMPL003](../IMPL003-vipunen-console-build.md)). That lowers the risk without
retiring it: the disagreement above lives in a tail a sample this size may not
contain, and the Symphonia spike agreed on six files before disagreeing on
sixty of 5,705. The durable fix is still the one deferred below.

The hasher is therefore **not** a free choice: it must remain the one that
produced the incumbent values. `[SPEC-RLK-150]` takes that up.

---

## 2. Decided, and deferred

**`[SPEC-RLK-150]` At the next re-extraction, Symphonia becomes the hash
authority and the ffmpeg dependency is retired.** *(Decided 2026-08-17. Not to
be done tonight, or on its own.)*

Not on merit: `[SPEC-RLK-080]` and `[SPEC-RLK-085]` establish that the two
readers merely disagree about trailing bytes no listener will ever hear. The
reason is **ownership**. Today the meaning of an identity key other tables are
keyed on is defined by an external package a routine `apt upgrade` can change
underneath the appliance `[SPEC-RLK-086]`. Symphonia is compiled in: the
definition would ship with the binary and could not drift without a deliberate
build. That is the difference between a key the project *has* and one it
*borrows*. It also removes the appliance's only use for a media framework.

**Why it waits.** `audio_md5` keys four tables — `files`, `lowlevel_cache`,
`identification_cache`, `ingest_decisions`, ~45,000 rows — which must be
rewritten together, keyed by the value that is changing; a half-applied
migration orphans every cache while looking merely cold. A re-extraction
regenerates them anyway, so the cost collapses to zero at that moment and no
other.

**Three preconditions, none optional.**

1. **Close or accept Symphonia's coverage gap** — 1 file of 5,743 that ffmpeg
   reads and it cannot, a `.mp3` it probes as an unsupported wave format.
   0.017%, but that file currently plays and would have no identity at all.
2. **One implementation, not two `[GDE-FBD-040]`.** Essentia emits
   `md5_encoded` free at extraction `[SPEC-SA-035]` and ingest is Python;
   moving the authority to a Rust crate means deciding where ingest gets its
   hash, not leaving both to compute an identity key their own way.
3. **Record the generator and its version alongside the values**, so a future
   disagreement is diagnosable rather than discovered as missing music.
   **Done 2026-09-22** — `files.md5_generator` `[SPEC-SC-038]`, `name@version`,
   written by both ingest tools, the MuLibPlay migration and the bundle
   importer. It is the one precondition that pays off whether or not the other
   two are ever met: it costs nothing now and is the only way a version-driven
   orphaning could be recognised for what it is rather than read as missing
   music. **Existing rows are `NULL` and stay that way** — back-annotating the
   incumbent 5,705 would be recording an inference in a provenance column, and
   `[SPEC-RLK-080]` already says what produced them.

   It also sharpens precondition 1 rather than closing it: once some rows say
   `ffmpeg@…` and later ones say `symphonia@…`, the coverage gap stops being a
   count someone remembers and becomes a query.
