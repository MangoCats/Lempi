# SPIN005: What an OpenSubsonic Surface Actually Unlocks

**Development Guidance — investigated on `sendspin`, 2026-08-28**

[SPIN004](SPIN004-opensubsonic-deep-dive.md) priced what an OpenSubsonic surface on Lempi would cost. This asks the question that actually justifies paying it: what can a listener *do* afterward that Bluetooth and local ALSA/PipeWire output do not already let them do today.

> **Related:** [SPIN004](SPIN004-opensubsonic-deep-dive.md) — the surface this assumes exists · [SPIN003](SPIN003-music-assistant-ecosystem-fit.md) §1 `[GDE-MSA-020]` — the Director/Smart-Shuffle distinction this returns to · [SPEC009](../docs/spec/SPEC009-program-director.md)

---

## 1. What is actually fixed today

**`[GDE-MSA-320]` lempi02w's whole listening experience is one appliance, one output, one queue, one control surface, one network.** Whatever device is physically or Bluetooth-attached to lempi02w is the only thing that ever makes sound; the Director's queue is the only source of what plays on it; Lempi's own web UI, reached from the same LAN, is the only way to see or nudge it. Every scenario below is a way one of those five words stops being true — not a change to the Director, and not a change to what lempi02w's own speaker does when nobody has asked it to do anything else.

---

## 2. New reach for output, without Lempi writing a single output driver

**`[GDE-MSA-330]` The single biggest unlock is not a client feature — it is that Music Assistant's own player catalog becomes lempi02w's output catalog for free.** Once Lempi's library is an OpenSubsonic-reachable music provider `[GDE-MSA-190]`, Music Assistant can play it out through **any player it already supports** — Sonos, Chromecast, AirPlay, DLNA, Squeezelite, Sendspin — none of which Lempi has ever implemented and none of which this project has any reason to implement directly. This is worth stating precisely: Lempi does not gain "a Sonos integration." It gains **every player Music Assistant will ever add**, retroactively and going forward, because the seam is the library, not the speaker. **A precision this needs: it is Music Assistant's own selection — a person in its app, or its Smart Shuffle — that reaches those players this way, not the Director's.** Whether the Director's own choice can reach them too is a separate question, answered in [SPIN006](SPIN006-director-driven-output-via-music-assistant.md).

**`[GDE-MSA-340]` A dozen already-built phone and desktop apps can browse and stream Lempi's library with no Lempi UI involved at all.** OpenSubsonic client apps already exist and are already maintained by other people — the same reasoning `[GDE-SPIN-070]` made about cheap Sendspin receivers applies here to software instead of hardware. A listener with earbuds gets Lempi's library on their own phone, decoupled entirely from whatever lempi02w itself is connected to.

---

## 3. New concurrency, and the tension it is honest to name

**`[GDE-MSA-350]` A Subsonic `stream` request never touches the Director's queue** — it asks for one passage's bytes and gets them; it does not enqueue, does not skip, does not interrupt whatever the room's own speaker is doing. This means, for the first time, **more than one person can listen to different things from the same library at the same moment**: one listener on headphones via a phone app, the room's own speaker still running whatever the Director queued, neither aware of the other. Today's model has exactly one queue and one Director; this adds a second, parallel, on-demand mode beside it, not instead of it.

**`[GDE-MSA-360]` That parallel mode is also, honestly, a way to bypass the thing this project is for.** `[GDE-MSA-020]` already drew the line between Music Assistant's recency-avoidance shuffle and the Director's flavor-distance selection — the reason to prefer the Director is that it chooses better than picking by hand. An OpenSubsonic client is, by design, a hand-picking tool: search, browse, tap a track. **Both are genuinely useful and neither replaces the other** — a person who wants the Director's judgement still gets it from the room's own speaker; a person who wants *this specific song, right now, on my own headphones* gets a tool that has never existed for this library before. Naming this is not a criticism of the idea, only an acknowledgement that "the library is now more reachable" and "the Director is now more avoidable" are the same fact seen from two sides.

---

## 4. New reach past the LAN — genuinely new, and genuinely a separate decision

**`[GDE-MSA-370]` Voice access, through Music Assistant's own Home Assistant integration** `[GDE-MSA-010]`: once Lempi is a provider, "play \<artist\> from the library" becomes a sentence Home Assistant Voice PE or any HA-connected assistant can act on — something a web UI with no voice surface at all has never offered.

**`[GDE-MSA-380]` Remote and car listening, offline caching — real, and NOT granted merely by turning the feature on.** Many Subsonic clients cache tracks for offline listening or work from outside the home network; a car head unit's client could reach the same library on the road. **Every one of these requires lempi02w's OpenSubsonic surface to be reachable from outside the LAN at all** — port forwarding, a reverse proxy, or a VPN — which is a distinct, larger decision than `[GDE-MSA-260]`'s "gate it because it is a new authenticated endpoint" and should be treated as one. LAN-only is the safe default this surface would ship with; anything past it is a second, explicit choice, not a side effect of the first.

**`[GDE-MSA-390]` Public share links exist in the wider spec and are the one thing worth naming as deliberately not wanted.** OpenSubsonic's "Sharing" category was already excluded from the minimal endpoint set `[GDE-MSA-240]` on relevance grounds; it is excluded here on purpose grounds too — a shareable public URL to a passage is exactly the kind of default-on exposure `[GDE-MSA-380]`'s own "LAN-only unless asked for" posture exists to prevent.

---

## 5. Recommendation

**`[GDE-MSA-400]` If a reason to build Mode A is ever needed beyond "Music Assistant can browse the library," this is it: the output reach, not the browsing.** `[GDE-MSA-330]` — every existing Music Assistant player becomes a Lempi output — is a materially larger unlock than anything about search or smart shuffle, and is the part of this whole investigation most likely to be worth the cost [SPIN004](SPIN004-opensubsonic-deep-dive.md) `[GDE-MSA-250..270]` found to be modest.

**`[GDE-MSA-410]` Ship it LAN-only, and let remote reach be asked for separately, by name, later.** `[GDE-MSA-380]`'s distinction is the one to keep sharpest: "Music Assistant can see my library" and "my library is reachable from a cellular network" are different sentences, and this document's scenarios should never be read as proof that the second follows automatically from the first.

---

## 6. Open

1. **`[GDE-MSA-420]` Whether a passage played via an OpenSubsonic `stream` request should count toward rotation** `[GDE-MSA-310]` already asked this about `scrobble` specifically; it applies just as much to plain streaming with no scrobble at all, since the Director's own rotation bookkeeping `[REQ-PD-112]` has never had to reason about a play it did not itself queue.
2. **`[GDE-MSA-430]` Whether "the room's speaker" and "my headphones via a Subsonic app" should ever be presented to a listener as the same choice**, or whether keeping them entirely separate — as `[GDE-MSA-350]` already finds is true today by construction — is the right permanent answer rather than an implementation detail to revisit.

---

**Traceability:** `[GDE-MSA-320..430]` · derived from `[GDE-MSA-010..310]`, `[GDE-SPIN-070]`, `[SPEC009]`
