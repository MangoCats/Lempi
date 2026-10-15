# GUIDE038: Closing the Integrity Gaps

**Guide — Tier 2 · plan, written 2026-10-14 · phases 0 and 1 done 2026-10-15; 2 to 4 not built**

The plan that follows from the
[integrity audit of 2026-10-14](../reports/integrity-audit-2026-10-14.md): every
file the fleet holds is hashed when it arrives, and almost nothing ever hashes
it again. A speaker's SD card can lose a song, a database can change a value,
a backup can rot on the mirror, and nothing would say so until it was needed.
This is how each of those becomes something the system notices, says, and
where it can, repairs.

> **Related:** [SPEC046](spec/SPEC046-star-sync.md) for the sync this reports
> through · [SPEC057](spec/SPEC057-holding-a-passage-back.md) for holding a
> passage back · [SPEC012](spec/SPEC012-library-relink.md) for `relink` ·
> [GOV002](GOV002-sources-of-truth.md) for ranking sources by measurement

---

## 1. Principles

**`[GDE-INT-010]` A file is checked where it lies, by the machine that holds
it.** The hub cannot vouch for a speaker's card; a sync's "has the file" is a
row, not a reading. Each machine re-hashes its own files against the hash its
own catalogue records, on a schedule, and keeps the result.

**`[GDE-INT-015]` A finding changes what happens, not only what is logged.** A
damaged file is withheld from the Director, shown where a person looks, and
offered for repair from a good copy. A log line nobody reads is how the gap
this closes stayed open.

**`[GDE-INT-020]` A check that did not run says so.** A pass cut short, a file
it could not open, a node that would not answer: each is a line in the result,
never a silence that reads as clean (CLAUDE.md §5 and §6).

## 2. Phase 0 -- measure before pacing anything

**`[GDE-INT-100]` One full pass on each speaker, at a quiet hour, by hand.**
`sha256sum` of every file its catalogue names, compared with the recorded
hash: how long it takes on `bose` (Pi 4), `lp3-wifi` (Pi 3) and `lempi02w`
(Pi Zero 2 W), and whether anything is already damaged. The hub's pass took
117 s for 48.8 GB; a Pi reading an SD card will be far slower, and that number
sets the scrub's pace in §4. Nothing is written.

*Run 2026-10-15, players idle, `nice -n 19 ionice -c3`, over the 5,870 files
each catalogue then named (49.3 GB):*

| node | time | rate | result |
| :--- | ---: | ---: | :--- |
| `smartboardpc` | 141 s | 349 MB/s | 5,870 match |
| `bose` (Pi 4, SD) | 19 min | 43 MB/s | 5,870 match |
| `lp3-wifi` (Pi 3, SD) | 43 min | 19 MB/s | 5,869 match; one row names a path that does not exist |
| `lempi02w` (Pi Zero 2 W, SD) | 37 min | 22 MB/s | 5,870 match |

No file's bytes differ anywhere. `lp3-wifi`'s one is a stale path, not damage:
its row for Robyn's *BodyTalk* has said `Bjork/BodyTalk.mp3` since 2026-08-09,
while the file, its bytes matching the record, is at `Robyn/BodyTalk.mp3`, as
`bose` and the hub record it. A player cannot open that passage there, and
nothing said so -- the scrub's `missing` outcome, found by its first pass.
Corrected the same day by hand, one row: the path and the file's own mtime.
A sync neither notices nor undoes it: `path` is a node's own column, outside
both the drift check and the patch (`SUMMARY_COLS`, `SIGNED_EXCLUDE`).
`lempi02w` was measured the same afternoon, after a send to it had ended.

## 3. Phase 1 -- small, and closes the most visible gaps

**`[GDE-INT-110]` Review decisions say where they go next, and can get
there.** *Built: the review page's half 2026-10-14 as `[REQ-LIB-177]`, the
Export page's 2026-10-15.* The review page says how many decisions wait to
reach the library and applies them, Vipunen's two steps relayed. The Export
page says "N review decisions are recorded and not yet in this library",
linking to both ways to apply them, since an empty Export with 42 waiting read
as decisions lost.

**`[GDE-INT-115]` The hub verifies its library every night.** *Built
2026-10-15: `tools/verify_library.py`; first run 5,870 of 5,870 match, 49.3 GB
in 120 s.* Every catalogued file re-hashed against
`files.sha256`; a mismatch re-checked by `audio_md5`, so a re-tag (bytes
changed, audio intact) is told from damage; a missing file named. A console
job, `verify-library`, with a button on the Jobs page, and run by the
*Lempi hub backup* scheduled task after the backup. Its result is kept
(`data/verify/<date>.json`) and `star_sync.py status` reports its age and any
finding, STALE past 30 h, as it already does the backup.

**`[GDE-INT-120]` Backups are re-read, here and on the mirror.** *Built
2026-10-15; first run here: all 24 kept objects whole, 9.9 GB in 13 s.* Each
night `backup` re-hashes every kept object not proven for six days, so each is
read within a week, and asks the mirror for the same with one `sha256sum` over
ssh. What was proven, when, and anything not whole is kept in
`<backups>/proven.json`; an object whose hash is not its name is said, makes
`status` STALE, and is re-read every night until it is whole. `status` says the
oldest proof here and on the mirror.

*Found building it: the mirror has not been written since 2026-09-26.* Every
nightly run since at least 2026-10-01 logged `MIRROR FAILED: reach the mirror:
exit 255`, and `status` STALE with it; the mirror is `teacherslounge`, which is
usually off at 03:30 -- it went offline again the afternoon of 2026-10-15 after
being brought up to date. The hub's pair has had no copy off this machine for
nearly three weeks. The maintainer's decision the same day: keep the mirror on
`teacherslounge`, daily, written whenever it is reachable -- `[SPEC-STAR-088]`.

