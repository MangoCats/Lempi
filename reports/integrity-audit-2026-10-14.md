# Integrity audit: what can go bad unnoticed (2026-10-14)

Which of the fleet's files carry an integrity record, when that record is
checked, and which files could become corrupt with no way of knowing. Asked by
the maintainer after the first sync under `[SPEC-STAR-082]`. The plan that
follows from it is [GUIDE038](../docs/GUIDE038-integrity-gap-closures.md).

**Method.** Read in the code, not taken from the documents: every place a
hash is computed, every place one is compared, every `integrity_check`, and
what a sync and the Export page actually ask a node. Then measured: the hub's
whole library re-hashed against its records, and each speaker's catalogue
read for what it records. Nothing in the tree or on a node was changed by the
audit.

---

## 1. What "the node has the file" means

A star sync's snapshot and the Export page's diff both ask the node's
**catalogue**: a `files` row with that `audio_md5` is "has it". Neither opens
the file. The snapshot's `path`, `size_bytes` and `mtime` are the node's own
record (`mesh_sync::catalogue_summary`, `mesh_diff.py`'s `files`), not a
reading of its disk. So "has every file" is a statement about rows.

## 2. What carries a record, and when it is checked

| what | its record | checked |
| :--- | :--- | :--- |
| Hub audio, 5,848 files, 48.8 GB | `audio_md5` (the encoded packets, tags and a cut-short last frame excluded) and `sha256` (the bytes), both on every row | **when sent**: `export_bundle.py` hashes the copy and refuses a bundle whose bytes differ from the record. By `relink`, run by hand. The console's library scan compares size only and says so. Nothing routine. |
| Speakers' audio, the same 5,848 on each of `bose`, `lp3-wifi`, `smartboardpc` | the same two, every row | **once, at import**: a staged file must match the payload's `sha256` (`bundle.rs`). Never after. |
| Phone audio | the same | at import; afterwards trusted if size and mtime are unchanged (`found.rs`) |
| Hub pair, `library.db` and `listener.db` | none of their own | `PRAGMA integrity_check` on the copy at every sync snapshot and every daily backup |
| A speaker's live pair | none | **never** -- `lempi-db-recover` deliberately does one cheap read at boot, not a check |
| A speaker's listener backups | none | `integrity_check` when one is exported or restored; an older one is tried if it fails (`backup.rs`) |
| Hub daily backups | named by their own SHA-256, sealed; the mirror re-hashes on arrival | when taken. **Never re-read** at rest, here or on the mirror |
| Bundles, snapshots, sync patches | SHA-256 per file or part; a patch row carries a digest of what it replaces | every transfer |
| Appliance binaries and configuration | deploy checks the upload's hash and the durable copy's `--version`; the overlay's lower layer is read-only; `--check` compares configuration with the repository | at deploy; `--check` when run by hand |
| CUE sheets (246), rip logs (56), `.accurip` (49), cover images (1,377) on the hub | **none** | never |

## 3. What could become corrupt with no way of knowing

1. **The speakers' audio, above all on SD cards.** `bose` and `lp3-wifi` hold
   48.8 GB each on `mmcblk0p4`; `lempi02w` its library on its card. Each
   file was checked once, on arrival. The catalogue holds the hash that would
   catch a change, and nothing reads it. A damaged file met in playback logs
   one `decode:` line, or a burst of bit-reservoir warnings, and the passage
   ends early (`engine::open`, `decoder.rs`); nothing is held, recorded or
   shown, and on an overlay node the journal does not outlive a reboot.
2. **A value inside any database.** `integrity_check` proves the structure,
   not the contents: a flipped bit in a stored value passes it. And a sync
   reads a changed value as an edit -- one on the hub's catalogue is patched
   into every speaker, one in a speaker's listener merged into the household.
3. **A speaker's live databases**, even structurally. The only signal is a
   query failing. When `lp3-wifi` logged "database disk image is malformed"
   about 110 times a second, it was found by reading its journal for
   something else; a `quick_check` run by hand then passed on both files, so
   that one was likely the library rewritten under the player, not damage
   (FLEET001 `[FLT-RUN-010]`) -- but the only reason anyone knows is that
   someone happened to look, and then ran the check by hand.
4. **Hub backups at rest.** Each is named by its own hash, so a check is a
   re-hash, and none is ever made. The mirror is on `teacherslounge`, powered
   off tonight, and `status` reports it behind.
5. **Files no catalogue tracks.** Rip logs and `.accurip` files -- the only
   record that a rip was verified -- CUE sheets, cover images; and on the hub
   13 MP3s more than the catalogue lists, 21 WAV and 14 AIFF files.
6. **Hub audio between sends** is detectable but only when someone looks:
   `relink` by hand, or a send of that very file.

**The hub's audio has no backup of its own.** The daily backup holds the two
databases and the mesh keys. The three speakers each hold a full copy of the
library, with the same hashes recorded -- a backup in fact, but one nobody
verifies, and on two SD cards.

## 4. Measured

- **Hub audio: all 5,848 files match their recorded SHA-256**, none missing;
  117 s, read-only. A nightly check is cheap.
- **Every speaker's catalogue records a hash for every file**: 5,848 of 5,848
  on `bose`, `lp3-wifi` and `smartboardpc`. The record exists to check against.
- **A speaker's recorded size cannot be trusted as a change detector.** `bose`
  records 4,853,042 bytes for *Prime Time* (Alan Parsons Project); the file is
  4,852,600 and its bytes hash exactly to the record. It was rewritten on
  2026-09-27 and the row kept the old size -- `size_bytes` is machine scope, so
  a sync never corrects it.
- **The hub's daily backup runs** (scheduled task *Lempi hub backup*, 03:30):
  2026-10-12, -13 and -14 present.
- **No speaker's audio was re-hashed.** A full pass on `bose` is 48.8 GB from
  an SD card while it plays; the plan's first step measures what it costs.

## 5. Also found: review decisions wait to be applied

The maintainer worked through the hub player's `/review` page and found the
Export page offering nothing to send. Nothing is wrong. A decision on that page is
written to `id_reviews` in the **listener**, a log the player honours itself
`[REQ-LIB-165]`; the catalogue is untouched until `apply_reviews.py` folds the
accepted ones in, which is the console's Flags page, *Apply pending edits to
this library*, two steps by design `[REQ-LIB-175]`. Only then are they
catalogue changes that Export's `credits` diff and a sync carry. Tonight: 42
reassignments waiting, the latest at 23:21; 14 *kept* and 48 *deferred*, which
change no catalogue row. Neither the review page nor Export says any of this,
so the decisions looked lost.

---

Written for the maintainer and whoever takes up the plan. Every count above
was read on 2026-10-14 between 22:50 and 23:30.
