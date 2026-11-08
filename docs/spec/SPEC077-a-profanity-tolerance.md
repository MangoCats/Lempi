# SPEC077: A Profanity Tolerance

**Design Specification — Tier 2 · written and built 2026-11-08, at the maintainer's direction · built on the desktop only; not yet deployed or heard**

One slider sets how much profanity the Program Director will choose, for every programme slot at once. It is the first built form of [GUIDE036](../GUIDE036-node-occasion-policies.md)'s node occasion policy, simpler than that design: one occasion, no separate floor, and a ramp that reaches zero.

> **Related:** [GUIDE036](../GUIDE036-node-occasion-policies.md) `[GDE-OCP-100]`, `[GDE-OCP-900]` (a policy never blocks a person) · [SPEC029](SPEC029-listener-preference-editing.md) `[SPEC-PREF-082]` (profanity values, and their flat curve) · [SPEC037](SPEC037-eligibility-and-frequency.md) (the weight's terms) · `player/core/src/director/frequency.rs` (`profanity_term`), `player/core/src/director/library.rs` (`load_profanity`), `player/src/session.rs`, `player/src/web/control.rs`, the MuLibPlay and Lempi skins

---

## 1. What there was

**`[SPEC-PTL-001]` Profanity values existed and did nothing.** *Measured on the hub, 2026-11-08:* 84 recordings carry a profanity value, 69 inherited from MuLibPlay `[SPEC-PREF-082]` and 15 set by hand in the specials panel. They range from 0.001 to 0.95; 24 are below 0.05. They cover 85 of 11,540 radio passages. The occasion registers profanity as a flat curve at ×1, so no value changed a pick `[GDE-OCP-030]`.

## 2. The control

**`[SPEC-PTL-010]` One tolerance, from 0.0 to 1.0, default 1.0, for every programme slot.** It is not part of a programme, so changing slot, by hand or by the clock, does not change it.

**`[SPEC-PTL-020]` Where it is.** In the MuLibPlay skin it is a horizontal slider under *Autoselect by clock time*. In the Lempi skin it is one to the right of the *Audio controls* button; that row now shows whenever the slider does, and the button only where the backend has audio controls to offer. The WinAmp skin shows none. Its readout says *any* at 1.0, *none* at 0.0, and the percentage between.

**`[SPEC-PTL-030]` Persisted on the player, and never shared.** It is stored in that player's `player_settings` as `profanity_tolerance`. Star sync keeps the hub's own `player_settings` in a merge and never patches a node's, so each player keeps its own setting. An absent or unreadable value is 1.0. Another process on the same listener file, `mpd_direct`, reads the player's value as it reads the held slot `[SPEC-DIR-109]`.

**`[SPEC-PTL-040]` A follower has none of its own.** A node following another plays what that node chose `[SPEC-ECHO-010]`, so only the followed node's tolerance matters. A follower's slider is shown disabled while it follows, and says whose setting applies.

## 3. What it does

**`[SPEC-PTL-050]` The term.** For a recording with profanity *p* under a tolerance *t*:

| case | effect |
| :--- | :--- |
| *t* = 1.0 | ×1: profanity is not considered |
| *p* = 0, or no value | ×1 |
| *p* ≥ *t*, *p* > 0 | excluded |
| 0 < *p* < *t* | ×(1 − *p*/*t*) |

At *t* = 0.0 every recording with any value above zero is excluded. Between, the ramp is linear: *p* at a quarter of *t* gives ×0.75, half gives ×0.5, three quarters ×0.25, and *p* = *t* is the ramp's zero, which excludes. The one discontinuity is deliberate: at 0.99 a recording at 0.98 is ×0.01, and at 1.0 it is ×1.

**`[SPEC-PTL-060]` *p* is the recording's value as the specials panel shows it.** That is the listener's own value where there is one, and the inherited one where not `[SPEC-PREF-085]`. A passage takes the value of the recording the Director holds for it, the highest-weighted, as every other term does. It is read whether or not the occasion is registered, since a node without the registry still has the values.

**`[SPEC-PTL-070]` A term of its own, and a reason of its own.** It multiplies the weight after the occasion term and is shown as its own line in "why this passage?". An exclusion is counted as *profanity* in the census, not as filtered or below minimum weight, so a pool that has shrunk says why. A ramp value small enough to fall under the minimum weight `[SPEC-DIR-137]` is excluded as below minimum weight, which is what it is.

**`[SPEC-PTL-080]` It shapes only what the Director chooses `[GDE-OCP-900]`.** A passage queued by hand, an album played whole and a search all include every passage. The fill used before a Director is built, `Library::fallback_radio` ([SPEC074](SPEC074-when-the-director-cannot-choose.md) §2), applies it too, so the first minutes after a start obey it as well. The relaxed draw of the same specification sets aside rotation only, and keeps this.

**`[SPEC-PTL-090]` What it would do on the hub.** *Measured 2026-11-08, from the values of `[SPEC-PTL-001]`:*

| tolerance | excluded | discounted |
| ---: | ---: | ---: |
| 0.00 | 85 | 0 |
| 0.25 | 26 | 59 |
| 0.50 | 14 | 71 |
| 0.75 | 4 | 81 |
| 0.99 | 0 | 85 |

## 4. Open

**`[SPEC-PTL-900]` GUIDE036's general form is not built.** A policy for any other occasion, a floor, and a node's own curve `[GDE-OCP-140]` remain designed and unscheduled. Profanity's flat curve stays registered, and inert.

**`[SPEC-PTL-910]` Built and tested on the desktop; not deployed, and not yet heard** choosing around a tolerance on a real programme.

---

**Traceability:** `[SPEC-PTL-001..090]`, `[SPEC-PTL-900]`, `[SPEC-PTL-910]` · from the maintainer's design of 2026-11-08 · satisfies `[REQ-PD-128]` · builds on `[GDE-OCP-100]`, `[GDE-OCP-900]`, `[SPEC-PREF-082]`, `[SPEC-PREF-085]`
