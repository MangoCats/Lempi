# IMPL002: Reviewing Questionable Recording Ids

**Implementation Guide — how to work the review queue `[REQ-LIB-165]`**

> **Related:** [SPEC010](spec/SPEC010-identification-review.md) for why it works this way · [REQ002 §4](spec/REQ002-functional-requirements.md)

---

## Before you start

Run the **current** binary. A build from before 2026-08-15 has neither the
`id_reviews` table nor the `release_recordings(mbid)` index, and without the
index `/review` takes minutes instead of half a second.

```
cargo build --release --manifest-path player/Cargo.toml
player/target/release/lempi --listener data/library.db
```

Both migrations run at startup and take under a second. To confirm:

```
sqlite3 data/library.db ".tables id_reviews"
```

---

## 1. Open the queue

`http://localhost:5720/browse` → **review ids**, or go straight to
`http://localhost:5720/review`.

You should see roughly **114 cards**. If it says the pass has never been run,
the findings have not been merged — see §5.

## 2. Read the chips

One per grade, each with its own count. The first four are on by default:

| Chip | Means | Count |
| :--- | :--- | ---: |
| **no MBID** | not a MusicBrainz id at all — a migration placeholder | 44 |
| **wrong song** | neither title nor performer matches the audio | 15 |
| **wrong artist** | same title, different performer | 32 |
| **wrong title** | same performer, different title | 23 |
| *different id* | the same recording under another MBID — off by default | 476 |
| *unverified* | AcoustID has no entry; **not evidence either way** — off | 843 |

Tap a chip to show or hide that grade. Worst first, always.

**Start with `wrong song`.** Fifteen cards, and they are the ones where the
player is announcing the wrong music.

## 3. Judge a card

Each card puts what the library believes on the left and what the audio says
on the right, with the fingerprint match percentage.

1. **Play now** — sends the passage to the player. Hearing it is the only
   thing that settles a case the names cannot. Have the speakers on.
2. **Pick a candidate** (the radio buttons) if the audio's version is right.
3. **Call the album** appears once you pick — choose which release to name it
   after. "Leave the album as it is" is fine; if no releases are known, the
   album keeps coming from the file's own tag.
4. Then one of:
   - **Use the match** — the candidate is right *(needs a candidate picked)*
   - **Keep ours** — the stored id is right despite the fingerprint
   - **Decide later** — looked at, not settled; leaves the working queue

Nothing here changes the library. Decisions are recorded and applied in §4.

**Changed your mind?** Turn on the **decided** chip, find the card, press
**Undo**. That works until the decision has been applied.

## 4. Apply the decisions

Rehearse first — it writes nothing and prints exactly what a real run does:

```
python tools/apply_reviews.py data/library.db
python tools/apply_reviews.py data/library.db --commit
```

A reassignment whose recording has no cached name is **refused**, not guessed
at, and reported so you can pick a different candidate.

**To undo something already applied**, the page will refuse and tell you to:

```
python tools/apply_reviews.py data/library.db --revert <passage_id> --commit
```

That restores the previous id and puts the passage back in the queue.

> **Why applying is separate.** Reassigning an id changes what a passage *is*,
> and play history is keyed by recording — doing it from a web click would
> silently re-attribute every past play of it. It is a migration, and
> migrations happen at a moment you chose.

## 5. If the queue is empty or stale

Re-run the fingerprint pass, then merge. It reads the library read-only and
writes to a sidecar, so it is safe to run while Lempi is playing.

```
python tools/fingerprint_ids.py data/library.db      # ~55 min for 8,078
python tools/fingerprint_ids.py data/library.db --merge
```

Needs `secrets/acoustid.key` (or `ACOUSTID_KEY`) and an `ffmpeg` built with
the chromaprint muxer — check with `ffmpeg -muxers | grep chromaprint`.

---

## What not to bother with

**`unverified` is not a finding.** It means AcoustID has no entry for that
audio, which says nothing about whether the stored id is right. There are 843
of them and working through the list would tell you almost nothing.

**`different id` is tidiness, not error.** 476 cards where the audio matches
the same song under a different MBID — a remaster, a 5.1 mix, a compilation's
own entry. Worth a pass one day, worth nothing today.
