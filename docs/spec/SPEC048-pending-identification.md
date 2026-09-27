# SPEC048: Pending Identification — Deciding What a Node Sent

**Design Specification — Tier 2 · written 2026-09-27, from the maintainer's direction that day**

A Lempi node sends Vipunen the music it found that the catalogue does not hold
`[REQ-AND-280]`, and what arrives waits until a person decides
`[REQ-AND-289]`. This is how the waiting is shown and the deciding is done. It
follows `tools/intake.py` — which receives — and adds nothing to it but the
repair routes of `[SPEC-PID-040]`.

> **Related:** [REQ007](REQ007-android.md) `[REQ-AND-280..289]` (the phone's side) · [SPEC013](SPEC013-vipunen-console.md) (the console) · [SPEC047](SPEC047-covers.md) (covers)

---

## 1. Identifying

**`[SPEC-PID-010]` Each waiting file is identified by its sound and by its
tags.** AcoustID is asked with the file's *measured* length, never the length
the sender offered: the Moto G offered "Think" as 160.5 s, and AcoustID, which
weighs the length it is given, named nothing until it was told 136. MusicBrainz
is searched by the file's artist and title, with its album first and then
without, and each result is marked for agreeing title and artist and for length
within 3 s. `tools/pending.py identify` writes what it found into the file's
note, beside it. The key is `secrets/acoustid.key`; without one the tags search
still runs, and the note says AcoustID was not asked.

**`[SPEC-PID-020]` The answer names what the library holds.** A song has many
MusicBrainz recordings, and AcoustID's first is often not the one the library
credits: Hendrix's "The Wind Cries Mary" gave four at 0.96, and the library's
was the second. So the best answer is the first strong candidate — AcoustID at
0.9 or more, or MusicBrainz with tags and length agreeing — that the library
holds, else the first strong one. It is shown as artist, recording, and the
releases it appears on, with the library's copy where there is one.

---

## 2. Deciding

**`[SPEC-PID-030]` Three outcomes, each a person's.**

| outcome | what happens | afterwards |
| :--- | :--- | :--- |
| **induct** | copied, checked by its bytes, to `MUSIC/<artist>/<album>/` by its own tags (to `MUSIC/<artist>/` with no album tag), then the usual induction over that folder | `inducted/` |
| **reject** | set aside | `rejected/`; the intake answers `rejected` if it is offered again, and does not take it |
| **repair** | the sender is offered the library's good copy `[SPEC-PID-040]` | waits, then `repaired/` |

A file with no artist tag is not inducted: there is nowhere honest to put it.
Nothing is placed over a different file already at the destination. The
console runs each as a job — the same `pending.py` command a person would type
`[SPEC-SUI-015]` — on its **intake** page, where each file can be played first.

**`[SPEC-PID-040]` A damaged copy is repaired at the sender, not inducted.** A
waiting file is a damaged copy of a library file when it has the same name and
size, at most 5% of its 4 KiB blocks differ, and the library's copy is still
the bytes the catalogue recorded. Found 2026-09-27: all four files the Moto G
first sent were such copies — one run of 20 to 52 KB each, starting on a 4 KiB
boundary, on its SD card — which is why none matched anything by signature. The
page says so, with where the damage lies.

Repair is carried by the intake, since the damage is on the sender:

1. `GET /repairs` lists each decided repair: the damaged byte hash, the good one, its size.
2. `GET /good/<sha256>` serves the good copy, only for a decided repair, checked against its hash before it is sent. It is not a way to fetch the library.
3. The sender writes it over its copy and reads it back, then sends `POST /repaired/<damaged sha256>` with what its file now hashes to. Only the good copy's hash closes the repair.

On the phone this is the Send screen: asking Vipunen also asks for repairs, and
"Repair N damaged file(s)" fetches and checks each good copy, asks Android's
leave to write over files Lempi did not create `[SPEC-PL-098]`, and writes them.
*Measured 2026-09-27 on the Moto G:* four repairs decided in the console, one
file damaged again on purpose for the test; all four fetched, written with
leave and reported, and the damaged file's bytes on the card then hashed to the
good copy's. The phone's catalogue still names each by its old signature until
its player rescans them, and Vipunen's next bundle names them.

---

**Traceability:** `[SPEC-PID-010..040]` · from the maintainer's direction of 2026-09-27 · refines `[REQ-AND-289]`
