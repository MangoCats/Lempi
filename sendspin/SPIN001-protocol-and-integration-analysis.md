# SPIN001: Sendspin — What It Is, and How Lempi/Vipunen Might Meet It

**Development Guidance — investigated on `sendspin`, 2026-08-28**

What the Sendspin multi-room audio protocol actually is, who is behind it, and the ways Lempi's engine or Vipunen's library could plug into its ecosystem — evaluated the way [GUIDE007](../docs/GUIDE007-external-backends-investigation.md) evaluated MPD and OpenSubsonic: cost against what already exists here, not against what would be nice.

> **Related:** [GUIDE007](../docs/GUIDE007-external-backends-investigation.md) is the sibling investigation this borrows its shape from · [`[REQ-HW-140]`](../docs/spec/REQ002-functional-requirements.md) the appliance's memory budget · [SPEC009](../docs/spec/SPEC009-program-director.md) the Program Director, whose selection is what any of this would carry · [https://esphome.io/components/sendspin/](https://esphome.io/components/sendspin/), [https://www.sendspin-audio.com](https://www.sendspin-audio.com), [https://www.sendspin-audio.com/spec/](https://www.sendspin-audio.com/spec/), [https://github.com/Sendspin](https://github.com/Sendspin)

---

## 1. What Sendspin is

**`[GDE-SPIN-010]` A synchronized multi-room audio protocol, not a streaming format.** Snapcast, AirPlay and Chromecast each solve "get this audio to that device"; Sendspin's stated goal is narrower and harder — keep several devices' output within a fraction of a millisecond of each other, so a listener moving between rooms never hears an echo. The reference demo claims ±0.2–0.5 ms across four rooms. Time sync is carried by a dedicated message pair (`client/time` / `server/time`) that clients feed into a Kalman filter (`time-filter`, a separate reference repo) before ever reporting themselves available to play — a player that has not converged does not join.

**`[GDE-SPIN-020]` Governed, not vendor-owned.** Built by the **Open Home Foundation** (the Swiss non-profit that also stewards Home Assistant) and released January 2026 under the name Sendspin — formerly "Resonate." Reference client and server are Apache 2.0. This matters for the same reason MPD's protocol stability mattered to `[GDE-BAK-090]`: a protocol one company can paywall or discontinue is a worse thing to depend on than one it cannot.

