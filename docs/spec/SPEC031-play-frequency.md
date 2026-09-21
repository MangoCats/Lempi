# SPEC031: How Often This Has Actually Played, and Who Chose Each Play

**Design Specification — Tier 2 · Built**

[SPEC029](SPEC029-listener-preference-editing.md) put three sliders in front
of a listener. It did not tell them what they were adjusting *against*. The
question a person is really answering at that panel is "do I want to hear
this more or less than I have been" `[REQ-VIS-300]`, and the second half of
that sentence was missing: nothing anywhere said how much they *had* been
hearing it. This is the readout that supplies it — five rolling windows of
play counts for whatever the preference panel currently has open, broken
out by who or what put each play on.

> **Status.** Built 2026-09-04 (commit `f300579`), per `[REQ-VIS-300]`.
> `PlayerStore::play_frequency` (`player/src/db/player_store.rs`), the
> `GET /play-frequency/:kind/:id` route (`player/src/web/preference.rs`),
> `loadFreqPanel`/`freqPanelSlot` (`player/src/web/core.js`), the
> `listener_play_history.selected_by` column, and `QueueEntry::selected_by`
> with its three assignment sites.
>
> **Specified 2026-09-10, after the fact.** The commit that built this named
> a SPEC031 that was never written, and `[REQ-VIS-300]` went six days
> undefined alongside it. This document is reconstructed from the
> implementation and that commit message rather than from a contemporaneous
> design — where it states a reason, that reason is the code's own comment
> or the commit's own words, not a rationale invented later to fit.

