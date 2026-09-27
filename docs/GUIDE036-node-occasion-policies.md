# GUIDE036: Node Occasion Policies

**Development Guidance — a design recorded for later: how one node may shape the household's occasions for itself, a profanity ceiling first**

Recorded 2026-09-27 from the maintainer's direction. **Designed, not scheduled.**
Nothing here changes what any node does today. It records how occasions work
now, what is shared and what is not, and the shape a node's own policy would
take. It is kept for when it is built.

> **Related:** [SPEC037](spec/SPEC037-eligibility-and-frequency.md) `[SPEC-DIR-130..137]` (the occasion multiplier) · [SPEC029](spec/SPEC029-listener-preference-editing.md) `[SPEC-PREF-080]` (the specials panel) · [SPEC046](spec/SPEC046-star-sync.md) (what is shared) · [SPEC049](spec/SPEC049-mesh-membership-and-trust.md) `[SPEC-MTR-040]`, `[SPEC-MTR-900]`

---

## 1. How occasions stand today

An occasion has three parts, and each is authored and shared differently.

| part | where | authored by | shared |
| :--- | :--- | :--- | :--- |
| the occasion (name, label, interpolation) | `listener_occasions` | `tools/load_occasions.py` | union, every node alike |
| its seasonal curve (date → multiplier) | `listener_occasion_points` | the same script, from MuLibPlay's `occasionWeight()` | union, every node alike |
| a recording's value (0–100%) | the catalogue's `flavor` (inherited), overlaid by `listener_characteristics` (edited) | the hub's catalogue; the specials panel on any node `[REQ-VIS-305]` | the catalogue from the hub; edits by last write wins, as preferences are |

**`[GDE-OCP-010]` A recording's occasion values are household data.** They
describe the music. They are edited in the specials panel on any Lempi node, and
travel to the hub and back out to every node, as recording and artist
preferences do. *Measured 2026-09-27:* seven such edits, all made on lempi02w,
and each is on the hub unchanged. Three are graded profanity values: Metallica's
"Murder One" 50%, Red Hot Chili Peppers' "Shallow Be Thy Game" 70%, and Guns N'
Roses' "It's So Easy" 50%.

**`[GDE-OCP-020]` The curves are authored in code and distributed as data.**
Their values were typed into `load_occasions.py`, which writes them as rows on
the hub. Star sync carries the rows to every node, and each Director reads them
when it loads. A changed curve needs no new player, but nothing edits one except
that script or SQL. Two nodes cannot hold different curves today either. The
merge keeps one value per point, the first node by name, and reports the
disagreement `[SPEC-STAR-030]`.

**`[GDE-OCP-030]` One term per occasion, multiplied together.** For each
occasion a recording carries, the Director applies
`1 + value × (curve(today) − 1)`, multiplies the terms, and clamps the product
at zero `[SPEC-DIR-130]`. Profanity and Spiritual are flat curves at ×1
`[SPEC-PREF-082]`, so today their values change nothing. The profanity edits
above are recorded and have no effect.

---

## 2. The design: a node's own policy

**`[GDE-OCP-100]` A node may set a policy of its own for any occasion.** The
curve says *how much this occasion matters today*, and every node shares it.
A policy says *how much of this is acceptable here*, and it is the node's
alone, as its programmes are `[SPEC-MTR-040]`. The kitchen, a child's room and
the phone may each differ. A policy is set in the node's own Settings, is never
merged, and is copied between nodes only by hand.

**`[GDE-OCP-110]` A ceiling, and a ramp below it.** For an occasion with a
policy switched on, the recording's value *v* gives a second term:

| *v* | term |
| :--- | :--- |
| above the ceiling *c* | **×0**: never chosen |
| from 0 up to *c* | from ×1 at 0 down to the node's *floor* at *c* |
| policy off | ×1: no effect |

The ramp interpolates in log space, as the curves already do, because these are
ratios `[SPEC-DIR-132]`. With a ceiling of 60% and a floor of ×0.25, the three
graded tags above work out as follows:
- "Shallow Be Thy Game", at 70%, is never chosen;
- "Murder One" and "It's So Easy", at 50%, fall to about ×0.31 and stay in rotation;
- the 69 inherited 100% tags are excluded.

A ceiling of 100% with the ramp alone reduces profanity without excluding any
of it.

**`[GDE-OCP-120]` The term stays legible.** It is multiplied after the
seasonal term and shown as its own line in "why this passage?": "Profanity 50%,
under this node's ceiling of 60%: ×0.31". Keeping each effect a single term was
the reason seasonality was not folded into the programme's target
`[SPEC-DIR-130]`, and the same reason applies here.

**`[GDE-OCP-130]` A floor below the Director's own minimum means never.** A
multiplier under about ×0.00083 takes an average passage below `min_weight`,
and that excludes it rather than making it rare `[SPEC-DIR-137]`. The floor
setting is therefore bounded above that line and says so. Exclusion is the
ceiling's job, not the floor's.

**`[GDE-OCP-140]` A node may also override a curve.** It could start Christmas
earlier, or allow children's songs in one room: its own points for an
occasion, in place of the household's, held and edited as its policy is. This
extends the maintainer's profanity example to every occasion. The same
node-local storage serves both.

**`[GDE-OCP-150]` Stored per node, and never merged.** A node-local table,
`listener_occasion_policy` (occasion, on or off, ceiling, floor), with node-local
curve points beside it, both LOCAL in star sync as programmes are. The
household's occasions and curves stay shared and unchanged, so a node without a
policy behaves exactly as today. This is the proposed answer to
`[SPEC-MTR-900]`: occasions stay shared, and a node's shaping of them is its own.

---

## 3. Open, for when it is taken up

1. **`[GDE-OCP-900]` Whether a policy governs a person's own choice.** A
   ceiling applies to what the Director picks. Whether a track a person queues
   by hand above the ceiling plays anyway, or is refused with the reason, has
   not been decided.
2. **`[GDE-OCP-910]` Whether the setting is protected.** A ceiling in a child's
   room means little if anyone at that room's Settings page can switch it off.
   Whether it needs a PIN, or can be set only from the hub, touches
   `[SPEC049]`'s membership.
3. **`[GDE-OCP-920]` A passage of several recordings.** Today the occasion term
   is read by the passage's recording. A passage crediting two recordings with
   different values would need a rule, most likely the highest value.
4. **`[GDE-OCP-930]` Editing the household's curves at all.** Today only
   `load_occasions.py` does. An editor at the hub, for the shared curves, is the
   natural companion to the node's own overrides.

---

**Traceability:** `[GDE-OCP-010..930]` · from the maintainer's direction of 2026-09-27 · touches `[SPEC-DIR-130..137]`, `[SPEC-PREF-080]`, `[SPEC-MTR-040]`, `[SPEC-MTR-900]`
