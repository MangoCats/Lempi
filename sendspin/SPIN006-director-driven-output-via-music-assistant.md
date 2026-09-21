# SPIN006: Can the Director Actually Drive Sonos Through Music Assistant?

**Development Guidance — investigated on `sendspin`, 2026-08-28**

[SPIN005](SPIN005-opensubsonic-usage-scenarios.md) `[GDE-MSA-330]` named "every Music Assistant player becomes a Lempi output" as Mode A's biggest unlock. That claim needs a direct answer to a direct question: does Mode A alone let the **Director's own choice** reach a Sonos speaker, or only let a person using Music Assistant's own UI pick a Lempi track by hand? It is the second one — Mode A is necessary but not sufficient, and the missing piece is a second integration this investigation has not priced before now.

> **Related:** [SPIN005](SPIN005-opensubsonic-usage-scenarios.md) `[GDE-MSA-330..360]` — the claim this refines · [SPIN004](SPIN004-opensubsonic-deep-dive.md) — the surface this assumes exists · [GUIDE007](../docs/GUIDE007-external-backends-investigation.md) `[GDE-BAK-025]` — the mapping problem this finds again

---

## 1. OpenSubsonic has no push, and that is the whole answer to "by itself"

**`[GDE-MSA-440]` A Subsonic-family protocol is pull-only, client-initiated, with no controller role at all.** Music Assistant, as the client, decides what to browse and when to call `stream` — nothing in `[GDE-MSA-150..180]`'s endpoint set lets a *server* tell a client "play this now." This is the opposite of Sendspin, which defines `controller@v1` explicitly for exactly this direction `[GDE-SPIN-040]`. **Lempi exposing OpenSubsonic makes its library reachable; it does not make the Director's queue drive anything.** A person picking a track in Music Assistant's own UI, or Music Assistant's own Smart Shuffle choosing one, can play through Sonos today, the moment Mode A exists. The Director choosing one cannot — not because of a missing feature in Mode A, but because Mode A was never the kind of thing that could carry a push.

---

## 2. The piece that would be needed: Lempi as a Music Assistant *control* client

**`[GDE-MSA-450]` Music Assistant has a real, separate, documented automation API for exactly this direction.** WebSocket and REST, bearer-token authenticated, with a `player_queues/play_media` command — the same primitive Home Assistant's own `media_player.play_media` service and `music_assistant.play_media` service call through. This is a genuinely different surface from the one Mode A builds: Mode A makes Lempi a **provider** Music Assistant pulls from; this would make Lempi a **client** commanding Music Assistant, the same shape GUIDE007 already scoped for driving MPD directly `[GDE-BAK-020]` — just aimed at Music Assistant's own API instead of MPD's line protocol.

**`[GDE-MSA-460]` And `play_media` takes a catalog reference, not a URL — which is where the two pieces actually meet.** Its `media` parameter is a `library://<type>/<id>` reference into Music Assistant's *own* indexed catalog, not an arbitrary stream address. So the Director cannot simply hand Music Assistant a URL and ask it to play there; **the track has to already exist in Music Assistant's library first** — which is precisely what Mode A supplies, by making Lempi a provider Music Assistant has indexed. Mode A is the prerequisite, not a substitute, for this.

**`[GDE-MSA-470]` And that reference is Music Assistant's own id, assigned on its own indexing pass — the identity-mapping problem, again.** `[GDE-BAK-025]` already named this "the part with no obvious answer" for MPD and OpenSubsonic clients in general: Lempi selects a passage in its own database; a remote catalog names the same thing by an id Lempi does not choose and cannot predict. Nothing about Music Assistant changes that finding — it recurs here in the same shape, one hop later than GUIDE007 first found it. Whoever builds this needs a live table mapping Lempi's own passage/recording identity to whatever `library://` id Music Assistant assigned it after indexing Mode A's surface, kept current as the library changes.

---

## 3. What survives the trip, if this were built

**`[GDE-MSA-480]` Passage fidelity survives, because of a choice SPIN004 already made for an unrelated reason.** `[GDE-MSA-220]` already decided `stream` should be built on `PassageDecoder`, not on serving `files.path` unmodified — precisely so a Subsonic client hears the trimmed, gained passage Vipunen actually curated, not the whole file it lives in. That decision pays off again here: whatever Music Assistant relays to Sonos is still the bytes Lempi's own `stream` endpoint produced, so the passage's own trim and gain carry through the whole path.

**`[GDE-MSA-490]` Crossfade does not, and this was already priced as an acceptable loss, not a new one.** The moment Music Assistant owns the queue for a Sonos player, sequencing between tracks is Music Assistant's job, not the engine's — the same conclusion `[GDE-MSA-220]` already reached about OpenSubsonic streaming in general applies unchanged to this path. Nothing new is lost by adding the control-API piece; it inherits exactly what `[GDE-MSA-220]` already gave up.

---

## 4. Answer

**`[GDE-MSA-500]` Yes, architecturally possible; no, not from Mode A alone; and the missing piece is unpriced and runs into this project's already-hardest open problem.** Mode A (Vipunen's library reachable by Music Assistant) plus a new, separate Lempi-as-control-client integration (the Director commanding `player_queues/play_media` against Music Assistant's own API) together would let the Director's own choice reach Sonos, Chromecast, or any other Music Assistant player. Mode A alone gets a person using Music Assistant's own app there today; it does not get the Director there at all.

---

## 5. Open

1. **`[GDE-MSA-510]` Whether the mapping table `[GDE-MSA-470]` needs is any easier here than `[GDE-BAK-025]` found it to be for MPD/OpenSubsonic in general** — Music Assistant's own indexing may expose a stable key (a Subsonic song id it preserves) worth checking before assuming a fresh unknown.
2. **`[GDE-MSA-520]` Whether this is worth building at all before `[GDE-SPIN-210]`'s still-cheaper Sendspin player mode** — this document prices a real capability, not a recommendation to build it next.

---

**Traceability:** `[GDE-MSA-440..520]` · derived from `[GDE-MSA-330..360]`, `[GDE-MSA-220]`, `[GDE-BAK-020]`, `[GDE-BAK-025]`, `[GDE-SPIN-040]`
