# SPEC075: Permanent Identity, One Catalogue and a Movable Hub

**Design Specification — Tier 2 · written 2026-11-07, from the maintainer's decisions that day · §3's first rule and §8's first step built 2026-11-07, not yet deployed; the rest is proposed and has not been prototyped**

Every node holds the household's music under its own passage numbers, and a number means nothing on another node `[SPEC-DF-035]`. That rule has lived in prose since it was written. Each new path across nodes has had to remember it, and the ones that forgot produced plausible wrong answers rather than errors. This design removes the need to remember: a passage gets a permanent identity, every node holds the same catalogue, and the hub becomes a role any capable node can take.

> **Related:** [SPEC006](SPEC006-data-flow-and-portability.md) `[SPEC-DF-035]` (a number may not cross an installation) · [SPEC058](SPEC058-catalogue-patch-by-natural-key.md) (today's patch) · [SPEC046](SPEC046-star-sync.md) (today's sync, which this replaces in steps) · [SPEC049](SPEC049-mesh-membership-and-trust.md) `[SPEC-MTR-070]`, `[SPEC-MTR-110]` (trust, which §6 changes) · [SPEC035](SPEC035-mesh-library-sync.md) (the mesh question this settles) · [GUIDE010](../GUIDE010-echo-node-capabilities.md) `[GDE-ECHO-420]` (what a follower does with music it lacks)

---

## 1. What was found

**`[SPEC-UID-001]` The same number is different music, measured 2026-11-07** from each node's newest snapshot, hub numbers read on each node (22,789 hub passages):

| node | same passage | a different file |
| :--- | ---: | ---: |
| `bose` | 16,456 | 5,462 |
| `lempi02w` | 16,448 | 5,236 |
| `smartboardpc` | 16,456 | 4,932 |
| `lp3-wifi` | 16,457 | 4,922 |
| `teacherslounge` | 16,443 | 4,994 |
| `lrhtpc` | 20,576 | 1,360 |

On 2026-10-05 the difference was 49 files `[SPEC-NKP-010]`. Between nodes, `bose`'s numbers name another file for 664 passages on `smartboardpc` and 2,290 on `lp3-wifi`.

**`[SPEC-UID-002]` It reached the speakers.** Echo sent the leader's number and the follower looked it up in its own catalogue, which succeeds whatever it finds. That day `bose` and `smartboardpc` both reported passage 21159 and the same queue: Bob Dylan's *I Want You* on one and Nirvana's *Serve the Servants* on the other.

**`[SPEC-UID-003]` Five causes, of which the numbers are only the first:**

1. A local row number looks like an identity, and nothing mechanical stops it travelling.
2. A passage's portable name, `(audio_md5, kind, start_ms, end_ms)`, includes its span. Every boundary edit changes it, which is why patches need `was`/`now` pairing `[SPEC-NKP-045]`, `[SPEC-DF-102]` needs a pre-edit span, and `passage_remap` falls back on position in the file.
3. Facts about music, one machine's holdings and hub-only caches share one catalogue, so each node holds its own filtered copy `[SPEC-STAR-082]` and needs its own baseline and patch.
4. Bundles write catalogue rows outside the sync, numbered in arrival order `[SPEC-STAR-940]`. Every irregular import widens the gap.
5. The sync compares whole database states rather than replaying changes. A run for one node kept 5.6 GB, and `data/sync` held 72 GB that day.

Translation has been patched at each boundary as it failed: `[SPEC-STAR-049]`, `[SPEC-STAR-051]`, `[SPEC-STAR-052]`, `[SPEC-STAR-945]`, `[SPEC-STAR-946]`, `[SPEC-NKP-046]`. Each is correct, and together they are about 7,600 lines and two repair tools.

## 2. What the maintainer decided

**`[SPEC-UID-005]` Decided 2026-11-07:**

- **Nodes will ingest music on their own,** so identity needs no coordination, and rare hash collisions must be handled rather than assumed away.
- **The hub is not the only possible writer.** Catastrophic loss of the hub must not cost the fleet its function. In practice one hub may write for long periods, but another node must be able to step up when needed or wanted.
- **Speakers hold the full catalogue,** including music they do not have.
- **Ongoing stability comes first.** Migration must be possible and reliable, but the design is judged on usage and on new music arriving at irregular intervals, on some nodes and not others.

## 3. Identity

