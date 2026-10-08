# System Architecture

**Orientation — Tier 1 · describes what is built, as of 2026-10-08**

How Lempi is put together: the components, the interfaces between them, and the
few rules that decide where a new piece belongs. Written against the tree rather
than against a plan — where something is designed but not built, it says so.

> **Related:** [SPEC018](spec/SPEC018-switching-backends.md) for the backend seam ·
> [SPEC020](spec/SPEC020-the-handoff.md) for what crosses it ·
> [SPEC016](spec/SPEC016-mpd-protocol-findings.md) for what MPD will and will not carry ·
> [SPEC017](spec/SPEC017-what-counts-as-a-play.md) for the listening rules ·
> [ROADMAP](ROADMAP.md) for what's not built yet · [GUIDE001](GUIDE001-lineage-and-lessons.md) for how the system got here

---

## 1. Two programs, one library

| | language | licence | writes |
| :--- | :--- | :--- | :--- |
| `player/` | Rust | MIT | listener state; applies catalogue data the hub derived |
| `tools/` — "Vipunen", about a hundred scripts, flat, no subpackages | Python | AGPL-3.0 | reference data, and the hub's sync runs |

The library is a pair of SQLite files on every node — `library.db`, the
catalogue, and `listener.db`, the listener's own state `[IMPL-DBSPLIT-025]`.
Everything that plays, or is played to, is in `player/`. Everything that *makes*
reference data — ingest, identification, flavor extraction, lyrics import — is
Vipunen's, and the hub's library is the one star sync carries to every other
node. **The player never authors what Vipunen owns**,
for the reason `[SPEC-LYR-030]` gives about lyrics and which generalises:
derived data has one writer, and a player that edits it becomes a second source
of truth for something it only reads. A node with no Vipunen still has to
*receive* that data, so the player crate carries the two ways it arrives —
`import_bundle`, for a bundle the hub sends, and the star-sync commit the hub
signs (`player/src/star_node.rs`) — and both apply the hub's rows unchanged.

---

## 2. The spine

```
 browser ──ws snapshot──┐        ┌─ Controls (intent cells) ─┐
                        ▼        ▼                           │
   web/    ──Command──▶ EngineHandle ──▶ host.rs loop ────────┘
                                            │
                                    Session (session.rs)
                                       │         │
                              Director │         │ dyn Backend
                                       ▼         ▼
                          director/library.rs   Switching (switch.rs)
                                                  ├── Engine      (local)
                                                  └── MpdBackend  (guest)
```

`host.rs` — `Player::start`, which the `lempi` binary calls with its resolved
command line and the Android app calls through `player/android` — owns one
thread and everything audio-related on it: `cpal`'s stream is not `Send`, so the
engine is built and pumped where it lives `[GDE-HST-040]`. The tokio web server
touches playback only through the two channels above. The Director, the
queue and the database layer live in `lempi-core` (`player/core/`), the crate
that decides what to play and holds nothing that makes a sound — a boundary
Cargo enforces.

**Two channels, and the difference matters.** A `Command` down `EngineHandle`
reaches the **local engine** and nothing else. An intent written into `Controls`
is picked up by the loop, which can reach whichever backend is live. Anything
that must act on the *sounding* side — a switch, a seek, a folder-writing
setting — is an intent cell; getting this wrong makes a control that silently
moves the wrong player.

---

## 3. The backend seam

Four traits, deliberately separate, in `playback.rs` and `switch.rs`:

| trait | methods | who wants it |
| :--- | :--- | :--- |
| `Playback` | `capabilities`, `enqueue`, `queued_ids`, `queued_ms`, `shortfall`, `take_dropped`, `resume_at`, `seek_to`, `tick`, `is_shutdown`, `apply_queue_settings` | ordinary playback |
| `FadeOut` | `fade_out`, `hand_off` | a handoff |
| `Publish` | `publish` | a guest's clients |
| `Progress` | `head_position`, `refresh`, `head_counted`, `adopt_counted` | a handoff |
| `Backend` | all four | `Switching` |

