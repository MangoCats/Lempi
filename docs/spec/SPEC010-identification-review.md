# SPEC010: Identification Review

**Design Specification — Tier 2**

How Lempi decides whether a recording MBID is right, and how a person settles the cases it cannot `[REQ-LIB-165]`.

> **Why this is a document.** Every recording id in this library arrived by one route — `source` on all 16,157 rows of `passage_recordings` reads `inherited:mulib` — so they are all exactly as good as one migration, and a wrong one is invisible: the player shows a real title by a real artist, and it is simply the wrong song. Nothing downstream can catch it, because everything downstream trusts the id.

> **Related:** [REQ002 §4](REQ002-functional-requirements.md#4-library-building--lib-vipunen) · [SPEC007 Vipunen](SPEC007-vipunen-architecture.md) · [SPEC008 schema](SPEC008-database-schema.md)

---

## 1. The requirement

This specification designs against `[REQ-LIB-165]`, which REQ002 states as: *recording ids are checked against the audio, and a person settles the disputes.* The requirement is defined there; what follows is how it is met.

---

## 2. Design and measurement


Comparing an id against the file's own tags is the obvious check and a weak one: the ids may well have been *derived* from those tags, in which case agreement proves only that a copy matches its original. A fingerprint owes nothing to either. Chromaprint reduces the audio to how it actually sounds, AcoustID maps that to what other people have identified the same sound as, and neither has ever seen this library's metadata.

> **Fingerprint the passage, not the file.** A passage is a slice — `start_ms` to `end_ms` — and in this library the median file holds a good deal that is not the song. The first pass fingerprinted whole files and called 3,940 of 8,078 passages unmatched. That was a finding about the tool, not about AcoustID: among the "unmatched" were *Magical Mystery Tour*, Cat Stevens' *Greatest Hits* and four Jimmy Buffett records. The correlation was total — where the passage covers the file, 3,732 confirmed against 335 unmatched; where it is a partial slice, 104 against 3,605. The passage's own duration must be sent too, because AcoustID filters candidates by length.
>
> **Confirmation is lenient, contradiction is strict.** The stored id counts as confirmed if it appears anywhere in a match scoring 0.50 or better — AcoustID clusters recordings that sound identical, so an id appearing second is still a match. Calling one *wrong* needs 0.90. The two errors are not equally costly: a wrongly confirmed id stays as wrong as it already was, while a wrongly contradicted one sends a person to review something that was fine, and the queue is only useful if nearly everything in it deserves to be there.
>
> **`unmatched` is not a finding.** It means AcoustID has no entry for the audio, which says nothing about whether the stored id is right. Those passages are recorded and deliberately kept out of the review queue; there are enough of them to bury every real case.
>
> **The pass reads the library read-only and writes to a sidecar.** Vipunen holds the write lock for a minute at a time, so a two-hour pass sharing it would spend most of its life waiting — and a pass that cannot write to the library also cannot damage it. `--merge` folds the findings in once the library is quiet.
>
> **ffmpeg's chromaprint muxer, not fpcalc.** The same library, already installed, so identification does not become a second binary the build depends on. Validated against AcoustID on known-good files before it was trusted: scores 0.986 to 0.995, with the stored id present every time.
>
> **The review page shows the two claims side by side and can play the passage.** Hearing it is the only thing that settles a case the names cannot, and it goes through the ordinary queue verb rather than a new audio route `[REQ-VIS-185]`.
>
> **A judgement can be withdrawn, and an applied one cannot be withdrawn quietly.** Decided cards return from the queue carrying their decision, behind a *decided* chip that is off by default so the working list still shortens. Undo puts a card back. But once `apply_reviews` has rewritten `passage_recordings`, deleting the review row would leave the library changed with nothing left saying what it replaced or why — an undo that leaves in place the thing it was undoing. So a decision is stamped `applied_at` when it reaches the library, the page refuses those with the reason, and `apply_reviews --revert <passage>` restores the old id and clears the record in one transaction. `previous_mbid` is captured when the decision is *made*, because applying it overwrites the only other copy.
>
> **The album can be named, not just guessed.** A recording sits on many releases — the album, the remaster, three compilations — and `ALBUM_EXPR` broke ties by release date. Choosing a candidate offers the releases Vipunen knows for it, and the answer is applied as `release_recordings.chosen`, which is the flag that already outranks the date. A recording with no known releases says so: the album keeps coming from the file's own tag.
>
> **A passage with no MBID is a different problem from a wrong one.** The migration left 44 carrying `local:track:N`, two of which share a number, so they do not even identify a track uniquely — and everything downstream keys on this string. That is an *absent* identification, not a questionable one, and it is certain rather than likely, so `no-mbid` leads the queue and is graded before the audio is consulted at all. Shape-checked rather than prefix-checked, so any other non-conforming id is caught too. With them the default view is 114 cards.
>
> **A decision is recorded; it is not applied.** Judgements go to `id_reviews`, which the player owns, and `tools/apply_reviews.py` folds accepted ones into `passage_recordings` as a separate, rehearse-by-default step. Reassigning an id changes what a passage *is*, and play history is keyed by recording — doing it silently from a web click would re-attribute every past play of it. It also leaves the read-only guard on the library intact.
>
> **Nothing to review must not look like a broken page.** `id_checks` is written by the pass, not by the player, so on a library where it has never run the table is absent — and a query naming a missing table fails outright rather than returning nothing. That exact mistake blanked the browse page twice. The page distinguishes "never looked", "found nothing" and "all dealt with".
>
> **First full run, 2026-08-15, all 8,078 radio passages in 57 minutes:**
>
> | verdict | count | share |
> |---|---:|---:|
> | confirmed | 6,591 | 82.0% |
> | contradicted | 567 | 7.0% |
> | unmatched | 864 | 10.7% |
> | inconclusive | 17 | 0.2% |
> | unreadable | 0 | 0% |
>
> So **82% of the migrated ids are now confirmed by evidence they did not come from**, which is the first independent word on them that has ever existed. Of the 7,158 the fingerprint could settle either way, 7.9% are wrong.
>
> **But most contradictions are the same song under a different recording id** — "Why Worry" against "Why Worry (5.1 mix)", two Bowie ids for one *Rock 'n' Roll Suicide*, an album cut against a long version. That is tidiness, not misidentification.
>
> **So findings are graded, and the queue is listed by severity.** One bit cannot tell a passage playing under a completely wrong name from a remaster with its own MBID, and on this library that difference is 87 cases against 1,346. The grades are the distinctions `verify_ids.py` already drew against the file tags — title agrees, artist agrees, neither — applied to evidence that is actually independent:
>
> | grade | count | shown by default |
> |---|---:|:--:|
> | `no-mbid` — no MusicBrainz id at all, a migration placeholder | 44 | ✓ |
| `wrong-song` — neither title nor performer matches | 17 | ✓ |
> | `wrong-artist` — same title, different performer | 46 | ✓ |
> | `wrong-title` — same performer, different title | 24 | ✓ |
> | `different-id` — the same recording under another MBID | 480 | |
> | `unverified` — AcoustID does not know this audio | 866 | |
>
> The page opens on **87 cards**, worst first, and each grade is a chip carrying its own count so the size of every kind of problem is visible before choosing what to work through. The two large, low-value classes are one tap away rather than absent — including `unmatched`, which is reachable deliberately but never by default, because 866 non-findings would bury the 17 that matter.
>
> **Exercised against a live server, which found two faults nothing else could** *(2026-08-15)*. Run on a copy of the real library, so the writes were real:
>
> **Browsing albums had become quadratic.** Album names are looked up by *recording*, and `release_recordings` is keyed `(release_mbid, mbid)` — the lookup uses the second column of the primary key, so no index applies and SQLite scans the table once per passage. Free while the table was empty; at Vipunen's 304,334 rows against 8,078 passages, `/review/queue` timed out past 120 s and `/browse/albums` ran past **400 s**. No commit caused it — the data grew under code that never changed, which is the kind of regression with nothing to bisect. An index on `release_recordings(mbid)` takes the review queue to **0.50 s** and album browsing to **7.0 s**.
>
> **`apply_reviews` could never have applied anything.** `recordings.title` and `recordings.source` are both `NOT NULL`; the writer supplied neither, and used `INSERT OR IGNORE`, which turns a constraint violation into nothing happening — so the row was silently skipped and the foreign key failed on the statement after. It now supplies `source`, inserts explicitly where a later statement depends on the row existing, and *refuses* a reassignment whose recording has no cached name rather than writing a nameless one. The tests missed it because the fixture declared `recordings(mbid, title)` with no constraints at all and accepted writes the real schema rejects; it now matches SPEC008 including `NOT NULL`.
>
> The full loop is verified: passage 8034, labelled *"The Four Seasons: Spring: I. Allegro"* by Various Artists, is in fact Bach's Air from BWV 1068. Reassigned on the page, applied by the tool, and browse then names it correctly with its three artists linked.
>
> **Two silences are not a disagreement.** Absent evidence never counts as agreement — a candidate naming a different artist is a real finding even if another names none — but where the library holds no artist, or no candidate states one, the artist cannot be *wrong* and the title decides alone. Grading that `wrong-artist` would invent a dispute out of missing data and send someone to adjudicate it.
>
> The 89 `local:track:N` placeholder ids from the migration were checked too: 21 contradicted, 23 unmatched, and the rest carry no passage. They are not MBIDs at all and want their own repair rather than case-by-case review.

---

## 3. Searching MusicBrainz directly

This designs against `[REQ-LIB-180]`. *(Requested 2026-08-27; designed and built 2026-08-27 — recording/artist search and artist-only correction; release and track search remain designed, not built, per `[SPEC-SUI-197]`'s table below.)* The review queue above settles the case AcoustID can speak to. It cannot help with the others, and there are three of them: self-released audio with no fingerprint entry at all, a remaster or bootleg AcoustID has never indexed, and — a case the queue does not even see today — a recording id that is *right* while its **credited artist** is wrong. `unverified` alone is 866 passages on this library, none of them reachable by a radio button.

**`[SPEC-SUI-195]` Reference before reassignment: every id on the card is a link to what it actually names.** A person cannot judge a candidate by its MBID; they can judge it by the MusicBrainz page it opens onto. Every mbid already shown on a review card — the stored recording, each suggested candidate, the artists and the release offered against it — becomes a plain link to `https://musicbrainz.org/<entity>/<mbid>`, `target="_blank"`. No proxying, no API call: this is the browser's own operator following the same link they could type by hand, and it costs MusicBrainz nothing to serve.

**`[SPEC-SUI-196]` Search is a proxy, and the proxy is where the rate limit lives.** MusicBrainz asks for a real `User-Agent` with a contact address and enforces roughly one request per second — `tools/fetch_releases.py` already carries both, for the bulk fetches Vipunen already runs. `GET /api/musicbrainz/search?kind=<recording|artist|release>&q=<text>` wraps `/ws/2/<kind>?query=...&fmt=json` behind that same discipline in **one place**, so the browser never calls musicbrainz.org directly and the rate limit cannot be bypassed by opening two tabs. Results come back shaped exactly like a fingerprint suggestion, so choosing a searched result and choosing a fingerprinted one are the same action from the operator's side of the page.
>
> **Built in Lempi, not Vipunen's console — a correction to this paragraph's first draft.** `/review` is served by Lempi since `[SPEC-SUI-135]`'s handoff, so `review.js` runs on Lempi's own origin; a route on the console's separate process would be a cross-origin call the browser refuses without CORS the console has no reason to grow. The rate limiter lives as a process-wide mutex in `player/src/web/musicbrainz.rs` (`web.rs` at the time, split 2026-09-02), gated `vipunen-support` along with the rest of this page, and Lempi's own `reqwest` dependency is declared `optional`, wired to that same feature, so an appliance build never resolves or compiles an HTTP client it will never call.

**`[SPEC-SUI-197]` Four things can be wrong independently, and one of them had no way to be fixed at all.**

| what | before this section | now |
| :--- | :--- | :--- |
| **recording** (the song) | fixable — `record_review`'s `chosen_mbid` | unchanged |
| **release** (the album) | fixable, but only among releases the *chosen recording* already links to | unchanged — any release found by search, linked on accept, stays wanted, not built |
| **artist** (the credit) | **not independently fixable** — it followed whichever recording was chosen | **fixable.** `artist_reviews`, `POST /review/:id/artist/correct` |
| **track** (position on the release) | not tracked as a correction at all | unchanged, still wanted |

**Recording/release/artist/track are used in the precise senses [SPEC023](SPEC023-domain-vocabulary.md) defines.** Choosing among "the releases Vipunen knows for it" two rows up is not always resolving a real ambiguity: MusicBrainz routinely catalogues one physical/logical album as several release MBIDs (a US pressing, a European one, a digital reissue) that are functionally indistinguishable for every purpose here — same tracklist, same title. Differing release MBIDs do not, by themselves, mean two different albums; `release_recordings.chosen` records which catalogue entry got picked, not a claim that the underlying album is otherwise ambiguous (`[SPEC023]` Release).

**The release/track half of this table has a second caller now.** [SPEC028 §3](SPEC028-cd-ripping-identification.md#3-when-automation-isnt-enough--the-person-with-the-disc-decides) reuses `[SPEC-SUI-196]`'s search proxy for CD ripping's own manual-fallback path, and names the still-unbuilt release/track search as a second, independent reason to finish it — searching by *album title*, what a disc actually carries, rather than by one track's recording title alone.

The artist case was the one this table added, not merely extended: `recording_artists` is keyed `(mbid, artist_mbid)`, and nothing wrote to it outside Vipunen's own ingest before this. A person confirming "this recording is right, but MusicBrainz's own credit is wrong" needed a decision `apply_reviews.py` could fold into `recording_artists` the same way it folds a recording reassignment into `passage_recordings` — a new decision shape, not a reuse of the existing one, because the row it corrects is a different table with a different key. Built 2026-08-27: `artist_reviews` carries `recording_mbid` and the previous credit (`previous_artist_mbid`/`name`/`weight`), captured at decision time for the same reason `id_reviews.previous_mbid` is — a credit correction has no re-derivable source the way a boundary edit does, so this is the only copy, and `apply_reviews.py --revert-artist` needs it to mean anything. Offered on any card whose stored id shape-checks as a real MusicBrainz id, independent of whatever else that card decides. See [IMPL006 Stage 10](../IMPL006-vipunen-editing-workflows.md#stage-10--artist-only-correction) for what was verified.

**Designed and built 2026-08-27**, alongside `[REQ-LIB-175]`'s waveform editor — both requested together, both designed together, and the build order in [IMPL006](../IMPL006-vipunen-editing-workflows.md) covers both, now complete.

---

**`[SPEC-SUI-199]` Built 2026-08-30. The queue's own narrowness was, by accident, the only door.** `review_queue` is deliberately CONTRADICTED-only — showing every passage nothing is wrong with would bury the real findings — but that also meant a passage that had simply never been fingerprinted, or whose stored id was merely not what someone wanted it to say, could not reach the search-and-reassign box (`[SPEC-SUI-196]`) at all, even though nothing about that box depends on being a finding. `Library::review_item_for(passage_id)` builds the same `ReviewItem` shape for one named passage regardless of `id_checks`, served at `GET /review/passage/:passage_id`; a never-checked passage gets a new `on-demand` severity (`SEVERITIES` in `player/core/src/db/library.rs`, `db.rs` at the time, split 2026-09-01) rather than a fabricated `checked_at`. `review.js`'s deep-link handling (`?passage=`) now asks this route directly whenever the passage was not already in the batch, so `profile.html`'s existing "Review in Lempi" handoff button works for any passage, not only ones the fingerprint pass flagged. No new write path — this reuses `record_review`/`apply_reviews.py` unchanged.
