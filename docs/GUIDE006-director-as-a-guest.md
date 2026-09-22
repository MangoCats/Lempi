# GUIDE006: The Director as a Guest

**Development Guidance — driving other players with Lempi's selection, and where that stops being possible**

Could the Program Director `[SPEC009]` choose the next passage for someone else's player? For anything whose files you hold, yes, and more cheaply than expected. For a streaming catalogue, no — and not for want of an API.

> **Related:** [SPEC009](spec/SPEC009-program-director.md) · [GUIDE004 §6](GUIDE004-phone-port-strategy.md) for `lempi-core` · [GUIDE005](GUIDE005-flavor-service.md) · `[REQ-LIB-120]`

---

## 1. What the Director actually requires

**`[GDE-EXT-010]` Two questions decide every integration**, and neither is about the player's API.

1. **Can flavor be had for the catalogue?** Stages B and C — pool shaping and acoustic flow `[SPEC-DIR-150]` — consume the 71-dimension vector. Flavor comes from audio, or from a precomputed store keyed by a stable identifier `[GDE-CLD-020]`. No audio and no store means no flavor.
2. **Can the next item be enqueued, and can plays be observed?** Stage A is rotation, recovery and restraint over play history keyed by recording `[GDE-PD-010]`.

Answer both and the Director works. Answer only the second and **stage one of four survives**, which is rotation-weighted shuffle — a thing that already exists and is not Lempi.

---

## 2. Where it works: anything whose files you hold

**`[GDE-EXT-020]` This is a smaller job than it sounds, because the architecture already splits here.** The Director produces a passage; the engine plays it. Substituting an *output adapter* that hands the choice to MPD, an OpenSubsonic server or a desktop player is a bounded piece of work, and it is exactly what `lempi-core` `[GDE-AND-045]` exists to make possible.

Plausible hosts, all of which let you both enqueue and read back what played:

| Host | Route | Note |
| :--- | :--- | :--- |
| **MPD** | its own protocol | scriptable, long-standing, the natural first target |
| **OpenSubsonic** servers — Navidrome, Gonic, Airsonic | one HTTP API, **many clients** | best reach per unit of work |
| **Jellyfin / Plex** | HTTP APIs | large existing libraries |
| **foobar2000, MusicBee, Kodi, Rhythmbox** | component/plugin SDKs | one integration each, Windows- or app-specific |

**OpenSubsonic is the highest-leverage target**: one protocol, several servers, and dozens of clients across every platform. A Director speaking it reaches all of them without knowing any of them.

**`[GDE-EXT-025]` The fidelity cost is real and specific: passages become tracks.** Lempi selects a *span* — `start_ms` to `end_ms`, with lead-in, lead-out, fade-in, fade-out and gain `[SPEC-SC-040]`, `[SPEC-SC-046]` — and MPD and Subsonic address whole files. Three consequences:

- the **Album/Radio duality is lost** `[GDE-BMK-030]`, which is MuLibPlay's best structural idea and the thing the `radio`-kind's own `start_ms`/`end_ms` exist for;
- **DAO captures cannot be played at all** as single songs, because the file is forty songs and the host has no way to be told which one;
- gain and the de-click ramps fall to the host, which will not have them.

Whether any given host can play a time range is worth checking per host rather than assuming; the safe assumption is that it cannot.

So the honest description is **"Lempi's taste, someone else's playback, at track granularity"** — genuinely useful for a library of single-track files, and a real loss for one built on DAO rips.

---

## 3. Where it stops: streaming

**`[GDE-EXT-030]` Spotify closed the door on 2024-11-27, and it is not ajar.** In one developer-blog post Spotify deprecated `audio-features`, `audio-analysis`, `recommendations`, related artists, and **30-second preview URLs** for all new applications. New apps receive `403`. Only applications holding extended access from before that date still reach them.

That is not one missing endpoint. It removes, together:

- **the descriptors** the Director would have used instead of its own flavor, and
- **the preview audio** from which they could have been derived independently.

With the catalogue itself under DRM, there is no third route. **A Lempi Director cannot be built against Spotify at any effort**, and Apple Music, Tidal and Deezer differ only in that they never offered the descriptors.

**`[GDE-EXT-035]` Terms of service would forbid it even if the data existed.** Streaming platforms restrict using their catalogue data to build recommendation systems. An integration that survived the technical problem would still be operating against the agreement it needs to keep.

**`[GDE-EXT-040]` The lesson is one this project already wrote down, and has now paid for twice.**

> `[REQ-LIB-120]` — *Compute flavor locally, with no dependence on any live external service.*

AcousticBrainz's API died within seven months of a successful bulk query `[GDE-MCR-045]`. Spotify's descriptors died three years later. **Acoustic descriptors computed by somebody else have now failed twice, from two unrelated directions**, and in both cases the failure was announced rather than negotiated. The requirement was written before the second failure and predicted it exactly.

This is also the argument against treating [GUIDE005](GUIDE005-flavor-service.md)'s lookup service as anything but a convenience: it is a third instance of the same dependency, and it should be built expecting to outlive its source.

---

## 4. Feasibility, plainly

**`[GDE-EXT-050]`**

| Target | Flavor obtainable | Control | Verdict |
| :--- | :--- | :--- | :--- |
| MPD, OpenSubsonic, Jellyfin, Plex | **yes** — you hold the files | yes | **feasible, modest** |
| foobar2000, MusicBee, Kodi | yes | yes, per-app plugin | feasible, repetitive |
| Apple Music, *own* imported library | yes — files are on the Mac | MusicKit | feasible, narrow |
| **Spotify** | **no** — deprecated, no previews | yes | **not feasible** |
| Apple Music / Tidal / Deezer **catalogue** | **no** — DRM | yes | **not feasible** |

**`[GDE-EXT-055]` The one artifact worth building first is not an integration.** Every row that says *feasible* wants the same thing: the selection engine, separated from the audio path, with a small interface for "here is the next passage — play it". That is `lempi-core` plus an output trait, and it is already most of the way there — the Director imports only `rusqlite`, `serde`, `crate::db` and `crate::queue::QueueEntry` `[GDE-AND-020]`, with one platform-conditional exception: `director/program.rs`'s OS UTC-offset sync calls `libc` on Unix and shells out to `powershell` on Windows.

Build that and MPD is a few hundred lines; skip it and every host is a fork.

**Built 2026-09-22**, as `player/core/` — see `[GDE-AND-045]` for what it holds and what the extraction measured. The `powershell` exception above survived the move unchanged and is still the only place the selection engine shells out for a platform fact.

---

## 5. Open

1. **`[GDE-EXT-060]` Whether any candidate host can play a time range.** It decides whether passages survive `[GDE-EXT-025]` or degrade to whole tracks, and it should be measured per host before choosing one.
2. **`[GDE-EXT-065]` Whether scrobbles are a sufficient history source.** Last.fm and ListenBrainz observe plays across many players; if a host cannot report what it played, a scrobble feed might close stage A's loop without any host cooperation at all.
3. **`[GDE-EXT-070]` Whether "rotation-only" is worth shipping** for hosts with no flavor. It is one stage of four and much less than Lempi — but it is also the stage that carries six years of tuned rotation, recovery and restraint `[GDE-PD-010]`, which nothing else has.

---

**Traceability:** `[GDE-EXT-010..070]` · derived from `[SPEC-DIR-150]`, `[REQ-LIB-120]`, `[GDE-BMK-030]`, `[GDE-AND-045]`
