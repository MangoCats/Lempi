# SPEC073: A Retired File on a Node

**Design Specification — Tier 2 · written 2026-11-01, at the maintainer's direction · built and run on three nodes 2026-11-01 · answers [SPEC069](SPEC069-a-disc-ripped-again.md) `[SPEC-AGN-920]`, `[SPEC-AGN-045]`**

When a disc ripped again is merged, the hub moves the files it no longer
plays to `_Retired` and removes their rows `[SPEC-AGN-030]`. The next star
sync removes those rows on each node, because a node's catalogue is the hub's
on the files it holds `[SPEC-STAR-080]`. The files themselves stay on the
node's disk, in the album's folder, beside the copy that replaced them, and
nothing catalogues them.

Measured 2026-11-01, read only:

- **`bose`** holds *Highway 61*'s six earlier track files in `Bob Dylan/Highway
  61 Revisited (1987)/`, next to the DAO file. No node's catalogue names any of
  the six.
- ***Wish You Were Here*'s five** retired files are on every node and still
  catalogued there, until the next sync removes their rows.

> **Related:** [SPEC069](SPEC069-a-disc-ripped-again.md) (the merge) ·
> [SPEC072](SPEC072-an-album-from-before-the-cd-import.md) (an inherited copy
> merged) · [SPEC046](SPEC046-star-sync.md) (star sync) ·
> `player/src/star_node.rs`, `player/src/catalogue_window.rs`,
> `player/src/found.rs`, `tools/star_sync.py`, `tools/ingest_folder.py`

---

## 1. Why a stray matters

It costs little room: 52 MB a node for *Wish You Were Here*. What matters is
that it is still in the music folder, and two things put a file from there
back in a catalogue:

- **A scan of the folder.** `ingest_folder.py` walks every folder under the
  one it is given, `_Retired` included. Rebuilding a node from its audio, or
  inducting the hub's music folder whole, catalogues each stray again as a
  second copy of an album the library already plays. That is the mistake
  `[SPEC-AID-025]` exists to prevent.
- **A phone's own scan** (`found.rs`) makes a tags-only passage of any audio
  in a `Music` folder whose bytes match nothing catalogued `[REQ-AND-260]`. A
  file the hub retired matches nothing catalogued, by construction. And
  `in_music_folder` counts `Music/_Retired` as music.

No phone holds a retired file today. A speaker plays only what its catalogue
names, so today's strays are an outage waiting to happen, not one in progress.

## 2. What is retired is what the hub retired

**`[SPEC-RTF-010]` A node retires a file only because the hub retired that
audio, and only when the bytes are the same.** It is never inferred from a
file being uncatalogued.

Absence does not identify a stray. A bundle copies audio before its import
binds it (the import's `awaiting` count). A phone's card can be out. And a
person may have put a file in a node's folder. The house rule is that an
empty answer is not evidence (`CLAUDE.md` §6, [GOV002](../GOV002-sources-of-truth.md)),
so the evidence here is positive: the hub's own record, matched by
`audio_md5` and confirmed by `sha256`.

**`[SPEC-RTF-015]` The hub keeps that record: one `ingest_decisions` row
per retired audio file,** with stage `retire`, outcome `retired`, keyed by
its `audio_md5`, and dated when the merge ran. Its `detail` holds:

- `sha256` and `size_bytes`, the bytes;
- `rel_path`, its place under the music folder
  (`Pink Floyd/Wish You Were Here/Have a Cigar.mp3`);
- `retired_to`, where the hub moved it;
- `replaced_by`, the audio that plays it now;
- `why` (`merge`).

*Built 2026-11-01* (`ingest_cd.record_retired`, read back by
`ingest_cd.retired_files`). The merge writes it in its own transaction,
while the files' rows still say what they were, so a merge that fails
leaves no row naming files that are still in place. A file the hub
catalogues again is not retired, whatever was once said.

*Designed as a table of its own, and changed when it was built.* The
catalogue merge carries every table the hub holds to each node
(`star_merge.build_catalogue`). And a patch never drops a table
(`star_patch.make_values` refuses a baseline that holds one its target
lacks). So a table kept on the hub alone could be neither kept home nor
left out. The per-file decision log was already the hub's record of what was
done to each file.

*Backfilled 2026-11-01* by `tools/backfill_retired.py`: 11 files, *Highway
61*'s six and *Wish You Were Here*'s five, each with its `sha256` and size.
They were read from the copies taken before each merge,
`data/backups/before-highway61-merge-2026-10-30` and
`data/backups/before-wywh-swap-2026-11-01`; the swap that copy preceded
changed passages, not files. Each merge's `merged` decision named where its
files went, and none was missing.

## 3. The node moves its own file

**`[SPEC-RTF-020]` The node retires a file in the commit that removes its
row.** Its player does the move, not the hub over SSH. The signed commit is
already where the node's player applies the hub's patch to its own catalogue
`[SPEC-NSH-080]`. It already opens a read-only partition itself, with
`catalogue_window` `[SPEC-STAR-104]`. On `bose` that partition holds the audio
as well as the catalogue, so one window covers both. SSH is what the signed
transport replaced [SPEC054](SPEC054-mesh-without-ssh.md), and a node reached
only that way would be left out.

The hub's patch gains a `retire` section. It is written by `star_sync.py
patch` for each `files` row the catalogue patch removes whose `audio_md5` the
hub has retired (`ingest_cd.retired_files`). Each entry holds:

- the `audio_md5` and `sha256`;
- the path the node's own row gave it, from the baseline the patch is cut
  against;
- the hub's `rel_path` and `retired_at`.

*Hub side built 2026-11-01* (`tools/retired.py`, `star_sync.retire_lines`;
the node's half is §7 step 4). The list is a file of its own,
`patches/<node>/retire.json`, sent with the commit as `retire`, not a section
of the catalogue patch. A node whose row an earlier run already removed is
still told: its path is taken from beside the disc that replaced it, since a
merge files the disc in the album's folder. That is how *Highway 61*'s six
are reached. A file settled on a node (`moved`, `not there`, `bytes differ`
in `sync/retired.json`) is not sent again.

Measured read-only the same day, against what each node was last sent:
`bose`, `lempi02w`, `lp3-wifi`, `lrhtpc` and `smartboardpc` would each be
told 11 files. *Wish You Were Here*'s five are found by their rows, *Highway
61*'s six beside their replacement, and every node holds both replacements.
No player reports `retire_files` yet, so the next run names all 11 under each
node as left on its disk.

**`[SPEC-RTF-025]` The node moves the file only when all of these hold:**

1. **The catalogue part has committed, and no row names the audio any more.**
   A file moved while still catalogued reads as *missing* in the node's
   integrity report, and that report makes *Send what's missing* send it
   again `[GDE-INT-150]`.
2. **The file is where its row said, and its bytes are the hub's:** the same
   size, then the same `sha256`.
3. **The destination is on the same file system,** so the move is a rename:
   all of the file or none of it, and never a copy.

The destination is the hub's layout, under the node's own music folder:
`<root>/_Retired/<album folder>/<retired_at date>/<name>`. `<root>` is the
node's path with the hub's `rel_path` taken off its end. Nothing is ever
written over: a name already taken gets ` (2)`, as `ingest_cd.retire_dir`
does on the hub. A file is moved, never deleted.

**`[SPEC-RTF-030]` Anything else leaves the file, and says so.** The commit's
result counts each outcome the node found:

- `moved`;
- `still catalogued`, waiting for a later run;
- `not there`, already gone;
- `bytes differ`, someone else's file at that path;
- `elsewhere`, a path that does not end in `rel_path`;
- `refused`, any error.

None of these fails the commit: the catalogue has landed, and a stray is
something to tidy, not something lost. A count other than `moved` is said, by
file, on the Sync page. The node reads back what it moved: the source gone,
the destination there, the `sha256` again. That makes `moved` a measurement,
not an intention `[GDE-DEP-070]`.

**`[SPEC-RTF-035]` A commit's reversal puts the files back.** The node keeps
each commit's inverse three deep `[SPEC-NSH-080]`. It gains the moves, newest
first, so undoing a commit restores the rows and then the files the rows
named `[SPEC-NSH-085]`. The hub records each node's moves in
`sync/retired.json`, an entry being `{node, run, audio_md5, from, to}`, beside
the bundle ledger `[SPEC-STAR-945]`. A later run doesn't ask about a file
already moved.

*The node's half built 2026-11-01, deployed to three nodes the same day (§7)* (`player/src/retire.rs`,
called from `star_node.rs`):

- **Snapshot.** It says `retire_files: 1`.
- **Rehearsal.** It answers `to move` for each file it would move. It moves
  and hashes nothing, and it cannot ask whether a row still names the audio,
  since the patch that removes the row has not run yet.
- **Commit.** Files move only after both halves of the commit have landed,
  inside the window the commit opens. A file to retire opens that window even
  when the run has no catalogue rows, because on `bose` the music is on the
  catalogue's partition.
- **Inverse.** The commit's inverse keeps the moves as `moved`, and
  `retire::undo` puts them back, never over a file already there. Like the
  rows' inverse, it is applied by hand: no step on the node reads an inverse
  back by itself.
- **MPD.** `_Retired/.mpdignore` is written (`*`) when the folder is first
  made.
- **The date.** The hub's date becomes a folder name of digits and dashes
  only, so it cannot climb out of `_Retired`.

Tested in `retire.rs`, and through `handle` on a read-only catalogue
(`a_commit_retires_the_files_its_patch_uncatalogues`).

## 4. A `_Retired` folder is never catalogued

**`[SPEC-RTF-040]` Every scan of a music folder skips any `_Retired` folder
within it, at any depth.** That covers three scanners:

- **`ingest_folder.py`**, which skips none today;
- **the phone's `found.rs`**, where `in_music_folder` is answered "no" for a
  path through `_Retired`;
- **`verify_library.py`**, which already skips the music folder's own
  `_Retired` and now skips one at any depth.

`.mpdignore` is created in each node's `_Retired` folder when that folder is
created, so `bose`'s MPD does not index the retired files. Its player plays
by catalogue, so this is tidiness, not a fix.

*Built 2026-11-01, first and on its own,* except `.mpdignore`, which goes
with the move (§3). It was the one live hazard: until then, an induct of the
hub's music folder would have catalogued *Highway 61*'s six and *Wish You
Were Here*'s five again. Built in these places:

- `ingest_folder.walk_music`, which the console's disk view also uses;
- `verify_library.strays`, which counts the files as retired at any depth;
- `relink::walk`, with its `RETIRED` constant;
- `found::in_music_folder`.

Each has a test.

## 5. The replacement before the original

**`[SPEC-RTF-050]` A run that would remove a retired file from a node that
does not yet hold its replacement says so, and does not wait.** The merge
decision names the DAO that replaced each file. If a node's summary lacks
that audio, `patch` lists the album under the node with the line *"no copy
of this album there once this lands: Send what's missing first"*, and the
Sync page offers the send before **Commit**.

Holding the removal back was weighed and rejected: it keeps a file the hub
judged worse (on *Wish You Were Here*, a glitched one) in rotation for as
long as the send is put off. *The maintainer's to change.*

## 6. A player that cannot

**`[SPEC-RTF-060]` The snapshot says whether a player retires files**
(`retire_files: 1`), as it says `catalogue_window`. A player that cannot is
sent its catalogue patch without the `retire` section, and the hub warns
loudly (`OLD PLAYER: retired files left on disk`) and lists the files. A
player built before this ignores a section it does not know, but it is not
sent one. That way a result never reads as if a move might have happened.

## 7. Order of building

1. Scanners skip `_Retired` `[SPEC-RTF-040]`: Python and the phone, each
   with a test, deployed to the phones.
2. The hub's record `[SPEC-RTF-015]`, written by `record_retired`, and its
   backfill, compared by count against both merges' decisions.
3. The `retire` section `[SPEC-RTF-020]`: `star_sync.py patch`, and
   `test_star_sync_flow.py`'s fake fleet answering it.
4. The player `[SPEC-RTF-025]`..`[SPEC-RTF-035]`: `star_node.rs`, inside the
   commit's window, after the listener half. Tested on a split pair, and
   with a read-only partition. Then deployed through
   `build/deploy-appliance.sh`, durable copy verified `[GDE-DEP-070]`.
5. A run. `bose`, `smartboardpc` and `lrhtpc` first: they hold both merges'
   files and have their replacements. Each node's `_Retired` is read back
   over its own report, not from the hub's ledger.

*Run 2026-11-01, `20261101T1757Z`.* `954422c` went to `bose` by
`build/deploy-appliance.sh`, to `smartboardpc` by
`build/update-source-host.sh`, and to `lrhtpc` by `LRHTPC/deploy-lrhtpc.sh`.
Each answered as that build: `bose` from its durable copy, the other two
from the running player. All three were playing before and after.

The sync then went from the console with those three. The rehearsal said
`11 to move` on each, and the commit `11 moved`. Read back on each node over
SSH, not from the hub:

- `_Retired` holds 11 audio files, each matching the hub's SHA-256;
- none is left in the album folders, which hold the two DAO files, their
  covers, and on `lrhtpc` a sheet;
- `_Retired/.mpdignore` reads `*`;
- the catalogue names none of the 11, and nothing under `_Retired`;
- `bose`'s `/srv/library` is mounted `ro` again.

`lempi02w` and `lp3-wifi` were left out of this run, and still hold the 11
in their album folders until they run this build and take part in a sync;
`teacherslounge` did not answer.

## 8. Open

**`[SPEC-RTF-900]`** Phones. A phone's music is in shared storage, which it
reaches through MediaStore, and a move there is a different operation, with
its own permission. Until it is designed, a phone is sent no `retire` section
(`[SPEC-RTF-060]` covers it). Step 1 already keeps a phone's scan from
cataloguing a stray.

**`[SPEC-RTF-910]`** A file the hub removes for any reason other than a
merge (deleted from the hub, or a duplicate cleared) leaves its node copy as
a stray too. It is out of scope until a tool records it as retired
(`ingest_cd.record_retired`), and then the same mechanism applies.

---

**Traceability:** `[SPEC-RTF-010..060]`, `[SPEC-RTF-900..910]` · answers `[SPEC-AGN-920]`, `[SPEC-AGN-045]` of [SPEC069](SPEC069-a-disc-ripped-again.md) · builds on `[SPEC-STAR-080]`, `[SPEC-STAR-104]`, `[SPEC-STAR-945]`, `[SPEC-NSH-080]`, `[SPEC-NSH-085]`, `[GDE-INT-150]`, `[REQ-AND-260]`
