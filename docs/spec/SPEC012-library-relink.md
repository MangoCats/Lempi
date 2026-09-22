# SPEC012: Library Relink

**Design Specification — binding a transported library to a target's own paths**

Applies `[SPEC-DF-030]`'s identity keys to deployment. Companion: [SPEC006](SPEC006-data-flow-and-portability.md).

---

## 1. Why this exists

**`[SPEC-RLK-010]` A database arrives knowing what its music *is*, and nothing
about where this machine keeps it.** `[SPEC-DF-030]` is explicit, and the schema
says so on the column itself: `path` is machine scope and **survives nothing —
never transported**. Yet a moved library needs 5,705 correct paths before a note
can be played. They come from the target, by asking each file what it is.

**`[SPEC-RLK-020]` The alternative was tried, and it is not sound.** The
2026-08-17 transfer rewrote every path on the *source* — `C:\Users\…\Music\` to
`/srv/library/audio/` — and shipped the result. It worked, and it worked for
reasons that are properties of that library rather than of the method:

| Assumption | Fails when |
|---|---|
| The target's tree matches the source's, exactly | anything is reorganised, renamed or re-foldered |
| Path case is preserved end to end | Windows (case-insensitive) feeds Linux (case-sensitive) |
| Unicode normalisation matches | macOS writes NFD, Linux stores NFC — **628 of this library's paths carry non-ASCII characters** |
| The same name is the same bytes | **Windows cannot store `:` or `?` in a filename and substitutes a private-use codepoint** — measured 2026-08-20 |
| The target holds the whole library | it holds a subset, and every absent row is indistinguishable from a broken one |

Its pre-flight check made the second row of that table in miniature:
`os.path.exists` on Windows is case-insensitive, so it would have passed paths
that 404 on the appliance. A byte-exact recheck found none — because the library
is well kept, not because the method noticed.

**`[SPEC-RLK-025]` The fourth row is measured, and it is the worst kind.**
Windows forbids `: ? * " < > |` and stores each as `U+F000 + the character`;
Linux stores the real one. **4.8% of this library's paths carry one**, and both
shells render both forms identically, so the difference is invisible exactly
where it would be looked for. Against the appliance, path matching lost one
file in twenty and the hash recovered every one — measured 2026-08-20,
[IMPL003](../IMPL003-vipunen-console-build.md).

---

## 2. The mechanism

**`[SPEC-RLK-030]` Hash what is on the target; match it; write the path.**

    for each file under the audio root
        md5 = MD5(encoded audio stream, container and tags excluded)
        row = SELECT file_id FROM files WHERE audio_md5 = md5
        if row: UPDATE files SET path = <this file> WHERE file_id = row

This is MuLibPlay's `scanFile` `[GDE-BMK-050]`, which hashed a **file**. Lempi
hashes the **encoded stream with tags excluded** — the correction `[SPEC-DF-030]`
names, since `[SPEC-DF-060]` uses embedded tags as a metadata *transport* and a
file-level hash would orphan every row the moment metadata was written back.

**`[SPEC-RLK-040]` Match by the narrowest key that fits `[SPEC-DF-040]`, then
stop.** `audio_md5` is this exact encoding — certain, and the only rung that
justifies a silent update. `recording_mbid` is the same recording re-encoded: a
*candidate*, not a match, because passage `start_ms`/`end_ms` (both `album`- and
`radio`-kind rows), `lead_in_ms`/`lead_out_ms` and replay gain are encoding-scope
and do not transfer to a different rip. It is reported
for a person to decide, never applied. Neither means the file is unknown here —
ingest, not relink `[SPEC-RLK-090]`.

*Designed, not yet built.* `player/core/src/relink.rs` and `player/src/bin/relink.rs`
implement only the `audio_md5` tier above; `Outcome` (`[SPEC-RLK-050]` below)
has no `candidate` variant, and an un-hashed file simply falls through to
`unknown`, full stop. The `recording_mbid` candidate lookup and its reporting
path remain to be built.

---

## 3. What it reports

**`[SPEC-RLK-050]` Five outcomes, all of them stated.** A relink that prints
"done" says nothing about a library that may be half bound.

| Outcome | Meaning | Action |
|---|---|---|
| **matched** | hashed, and the hash agrees with the path already held | none |
| **moved** | hashed, found elsewhere; path updated | none |
| **missing** | a row whose audio is nowhere on this machine | mark absent, keep the row |
| **corrupt** | bytes present where the row expects them, hash disagrees | report loudly; never bind |
| **unknown** | audio here that no row claims | list; ingest is a separate job |

