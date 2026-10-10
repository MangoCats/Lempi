# SPEC062: Named Programmes

**Design Specification — Tier 2 · written 2026-10-09, from the maintainer's decisions that day · step 1 built 2026-10-09; steps 2–4 not yet**

A node's listening day is shaped by its programme: the time slots that come on
air through the day, and the seed recordings that give each its sound. Until
now a programme was not a thing anyone could name, keep, send or receive. Each
node held eight time slots, the hub held its own, and nothing moved them: the
phone ran for two weeks with none at all, and the only remedy was a hand edit
of its listener file (FLEET001). This specification makes a programme a named
thing the hub keeps in a library, assigns to nodes and sends, and that a node
can offer back.

> **Related:** [SPEC023](SPEC023-domain-vocabulary.md) `[ENT-PROGRAMME-010]`, `[ENT-SLOT-010]`, `[ENT-SEED-010]` (the words) · [SPEC009](SPEC009-program-director.md) `[SPEC-DIR-140..185]` (what a programme does) · [SPEC049](SPEC049-mesh-membership-and-trust.md) `[SPEC-MTR-040]` (which this refines) · [SPEC046](SPEC046-star-sync.md) (which never merges a programme)

---

## 1. The maintainer's decisions, 2026-10-09

**`[SPEC-PGM-010]` A programme is a named, complete day**: its time slots and
their seeds, run whole `[ENT-PROGRAMME-010]`. Not one time slot. The word is
defined once, in SPEC023, so it cannot drift back into meaning both.

**`[SPEC-PGM-020]` The hub keeps a library of separate programmes.** Each is
stored under its own name, independent of the others, with the versions it has
had. A programme a node offers under a name the hub does not have is stored as
a new programme.

**`[SPEC-PGM-030]` An offer under a name the hub already has waits for a
person at the hub.** It is shown in Vipunen's console beside the hub's own
version, and becomes the hub's version only when someone accepts it. Accepting
is a complete replacement, never a merge of slots or seeds. The version it
replaces is kept.

**`[SPEC-PGM-040]` Each leaf node runs exactly one programme, assigned at the
hub, and follows it.** From the assignment on, every new version of that
programme is sent to the node.

**`[SPEC-PGM-050]` A node asks before its programme is replaced.** When a
programme arrives that differs from the one the node runs, the node's own
interface asks whether to replace it. Replacing is complete. Keeping its own
leaves the node as it was, and the node is not asked again about that version;
a newer one asks again. A node that runs no programme at all also asks: one
answer, and the arrival is not imposed on anyone.

**`[SPEC-PGM-060]` This refines `[SPEC-MTR-040]`, and keeps its core.**
Programmes are still each node's own, and star sync still never merges them.
What changes is the "by hand": copying is now an assignment at the hub and a
confirmation at the node, both by a person, instead of an edit of a database.

---

## 2. Identity and difference

**`[SPEC-PGM-100]` A programme's identity is its name.** Names are compared
without regard to case or surrounding space, so "Household" and "household " are
one programme; the name is shown as it was first written.

**`[SPEC-PGM-110]` "Differs" means different content, never a different
date.** A programme's content is its slots, in start-time order — name and
start of each — and each slot's seeds in their order. Its fingerprint is
SHA-256 over that content in one canonical form. Two copies with the same
fingerprint are the same programme, wherever they came from and whenever they
were saved; a re-send of an unchanged version asks nothing.

**`[SPEC-PGM-120]` A version is a fingerprint the hub has kept**, numbered in
the order the hub first held it. A node records which programme it runs, the
fingerprint it runs, and where that came from: the hub's version *n*, or edited
on the node since.

---

## 3. Where it is kept

**`[SPEC-PGM-200]` On a node, in its listener file**, beside what it already
holds there, and like it, backed up and never merged `[REQ-LIB-160]`:
- `listener_programs` and `listener_program_seeds`: the time slots and seeds of
  the programme it runs, as today `[SPEC-VOC-020]`;
- a new `listener_programme`: one row, the name, fingerprint and source of what
  it runs;
- a new `listener_programme_pending`: what has arrived and not been answered, or
  was declined, with its full content, so the question survives a restart.

**`[SPEC-PGM-210]` On the hub, in Vipunen's sidecar** (`library.console.db`),
since the library is the hub's and not any one listener's: the programmes, their
versions with full content, the offers waiting, and the assignments — which
programme each node runs, what was last sent to it, and what it last said it
runs. The hub's own player is a node like any other, assigned like the rest.

**`[SPEC-PGM-220]` Star sync leaves all of it alone.** The node tables stay
`LOCAL` in its rules, as `listener_programs` is now; the sidecar is not synced at
all.

---

## 4. How it travels

**`[SPEC-PGM-300]` Over the members' channel**, which already carries a member's
updates and the hub's roster `[SPEC-MTR-010]`, signed and per member:
- `GET /member/programme` — the current version of the programme assigned to
  this member, or nothing;
- `POST /member/programme/state` — what the member runs now, and whether it
  replaced or kept on the last arrival;