Fading, publishing and position are things a **handoff** wants and ordinary
playback never does. Widening `Playback` for one caller is how a seam stops
being the shape of what crosses it `[SPEC-BK-020]`.

**`Switching` is itself a `Backend`**, forwarding to whichever side is live, so
`Session` drives one thing and never learns it changed. `Capabilities` is
reported from the **live side only, never the union** — reporting `FULL` while a
guest plays would promise gain and ramps MPD cannot honour `[SPEC-BK-040]`.

**What crosses a handoff is passage ids and a position**, never audio and never
decoder state. Spans, gain and ramps are re-derived from the library on arrival,
because they belong to the passage and not to whichever backend last played it.

---

## 4. The audio path

```
Director ─▶ QueueEntry ─▶ queue.rs ─▶ decoder.rs ─▶ resample.rs ─▶ mixer.rs ─▶ output ring ─▶ path.rs ─▶ device
                                     (symphonia,     (rubato)     (per-passage    (~14 s)     (supervisor,
                                      seeks to span)               gain, ramps)                owns the device)
```

`QueueEntry` is the unit every layer speaks: `passage_id`, `path`,
`start_ms`/`end_ms`, `lead_in_ms`/`lead_out_ms` (crossfade-admission timing
only), `fade_in_ms`/`fade_out_ms`/`fade_in_curve`/`fade_out_curve`
(this passage's own volume envelope `[SPEC-SC-046]` — the actual ramp the
mixer applies), `gain_db`, `mbid`, naming.

**A passage is a span of a file**, which is why a whole-file backend cannot carry
`kind='radio'` trim points, and why one capture holds forty passages with one set
of tags.

Three things worth knowing before changing anything here:

* **`path.rs` is a supervisor on its own thread** and owns the device. A sink
  that vanishes — a Bluetooth speaker walking out of range — is reopened rather
  than fatal `[SPEC-APS-060]`.
* **The ring holds about 14 seconds.** Anything the listener asks for *now* —
  skip, seek — must cut the ring back, or it arrives 14 seconds later. There is
  one place that does this and both callers share it.
* **Gain is applied per passage, before the mix**, so each side of a crossfade
  carries its own level. Applying it after would level the blend rather than the
  passages.

---

## 5. The Director

`player/core/src/director/` splits selection into `library` (the pool, and `decide`), `frequency`
(suppression windows and rotation), `flavor` (acoustic distance), `occasion`,
`shape` and `program`. It returns an `Explanation` with every choice, which is
what `/why` shows and what a guest's clients read as an MPD sticker.

**It is the same `decide` call whichever backend is sounding.** The Director has
no idea whether its choice will be decoded locally or handed to MPD.

---

## 6. Data

`player/core/src/db/` is the only gateway to SQLite (`mod`, `library`,
`player_store`). Each connection opens `listener.db` and attaches `library.db`
read-only as `lib`; a query names a catalogue table `__LIB__.name`, and
`QualifyingConn` is the one place that resolves it `[IMPL-DBSPLIT-035]`. Two
classes of data, and the distinction is load-bearing `[SPEC-DF-055]`:

* **Class C — reference data** (recordings, artists, releases, flavor, lyrics,
  cover art), in `library.db`. Derived, reproducible, and it **travels** between
  installations.
* **Class D — listener state** (plays, rejections, resume point, settings,
  programmes), in `listener.db`. Personal, irreplaceable, and it **never travels
  with music**. A node's plays never leave it `[REQ-PD-113]`; the listener's
  edits — preferences, likes, flags — are merged across the household's own
  nodes by star sync ([SPEC046](spec/SPEC046-star-sync.md)).

Identity is `audio_md5` > `recording_mbid` > `file_path` `[SPEC-DF-030]`. That
ladder is why a lyrics import is a join rather than a matching problem, and why
a library that moved on disk can be relinked at all.

---

## 7. The interface

One `Snapshot` struct, serialised to JSON and pushed over `/ws`; REST for
actions. Three skins — `lempi`, `mulibplay`, `winamp` — served from
`web/skins/`, sharing `core.js`.

