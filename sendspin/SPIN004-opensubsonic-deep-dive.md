# SPIN004: OpenSubsonic in Detail — What It Is, and What It Would Cost on lempipi

**Development Guidance — investigated on `sendspin`, 2026-08-28**

[SPIN003](SPIN003-music-assistant-ecosystem-fit.md) `[GDE-MSA-040]` named an OpenSubsonic-compatible surface as the cheapest way for Music Assistant to reach Vipunen's library, and cited GUIDE007's existing cost estimate as the number that applies. That citation turns out to be wrong in a way worth correcting before anything else: GUIDE007 priced Lempi as a Subsonic **client** (driving a remote server); what Mode A actually needs is Lempi or Vipunen **serving** one. This re-derives the real number, and asks where that server should actually live.

> **Related:** [SPIN003](SPIN003-music-assistant-ecosystem-fit.md) — this corrects and replaces its `[GDE-MSA-040]` cost citation · [GUIDE007](../docs/GUIDE007-external-backends-investigation.md) — correctly priced, for a different direction · [player/src/decoder.rs](../player/src/decoder.rs), [player/src/web/](../player/src/web/) (`web.rs` at the time of writing, split into topic files 2026-09-02) — the code this reuses

---

## 1. Correction, first: GUIDE007 priced the other direction

**`[GDE-MSA-140]` GUIDE007's own words: *"What it would cost to let the Director drive MPD or an OpenSubsonic server."*** That is Lempi as a **client**, sending commands to somebody else's already-running server and letting that server's own audio path do the work — the same shape as the MPD adapter beside it in that document. It says nothing about implementing the *server* side: no endpoint list, no auth scheme, no streaming format, because none of that is Lempi's problem when Lempi is the one issuing requests. [SPIN003](SPIN003-music-assistant-ecosystem-fit.md)'s Mode A needs the opposite: Vipunen or Lempi answering Music Assistant's requests. **The two are not the same estimate, and this document supplies the one that was actually missing.**

---

## 2. What OpenSubsonic is

**`[GDE-MSA-150]` A community-governed superset of the original Subsonic API, built to fix what made Subsonic itself hard to depend on.** Its own stated reasons for existing: *"Outdated and insecure authentication methods,"* *"Suboptimal versioning schema,"* *"Insufficient methods for expressing server functionality,"* and *"Lack of an open and collaborative way to evolve the API."* Governance is open — *"Any server or client can join the organization and make proposals"* — with a real reciprocity condition: a client requesting an extension commits to implementing it. This is the detail Music Assistant's own docs already surfaced in [SPIN003](SPIN003-music-assistant-ecosystem-fit.md) `[GDE-MSA-040]`: *"this will not work with the original Subsonic... unless that software has since moved over to the Open Subsonic specification"* — OpenSubsonic is the dialect that matters here, not legacy Subsonic.

**`[GDE-MSA-160]` The full spec is large — 15 categories, roughly 85 endpoints — and almost none of it is relevant to this project.** Chat, Sharing, Podcasts, Internet Radio, Bookmarks, video: none of it has anything to do with a curated local radio library. **A server needs to implement only what a client actually calls**, and OpenSubsonic makes that explicit rather than implicit: `getOpenSubsonicExtensions` lets a server declare exactly what it supports, and a client is expected to ask before assuming.

**`[GDE-MSA-170]` Two authentication schemes exist, and the older one is worse than skipping it.** Legacy: `token = md5(password + salt)`, salt supplied per-request by the client — which means **the server must be able to recompute this, i.e. hold the password in a form it can reconstruct, not merely a hash it can compare against.** OpenSubsonic's own guidance: *"it is recommended that servers which provide API-key authentication no longer support salt/token-based authentication."* The API Key extension is a plain bearer token instead — no password reconstruction, no MD5 concatenation scheme, and no reason for this project to implement the legacy one at all.

