# GUIDE023: Lists That Mirror Something Else

**Development Guidance — written 2026-09-18 from a structural search for the
faults [GUIDE021](GUIDE021-echo-review.md) and
[GUIDE022](GUIDE022-the-follower-nothing-builds.md) found in the echo path**

Those two documents found one shape four times: an actuator, a build, a wire
and a gate, each declining silently one call below something whose tests
passed. This asks whether the shape is local to echo. It is not. What follows
is what a search for each variant turned up elsewhere, and what was done.

> **Related:** [GUIDE021](GUIDE021-echo-review.md) `[GDE-ECHO-371]` — the review that named the shape · [GUIDE022](GUIDE022-the-follower-nothing-builds.md) `[GDE-ECHO-379]` — the build and the wire · [GOV001](GOV001-document-hygiene.md) `[GOV-DOC-010]` — the governance this checks · `build/verify-targets.sh` — the gate

---

## 1. The shape, stated once

**`[GDE-ARC-031]` A list that mirrors something else fails silently, in the
direction of checking less.** Every finding below is one list, hand-written,
whose correctness depends on a thing it cannot see.

The failure is always the same and is never noisy. The list omits an entry;
the omission is invisible because the list *is* the definition of what gets
checked; and the omission's direction is always towards less coverage, never
more. Nothing fails. The gap is discovered by someone reading, or by the
thing it was guarding going wrong in production.

**The fix is always the same too: derive the list from the other side rather
than restating it beside it.** Not a better list — a list nobody writes.

Three of the four below had a correct example already in the tree, which is
the useful part: the project knows how to do this and does it inconsistently,
so the fix is to apply its own pattern rather than to invent one.

---

## 2. Eight tests that assert what they do not check

**`[GDE-ARC-032]` `web/mod.rs` has eight tests named for a two-sided property
and checking one side.** The largest instance, and the only one that had no
guard at all.

`the_wifi_controls_reach_the_routes_the_router_serves` and seven siblings do
this, against a hand-typed list of paths:

```rust
assert!(skin.js.contains(route), "lempi's skin.js never asks for {route}");
```

Nothing in any of them inspects the router. Rename a path in `router()` and
all eight still pass, because the JS still contains the string they were told
to look for. That is `[GDE-ECHO-378]` with the router as the actuator: the
request is checked, the delivery is not.

**The project had already invented the fix and used it twice.**
`REVIEW_QUEUE_ROUTE` and `SEGMENT_QUEUE_ROUTE` are constants, registered by
the router and asserted into the JS — one name, both sides, rename-proof.
Two routes of eighty-four.

`every_path_the_pages_fetch_is_a_route_the_router_serves` now derives both
sides: it scans `.route(...)` out of the router source, resolving named
constants, scans `fetch(...)` out of every page and skin, and matches them
under axum's own rules. Verified by renaming a path and watching it fail —
and verified *not* to fail on renaming a `:param`, which serves identical
URLs. It asserts a floor on both counts, so a scan that reads nothing cannot
pass by finding no fault `[GDE-ECHO-547]`.

No live breakage was found: all 47 literal fetch targets resolve today. **The
defect was the absent guard, not a present fault** — worth saying plainly,
because the eight tests had made it look guarded for as long as they have
existed.

---

## 3. The gate, again, two stages further down

**`[GDE-ARC-035]` The same piped status was still in the script after the
same fault was fixed three stages above it.** Found by grepping for the shape
rather than trusting the repair.

Stages A, B and C were fixed by `[GDE-ECHO-386]`. The bounded-decode gate at the
bottom of the same file still read:

```sh
( cd … cargo run … --bin memcheck -- --file "$LEMPI_LONG_FILE" 2>&1 | tail -4 ) || fail=…
```

`tail`'s status, and `tail` succeeds at printing nothing — the third disguise
CLAUDE.md §6 lists by name. A `run_step` helper now takes the command's own
status and shows the output separately.

**Why it concentrates here is structural, and worth knowing.** Every
`#!/bin/bash` script in `build/` sets `pipefail`. All three `#!/bin/sh`
scripts cannot: POSIX `sh` has no such option. And all three are the ones
whose job is *measuring* — `verify-targets.sh`, `tools/echo_skew.sh`,
`tools/drift_sample.sh`. The risk lands exactly where truthfulness matters
most, by an accident of which interpreter each was written for. A measuring
script in `sh` has to capture status by hand, every time, or it will read a
filter's.

Smaller, same family: `tools/echo_skew.sh` exits 1 on a clean run, because
its last statement is `[ "$i" -le "$N" ] && sleep "$GAP"`, false on the final
iteration. It fails safe rather than open, and is left as a note here rather
than fixed in passing.

---

## 4. A limit enforced three times and told to nobody

**`[GDE-ARC-033]` `ECHO_TRIM_LIMIT_MS` had three copies in two languages.**
The engine's command handler clamped to it, the store clamped to it again on
load, and the skin's HTML hardcoded `min="-2000" max="2000"`.

