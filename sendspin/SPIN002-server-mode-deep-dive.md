# SPIN002: Mode A in Detail — Lempi Hosting Its Own Sendspin Group

**Development Guidance — investigated on `sendspin`, 2026-08-28**

[SPIN001](SPIN001-protocol-and-integration-analysis.md) `[GDE-SPIN-110]` named Mode A — Lempi as its own Sendspin server, fanning the Director's own selection out to cheap synced receivers — as the option that preserves the most of what the Program Director is for, and the most protocol to build. This is what "the most protocol to build" actually contains, and what it would take to avoid building most of it.

> **Related:** [SPIN001](SPIN001-protocol-and-integration-analysis.md) · [GUIDE007](../docs/GUIDE007-external-backends-investigation.md) for the sibling cost analysis and its feature-gate conclusion · [`build/install-player.sh`](../build/install-player.sh) for the "ship a finished artifact, run it as a unit" pattern this borrows

---

## 1. What "hosting a group" actually obligates the server to do

**`[GDE-SPIN-300]` The server is always the Noise initiator — by design, not convention.** The spec is explicit: *"The server is the Noise initiator, the client is the Noise responder, regardless of which side initiated the WebSocket connection."* The reason is structural, not arbitrary: the server's first handshake message carries a `psk_id` so the client can select the right pre-shared key before that key is mixed into the second message — the client has no way to start the handshake without already knowing which PSK applies. Whatever hosts a Sendspin group therefore owns the harder half of the crypto, for every client, all the time it is running — this is not a cost Mode A can shrink by choosing a smaller feature set; it is inherent to being the server at all.

**`[GDE-SPIN-310]` A server also owns a persistent trust store with no protocol-level lifecycle.** The spec defines two ways to keep a paired client's long-term PSK (alongside the server's own id, or shared without one) but is silent on revocation, rotation, or what happens to a paired-but-now-absent device. Whoever is the server decides this, the same way Lempi's own schema decides what a decision journal remembers `[SPEC-DF-104]` — there is no protocol answer to copy.

**`[GDE-SPIN-320]` Metadata and artwork have no defined source — this is the gap that matters most.** `metadata@v1` and `artwork@v1` are broadcast by the server via `server/state`, but the spec never says where the server gets the title, artist, or cover it broadcasts. That is left entirely to whoever builds the server. For Lempi this is not a detail to fill in later: **the whole point of Mode A is that a receiver shows what the Director is actually playing**, so a server that cannot be told what changed has not delivered the feature — it has delivered synced silence with a name on it.

**`[GDE-SPIN-330]` Audio is encoded per client, not once and broadcast.** Confirmed directly: *"Each client receives its own independently encoded stream based on its capabilities and preferences"* — a hi-fi client and a kitchen speaker in the same group genuinely each get their own encode. On a Pi Zero 2W this is a real CPU line, not just the memory one `[GDE-SPIN-150]` already named: N receivers is N encoder instances running concurrently, not one encode fanned out.

---

## 2. There is no Rust server to build on — only a Rust client, and a working Go server

**`[GDE-SPIN-340]` `sendspin-rs` is a *client* implementation, and an unfinished one.** Its own status: Phase 1 done (message types, WebSocket handshake, a PCM decoder, NTP-style clock sync), Phase 2 not started (`cpal` audio output, the scheduler, "end-to-end player"). It is being built toward being a receiver, not a hub — `cpal` is an output-device crate, meaningless for a server that never plays audio itself. There is currently no Rust crate that implements the server side of Noise-as-initiator, pairing, or group/time management at all. A Mode A built in Rust today means writing that from the spec directly, in a security-sensitive protocol its own authors call unfinished.

**`[GDE-SPIN-350]` `sendspin-go-server` already does all of §1's crypto and sync, packaged as one binary.** *"The wire protocol, codecs, clock sync, and group/role model all live in the SDK [`sendspin-go`]... This repo owns the CLI flags, the TUI, the audio source decoders, and packaging."* It takes `--audio <file|http-url|hls-url>`, transcodes to Opus, discovers and pairs clients over mDNS, and ships a systemd daemon mode (`make install-server-daemon`) — deployable the same shape as Lempi itself already is on lempi02w. Crucially, **`--audio` accepts a plain HTTP URL as a pull source**, which is the seam Lempi could use without writing any protocol code at all.

---

## 3. Two architectures, and why the obvious one has a hole in it