> **Related:** [SPEC029](SPEC029-listener-preference-editing.md) for the
> panel this sits under and the tuning it informs ·
> [SPEC017](SPEC017-what-counts-as-a-play.md) for what put a row in
> `listener_play_history` at all, which §5 does not relitigate ·
> [SPEC008 §6](SPEC008-database-schema.md#6-listener-state--class-d) for
> that table's own schema · [SPEC009](SPEC009-program-director.md) for the
> programmes whose names become row labels here

---

## 1. What is counted, and against whose clock

**`[SPEC-FREQ-010]` Five rolling windows, narrowest first: the past 24
hours, 7 days, 30 days, 365 days, and all time.** Rolling, not
calendar-aligned — "the past 24 hours", never "today", so the answer does
not lurch at midnight. Roughly geometric spacing is what makes the row
worth reading: a recording heard twice this week and a recording heard
twice since 2020 have the *same all-time count*, and only the narrow
columns separate them. Narrowest first, so the eye reads left to right from
*lately* to *ever* and the shape of the row — front-loaded or flat — is the
answer before any single number is.

**`[SPEC-FREQ-015]` All time rides the same loop as the other four, as a
sentinel that skips the bound test rather than one that passes it.**
`WINDOWS`' fifth entry is `i64::MIN`, and the comparison is guarded
(`*window != i64::MIN && ...`) rather than evaluated — `now - i64::MIN`
would overflow. So all five columns accumulate in one loop body over one
fetched row-set, and all-time cannot drift from the columns beside it by
being counted somewhere else; but it is a short-circuit, not an unbounded
comparison, and the guard is load-bearing rather than defensive.

**`[SPEC-FREQ-020]` Counts are computed at read time against `now`, never
stored and never cached.** The answer legitimately changes with the clock
while nothing plays at all: a play drops out of "past 24 hours" an hour
after it drops out of being recent. A cached count would be wrong in a way
nothing invalidates.

## 2. Who chose this play

**`[SPEC-FREQ-030]` Provenance is recorded when a passage is *queued*, and
never inferred when it is read back.** `QueueEntry::selected_by` is set at
exactly three places, and is `None` everywhere else:

| Value | Set where | Means |
| :--- | :--- | :--- |
| `"user"` | `control.rs`, the manual queue route | The listener chose this one directly |
| the programme's own name | `session.rs`, the Director's pick | That programme selected it |
| `"auto"` | `session.rs`, both the Director's pick with no programme in force and the random-radio refill | A genuine Director selection, steered by no programme |

It rides unchanged through to `record_play`, which writes it to
`listener_play_history.selected_by` (added by the established idempotent
`ALTER TABLE` pattern). Deriving it at read time would mean guessing from a
timestamp which programme was probably in force, which is exactly the kind
of plausible reconstruction this panel exists to replace.

**`[SPEC-FREQ-035]` `None` and `"auto"` are different answers and must
never be collapsed.** `"auto"` is a real selection event that had no
programme behind it. `None` is *no selection event to report at all* — a
resumed entry, a reconstructed one, or a backend that does not track this.
Folding them together would invent Director picks out of rows that record
nothing about how they were chosen.

**`[SPEC-FREQ-040]` Nothing is back-annotated, ever.** Every row written
before the column existed stays `NULL` permanently. A library that has been
playing for years is mostly such rows, and that is reported as what it is
rather than being filled in with a story nobody verified. This was asked
for explicitly when the feature was built, and it is the same posture
`[SPEC-FREQ-030]` takes one step earlier: this panel reports, it does not
reconstruct.

**`[SPEC-FREQ-045]` A backend that does not track provenance still records
the play.** Both MPD paths (`mpd_backend.rs`, `bin/mpd_direct.rs`) pass
`None`. The play is real and counts; only its attribution is unavailable,
and §6 holds that open rather than pretending it is closed.

## 3. The rows, and why *All* is a ceiling

**`[SPEC-FREQ-050]` *All* counts every play in the window whatever its
provenance, including the `NULL` rows.** So *All* is never less than any
other row and is usually more. That property is the whole reason a library
full of pre-column history is still worth showing this panel for: the top
row is always a true answer, however little is known about the rows beneath
it.

**`[SPEC-FREQ-055]` *User* is always present, even at all-zero.** "Never
once put on deliberately" is itself an answer, and it is precisely the
reading — a high *All* over a zero *User* — that tells a listener this is
something the Director keeps choosing and they never do. Suppressing an
empty *User* row would hide the most actionable case the panel has.

**`[SPEC-FREQ-060]` Programme rows are dynamic: one per programme that has
actually selected this subject, sorted by its own all-time count.** A
programme that has never chosen this recording gets no row rather than a row
of zeros — with eight programmes defined, six empty rows would push the two
that matter off the bottom. Sorting by all-time count puts the most
relevant first. `"auto"` renders as `Auto`, the only cosmetic difference
between a stored value and its label.

**`[SPEC-FREQ-065]` One fetch of every matching `(selected_by, played_at)`
pair, aggregated in Rust.** Not a cross-tab SQL query, which would have to
know the distinct programme names in advance — and those are data, added and
renamed by a listener. The scale justifies it: one subject's play history is
a handful of rows even on a well-used appliance, the same argument
`remote_flags.py` already makes for `listener_flags`.

## 4. Reaching it

**`[SPEC-FREQ-070]` `GET /play-frequency/:kind/:id`**, `kind` ∈
{`recording`,`artist`}, `400` otherwise. Registered unconditionally, not
behind `vipunen-support`, for the same reason the preference routes beside it
are: this is core Program Director data and the appliance needs it exactly
as much as the desktop does.

**`[SPEC-FREQ-075]` Artist mode joins through `recording_artists`**, so a
play recorded against a recording counts toward the artist credited with it
— a play is only ever written against a recording, so an artist with no
join would read as never played. The same join `library.rs`'s
`HIST_ARTIST_MBID_EXPR` uses, so the history page and this panel cannot
disagree about who performed something.

**`[SPEC-FREQ-080]` Its own route and its own fetch, never awaited by the
preference panel.** Opening the preference panel must not wait on this
`[REQ-VIS-300]`: the sliders are the thing being used, this is the readout
beside them. The table appears when it is ready, and closes when the
preference panel closes — there is no reading play frequency for a subject
whose panel is no longer open.

**`[SPEC-FREQ-085]` A response that is no longer current is dropped, not
rendered.** Each call captures a monotonically increasing token and applies
its result only if that token is still the latest. This guards a fast
double-click across two different subjects — the slower response would
otherwise land second and label one recording with another's counts — and a
response arriving after the panel has already closed.

**`[SPEC-FREQ-090]` A table, not five labelled numbers per row.** Windows
are columns, *All*/*User*/each programme are rows; a grid reads far more
directly than repeated labels. Each skin carries its own empty
`#freq-panel` slot, filled once and styled entirely by that skin's own CSS,
the same arrangement `[SPEC-PREF-045]` settled on for the preference panel.
A skin with no slot (WinAmp) silently gets no table.

**`[SPEC-FREQ-095]` A failed fetch is silent.** No error row, no empty
table — it simply does not appear, the same posture a failed preference
fetch already takes. This is a supporting readout, and an error message
about it would be more intrusive than the thing it is reporting.

## 5. What this does not do

**`[SPEC-FREQ-100]` It does not decide what counts as a play.**
[SPEC017](SPEC017-what-counts-as-a-play.md) does, for every path that writes
`listener_play_history`, and this counts exactly the rows that discipline
chose to write — thresholds inherited unchanged. A skip is absent here
because it was never written as a play there, not because this filtered it
out.

**`[SPEC-FREQ-105]` It does not adjust anything.** Read-only: it informs
the sliders beside it and never moves them. The Director already derives its
own cooldown and recovery from play history `[SPEC-DIR-110]`; a second,
implicit path from "played a lot" to "play less" would be a feedback loop
fighting the explicit one, with neither visible to the listener as the cause.

---

## 6. Open

**`[SPEC-FREQ-110]` The MPD backends record no provenance**
`[SPEC-FREQ-045]`. Every play through either path lands in *All* alone,
permanently — and permanently is the right word, since `[SPEC-FREQ-040]`
rules out filling them in afterwards. Closing this means threading
`selected_by` through both paths at the point a passage is queued, where
the information still exists.

**`[SPEC-FREQ-115]` Play history does not sync between installations, so
these counts are per-machine.** [SPEC030](SPEC030-preference-sync.md)
reconciles `listener_preferences` and `listener_characteristics`; it does
not touch `listener_play_history`, and the hourly Class-D backup is a
whole-table snapshot rather than a merge. So a listener with a desktop and
an appliance gets two partial answers to "how often do I hear this", and
the panel does not say which one it is showing. Whether they *should* pool
is genuinely undecided: it would make the number a truer answer, but
SPEC030's last-write-wins has nothing to say about two independent
append-only event streams, and merging those is a different problem from
reconciling two current values — closer to `[SPEC-DF-090]`'s territory than
to `[SPEC-PREF-105]`'s.

**`[SPEC-FREQ-120]` The windows are fixed.** 24h/7d/30d/365d/all-time are
constants in `play_frequency`, not settings. Nobody has asked for others;
recorded so that "why can I not see the past hour" has an answer other than
silence.

---

**Traceability:** `[SPEC-FREQ-010..120]` · derives `[REQ-VIS-300]` · reads
`listener_play_history` (`[SPEC008]`) under [SPEC017](SPEC017-what-counts-as-a-play.md)'s
own rule for what goes in it (`[SPEC-PLAY-*]`) · sits inside
[SPEC029](SPEC029-listener-preference-editing.md)'s panel and reuses its
slot-and-skin-CSS arrangement (`[SPEC-PREF-045]`) · labels its rows with
[SPEC009](SPEC009-program-director.md)'s programme names · specified after
the fact, 2026-09-10, six days after the code it describes
