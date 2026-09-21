# SPEC025: CD Ripping via a Disc's Own Table of Contents

**Design Specification — Tier 2 · Built, single-disc case, person-assisted**

A new ingest entry point for Vipunen: rip a physical CD directly, rather than
starting from files already on disk. Where `[SPEC024](SPEC024-dao-segmentation-cascade.md)`
infers track boundaries from audio content because the original disc's own
data is long gone, this document is for the case where it isn't gone yet —
the disc is in the drive, and its own table of contents (TOC) states
boundaries to the sector rather than asking anything to be inferred.

> **Status.** Built 2026-09-04, per `[REQ-LIB-220..300]`: `tools/cd_toc.py`
> (TOC/cue/log parsing, both disc-id algorithms) and `tools/ingest_cd.py`
> (the orchestrator), wired into `jobs.py` as `cd-rip`. The ripping-tool
> choice (§2) is decided **per platform** — EAC on Windows, `cdrdao` on
> Linux — and **confirmed on real hardware, 2026-09-03**
> ([LOG005](../LOG005-cd-ripping-hardware-findings.md)), then **exercised
> end-to-end against that same real rip, 2026-09-04**: Disc ID resolved it
> exactly (MusicBrainz's own `offsets` matched what `cd_toc.py` computed,
> byte for byte) to "The Essential Cyndi Lauper," all 14 tracks correctly
> identified with real artist credits. `[SPEC-RIP-082]`'s automation
> question is resolved **person-assisted** (§2, [SPEC027](SPEC027-cd-ripping-windows-automation.md)) —
> `ingest_cd.py` reads a rip a person already ran to completion, never
> drives a drive itself. **Still not built:** Vipunen spawning `cdrdao`
> itself on Linux — per `[LOG-RIP-030]`, the one drive this session tested
> returns noise, not audio, over `cdrdao`'s Linux DAE path, so a *working*
> rip needs independent verification on some drive before automating it is
> worth doing, not merely more test hardware. [SPEC026](SPEC026-cd-ripping-passages.md)'s
> hidden-audio/multi-disc passages are the other deferred piece — both
> named explicitly in §8, not silently absent.

