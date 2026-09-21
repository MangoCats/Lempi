# SPEC019: Lyrics

**Design Specification — Tier 2 · storage, Lempi's own skins, the cache route built and confirmed against a client, and the music-folder sidecar built (`[SPEC-LYR-080]`)**

MuLibPlay accumulated lyrics for a quarter of the library. This is how they
reach Lempi, its own skins, and — as far as the protocol allows — a guest's
clients.

> **Related:** [SPEC016](SPEC016-mpd-protocol-findings.md) for what MPD will and will not carry · [REQ-VIS-213](../spec/REQ002-functional-requirements.md) for the display itself, [REQ-VIS-205], [REQ-VIS-210] for the two settings this follows

---

## 1. What exists

Measured against `MuLibPlay/mulib.db` and the live library, 2026-08-22.

| | |
| :--- | ---: |
| MuLibPlay tracks | 8,116 |
| …carrying lyrics | **2,288 (28.2%)** |
| …that also carry a recording MBID | **2,288 — all of them** |
| Recordings Lempi also knows | **2,265 (99.0%)** |
| Radio passages that would gain lyrics | **2,326 of 8,330 (27.9%)** |
| …of those, inside a DAO capture | 702 |

Plain text, not timestamped. Average 1,140 characters, longest 5,820.

**`[SPEC-LYR-010]` They key by recording MBID, which is why this is worth
doing.** Every lyric in MuLibPlay carries `mbidRecording`, and that is the
identity Lempi already transports `[SPEC-DF-030]`. The import is a join, not a
matching problem — no fuzzy titles, no heuristics, and 99% land.

---

## 2. Where they live

**`[SPEC-LYR-020]` A `lyrics` table keyed by `mbid`, beside `recordings`.**

```sql
CREATE TABLE lyrics (
    mbid       TEXT PRIMARY KEY REFERENCES recordings(mbid),
    text       TEXT NOT NULL,
    source     TEXT NOT NULL,     -- 'mulibplay', later others
    fetched_at TEXT NOT NULL
);
```

**Keyed by recording, not by passage.** A recording's words do not change
because it was ripped twice `[SPEC-DF-040]`, and two passages of one recording
share them. This is the same scope `flavor` and `recordings` already use.

**`[SPEC-LYR-025]` Class C: they travel.** Lyrics are derived reference data
about a recording, like a title or a flavor vector — not an account of one
person's listening `[SPEC-DF-055]`. A payload carrying a recording should carry
its words, and `[SPEC-PL-*]` gains a `lyrics` field on the recording object.

> At an average 1,140 bytes over 2,265 recordings this is about 2.6 MB — a
> rounding error beside the audio, and worth measuring again if a source with
> longer texts arrives.

**`[SPEC-LYR-030]` Import is Vipunen's, not the player's.** `tools/import_lyrics.py`
reads a MuLibPlay database and writes `lyrics` rows. The player never writes
this table, for the same reason it does not write `recordings`.

---

## 3. Reaching Lempi's own skins

**`[SPEC-LYR-040]` A separate endpoint, not the snapshot.** `GET /lyrics/{passage_id}`
returns the words for that passage's recording, or 404. The snapshot is
published on every tick and read by every skin; 5.8 KB of text in it would be
sent hundreds of times to say what changes once a song.

The skin fetches on a change of passage, which it already notices.

**`[SPEC-LYR-045]` The MuLibPlay skin gets a panel; the others may.** The
endpoint is skin-neutral and every skin can adopt it, exactly as `build` was
made available to all three `[REQ-VIS-200]`.

---

## 4. Reaching a guest's clients

**`[SPEC-LYR-050]` MPD has no lyrics facility, and this must be said plainly.**
Measured: **no lyrics command among its 102**, and **no lyrics tag among its
34**. There is no "MPD lyrics interface" to implement against. Whatever a client
displays, it found by its own convention — a sidecar file beside the audio, an
embedded `USLT` frame, or an online lookup.

So this is the third instance of one shape `[SPEC-MPD-052]`: Lempi knows
something the protocol cannot carry, and the only route is to put a file where
the client will look.

**`[SPEC-LYR-055]` A sidecar, behind an opt-in setting of its own.** `<basename>.lyrics`
beside the audio file, written only when asked, never overwriting one Lempi did
not write, idempotent — the same three properties `[REQ-VIS-205]` and
`[REQ-VIS-210]` already keep, for the same reason: it writes into a folder Lempi
does not own.

**And the same limit, in the same place.** One file holds one sidecar, so a
capture cannot express per-passage lyrics: **1,624 passages are reachable this
way, 702 are not**.

**`[SPEC-LYR-060]` The timed `.lrc` idea is dropped, and the lookup order is
now known rather than guessed.** *(Measured 2026-08-22.)* Cantata's own
`context/songview.cpp` gives the sequence:

| | tried |
| ---: | :--- |
| 1 | lyrics embedded in the tags, via TagLib |
| 2 | `<audiofile>.lyrics` beside the music |
| 3 | `<audiofile>.txt` |
| 4 | `cache/lyrics/<artist>/<title>.lyrics` — **nested per artist** |
| 5 | `cache/lyrics/<artist>/<title>.txt` |
| 6 | `~/.lyrics/…` (not on Windows) |
| 7 | online |

*Re-read 2026-08-22: the embedded-tag step was missed the first time. It changes
nothing here — Lempi does not embed — but the sidecar is second, not first.*