**`[SPEC-UID-010]` A local number never leaves its database; what crosses is what the thing is.** Built first for echo: a leader names each passage it announces, and a follower turns every number into its own on arrival, so nothing after that point handles a foreign one. A number with no local match becomes one that names nothing, and the node reports it and plays its own queue `[GDE-ECHO-420]`. Until `[SPEC-UID-030]` exists, the name is `(audio_md5, kind, start_ms)`, with `end_ms` only breaking a tie, because a sync that has reached one node and not another leaves the same passage ending in two places. A leader too old to name anything is followed by number, with a warning, so the fleet can be updated one node at a time.

**`[SPEC-UID-020]` The fleet's cross-node paths are tested over catalogues numbered unlike each other** (`tools/test_numbering_fleet.py`, and echo's tests in `player/src/echo_client.rs`). One node gives the same numbers to other music, which is the dangerous case, since a stray number still finds a passage. Another uses numbers no other node has. A new cross-node path joins this test or does not ship.

**`[SPEC-UID-030]` A passage has a `passage_uid`, set once when it is created and never recalculated:** the first 128 bits of SHA-256 over its birth key, `(audio_md5, kind, start_ms, end_ms, salt)`, with salt 0 unless `[SPEC-UID-052]` applies. Being derived from the content, two nodes making the same cut get the same identity without talking to each other. Being stored rather than recomputed, it survives boundary edits, album timing and audio re-keys `[SPEC-RLK-155]`. The birth key is kept beside it, which is what makes a collision detectable.

**`[SPEC-UID-035]` Local numbers remain, for joins inside one database and nothing else.** Wire messages, URLs, listener tables, plan files and logs meant for people use the uid. Keeping the integer avoids rewriting the engine's and Director's in-memory structures; replacing it outright is possible later and would gain little.

**`[SPEC-UID-040]` A file remains its `audio_md5`,** which is already content-derived, with `audio_md5_aliases` for re-keys. Other references stay as they are: recordings by MBID, flavor and preferences by recording or artist.

**`[SPEC-UID-050]` Collisions, each detected and each with a rule:**

| what happens | how it is seen | what is done |
| :--- | :--- | :--- |
| `[SPEC-UID-052]` Two different birth keys hash to one uid. About 1.5 × 10⁻²⁷ at a million passages | same uid, different birth key | The earlier-stamped keeps it. The other takes salt + 1, and `passage_lineage` carries what named it |
| `[SPEC-UID-054]` A passage was edited, and a new cut is born where it was born | same birth key, the uid live on this node | Salt + 1: they are different passages |
| `[SPEC-UID-056]` The cut is made again after being retired | same birth key, a tombstone | The uid revives, so plays and reviews reattach |
| `[SPEC-UID-058]` Two nodes make the same cut with different facts | same uid, different values | One passage. The authority's values stand, and the proposal's differences become a review item, never dropped silently |
| `[SPEC-UID-060]` Two different recordings share an `audio_md5` | same hash, but different decoded frames, rate, channels or second audio digest | Quarantined: neither is linked to the other's facts, the later takes a salted key, and a person is told |

**`[SPEC-UID-061]` `files` gains a second digest of the decoded audio**, computed during the same decode as `audio_md5`, to make `[SPEC-UID-060]` decidable. `sha256` cannot do it, because a re-tagged copy of the same audio has different bytes `[REQ-AND-270]`. Identical audio in two encodings with identical samples stays one file, by design, with two holdings.

**`[SPEC-UID-065]` A re-cut says what it replaced.** `passage_lineage(old_uid, new_uid, why, stamp)` records each split, merge and salt, so plays, reviews and queues follow without a positional guess.

**`[SPEC-UID-070]` The mistake is made hard to write.** In Rust, `LocalPassageId` is a type with no `Serialize`, so a number in a wire struct fails to compile. A check beside `tools/check_docs.py` refuses `passage_id` in a new wire struct or a new listener column, with an allow-list that can only shrink.

## 4. One catalogue, and holdings apart from it

**`[SPEC-UID-100]` What a machine holds moves out of the catalogue.** A per-node `holdings` table, keyed by `audio_md5`, takes `path`, `size_bytes`, `mtime`, `first_seen` and `last_seen`, the columns `[SPEC-NKP-050]` already treats as machine-scope, plus how the file last read back. A node may play what it holds. A view named `files` keeps the 26 tools and 13 Rust files that write these tables working during migration.

**`[SPEC-UID-110]` Every speaker holds the same catalogue**, including facts about music it lacks, so a node's state is one comparison: this generation, this digest. `[SPEC-STAR-082]`'s filtering, its left-out list and the `rows` step `[SPEC-STAR-945]` go.