Two models of one quantity `[GDE-ECHO-375]`, and the smallest instance here —
but notable because every neighbouring control already does it correctly.
`SkipShape` ships `fade_max_ms`, `lead_min_ms` and `lead_max_ms`; the fader
ships `fader_min_db`, "sent so the control can shape itself around the
engine's floor instead of keeping its own copy of the number"
`[REQ-AUD-156]`. The delay trim was the one that kept its own copy, and it is
the only such duplication in the skin HTML — every other bound there is a
natural range.

`EchoNode` now carries `trim_limit_ms` and the skin sets the input's bounds
from it. `EchoNode::default()` is written out by hand for that one field, so
a snapshot built by *any* path — the engine's `publish`, a test fixture, a
future caller — carries the real limit rather than a zero that reads as "no
limit".

---

## 5. The governance gate could not see eleven files

**`[GDE-ARC-034]` `check_docs.py` discovered documents from a hand-written
glob per folder, and the comment above it records the list being extended
three times.** Each extension came after a folder had already been invisible
for a while.

Measured: eleven files outside it, carrying **56 tags and 37 markdown links**
— `tools/README.md` (27 tags), `README.md` (29 links), the fixture READMEs,
`build/`, `data/`, `pictures/`, `reports/`, and **`CLAUDE.md` itself**, which
is where the instruction to run this checker is written down.

Discovery is now a walk with an explicit skip list, so the default is
*checked* and an exclusion has to say why. What that surfaced, once run:

- Three genuine tag-definition collisions, all the same shape — a plain
  citation that happens to *begin a line*, which the house rule reads as a
  definition. Fixed by moving each mid-line, per that rule, with no
  renumbering.
- Four path citations that are heuristic false positives. Two were prose
  placeholders naming a script that was never meant to exist, reworded. Two were
  `player/target/release/lempi` — a **build output**, absent from the tree by
  design and cited correctly by a how-to. `cited_paths` now skips `target/`
  segments: the prefix list was written narrow to avoid false positives and
  could not anticipate a case it was never shown.
- One stale claim of the kind the checker exists to catch.
  `fixtures/fade/README.md` said the fade curves are what `[SPEC-AUD-040]`
  specifies. That tag is struck through in GOV001's own registry as a dead
  entry from `SPEC001-audio-engine.md`, deleted 2026-08-30 — and every
  *checked* document citing it says so explicitly. The claim outlived the
  document by two and a half weeks in the one file nothing read.

After the triage: 69 warnings and 0 errors, the same as before, over eleven
more files.

---

## 6. What was already right

Recorded deliberately, because "this codebase has a systemic problem" would
be the wrong conclusion. Three of the four above had a working example
sitting beside them.

- **`Settings::KEYS` is the exemplar.** It looks like an unguarded hand-list
  and is not: `every_setting_survives_a_round_trip` builds an exhaustive
  struct literal — which the compiler forces you to update — sets every field
  to a non-default value, round-trips through `value_of`/`set`, and compares
  the whole struct against one built from `default()`. A field missing from
  `KEYS` therefore *fails* it. Compiler-forced completeness on one side, a
  whole-value comparison on the other. This is the pattern the rest should
  copy.
- **Vipunen's link probes rather than assumes.** `browse.js` fetches
  `/vipunen/available` before offering it — the correct handling of a feature
  gate, and the contrast that makes `[GDE-ECHO-379]` a defect rather than a
  style choice. The MPD backend block does the same with `guest_available`.
- **`check_docs.py` had already fixed this bug class once**, for `--strict`
  exiting 0 on a warning-shaped error.
- **`verify-skins.js` genuinely drives the skins** in a real DOM and records
  real POSTs. Its `browse OK 0 rows rendered` line reads like a hollow pass
  and is not — `rows()[0]` is dereferenced and two ids are posted; the zero
  is the count after a deliberate failure case clears the list. A misleading
  summary number in a project bitten by exactly that reading, and nothing
  more.

---

## 7. What the search cost, and what it is worth

**`[GDE-ARC-036]` Two of this review's own findings were false, and both were
the reviewer's tooling rather than the code.** Recorded because a structural
search produces them at a predictable rate and a report that hides them
invites the next reader to trust the method further than it earns.

A scanner that read only `.route("…")` string literals reported
`/review/queue` as served by nothing — a false alarm caused *by* the good
pattern, since that route is registered through a named constant. And a
dangling-tag check that stripped `*_> ` but not `#` read every
heading-defined tag as undefined, producing four dangling tags of which none
was.

Both were caught by checking a claim against the source before reporting it.
The general rule is the one this whole family is about, pointed at the
reviewer: **a scan that cannot see a construct reports its absence as a
fault, and an absence is exactly what these searches are looking for.** Assert
a floor on what the scan found, and read one hit by hand before believing the
misses.
