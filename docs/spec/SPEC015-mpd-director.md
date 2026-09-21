# SPEC015: The Director as an MPD Client

**Design Specification — Tier 2 · PROVISIONAL, on `investigate/external-players`**

How Lempi's Program Director `[SPEC009]` reaches the MPD ecosystem: what MPD already does better than reimplementing it, and what it cannot do that Lempi must supply.

> **Related:** [GUIDE007](../GUIDE007-external-backends-investigation.md) measured the cost · [GUIDE006](../GUIDE006-director-as-a-guest.md) posed it · [SPEC013](SPEC013-vipunen-console.md) for the two-UI precedent

> **Naming note, corrected 2026-09-02:** this document was written against a
> separate `lempi-mpd` binary that was never built. What shipped instead —
> measured in [IMPL005 §Stage 5](../IMPL005-mpd-prototype-results.md) — is the
> MPD integration folded into the single `lempi` binary behind `--features
> mpd`, reached through `switch.rs`/`mpd_backend.rs` as one more backend per
> the seam `[SPEC-BK-020]`, described in
> [architecture.md §3](../architecture.md#3-the-backend-seam). `lempi-mpd`
> below names the **role** — the Director acting as an MPD client — not a
> literal binary; `[SPEC-MPD-070]` gives the corrected mechanism.

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §7 in [SPEC038](SPEC038-mpd-director-settled.md), the rest in [SPEC015](SPEC015-mpd-director.md).

## 1. Shape

**`[SPEC-MPD-010]` It is a client, because MPD has no seam for anything else.** The protocol documentation is explicit that there is no plugin interface for deciding what plays next; queue order is a client's business, and `prio`/`prioid` only reorder within random mode. So the Director sits beside MPD and feeds it.

```
   ncmpcpp · M.A.L.P. · myMPD · Cantata · Shinobu          ← ~60 existing clients
                        │  control playback as usual
                        ▼
                      MPD        ← plays files, owns the queue, owns the audio
                        ▲
        idle · addid · rangeid · sticker · currentsong
                        │
                   lempi-mpd     ← the Director, headless, no audio path
                        │
                        ▼
                    lempi.db     ← flavor, history, rotation  (Vipunen unchanged)
```

**`[SPEC-MPD-015]` This is an established shape in the ecosystem, not a novelty.** MPD's own client list carries a *utility / non-interactive* category, and `MPD_sima` already occupies it as an auto-queueing client. Lempi would be another of those, with a better answer to the same question.

**`[SPEC-MPD-020]` The payoff is the client ecosystem, and it is the real reason to do this.** Lempi gains around sixty maintained front-ends — terminal, web, desktop, Android, Wear OS, iOS — without writing any of them, and gains them on **existing installations**. A moOde or Volumio owner adds selection to a box they already have `[GDE-BAK-080]`; nobody is asked to adopt an appliance image.

---

## 2. What MPD is left to do

**`[SPEC-MPD-030]` Everything it is already good at, unmodified.**

| Concern | MPD command | Note |
| :--- | :--- | :--- |
| Notice a track ended | `idle player playlist options` | event-driven; no polling loop |
| Add a chosen passage | `addid <uri>` → song id | |
| **Play a passage, not a file** | **`rangeid <id> <start:end>`** | fractional seconds, since 0.19 `[GDE-BAK-035]` |
| Retire what has played | `consume 1` | MPD removes it; the queue becomes pure lookahead |
| Observe what is playing | `status`, `currentsong` | elapsed and duration, so a skip is distinguishable from a play |
| Audio output, formats, multi-room | its own | Lempi contributes nothing and should not |

**`[SPEC-MPD-035]` `consume 1` makes MPD's queue the same object as Lempi's.** Lempi keeps a depth of five ahead; with `consume` on, the MPD queue drains as it plays and `lempi-mpd` tops it back up. The refill rule is the one already written — `[SPEC-DIR-160]`'s flow is measured from the tail, and the queued ids are the exclusion set.

---

## 3. What Lempi supplies that MPD has not got

**`[SPEC-MPD-040]`** In descending order of how badly MPD lacks it:

1. **Flavor.** MPD has no acoustic notion of any kind. The 71-dimension vector, its provenance and its measured accuracy are Lempi's `[SPEC-SC-060]`.
2. **Rotation, recovery, restraint.** MPD offers `random`, `repeat`, `single`, `consume` — none time-aware. Six years of tuned frequency behaviour has no MPD counterpart `[GDE-PD-010]`.
3. **The Album/Radio duality.** MPD stores no per-song trim. Lempi holds `start_ms`/`end_ms` and applies them per add via `rangeid`, which is why this target keeps what 98.6% of the library is shaped by `[GDE-BAK-030]`.
4. **Long-term play history.** MPD does not keep one. Lempi's `listener_play_history` does, and it is the only irreplaceable data in the system `[SPEC-DF-090]`.
5. **Why this passage.** No equivalent — see below.

---

## 4. Extending MPD without patching MPD

**`[SPEC-MPD-050]` The sticker database is the extension point, and it is a real one.** MPD attaches arbitrary name/value pairs to a song URI, persists them, and `sticker find` supports `==`, `<`, `>`, `contains` and `starts_with`. Nothing needs to be added to MPD for Lempi to publish into the world MPD's clients already read. Proposed namespace, all under `lempi.`:

| Sticker | Value | Read by |
| :--- | :--- | :--- |
| `lempi.passage` | passage id in `lempi.db` | `lempi-mpd` itself — see `[SPEC-MPD-060]` |
| `lempi.why` | the weight decomposition, JSON `[REQ-VIS-100]` | any sticker-aware client |
| `lempi.flavor` | a short human summary, e.g. `danceable · not sad` | any sticker-aware client |
| `lempi.title`, `lempi.artist` | the passage's real title/artist | any sticker-aware client — `naming: false` in `Capabilities::MPD`, see `[SPEC-MPD-052]` |
| `lempi.chosen_at` | unix seconds | `sticker find` for "what did it pick today" |

**A client that knows nothing of Lempi is unaffected**; one that shows stickers gains the "why this passage" panel for free. That is the whole of the extension, and it requires no MPD change, no fork, and no protocol addition.

**`[SPEC-MPD-055]` What cannot be expressed, stated rather than approximated.**

| Lempi has | MPD has | Verdict |
| :--- | :--- | :--- |
| per-passage `gain_db` `[SPEC-SC-040]` | `replay_gain_mode`, **per file** | **cannot express** — two passages in one file may differ |
| `lead_in_ms` / `lead_out_ms`, per passage — *timing* only `[SPEC-SC-043]` | `crossfade` (global seconds), `mixrampdb` | **approximation only** — the median lead-out is 946 ms and a global crossfade is one number for every track |
| `fade_in_ms` / `fade_out_ms`, per passage — the actual gain ramp `[SPEC-SC-046]` | nothing analogous | **cannot express at all** — MPD's `crossfade`/`mixrampdb` only blend between two adjacent files; they shape nothing on a passage played alone, which is what fade does on every passage regardless of a neighbour |

All three are declared through `Capabilities` `[GDE-BAK-040]` rather than silently dropped: `Capabilities::MPD` is `{ spans: true, gain: false, ramps: false }` — `ramps` covering both lead's timing and fade's envelope, since neither survives the handoff. A run that cannot honour gain or ramps says so once at startup, not never.

---

## 5. The mapping, which is the hard part

**`[SPEC-MPD-060]` Lempi names a passage; MPD names a URI relative to its own `music_directory`. Nothing guarantees the two libraries agree** — this is `[SPEC-RLK-025]`'s lesson in a new costume, and GUIDE007 named it the piece to prototype first `[GDE-BAK-025]`.

Resolution order, narrowest first, in the spirit of `[SPEC-DF-040]`:

1. **Same tree.** Where MPD's `music_directory` is a prefix of `files.path`, the URI is the remainder. Cheap, exact, and the common case for an appliance where both were configured by the same person.
2. **MusicBrainz recording id.** MPD reads and exposes `MUSICBRAINZ_TRACKID` where files carry it; Lempi holds recording MBIDs. Survives different trees, fails on the 164 recordings here that have no MBID at all.
3. **Ask a person.** Anything unresolved is reported, never guessed. A wrong mapping plays a real song under a real name and nothing downstream can tell — the exact failure `[REQ-LIB-165]` exists to correct.

**`[SPEC-MPD-065]` The resolved mapping is also written into MPD's own sticker database**, as `lempi.passage` on the URI, and **not** into `lempi.db` — the mapping is a property of *that MPD instance*, not the library; a second MPD gets its own without collision; a Lempi user who never touches MPD grows no table they will not fill `[SPEC-SC-015]`. **Corrected 2026-09-02: the shipping backend implements rung 1 only, and writes this sticker without reading it back.** `MpdBackend::nameable_uris` builds the URI map from same-tree path stripping alone, never `MUSICBRAINZ_TRACKID` or MPD's song list, and `MpdBackend::poll` resolves purely from its in-memory map. Rungs 2/3 and the sticker read-back exist only in the offline `mpd_map.rs` tool and the retired `mpd_direct.rs` prototype — diagnostic tooling, not the running Director; `[IMPL004]` measured rung 2 as what resolves 100% of the cross-platform (Linux MPD, foreign tree) case, and `[IMPL005]` §Stage 4 already found the sticker cache "barely one" in practice. Extending `nameable_uris` to the full ladder and the cache read-back remains open.

---

## 6. Containment

**`[SPEC-MPD-070]` Folded into the one binary, behind a default-off feature —
not the separate binary this section originally specified.** MPD support
lives in `player/src/mpd_backend.rs`, reached through `switch.rs` as a
`Backend` `[SPEC-BK-020]` beside the local `Engine`, gated by `--features
mpd`, using `std::net` and no new dependency. `player/src/bin/` also carries
`mpd_direct.rs`, `mpd_fill.rs`, `mpd_map.rs`, `mpd_session.rs` and
`mpd_watch.rs` — the staged prototype and ops tools from
[IMPL004](../IMPL004-mpd-prototype.md), not the shipping integration point.

The byte-identical claim does not hold as originally stated: `[GDE-BAK-050]`
`[REQ-HW-140]` promised a build indistinguishable from one without this
branch, but the branch also changed the local player (scrobbling alignment,
skip/dequeue suppression) for reasons unrelated to MPD. What **does** hold,
measured in [IMPL005 §Stage 5](../IMPL005-mpd-prototype-results.md): with
`--features mpd` off, the binary contains zero occurrences of MPD protocol
strings (`listallinfo`, `rangeid`, `sticker set song`, `OK MPD`); with it on,
the binary grows by 8,192 bytes, none of it MPD code. A user who does not
want this compiles without the feature and gets a binary with none of it —
which is the property this spec actually needs.

**`[SPEC-MPD-075]` Lempi's own UI keeps its job.** MPD clients drive playback; Lempi's web UI stays for what they cannot show — browse with provenance, the review queue, the why panel `[REQ-VIS-180]`. The same division SPEC013 draws between the console and the player: two surfaces, two questions, one database.

**`[SPEC-MPD-080]` Target MPD 0.19's protocol surface, not the newest.** `rangeid`, `sticker`, `idle` and `consume` have all been present since 2016. Stable-distro users run years-old versions by policy rather than choice `[GDE-BAK-120]`, and depending on anything newer would exclude half the user base for years.

---

> **Split on 2026-09-10.** The settled decisions are now
> [SPEC038](SPEC038-mpd-director-settled.md).
## 8. Open

**`[SPEC-MPD-125]` Settled 2026-08-21: the local engine adopts the threshold.**
The scrobbling alignment is not the MPD path's local convention — it applies to
Lempi's own playback equally. Moved to
[SPEC017](SPEC017-what-counts-as-a-play.md), which now owns the rule for every
path; `[SPEC-PLAY-040]` records the MuLibPlay divergence and the one consequence
that came with it.

Nothing else outstanding.

---

**Traceability:** `[SPEC-MPD-010..120]` · derived from `[GDE-BAK-035]`, `[GDE-BAK-025]`, `[SPEC-DIR-160]`, `[SPEC-SC-043]`