**`[SPEC-UID-115]` Hub working data leaves the replicated catalogue where nothing on a speaker reads it.** By size on 2026-11-07, of 1,422 MB: `musicbrainz_cache` 528 MB, `lowlevel_cache` 263 MB, `identification_cache` 39 MB, `ingest_decisions` 29 MB. The player's code never names the first, and names the second only in bundle import. Each table's readers are to be confirmed before it moves. About 560 MB would remain.

**`[SPEC-UID-120]` A bundle carries audio.** Facts arrive by replication, and the two no longer have an order. A file arriving first waits in holdings, marked as unidentified, and attaches by `audio_md5` when its facts come. Facts arriving first simply wait for the file.

**`[SPEC-UID-130]` Every node's holdings are reported to the authority**, as data rather than facts about music. That answers who can supply a file, and **how many copies of each file exist**: a file on one machine alone is shown as at risk. On 2026-11-07 that was 12 files, 0.06 GB, on the hub alone.

## 5. Replication by numbered change

**`[SPEC-UID-200]` Every change to the catalogue is stamped by triggers on the authority**, so no tool can bypass it, including tools not yet written. `row_stamp = (epoch << 40) | sequence`, which stays exact in JavaScript. A removal leaves a stamped tombstone.

**`[SPEC-UID-210]` A node asks for everything after its stamp** and applies it in one transaction. The sync can resume, repeats do no harm, and no baseline is stored. A gap is refused by name. A generation's digest proves two nodes hold the same catalogue, which is the check `[SPEC-STAR-070]` makes today by rehearsal.

**`[SPEC-UID-215]` A node too far behind, or new, takes a signed snapshot and catches up from its stamp.** Tombstones are kept for longer than any node is expected to be away, and a node away for longer takes a snapshot.

**`[SPEC-UID-220]` Replicated is not published.** The maintainer holds changes on the hub until they have settled, and that stays: the authority publishes a stamp, and speakers apply up to it. Standbys `[SPEC-UID-340]` hold everything, published or not, or a hub's loss would lose what had not settled.

**`[SPEC-UID-230]` A node's own changes go up as proposals.** New music, boundary edits and identifications are written locally under their own uids, usable at once and with no hub reachable, and queued. The authority accepts them into the stream, and the node's overlay row retires when its own change comes back. Listener edits keep the rules of `[SPEC-STAR-030]`, under uids instead of translation.

## 6. A hub that can move

**`[SPEC-UID-300]` The hub is two roles, and they separate.** The **authority** stamps the catalogue, publishes, signs and merges: little work, and possible on any node that stays up. The **workbench** runs Vipunen: extraction, identification, the console. Ingest on any workbench becomes proposals, so a Pi may hold authority while a desktop rips.

**`[SPEC-UID-310]` One authority at a time, in numbered epochs.** Not a multi-writer mesh: `[SPEC035]`'s conflict rules are a large failure surface for a household that writes from one place nearly always. This settles that document's open question in favour of a star whose centre can move.

**`[SPEC-UID-320]` The roster names successors in advance.** `[SPEC-MTR-110]` makes a mesh its hub's key, and losing it means enrolling every member again, and `[SPEC-MTR-070]` moves the hub only by an act at the old hub. Neither survives a lost hub. So the roster, signed by the current authority, lists successor node keys. Members pin them, and accept a higher-epoch roster signed by a named successor. No root key is copied. A roster naming no successor is reported as a risk. Hub requests `[SPEC-NSH-020]` are then verified against the authority named in the roster.

**`[SPEC-UID-330]` Taking over is a deliberate act, not an election.** One command or button on the successor. Nothing happens automatically, because a hub going quiet is routine here: `bose`'s Wi-Fi has died silently for hours `[FLT-ISS-010]`, and the desktop is sometimes off. The fleet may say the hub has been silent and who could step up.

**`[SPEC-UID-335]` Before taking over, it asks every reachable node how far each has got**, and says if another holds more, or if nodes cannot be reached. It starts epoch + 1 from its own position, recorded in `catalogue_epochs` as the fork point.

**`[SPEC-UID-340]` A standby keeps up continuously.** At least one named successor pulls every change as it happens, unpublished ones included, along with the listener half, and its lag is shown. A sync started by hand every few days would otherwise mean losing days of work with the hub.

**`[SPEC-UID-345]` `teacherslounge` is the first standby**, decided by the maintainer 2026-11-07. It already holds the mirror. Vipunen has not been run there `[TL-OPN-010]`, so step 5a has to prove that half too, and a machine that is sometimes powered off makes the lag of `[SPEC-UID-340]` a number to watch.