**`core.js` owns anything two skins would otherwise implement twice**: the queue
edit verbs, the seek arithmetic, art and lyrics fetching, formatting. A skin
styles and places; it does not decide what a control does. Skins do differ in
which panels they carry — Settings, Bluetooth, Play History and Restart/Shutdown
are in `lempi` only, lyrics in `mulibplay` only, and a new browser starts on
`mulibplay` — so a built feature can still be absent from the skin in use. The
in-app guide's *Advanced Features* page keeps the table.

A test asserts the snapshot's **field names**, because renaming one silently
blanks part of every skin and no Rust test would otherwise catch it.

---

## 8. Where the audio comes out

> **Rule**: *where the Lempi server runs is where the audio comes out.*

Lempi is not a network audio server. The web interface is a control and
visualisation plane; sound leaves the host's own hardware — a DAC hat, a
Bluetooth pair, a sound card.

**The MPD backend does not change this.** MPD is a *guest on the same machine*:
Lempi hands it passages, and MPD plays them out of the same host's audio. What
moves over the network in that arrangement is control, not sound.

---

## 9. Conventions that hold everywhere

* **Every tunable default is defined once**, with its bounds, in `lempi-core`'s
  `player/core/src/settings.rs`, and re-exported from the player's `lib.rs`
  under the same names — `SKIP_SUPPRESS_H`, `QUEUE_DEPTH`,
  `SAMPLE_INTERVAL_MS`. A number that appears twice will diverge.
* **A measured constant carries its measurement** in the doc comment beside it.
  The comment is where the reasoning lives; a threshold with no number behind it
  is a preference pretending to be a finding `[GOV-SRC-040]`.
* **Anything written outside Lempi's own storage is off by default** and asked
  for explicitly — cue sheets, cover art, lyrics `[REQ-VIS-205]`.
* **Reports name what they left out.** A count is not a report; a shortened
  queue that says nothing about being shortened is the failure `[PI3-API-030]`
  exists to refuse.

---

## 10. Known gaps

Where what is built falls short of a specification, or of what a reader would
assume. The specifications stay the statement of intent: each line here is a
fault in the code, to be fixed there, and found 2026-10-08 unless it says
otherwise.

* **A seek in a passage's last ~15 s moves the next passage.** The browser
  sends an offset against the passage it shows; `seek_to` applies it to the
  first passage still being mixed, which for a ring's depth before the end
  (`BUFFER_FRAMES`) is already the next one. A seek during a crossfade discards
  the incoming passage, unplayed and unrecorded `[REQ-VIS-225]`.
* **The live Director never hears of in-session rejections, or of a person's
  own picks.** Skip and dequeue windows are read when the Director is built, so
  they take effect only after a restart or a library reload. Until then a
  skipped or removed passage keeps the artist, work and recording marks set
  when it was queued — which a rejection never earns `[SPEC-PLAY-050]`. The MPD
  backend un-notes a removed passage as `[IMPL-MPD-045]` requires, but without
  the dequeue window of `[SPEC-PLAY-055]` the recording is eligible again at
  once. A passage a person queues marks nothing at all `[REQ-PD-112]`.
* **The draining clock runs while paused.** A passage's last ring-depth of audio
  is timed by the wall clock, so pausing there runs the progress bar to the end
  and records the tail as heard `[REQ-VIS-250]`.
* **The no-Director fallback is uniform random.** On a first start, with no
  remembered queue, or when every candidate is blocked, `random_radio` fills
  the queue, ignoring holds `[SPEC-HOLD-010]`, characteristic exclusions,
  rejections and rotation.
* **Listener restore does not work on a split pair.** `backup::restore` looks
  for `passage_recordings` in the listener file, so a rehearsal reports zeros
  and `--commit` fails and rolls back. It is reachable only as the cargo example
  `restore_listener`, whose default path predates the split `[REQ-LIB-160]`,
  `[PI-DB-030]`.
* **At a queue depth of 1, flow never applies.** Each pick is made with the
  queue empty, so there is no tail to measure from `[SPEC-DIR-160]`.