- `POST /member/programme/offer` — a programme offered to the hub.

**`[SPEC-PGM-310]` A member asks; the hub does not reach in.** The phone already
asks the members' channel when its Mesh screen opens; a player asks at start and
hourly, as it asks the clock for its offset `[SPEC-DIR-180]`. A player has no
client for the channel yet; this is the first thing it fetches there.

---

## 5. Where a person does it

**`[SPEC-PGM-400]` On a node: a Programme page in the player, `/programme`.**
Every node has it, in every skin and in the phone's app, since the app shows
the player's pages. It shows the programme the node runs — its name, and
whether it is the hub's version *n* or edited here — with its time slots, the
range each is on air for (from its start to the next slot's), and their
seeds, each shown as the recording, its artist and its album. A person edits
it there: the programme renamed; slots named, timed, added and removed; seeds
removed and reordered. "Offer to the household" (step 3) sends it to the hub,
under its own name or a new one.

**`[SPEC-PGM-405]` An edit can be saved as a new programme.** *The maintainer's
note, 2026-10-09.* **Save** keeps the programme's name: it is an edit of that
programme, and offered to the hub it would wait there as a replacement for the
hub's version of that name `[SPEC-PGM-030]`. **Save as a new programme…** asks
for another name, so the programme the edit started from stays unchanged
wherever else it is kept — above all in the hub's library — and the node runs
the new one, which an offer would add to the library under its own name
`[SPEC-PGM-020]`. On the node either is a complete replacement of what it runs,
and makes it the node's own (`source = 'node'`).

**`[SPEC-PGM-410]` The question is asked where the node is used.** An arrival
that differs shows a banner in the player, as a new Wi-Fi network's "Keep this
network?" does: *"The household sent Household (version 4). Replace this
node's programme?"* with the differences one tap away, slot by slot, and
**Replace** or **Keep this node's**. On the phone and the touchscreen node that
is the screen in front of someone; on a node with no screen, it waits in its
web page until someone answers.

**`[SPEC-PGM-420]` Seeds are added where a recording is heard or found.** The
shared preference panel, which opens from any linked title in now playing, the
queue and history `[SPEC-PREF-045]`, gains a row of the programme's time slots
to tick ("Seed of: ☐ Cool ☑ Mellow"), and Browse's track rows gain the same.
One panel in `core.js`, so every skin has it at once.

**`[SPEC-PGM-430]` The picker leads to the page.** Each skin's picker lists the
time slots of the programme the node runs, under its name. Its empty state,
"no programmes configured", becomes "No programme — choose one", a link to
`/programme`.

**`[SPEC-PGM-440]` On the hub: a Programmes page in Vipunen's console.** It
lists the library: each programme, its versions, and which nodes run it — the
current version, an older one, a question waiting, a version kept back, or
edited on the node. A person assigns a programme to a node there, and answers
offers: accept as the new version, keep it under a new name, or decline.

---

## 6. Moving to it

**`[SPEC-PGM-500]` The eight time slots every node has become one programme.**
The hub's set is stored as the library's first programme, version 1, named
**WKMP** — the maintainer's name for it, 2026-10-09. Each node whose slots have that fingerprint is
recorded as running it; a node whose slots differ is recorded as running its
own, unnamed until someone names or replaces it. Nothing on any node changes in
the move.

**`[SPEC-PGM-510]` Built in four steps, each usable on its own:**
1. the node: its programme recorded and named, the `/programme` page, and the
   picker's link — editing on one node, nothing travels;
2. the hub's library and console page, assignment, the members' channel, and
   the node's question — the hub's programme reaches the nodes;
3. offers from a node, and the hub's inbox;
4. seeds from the preference panel and Browse.

**`[SPEC-PGM-520]` Step 1, as built 2026-10-09.**
- The node's record is `listener_programme`, made at the first read:
  unnamed and the node's own, with the fingerprint of the slots it already
  had. Slots changed behind it, by a tool or by hand, read as the node's own.
  The player now creates the slot tables too, on a listener file that never
  went through the split.
- The fingerprint is checked against Python's standard JSON form
  `[SPEC-PGM-110]`: computed both ways over the hub's eight slots, it was the
  same value.
- A save is one transaction. A kept slot keeps its id, so history still
  names it. A new slot takes an id above any a slot or `selection_decisions`
  has had, never the deleted one SQLite would reuse. The running Director
  takes the new slots at its next refill, keeping its clock offset and a
  slot held by hand unless the edit removed it.
- The page is `/programme`, linked beside every skin's picker and from its
  empty state. Its start times are 24-hour text, not a locale's time picker.
---

**Traceability:** `[SPEC-PGM-010..520]` · from the maintainer's decisions of 2026-10-09 · words in [SPEC023](SPEC023-domain-vocabulary.md) `[ENT-PROGRAMME-010]`, `[ENT-SLOT-010]`, `[ENT-SEED-010]`, `[SPEC-VOC-020]` · refines `[SPEC-MTR-040]`