> **Related:** [SPEC024](SPEC024-dao-segmentation-cascade.md) for the
> audio-content cascade this supersedes for a fresh rip, and remains the
> fallback for everything else · [SPEC007 §2](SPEC007-vipunen-architecture.md#2-pipeline)
> for where this joins Vipunen's pipeline · [SPEC008](SPEC008-database-schema.md)
> §3 for `boundary_src`'s existing `imported:` convention this reuses ·
> [SPEC026](SPEC026-cd-ripping-passages.md) for hidden-audio and
> multi-disc passage representation, [SPEC027](SPEC027-cd-ripping-windows-automation.md)
> for how Vipunen drives a GUI-only Windows tool, and
> [SPEC028](SPEC028-cd-ripping-identification.md) for Disc ID/CD-TEXT/
> MusicBrainz — all three split out once they pushed this document past
> `[GOV-DOC-010]`'s line limit ·
> [ROADMAP §3](../ROADMAP.md#3-rearchitecture--whats-still-ahead)

---

## 1. Why a disc's own TOC beats inference

**`[SPEC-RIP-010]`** A disc-at-once rip's own TOC — read from the disc's
Q-subchannel, not guessed from the encoded audio afterward — states every
track's start, length, pregap and index points to the sector (1/75 s). That
is ground truth, not the best available estimate `[REQ-LIB-220]`: nothing
in `[SPEC024](SPEC024-dao-segmentation-cascade.md)`'s cascade — grid search,
DP assembly, the RMS fallback, extra-track merging — is needed when the
boundaries are already known rather than inferred. The cascade remains
exactly as necessary as before for everything a TOC cannot help with: vinyl
transfers, cassette transfers, live recordings, or audio that reached this
library already ripped or downloaded with no TOC of its own.

A second, larger win rides along with the first: the TOC's own sector
offsets are also a **MusicBrainz Disc ID** — a lookup that identifies the
exact pressing from disc geometry
([SPEC028](SPEC028-cd-ripping-identification.md)), a stronger answer than
any text or audio-based match and one that arrives for free once a TOC
exists at all.

---

## 2. The ripping tool — decided per platform

**`[SPEC-RIP-020]` EAC on Windows, `cdrdao` on Linux — checked, not
assumed.** cdrdao's own Win32 build
([`README.Win32`](https://github.com/Distrotech/cdrdao/blob/master/README.Win32))
states plainly that it requires a working **ASPI** installation to reach
the drive — Adaptec's SCSI interface, unsupported by Windows natively since
Vista, and increasingly hostile territory on Windows 11 as cross-signed
legacy kernel drivers lose blanket trust. Exact Audio Copy, by contrast, is
actively maintained, genuinely Windows-11-compatible, and built on the
modern **SPTI** interface — the thing cdrdao's Windows port was never
adapted to use. On Linux, cdrdao needs none of this: ASPI's Windows-only
baggage doesn't apply, and it is the actively-supported, natural choice
there.

| | cdrdao (Linux) | Exact Audio Copy (Windows) |
| :--- | :--- | :--- |
| Platform | Native, actively maintained | Native, actively maintained (v1.8, 2025) |
| Drive access | Direct SCSI, no legacy layer needed | Modern SPTI |
| Output | `.toc` (text) + audio | `.cue` + log — same `MM:SS:FF` frame timebase (§3) |
| Read verification | `--paranoia-mode 0-3`, built in (§5) | "Secure mode" (multiple read passes); mapped to the same 0-3 scale at the adapter, not exposed as a second setting |
| Automation | Pure CLI, built for scripting | **GUI-only** — checked directly against the real 1.8 build, corrected from an earlier, unverified claim; see [SPEC027](SPEC027-cd-ripping-windows-automation.md) |
| Licence | GPL-2.0-or-later | Freeware, **not open source** |

**Confirmed on real hardware, not only researched — 2026-09-03, see
[LOG005](../LOG005-cd-ripping-hardware-findings.md).** The same physical
USB drive failed DAE via `cdrdao` on Linux and extracted cleanly via
EAC/SPTI on Windows, exactly as the reasoning above predicts.

**`[SPEC-RIP-022]` The one real tradeoff, named rather than absorbed
silently.** Every other external tool Vipunen depends on — `ffmpeg`,
`fpcalc`, the Essentia extractor, cdrdao itself — is open source, matching
`[GDE-ARC-018]`'s own licence-direction discipline; EAC is not. Subprocess
invocation (never linking) is the same posture that already keeps AGPL/GPL
tools clean of that question, but it does not make EAC open source — this
is a deliberate, Windows-specific compromise, not a clean architectural
fit, made because the alternative (chasing an abandoned Adaptec driver
into an OS actively hardening against it) is not a realistic path today.

**`[SPEC-RIP-024]` Both are optional, user-installed dependencies; the
capability degrades gracefully, never crashes, when neither is found.**
The same posture `analyze_amplitude.py` already takes toward a missing
`ffmpeg` (`shutil.which()` at startup, a clean `{"ok": false, "error":
"..."}`/plain message at the point of use, never a traceback) applies here:
detect the platform's own tool on PATH, and if it is absent, the "Rip a
CD" action is offered as unavailable with a plain reason rather than failing
opaquely mid-rip. Nothing else in Vipunen requires either tool — a library
built entirely from files already on disk `[SPEC024]` never touches this
code path at all `[REQ-LIB-250]`.

A thin adapter boundary — "produce a TOC/cue-equivalent structure plus the
ripped audio" — is what the rest of this document specifies against,
letting §§3-7 read identically regardless of which side actually ran.

---

## 3. Reading the TOC

**`[SPEC-RIP-030]`** cdrdao's `.toc` and EAC's `.cue` both express positions
as `MM:SS:FF` — minutes, seconds, **frames**, 75 frames per second, the
CD's own timebase, not milliseconds — the standard CD-frame convention
shared by both formats, not a coincidence needing two conversions. One
function, tested on its own rather than folded into a larger one, the same
lesson McRhythm's own tick-based timing already cost this project's
lineage once (`[GDE-MCR-*]`):

```
ms = round((minutes * 60 + seconds) * 1000 + frames * 1000 / 75)
```

**`[SPEC-RIP-035]`** A `PREGAP` is silence *before* a track's own audio
begins, conventionally 2 seconds before track 2 onward and commonly absent
before track 1; an `INDEX` marks a sub-position inside a track (a hidden
interlude at `INDEX 00`, the audible start at `INDEX 01`). Both are read
from the TOC and kept distinct from the track's own `start_ms`/`end_ms` —
collapsing a pregap into the previous track's length would silently move a
boundary the disc itself did not draw there. What becomes of real audio
found in these spans — rather than the silence a pregap ordinarily is —
is designed in [SPEC026 §1](SPEC026-cd-ripping-passages.md#1-hidden-and-pregap-only-audio--its-own-passage-optionally-folded-in-too).

---

## 4. Encoding

**`[SPEC-RIP-040]`** cdrdao reads raw PCM; nothing library-facing consumes
that directly. A rip decodes once, to a working lossless file (WAV or
FLAC), then that working file is encoded to the library's own format and
discarded `[REQ-LIB-240]` — an archival lossless copy is opt-in, never kept
by default, the same "off unless asked" posture `[REQ-VIS-205]` already
holds for cue sheets, cover art and lyrics written outside Lempi's own
storage.

**`[SPEC-RIP-045]` Default library format: MP3, matching what the library
already is.** The existing library is overwhelmingly MP3 with some OGG
Vorbis mixed in; introducing FLAC as a third format for new rips would cost
every downstream tool (extraction, fingerprinting, the appliance's own
storage-budget math, `[REQ-HW-100]`) a third case to handle for no benefit
once the archival step is past. FLAC on the appliance specifically is
worth naming as a **non-goal**: it runs roughly 2-3x an equivalent-quality
lossy encode for no audible gain on that hardware, and the appliance's SD
card is the resource-constrained end of this system, not the desktop doing
the ripping.

---

## 5. Paranoia level

**`[SPEC-RIP-050]` A user-adjustable setting, 0-3, default 2**, per
`[REQ-LIB-245]`. Matches `cdrdao read-cd --paranoia-mode` exactly rather
than inventing a parallel scale:

| Level | Behaviour | Cost |
| ---: | :--- | :--- |
| 0 | No checking — copied directly from the drive | fastest |
| 1 | Overlapped reads, avoids jitter | + |
| 2 | **Level 1, plus verification of the read audio data (default)** | ++ |
| 3 | Level 2, plus scratch detection and repair | slowest |

cdrdao's own upstream default is 3 (full paranoia). Lempi's default is
**2**, a deliberate one-step departure: a personal collection is presumed
mostly undamaged, and level 3's scratch-detection-and-repair pass costs
materially more time for discs that will rarely need it. Level 3 stays one
setting away for a disc that is visibly scratched — the point is a sensible
default, not a ceiling. Stored as a Vipunen console setting (its own sidecar,
alongside `remote_config`'s existing key/value shape `[SPEC-DF-*]`), never
a `lempi.db`/player table — this is purely a Vipunen-side ripping preference,
not listener state.

---

## 5a. Handling rip failures — best effort, never silent

**First real-hardware finding, 2026-09-03: see [LOG005](../LOG005-cd-ripping-hardware-findings.md).**

**`[SPEC-RIP-052]` No optical drive detected degrades exactly like no
ripping tool found (`[SPEC-RIP-024]`).** The two checks — tool on PATH,
drive present — run together at the same point, and either absence has
the same effect: "Rip a CD" is offered as unavailable with a plain reason
rather than left to fail on click.

**`[SPEC-RIP-054]` A track that fails to verify at the configured
paranoia level does not abort the rip.** The tool's own retries (§5) run
first; if a track still won't verify, ripping continues with the
remaining tracks rather than stopping the whole disc, and the failed
track is written anyway rather than silently dropped — a degraded rip of
a scratched disc is more useful than none. The attempt is recorded as its
own `ingest_decisions` row (`stage='rip'`, the same shape `[SPEC-RIP-075]`
already writes for Disc ID matches), outcome `verification_failed`,
discoverable as a worklist the same way an unconfirmed segmentation
already is (`[REQ-LIB-215]`, [SPEC024 §7](SPEC024-dao-segmentation-cascade.md#7-review-queue))
— reviewed and re-ripped, or accepted as-is, not left in the library with
no marker at all.

**`[SPEC-RIP-056]` A drive that stops responding mid-rip — a jammed tray,
a disc needing to be reseated — is the same failure shape as a
verification failure, not a new one.** Report which track was in
progress, offer retry, and treat anything ripped before the stall as
already good: `ingest_decisions` rows are per-track, not per-disc, so a
stall on track 7 does not implicate tracks 1-6. This is the single-disc
case; a *disc* needing to be swapped deliberately, mid-set, is the
multi-disc rip session's own prompt (`[SPEC-RIP-104]`), already covered.

---

## 6. Disc ID, CD-TEXT, and MusicBrainz

**Designed in [SPEC028](SPEC028-cd-ripping-identification.md)**, split out
once it and [SPEC026](SPEC026-cd-ripping-passages.md) together pushed this
document past `[GOV-DOC-010]`'s line limit: how the TOC's own sector
offsets resolve a MusicBrainz Disc ID match (`[SPEC-RIP-060]`), the
AcoustID fallback when nothing resolves (`[SPEC-RIP-065]`), and CD-TEXT's
precedence over a MusicBrainz match when both exist (`[SPEC-RIP-066..068]`).

---

## 7. Provenance

**`[SPEC-RIP-070]` `boundary_src = 'imported:cdrdao-toc'`** (or the
ripping tool actually resolved in §2) — an instance of `[SPEC008]`'s own
already-stated `'imported:<x>'` convention, not a new one invented for
this. Ranked above `computed:segment-cascade@v1` and below `manual`
`[REQ-LIB-225]`: a disc's own TOC is ground truth the cascade only
approximates, and a person's own correction still outranks both, the same
"never silently recompute over a human decision" rule `[SPEC-SC-045]`
already holds everywhere else.

**`[SPEC-RIP-075]`** The Disc ID match, when one is made, writes an
`ingest_decisions` row (`stage='rip'`) the same shape
`tools/choose_release.py`'s `stage='release_match'` rows already use —
outcome the matched release, confidence a margin where a fuzzy match
returned more than one candidate, detail the candidates considered. A rip
that resolved nothing writes nothing here; there is no decision to record,
only a fallback to the ordinary identification path `[SPEC-RIP-065]`
already covers.

---

## 8. Open

**`[SPEC-RIP-080]`** Every question this section used to hold open — the
ripping-tool choice, hidden/pregap audio, multi-disc sets including the
rip-session prompt flow, CD-TEXT vs. MusicBrainz, and drive/hardware
failure modes — is decided, in this document (§§2, 5a, 6) or in
[SPEC026](SPEC026-cd-ripping-passages.md).

**One new question, found by real-hardware testing rather than left
unexamined: how does Vipunen actually drive EAC, given it has no unattended
CLI mode?** Resolved 2026-09-04 in [SPEC027](SPEC027-cd-ripping-windows-automation.md):
person-assisted (shape (c) there) — `ingest_cd.py` reads a completed rip
rather than driving one.

**Still open, named rather than silently absent:** Vipunen spawning `cdrdao`
itself on Linux — this pass's own `.toc` parsing is ready for it, but
`[LOG-RIP-030]` found the one drive tested returns noise over `cdrdao`'s
DAE path on Linux, so a *working* rip must be verified on some drive
before automating it is worth doing — and [SPEC026](SPEC026-cd-ripping-passages.md)'s
hidden-audio/multi-disc passages, deferred by this build-out's own stated
scope, not by any remaining design gap.

---

**Traceability:** `[SPEC-RIP-010..056]`, `[SPEC-RIP-070..080]` · derives
`[REQ-LIB-220..290]` · extends `[SPEC-SC-045]`'s provenance ladder and
`[SPEC008]`'s `imported:` convention · complements, does not replace,
`[SPEC024](SPEC024-dao-segmentation-cascade.md)` · extended by
[SPEC026](SPEC026-cd-ripping-passages.md) for hidden-audio and multi-disc
passage representation, [SPEC027](SPEC027-cd-ripping-windows-automation.md)
for driving a GUI-only Windows tool, and
[SPEC028](SPEC028-cd-ripping-identification.md) for Disc ID/CD-TEXT/
MusicBrainz