* **The UTC offset is read once per process.** An appliance that stays up
  across a change of daylight saving time runs its programmes an hour off until
  it restarts `[SPEC-DIR-180]`.
* **`tools/test_mesh_sync.py` fails**, since `fb766de` made the hub take part in
  every run `[SPEC-STAR-134]`: its plan has no backups directory, and it does
  not stub the console check as `test_star_sync_flow.py` does.
* **The now-playing panel is blank while MPD is the live backend** — title,
  position and duration come from the local engine's published state, so the
  seek bar has nothing to act on there, though MPD itself can seek.
* **The MPD protocol findings are from the development machine.** Every one in
  [SPEC020](spec/SPEC020-the-handoff.md) was measured against MPD 0.24.0 there;
  only `seekid` has been re-checked against the appliance's 0.23.12
  `[SPEC-BK-060]`.

---

## 11. The subsystems around the spine

Each is specified in its own document; this table is where to start reading.

| subsystem | where | specified in |
| :--- | :--- | :--- |
| Echo playback: several players, one programme, in step | `player/src/echo.rs`, `player/src/echo_client.rs` | [SPEC044](spec/SPEC044-echo-mode-control.md), [SPEC043](spec/SPEC043-node-delay-control.md), [GUIDE029](GUIDE029-what-is-known-about-alignment.md) |
| Star sync: the hub merges both halves and distributes them | `tools/star_sync.py` and its siblings on the hub; `player/src/star_node.rs` and `player/core/src/mesh_sync.rs` on a node | [SPEC046](spec/SPEC046-star-sync.md), [SPEC054](spec/SPEC054-mesh-without-ssh.md), [SPEC058](spec/SPEC058-catalogue-patch-by-natural-key.md) |
| Mesh membership, pairing, discovery, trusted networks | `player/src/membership.rs`, `pairing.rs`, `discovery.rs`, `trust.rs` | [SPEC049](spec/SPEC049-mesh-membership-and-trust.md), [SPEC050](spec/SPEC050-node-discovery.md), [SPEC051](spec/SPEC051-trusted-networks.md) |
| Web access: the Origin/Host guard, and a phone's launch key | `player/src/web/access.rs` | [SPEC052](spec/SPEC052-web-access-guard.md), [SPEC053](spec/SPEC053-security-hardening.md) |
| The Android host | `player/android/`, `android/` | [REQ007](spec/REQ007-android.md), [GUIDE033](GUIDE033-the-player-without-an-appliance.md) |
| Framebuffer touch UI | `player/src/bin/fbui.rs` | [SPEC036](spec/SPEC036-framebuffer-touch-ui.md) |
| Bluetooth speakers, and their own buttons (AVRCP) | `player/src/bluetooth.rs`, `player/src/avrcp.rs`, `appliance/bluetooth/` | [SPEC011](spec/SPEC011-audio-path-supervisor.md), [IMPL019](IMPL019-native-bluetooth-controls.md) |
| Wi-Fi, and the travel access point | `player/src/web/wifi.rs`, `appliance/bluetooth/lempi-wifi-failover` | [SPEC034](spec/SPEC034-wifi-configuration.md), [SPEC061](spec/SPEC061-travel-wifi-failover.md) |
| Per-output volume | `player/core/src/db/player_store.rs` | [SPEC060](spec/SPEC060-per-output-volume.md) |
| Queued shutdown | `player/src/engine/` | [SPEC059](spec/SPEC059-queued-shutdown.md) |
| Holding a passage back | `player/core/src/director/library.rs`, `tools/passage_hold.py` | [SPEC057](spec/SPEC057-holding-a-passage-back.md) |
| CD import | `tools/cd_import.py`, and the console's import page | [SPEC056](spec/SPEC056-cd-import.md), [GUIDE037](GUIDE037-ripping-a-cd.md) |
| Listener backups | `player/src/backup.rs` | `[REQ-LIB-160]` |

---

**Traceability:** describes `[SPEC-BK-020..065]`, `[SPEC-DF-030]`,
`[SPEC-DF-055]`, `[SPEC-APS-060]` · supersedes the pre-implementation sketch of
2026-07-26
