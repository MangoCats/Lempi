# SPEC070: A Link to Nothing

**Design Specification — Tier 2 · written 2026-10-31, at the maintainer's direction · built 2026-10-31: found, said and decided in the sync; removal and repair carried by its commit · extends [SPEC046](SPEC046-star-sync.md) `[SPEC-STAR-110]`, `[SPEC-STAR-085]`**

A catalogue row that names a row the catalogue does not hold. SQLite would
refuse one with foreign keys on; most of the tools that write the catalogue
run without them, so nothing did. This says how the sync finds them, puts
them in front of a person, and carries what that person decides to every node.

> **Related:** [SPEC046](SPEC046-star-sync.md) (the sync this lives in) ·
> [SPEC058](SPEC058-catalogue-patch-by-natural-key.md) (how a removal reaches a
> node) · [SPEC013](SPEC013-vipunen-console.md) (the Sync page) ·
> `tools/catalogue_integrity.py`, `tools/star_sync.py`, `player/core/src/mesh_sync.rs`

---

## 1. What was found

**`[SPEC-LNK-010]` 34 `release_recordings` rows name a recording that is not in
`recordings`**, measured 2026-10-31 on the hub, on the hub's backup from
before the *Highway 61* merge, and on `bose`, `smartboardpc` and `lrhtpc`:
the same 34, from the hub. No other foreign key in the catalogue points at
nothing. They are 30 recordings across 12 releases, and nothing else names
any of them: no passage, artist credit, flavor or work.

| release | rows | of its tracklist |
| :--- | ---: | :--- |
| *Caribou*, five editions (one of them the chosen 1974 Elton John) | 5 | track 9, a second recording beside the chosen one |
| *The Best of Foghat*, two editions | 6 | 3 of 11, 3 of 8 |
| *Org*, *MusicBrainz*, *HTTPS*, *https://*, *https://derpibooru.org/* | 23 | all of each: candidates a search for a URL found |

**`[SPEC-LNK-015]` They were written whole.** Each of the 12 releases is in
`musicbrainz_cache` as a `release-detail`, fetched 2026-08-31 to 09-01, which
only `suggest_release.py` writes; none of the 30 recordings was ever fetched
as one. `store_release_detail` wrote a candidate's tracklist, recording ids
and all, and never the recordings. No tool deletes from `recordings`. *Fixed
2026-10-31:* it now writes each recording beside its tracklist row.

## 2. Where it is found

**`[SPEC-LNK-020]` In the sync, at the merge.** A sync already reads the hub's
whole catalogue, already reaches every node taking part, and already carries
whatever the hub's catalogue becomes to each of them; it is also where a person
already reads a run before it is sent `[SPEC-STAR-070]`. Every foreign key in
the catalogue is checked against `merge/library.db` before each node's copy is
made from it (`catalogue_integrity.find`), so a correction made there is in
the hub's own patch and in every node's.

**`[SPEC-LNK-022]` A finding is one row's dangling references, grouped by what
owns them**: the child's key without the column that dangles --
`release_recordings` by release, so *Org* is one finding of nine rows, not nine.
Its id is a hash of the table, the owner and the keys missing, so the same
situation keeps its id from run to run, and a changed one is raised afresh.

**`[SPEC-LNK-024]` It does not stop the sync.** These rows were harmless for
two months, and a routine sync that waited on them would wait on every node's
edits too. An undecided finding is left as it is for this run, and said: in
the merge's line, in `SUMMARY.md`, and on the Sync page above the items.

## 3. What a person decides

**`[SPEC-LNK-030]` Per finding, one of:**

- **leave** -- kept as it is, and remembered: it is not raised again until it changes.
- **remove** -- the dangling rows go, on the hub and on every node. Offered only
  where a row is a link and nothing more (`release_recordings`,
  `recording_artists`, `recording_relations`); a passage's credit pointing at
  nothing is a fault to repair, not a row to drop.
- **repair from NODE** -- the rows a node holds that the hub lacks
  (`[SPEC-LNK-040]`), added to the hub and so to every node.
- **repair from NODE, with its files** -- the same, and the node's audio that
  names them, brought to the hub (`[SPEC-LNK-050]`).

A decision is kept in `<runs>/integrity.json`, not in the run, so it outlives
the run it was made in. A remove or repair made after the merge leaves the
merge stale, and **Prepare patches** merges again, as a changed verdict does
`[SPEC-STAR-114]`; the gate says so until it has.

**`[SPEC-LNK-034]` It reaches the nodes when they next sync.** The commit
patches the hub first and then each node taking part. A node left alone, not
reached or too old for a catalogue patch keeps the rows until its next sync,
which is made from the hub's catalogue as it then stands -- nothing is queued
for it, because nothing needs to be. The finding says which nodes this run
carries it to and which it does not.

## 4. When a node has what the hub lacks

**`[SPEC-LNK-040]` Every player taking part is asked**, at the snapshot, for its
own rows of the keys the hub is missing -- the `rows` request
`[SPEC-STAR-945]`, given recordings, releases or artists rather than files.
A node that holds one has the link whole: that is said on the finding, before
the person decides, as *bose holds 3 of these 9*.

**`[SPEC-LNK-042]` A remove is held where a node can repair it.** A remove
decided before a node was found holding the rows is not applied: the finding
is raised again with the new offer, since the removal would take from that
node a link that works there. A repair whose node is not in this run, or no
longer holds the rows, is held the same way.

**`[SPEC-LNK-044]` A repair adds; it never replaces.** The node's rows go in
only where the hub has no row of that key, and a child only where its parent is
then held -- a recording's artist credit with the artist, never without. A
repair from a node holding some of the keys repairs those; the rest dangle, and
come up as a new finding.

## 5. Files

**`[SPEC-LNK-050]` A node's files that name a missing recording are listed by
the node**, when asked by recording: a current build widens the `rows` scope
from a recording to the passages that credit it and their files
(`by_recording`, `catalogue_rows: 2`). An older build answers the recordings
alone, and the finding says *which of its files name them, this build cannot
say* rather than nothing.

**`[SPEC-LNK-052]` With its files, the audio comes to the hub at the commit.**
Each file goes under the hub's music root at the place it holds under the
node's -- the same relative path a bundle would give it, the other way --
and is catalogued from the node's own rows: its passages, their credits, its
tags and release. It is copied over ssh before the hub is patched, its size
(and SHA-256, where the node holds one) checked, and a copy that fails stops
the commit before any catalogue is patched. A phone is not asked at all: it
uploads its edits and answers no request, and the finding says *not asked*.

**`[SPEC-LNK-054]` To the rest of the fleet, by the Export page.** The sync
carries catalogues, never audio. Once the hub holds a file, every node without
it is told so in the merge report as any hub file is `[SPEC-STAR-082]`, and
the Export page sends it. The finding says so when it is applied.

## 6. Where it is

**`[SPEC-LNK-060]`** `star_sync.py PLAN integrity` lists the findings and
where each stands; `decide ID leave|remove|repair:NODE|repair-files:NODE`
gives one decision, `decide all leave|remove` every undecided one. The Sync
page shows the same, with the details, above the items for a verdict.

## 7. Open

**`[SPEC-LNK-900]`** A release whose whole tracklist was removed stays, empty:
the five URL-named candidates would. Whether a candidate with no tracks should
go too is a question about candidates, not links, and is not decided here.

**`[SPEC-LNK-910]`** A node's own dangling references, ones the hub does not
share, are not looked for: the hub asks only about the keys it is missing.
None was seen on the three nodes read 2026-10-31.
