# SPEC082: Asking MusicBrainz

**Design Specification — Tier 2 · written and built 2026-11-19**

How fast this machine asks MusicBrainz, whoever asks. Their published limit is
one request a second for each IP address, and an address that keeps going
faster is blocked. A blocked address stops every lookup, a slow one stops
none.

> **Related:** [SPEC081](SPEC081-finishing-an-album-from-files.md) (whose
> finishing job asks in several steps) · `tools/mb_pace.py`,
> `player/src/web/musicbrainz.rs`

---

**`[SPEC-MBP-010]` Every request to MusicBrainz waits on one pace.** The
tools' requests go through `mb_pace.wait`: `fetch_releases.get`, and through
it `suggest_release`, `ingest_cd`, `apply_reviews`, `artist_sort` and the rest,
then `fetch_works`, `fetch_chosen_tracks` and `pending`. A retry waits too,
since a retry is a request. Until 2026-11-19 each tool slept a second after
its own requests. That left no margin, and nothing between tools: two tools,
or two steps of one console job, could ask back to back.

**`[SPEC-MBP-020]` The pace is 1.2 s: the limit with a fifth to spare.** The
maintainer's margin, 2026-11-19, so a request timed at the edge of a second
never reads to their counter as two in one. `MIN_INTERVAL_S` in
`mb_pace.py` and `MB_MIN_INTERVAL_S` in the player; a test on each side holds
it.

**`[SPEC-MBP-030]` The pace is the machine's, not a process's.** The last
request's time is kept in one file. That is `LEMPI_MB_PACE_FILE`, else
`%LOCALAPPDATA%\Lempi\musicbrainz.pace` on Windows, else
`$XDG_CACHE_HOME/lempi/` or `~/.cache/lempi/`. Not the temporary folder,
which a shell can set for itself. Each caller locks the file, takes the next
free moment at least 1.2 s after the last, writes it, and sleeps until it
outside the lock. So waiting callers queue in turn: three processes sharing
it in `test_mb_pace.py` kept every gap. The player's own search on the review
page waits on the same file. If it cannot use the file, it says so in its log
and paces its own process alone, as it did before.

A reservation more than a minute ahead is a clock set back, not a queue, and
is forgotten rather than waited out. The file holds seconds since the epoch,
to three places, as both sides read and write it.

**`[SPEC-MBP-040]` Only MusicBrainz waits.** A test's own server, reached
through `LEMPI_MUSICBRAINZ_URL`, has no limit to keep. The Cover Art Archive
and AcoustID keep their own published rates, in `fetch_cover_art.py` and
`fingerprint_ids.py`. The limit is per address, so another machine of the
household asking at the same time is not paced by this one.

---

**Traceability:** `[SPEC-MBP-010..040]` · applies to `[SPEC-SUI-196]`, `[SPEC-SUI-215]`, `[SPEC-FIN-050]`
