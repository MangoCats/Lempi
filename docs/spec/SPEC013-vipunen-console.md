# SPEC013: Vipunen Console

**Design Specification — Tier 2 · PROVISIONAL**

Vipunen's web interface `[REQ-LIB-170]`: the surface that makes induction something a person can see and start, rather than seven scripts whose order lives in someone's head.

> **Status.** §§1–4 are settled against tools that exist. **§5 specifies a two-part deliverable** — Vipunen's exporter and Lempi's importer, one payload between them — and neither half is built.

> **Related:** [SPEC007 Vipunen](SPEC007-vipunen-architecture.md) · [SPEC006 Data Flow](SPEC006-data-flow-and-portability.md) · [SPEC012 Relink](SPEC012-library-relink.md) · [SPEC008 Schema](SPEC008-database-schema.md) · [REQ002 §4](REQ002-functional-requirements.md#4-library-building--lib-vipunen)

---

## 1. Why this exists

The pipeline works and nobody can see it. Seven stages `[SPEC-SA-020]`, each a script with its own flags, run in an order recorded nowhere — which is precisely the failure `[GDE-BMK-050]` names in MuLibPlay, reproduced with better parts. A four-track EP sat in `Music/Mangocats/Tropicat` for four months, perfectly readable, and what eventually found it was a person thinking to look.

Three consequences, and together they are the requirement:

- **What is known must be inspectable.** `ingest_decisions` records what each stage chose, at what confidence, and what it rejected `[SPEC-SC-100]`, `[SPEC-SA-085]` — and nothing reads it. A durable record no one can open is a log line with extra ceremony.
- **What is *not* known must surface without being asked for by name.** `Frisina, Gerardo` — six files, two albums — was absent from the library for exactly as long as nobody dry-ran a tool at that folder.
- **Adding music must be one act**, not seven each of which can be skipped in silence.

---

## 2. Identity and boundaries

**`[SPEC-SUI-010]` One process, on the loopback interface, for one operator.**

| | Value | Forced by |
| :--- | :--- | :--- |
| Language | Python | Vipunen is Python `[SPEC-SA-010]`; another language would shell into it anyway |
| Binding | **`127.0.0.1` only** | it holds the library's write lock, spawns subprocesses and reaches ssh keys |
| Server | stdlib `socketserver.ThreadingTCPServer` + a `BaseHTTPRequestHandler` | [requirements.txt](../../tools/requirements.txt) records fastapi/uvicorn as the **v1 player's**, since removed; four pages do not justify their return `[GDE-FBD-060]` |
| Concurrency | one job at a time | §4 |

Lempi's player is a LAN service for a household. This is an operator's console on the machine that owns the library, and the two exposures are not comparable.

**`[SPEC-SUI-012]` In production there is one live database, and both programs are in it.** `[SPEC-DF-080]`'s co-resident row is the whole arrangement — Vipunen writes `lempi.db`, Lempi reads it, no transport — so the shared file is not a deployment variant of `[SPEC-SA-015]`'s channel, it *is* the channel. The only other copies are its backups `[SPEC-DF-094]`. **Several databases at once is a development and testing condition**: `data/`, `scratch/` and the appliance's own file are all real files, and none of them is the live one.

Two consequences run through this document:

- **Nothing here may assume exclusive access** (§4). A player is reading, and writing player state, throughout every job the console runs.
- **The guards that tell one database from another exist for development**, where several are reachable and choosing wrong is silent. Production is the case where they cannot bite — which is precisely why they must be structural rather than remembered `[SPEC-SUI-170]`.

**`[SPEC-SUI-015]` The console drives the stages; it does not contain them.** Every view is a client of the same module a person runs by hand — [`ingest_folder.py`](../../tools/ingest_folder.py), [`extract_library.py`](../../tools/extract_library.py), [`fingerprint_ids.py`](../../tools/fingerprint_ids.py), [`fetch_releases.py`](../../tools/fetch_releases.py), [`choose_release.py`](../../tools/choose_release.py), [`fetch_cover_art.py`](../../tools/fetch_cover_art.py). Two ingest paths is the fault `[GDE-FBD-040]` names, and the CLI must keep working unattended `[SPEC-DF-095]` — a console that becomes the only way in has broken headless operation to add a button.

**`[SPEC-SUI-020]` It is not the player's browse page, and the difference is the point.**

| Lempi `/browse` `[REQ-VIS-180]` | Vipunen `/library` |
| :--- | :--- |
| artist → album → track, then a queue verb | file → passage → profile, then a stage |
| what a listener needs in order to choose | provenance, accuracy, and what was rejected |
| never shows extraction state | never has a queue verb |

One shared page means either a listener's page carrying model error figures, or an operator's page offering playback on a machine with no speakers. They browse the same rows to answer different questions.

**`[SPEC-SUI-025]` No Vipunen process ever speaks to a Lempi process** `[SPEC-SA-015]`. Export writes files and moves them with ssh and rsync (§5). The receiving Lempi is never asked a question and need not know Vipunen exists — which is `[REQ-PORT-100]` restated as a network fact rather than an aspiration.

A **link** is not a channel, and §3.4 leans on the difference: a hyperlink or a frame is the *browser* fetching a second page, with the two servers never in contact. What must not appear is Vipunen calling a Lempi route and reading the answer.

---

## 3. The interface

**`[SPEC-SUI-030]`** *(Route table corrected 2026-09-02 — the original below was a day-one sketch; `console.py`'s own comments already flagged two of its entries as stale. This reflects the real, larger surface, grown well past "four views, one page each.")*

```
GET  /                          library: artist / album / unidentified
GET  /profile/<id>              one recording or passage, everything known and how
GET  /folder                    configured roots, and their state
GET  /api/folder/scan           cheap pass — read-only, so a refresh is safe
POST /api/induct/propose        propose -> a plan, nothing written
POST /api/induct/<plan>/commit  run the plan as a job
GET  /jobs                      running and recent
GET  /api/jobs/<id>/stream      progress, server-sent events
POST /api/jobs/<id>/stop        interrupt; resumable [REQ-LIB-130]
GET  /export                    the deploy page
POST /api/export/bundle         build a bundle, as a job — pushing it is prepared, not performed [SPEC-SUI-110]
GET  /flags                     Lempi's own "flag this" worklist [SPEC-SUI-202]
GET  /system                    which instance this is, and stopping it [SPEC-SUI-210]
```

Not exhaustive — `/api/profile/<id>/{remote,flag,accept-remote,unflag}`, `/api/remote{,/pull,/push}`, `/api/reanalyze`, `/api/analyze-amplitude`, `/api/release/{suggest,accept}` and `/api/system/shutdown` all exist beside these (§§3.5–3.8, §4) but are workflow detail this table isn't meant to enumerate. Two entries from the original sketch were never built at all: `POST /folder/hash` — `[SPEC-SUI-060]`'s named remediation for an *assumed* file remains unimplemented (§3.2) — and `GET /export/<target>/delta`.

Server-sent events rather than a WebSocket: the player pushes continuous state at a listener and needs a duplex socket; a job emits progress in one direction and takes its commands as POSTs. SSE is the smaller mechanism that fits `[GDE-FBD-060]`.

### 3.1 Library — what is known, and how it came to be known

**`[SPEC-SUI-040]` A profile is the whole derivation, not the metadata.** One passage's page carries: identity (MBID or `local:audio:`, with `source`); boundaries, lead-in/lead-out, fade-in/fade-out, gain and `boundary_src` `[SPEC-SC-040]`, `[SPEC-SC-046]`; whether `lowlevel_cache` holds features for this exact slice `[SPEC-SC-080]`; and the flavor vector as *n* of 71 characteristics, each with its own `source` and measured `accuracy` `[SPEC-SC-060]`, `[SPEC-SC-070]`.

Per-characteristic provenance is displayed because it is stored per characteristic. A library mixing local and inherited values costs ~8 points of retrieval accuracy `[SPEC-FD-145]`, and a page showing one aggregate "flavor: yes" would hide the mixture that matters.

**`[SPEC-SUI-045]` The rejected candidates are shown beside the chosen one.** `choose_release.py` writes its margin and its runners-up to `ingest_decisions` on the stated grounds that *"a selection nobody can argue with is a selection nobody can correct"*. This view is where the arguing happens; without it the table is write-only `[GDE-CHT-030]`.

### 3.2 Folder — what is here, and what is known about it

**`[SPEC-SUI-050]` Keyed by hash, never by path.** The path-comparison version of this view is the mistake `[SPEC-RLK-020]` already dissects: `os.path.exists` on Windows is case-insensitive, 628 of this library's paths carry non-ASCII characters, and NFC/NFD normalisation differs across the platforms involved. A folder view built on string equality reports well-kept libraries correctly and lies about every other kind.

**`[SPEC-SUI-055]` Two axes, because "has a profile" is not one bit.** Identity and completeness are independent, and one status word would have to conflate them.

*Identity* — one of, reusing relink's vocabulary rather than minting a second `[SPEC-RLK-050]`:

| | meaning | offer |
| :--- | :--- | :--- |
| `unknown` | no row holds this hash | induct |
| `here` | a row holds it, and its path is this file | — |
| `elsewhere` | a row holds it; its path points somewhere else | relink |
| `corrupt` | a row's path **is** this file and the bytes disagree | never induct `[SPEC-RLK-055]` |

**Status: designed, not yet built.** `tools/console.py`'s `scan()` currently reports only aggregate `assumed_here`/`changed`/`unclaimed`/`missing` counts, not this four-state per-file classification, and `/folder` offers no per-row induct/relink action — relink itself is a standalone binary (`player/src/bin/relink.rs`) not yet wired into the console at all. See `[SPEC-SUI-060]`'s `assumed`/`missing` output, which is what actually ships today.

*Completeness* — independent ticks, not a score: passages (whole-file or segmented), lowlevel cache, flavor (*n*/71), MBID against `local:audio:`, release chosen, cover art.

After `ingest_folder.py --commit` a file has a row, one whole-file passage and **no flavor at all**. That is a real and correct intermediate state — the Tropicat EP's exact state — and a green dot would call it finished.

**`[SPEC-SUI-060]` Cheap pass first, and the page says what it took on trust.** Hashing 7,232 files costs about nine minutes at the measured 74 ms each `[SPEC-RLK-070]`, which is not a page load. So the scan compares `size_bytes` and `mtime` — the columns `[SPEC-SC-030]` provides for exactly this — and hashes only what is unclaimed or disagrees.

That is a weaker check, so it is labelled as one. A file passed on size and mtime is shown as *assumed*, not *verified*, and `--hash` re-checks a named subset properly. The hazard `[SPEC-RLK-140]` identifies is a check that reports success without observing anything; it is avoided by saying which of the two happened, never by pretending they are the same.

### 3.3 Induct — the pipeline as one act

**`[SPEC-SUI-070]` Propose, then commit.** Every tool here already refuses to write without `--commit` or `--apply`, and the console keeps that shape: `POST /induct` returns a **plan** — these files, these stages, this estimated cost — and writes nothing. The plan is what gets confirmed, so what runs is what was read.

**`[SPEC-SUI-075]` Stages are chosen for the material, not run blindly.** Segmentation is for DAO captures and wrong for single-recording files `[SPEC-SA-070]`; identification of self-published audio will return `unmatched`, which is not a failure `[REQ-LIB-165]`. The plan states which stages it will skip and why, because a stage silently omitted is how a library ends up with 91% of its descriptors inherited and nothing recording it `[GDE-V1-010]`.

### 3.4 Handoff — the player's own pages, inside Vipunen's workflow

> **Status.** The mechanics below — `[SPEC-SUI-140]`, `[SPEC-SUI-145]`, `[SPEC-SUI-150]`, `[SPEC-SUI-155]`, `[SPEC-SUI-170]` — were decided 2026-08-20 and **built 2026-08-27**: `ensure_lempi()` (`console.py` at the time, split into `tools/lempi_control.py`), and `review.js`'s `?passage=` deep link. `[SPEC-SUI-135]`'s waveform editor is [built too](SPEC021-waveform-boundary-editor.md) as of the same date. **Correction, found building `[REQ-LIB-190]`:** the profile page had only ever grown the "Review in Lempi" box; the editor's own handoff was designed and its mechanics existed, but nothing on the page linked to `/edit/:passage_id` at all. `handoffBox()` is now parameterised by route and label, called twice — id review and the waveform editor are the same handoff shape aimed at a different route, not two mechanisms.

**`[SPEC-SUI-140]` Where a capability is naturally Lempi's, it is built there and *presented* through Vipunen's workflow.** *(Decided 2026-08-20.)* Not "left there and linked to as a concession" — chosen there, because that is where the resources for it already are, and then composed into the operator's sequence here.

Identification review is the first case. The review page can **play the passage**, and hearing it is the only thing that settles a case the names cannot `[REQ-LIB-165]`. Rebuilding it here would mean giving an audio path to a program defined by not having one `[SPEC-SA-100]`; duplicating the queue would mean two lists of the same disputes, disagreeing inside a week.

**`[SPEC-SUI-135]` The waveform editor is the second case, and it lives in Lempi.** *(Decided 2026-08-20.)* `[SPEC-SA-080]` requires reviewable, overridable segmentation and amplitude results — draggable boundaries, lead-in/lead-out markers, gain, and (added after the fact `[SPEC-SC-046]`) fade-in/fade-out markers and curves. Every resource that needs is already in the player: decoders, the audio path, the passage span, and the means to hear the edit while making it. **Its natural place of *use* is the induct workflow; that is not a reason to reimplement waveform handling in Python.** It is built in Lempi, reached from a passage's profile page, and `[SPEC-SA-100]` stands unamended — Vipunen still does not play audio.

**`[SPEC-SUI-138]` A co-resident Lempi is therefore a dependency, not a convenience.** Vipunen always runs where a local player does `[SPEC-DF-080]`, and this section spends that assumption rather than merely benefiting from it. It costs nothing architecturally: no code is linked, no process is shared, and the licence direction is untouched `[GDE-ARC-018]` — a browser following a link is not Vipunen incorporating Lempi.

**`[SPEC-SUI-170]` Vipunen starts the local player when it needs one, and names what is lost if it cannot.** *(Decided 2026-08-20.)* Lazily — at the moment a handoff is taken, not at console start. A session that never opens a profile page never needs a player.

1. **Already running?** Use it. Do not start a second.
2. **Not running?** Start it, on **Vipunen's own database path**.
3. **Start failed?** Say which capability is now unavailable, and why. Silent degradation is its own failure `[SPEC-DF-095]`.

**Step 1 is correctness, not tidiness.** Two players on one library contend for the audio device and both write the single resume row `[SPEC-SC-098]`, so a duplicate launch damages a session the operator already had.

**Step 2 is what makes `[SPEC-SUI-150]` sound.** That rule needed a structural guard rather than a documentary one, and this is it: Vipunen hands its *own* db path to the player it starts, so the handoff target is reading the file the passage ids came from — because Vipunen told it which file, not because a configuration was kept accurate by hand. In production this names the one live database there is `[SPEC-SUI-012]`; it earns its keep in development, where several are reachable and the wrong one still opens.

**Liveness is a socket question, not a route question.** Vipunen may ask the operating system whether the port accepts a connection; it may not ask Lempi anything about the library. `[SPEC-SUI-025]`'s invariant is about application exchanges, and a player Vipunen started is a child process it supervises rather than a service it queries.

The affordance is therefore always drawn — a launch that has not been attempted cannot be known to fail — and step 3 is where the operator learns, in the words of the thing they wanted: *this passage cannot be played here because the local player would not start.*

**`[SPEC-SUI-213]` Built 2026-08-30, narrowing "not a route question" by exactly one route: a reachable port is not necessarily a *capable* one.** Found live the same day: a plain, appliance-equivalent `lempi.exe` (`vipunen-support` is opt-in, `default = []` `[SPEC-SUI-190]`) answers step 1's socket check identically to a desktop build, then 404s every review/edit link — a dead page in the browser with no reason given, indistinguishable from a working handoff until clicked. `tools/lempi_control.py`'s `_lempi_has_vipunen_support()` (`console.py` at the time, split into `lempi_control.py`) asks one further, narrow question before declaring either step 1 or step 2 a success: does `GET /review.js` — a static asset that exists at all only because that build feature compiled it in — answer below 400. Nothing about *this library or any library* is read, which is the boundary `[SPEC-SUI-025]` actually protects, not "no route is ever asked about" read literally; the distinction is build **capability** versus application **data**, and only the latter is off limits. A capability miss is reported exactly like a start failure already is — by name, pointing at `HOWTO.md §2` — rather than left for a click to discover.

**`[SPEC-SUI-227]` Built 2026-08-31, narrowing "is it there and capable" by one further question: is it *current*.** Found live the same day: a co-resident Lempi that was the only one running, and that answered `[SPEC-SUI-213]`'s own capability probe, was still hours behind the checkout — a boundary edit saved through it did not reappear on reopening the editor, because the merge-with-draft logic that fixed that had landed in a commit the running binary predated. Neither earlier check catches this: `[SPEC-SUI-170]`'s reuse rule and `[SPEC-SUI-213]`'s capability probe both ask "is something there," never "is it the same something this checkout would build." `GET /build` — unconditional, the same build-capability-not-library-data boundary `[SPEC-SUI-213]` already draws — answers with the running Lempi's own compiled-in commit hash, the same one its Settings page already shows a person. `tools/lempi_control.py`'s `_lempi_staleness()` (`console.py` at the time, split into `lempi_control.py`) compares that against Vipunen's own commit snapshot (`[SPEC-SUI-211]`, taken once at Vipunen's own startup) and names a mismatch instead of leaving a fix silently absent. Silent, not refused, whenever either identity is unknowable — no git checkout under Vipunen, no `/build` route on a Lempi too old to have it — the same honest skip `[SPEC-DF-095]` already asks for elsewhere, since there is nothing here worth guessing past.

**`[SPEC-SUI-145]` The round trip closes through the database, not through the browser.** Vipunen hands off; the operator works in the player; Vipunen learns what changed by re-reading the shared file — `id_reviews`, `passage_recordings` — on its next scan. Nothing reports back, because `[SPEC-SA-015]`'s one channel is the file and it is sufficient. This is what keeps a link from quietly becoming an integration: no callback, no postMessage, no polling a Lempi route.

The division of labour that follows is worth stating plainly, because it is not the obvious one:

| Vipunen does | Lempi does |
| :--- | :--- |
| **read** the shared tables and show the counts — *"12 passages need id review"* | own the queue and the verbs |
| link, with the filter already applied | play the passage, take the decision |

Reading is not duplication. Vipunen showing that twelve disputes exist is what makes the handoff discoverable at all, and it costs one query against a file it already holds open.

**`[SPEC-SUI-150]` Handoff may use `passage_id` **because** it never leaves the installation.** Every relevant player route is passage-keyed — `/review/:passage_id/:decision`, `/why/:passage_id`, `/queue/:passages/:action` — and a local sequence number is admissible exactly where the potential for confusion is structurally absent `[SPEC-DF-035]`. Handoff qualifies: same database file, same machine, loopback. It is not a lax case, it is the case the rule permits.

**The same link aimed at the appliance is a defect**, and the guard has to be real rather than documentary. A URL carrying Vipunen's passage 8034 to `lempi02w` resolves against *its* numbering, lands on a real passage, and plays a real song under a real name — the exact failure shape `[REQ-LIB-165]` exists to correct. So the two Lempis here are not interchangeable and the console must not let one substitute for the other:

| relationship | which Lempi | keyed by |
| :--- | :--- | :--- |
| **handoff** § here | the co-resident player, same `lempi.db` `[SPEC-DF-080]` | `passage_id` — valid only here `[SPEC-DF-035]` |
| **export** §5 | the appliance, its own database | `audio_md5` — valid anywhere `[SPEC-DF-030]` |

The handoff URL is configuration, and it names the local player alone (default port 5720). Remote handoff is not forbidden in principle — it needs the portable passage key `(audio_md5, kind, start_ms, end_ms)` on the player's routes, which do not accept it today and are not owed it by anything yet.

**`[SPEC-SUI-155]` Embed it where it embeds; link it where it does not.** A frame gives the operator continuity of place, which is most of what "incorporated" means, and the player's review page is already self-contained. **Checked rather than assumed, 2026-08-20:** the player sends no `X-Frame-Options` and no CSP `frame-ancestors`, so it frames today.

But that is the *absence* of a header, not a promise of one, and the fallback is load-bearing for a reason worth naming: if the embed were required to work, Lempi would have to start sending a header for Vipunen's benefit — Vipunen-facing code in every Lempi, which is precisely what `[SPEC-SUI-110]` refuses. So a frame that does not load degrades to a plain link in a new tab, and nothing is asked of the player to keep it working.

Cross-origin also means Vipunen cannot see inside the frame. That is not a limitation to engineer around; it is `[SPEC-SUI-145]` holding.

**`[SPEC-SUI-190]` Everything in this section is behind `vipunen-support`, off by default.** *(Decided 2026-08-27.)* `[SPEC-SA-010]` already confines Vipunen itself to an x86 desktop that never runs on the appliance; a Lempi feature that exists only to be *reached from* Vipunen inherits the same confinement, and a Pi Zero 2W that will never see a console has no reason to carry the review page's routes, its embedded HTML and JS, or the database code behind them. Cargo's own `mpd` feature is the precedent — off by default, one line of `Environment` away from being real. `cargo build --release` for the appliance carries none of it; a build for a desktop induct session adds `--features vipunen-support`. Measured **3.05 MB smaller** without it — 11,110,704 against 7,912,728 bytes,
real aarch64 binaries, 2026-09-11. *Corrected from ≈200 KB, carried here since
2026-08-27 and repeated in four other documents: it understated the saving
fifteen-fold.* The routes are small; the `reqwest`/`rustls` stack behind them
`[SPEC-SUI-196]` is not. Size was never the real argument — on `bose` the
catalogue is read-only twice over, so these pages took input they could not
save `[GDE-DEP-098]`.

This is why the waveform editor and the MusicBrainz search of [SPEC010 §3](SPEC010-identification-review.md#3-searching-musicbrainz-directly) belong behind the same flag from the day they are written, not retrofitted afterward — the second thing built ungated is the second thing that has to be moved later.

### 3.5 Flags — a worklist over what Lempi noticed while playing

**`[SPEC-SUI-202]` Built 2026-08-27, against `[REQ-VIS-265]`, `[REQ-LIB-190]`.** Something worth a person's attention — a misidentified track, a clipped fade, a wrong credit — is noticed while *listening*, not while inducting, and until now there was no way to mark it without leaving the player. `GET /flags` lists everything Lempi's own "flag this for review" checkbox has set, newest first, resolved to a title and every passage that still carries it.

**Unconditional in Lempi, not behind `vipunen-support`.** Unlike the review page and the editor, flagging costs nothing an appliance cannot afford — one small table, two routes, no decoder, no `reqwest`. A listener on the Pi can flag a track exactly the way a listener at a desk can; only Vipunen's own worklist needs the desktop tool at all.

**`[SPEC-SUI-203]` Read-only, like everything else in this file.** The checkbox that sets and clears a flag lives in Lempi because it is listener state and listener state is Lempi's to write `[SPEC-SC-020]`. This page only ever queries `listener_flags`; choosing a row opens its profile page, which is where both handoffs already live.

Keyed the way `flavor` already is — a recording when the play had one, a passage when it did not — because the unidentified case is often exactly the one worth flagging, and a flag with nowhere stable to attach would be no flag at all `[SPEC-DF-035]`. A passage-keyed flag surviving a rescan that renumbers passages is not promised; the page says so plainly rather than showing a blank row when it happens.

### 3.6 System — which instance this is, and stopping it

**`[SPEC-SUI-210]` Built 2026-08-29, out of a real incident.** Two stale `console.py` processes were found both alive against the same library, both bound to `:5730` via a Windows `SO_REUSEADDR` quirk, and telling them apart took forensic process-listing by hand. `GET /system` answers "which instance is this" at a glance: the PID, when it started, the port and database path it was given, and a "shut down this instance" button — the operator's own equivalent of the `taskkill` that incident actually needed.

**`[SPEC-SUI-211]` The commit shown is a startup snapshot, not a live `git status`.** This tool has no compiled build to embed a version into; the checkout it runs from is the closest honest equivalent, read once via `git rev-parse HEAD` (and `git status --porcelain`, for whether the working tree was clean) when the process starts. It cannot change under a process already running from it, so it is not re-read per request — the same reasoning a compiled binary's own version string does not change at runtime.

**`[SPEC-SUI-212]` Shutdown is refused outright while any job is running, not merely warned about.** `remote-push` briefly stops lempi02w's own player mid-sync `[SPEC-DF-111]`; killing this process between that `systemctl stop` and its own `systemctl start` would leave the appliance silent with nothing left running to issue the restart. The check is not scoped to `remote-push` alone — every job kind is refused while active, since telling "safe to interrupt" apart from "not" costs more than just asking whether one is running at all. `POST /api/system/shutdown` calls `BaseServer.shutdown()` from the request's own thread (a different thread than `serve_forever()`'s, which is what makes the call safe rather than a deadlock), after a short delay lets the confirming response actually reach the browser.

### 3.7 Reanalyze, and suggesting a release — closing two gaps a flagged, unmatched track exposed

**`[SPEC-SUI-214]` Built 2026-08-30. `fingerprint_ids.py --recheck` already existed and no caller ever passed it.** `identify` skips any passage already in `id_checks` — `unmatched` included — so a second `induct` over an already-ingested folder could never retry one; the flag that fixes this was already built, just never wired to the console. `POST /api/reanalyze` submits a `reanalyze` job — the same pipeline `induct` already runs (`ingest`/`extract`/`identify`/`merge`, and since 2026-09-25 `works`/`load-works` `[GDE-WRK-130]`), `steps_for(..., recheck=True)` threading `--recheck` into the `identify` stage's own argv alone. No propose/plan step, unlike fresh induction: the folder is already known, so there is no "new files discovered" surprise to preview, only whether to retry what a stage already gave up on.

**`[SPEC-SUI-215]` Built 2026-08-30: a folder-level release suggestion for exactly the case AcoustID fingerprinting has nothing for.** Not a reproduction of the inherited McRhythm album-matcher (`MCR-SPEC033`, still PROVISIONAL/P4 in §6) — that solves a harder, different problem, finding cut points inside one continuous DAO-rip file with no identifiers at all. A folder is already split one file per track, tags carrying title/artist/track number; matching that short list against a release's own short tracklist needs none of the boundary-detection/DP-assembly machinery, only fuzzy title/duration matching between two lists of ~10-20 items. `tools/suggest_release.py` guesses `artist`/`album` from the folder's own file tags, searches MusicBrainz's release search, scores each candidate by how much of the folder it explains, and writes nothing until a specific release is picked — `POST /api/release/suggest` (discovery, `releases`/`release_recordings` cached same as `fetch_releases.py` already writes) and `POST /api/release/accept` (the one write, assigning matched recordings and logging one `ingest_decisions` row per passage in the exact shape `choose_release.py` already established, so `profile.html`'s existing decisions table needed no change to render it).

**The name floor is a gate, not a term in the sum — found by a test, not reasoned in advance.** An early version added a track-number-agreement bonus and a duration-closeness bonus directly to the title-similarity score; a genuinely unrelated file (a bonus track, a bit of studio chatter) that merely happened to sit at the same track position as a real tracklist entry scored high enough to be claimed as a match, purely on the strength of two bonuses stacked onto a mediocre name score. Fixed by requiring the title similarity alone to clear `NAME_FLOOR` before either bonus is even computed — the same shape `choose_release.py`'s own tiered scoring already argues for elsewhere in this file: a bonus may break a tie among plausible candidates, never manufacture one that title similarity itself refused to support.

**Verified live, not only against fixtures**, 2026-08-30: run against `Foghat/The Best of Foghat` (the folder a real flagged, `unmatched` passage — "Slow Ride" — lives in), the top three scored candidates all reached 1.00 (16/16 tracks), one of them the specific release an operator had independently found by hand on musicbrainz.org; accepting it matched and applied all 16 files, and the previously-`local:audio:`-only passage now carries a real recording MBID and a resolved artist.

**Two more bugs the live run itself then exposed, both fixed the same day.** A flag set against a passage's pre-accept id was left orphaned once `accept` reassigned `passage_recordings` — `console.flags()` read it as "no longer resolvable", indistinguishable from a vanished passage, when the opposite had happened. Fixed by reusing `apply_changes.py`'s own `clear_flags_for()` `[SPEC-DF-112]`. Separately, Lempi's own review queue (`player/core/src/db/library.rs`'s `review_queue()`, `db.rs` at the time, split 2026-09-01) reads `id_checks.stored_mbid` directly — a *different*, AcoustID-fingerprint-based identification method this tool never runs — so an accepted passage kept surfacing there captioned with its old id. Fixed by deleting the stale `id_checks` row on accept, since no fresher fingerprint verdict exists to write instead.

**A third bug, found the same way against `Xavier Rudd/White Moth`, 2026-08-31: `--query` sent straight to search, even when it was never a search phrase.** An operator who has already found the right release by hand on musicbrainz.org has a bare id or a page URL, not a title/artist guess — pasted into `--query` unchanged, it reached the free-text `/ws/2/release` search endpoint, where a UUID matches nothing indexed as words, and reported "0 candidate release(s) scored" for a release that plainly exists. `release_id_in()` recognizes a MusicBrainz id anywhere in the string (so the bare id and the whole pasted URL both work, matching what `choose_release.py`'s own `--accept` already expects) and, when found, fetches that one release directly instead of searching — still surfaced as a single scored candidate through the same discover/accept flow, so the operator confirms it before anything is written, same as any other match. Verified live: the pasted `https://musicbrainz.org/release/27dc1298-adc6-4975-bd0c-a896fcea5ecc` URL now returns exactly that release, cached and scored, instead of zero.

**`[SPEC-SUI-198]` Built 2026-08-30, the second half of that fix.** `ReviewItem` now carries `checked_at`, and "The audio says" states plainly what kind of check this is — AcoustID's own fingerprint match, not a live view of the current id — and when it last ran, rather than leaving both to be inferred from a card that can now go stale for reasons that have nothing to do with fingerprinting at all.

### 3.8 Amplitude — automatic lead-in/lead-out

**`[SPEC-SUI-216]` Built 2026-08-30: `[SPEC-SA-075]` finally has an implementation, ported from the inherited McRhythm algorithm** (`MCR-SPEC025`) rather than the still-PROVISIONAL segmentation cascade beside it in `SPEC007` §6 — a self-contained, much smaller problem (an RMS envelope over one passage's own audio, absolute-dB thresholds, a "quick ramp" shortcut), needing no MusicBrainz calls and no DP assembly. `tools/analyze_amplitude.py` decodes via the same `ffmpeg` slicing `extract_library.py` already established, piped to raw PCM instead of a temp WAV. **A sign error in the inherited spec was caught, not copied**: its own worked formula gives a threshold >1 for a value the same document bounds to `[0,1]`; corrected to `10^(-dB/20)`, matching `REQ-AUD-154`'s own dB convention elsewhere in this codebase.

**Deliberately not wired into `induct`/`reanalyze`.** `[SPEC-SA-075]` carries the same "PROVISIONAL, needs independent re-verification for Lempi" status as segmentation — `jobs.py`'s `SKIPPED` list now names `amplitude` the same way it already names `segment`/`releases`/`cover art`, and the capability is offered only as its own explicit action (`POST /api/analyze-amplitude`, a button on `profile.html`), never silently run. Gated per `[SPEC-SA-080]` ("manual edits outrank computed values permanently"): a passage with `boundary_src='manual'` is never touched, with or without `--recheck`.

---

## 4. Jobs

**`[SPEC-SUI-080]` One job at a time, and it says so.** `fingerprint_ids.py` documents Vipunen holding the library's write lock for a minute at a time; a console that starts a second job creates lock contention that surfaces as an unexplained stall. Requests to start while one runs are queued and shown as queued.

**`[SPEC-SUI-082]` The player is a second writer, and it never stops** `[SPEC-SUI-012]`. The library runs in WAL mode — the appliance's `lempi.db-wal` and `-shm` are there to see — so a job holding the write lock does **not** block the player's reads: playback and browsing continue throughout. What it blocks is the player's *writes*, the resume row `[SPEC-SC-098]` and play history.

The pattern for a long pass is therefore already in the tree and is not the console's to invent: `fingerprint_ids.py` opens the library **read-only**, writes its findings to a sidecar, and folds them in with `--merge` once the library is quiet — on the stated grounds that a pass which cannot write to the library also cannot damage it.

**`[SPEC-SUI-085]` Job state lives in the database, not the browser.** Extraction of a large folder is hours `[SPEC-SA-025]`. The page must be closeable, reopenable and survive a reload with the job still visible — and an interrupted job must lose at most the in-flight item `[SPEC-SA-028]`, `[REQ-LIB-130]`. Progress is displayed from what the stage has committed, so the bar cannot claim work the database has not accepted `[REQ-VIS-140]`.

---

## 5. Export — new music to a remote Lempi

**`[SPEC-SUI-090]` The common case is six files, and the obvious method costs a gigabyte.** Measured against the appliance on 2026-08-20, for the two-album `Frisina, Gerardo` addition:

| what must cross | size |
| :--- | ---: |
| the audio itself | 6 files, **40 MB** |
| whole-database migration T3 `[SPEC-DF-080]` | **~1.02 GB** — 25× the audio, and it carries class D |
| the derived facts for those 6 hashes | single-digit **KB** |

Shipping the database also forces the question the transfer script defers: the file it would replace holds *the appliance's own* play history, and a copy that overwrites it destroys the only irreplaceable data in the system `[SPEC-DF-090]`.

**`[SPEC-SUI-095]` So the export unit is a bundle, not a database.** Audio files, plus the class A/B/C payload for exactly the hashes the target lacks `[SPEC-DF-050]`. This adds an **envelope**, not a schema: the payload is the one `[SPEC-DF-065]` already defines for embedded tags and sidecars, so there is one serializer and one parser across all of it.

**`[SPEC-SUI-100]` Class D cannot travel, by construction rather than by care.** A bundle is assembled from the class A/B/C tables; listener state is physically segregated by prefix `[SPEC-SC-020]`, so there is no path by which a play count reaches the wire. The target *merges* the bundle into its live database and its own history is never in the transaction. This is what makes export repeatable rather than a decision to be weighed each time `[SPEC-DF-055]`.

**`[SPEC-SUI-105]` The delta is computed by `audio_md5`, and the target's relink is scoped to it.** A full relink after adding six tracks re-hashes 7,232 files to bind six — estimated under an hour on the appliance `[SPEC-RLK-070]` — against seconds for the manifest.

This is **not** the optimisation `[SPEC-RLK-140]` forbids. `--quick` narrows the *rigour*, taking the database's word on files it does not hash; a manifest narrows the *set*, hashing every file it considers, completely. The distinction only holds if the output states it: *"verified 6 of 7,232 files; the remainder were not examined"* is a different sentence from *"matched"*, and the report must be the first one.

> **Refined by `[SPEC-PL-085]`:** for a *bundle*, this is not a separate pass at all. Relink cannot bind an arriving row because it never creates one `[SPEC-RLK-090]`, so the importer must hash, verify and write the path itself — which is the same walk. The scoped relink described here is what the **importer does**, not a step after it. Relink proper remains for the whole-library case.

**`[SPEC-SUI-110]` The target is an ssh host and a directory — never a Lempi endpoint** `[SPEC-SUI-025]`. rsync moves the bundle; binding it is the target's own work, offered over ssh as a convenience and never required for the export to be complete and correct. Requiring an API would put Vipunen-facing code in every Lempi, including the ones that will never meet one.

**`[SPEC-SUI-120]` T3 survives for what it is good at**: first installation, and moving a whole library between a user's own machines `[SPEC-DF-080]`. The bundle is the incremental case, which `[REQ-LIB-130]` states is the common one.

**`[SPEC-SUI-130]` The importer is half of the exporter, not a dependency on someone else.** *(Decided 2026-08-20.)* An export that cannot be received is not an export, so Lempi's bundle importer is in scope here and ships with the feature. It verifies before trusting — recompute `audio_md5`, discard class C on disagreement `[SPEC-DF-070]` — and it is what finally makes `[REQ-PORT-100]` true rather than intended: a Lempi with no Vipunen, receiving derived data it could never compute.

**The two halves are in different languages under different licences, and that is why the payload is specified rather than implied.** The exporter is Vipunen's — Python, AGPL. The importer is Lempi's — Rust, MIT, and it must be written as Lempi code: MIT may be incorporated into an AGPL work and not the reverse `[GDE-ARC-018]`, so no Vipunen code travels into the player. They meet only at `[SPEC-DF-065]`'s payload schema, which is now carrying two independent implementations instead of one, and is the sole thing keeping them in agreement.

**`[SPEC-SUI-165]` A compatible payload is kept whole and used in part; an incompatible one is rejected whole.** *(Decided 2026-08-20.)* A Vipunen ahead of the appliance's Lempi is the **normal** state, not an edge case — the desktop is where the work happens.

*Compatible* — **keep everything, use what is understood.** The receiver stores the payload as it arrived and fills the columns it knows. What it cannot yet interpret is **not discarded**: the bundle crossed a link measured in hours `[SPEC-RLK-070]`, and a later Lempi that understands the field must not have to ask for it again. Measured `[SPEC-PL-090]`, retaining the payload for the **whole** library costs **10.4 MB compressed** — 91 MB if stored readable — against a 1,072 MB database. The cheaper side of the trade by two orders of magnitude.

> This is not the empty column `[SPEC-SC-015]` forbids. That rule bans *inventing* fields nobody fills; this *retains* data that actually arrived, whose consumer is a later version of the same program and whose alternative is a re-transfer.

*Incompatible* — **reject the import; change nothing.** Incompatible means the receiver cannot construct what it requires: a field it needs is absent, or a conflict has no resolution `[SPEC-DF-070]`. Note this is **not a version comparison.** A *newer* payload that dropped or renamed a required field is incompatible, and an older one may be perfectly usable — so compatibility is a question about the required set, answered per bundle, never by ordering two version numbers.

Rejection is whole and it is loud: one transaction, the target unchanged, and a statement of *which* requirement went unmet. A partial import is the worse failure — it leaves a library that is neither the old one nor the new one, with nothing recording which parts are which.

**A rejected import does not cost the transfer**, because the audio travelled separately `[SPEC-SUI-095]`. Files already on disk are `unknown` to relink — a stated, recoverable outcome `[SPEC-RLK-050]` — and re-exporting from a corrected Vipunen re-sends the payload, not the 40 MB.

---

## 6. Open

1. **`[SPEC-SUI-175]` Nothing re-reads a retained payload.** `[SPEC-SUI-165]` keeps fields the receiver cannot yet use, and that only pays off if something notices, after an upgrade, that it now can. What triggers the re-parse — and on what signal a Lempi concludes its own understanding has widened — is unspecified. Retention with nothing to re-read it is storage that merely looks like foresight.
2. ~~**`[SPEC-SUI-180]` Re-importing the same bundle is unspecified.**~~ **Resolved and built 2026-09-06**: [SPEC035](SPEC035-mesh-library-sync.md) `[SPEC-MESH-080]`. Turned out narrower than expected — the importer was already idempotent at the file level; the actual gap was an already-held encoding silently dropping a *later* bundle's improved recording-scope data, now fixed and covered by two tests.
3. **The per-file identity vocabulary and per-row offers `[SPEC-SUI-055]` describes (`unknown`/`here`/`elsewhere`/`corrupt`, induct/relink) are not built.** `/folder` today shows only aggregate counts; see the status note at `[SPEC-SUI-055]` in §3.2.

---

**Traceability:** `[SPEC-SUI-010..216]` · derived from `[REQ-LIB-170]`, `[SPEC-SA-015]`, `[SPEC-DF-035]`, `[SPEC-DF-050]`, `[SPEC-DF-080]`, `[SPEC-RLK-140]`, `[GDE-CHT-030]` · `[SPEC-SUI-210..212]` built 2026-08-29 (`tools/console.py`'s `build_info()`/`system_status()`, `tools/console_web/system.html`) · `[SPEC-SUI-213]` built 2026-08-30 (`_lempi_has_vipunen_support()`/`_lempi_ready()` — `console.py` at the time, split into `tools/lempi_control.py`) · `[SPEC-SUI-214..215]` built 2026-08-30 (`tools/jobs.py`'s `reanalyze`/`suggest-release`/`accept-release` kinds, `tools/suggest_release.py`, `tools/console_web/profile.html`) · `[SPEC-SUI-198]` built 2026-08-30 (`player/core/src/db/library.rs`'s `ReviewItem.checked_at` — `db.rs` at the time, split 2026-09-01 — `player/src/web/review.js`) · `[SPEC-SUI-216]` built 2026-08-30 (`tools/analyze_amplitude.py`, `tools/jobs.py`'s `analyze-amplitude` kind) · `[SPEC-SUI-227]` built 2026-08-31 (`player/src/web/mod.rs`'s `/build` route — `web.rs` at the time, split 2026-09-02 — `_lempi_build()`/`_lempi_staleness()`/`_lempi_ready()`, `console.py` at the time, split into `tools/lempi_control.py`)
