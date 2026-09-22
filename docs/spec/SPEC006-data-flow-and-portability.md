# SPEC006: Data Flow & Library Portability

**Design Specification — Tier 2**

How data originates, where it lives, and how Vipunen's derived knowledge reaches a Lempi installation that has no Vipunen. Covers the identity scheme that makes derived data re-bindable to audio on a foreign system.

> **Related:** [GUIDE002 §2 Architecture](../GUIDE002-rearchitecture-plan.md#2-architectural-decisions) · [SPEC005: Flavor Distance](SPEC005-flavor-distance.md) · [GUIDE003: Feature Extraction](../GUIDE003-feature-extraction-strategy.md)

---

## 1. Source of Truth

**`[SPEC-DF-010]` The user's music files are the only original data.** Everything else — identification, segmentation, flavor, loudness — is derived, and derivable again from the audio alone by Vipunen. `lempi.db` is a cache of expensive derivations, never a source. Deleting it must cost time, never information.

**`[SPEC-DF-020]` The bit-exact guarantee, stated precisely.** The promise is **the decoded audio stream is never altered** — not that file bytes are never touched. This distinction is what makes `[SPEC-DF-060]` metadata transport possible at all.

Measured 2026-08-09, adding a 400-byte ID3 `TXXX` frame to an MP3:

| | before | after |
| :--- | :--- | :--- |
| whole-file MD5 | `04bf496b…` | `2d8f8194…` — **changed** |
| `md5_encoded` (decoded audio) | `f1556800a10f8d1f…` | `f1556800a10f8d1f…` — **unchanged** |

Container tags live outside the audio stream. So Lempi plays byte-identical audio whether or not Vipunen has annotated the file, and **the guarantee is verifiable rather than merely asserted**: extract, tag, re-extract, compare `md5_encoded`.

---

## 2. Identity — Three Keys, Three Scopes

**`[SPEC-DF-030]`** Derived data binds to audio at three different scopes. Conflating them is what makes library migration brittle.

| Key | Scope | Source | Survives |
| :--- | :--- | :--- | :--- |
| **`audio_md5`** | *this exact encoding* | Essentia `md5_encoded`, free at extraction | re-tagging, renaming, moving, copying |
| **`recording_mbid`** | *this recording, any encoding* | MusicBrainz via AcoustID | re-encoding, different rip, different bitrate |
| **`file_path`** | *this machine only* | filesystem | nothing — never transported |

**`[SPEC-DF-035]` A local sequence number is a fourth key, and it may not cross an installation.** `passage_id` is an `INTEGER PRIMARY KEY` — efficient, and correct as a foreign-key target inside one database `[SPEC-SC-040]`. It is not an *identity*: a re-derivation renumbers passages `[REQ-LIB-160]`, so the same integer means a different passage on the same machine after a rebuild, and an unrelated one on any other machine.

The rule: **a local sequence number may be used only where the potential for confusion is structurally absent** — within a single database file, in one process's queries, as a foreign key. Anything that crosses an installation, a transport, or a rebuild keys by scope instead.

For a passage the portable key is **`(audio_md5, kind, start_ms, end_ms)`** — encoding scope, because a passage is a span of one exact encoding and its boundaries are class C `[SPEC-DF-050]`. This names a shape the schema already has rather than adding one: `passages_span` is exactly that uniqueness constraint, and `lowlevel_cache` is already keyed this way `[SPEC-SC-080]`.

What the rule prevents is silent by construction. A stale or foreign `passage_id` does not fail — it resolves to a real passage, which plays a real song under a real name, and nothing downstream can tell `[REQ-LIB-165]`.

**`[SPEC-DF-040]` Match by the narrowest key that fits the data.**

- **Recording-scope data** — flavor vector, MBIDs, artist/album/title, work relations. A property of *the music*, so it binds by `recording_mbid` and is valid for anyone holding that recording in any encoding.
- **Encoding-scope data** — passage `start_ms`/`end_ms` (DAO segmentation and the `album`/`radio`-kind split alike), `lead_in_ms`/`lead_out_ms`, replay gain, decoded duration. A property of *this rip*, meaningless against a different encode, so it binds by `audio_md5`.
- **Machine-scope data** — `file_path`, mtime, size. Never leaves the installation.

`audio_md5` is the workhorse: it is exactly MuLibPlay's `files.sig` idea `[GDE-BMK-050]`, corrected. MuLibPlay could relocate a known file; hashing decoded audio additionally survives the tag writes that `[SPEC-DF-060]` depends on.

---

## 3. What Travels, and What Must Not

**`[SPEC-DF-050]`** Four data classes, with sharply different portability rules:

| Class | Examples | Travels? |
| :--- | :--- | :--- |
| **A · Derived facts** | flavor vector, `audio_md5`, decoded duration, replay gain, chromaprint | **Yes.** Deterministic from audio; expensive; identical for everyone. |
| **B · Identification** | recording/release MBIDs, artist, album, title | **Yes.** Costs network lookups and human correction. |
| **C · Segmentation** | DAO passage boundaries, Album/Radio duality, `lead_in_ms`/`lead_out_ms` | **Yes — the highest-value payload.** This is the manual labour that made induction painful `[GDE-BMK-050]`. Sharing it is the single biggest convenience win. |
| **D · Listener state** | rotation/recovery/restraint, likes/dislikes, play history, programs | **Never.** |

**`[SPEC-DF-055]` Class D never travels with music.** It describes *the listener*, not the music: personal, private, and meaningless to anyone else. A shared file carrying someone's play history would be both a privacy leak and noise. Listener state moves only by deliberate whole-`lempi.db` migration `[SPEC-DF-080]` or the class-D export `[SPEC-DF-090]`, both between a user's own machines.

**The rule:** *the transport carries facts about the music, never facts about the listener.*

---

## 4. Three Transports

**`[SPEC-DF-060]`** In increasing order of invasiveness. All three carry the same payload schema; they differ only in where it is written.

### T1 · Embedded tags — travels inside the file

*Designed, not yet built.* No tag-writing code exists in the tree yet — `player/src/tags.rs` only reads embedded cover art. T2 (sidecar) and T3 are the transports actually implemented today; treat what follows as the settled design for T1, not a description of current behavior.

Written into the container's native tag block, so the data survives any copy, move, or rename, with nothing to keep alongside it. This is the mechanism that makes a "straight, playable audio file" self-describing.

| Container | Mechanism |
| :--- | :--- |
| MP3 | ID3v2 `TXXX` frames |
| FLAC / Ogg | Vorbis comments |
| MP4 / M4A | `----:com.apple.iTunes:` freeform atoms |

Follow MusicBrainz Picard's existing conventions where they exist (`MUSICBRAINZ_TRACKID`, `MUSICBRAINZ_ALBUMID`, `ACOUSTID_ID`) so Lempi interoperates with the wider tooling ecosystem rather than inventing a private dialect. Lempi-specific payloads take a `LEMPI_*` namespace.

Size is not a constraint: a JSON flavor payload is ~1–2 KB `[SPEC-DF-093]`, and a 40-passage DAO segmentation roughly 3 KB.

**Writes to the user's files, so it requires informed consent once, then proceeds by default** `[SPEC-DF-092]`. Tag writers can corrupt containers; every write is temp-file → verify `md5_encoded` → atomic replace, so the original is never mutated in place.

### T2 · Sidecar fragments — travels beside the file

One `.lempi.json` per audio file `[SPEC-DF-091]`. Zero risk to audio, no consent needed, works on read-only media and containers Lempi cannot tag. Lost if the audio is copied without it — acceptable, because this is the fallback for when T1 is unavailable rather than the primary route.

### T3 · Database migration — whole library at once

Ship `lempi.db` itself and rebind by `audio_md5` `[SPEC-DF-040]`. This is MuLibPlay's proven route, and the only one that can carry class D. For moving a library between a user's *own* machines.

**`[SPEC-DF-065]` T1 and T2 are the same payload in different envelopes.** One serializer, one parser, one schema version. Vipunen emits; Lempi imports.

---

## 5. Trust and Conflict

**`[SPEC-DF-070]` Imported metadata is a claim, not a fact.** It may be stale, wrong, produced by an older Vipunen, or hostile.

1. **Verify before trusting encoding-scope data.** Recompute `audio_md5`; if it disagrees with the embedded value, discard all class C data — it describes a different encode. Cheap and decisive.
2. **Class A/B may be accepted on `recording_mbid` alone**, since it is encoding-independent by construction.
3. **Provenance is mandatory** `[GDE-FBD-020]`. Every imported value records its origin — `vipunen@<version>`, `acousticbrainz-dump-20220623`, `imported:<source>`, `manual`.
4. **Conflict resolution by provenance rank, then recency.** `manual` outranks everything; a newer Vipunen model version outranks an older one; local outranks imported at equal rank. Never silently overwrite a user's correction. *(Amended 2026-09-06 — [SPEC035](SPEC035-mesh-library-sync.md) §6: "local outranks imported at equal rank" presumed only one side could ever be `manual`, true for every topology except Mesh. When **both** sides are `manual` and disagree, this rule does not apply — that case is routed to a person, not decided by either side unilaterally.)*
5. **Never execute or interpolate imported strings.** Text fields are display data.

---

## 6. Deployment Topologies

**`[SPEC-DF-080]`**

| Topology | Flow |
| :--- | :--- |
| **Co-resident** (desktop) | Vipunen scans → writes `lempi.db` → Lempi reads. No transport needed. |
| **Appliance** (Pi + desktop Vipunen) | Vipunen builds on desktop → T3 migration → Lempi rebinds by `audio_md5`. The Pi never runs Vipunen `[GDE-ARC-010]`. |
| **Sharing** (foreign install) | Audio arrives with T1/T2 payload → Lempi imports class A/B/C after verification → advanced features work with **no Vipunen present at all**. |
| **Recovery** | `lempi.db` lost. If T1 was used, re-import from the files themselves; otherwise re-derive with Vipunen. Class D is lost unless separately backed up — the one asymmetry worth warning users about. |
| **Mesh** *(added 2026-09-06)* | Two or more Vipunen-capable installations each ingest independently; an automatic diff proposes what each lacks, a person approves, bundles move in whichever direction closes the gap. Designed, not built — [SPEC035](SPEC035-mesh-library-sync.md). |

The sharing row is the point of the whole design: it lets a Lempi-only installation benefit from segmentation and flavor work it could never perform itself, because Vipunen is x86-only, AGPL, and heavyweight `[GDE-ARC-015]`.

**The Mesh row breaks an assumption every other row shares — that only one side can ever hold a manual correction — which is why it needed its own document rather than a table row alone.** See [SPEC035](SPEC035-mesh-library-sync.md) §6 for the resulting amendment to §5's trust rule below.

---

## 7. Resolved Decisions

Settled 2026-08-09. Each replaces an open question; the reasoning is kept so a future reader can tell a decision from an accident.

**`[SPEC-DF-090]` Listener state is exported automatically, class D only.**
A small, private file on a schedule — never bundled with music `[SPEC-DF-055]`. Rationale: class D is the *only* irreplaceable data in the system, since everything else re-derives from audio `[SPEC-DF-010]`. It is also small: MuLibPlay's six years amount to 37,134 play events plus ~2,900 preference rows. A full `lempi.db` snapshot was rejected as mostly re-derivable cache (164 MB, largely cover art), and manual export as depending on remembering — which is precisely why six years of MuLibPlay history sits unprotected today.

The same export is the **sync unit between a user's own machines**, so the backup path is exercised in normal use rather than only in disaster.

**`[SPEC-DF-091]` Sidecars are per-file `.lempi.json`.**
Sidecars are the fallback for when embedding is impossible — read-only media, unsupported containers, or a user who declines tag writes. On an exception path robustness outranks tidiness, and a per-file sidecar survives single-file copies that a directory manifest would not. Directory clutter is accepted as the cost.

**`[SPEC-DF-092]` Embedded tags: informed consent once at first import, then default on.** *(Designed, not yet built — see T1's status note in §4.)*
Not permanently opt-in. A default-off path receives little real-world exercise and rots, while self-describing files are the point of the design `[SPEC-DF-060]`. Not default-on-silent either: modifying a user's music library without asking is not justifiable even with verification.

Every write is **temp-file → verify → atomic replace**:
1. Copy to a temp file, write tags there.
2. Recompute `md5_encoded` on the temp file.
3. Replace the original **only** if it matches the pre-write value `[SPEC-DF-020]`.
4. On mismatch, discard the temp file, log, and mark the file tag-ineligible.

The original is never mutated in place, so a failed write cannot damage the library.

**`[SPEC-DF-093]` Payload is human-readable JSON.**
At ~1–2 KB per recording the compact alternative saves ~1.5 KB and costs inspectability, exact float precision, and debuggability. A packed fp16 form would be opaque to every tool including our own. This is a system whose headline requirement is that its processes be visible `[GDE-CHT-030]`; an unreadable payload embedded in the user's own files contradicts that for no measurable gain. One format, one parser `[SPEC-DF-065]`.

---

## 8. Export & Consent Policy

Settled 2026-08-09, completing the decisions in §7.

**`[SPEC-DF-094]` Class-D export: hourly, grandfather-father-son, integrity-checked before rotation.** *(Retention schedule corrected 2026-08-30 to match the built and verified system — see below; the design rationale in this section is unchanged and still holds.)*

1. **Verify first.** Confirm `lempi.db` is readable and self-consistent (`PRAGMA integrity_check`, plus row-count and referential sanity on the class-D tables) **before** writing a new generation. A failed check aborts the rotation and raises an alert — it never overwrites a good backup with a bad one.
2. **Retain generations, grandfather-father-son:** one per day for seven days, one per month for twelve months, one per year indefinitely, and always the newest whatever else happens. Three years of hourly snapshots thin from 26,280 raw files to **20** retained; the yearly tier is unbounded on purpose — a decade of them is ten files.
3. **Cost is negligible.** Class D is a few MB — MuLibPlay's six years is 37,134 play events, `lempi.db` holds 74,299 — so the retained ladder stays well under 100 MB indefinitely, growing by roughly one file's worth per year once the daily and monthly tiers are full.

Rationale: storage is not the constraint here; **undetected corruption is**. A single rotating snapshot faithfully copies a corrupted database over the last good copy, and the loss is discovered only when someone looks. Depth plus a pre-rotation integrity check is what makes the backup trustworthy rather than merely present. Grandfather-father-son specifically, rather than a flat depth, because the value of an old snapshot is not that it is old but that it *predates whatever went wrong*: damage noticed the same afternoon needs yesterday, damage noticed at Christmas needs March, a preference quietly corrupted two years ago needs a copy from before it.

Rejected: *on clean shutdown* (a 24/7 appliance may never shut down cleanly — or only ever lose power, exporting nothing); *daily-only, no generational ladder* (a single rotating slot loses exactly the corrupted-two-years-ago case this exists for). The **append-only journal** alternative is strictly more robust and remains the upgrade path if snapshots prove insufficient — it was declined only for the second write path it would add `[GDE-CHT-040]`.

**Built and verified as `REQ-LIB-160`.** That is the authoritative account of the shipped mechanism — the SQLite backup API against a read-only-attached library, atomic rename, `restore_listener`'s rehearse-by-default restore, the pre-restore safety copy exempt from rotation, re-pointing plays through `recording_mbid` rather than the unstable `passage_id`, and the Howard Hinnant civil-from-days date arithmetic that keeps the yearly tier from drifting. This section states the design decision and why; `REQ-LIB-160` states what was built to meet it and how it was measured — read both rather than expecting either alone to be complete.

**`[SPEC-DF-095]` Headless Vipunen falls back to sidecar-only, with an explicit `--embed` override.** *(Designed, not yet built — T1 embedding itself does not exist yet; see §4.)*

Consent applies solely where Vipunen runs. Lempi only ever *reads* tags, and the appliance never runs Vipunen `[GDE-ARC-010]`, so the Pi never faces this question.

For a non-interactive Vipunen run (scripted, batch, NAS) with no recorded consent:
1. Write `.lempi.json` sidecars `[SPEC-DF-091]` instead of touching audio.
2. Log the downgrade explicitly — silent degradation is its own failure `[GDE-CHT-030]`.
3. `--embed` grants consent for scripted use where the operator has decided.

The run still produces fully portable data `[SPEC-DF-065]`; only the envelope changes. Failing safe beats both aborting (turning a first scripted run into a diagnosis exercise) and assuming consent (modifying a user's music library on an inference).

---

## 9. Syncing an applied edit to a remote installation

Designed 2026-08-27, against `[REQ-LIB-185]`. The bundle transport (`[SPEC-SUI-095]`) answers "how does new music, and what Vipunen learned about it, reach a Lempi that has none of it." It does not answer a different question: **the appliance already has this track — how does a correction made on the desktop reach it?** `import_bundle` treats a held `audio_md5` as fully present and applies nothing further to that encoding, which is right for new music and silently wrong for an edit to old music: nothing about it is broken, it simply was never asked to do this.

**`[SPEC-DF-100]` The sync unit is the reviewed decision, not the row it wrote.** `id_reviews`, `boundary_reviews` and `artist_reviews` `[SPEC021 §2]` are already, in effect, small journals: what changed, when it was decided, and — for two of the three — what it replaced. A decision is also small (a handful of fields) where the tables it touches are not, so transporting decisions rather than diffing whole tables is both the cheaper mechanism and the one that already exists in the schema, half-built.

**`[SPEC-DF-101]` Every synced decision carries a baseline and a target, and a receiver merges the same way git does.** Comparing the receiver's *current* value at the same identity against the two:

| receiver's current value | means | action |
| :--- | :--- | :--- |
| equals **baseline** | nothing changed there since the source's own edit | **fast-forward**, no flag — apply automatically |
| equals **target** already | the same correction, or an equivalent one, already landed | **no-op**, already in sync |
| equals **neither** | changed independently since the shared baseline | **conflict** — stop, show both histories, wait for a person |

This is `[SPEC-DF-070]`'s "never silently overwrite a user's correction" rule, generalised from "an import disagreeing with the receiver" to "two installations that have each made their own decision since they last agreed."

**`[SPEC-DF-102]` A baseline is not optional, and one review table did not have one.** `id_reviews.previous_mbid` and the artist correction's `previous_artist_*` `[SPEC-SUI-197]` already capture what they replaced, for revert — and that value doubles exactly as a sync baseline. `boundary_reviews` deliberately captures no `previous_*` `[SPEC021 §2]`, because a boundary's old values are re-derivable by re-running segmentation and revert never needed them. Sync needs them for a different reason: the edit itself changes the passage's *only* portable identity, `(audio_md5, kind, start_ms, end_ms)` `[SPEC-DF-035]`, so without the pre-edit span captured, a receiver has nothing stable to resolve the decision against. This does not reopen `[SPEC021]`'s revert decision — revert still re-derives — it adds a second, narrower reason for a table to remember where it started.

`boundary_reviews` gains, captured at decision time from the passage's *current* row, the same way `id_reviews.previous_mbid` is captured from `passage_recordings`'s current row: `audio_md5`, `orig_kind`, `orig_start_ms`, `orig_end_ms`, `orig_lead_in_ms`, `orig_lead_out_ms`, `orig_gain_db`, plus `orig_fade_in_ms`/`orig_fade_out_ms`/`orig_fade_in_curve`/`orig_fade_out_curve` added alongside `fade_in_ms`/`fade_out_ms`/`fade_in_curve`/`fade_out_curve` themselves `[SPEC-SUI-226]` — a fade edit needs the identical sync-safe baseline a boundary edit already has, for the same reason `[SPEC021 §2]`.

**`[SPEC-DF-103]` Portable identity, one per kind, narrowest that fits `[SPEC-DF-040]`:**

| kind | identity | why |
| :--- | :--- | :--- |
| `artist_review` | `recording_mbid` alone | a credit is a fact about the recording, not the passage — no passage resolution needed at all |
| `id_review` | `(audio_md5, kind, start_ms, end_ms)`, read live | its own edit never touches boundaries, so the passage's current span is already stable |
| `boundary_review` | `(audio_md5, kind, orig_start_ms, orig_end_ms)` | the span *as it was*, per `[SPEC-DF-102]` — the only span a receiver that has not seen the edit can still recognise |

**`[SPEC-DF-104]` Provenance travels with the decision, across as many hops as it takes.** Each review row gains `origin` — absent for a decision made on this machine, else the hostname that first decided it. A receiver landing a synced decision stamps its own copy with the *original* `origin` and the *original* `decided_at`, never its own arrival time — `apply_reviews.py`-style provenance (`[SPEC-DF-070]`'s rule 3) applied to installations instead of import sources, so a decision synced B → C still says where and when it was actually made, not merely that C received it from B.

**`[SPEC-DF-105]` Two tools, the same rehearse-by-default shape every tool in this codebase already uses. Transport is manual, the same reason `[SPEC-SUI-095]`'s bundle transport is `[GDE-ARC-018]`: neither tool touches a network.**

```
python tools/export_changes.py <local_db> -o changes.json
rsync changes.json pi@lempi02w:/srv/library/incoming/

python tools/apply_changes.py <remote_db> changes.json               # rehearsal
python tools/apply_changes.py <remote_db> changes.json --commit      # fast-forwards land; conflicts are reported, not written
python tools/apply_changes.py <remote_db> changes.json --resolve 3=ours    # keep what's already there
python tools/apply_changes.py <remote_db> changes.json --resolve 3=theirs  # apply the incoming decision
```

`export_changes.py` reads every *applied* decision and writes one portable JSON record per row — no filtering by "already sent" in this version; idempotency (`[SPEC-DF-101]`'s no-op case) is what makes re-sending the whole history harmless rather than merely convenient, and a `--since` narrowing is a later optimisation, not a correctness requirement.

`apply_changes.py` runs directly against a database path — the listener half (`/var/lempi/listener.db` on a split peer, with the catalogue attached `[SPEC-DF-129]`; a single `lempi.db` on one that has not split), with the player stopped, the same posture `[PI5-LIB-010]` already used for the one real library swap — and needs no Lempi process, no HTTP route, and no `vipunen-support` build on the target. Landing a decision writes it twice, in one transaction: the review-table row itself (so the target's own history and any *further* sync hop can see it), and the live schema write `apply_reviews.py`/`apply_boundary_reviews.py` would have made for the same row — a synced decision becomes indistinguishable from one made locally and applied locally, except for `origin` saying otherwise.

**`[SPEC-DF-106]` A conflict report names both sides, not just that they disagree.**

```
#3 CONFLICT  artist credit for recording 99b75401-… ("Jump")
   incoming (Desktop, decided 2026-08-27 14:02): -> Dire Straits
   baseline (what Desktop saw before its edit):     Van Halen
   here now:                                        Eddie Van Halen (Solo)
     decided here 2026-08-26 09:15 -- diverged independently, no shared baseline
```

If the receiver's current value carries no review row at all — ordinary Vipunen ingest, never corrected here — the report says that instead of inventing a `decided_at` for a decision nobody made.

---

## 10. Syncing a flag's fate to a remote installation — moved

Split out to [SPEC022: Syncing a Flag's Fate, and a Console GUI Over It](SPEC022-flag-and-edit-sync.md) once this document passed its own 300-line limit `[GOV-DOC-010]`. Covers `listener_flags`'s own portable form (`[SPEC-DF-107..113]`), the pull/push tools built on it, a console GUI over both (`[SPEC-DF-114..115]`), and the settled-but-not-yet-built design for targeted remote reads at edit-open time rather than push time (`[SPEC-DF-116..118]`).

---

**Traceability:** `[SPEC-DF-010..106]` · derived from `[GDE-BMK-050]`, `[GDE-ARC-010]`, `[GDE-CHT-045]`, `[GDE-FBD-020]` · `[SPEC-DF-107..118]` continue in [SPEC022](SPEC022-flag-and-edit-sync.md)