Confirmed by experiment: a handwritten `<audiofile>.lyrics` displayed, as a
**static block** — which is how MuLibPlay showed them and how Lempi will. No
synchronisation is wanted, so the `.lrc` is unnecessary.

**The order is the design constraint.** The music-folder sidecar wins over the
cache, so the two routes are ranked rather than complementary:

* **Ordinary files** — one song each — take `<audiofile>.lyrics`. A convention
  other clients share, and portable.
* **Captures** cannot: one file, one sidecar, twelve songs. Per-passage words
  would have to go in the **cache**, keyed by the artist and title the cue sheet
  already supplies `[SPEC-MPD-056]`.

> **The cache route is not obviously Lempi's to take.** It means writing into
> another application's private directory, and it is **Cantata-specific** — no
> other client looks there. That is a different kind of intrusion from the music
> folder and deserves its own decision, not a fold into the same checkbox.

**`[SPEC-LYR-070]` The cache route is built, behind its own checkbox
`[REQ-VIS-215]`.** `player/src/lyrics_cache.rs` writes
`<artist>/<title>.lyrics`, naming each file with a port of Cantata's
`Covers::encodeName` rather than an approximation — a name the two disagree
about is a file the client never opens.

**Named by what MPD will report, not by what Lempi believes.** For a cue track
that is the sheet's own `TITLE`/`PERFORMER`, which Lempi wrote from MusicBrainz;
for an ordinary file it is the file's tags. Measured on the live library:
**2,235 songs written**, one file each, captures included, and a second run
rewrites none of them.

**Confirmed in Cantata, both halves, 2026-08-22.** An ordinary file
(Fiona Apple, *The First Taste*) shows its words, which proves the path, the
nested layout and the name encoding. A passage **inside a capture**
(The Police, *King of Pain*, inside `Synchronicity.mp3`) shows its own words
too — and that is the half worth testing, because a capture's name comes from
the cue sheet rather than from file tags `[SPEC-MPD-056]`. Had the two naming
paths disagreed, the file would have sat there unopened.

**`[SPEC-LYR-075]` The cache is on the client's machine, not the server's — and
this is the limit to state before anyone relies on it.** It works when Lempi and
the client share a machine and does nothing when the client is elsewhere. The
music-folder sidecar `[SPEC-LYR-055]` has the opposite property: portable to any
client anywhere, but blind to the 702 passages inside captures. **They are
complementary, and neither replaces the other** — which is why they are two
settings rather than one.

**`[SPEC-LYR-080]` The sidecar is built too, and skips captures on purpose.**
`player/src/lyrics_sidecar.rs`, behind a fourth checkbox `[REQ-VIS-220]`.
**1,624 single-passage files** get `<audiofile>.lyrics`; the **702 capture
passages are left to the cache**.

**The reason is not that a capture cannot express per-song words — it is that
trying would undo the cache.** The sidecar is tried *before* the cache, so one
written beside a capture would overrule the 702 per-song files and show all
twelve songs at once for each of them. Skipping captures is what makes the two
settings complementary instead of one quietly defeating the other.

**`[SPEC-LYR-085]` The sidecar reaches another machine only where that machine
can read the music folder.** Cantata builds this path from **its own** Music
Folder setting joined with the song's path
(`song.filePath(MPDConnection::self()->getDetails().dir)`), not from anything
the server sends — MPD carries no lyrics at all `[SPEC-LYR-050]`. So a client
with the music folder mounted gets them; one with only a network connection to
MPD gets nothing either way. **Neither route reaches a client that cannot see
the files**, and the settings page says so rather than implying otherwise.

---

## 5. Auto-scroll: a guess, not a return to timed lyrics

**`[SPEC-LYR-090]` The MuLibPlay panel scrolls itself between the 20% and
80% marks of the passage, reaching the last line a little before the song
ends rather than exactly when it's sung.** This does not reopen the
`.lrc` question `[SPEC-LYR-060]` — it needs no timestamp data at all, only
`position_ms`/`duration_ms`, which every snapshot tick already carries.
Driven from that tick rather than a separate timer, so it freezes on
pause and jumps on seek for free, with no extra handling needed for
either.

**Wrong by construction for a repeated chorus, and that is tolerable.**
Lyrics text usually writes a chorus once; most songs sing it two or three
times. A linear line-to-time mapping is therefore structurally early for
the back half of most pop/rock songs, not merely noisy — but the text the
window shows late in that case is still the *right words*, since a
chorus repeats verbatim. The failure mode is usually self-cancelling.

**The scrollbar remains a full manual override**, compared against the
position this code itself last wrote rather than a browser `scroll`
event, so there is no race with the event's own timing to get wrong.
Once a listener's own scroll disagrees with that, auto-scroll stops
touching the box until the passage changes — never fighting a correction
mid-song.

## 6. What this proposal does not do

- **No online lookup.** A missing lyric stays missing; nothing here reaches the
  network `[SPEC-MPD-100]`.
- **No embedding into audio files.** Rejected for cover art on the same grounds
  and rejected here more firmly: a re-tag that goes wrong is not recoverable
  from a database backup.
- **No editing.** Lempi displays what it was given. A correction belongs where
  the data came from.

---

**Traceability:** `[SPEC-LYR-010..090]` · transported under `[SPEC-DF-030]` ·
guest limits from `[SPEC-MPD-052]` · settings pattern from `[REQ-VIS-205]` ·
display requirement `[REQ-VIS-213]`