**`[GDE-MSA-180]` Response format is negotiable, and the negotiation itself narrows the real work.** *"Supported values are 'xml', 'json' (since 1.4.0) and 'jsonp' (since 1.6.0)"* — XML if the `f` parameter is omitted, JSON or JSONP if a client asks. A modern client built in the last few years, Music Assistant included, asks for JSON; nothing requires a server to support a format no client it cares about will request `[GDE-MSA-260]`.

---

## 3. Where Vipunen–Lempi–Music Assistant actually meet

**`[GDE-MSA-190]` The server role belongs on lempipi, not in Vipunen — a correction to SPIN003's framing, not just GUIDE007's.** SPIN003 said "Vipunen's library behind an OpenSubsonic-compatible surface," which reads as Vipunen hosting it. Vipunen is a **builder tool**, run when someone is inducting or reviewing, not a thing meant to answer requests around the clock `[SPEC013]`. Music Assistant wants an always-reachable server, and the only always-running process in this project's own architecture that already is one is **Lempi, on the appliance** — the same reasoning that makes lempipi run Lempi as a systemd unit at all. Vipunen's role is what it already is for every other cross-installation question this project has solved: the source of *derived facts* — corrected identifications, flavor, `[REQ-LIB-195]`'s flag sync — that reach lempipi's own `lempi.db` through the sync machinery `[SPEC006 §9, §10]` already built for exactly this, not through Vipunen answering Music Assistant's HTTP requests directly.

**`[GDE-MSA-200]` So the actual picture is Lempi serving OpenSubsonic out of its own already-current database, and it costs less than GUIDE007's misapplied estimate implied.** Lempi's `Cargo.toml` already carries `axum` (with `ws`), `serde`/`serde_json`, and — startlingly relevant — **`md5 = "0.7"`, unconditionally, for something unrelated**, which means even the legacy Subsonic auth scheme `[GDE-MSA-170]` would need no new dependency at all, though `[GDE-MSA-170]`'s own advice is to skip it anyway. No XML crate exists in the tree today; scoping to **JSON-only** (a real, spec-legal choice for a server that only needs to satisfy Music Assistant) means never adding one.

**`[GDE-MSA-210]` Cover art is nearly free: the route already exists.** `GET /art/:passage_id` (`player/src/web/media.rs`, `web.rs` at the time, split 2026-09-02) already resolves a passage to its cover through `cover_art`, keyed by `release_mbid`, with fallbacks to embedded and folder art. `getCoverArt` needs only its own id scheme mapped onto the same resolution path already written and tested — not a new capability, a new name for one that exists.

**`[GDE-MSA-220]` Streaming a *passage* rather than a *file* is the one place real reuse, not just wrapping, is needed — and it turns out to already be shaped for exactly this.** `PassageDecoder::open(path, start_ms, end_ms)` (`player/src/decoder.rs`) is already a standalone, pull-based decoder with no dependency on the live `cpal` playback callback — `.next()` yields frames on demand, which is precisely what an HTTP response body streaming out to a `GET stream` request wants. **This matters because OpenSubsonic has no concept of a passage at all** — only whole tracks — and `[GDE-SPIN-140]`'s recurring finding (a sidecar or adapter fed whole files plays something other than what was actually chosen, true for 98.6% of this library's own radio passages `[GDE-BAK-030]`) applies here word for word unless `stream` is built on `PassageDecoder` rather than on serving `files.path` unmodified. Gain, being a scalar applied per-passage, composes trivially; crossfade does not need to, since it is a property of *adjacent* passages in a live queue, not of any one passage played in isolation — Subsonic's own model has no place for it either, so nothing is actually lost there.

---

## 4. A minimal, honestly-scoped endpoint set