**`[GDE-SPIN-360]` Architecture 1 — sidecar the reference binary.** Lempi's web server (already Axum, already serving the mixed, post-crossfade, post-gain PCM the engine produces — `[GDE-FBD-010]`'s "audio is never decoded whole" buffer is exactly the shape a live stream wants) exposes a small local endpoint serving that stream; `sendspin-go-server --audio http://127.0.0.1:PORT/stream` is supervised as a sidecar process, the same operational shape `deploy-player.sh` already uses for Lempi itself. **Lempi writes zero lines of Sendspin protocol.** All of §1's crypto, pairing, and per-client encoding is the Go binary's problem, running a binary this project did not write, on hardware it does not control the resource ceiling of independently from lempi's own process.

This is the cheap option, and `[GDE-SPIN-320]` is exactly why it is not yet the *whole* option: **a continuous PCM stream carries no title, artist, or artwork**, and nothing in the documented CLI surface accepts one from outside. Pointing the server at actual files instead (`--audio /path/to/track.flac`) would recover file-tag metadata for whatever server-side extraction the Go SDK does — but only by handing it whole files, which throws away exactly what makes Lempi's own passages different from the files they come from: trimmed spans, crossfades, per-passage gain. **A sidecar fed whole files is not playing what the Director chose; it is playing the file the chosen passage happens to live in.** The two are the same track only when a passage is the whole file — 1.4% of this library's own radio passages, per `[GDE-BAK-030]`'s own measurement.

**`[GDE-SPIN-370]` Architecture 2 — a native server, in Rust or as a purpose-built sidecar.** Either write §1 directly against the spec in Rust (large, and `[GDE-SPIN-050]`'s "not yet finalized" warning applies to exactly the surface being built against), or write a small custom server against the **`sendspin-go` library** (not the packaged CLI) that exposes whatever hook Lempi actually needs — an RPC or local socket Lempi's engine calls on every track change, carrying the metadata the reference binary has nowhere to accept. This closes `[GDE-SPIN-320]`'s gap by construction, at the cost of maintaining a second, purpose-built server instead of running one already built and tested by the protocol's own authors.

---

## 4. Recommendation

**`[GDE-SPIN-380]` Prototype the metadata gap before either architecture, because it decides between them.** If `sendspin-go`'s library surface (not just the `sendspin-go-server` binary) turns out to expose a way to push per-client `server/state` updates externally — even undocumented, even by embedding the library in a tiny purpose-built Go shim rather than shelling out to the packaged CLI — Architecture 1's economics hold and Lempi need never touch Noise, pairing, or per-client encoding at all. If it does not, the choice is between shipping Sendspin receivers that show no metadata (a real regression from what Lempi's own web UI already shows) or building Architecture 2, and that is a decision worth making deliberately rather than discovering after the sidecar is already running.

**`[GDE-SPIN-390]` Nothing here changes SPIN001's overall recommendation.** Watch, and if a first step is taken at all, take Mode C first `[GDE-SPIN-210]` — it needs none of this. This document exists so that *if* Mode A is ever prioritized, the actual shape of the work is already known rather than discovered mid-implementation: it is not "implement a protocol," it is "answer the metadata question, then either drive an existing binary or write a small, tightly-scoped one."

---

## 5. Open

1. **`[GDE-SPIN-400]` Whether `sendspin-go` (the library) has any metadata/state hook not exposed by `sendspin-go-server` (the CLI)** — the single fact `[GDE-SPIN-380]` turns on. Answerable by reading the library's own godoc, not by more reading of the CLI's README.
2. **`[GDE-SPIN-410]` What per-client Opus encoding actually costs on a Pi Zero 2W for a plausible group size** (2–4 receivers) — `[GDE-SPIN-330]`'s CPU question has no measurement yet, the same way `[GDE-BAK-030]`'s trim measurement had to be taken rather than assumed.
3. **`[GDE-SPIN-420]` Whether running a second process (the sidecar) alongside Lempi's own on a 512 MB appliance is a memory decision `[REQ-HW-140]` already answers "no" to** — `sendspin-go-server`'s own footprint has not been measured against lempi02w's actual headroom.
4. **`[GDE-SPIN-430]` Who holds the pairing trust store in Architecture 1** — the sidecar process does, by construction, which means lempi02w's own backup story `[REQ-LIB-160]` would need to know about a second piece of state it does not otherwise own.

---

**Traceability:** `[GDE-SPIN-300..430]` · derived from `[GDE-SPIN-010..250]`, `[GDE-BAK-025]`, `[GDE-BAK-030]`, `[REQ-HW-140]`
