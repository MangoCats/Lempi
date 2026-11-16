# LOG015: Library Work, to 2026-11-08

**Experiment Record — closed, continued in LOG014 · split from it 2026-11-16**

The oldest entries of [LOG014](LOG014-library-work.md), moved here by its own
rule when it passed 250 lines `[LOG-LIBW-040]`, and unchanged. What is still
open, and every entry since, is there.

> **Related:** [LOG014](LOG014-library-work.md) for how an entry is written
> and what is open · [data/README.md](../data/README.md) for where each copy
> of the pair lives

---

## 1. Entries, newest first

**`[LOG-LIBW-250]` 2026-11-08: *Broken*, and the backups pruned by rule.**
Nine Inch Nails' *Broken* is in the catalogue: one image file, **9 album
passages and 8 radio**, read from the hub that day. Its run of silent tracks
is one album passage and none for rotation `[SPEC-SIL-020]`. The player change
that plays it that way (`4bca76a`) is on the desktop only, so a speaker plays
*Broken* as an album without that rule until it is deployed. The hub held
138 passages back that day, against 112 on 2026-11-05: rips added since.
From that night, tools' backups older than 30 days are removed
`[SPEC-STAR-084]`. The first run removes the three of 2026-09-27
(`pre-covers-`, two `pre-rekey-`), 3.5 GB, and keeps
`prime-time-original-20260927/`, a song's original file.

**`[LOG-LIBW-230]` 2026-11-07: the rest of the short ends, and their cause
closed.** At the maintainer's explicit word, after seeing the number
(218 tracks / 425 passages for the fades, not album timing):
`repair_passage_ends.py --only fade`, 67 passages on 39 tracks, then
`--only cut --min-tail-ms 1000`, 8 on 4, found by a 1 s tail rehearsal.
Hub only. Backups `data/backups/pre-fade-ends-20261107/` and
`pre-small-tails-20261107/`. The cause was closed at intake the same day by
`21586b6` `[REQ-LIB-145]`: the phone's found-file scan (`player/src/tags.rs`)
sums packets for MPEG and AAC, and `segment_dao.py` decodes instead of asking
`ffprobe`. Over all 6,888 files, 1,657 had been more than 5 s from the decode;
none were afterwards. Mainline intake was already clean: 150 of 1,184 files
added since 2026-09-01 matched a fresh decode within 1 ms.

**`[LOG-LIBW-220]` 2026-11-06: an audit, and passages that stopped before the
music did.** Hub, `bose` and `smartboardpc` read. Of 704 files with a tail of
5 s or more, 194 stopped with loud music still to come, by up to 100 s
`[SPEC-SA-112]`. Their ends came from a bitrate estimate: of the first 218
MP3s, 211 ended within 100 ms of `ffprobe`'s figure, a median 13.8 s short
of the decode. 207 came from the MuLibPlay migration and 11 from the first
ingests of 2026-08-15. `repair_passage_ends.py --only cut`: 358 passages on
179 tracks, hub only, backup `data/backups/pre-passage-ends-20261106/`.
*A correction made that day:* an early report overstated the cut files,
because `volumedetect` runs before an output `-ss` trims, so its tail levels
were the whole song's. Measure a tail with `-af atrim=start=X,volumedetect`
and check `n_samples` against the tail's length.

**`[LOG-LIBW-210]` 2026-11-05: an AccurateRip match outranks read errors.**
The maintainer's rule: a track AccurateRip matched is good, whatever its read
errors `[SPEC-RIP-120]`, and a file is judged only by its own disc's log
`[SPEC-RIP-121]`. Applied: 10 tracks recorded and held (*Batman* 2-9,
*Hot Rocks* 1-12, *Voodoo Lounge* 10), 5 released (*The Last DJ* 10 and 11,
*99.9 F°* 4 and 7, *Tommy* Orchestral 2). The hub then held 112 passages;
`bose`, `lempi02w`, `lp3-wifi`, `lrhtpc`, `smartboardpc` and `teacherslounge`
104 each, **read on each node** from `passages.director_hold`. The 8 between
them are *She Who Dwells* disc 2, arriving with its audio on the next Export.
A release is written `""`, not NULL: a bundle carries only decided values, so
NULL would leave a speaker's old hold in place `[SPEC-HOLD-080]`. Backup
`data/backups/pre-damage-rule-20261105/`.

**`[LOG-LIBW-200]` 2026-11-05: albums filed under a literal `~`.** From
2026-11-03 the console, started without `--root`, filed albums under an
unexpanded `~/Music`. Fixed and the files migrated `[SPEC-NAM-070]`. Backup
`data/backups/pre-tilde-move-20261105/`.

**`[LOG-LIBW-110]` Before this log: what the backups say.** Earlier library
work was recorded in its own commit and specification, not here. The
backups it left, oldest first:

| backup | the work | recorded in |
| :--- | :--- | :--- |
| `pre-rekey-20260927T0415Z`, `pre-rekey-20260927T1257Z` | The identity hash re-keyed to Symphonia | [SPEC045](spec/SPEC045-the-identity-hash.md) |
| `prime-time-original-20260927` | *Prime Time*'s WAV wrapper removed; the original file kept, and kept past the 30 days | `f34102c`, `15e66e4` |
| `pre-covers-20260927` | Cover art brought in (`data/induct-covers-2026-09-27.log`) | — |
| `pre-repair-20261018T2158Z` | `repair_review_copies.py` undid the review copies a star sync had made on the hub | `[SPEC-STAR-051]` |
| `before-highway61-merge-2026-10-30` | *Highway 61 Revisited*, ripped again and merged | [SPEC069](spec/SPEC069-a-disc-ripped-again.md) |
| `before-wywh-swap-2026-11-01` | *Wish You Were Here* swapped for a new rip | `[SPEC-RTF-015]` |

The last two were read back by `tools/backfill_retired.py` to record which
files those merges retired. The hub held the 11 `retire` decisions it wrote,
dated 2026-10-30 and 2026-11-01, on 2026-11-08, so neither backup is needed
for that any more.

---

**Traceability:** an Experiment Record per [GOV001](GOV001-document-hygiene.md);
nothing here is a requirement. Each entry traces to the tag of the tool or
rule it applied.
