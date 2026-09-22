# SPIN003: Where Lempi/Vipunen Might Fit Into Music Assistant

**Development Guidance — investigated on `sendspin`, 2026-08-28**

Sendspin turned out to have a parent: it was released *by* Music Assistant, an Open Home Foundation project that already does a large fraction of what Vipunen and Lempi were built to do — aggregate a library, keep several speakers in sync, choose what plays next. This asks the honest question first (is there anything left for this project to add) and then the integration question (if so, what does it cost to reach it), the same order [GUIDE006](../docs/GUIDE006-director-as-a-guest.md) used for "could the Director drive someone else's player."

> **Related:** [SPIN001](SPIN001-protocol-and-integration-analysis.md) §2 first named Music Assistant as Sendspin's reference server · [GUIDE007](../docs/GUIDE007-external-backends-investigation.md) already priced an OpenSubsonic adapter, which turns out to be the relevant number here · [GUIDE006](../docs/GUIDE006-director-as-a-guest.md) · [SPEC009](../docs/spec/SPEC009-program-director.md)

---

## 1. What Music Assistant actually is

**`[GDE-MSA-010]` A library aggregator with a real community behind it, not a hobby project.** `music-assistant/server`: **3,000 stars, 564 forks, Apache-2.0, Python/asyncio**, an Open Home Foundation project (the same steward as Sendspin and Home Assistant). It connects **51 music providers** — Spotify, Tidal, Qobuz, YouTube Music, podcasts, internet radio, and self-hosted sources including Plex, Jellyfin, Emby, a generic Subsonic-family provider, and a local-filesystem provider — to a shared library, and fans the result out to **player providers**: Sonos, Chromecast, AirPlay, Squeezelite, DLNA, and, as of 2.8, Sendspin natively.

**`[GDE-MSA-020]` It already has a history-aware queue, and it is worth naming precisely rather than assuming it is a competitor to the Program Director.** "Smart Shuffle" *"uses your listening history to push recently played tracks to the back of the queue"* and avoids back-to-back artists in a dynamic queue. That is recency-and-diversity avoidance — a real, useful heuristic, and not what `[SPEC009]`'s flavor-distance scoring or six years of tuned MuLibPlay selection behaviour `[GDE-BMK-050]` does. Nothing in Music Assistant's own feature list claims similarity-based next-track selection, occasion-aware programming, or a queue shaped by a specific listening history the way the Director's is. **That gap is this project's actual reason to interface with Music Assistant rather than simply recommend it as a replacement.**

---

## 2. Four ways to meet it

**`[GDE-MSA-030]` Ranked by how much cooperation from Music Assistant's own maintainers each one requires — the axis that turned out to matter most.**

| Mode | What it is | MA-side cooperation needed |
| :--- | :--- | :--- |
| **A** | An OpenSubsonic-compatible surface on lempi02w's own current library | **None** — MA's existing Subsonic provider already speaks it |
| **B** | A native Music Assistant `MusicProvider`/`MetadataProvider` plugin | A merged PR, and permanent upstream maintenance |
| **C** | Lempi as a Sendspin player/source inside an MA-run group | None to little — already analysed in [SPIN001](SPIN001-protocol-and-integration-analysis.md)/[SPIN002](SPIN002-server-mode-deep-dive.md) |
| **D** | Run both, unconnected | None — available today |

**`[GDE-MSA-040]` Mode A — OpenSubsonic.** Music Assistant's Subsonic provider is explicit about which spec it needs: *"This source only works with servers that follow the Open Subsonic specification... this will not work with the original Subsonic or with anything built on it, such as Airsonic."* Tested by MA's own authors against Gonic and Navidrome. **Build the OpenSubsonic surface once, and MA gets it for free**, with no cooperation, no PR, no coupling to MA's release cycle. Known rough edges on MA's side: *"some files may not play at all, most often m4a and opus, and anything encoded at a variable bitrate"* — worth checking against this library's own format mix before assuming it is transparent. *Correction, [SPIN004](SPIN004-opensubsonic-deep-dive.md): `[GDE-BAK-020]`'s existing OpenSubsonic estimate, first cited here, prices Lempi as a Subsonic* client *driving someone else's server — the opposite direction from serving one to Music Assistant. [SPIN004](SPIN004-opensubsonic-deep-dive.md) supplies the estimate that actually applies, and finds it cheaper, and better placed in Lempi than in Vipunen.*

