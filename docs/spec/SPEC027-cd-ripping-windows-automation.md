# SPEC027: CD Ripping — Driving a GUI-Only Tool on Windows

**Design Specification — Tier 2 · Decided 2026-09-04 — person-assisted (c)**

One question, found by real-hardware testing rather than left unexamined,
and split into its own document the moment it pushed
[SPEC025](SPEC025-cd-ripping.md) past `[GOV-DOC-010]`'s 300-line hard
limit — the same reason [SPEC026](SPEC026-cd-ripping-passages.md) exists
as its own document rather than a section of SPEC025.

> **Status.** Decided and built 2026-09-04: shape (c), person-assisted
> (§2). `tools/ingest_cd.py` reads a rip a person already ran to
> completion — it never touches EAC's window or the optical drive itself.
> Read
> [SPEC025 §2](SPEC025-cd-ripping.md#2-the-ripping-tool--decided-per-platform)
> and [LOG005](../LOG005-cd-ripping-hardware-findings.md) `[LOG-RIP-060]`
> first — this document exists because of what they found, not
> independently of them. (a) and (b) below are kept as the alternatives
> actually weighed, not deleted now that (c) won.

> **Related:** [SPEC025](SPEC025-cd-ripping.md) for why EAC is the decided
> tool on Windows despite this open question about how to drive it ·
> [LOG005](../LOG005-cd-ripping-hardware-findings.md) for the real-hardware
> test that found it

---

## 1. The question

**`[SPEC-RIP-082]` How does Vipunen actually drive EAC, given it has no
unattended CLI mode?** `[LOG-RIP-060]` confirmed there is no documented or
working headless invocation — checked directly against the real 1.8
build's own bundled documentation, not assumed from a secondary source.
`-testandcopy -imagewav ... -close`, the switches [SPEC025 §2](SPEC025-cd-ripping.md#2-the-ripping-tool--decided-per-platform)
originally cited, do nothing when actually run: the process launches and
sits idle. What is real is a handful of driver-compatibility flags
(`nocdtext`, `nostopcommand`, `notestunit`, `nospeedsel`, `noreadsub`,
`nomultisession`) and a per-track *post-encode* external-program hook —
neither drives a rip.

Only the GUI reaches the actual extraction, and only a person or something
driving the GUI itself can reach it. Mouse/keyboard automation against
EAC's own window was proven *possible* in the course of the same test that
found this gap — but also proven fragile in the same session: one
imprecise coordinate closed the whole application rather than the intended
dialog, on a custom-painted UI that does not expose standard accessible
button roles to `UIAutomation`'s `Invoke` pattern, only raw bounding
rectangles a caller must click blind.

---

## 2. Three shapes — (c) decided

**`[SPEC-RIP-084]` (a) Genuine UI automation, accepting the fragility.**
Coordinate- or image-based clicking against EAC's own window, scripted
end to end. Cheapest to build on top of what already exists; the least
robust across an EAC version change, a moved window, or a differently
sized display, and the one approach this document's own originating test
already showed can go wrong in a way that damages nothing but wastes the
attempt (closing the app, not the data).

**`[SPEC-RIP-086]` (b) A different, more automatable SPTI-based Windows
tool in EAC's place.** The *hardware* question — does SPTI work on this
drive at all — is now answered independently of EAC specifically
(`[LOG-RIP-050]`): any tool reaching the drive over SPTI, not only EAC,
should see the same clean DAE this test measured. Whether a genuinely
scriptable alternative exists with EAC's own accuracy reputation, or
whether one would need to be built, is not researched here.

**`[SPEC-RIP-088]` (c) A person-assisted flow — decided, and built
2026-09-04.** Vipunen prepares (which disc, which release, where the output
goes) and a person clicks through EAC's own GUI once per disc — closer to
how a manual rip already works today than to `[SPEC-RIP-024]`'s "detect
the tool, offer the action" automation model the rest of SPEC025 assumes.
The cheapest to build, and the one that concedes the most: "Rip a CD" is
not a single Vipunen-driven action the way every other ingest step is —
`tools/ingest_cd.py` is a `cd-rip` job that ingests a folder a person
already finished ripping into, offered as its own explicit console action
(`SKIPPED` in `tools/jobs.py`, the same posture `segment`/`amplitude`
already have), never a button that reaches the drive itself. Chosen over
(a) and (b) below on the evidence this same test produced: (a) was proven
fragile in the very session that found this gap (one imprecise coordinate
closed the whole application), and (b) remains unresearched — (c) asks
nothing of either problem, at the cost named above.

---

## 3. What this does not affect

**`[SPEC-RIP-089]`** Nothing about §§1-2 changes what SPEC025/SPEC026
already decided: TOC/cue parsing, encoding, paranoia level, Disc ID
lookup, provenance, hidden-audio and multi-disc passage representation are
all settled independent of how the tool is actually launched. This
document is scoped to the one missing link — invocation — not a reopening
of anything already decided.

---

**Traceability:** `[SPEC-RIP-082..089]` · found by
[LOG005](../LOG005-cd-ripping-hardware-findings.md) `[LOG-RIP-060]` ·
decided (c), built as `tools/ingest_cd.py` and `jobs.py`'s `cd-rip` kind,
2026-09-04 — unblocks [SPEC025](SPEC025-cd-ripping.md) on Windows. The
Linux path (`cdrdao`, genuine CLI) was never blocked by *this* invocation
question, but is blocked regardless, by a different and lower-level
finding this document does not own: `[LOG-RIP-030]` found the one drive
tested returns noise, not audio, over `cdrdao`'s own Linux DAE path —
[SPEC025 §8](SPEC025-cd-ripping.md#8-open)
