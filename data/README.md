# `data/`

## Where the live pair lives

**The arrangement, as the maintainer describes it:** the Vipunen primary is in
this directory on **the Windows desktop**, `GMKtec` (Windows 11, where the 44 GB
of music is under `%USERPROFILE%\Music`). This pair is the hub of star sync
`[SPEC-STAR-010]`. **`teacherslounge`** (Ubuntu 22.04) validates the same tools
under Linux and, like every other node, keeps its own pair in `~/lempi-data/`
with its **own** listener history: a spoke, not a mirror of this one. Each
appliance likewise keeps its own catalogue copy and its own listener history.

**2026-09-26: the primary was lost, and rebuilt.** It lived in `data/` of the
previous repository's checkout on this PC, a sibling of `Dev\Lempi` whose path
is recorded in `[GDE-NAM-040]`. This repository was seeded on 2026-09-21
without those untracked files, and that checkout no longer exists; it is not in
the Recycle Bin. Its sidecars went with it: `library.console.db` and
`library.idchecks.db`, the 8,330 fingerprints described below. The copies that
survived, read that day:

| copy | files | passages | recordings | `recording_works` | plays (newest) |
| :--- | ---: | ---: | ---: | ---: | :--- |
| a scratch pair on this PC, 2026-09-10 | 5,709 | 16,661 | 8,185 | — | 37,763 (09-08) |
| `teacherslounge:~/lempi-data`, copied 09-21 from its own 09-12 data | 5,709 | 16,661 | **8,224** | — | 37,762 (09-06) |
| `bose`, `lp3-wifi` | 5,709 | 16,661 | 8,185 | 7,154 | each its own |
| `lempi02w` | 5,709 | 16,409 | 8,191 | 7,188 | its own |

None was a superset of the others, so the hub was rebuilt by merging all of
them, not by choosing one: [SPEC046](../docs/spec/SPEC046-star-sync.md), by
`tools/star_merge.py`.

**The pair here is `merge-full-5`**, from `data/recovery/2026-09-26/`, promoted
on 2026-09-26 at the maintainer's word. It is the `merge-full-4` the maintainer
reviewed, table for table, except that each node's plays are no longer merged
into the hub's `[REQ-PD-113]`: the hub holds the desktop's own 37,763. Its
`REVIEW.md` shows the recent edits by name, and `report.md` every decision.
Every node's snapshot is in the folder beside it, hash-verified against the
node and read-only.

**Since promotion, `files.sha256` is filled** `[REQ-AND-960]`: every one of the
5,709 files hashed by `tools/add_byte_hashes.py` on 2026-09-26, in 111 s, all
distinct, spot-checked against the files. That column is the one difference
from `merge-full-5`, and no node has it yet.

**Since 2026-09-27 04:15 UTC the pair is keyed by Symphonia's reading**
`[SPEC-RLK-152]`. `tools/rekey_identity.py` hashed all 5,709 files in 212 s:
5,648 kept their `audio_md5`, 60 changed (353 rows across four tables), and one
could not be read (*Ammonia Avenue*'s "Prime Time", an MP3 in a WAV container).
That file was fixed at 12:53 UTC, its wrapper removed (`[SPEC-RLK-152]` says how),
and a second run re-keyed it: every file now records `symphonia@0.5.5`, and the
61 retired keys are in `audio_md5_aliases` `[SPEC-RLK-155]`. Both halves as they
were before each run are in `backups/pre-rekey-20260927T0415Z/` and
`backups/pre-rekey-20260927T1257Z/`, the original file in
`backups/prime-time-original-20260927/`, and the runs' output in
`rekey-2026-09-27.log` and `rekey-2026-09-27-prime.log`. **Every node has both since
star-sync run `20260927T1303Z`**, committed and verified on all five, and read
back from each node's disk: 5,709 of 5,709 files record `symphonia@0.5.5`, and
61 aliases. One accepted wart: each node's `files.size_bytes` for Prime Time is
still the old file's, 442 bytes too high. Size is a per-node column the sync
takes from the copy it last sent, and no node's player reads it.

**`mesh/`, once a mesh is created, holds the mesh's keys and roster** `[SPEC-MTR-110]`.
It is untracked like the pair. Losing `mesh.key` means enrolling every member
again, so the daily backup keeps it as an SQLite archive beside the pair's
objects (`sqlite3 FILE -Ax` extracts it). Not yet created as of 2026-09-27.

**Since 2026-09-27 the catalogue holds the covers found on disk** `[SPEC-COV-020]`.
`tools/induct_covers.py --write` took 384 release covers into `cover_art` and 116
file covers into the new `file_art`. The catalogue then held 1,256 and 116; the
output is in `induct-covers-2026-09-27.log`. The catalogue as it was before
is in `backups/pre-covers-20260927/library.db`, sha256 `00ba3ad29de3cc6c…`.

**Each node received its own copy by patch** `[SPEC-STAR-080]`, and keeps the
pair it had before in `pre-star-2026-09-26/` beside its listener. Which nodes,
and what was verified, is in [FLEET001](../fleet/FLEET001-the-fleet.md)
`[FLT-DAT-020]`.

## What the pair is

**`library.db` and `listener.db` are the live pair** — the catalogue half and
the listener half of one database, split per `[IMPL-DBSPLIT-025]` and matching
the shape all three appliances run. Every tool and doc example in this repo means
this pair when it says "the library", and every tool takes **either** path as
its single argument: `tools/lempi_db.py` finds the other half beside it and
attaches it.

`lempi_new.db` was the whole pre-split database and is **gone** as of
2026-09-11, along with its dated backups. It was superseded on 2026-09-11
00:01 and had fallen behind on schema as well as data — it never had
`listener_characteristics`. While it existed, a tool pointed at it ran
perfectly and wrote to a file nothing read; deleting it makes that mistake
fail immediately instead `[PI-PRE-098]`.

Sidecars are derived from the database path, so they follow the split:
`library.console.db` (console state) and `library.idchecks.db` (8,330
Chromaprint fingerprints and their AcoustID verdicts — carried over from the
pre-split sidecar, 99.7% of its rows still keyed to live passages).

**Both sidecars went with the lost primary.** Measured 2026-09-26 on the
rebuilt pair:
- The console runs against it (`tools/launch_vipunen_console.bat`, whose
  path was corrected that day: it still named `lempi_new.db`). Its totals
  match the database: 5,709 files, 16,661 passages, 8,224 recordings.
- It made a new, empty `library.console.db`. Its peer list was re-entered
  for `lempi02w`, `bose` and `lp3-wifi`, and all three answer.
- `library.idchecks.db` is not rebuilt. The verdicts survive in the
  catalogue's own `id_checks` table (8,273), which is what the console
  counts. Only the raw fingerprints are gone, and they matter only when a
  passage is fingerprinted again. Rebuilding them all is 8,330 AcoustID
  lookups; `tools/fingerprint_ids.py` makes them one passage at a time as
  it is needed.

**Backups and routine syncs, since 2026-09-26** `[SPEC-STAR-085..087]`:
- `data/backups/` holds a daily copy of the pair, stored by content hash,
  mirrored to `teacherslounge:~/lempi-hub-backups/`. It is run by the
  Windows task "Lempi hub backup" at 03:30.
- `data/sync/` holds each sync's run folder, and `state.json`, which
  records what each node was last sent.
- `python tools/star_sync.py fleet/star-plan.json status` shows both.

`flavor.db`, `flavor-sample.db`, `sample-library.db` and
`sample-library.console.db` are fixtures and extraction data, not copies of
the live pair.
