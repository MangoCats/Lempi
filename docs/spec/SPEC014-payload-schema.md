# SPEC014: Derived-Data Payload Schema

**Design Specification — Tier 2**

The one payload `[SPEC-DF-065]` promises and does not contain. Written because `[SPEC-SUI-130]` puts two implementations, in two languages under two licences, on either end of it.

> **Related:** [SPEC006 §3–4](SPEC006-data-flow-and-portability.md#3-what-travels-and-what-must-not) · [SPEC013 §5](SPEC013-vipunen-console.md#5-export--new-music-to-a-remote-lempi) · [SPEC008](SPEC008-database-schema.md) · fixtures in [`fixtures/payload/`](../../fixtures/payload/README.md) · serializer [`tools/payload.py`](../../tools/payload.py)

---

## 1. What this is

**`[SPEC-PL-010]`** `[SPEC-DF-065]` states the requirement — *"one serializer, one parser, one schema version"* — and then describes three envelopes without ever giving the thing inside them. That cost nothing while nothing implemented it. An exporter in Python and an importer in Rust make the absence expensive: the format would otherwise be defined by whichever was written first, and discovered by the other.

**`[SPEC-PL-015]` One payload, three envelopes.** Embedded tags `[SPEC-DF-060]`, per-file sidecars `[SPEC-DF-091]` and the bundle `[SPEC-SUI-095]` carry the same object. A bundle is many payload entries and the audio they describe; a sidecar is one entry and no audio. Nothing about the schema changes between them.

---

## 2. Shape — two arrays, because there are two scopes

**`[SPEC-PL-020]`** The payload is keyed by binding scope `[SPEC-DF-040]`, not by table.

```jsonc
{
  "payload_version": 1,
  "generator": "vipunen-payload@0.1",
  "encodings": [{                    // binds by audio_md5 — this exact rip
    "audio_md5": "b34c1da3…",
    "bundle_path": "Frisina, Gerardo/Duala - Lindeza/… 01 Duala.mp3",
    "format": "mp3", "duration_ms": 284250,
    "tags": { "title": "Duala", "artist": "Gerardo Frisina", … },
    "passages": [{
      "kind": "radio", "start_ms": 0, "end_ms": 284250,
      "lead_in_ms": null, "lead_out_ms": null, "gain_db": null,
      "boundary_src": "ingest:whole-file",
      "fade_in_ms": 20, "fade_out_ms": 20,
      "fade_in_curve": "exponential", "fade_out_curve": "exponential",
      "recordings": [{ "mbid": "local:audio:b34c…", "weight": 1.0, "source": "local:ingest" }]
    }]
  }],
  "recordings": [{                   // binds by recording_mbid — this music, any encoding
    "mbid": "local:audio:b34c…", "title": "Duala", "length_ms": 284250,
    "source": "local:ingest", "artists": [],
    "flavor": [{ "characteristic": "danceability", "class": "danceable",
                 "value": 0.9992…, "source": "local:essentia-2.1-beta2+gaia-beta1",
                 "accuracy": null }]
  }]
}
```

**They are two arrays and not one tree** because a recording may be referenced by several encodings — the schema's own many-to-many `[SPEC-SC-050]`, not a serialisation preference. Nesting would duplicate a 71-row flavor vector once per rip.

**`[SPEC-PL-025]` `bundle_path` is bundle scope — a third thing, and neither of the other two.** It says which arriving file an entry describes. It is **not** machine scope, because it never names a location on either machine, and it is not identity: the hash proves the answer and this string only shortens the search. Written with forward slashes, always. A backslash is a legal filename character on Linux, so shipping a Windows separator would not look wrong — it would name a different file `[SPEC-RLK-020]`.

**`[SPEC-PL-030]` `null` means "not analysed" and must survive as `null`.** `lead_in_ms`, `lead_out_ms` and `gain_db` are absent on all four reference tracks because amplitude analysis `[SPEC-SA-075]` has not run — 252 of 16,409 passages are in that state. Coercing them to `0` would assert a measured silence of zero length, which the player would then act on.

**`[SPEC-PL-032]` `fade_in_ms`/`fade_out_ms`/`fade_in_curve`/`fade_out_curve` `[SPEC-SC-046]` travel too, and mean something different when absent than `lead_in_ms` does.** Fade is this passage's own volume envelope, orthogonal to lead, and — unlike lead/gain — has no "not analysed" state to preserve: `passages.fade_in_ms` etc. are `NOT NULL DEFAULT` in SPEC008, so a migrated source always has a real value to send. The keys are absent from a payload only when the *sender* predates `[SPEC-SUI-226]` — a database `tools/add_fade_columns.py` has never touched — and a receiver reads that absence as "use the schema's own default," exactly what a bare `INSERT` omitting the columns would produce, not as `null`'s "no opinion, apply no ramp." `tools/payload.py`'s `build()` and `player/src/bundle.rs`'s importer agree on this, checked against `fixtures/payload/09-fade-fields.json` — `01` itself still shows the absent case, since the real library it is generated from predates the migration.

---

## 3. What does not travel, measured

**`[SPEC-PL-040]` Most of the database is Vipunen's workings.** Measured 2026-08-20 on the 1,072 MB live library:

| table | size | why it stays |
| :--- | ---: | :--- |
| `musicbrainz_cache` | **547 MB** | so Vipunen need not re-query a rate-limited service `[SPEC-SC-085]`. A player never queries. |
| `lowlevel_cache` | **202 MB** | feeds classification `[SPEC-SC-080]`; the extractor is x86-only `[SPEC-SA-018]`, so an appliance could not use it if it had it. |
| `identification_cache` | 37 MB | as above. |

**73% of the file is cache a receiver has no use for.** This is the measured form of `[SPEC-SUI-095]`'s argument, and it is stronger than the size comparison that motivated it: shipping the database is not merely 25× the audio, it is 786 MB of answers to questions the receiver will never ask.

**`[SPEC-PL-045]` Also absent, each for its own reason.** Class D — the `listener_*` tables — never travels `[SPEC-DF-055]`, and is excluded by table prefix rather than by judgement `[SPEC-SC-020]`. `ingest_decisions` and `selection_decisions` describe process, not music `[SPEC-SC-100]`. `player_state` is operational `[SPEC-SC-098]`. Machine scope — `path`, `mtime`, `size_bytes` — is supplied by the receiver from the file it actually holds `[SPEC-DF-030]`.

**`[SPEC-PL-050]` `tags` travel although they are re-derivable, and the reason is a measured one.** The four reference recordings have **no artists at all** in `recording_artists`: they carry `local:audio:` ids and never met MusicBrainz. The player resolves a display name MusicBrainz → tag → filename, so the file's own `artist` tag is the only place *"Gerardo Frisina"* exists. Omitting it would land the music artist-less until the receiver ran a probe pass — recovering what the sender already knew. The cost is 127 bytes per track.

---

## 4. Acceptance

**`[SPEC-PL-060]` Compatible means the receiver can construct what it requires, and nothing more.** `[SPEC-SUI-165]` names two ways a payload fails, and a checker implementing only the first passes fixture `04`.

*Required set* — read off SPEC008's `NOT NULL` constraints rather than chosen, which is what makes it normative rather than descriptive:

| object | required |
| :--- | :--- |
| encoding | `audio_md5`, `bundle_path`, `format`, `duration_ms`, ≥1 passage |
| passage | `kind`, `start_ms`, `end_ms`, `boundary_src` |
| credit | `mbid`, `weight`, `source` |
| recording | `mbid`, `title`, `source` |
| flavor | `characteristic`, `class`, `value`, `source` |

*Unresolvable conflict* — a payload disagreeing with **itself**. `[SPEC-DF-070]` ranks a payload against the receiver's values by provenance then recency; nothing ranks it against itself, so two titles for one mbid have equal claim and choosing either would be a guess recorded as a fact. CHECK violations belong here too: a row SQLite would refuse is not a value to reconcile.

**`[SPEC-PL-065]` `payload_version` is recorded and never consulted for acceptance.** Ordering two version numbers answers the wrong question `[SPEC-SUI-165]`: a *newer* payload that dropped a required field is incompatible, an older one may be perfectly usable. Fixture `02` declares version 2 and must be accepted.

**`[SPEC-PL-070]` Rejection is whole, and it names the unmet requirement.** One transaction; the target byte-identical. A partial import leaves a library that is neither the old one nor the new one, with nothing recording which parts are which.

**`[SPEC-PL-075]` Acceptance and arrival are different axes.** Acceptance is per bundle and all-or-nothing. Whether the audio for a given encoding actually turned up is per encoding and partial by nature — missing audio is a transfer gap, and audio that hashes wrong is `corrupt`, never `unknown` `[SPEC-RLK-055]`. Neither is a schema disagreement, and folding them into the same verdict would reject three good tracks over one truncated file.

**`[SPEC-PL-080]` Idempotent by identity, not by flag** `[SPEC-SUI-180]`. `files.audio_md5` is `UNIQUE` and `passages_span` is unique on `(file_id, kind, start_ms, end_ms)`, so a re-import matches existing rows rather than duplicating them. **Existence is checked explicitly; `INSERT OR IGNORE` is forbidden here** — it turns a `NOT NULL` violation into nothing happening, which is exactly how `apply_reviews` came to be unable to apply anything `[REQ-LIB-165]`.

**`[SPEC-PL-085]` The import binds what it creates.** Relink never creates a row `[SPEC-RLK-090]`, so it cannot bind an arriving one; the importer hashes each file it is given, verifies it against the payload `[SPEC-DF-070]`, stats it for the machine-scope columns and writes the path itself. **A separate scoped relink pass is therefore not needed for a bundle** — refining `[SPEC-SUI-105]`, whose work turns out to be the same walk. Relink proper remains for the whole-library case.

---

## 5. Size, and a correction

**`[SPEC-PL-090]`** Measured on fixture `01` — four recordings, full 71-characteristic flavor:

| form | per recording | whole library (8,330) |
| :--- | ---: | ---: |
| JSON, indented | 16.9 KB | 141 MB |
| JSON, compact | 11.0 KB | 91 MB |
| **gzip(compact)** | **1.27 KB** | **10.4 MB** |

The estimate in `[SPEC-DF-093]` — *"~1–2 KB per recording"* — **is true only of the compressed form**, and it was reasoning about the uncompressed one: it weighs readable JSON against "a packed fp16 form" saving "~1.5 KB". Readable JSON is roughly **nine times** that estimate.

**The decision it was defending survives intact, and is now better supported.** Compression reaches 1.27 KB per recording while keeping every property the packed form would have cost: inspectable, exact, debuggable, parseable by every tool including our own. The packed alternative would save fractions of a kilobyte against *this* baseline rather than the stated 1.5 KB. So: **stored readable, transported compressed**, and `[SPEC-DF-093]`'s arithmetic is corrected without its conclusion moving.

---

## 6. Open

1. **`[SPEC-PL-100]` `flavor.accuracy` is `NULL` on all 578,452 rows.** `[SPEC-SC-070]` says it carries the model's measured error into the distance metric `[SPEC-FD-120]`, populated from the model manifest, and nothing has ever populated it. The payload carries the field; today it carries nothing in it. Whether the importer should fill it from its *own* manifest — the accuracies are a property of the model, not the recording — is unsettled and probably yes.
2. **`[SPEC-PL-105]` Releases and cover art are specified but not yet emitted.** `releases`, `release_recordings` and `cover_art` are class B and belong in the payload; the reference recordings have none, so the serializer has nothing to test against and does not yet write them. Additive `[SPEC-PL-065]`.

---

**Traceability:** `[SPEC-PL-010..105]` · derived from `[SPEC-DF-050]`, `[SPEC-DF-065]`, `[SPEC-DF-093]`, `[SPEC-SUI-165]`, `[SPEC-SUI-180]`