**`[GDE-SPIN-030]` The wire shape.** WebSocket (`ws://`, port 8928, path advertised over mDNS) carrying a cleartext handshake and then Noise-Protocol-encrypted (`KKpsk2`, ChaCha20-Poly1305 or AES-GCM) binary frames. Devices are identified by long-lived Curve25519 keypairs, not passwords; pairing is a PSK exchange, a dynamic PIN via a PAKE (CPace-X25519), or a static PIN behind a physical button — three flows, each documented to RFC-2119 rigor in the [spec repo](https://github.com/Sendspin/spec). Audio itself streams as binary frames (`stream/start` names codec/rate/channels — Opus or PCM observed; Music Assistant currently sends 16-bit) tagged with server-domain timestamps a player schedules against, not "send fast and hope."

**`[GDE-SPIN-040]` Six roles, and a device declares which it plays.** `player@v1` (outputs audio), `source@v1` (supplies it), `controller@v1` (issues play/pause/seek/volume), `metadata@v1`, `artwork@v1`, `visualizer@v1`, plus a reserved `color@v1` for smart lighting. A server activates whichever roles a connecting device advertises support for; nothing requires a device to be all six. This role split is the single fact that shapes every option in §3 below — Lempi does not have to become a Sendspin *server* to speak Sendspin at all.

**`[GDE-SPIN-050]` Still experimental, by its own authors' word.** ESPHome's own component page: *"The Sendspin protocol is not yet finalized and this component is considered experimental."* The core message version is pinned at `1` with an exact-match check — a future revision bumps it rather than silently drifting — but nothing here has years of stable-distro mileage the way MPD's `rangeid` did `[GDE-BAK-090]`. Treat every number and message shape above as "true as documented in August 2026," not as a load-bearing constant.

---

## 2. The ecosystem, as it stands

**`[GDE-SPIN-060]` One reference server, and it is a serious one.** Music Assistant — a Python, HomeAssistant-adjacent, self-hosted "aggregate every music source into one library and multi-room system" project — shipped Sendspin as its own native transport in **2.8** (March 2026) and added visualizer support in **2.9** (June 2026). It is described, in Music Assistant's own docs, as *"Music Assistant's own way of sending audio to a player."* This puts a real, actively developed piece of software in the position Sonos or a Squeezebox server would occupy for other protocols — and it is a **library aggregator with a Program-Director-shaped hole of its own**, which matters for §3.

**`[GDE-SPIN-070]` The receiving hardware is cheap and multiplying.** A `sendspin-cpp`-based ESPHome component targets any ESP32 board; the reference "SendspinZero" is quoted at **under $10** in parts (ESP32-S3 + DAC). Home Assistant Voice PE ships Sendspin support in recent firmware at $59/unit. Music Assistant 2.8 also added **Sendspin Bridges**, wrapping existing Chromecast- and AirPlay-capable hardware (including Sonos) so they join a Sendspin group without themselves speaking the protocol.

**`[GDE-SPIN-080]` SDKs exist in eight languages, including the two this project is written in.** `aiosendspin` (async Python — Vipunen's language) backs Music Assistant's own implementation; `sendspin-rs` (Rust — Lempi's language) is explicitly marked work-in-progress. Neither has meaningful README documentation yet, consistent with `[GDE-SPIN-050]`'s "experimental" framing — a real implementation here would be reading the spec and the reference C++/Go/Python clients side by side, not `cargo add` and go.

**`[GDE-SPIN-090]` What this is not, yet: a large installed base.** No popcon-style figures exist for a nine-month-old protocol the way `[GDE-BAK-065..095]` could measure for MPD. The honest read is "one well-resourced open-source project has adopted it as its native transport, hardware vendors are shipping support, and the spec is written like something intended to last" — a bet on trajectory, not a measurement of reach.

---

## 3. Three ways to meet it, and what each costs

**`[GDE-SPIN-100]` The role split means "integrate with Sendspin" is not one decision.** Ranked by how much of Lempi's own reason for existing — the Program Director's selection — the mode preserves.

| Mode | Lempi/Vipunen is a Sendspin… | Director stays in charge? | New protocol surface |
| :--- | :--- | :--- | :--- |
| **A** | **server**, hosting its own group | Yes — fully | server/init, hello, activate, time, stream, group/state, pairing (all of it) |
| **B** | **source**, feeding an existing server | Yes, for what it plays | client handshake + `source@v1` + enough `controller@v1` to report state honestly |
| **C** | **player**, joining someone else's group | No — a remote controller picks | client handshake + `player@v1` only |

**`[GDE-SPIN-110]` Mode A — Lempi as its own hub.** lempi02w already decodes and mixes audio in a fixed-capacity buffer per passage `[GDE-FBD-010]`; feeding that same PCM to a Sendspin server component alongside the existing local output is additive, not a replacement — one lempi02w with the real library and the Director could drive a houseful of $10 ESP32 receivers in sync, which is a materially different value proposition than one appliance per room. It is also the most protocol to build: the full handshake, Noise encryption, at least the Pairing-PSK flow (a PIN flow needs a display or a button this appliance may not have), the Kalman time-filter, and Opus or PCM framing. Unlike GUIDE007's MPD/Subsonic adapters — one alternate backend swapped in for the local engine — this is fan-out **alongside** it, a shape nothing in `player/src/playback.rs`'s `Playback` trait currently models. Deeper dive: [SPIN002](SPIN002-server-mode-deep-dive.md).

**`[GDE-SPIN-120]` Mode B — Lempi as a source into an existing Music Assistant (or other server).** Narrower: implement the client handshake plus `source@v1`, offer the Director's chosen stream, and let whatever Sendspin server is already on the network (most plausibly Music Assistant, since it is the one with real install numbers) do the fan-out, pairing UI, and group management Mode A would otherwise require Lempi to build from scratch. This is architecturally the `sendspin-jack-bridge` pattern already published by the Sendspin org — a process that generates audio handing it to Sendspin rather than serving it. The cost is smaller and the risk is different: Lempi's selection now depends on a second piece of software staying up, and "who is the controller of record" needs an answer (`controller@v1` reporting, so Music Assistant's own UI shows the Director's choice rather than "unknown").

**`[GDE-SPIN-130]` Mode C — lempi02w as a plain Sendspin player.** The cheapest by far — `player@v1` only, no source or server role — and the only one where **the Director is bypassed entirely**: some other controller picks what plays, and lempi02w is a synced speaker like any $10 ESP32. This has a real, narrow use: a housemate casting from Music Assistant's own library to the room lempi02w occupies without displacing whatever lempi02w itself is doing when idle. It earns no line in `[SPEC009]`'s own story and should be judged only as "the appliance can also be an ordinary Sendspin speaker when asked," never as a Director feature.

**`[GDE-SPIN-140]` A fourth, non-protocol option: Vipunen talks to Music Assistant's catalog, not its wire format.** Vipunen's derived facts — corrected identifications, artist credits, `[REQ-LIB-195]`'s flag-and-sync mechanism — are data-flow problems already solved for *Lempi-to-Lempi* sync (`[SPEC006 §9, §10]`). Whether any of that has a receiving end in Music Assistant's own database is a question about Music Assistant's import/metadata API, not about Sendspin at all, and is out of scope for this document; noted here only so it is not mistaken for a fifth Sendspin mode. Answered in full in [SPIN003](SPIN003-music-assistant-ecosystem-fit.md), which found the cheaper route runs through the OpenSubsonic adapter `[GDE-BAK-020]` already priced, not through Music Assistant's own plugin API `[GDE-MSA-050]`.

---

## 4. What any of it would cost, concretely

**`[GDE-SPIN-150]` New dependencies, same question GUIDE007 already asked and answered once.** None of Noise-protocol crypto, WebSocket framing, mDNS advertisement/discovery, or Opus encode/decode are in `player/Cargo.toml` today. `axum` already carries a `ws` feature (`[REQ-HW-140]`'s existing budget line), so the WebSocket transport is close to free; Noise (a `snow`-crate-sized dependency) and Opus are not. Mode C is the cheapest to *try*: `player@v1` alone needs no source-side encoder, since it only ever decodes what the server sends.

**`[GDE-SPIN-160]` The mapping problem GUIDE007 found for MPD does not recur here — a different one takes its place.** MPD needed a passage-to-server-song identity mapping `[GDE-BAK-025]`; Sendspin never asks Lempi to name a track to a peer at all, since audio travels as raw frames, not by reference. What it does ask for for Mode A/B is a **pairing UI** lempi02w may not have the input hardware for — the PIN flows assume a display or a button `[GDE-SPIN-030]`; the Pairing-PSK flow avoids that but needs an out-of-band provisioning step (a QR code, a config file) instead.

**`[GDE-SPIN-170]` Time sync is the part with no local precedent to lean on.** Lempi's crossfade and gain machinery already reasons in milliseconds within one output stream; keeping a *second, independently-clocked* device within half a millisecond of it is a new problem this codebase has not had to solve before, however small the `time-filter` reference implementation turns out to be.

---

## 5. Keeping it out of the way, if any of it is built

**`[GDE-SPIN-180]` Cargo feature, default off — the same shape `[GDE-BAK-050]` already proposed for MPD/Subsonic:**

```toml
[features]
sendspin = ["dep:snow", "dep:audiopus"]   # or whatever the chosen crates turn out to be
```

A build without it contains no Noise code, no Opus code, and no larger binary — the appliance's 6.8 MB baseline stays the number quoted in `[GDE-BAK-050]` until someone opts in.

**`[GDE-SPIN-190]` Mode C first, if anything, precisely because it is reversible and additive.** It touches no selection logic, no schema, and can sit beside the existing local output exactly the way `[SPEC018]`'s backend-switching seam already tolerates a second consumer of the same engine. Mode A or B — the ones that actually extend the Director's reach — are a substantially larger undertaking against a protocol that, by its own authors' admission, has not finalized yet; building against it today is building against a moving target.

---

## 6. Recommendation

**`[GDE-SPIN-200]` Watch, prototype small, commit to nothing yet.** The protocol is well-specified for something nine months old, backed by an organization with no incentive to abandon it, and already has real hardware and a real reference server — better signals at this stage than MPD's own were ever going to need, since MPD had decades to accumulate `[GDE-BAK-065..095]`'s numbers and Sendspin cannot yet. But "not yet finalized" is the authors' own word, and the two modes that would actually matter to this project's mission (A and B, both keeping the Director in charge) are also the two with the most protocol surface to build against a spec that may still move.

**`[GDE-SPIN-210]` If a first step is taken, take Mode C.** It is the cheapest, the most reversible, and the only one where getting it wrong costs nothing more than an unused feature flag — unlike Mode A or B, it never risks the Director's own selection being wrong, because it never runs while the Director is choosing anything.

---

## 7. Open

1. **`[GDE-SPIN-220]` Whether `sendspin-rs` matures enough to build on**, rather than implementing the handshake and Noise layer directly against the spec — worth re-checking before any Mode A/B work starts.
2. ~~**`[GDE-SPIN-230]` Whether Music Assistant's own catalog has any use for what Vipunen already derives**~~ — answered in [SPIN003](SPIN003-music-assistant-ecosystem-fit.md).
3. **`[GDE-SPIN-240]` What pairing looks like on a Pi appliance with no display and no dedicated button** — the gap `[GDE-SPIN-160]` names without resolving.
4. **`[GDE-SPIN-250]` Whether Opus is worth the dependency over PCM** for a protocol still willing to accept 16-bit PCM in its own flagship server implementation `[GDE-SPIN-060]`.

---

**Traceability:** `[GDE-SPIN-010..250]` · derived from `[GDE-BAK-010..120]`, `[REQ-HW-140]`, `[SPEC009]`, `[GDE-FBD-010]`
