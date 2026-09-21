# SPEC029: Editing What a Listener Knows About One Artist or Recording

**Design Specification — Tier 2 · Built**

MuLibPlay let a listener hand-tune `rotation`/`recovery`/`restraint` per
artist and per recording directly — real, used functionality: `[GDE-BMK-020]`
records that 2,918 of 8,116 tracks (36%) carried a tuned value. Lempi's
schema (`listener_preferences`, [SPEC008 §6](SPEC008-database-schema.md#6-listener-state--class-d))
already carries those exact rows forward unchanged from the MuLibPlay
migration, but until this document nothing reached them except
`Director::load()` at startup and the one-time migration script — no route,
no page, anywhere, to view or change a single artist's or recording's own
tuning. This restores that surface, and §4 restores the other half of the
same editor — MuLibPlay's Christmas/Winter/Summer/Children's tags and its
profanity slider, inherited the same way and reachable by just as little.

> **Status.** Built 2026-09-04, per `[REQ-VIS-285]`, `[REQ-VIS-290]`.
> `player/src/web/preference.rs` (routes), `PlayerStore::get_preference`/
> `set_preference`/`reset_preference` (`player/src/db/player_store.rs`), and
> `Lempi.editPreference` (`player/src/web/core.js`, the one shared panel
> every skin below reuses).
>
> **§4 and §5 built 2026-09-10**, per `[REQ-VIS-305]`, `[REQ-VIS-310]`.
> `listener_characteristics` (new, listener-side), `PlayerStore::list_specials`/
> `set_special`/`reset_special`/`subject_naming`, the `label` column on
> `listener_occasions` that `tools/load_occasions.py` now writes, and
> `load_occasions()`'s own overlay in `player/src/director/library.rs`.
> Exercised in a real DOM by `build/verify-skins.js`, which renders the
> panel against a served fixture and checks what Save posts back.

> **Related:** [SPEC031](SPEC031-play-frequency.md) for the play-frequency table
> that opens underneath this panel — the readout these sliders are set against ·
> [SPEC008 §6](SPEC008-database-schema.md#6-listener-state--class-d) for
> `listener_preferences`'s own schema · [SPEC009](SPEC009-program-director.md) supplies
> the log-scale formulas this panel's readouts use (`[SPEC-DIR-110]`/`[SPEC-DIR-115]`)
> and the master time-scale dials this document does not touch (`[SPEC-DIR-118]`) ·
> [GUIDE001](../GUIDE001-lineage-and-lessons.md) supplies the migration count
> establishing this was real, used functionality (`[GDE-BMK-020]`) and the
> boundary §6 explains (`[GDE-LES-080]`) · [SPEC009 `[SPEC-DIR-130]`](SPEC009-program-director.md)
> for the occasion registry §4 turns into an editing surface

---

## 1. The data model — reused, not rebuilt

**`[SPEC-PREF-010]`** `listener_preferences` (`subject_kind` ∈
{`recording`,`artist`}, `subject_id` = mbid, `rotation`/`recovery`/
`restraint` REAL, `updated_at`) already existed, unchanged from the
MuLibPlay migration. No new table. `NULL` in any column means "not tuned,
use the Program Director's own default" (`Tuning::recording_defaults()`/
`artist_defaults()`, `player/src/director/frequency.rs`) — never a
fabricated zero, and never silently filled in server-side, so a caller can
always tell "unset" from "explicitly set to the default value."

**`[SPEC-PREF-015]`** A row absent entirely (subject never edited) reads
identically to a row present with every column `NULL` — `get_preference`
returns three `None`s either way, so nothing calling it has to special-case
"no row yet" against "row exists, nothing tuned."

## 2. The read/write contract

**`[SPEC-PREF-020]` `GET /preference/:kind/:id`** → `{rotation, recovery,
restraint, defaults: {rotation, recovery, restraint}}`, `kind` ∈
{`recording`,`artist`}, `400` otherwise. Every field independently
nullable; `defaults` always concrete, from `frequency.rs`'s own constants —
not re-typed as a second copy of them.

**`[SPEC-PREF-025]` `POST /preference/:kind/:id?rotation=&recovery=&restraint=`
— a three-way query, not a plain value set.** A field **absent** from the
query string is left exactly as stored; **empty** (`?rotation=`) resets it
to "use the default" (`PlayerStore::reset_preference`); a **number** sets
it (`PlayerStore::set_preference`). This is why the query is read as a
plain string map (`axum::extract::Query<HashMap<String,String>>`) rather
than a typed extractor, which would collapse "absent" and "empty" into the
same value and make a client unable to say "leave rotation alone, but
clear restraint" in one request.

**`[SPEC-PREF-030]` `set_preference`'s own `COALESCE`-based upsert cannot
clear a field — that is deliberate.** A caller passing `None` for one field
means "this save did not touch it," so a slider drag on rotation alone
must never blank a restraint value it never looked at. `reset_preference`
is the one operation that actually writes `NULL`, and it is always a
separate, explicit call.

**`[SPEC-PREF-035]` A successful write requests a Director reload the same
way `POST /library/reload` does** (`control.rs`'s `reload_library` —
`ui.controls.lock().reload_requested = true`). One request both saves the
value and lets the running engine pick it up on its own next refill; the
client never has to make a second call.

## 3. Where it's reachable

**`[SPEC-PREF-040]`** A displayed title or artist becomes a clickable link
wherever a real mbid is already known — unidentified audio or an
uncredited artist stays plain text, the same case that already leaves the
name itself unbadged (`[REQ-VIS-120]`). In scope: the Lempi skin's
now-playing row, queue, and history table; the MuLibPlay skin's
now-playing row and queue. Out of scope, named rather than silently
dropped: the WinAmp skin, whose marquee concatenates artist and title into
one scrolling string with no separate DOM node to link — a materially
bigger restructuring of a skin this feature was never asked to touch.

**`[SPEC-PREF-045]` One shared panel, built once in `core.js`, not a
bespoke editor per skin.** No modal/dialog convention exists anywhere in
this codebase — every interactive surface is either a `hidden`-toggled
sibling `<section>` (the Lempi skin's own `panel-main`/`panel-settings`/
`panel-history` switch) or an inline card list (`review.html`). MuLibPlay's
skin has neither kind of infrastructure to extend. Rather than invent two
different mechanisms, `Lempi.editPreference(kind, id, label)` builds a
single floating panel, lazily, appended to `document.body`, restyleable by
any skin's own CSS via `.pref-panel`/`.pref-box`/`.pref-link` but requiring
no per-skin markup.

**`[SPEC-PREF-050]` Rotation and recovery are shown as a human duration,
restraint as a human multiplier — not the raw log-scale float.** Client-side
readouts reuse `frequency.rs`'s own formulas (`10^v` hours; `10^-v`) for
display only; the server remains the sole place a value actually takes
effect, so a display bug here can misrepresent a number but never change
what gets stored.

## 4. The specials — MuLibPlay's other editable per-track values

MuLibPlay's track editor carried more than the three tuning sliders §2
describes. Beside them sat a **Profanity** slider, and each track's
`occasions` string held the hardcoded tags `[C]`, `[W]`, `[S]` and `[K]` —
Christmas, Winter, Summer, Kids' Songs. Lempi inherited the tagging (41
christmasy recordings, 140 for children, a handful wintry and summery) and
`tools/load_occasions.py` made the curves act again, but there has been
nothing anywhere to tag a *new* recording, or to correct an old one. This
is the same gap §1 describes for `rotation`/`recovery`/`restraint`, one
table over.

**`[SPEC-PREF-080]` The list of specials is `listener_occasions`, not a list
in the panel's own code.** The registry a listener is offered and the
registry the Program Director actually weighs by are the same rows, so the
two cannot drift into disagreeing about what exists. Adding a special stays
what [SPEC009 `[SPEC-DIR-130]`](SPEC009-program-director.md) already
promised — rows in two tables, no edit to the engine — and now no edit to
`core.js` either. One column is new: `listener_occasions.label`, what a
person is offered the characteristic as. The label tracks the characteristic
rather than departing from it — "Christmas" for `user.christmas` — so that
a person reading the panel and a person reading `listener_occasions` are
looking at the same word. The column exists because the two *can* differ
("Children's", which `user.childrens` cannot spell), not because they
should. A `NULL` label falls back to the characteristic's own last segment,
title-cased.

**`[SPEC-PREF-082]` Profanity is registered as a one-point curve at
×1.0.** MuLibPlay stored it and never read it — nothing in `musicdirector.cpp`
consulted `profanity` at all — so a neutral multiplier reproduces that
behaviour exactly, while stating it rather than leaving it an accident.
`[K]` set the precedent: it was never seasonal either, and expresses as a
one-point curve today. The value of doing it this way is that
"play the explicit ones a quarter as often" then costs one number
(`load_occasions.py --profanity 0.25`) instead of a feature.

It **does** carry inherited values, recovered rather than
migrated. `migrate_mulib.py` dropped all of them by listing the field among
its dead ones, justified as "NULL for all 8,116 rows" and citing
`[GDE-BMK-040]` — a claim audited on 2026-09-10 and found wrong for exactly
this field and `lyrics`, since 69 MuLibPlay tracks carry a real non-zero
value between 0.001 and 0.874. `[GDE-BMK-040]` itself covers nine columns
and never named either; the migration script overreached in citing it.
`tools/backfill_profanity.py` restores them: every one of the 69 carries an
`mbidRecording` that resolves in the catalogue, so it is a join and not a
re-derivation — no decode, no fingerprint, no `sig` bridge.

**`[SPEC-PREF-083]` `user.spiritual` is the first special with no MuLibPlay
ancestor at all, and every existing recording reads 0.0 for it by carrying
no row rather than a zero.** It is the real test of whether "a new special
is only rows" holds: registered exactly as profanity is, a one-point curve
at ×1.0, and nothing in the engine, the routes or the panel knows its name.
An absent value already *is* 0.0 everywhere it is consulted — the occasion
multiplier is `1 + value × (curve − 1)`, so 0.0 ignores the curve exactly —
and writing 8,116 explicit zeros would cost a row per recording while
destroying the one distinction this whole panel is built on: "nobody has an
opinion" against "somebody said no". A value becomes a row the moment
someone sets one, and travels from there (§7's sync).

**`[SPEC-PREF-084]` A recovered value goes to `flavor`, not to
`listener_characteristics`.** `source='inherited:mulib'`, positive class and
complement, exactly as the same migration wrote the four occasion tags
beside it — because `listener_characteristics` asserts "this listener set
this by hand" (`[SPEC-PREF-085]`), and a six-year-old migrated value is not
that. Getting this wrong would have made every recovered rating
un-resettable: Reset drops the listener's own row and falls back to the
inherited one, and there would have been nothing to fall back to. Applied
2026-09-10 to the desktop and to `lempipi`, 69 ratings each, reaching 69
radio passages on both.

**`[SPEC-PREF-085]` A listener's own value lives in `listener_characteristics`,
not in `flavor`, and overrides rather than merges.** `flavor` is the
catalog's, and once `tools/split_database.py` has run it is ATTACHed
**read-only** by the player (`[PI-DB-020]`) — so on the installation this
feature matters most on, the player physically cannot write it. It is also
the right side on the merits: this is a person's judgment about their own
music, Class D like every other `listener_*` table, and it is backed up and
split with them. `Director::load()` reads `flavor` first and lays these over
it, so a listener who says "not really a Christmas song" about something the
migration tagged as one wins — the alternative would be a panel that accepts
an edit the engine then ignores.

**`[SPEC-PREF-087]` Both values are carried to the client, and Reset deletes
rather than zeroes.** `GET` reports `inherited` (what `flavor` holds) and
`value` (what this listener set, `null` when they never did) as separate
fields, which is what lets the panel read "100% (inherited)" rather than
implying somebody chose it. Reset **deletes** the listener's row: writing
zero would be the different and permanent claim "this is definitely not a
Christmas song," and would bury what the migration knew. Writes ride the same
three-way query `[SPEC-PREF-025]` defines, under keys shaped
`special:<characteristic>:<class>` — split at the first colon, since a
characteristic contains a dot (`user.christmas`) and neither field ever
contains a colon.

**`[SPEC-PREF-088]` Specials are per recording, and an artist is refused
rather than silently ignored.** `flavor`'s own `subject_kind` admits only
`recording` and `passage`, and an occasion is a property of a song, not of
everything a performer ever recorded. `GET` on an artist returns an empty
list rather than omitting the field, so the panel has one shape to render;
`POST` of a special against an artist is a `400`.

## 5. Naming what is being edited

**`[SPEC-PREF-090]` The heading names the recording, its artist and its
release, and the server decides all three.** MuLibPlay's own track editor
headed its page "*name* by *artist* from *album*", and that is the phrasing
kept. It is answered by `GET /preference/:kind/:id` rather than passed in by
whichever surface was clicked, because those surfaces do not know the same
things — the queue carries no album, the Lempi skin's runner-up list carries
only a title — and a panel should not be better or worse informed depending
on where it was opened from. The caller's own label still fills the heading
until the fetch lands, and remains if it fails. Either half is dropped when
the library does not know it: an uncredited artist and a recording on no
release are ordinary (`[REQ-VIS-120]`), not a gap to paper over. The artist
and release expressions are the ones a history row already uses, tie-break
included, so a recording that appears on thirty releases is named the same
way in both places.

## 6. What this does not do

**`[SPEC-PREF-060]` This does not relitigate `[GDE-LES-080]`.** GUIDE001
records this project's own retrospective judgment that direct
slider-tuning was the wrong *primary* way for a listener to express taste
going forward — "naming six songs beats tuning eleven sliders" — favoring
the exemplar/seed-track model instead. That lesson is about Like/Dislike/
Taste, a genuinely separate system this document does not touch. What this
restores is narrower and already-designed-for: `listener_preferences` was
real, used MuLibPlay functionality that Lempi's own schema already carries
forward, with nothing anywhere to reach it. Building an editor for data
already being carried is not the same claim as choosing sliders over seeds
as the primary preference mechanism.

**`[SPEC-PREF-065]`** The two global `listener_settings` master time-scale
multipliers (`[SPEC-DIR-118]`) are a different, whole-library control —
not per-subject, not touched here.

---

## 7. Syncing what was edited here

**`[SPEC-PREF-098]`** Everything this panel writes —
`listener_preferences`, `listener_characteristics`, and the
`listener_occasions`/`listener_occasion_points` registry a special is
meaningful against — reconciles between installations in one action, and
[SPEC030](SPEC030-preference-sync.md) is where that is specified rather
than here. The division is the one already in force: this document is the
editing surface, that one is what happens when two installations have each
used it.

## 8. Open

**`[SPEC-PREF-070]`** The WinAmp skin (§3) — deferred, not designed against;
would need its marquee restructured to carry artist and title as separate
nodes before it could opt in at all.

**`[SPEC-PREF-095]`** A special set here reaches the **occasion** layer only.
Flavor distance (`[SPEC005]`) reads `flavor` directly, so a listener's own
value does not move a recording in flavor space, and the complement class
the migration wrote (`not_christmasy`) is left as it was. Correct for what
this edits — [SPEC009 `[SPEC-DIR-130]`](SPEC009-program-director.md) keeps
seasonality a time layer rather than a flavor dimension on purpose — but it
does mean these two readings of the same characteristic can disagree, which
no code currently notices.

---

---

**Traceability:** `[SPEC-PREF-010..098]` · derives `[REQ-VIS-285]`,
`[REQ-VIS-290]`, `[REQ-VIS-305]`, `[REQ-VIS-310]` · synced by
`[REQ-VIS-315]` (`[SPEC030]`) · reuses
`listener_preferences` (`[SPEC008]`) and the `Tuning` defaults/formulas
(`[SPEC009]` `[SPEC-DIR-110]`, `[SPEC-DIR-115]`) · adds
`listener_characteristics` and `listener_occasions.label` (`[SPEC008]`) ·
extends the occasion registry (`[SPEC-DIR-130]`, `[SPEC-DIR-134]`) into an
editing surface without changing how a curve is evaluated · restores, does
not replace, `[GDE-BMK-020]`'s migrated MuLibPlay data; does not touch
`[GDE-LES-080]`'s Like/Dislike/Taste scope