## 4. Phase 2 -- each speaker checks its own audio

**`[GDE-INT-130]` A scrub in the player.** A background task walks the
catalogue's files, longest-unchecked first, hashing each against
`files.sha256` at a paced rate (bytes per minute, set from `[GDE-INT-100]`)
and only while the audio path is not starved. Results go to a new
listener table, `file_checks (audio_md5 PRIMARY KEY, checked_at, outcome,
size, detail)` -- in the **listener**, since `bose` mounts its catalogue
read-only. Outcomes: `ok`, `retagged` (bytes differ, audio intact),
`damaged`, `missing`, `unreadable`. It records the size it measured, which
corrects the stale `size_bytes` the audio found on `bose` without writing the
catalogue. `star_merge.py`'s table list needs `file_checks` as `LOCAL`, or the
merge refuses the next run for a table with no rule.

**`[GDE-INT-135]` A damaged file is withheld.** The Director leaves out the
passages of a file whose last check is `damaged`, `missing` or `unreadable`,
read the way holds already are (`optional_read`, so an older listener without
the table plays as before). The player's Settings page says how many, and
which; a whole-album play skips them as it skips a held passage
`[REQ-PD-127]`.

**`[GDE-INT-140]` A decode failure is checked at once.** When playback logs
`decode:` or the bit-reservoir warning that suggests damage, that file goes to
the front of the scrub, so the failure becomes a finding within minutes and
is withheld if it is real -- not a line in a journal that a reboot clears.

**`[GDE-INT-145]` The hub hears of it.** The sync snapshot carries each
node's `file_checks` summary: how much of the library was checked, when the
last full pass ended, and every file not `ok`. `SUMMARY.md` lists them per
node; `status` names a node whose pass has not completed in 30 days.

**`[GDE-INT-150]` Repair is a send.** Export's diff treats a file the node
reports damaged as one it lacks, so *Send what's missing* bundles its audio.
The import already replaces a held file whose bytes differ when the staged
copy matches (`replace_if_rewritten`), and the next check finds it `ok`.

**`[GDE-INT-155]` The phone, later.** The same scrub in the Android host, run
only while charging. Its files are trusted on size and mtime today
`[REQ-AND-260]`.

## 5. Phase 3 -- the databases

**`[GDE-INT-160]` Each speaker checks its databases weekly.** `PRAGMA
quick_check` on read-only connections while idle, the result kept beside the
file checks and reported in the snapshot. A sync does not commit to a node
whose last check failed; it says why. The boot-time recovery stays the cheap
read it is (`lempi-db-recover`), for the reason it gives.

**`[GDE-INT-165]` A changed value must have a reason before it is sent.**
Structure is checked; values are not, and a sync spreads a changed value
everywhere. Before a run's patches, compare the hub's catalogue with the last
backup: a row whose contents changed while its provenance and time columns did
not is listed as an item for a verdict, not patched. **Measure first**: count
such rows across consecutive daily backups. If edits always move their
`source` or a timestamp, the count is near zero and this is a sharp tripwire;
if not, the tools that edit without a stamp are found and fixed first.

## 6. Phase 4 -- the hub's own audio, and what nothing tracks

**`[GDE-INT-170]` The hub repairs from a speaker.** The hub's audio has no
backup of its own, only the speakers' three copies. Once `[GDE-INT-130]` has
proven them, a damaged hub file is fetched from a speaker whose last check of
it is `ok`, its hash confirmed, and put back -- the speakers become the
verified backup they already are in fact.

**`[GDE-INT-175]` Sidecars and strays.** A CD import records the hash of its
rip log, `.accurip` and CUE in `ingest_decisions`, and `[GDE-INT-115]` checks
them with the audio. The verify job lists audio in the music folder that no
row claims -- 13 MP3s, 21 WAV and 14 AIFF files tonight -- for a person to
induct or remove.

## 7. Order and size

| phase | items | size | needs |
| :--- | :--- | :--- | :--- |
| 0 | `[GDE-INT-100]` | an evening, by hand | -- |
| 1 | `[GDE-INT-110]`, `[GDE-INT-115]`, `[GDE-INT-120]` | small each | -- |
| 2 | `[GDE-INT-130]` to `[GDE-INT-150]` | the largest: a player task, a table, the Director, the snapshot | phase 0's numbers |
| 2 | `[GDE-INT-155]` | medium | 2 |
| 3 | `[GDE-INT-160]`, `[GDE-INT-165]` | medium; 165 after its measurement | -- |
| 4 | `[GDE-INT-170]`, `[GDE-INT-175]` | medium, small | 2, 1 |

Phases 1 and 0 can go together; each phase is useful on its own.

## 8. Open

1. **Does the scrub run while playing?** Reading a few hundred KB a second is
   nothing beside an SD card's bandwidth, but `lempi02w` shares one radio and
   one weak CPU; phase 0 decides.
2. **Is `retagged` a finding?** The audio is intact; the bytes are not what
   the catalogue records, so a bundle would call it different. Likely: shown,
   not withheld, and the record brought up to date at the next send.
3. **How long `quick_check` takes** on a 1.1 GB library on a Pi Zero 2 W.

---

**Traceability:** `[GDE-INT-010..175]` · from the
[integrity audit of 2026-10-14](../reports/integrity-audit-2026-10-14.md) ·
touches `[SPEC-STAR-082]`, `[SPEC-STAR-090]`, `[SPEC-HOLD-010]`,
`[REQ-LIB-165]`, `[REQ-LIB-175]`, `[REQ-AND-260]`, `[REQ-PD-127]`
