# Changed without a stamp: the catalogue's daily backups (2026-10-15)

The measurement `[GDE-INT-165]` asks for before it is built: across consecutive
daily backups of the hub's catalogue, how many rows changed while none of their
provenance or time columns did. If edits always move one, the tripwire -- hold
such a row for a verdict instead of sending it -- is sharp. If not, the tools
that edit without a stamp are found first. The plan is
[GUIDE038](../docs/GUIDE038-integrity-gap-closures.md).

**Method.** Read-only. The day manifests in `data/backups/days/` name eight
distinct library objects from 2026-09-26 to 2026-10-15 (a day whose catalogue
had not changed reuses the last object), so seven consecutive pairs, plus the
10-15 backup against the live `data/library.db` at 17:00. Every catalogue
table has a primary key; rows were matched by it, and a changed row counted as
*stamped* when any of `source`, `*_src`, `*_at`, `first_seen`, `last_seen`,
`mtime`, `*generator`, `extractor` or `measured_on` moved with it. Each kind of
unstamped change was then tested against the other rows that moved in the same
interval. Nothing was written.

## 1. The count

| interval | rows changed | stamped | unstamped |
| :--- | ---: | ---: | ---: |
| 09-26 → 10-01 | 5,885 | 5,770 | 115 |
| 10-01 → 10-02 | 5,172 | 7 | 5,165 |
| 10-02 → 10-03 | 309 | 258 | 51 |
| 10-03 → 10-06 | 491 | 7 | 484 |
| 10-06 → 10-07 | 134 | 1 | 133 |
| 10-07 → 10-14 | 0 | 0 | 0 |
| 10-14 → 10-15 | 284 | 6 | 278 |
| 10-15 → live | 1,135 | 3 | 1,132 |
| **total** | **13,410** | **6,052** | **7,358** |

Taken as the plan first put it, the tripwire would hold more than half of
every change, every day: not sharp. But almost all of it has a reason that
another row records.

## 2. What the 7,358 are

**Explained by another row that moved -- 6,866.**

- `flavor.value`, **6,729 rows over 106 recordings.** A recording's flavor is
  the average of its passages', and is recomputed when one of its passages is
  linked, unlinked or reweighted, or has its features extracted. In every
  interval, every recording whose flavor moved had one of those in the same
  interval: 76 of 76, 7, 2, 4, 17 of 17. The row keeps `source =
  local:essentia-…` because the source has not changed; the inputs have.
- `release_recordings.chosen`, **22 rows, one per recording.** A CD add makes
  its disc the chosen release: the old choice drops to 0 unstamped, and the new
  row moves its `source` to `cd:import`. Paired in all 22.
- `ingest_decisions.audio_md5`, **115 rows**, 2026-09-27. The rekey: all 115
  are exactly an `old_md5 → new_md5` in `audio_md5_aliases`.

**A value where there was none -- 481.** Backfills and first choices:
`artists.sort_name` 64 (`artist_sort.py`), `files.sha256` 10, `passages`
`lead_in_ms`/`lead_out_ms` 365 (`analyze_amplitude.py`), all 2026-10-02;
`releases` status or date 2 (10-03) and `artist_credit` 2 (today); `chosen`
0 → 1 on 38 recordings that had no chosen release, 10-03. Every one was
`NULL` or nothing chosen before -- as measured, not as the writers promise:
`analyze_amplitude.py`'s update has no `IS NULL` guard.

**Neither -- 11 rows, all on 2026-10-03.**

- `releases`, **9 rows**: six titles (`That's` → `That’s` and the like), a
  country `FR → XE`, a release group, and one row whose title, date, status
  and country all changed.
  `fetch_releases.py` upserts a release from a fresh MusicBrainz response and
  writes `source = 'musicbrainz'` again; `releases` has no time column, so
  nothing moves. 161 `musicbrainz_cache` responses were fetched that day.
- `release_recordings.chosen`, **2 rows, 1 recording** re-chosen the day
  `file_releases` was introduced (`choose_release.py`), no stamp on either row.

## 3. What follows for `[GDE-INT-165]`

The tripwire is sharp **once it knows the three reasons** in §2: eleven rows
in twenty days, all on one day of MusicBrainz re-fetching. Built as:

1. A changed row is explained when its stamp moved; a `flavor` row when its
   recording's passage links or features moved; a `chosen` flip when a sibling
   row of the same recording moved its `source`; a key when it is exactly an
   alias.
2. `NULL → value` is listed with its count, not held: a fill is how every
   backfill above works, and a damaged value rarely arrives where none was.
3. Anything else is held for a verdict and named, not patched.

The nine `releases` rows would still be held. Two ways out, either after the
rollout:
`releases` gains a `fetched_at` (a schema change, so `sql/` and the player's
reader -- not while `player/` is frozen), or the tripwire accepts a release
whose new values are exactly what a `musicbrainz_cache` response fetched since
the backup says. The second needs no schema, and is the recommendation.

This is about **values a tool wrote wrongly**. A page SQLite cannot read is
`[GDE-INT-160]`'s question.