**`[GDE-MSA-230]` Roughly a dozen endpoints, not eighty-five, cover what Music Assistant's own Subsonic provider is documented to need:** `ping`, `getOpenSubsonicExtensions`, `getLicense` (trivially "always valid" — there is no license to check), `getMusicFolders`, `getIndexes` or `getArtists`, `getArtist`, `getAlbum`, `getAlbumList2`, `getGenres`, `search3`, `getCoverArt`, `stream`. Every one of these is a read against tables Lempi already opens (`recordings`, `artists`, `passages`, `passage_recordings`, `cover_art`) via routes shaped exactly like the ones `player/src/web/` already has for its own web UI — new handlers, not new data model.

**`[GDE-MSA-240]` What is deliberately left out, and why each is a real decision rather than an oversight:** `scrobble`/`reportPlayback` (accepting a play report from Music Assistant means deciding whether it becomes a row in `listener_play_history` — the same provenance question `[SPEC-DF-104]` already answered for a synced *decision*, unanswered here for a synced *play*, and not to be assumed); `star`/`setRating` (would need a place in the schema to live, and nothing asked for one yet); `getTranscodeStream`/the Transcoding extension (deliberately never advertised — passthrough of what `PassageDecoder` already produces is the whole story, and adding a transcoder is a real new dependency this scope does not need).

---

## 5. Weight on lempipi, concretely

**`[GDE-MSA-250]` New Cargo dependencies: none, if scoped as above.** `axum`, `serde_json`, `md5` are already unconditional. The binary grows by however much the new route handlers themselves compile to — a few hundred lines of Rust, the same order of magnitude as the history panel or the flag routes already shipped unconditionally, not a new subsystem.

**`[GDE-MSA-260]` The gate is attack surface, not memory** — a different reason than `vipunen-support`'s. `vipunen-support` is gated because an appliance that never runs Vipunen has no use for the extra megabytes `[SPEC-SUI-190]`; an OpenSubsonic surface costs almost no megabytes at all, but it is a **new authenticated, unauthenticated-until-paired network entry point** on a home appliance, and "a person deliberately turned this on" is worth more here than a few kilobytes ever were. Recommend a `subsonic` feature, default off, gated the same shape as `mpd` — cheap to build, off until asked for.

**`[GDE-MSA-270]` The real cost is not code size, it is the passage-clipped `stream` handler doing genuine per-request decode work** — `PassageDecoder` running once per stream request, the same CPU `PassageDecoder` already spends for local playback, now potentially concurrent with local playback if lempipi is asked to serve a Music Assistant client and play locally at the same time. Unmeasured; the honest comparison is against whatever headroom `[PI-BOS-...]`-style survey work has already found on lempipi's own CPU, not assumed to be free because the code already exists.

---

## 6. Recommendation

**`[GDE-MSA-280]` If Mode A is built, build it in Lempi, JSON-only, API-Key-authenticated, `stream` on `PassageDecoder`, gated behind a default-off `subsonic` feature — and treat this document, not `[GDE-BAK-020]`, as the estimate for it.** SPIN003's overall recommendation does not change: this is still the cheapest of the modes it compared, cheaper than SPIN003 itself assumed, but still not something to build ahead of the still-cheaper, still-more-reversible Sendspin player mode `[GDE-SPIN-210]` this whole investigation keeps landing on first.

---

## 7. Open

1. **`[GDE-MSA-290]` Whether Music Assistant's Subsonic provider actually requests `f=json`** rather than defaulting to XML — assumed from its being a modern implementation, not confirmed by reading its own client code.
2. **`[GDE-MSA-300]` What concurrent local playback plus one or more `stream` requests actually costs on lempipi's own CPU** `[GDE-MSA-270]` — no measurement exists yet.
3. **`[GDE-MSA-310]` Whether a played-via-Music-Assistant passage should ever become a `listener_play_history` row**, and under what provenance marker, before `scrobble` is ever implemented at all `[GDE-MSA-240]`.

---

**Traceability:** `[GDE-MSA-140..310]` · corrects `[GDE-MSA-040]`'s citation of `[GDE-BAK-020]` · derived from `[GDE-SPIN-140]`, `[GDE-BAK-030]`, `[SPEC-DF-104]`, `[REQ-HW-140]`
