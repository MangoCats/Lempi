# SPEC079: Editing Lyrics, and Which Words a Passage Shows

**Design Specification — Tier 2 · specified 2026-11-12, from the maintainer's request and answers given that day · not built**

Lempi shows the words MuLibPlay collected and has no way to add or correct any. This restores MuLibPlay's two-column lyric page: an editor on the left, a web search for the song on the right. It also changes what the words belong to. They are saved for a passage, and a passage with none of its own shows the nearest words the library has.

> **Related:** [SPEC019](SPEC019-lyrics.md) (the imported words, the endpoint, the MuLibPlay skin's window) · [REQ006](REQ006-visibility-words-and-the-rest.md) `[REQ-VIS-330]` to `[REQ-VIS-350]` (what is asked) · [SPEC029](SPEC029-listener-preference-editing.md) (the other editor MuLibPlay had, and the pattern followed here) · [SPEC046](SPEC046-star-sync.md) (how an edit reaches other nodes) · [SPEC075](SPEC075-permanent-identity-and-a-movable-hub.md) (why no passage number is stored) · [GUIDE012](../GUIDE012-work-based-song-blocking.md) (the same three tiers, used for blocking)

---

## 1. What MuLibPlay had

Read from its source, 2026-11-12 (`trackController.cpp`, `lyricEditForm` and `lyricEntryForm`).

- A **Lyrics** button on a track's ratings page opened a frameset of two equal columns.
- The left frame held the track's description as a heading, one text box 35 rows by 60 columns holding the track's words, **Save** and **Exit**.
- The right frame was `https://google.com/search?igu=1&q=lyrics+` followed by the track's description.
- Save wrote `tracks.lyrics`, one text per track. An empty box was ignored, so words could be replaced and never removed.
- `radioController.cpp` has the same frameset with a left frame that was never written: it prints `lyricEntry` and the track number.

2,288 of its 8,116 tracks carried words entered this way `[SPEC-LYR-010]`.

## 2. What Lempi has today

Measured on the hub's pair, 2026-11-12: 24,053 passages (12,027 album, 12,026 radio) on 7,247 files.

| | |
| :--- | ---: |
| rows in `lyrics`, every one `source = 'mulibplay'` | 2,265 |
| passages those reach, by recording | 4,739 (19.7%) |
| passages naming more than one recording | 1 |
| recordings with a work, of 11,740 a passage names | 7,472 |
| works on more than one recording | 534 |

The words are keyed by recording `[SPEC-LYR-020]`, in the catalogue half, which the player attaches read-only `[PI-DB-020]` and never writes `[SPEC-LYR-030]`. Only the MuLibPlay skin shows them `[REQ-VIS-213]`.

## 3. Which words a passage shows

**`[SPEC-LYR-100]` Four tiers, searched in order, and the first that holds anything is shown:**

| tier | whose words | how it is matched |
| ---: | :--- | :--- |
| 1 | the passage's own | saved for this passage `[SPEC-LYR-140]` |
| 2 | another passage of the same audio | the same file, and the same segment `[SPEC-LYR-110]` |
| 3 | the same recording, in any file | the passage's recording MBID: words saved for another passage of it, or the imported row |
| 4 | the same work, in any file | any work the passage's recording performs, through `recording_works` |

Tiers 1, 3 and 4 are the three identities selection already blocks on `[GDE-WRK-035]`. Tier 2 is added because an album cut and a radio cut of one track are two passages, and words typed for one are the words of the other.

**`[SPEC-LYR-110]` Two passages of one file are the same segment when the stretch they share is at least half of the longer of the two.** Their kinds do not matter. Measured on the hub, 2026-11-12, over the 12,029 pairs of passages in one file that share any audio:

| share of the longer passage | pairs |
| :--- | ---: |
| 90% or more | 11,995 |
| 60% to 90% | 32 |
| 20% to 60% | 0 |
| under 20% | 2 |

Half sits in the empty part. 12,027 of the pairs are an album cut and a radio cut. 24,048 passages have exactly one such sibling, three have two, and two have none.

**`[SPEC-LYR-115]` The recording is the passage's heaviest, as the endpoint reads it today** (`ORDER BY weight DESC LIMIT 1`). One passage in the library names two. A recording that performs several works, of which there are 69, matches through any of them.

**`[SPEC-LYR-120]` Within a tier, words a person saved come before imported ones, and newer before older.** A tie is broken by the entry's key, so every node reads the same answer from the same rows. The order matters most in tier 4: 54 works have words on more than one recording, and in 33 of them the texts differ.

**`[SPEC-LYR-125]` Tier 4 reaches 494 passages that show nothing today** (248 radio, 246 album, through 238 recordings), taking the library from 4,739 worded passages to 5,233. This needs no new table, and is the first step of §8.

**`[SPEC-LYR-130]` A passage can be marked as having no words, and the mark is an answer, not an absence.** Found in tier 1 or tier 2 it ends the search and nothing is shown: tier 2 is the same audio, so what is true of one cut is true of the other. A mark is never read in tiers 3 or 4, since another recording of a song may be sung where this one is not. Without it, tier 4 would show a song's words over an instrumental version of it.

## 4. Where an edit is kept

**`[SPEC-LYR-140]` A table `passage_lyrics`, in the listener half.**

```sql
CREATE TABLE passage_lyrics (
    audio_md5   TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    start_ms    INTEGER NOT NULL,
    end_ms      INTEGER NOT NULL,
    mbid        TEXT,                        -- the passage's heaviest recording when saved
    text        TEXT    NOT NULL,            -- '' with no_words = 0: cleared
    no_words    INTEGER NOT NULL DEFAULT 0,  -- 1: this passage has no words
    updated_at  TEXT    NOT NULL,
    origin      TEXT,                        -- the node it was saved on
    PRIMARY KEY (audio_md5, kind, start_ms)
);
```

It is in the listener half because the player cannot write the catalogue on a split node, which is every node, and for the reason `[SPEC-PREF-085]` gives for a listener's own tags: this is a person's edit, backed up and synced with their others. `lyrics` stays as it is, a source the player reads and never writes.

**`[SPEC-LYR-145]` No passage number is stored.** A number means nothing on another node `[SPEC-DF-035]`, and a new listener column holding one is what `[SPEC-UID-070]` exists to refuse. The key is the name `[SPEC-UID-010]` uses until a passage has a permanent identity: the file's `audio_md5`, the passage's kind and its start. A retired `audio_md5` is followed through `audio_md5_aliases` `[SPEC-RLK-155]`. When `passage_uid` exists `[SPEC-UID-030]`, this table takes it with the other listener tables.

**`[SPEC-LYR-150]` A passage's own entry is found by segment, so a boundary edit does not orphan it.** The own entry is the newest row of the passage's file and kind that is the same segment as the passage is now `[SPEC-LYR-110]`. A passage whose start is moved therefore keeps its words, and its end is not in the key at all. Save writes under the passage's present start and end. An older row left under a former start is the same entry, superseded, and is never shown.

**`[SPEC-LYR-155]` A row is never deleted.** Clearing writes an empty text, and unmarking writes `no_words = 0`, each with a new `updated_at`. A missing row cannot be told from one never written `[SPEC-STAR-050]`, and a cleared one can. A cleared row is read as no entry at all.

**`[SPEC-LYR-160]` The row carries its recording, so tiers 3 and 4 work on a node without the file.** A node's catalogue leaves out the files it does not hold `[SPEC-STAR-082]`, and keeps every recording and work. So a row saved for a file this node lacks is matched by its stored `mbid`. Where the node does hold the file, the passage's present recording is used instead, because an identification may have been corrected since the words were saved.

## 5. Reading and writing

**`[SPEC-LYR-170]` `GET /lyrics/:passage_id` keeps its shape and takes the new order.** It answers the text `[SPEC-LYR-100]` chooses, or 404 for none and for a mark. No skin changes to follow it `[SPEC-LYR-040]`, and the MuLibPlay skin's scrolling is untouched `[SPEC-LYR-090]`.

**`[SPEC-LYR-172]` `GET /lyrics/:passage_id/sources` answers everything the editor shows:**

- the heading: title, artist and release, by the expressions the preference panel uses `[SPEC-PREF-090]`
- the passage's own entry: its text, whether it is marked, when and where it was saved
- every other source that has text, in the order of `[SPEC-LYR-100]` and `[SPEC-LYR-120]`, each with its tier, its text, and what names it: the other cut's kind, or the recording's title, artist and release, and either *imported from MuLibPlay* or when and where it was saved

A text that appears more than once is listed once, at its highest place. Marks and cleared rows are not sources.

**`[SPEC-LYR-174]` `POST /lyrics/:passage_id` saves one passage, and nothing else.** The body is JSON, `{"text": …, "no_words": …}`, not a query string: the longest text held is 5,820 characters. Line ends are stored as `\n`, and a text of only white space is empty. A body over 64 KiB is refused. An unknown passage is a 404. The answer is the entry as stored. The route writes `passage_lyrics` and no other table: words reached through tiers 2 to 4 are read-only, here and on the page.

**`[SPEC-LYR-176]` The files written for a guest's clients take the same order, for a song that has no file yet.** Decided 2026-11-12. The cache `[SPEC-LYR-070]` and the sidecar `[SPEC-LYR-080]` choose a passage's words by `[SPEC-LYR-100]`, where today they join the imported table. A marked passage gets no file. The cache holds one file for an artist and title, so where two passages share both, the words are the first by `[SPEC-LYR-120]`. Neither writer replaces a file that is already there, which is unchanged `[SPEC-LYR-900]`.

## 6. The page

**`[SPEC-LYR-180]` A small link, *lyrics*, beside what is playing, in the Lempi and MuLibPlay skins.** It is shown whenever a passage is playing and hidden when none is, whether or not the passage has words. `core.js` sets its target, since a skin builds no URL `[REQ-VIS-160]`. It opens `GET /lyrics/:passage_id/edit` in the same tab. The player is the server's, so the music does not stop.

**`[SPEC-LYR-181]` The same link is on Lempi's passage page, `/passage/:id`** `[REQ-VIS-270]`. Decided 2026-11-12. Browse links to that page on every node, so the words of any passage can be edited without playing it.

**`[SPEC-LYR-182]` Two equal columns, and the left one is filled.** The editor and the reference box each take the column's full width, and share between them all the height the heading, the controls and the drop-down leave, growing and shrinking with the window. Neither is a box of fixed rows and columns, as MuLibPlay's was: asked for by the maintainer 2026-11-12, on seeing a fixed box leave most of the column empty. In a window too narrow for two columns, they stack, editor first.

```
+----------------------------+----------------------------+
| Title by Artist from Album |  open this search in a     |
| [ editor: this passage   ] |  new window                |
| [                        ] | +------------------------+ |
| [ ] This passage has no    | | web search:            | |
|     lyrics                 | | lyrics Title Artist    | |
| [Save]  Back to the player | |                        | |
| ( reference, read-only   ) | |                        | |
| (                        ) | |                        | |
| [v Recording: MuLibPlay ]  | +------------------------+ |
| [Copy all to editor]       |                            |
+----------------------------+----------------------------+
```

**`[SPEC-LYR-184]` The editor holds the passage's own words, and starts empty when it has none.** Decided 2026-11-12: words shown through a lower tier are not put in the box. Nothing becomes the passage's own until a person puts it there and saves.

**`[SPEC-LYR-186]` Under the editor, a read-only box shows one other source, chosen by a drop-down beneath it.** The drop-down lists every source of `[SPEC-LYR-172]`, and **None**, which is offered even when sources exist: it hides the box and gives its height to the editor. The page opens on the first source. With no source, neither the box nor the drop-down is shown. The text can be selected and copied in whole or in part, and **Copy all to editor** puts the whole of it in the editor, asking first if the editor already holds text.

**`[SPEC-LYR-188]` Save, clear and the mark.** Decided 2026-11-12:

- **Save** stores the editor's text as the passage's own, and says when it has.
- **Saving an empty editor** clears the passage's own words, and the lower tiers show again `[SPEC-LYR-155]`.
- **This passage has no lyrics**, ticked and saved, stores the mark `[SPEC-LYR-130]`. The editor's text is kept under the mark and not shown while it stands, so unticking brings it back.
- Leaving with unsaved text asks first.

**`[SPEC-LYR-190]` The page stays on the passage it was opened for.** While the passage playing is another one, the page says which, with a link to that passage's page. It does not follow by itself, because the editor may hold unsaved text.

**`[SPEC-LYR-192]` The right column is a frame on a web search for `lyrics`, the title and the artist.** The title and artist are the heading's, so unidentified audio is searched by its file tags. The address is MuLibPlay's, `https://www.google.com/search?igu=1&q=…`. Measured 2026-11-12 from the hub: with `igu=1` the answer carries no `X-Frame-Options`, and without it, `SAMEORIGIN`. A headless browser the same day drew Google's page inside the frame, and that page was its check for automated traffic, not results. The maintainer's own browser on the hub, the same day, showed the search's results in the frame of the same test page. So the frame works on a desktop, and a phone's WebView is still to be seen `[SPEC-LYR-915]`. The parameter is undocumented, and a page cannot learn that a frame was refused, so *open this search in a new window* is always shown above the frame. The frame is given `referrerpolicy="no-referrer"`, so the search is not told the player's address.

**`[SPEC-LYR-194]` Lempi still reaches no network.** The search is made by the person's browser, from the person's machine. The player fetches nothing, so SPEC019 §6's *no online lookup* stands. On a node with no route out, the frame fails and the editor works.

**`[SPEC-LYR-196]` Whoever can open the player can edit.** The page asks for nothing the preference panel does not `[REQ-VIS-285]`.

## 7. Reaching other nodes

**`[SPEC-LYR-200]` `passage_lyrics` is a shared table of the star sync, last write wins on `updated_at`** `[SPEC-STAR-040]`, keyed by its primary key. Words are reference data about music and travel `[SPEC-LYR-025]`. Because the key holds no local number, the table needs none of the translation `[SPEC-STAR-049]` and `[SPEC-STAR-051]` do for a node's passage ids. Because no row is deleted, it needs no removal evidence. **Every node receives every row, whether or not it holds the file.** That is what lets tiers 3 and 4 work there `[SPEC-LYR-160]`, and it means the words are already on a node when the audio arrives by bundle. Two nodes that save one passage under different starts, on either side of a boundary edit, leave two rows that are one entry, and each node reads the newer `[SPEC-LYR-150]`.

**`[SPEC-LYR-205]` It joins the numbering test before it ships** `[SPEC-UID-020]`: a row saved on one node must show on another whose passages are numbered differently, and on one that lacks the file.

**`[SPEC-LYR-210]` A phone receives the table over its own channel** `[REQ-AND-330]`, as it does the other shared tables.

## 8. Build order

Each step can be used alone, in the order the fleet takes any player change: this machine, one node, then the rest.

| step | what | how it is proved |
| ---: | :--- | :--- |
| 1 | The order of `[SPEC-LYR-100]` over today's tables: tiers 3 and 4 | `Library::lyrics` tests; 494 more passages show words `[SPEC-LYR-125]` |
| 2 | `passage_lyrics`, the three routes, the page, the link in two skins and on the passage page | route tests; `build/verify-skins.js` drives the page and reads what Save posts |
| 3 | The table in the star sync | `[SPEC-LYR-205]`; a row saved on one node read back from another's file |
| 4 | The cache and the sidecar by the same order `[SPEC-LYR-176]` | their tests, with a saved passage and a marked one |

## 9. What this does not do

Each decided by the maintainer, 2026-11-12.

- **`[SPEC-LYR-900]` A corrected text does not replace a file already written for a guest's client.** Neither writer keeps a record of what it wrote, so a file that differs is taken for someone else's and kept `[SPEC-LYR-176]`. Replacing Lempi's own would need that record.
- **`[SPEC-LYR-905]` A bundle carries no saved words** `[SPEC-PL-052]`. They are on a node before its audio is `[SPEC-LYR-200]`. Words saved since the last sync arrive with the next, and a phone outside the mesh receives none.
- **`[SPEC-LYR-910]` The Lempi skin shows no words.** It gets the link only. The WinAmp skin gets neither.
- **`[SPEC-LYR-920]` A row's stored recording can go stale on a node without the file**, when the passage is identified again elsewhere `[SPEC-LYR-160]`. Saving that passage again corrects it. One catalogue on every node `[SPEC-UID-110]` removes the case.
- **`[SPEC-LYR-925]` Vipunen's console neither shows nor edits these words.** The passage page is the way to a passage that is not playing `[SPEC-LYR-181]`.

## 10. Open

1. **`[SPEC-LYR-915]` What the frame shows in a phone's WebView has not been seen.** A desktop browser shows results `[SPEC-LYR-192]`. Whether a phone gets results or a check for automated traffic is known only by looking, and the maintainer put that off on 2026-11-12.

---

**Traceability:** `[SPEC-LYR-100..925]` · derives `[REQ-VIS-330]`, `[REQ-VIS-335]`, `[REQ-VIS-340]`, `[REQ-VIS-345]`, `[REQ-VIS-350]` · extends `[SPEC-LYR-040]` and `[REQ-VIS-213]` · reuses the tiers of `[GDE-WRK-035]` · applies `[SPEC-DF-035]`, `[SPEC-UID-010]`, `[SPEC-STAR-040]`