**`[GDE-MSA-050]` Mode B — a native provider — is not a smaller version of Mode A, it is a different commitment entirely.** Music Assistant ships official `MusicProvider`/`PlayerProvider`/`MetadataProvider`/`PluginProvider` base classes with template stubs, which reads like an invitation — but a maintainer's answer to exactly this question, asked directly in the project's own GitHub discussions, was unambiguous: *"No, we don't consider that [a third-party/unsafe-mode loading path]. Just follow the development workflow and we're open for PRs."* **There is no supported way to run a Vipunen provider without merging it into `music-assistant/server` and maintaining it there, under their review standards, for as long as it exists.** A `MetadataProvider` supplying Vipunen's corrected identifications and flavor data to enrich MA's own local-files scan is the more interesting of the two roles on paper — it does not require Vipunen to also solve streaming — but it inherits the same governance cost. This is the same shape of finding `[GDE-SPIN-340]` made about `sendspin-rs`: the inviting-looking door is not the cheap one.

**`[GDE-MSA-060]` Mode C is already analysed, and does not need re-deriving here.** Music Assistant is the reference Sendspin server named in `[GDE-SPIN-060]`; anything [SPIN001](SPIN001-protocol-and-integration-analysis.md) §3 and [SPIN002](SPIN002-server-mode-deep-dive.md) said about Lempi speaking Sendspin as a player or source applies unchanged to "the server on the other end happens to be Music Assistant." No new cost, no new finding.

**`[GDE-MSA-070]` Mode D is not a null result — it is the honest baseline.** Nothing prevents someone running Music Assistant for the streaming-service aggregation and casual multi-room listening it is good at, and Lempi/Vipunen for the curated, single-library, Director-driven listening it is good at, on the same network, entirely unconnected, today. Naming this explicitly matters because it is the comparison every other mode has to beat: Mode A costs a real adapter to build; Mode D costs nothing and already works.

---

## 3. Recommendation

**`[GDE-MSA-080]` If Music Assistant interop is ever built, build Mode A.** It is the only mode that reaches Music Assistant's install base without asking anything of Music Assistant's maintainers. [SPIN004](SPIN004-opensubsonic-deep-dive.md) works out what it actually costs and where it should live — Lempi, not Vipunen — and finds it cheaper than first assumed here. It also composes with Mode C for free: a library reachable over OpenSubsonic and a player reachable over Sendspin cover both directions Music Assistant might want to meet Lempi/Vipunen, without either needing the other to exist.

**`[GDE-MSA-090]` Mode B should not be pursued while Mode A remains untried.** Upstream governance cost is not a one-time price; it is recurring, and it was answered directly and unambiguously by the people who would bear it. Nothing about Vipunen's own data is currently worth that commitment when Mode A reaches the same users.

**`[GDE-MSA-100]` This does not change SPIN001's overall posture.** Watch, and if a first concrete step is taken anywhere in this investigation, take the cheapest reversible one — which, across all of Sendspin and Music Assistant both, is still `[GDE-SPIN-210]`'s Mode C: Lempi as a plain Sendspin player, touching neither the Director nor anyone else's codebase.

---

## 4. Open

1. **`[GDE-MSA-110]` Whether this library's own format mix hits the "m4a/opus/VBR" playback issue** `[GDE-MSA-040]` flags on Music Assistant's Subsonic client, before assuming an OpenSubsonic surface would be transparent to it.
2. **`[GDE-MSA-120]` Whether Vipunen's derived facts (flavor, corrected ids) have any expression at all in the OpenSubsonic schema**, or whether Mode A necessarily means Music Assistant sees Vipunen's library exactly as well as any other Subsonic server's — i.e., without the enrichment that is Vipunen's actual point.
3. **`[GDE-MSA-130]` Whether Music Assistant's own metadata-provider ecosystem (already-merged ones, not a hypothetical Vipunen one) does anything Vipunen's identification work would be redundant with**, before any Mode B conversation restarts.

---

**Traceability:** `[GDE-MSA-010..130]` · corrected and extended by `[GDE-MSA-140..310]` in [SPIN004](SPIN004-opensubsonic-deep-dive.md) · derived from `[GDE-SPIN-010..430]`, `[GDE-BAK-020]`, `[SPEC009]`, `[GDE-BMK-050]`