**`[SPEC-UID-350]` A returning hub steps down and loses nothing.** It sees a higher epoch and stops writing. Any row it or a node holds stamped past the fork point in the old epoch goes back as a proposal, through `[SPEC-UID-230]`, and the conflict rules of `[SPEC-UID-050]` settle them. This is what makes a mistaken takeover recoverable.

**`[SPEC-UID-360]` What each kind of loss costs, by design:**

| lost | speakers | catalogue | music | listener's history |
| :--- | :--- | :--- | :--- | :--- |
| the hub, with a standby | play on | whole, from the standby | any file held elsewhere, and the at-risk list says which is not | that node's plays from its last backup `[REQ-PD-113]` |
| the hub, no standby | play on | as of the last sync | as above | as above |
| a speaker | others play on | whole on the rest | whatever it alone held | that node's plays from its last backup |

**`[SPEC-UID-370]` A node's plays stay on that node `[REQ-PD-113]`, and so need their own protection.** The hub's 41,198 plays are the only irreplaceable data in the system `[SPEC-DF-010]`. The daily mirror covers the hub's today. Each node's plays reaching a second machine is a backup, not a merge, and is to be decided in §9.

## 7. How it handles irregular use

| what happens | what follows |
| :--- | :--- |
| New music sent to `bose` only | Every node learns the facts; only `bose` can play it |
| A node away for three weeks | Catches up from its stamp, or takes a snapshot |
| A node rips while travelling | Plays at once; proposed on return; the same uid if the hub made the same cut |
| A phone finds unknown music | Its own uid, tags-only `[REQ-AND-260]`, and later joined by `audio_md5` |
| A follower lacks the leader's track | Says so, and plays its own queue |
| Boundary edit, or album timing | Plain updates. The uid does not change |
| Re-segmenting a file | New uids, with lineage from the old |
| The hub is destroyed | Successor takes over. Speakers never stopped |
| The hub was only off | It returns, steps down, and proposes what it had not sent |

## 8. Migration

Each step can be used alone, and reversed. Nothing requires every node to change at once.

| step | what | how it is proved | how it is undone |
| :--- | :--- | :--- | :--- |
| 1 | `[SPEC-UID-010]`, `[SPEC-UID-020]` | tests fail without it; a follower plays the leader's song | redeploy the previous player |
| 2 | Add `passage_uid`, birth key and second digest, by today's patch `[SPEC-NKP-040]` | every node agrees uid for uid; digest mismatches listed | columns are ignored |
| 3 | Listener tables, messages and URLs take uids, old numbers still accepted; nodes report a capability, as `[SPEC-NKP-075]` | merge output equal with and without translation | old columns remain until step 6 |
| 4 | `holdings` split, behind the `files` view | full test suite; each node's playable set unchanged | the view can be undone from holdings |
| 5 | Numbered changes and proposals in shadow beside the sync, compared on real runs | the same result as today's patches over several runs | stop the shadow |
| 5a | Standby and successor roster; a takeover rehearsed on real nodes, including the old hub returning | the fleet plays and syncs under the new authority, and back | move back; the roster keeps the epoch |
| 6 | Change over, and retire remap, unmap, `pair_moved`, adoption and baselines | as above, then with the old paths removed | the last snapshot and the previous player |

**`[SPEC-UID-400]` Album timing should wait for step 2** if it is applied at all. Today, its 8,634 changed spans would be 8,634 identity changes, on every node, through the positional fallback.

**`[SPEC-UID-410]` Phones move last.** They update on their own schedule, so each step above must work beside an older phone.

## 9. Open

- **Phones and the full catalogue.** `[SPEC-UID-110]` was decided for speakers. About 560 MB may be too much for a phone, and a phone can filter the same stream by uid. To be measured.
- **Whether a workbench needs the AcoustID key and MusicBrainz cache copied**, or takes them from the mirror when needed.
- **Each node's plays** reaching a second machine `[SPEC-UID-370]`.
- **How long tombstones are kept** `[SPEC-UID-215]`, against the longest time a node has been away.

---

**Traceability:** `[SPEC-UID-001..410]` · applies `[SPEC-DF-035]` · changes `[SPEC-MTR-070]`, `[SPEC-MTR-110]`, `[SPEC-STAR-082]`, `[SPEC-STAR-945]` · answers the open question of `[SPEC035]` · proves `[GDE-ECHO-420]` as written