**`[SPEC-RLK-055]` `corrupt` and `unknown` must not be confused.** A truncated
copy of a known file hashes to nothing the database recognises, so the naive
reading is "unknown music" — a failed transfer reported as a library
discovery. They are separable: a row is **corrupt** rather than **missing**
when a file exists where it says and does not hash to it; a file is
**unknown** rather than **corrupt** when nothing claims its location.
`size_bytes` sharpens the same call. Get it wrong and a corrupted library is
reported as an enlarged one.

**`[SPEC-RLK-060]` Missing is a state, not an error.** A Pi holding a subset
of a 44 GB library is a normal deployment. Those passages are excluded from
selection and say why, as an unidentified passage still plays but cannot
contribute to rotation. What must never happen is a row silently keeping a
path that resolves to nothing.

---

## 4. Cost, measured

**`[SPEC-RLK-070]`** `ffmpeg -i F -vn -c:a copy -f md5 -` hashes the encoded
packets as they are read — no decode, so the work is I/O. Measured end to end:
**5,742 files in 423 s**, 74 ms each, against the schema's estimate of ~70.

**`[SPEC-RLK-075]` On the appliance it is 2 h 37 m, not the hour this document
guessed.** *(Measured 2026-08-20, first full run on the Pi Zero 2 W: 5,745 files,
~1.6 s each — **21× the desktop's 74 ms**, and 2.6× the estimate.)* The estimate
was an extrapolation from x86 timings with a hand-wave at "bounded by SD card
read"; the hardware disagreed. It remains a cost not worth optimising — the same
library takes 11 hours to arrive over that Pi's Wi-Fi — but it is long enough to
plan around, and long enough that **the player must be stopped for it** rather
than merely discouraged.

It is also long enough that the all-or-nothing shape matters: hashing completes
before any path is written, so an interruption at 97% loses the run entire.
`[SPEC-RLK-110]`'s "the rows it reached are correct" is about consistency, not
progress — no row is reached until the walk ends.

First end-to-end run, database carrying target paths and audio at its source —
the deployment case exactly:

    5,705 rows, 5,743 audio files under the root
    moved     5,705      every row bound
    missing       0
    corrupt       0
    unknown      28      never indexed, including a scratch directory
    duplicate     9      literal copies, `X.mp3` beside `X_2.mp3`

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

## 5. Boundaries and risks

**`[SPEC-RLK-090]` Relink is not ingest.** It binds rows that exist; it never
creates one. MuLibPlay could relocate a known file and could not induct a new
one, which is why its new-music process was "undocumented, unrepeatable, and
easily forgotten" `[GDE-BMK-050]`. Lempi takes the relocation without the
boundary: `unknown` files are reported so ingest has somewhere to start.

**`[SPEC-RLK-100]` It touches one column.** `path`, and nothing else — not
passages, not flavor, not history. A tool that can only rewrite where a file
lives cannot corrupt what is known about it, which is what makes it safe to
run unattended and repeatedly.

**`[SPEC-RLK-110]` Idempotent, and interruptible.** A second run changes
nothing. Interrupted, the rows it reached are correct and the rest unchanged —
there is no state in which the library disagrees with itself.

**`[SPEC-RLK-120]` The uniqueness constraint is load-bearing.** `audio_md5` is
`NOT NULL UNIQUE`, so two files with one hash are a duplicate on disk rather
than an ambiguity in the match. Relink reports the pair and binds the row to
the first in walk order, which is sorted — reproducible, not whichever the
filesystem offered first.

---

## 6. Settled

**`[SPEC-RLK-130]` The shipped database keeps its paths when it is a plain
copy, and loses them when it is not.** *(Decided 2026-08-17.)*

Blanking paths would make `[SPEC-DF-030]`'s "never transported" enforceable
rather than advisory — the stronger position, but not worth inventing a
transformation step for. Where the database ships as a straight file copy the
stale paths are harmless: relink overwrites every one of them.

Where a database is **already** being customised — pruning caches, subsetting a
library — the paths come out during that pass. The work is being done anyway,
and leaving machine-scope data in a database deliberately shaped for another
machine is an omission rather than an efficiency.

So the source-side path rewrite of 2026-08-17 `[SPEC-RLK-020]` is **not** needed
once relink exists. It was the only reason to touch the database at all.

**`[SPEC-RLK-140]` Relink is also the integrity check.** *(Decided
2026-08-17.)* A hash that matches proves the bytes arrived intact. The walk pays
for that hash already; only the reporting was missing, and a transfer otherwise
has nothing checking it beyond rsync's own framing.

**This forbids the obvious optimisation.** A relink that skipped files whose
path already looked right would be faster and would verify nothing — `matched`
would mean "the path resolves", which is an assumption wearing the costume of a
result. Every file is hashed, every run.

A `--quick` mode skipping bound files may still be wanted for a large library
relinked repeatedly. If it exists it must say so in its own output: that it
verified nothing.

---

## 7. Decided, and deferred

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
