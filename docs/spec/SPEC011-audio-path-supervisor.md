# SPEC011: The Audio Path Supervisor

**Design Specification — audio device, sink and speaker lifecycle**

Companion: [LempiPi/PI003-choosing-a-speaker.md](../../LempiPi/PI003-choosing-a-speaker.md)
records the measurements this design is drawn from. This document is the
architectural answer to them.

---

## 1. Why this exists

**`[SPEC-APS-010]` One concern currently has six owners.** "Maintain an audible
path to the chosen speaker" is implemented across:

| Where | Part of the job it holds |
|---|---|
| `player/src/output.rs` | device lifecycle, the `failed` / `silent` flags |
| `player/src/sink.rs` | polls `wpctl` to learn where audio actually goes |
| `player/src/bluetooth.rs` → `lempi-btctl` | BlueZ policy, in shell, behind sudo |
| `LempiPi/lempi-speaker.sh` + timer | reconnect policy, in systemd |
| `LempiPi/lempi-wait-sink` | startup ordering, in shell |
| `player/src/engine/` (formerly `engine.rs`) | held the retry/backoff state machine, inside the mix loop, before extraction |

No component owns the concern, so no component can be tested for it, and (at
the time this table was written) the recovery state machine lived inside the
one loop that must never stall. This is why the file reached 1,297 lines before
`[SPEC-APS-100]` step 1 pulled the supervisor out of it into `player/src/path.rs`
— see §3/§4 below for what has since moved and what has not: it held mixing and
device policy, two concerns whose timing requirements have nothing in common.

**`[SPEC-APS-020]` The periphery accreted patches; the core did not.** Every
defect of 2026-08-15/16 was in this seam, and none were in the decoders, the
mixer, the fades, the queue, or the buffer model. That is the evidence for
restructuring one seam rather than rewriting: the audio core was derived from
measured constraints `[GDE-ARC-050]` and has survived every failure since.

---

## 2. The lessons that force the design

**`[SPEC-APS-030]` Status must be observed, never inferred from execution.**
The recurring defect, in five separate places:

- `lempi-btctl` returned `ok:true` because the helper *ran*, while `state` said
  `found` — it had destroyed a pairing and failed to rebuild it
- the player reported a running position while bound to `Dummy Output`
- `Paired: yes` was believed over `Bonded: no`, where only the second means a
  link key exists `[PI3-FOUND-030]`
- `NRestarts=0` declared the audio stack healthy while its BlueZ connection
  cycled 21 times in 20 minutes — the process never died
- a two-minute test reported 8/8 when the failure interval was 2.5 minutes

Each was an answer about an *attempt* standing in for an answer about an
*effect*. Every field this design publishes is derived from an observation, and
where no observation is available the field is `unknown` rather than a
reassuring default.

**`[SPEC-APS-040]` Discipline failed three times; only structure will hold.**
Blocking work reached the engine tick three times — a `wpctl` fork/exec, then a
700 ms sleep, then nearly again. Each presented as underruns and dropouts
indistinguishable from hardware failure, and each was found only because a human
watched a counter climb.

The cause is not carelessness. It is that `Engine` can call anything: nothing in
the type system distinguishes the loop feeding a ring from ordinary code. The
supervisor exists as much to *remove that capability* as to hold the policy.

**`[SPEC-APS-050]` The events were being emitted the whole time.** `bluetoothd`
logged every endpoint unregistration and every media-transport rebuild while two
evenings were spent measuring a radio. Nothing subscribed, because the design
polls subprocesses and parses prose. An event subscription would have shown
`[PI3-FOUND-030]` on the first evening.

---

## 3. The design

**`[SPEC-APS-060]` One supervisor, one thread, one snapshot.** A single
component owns: the chosen speaker (persisted), link state, sink identity,
output device lifecycle, and the recovery state machine. It publishes an
immutable snapshot; nothing else decides anything about devices.

```rust
/// What is true about the audio path right now, as observed.
pub struct PathState {
    /// The speaker the listener chose. Persisted; survives restart.
    pub chosen: Option<SpeakerId>,
    /// Bonded, connected -- from the stack, not from our last attempt.
    pub link: LinkState,
    /// Where the stream is actually linked. `Dummy` is reported, never hidden.
    pub sink: SinkState,
    /// Whether anything could hear us. `Unknown` when it cannot be determined.
    pub audible: Tristate,
    /// Recoveries since start, for the panel and for tests.
    pub recoveries: u64,
}
```

**`[SPEC-APS-070]` The engine holds no capability to block.** `Engine` receives
`&PathState` (or an `Arc` snapshot) and a sender. It has no device handle, no
`Command::new`, no sleep, no filesystem. The bug class of `[SPEC-APS-040]`
becomes unwritable rather than forbidden — which is worth more than the
tidiness, because the discipline demonstrably did not hold.

Reading the state is a load. Requesting a change is a send. Neither can stall.

**`[SPEC-APS-080]` The backend is a trait, and a fake implements it.** The audio
path is currently untestable without a Pi and a speaker, which is why its
failures were only ever found in the room:

```rust
pub trait PathBackend: Send {
    fn subscribe(&mut self) -> Receiver<PathEvent>;
    fn connect(&mut self, id: &SpeakerId) -> Result<(), PathError>;
    fn open_output(&mut self, sink: &SinkId) -> Result<Stream, PathError>;
    fn observe(&self) -> PathState;
}
```

With a fake, "speaker drops mid-playback, recovers, drops again, endpoints
withdrawn underneath" is a unit test instead of an evening. The recovery
state machine — backoff, settle, two-phase release/attach — is then testable at
its boundaries, which it has never been.

**`[SPEC-APS-090]` Speak D-Bus; stop shelling out.** BlueZ and PipeWire both
expose D-Bus with change notification. Subscribing (`zbus`) removes, in one
move: the 3-second `wpctl` poll, the 30-second reconnect timer, sudo, the
privileged helper, the JSON-through-shell contract, and the address validation
duplicated between `bluetooth.rs` and `lempi-btctl`.

It also replaces "ask every few seconds and hope the sample lands on the fault"
with the events named in `[SPEC-APS-050]`. Transport teardown becomes a fact the
supervisor is told, at the moment it happens.

**Status, against `[SPEC-APS-100]` below: this section is target design, not yet
the shape of `path.rs`.** Step 1 (done) extracted the supervisor with today's
subprocess backend behind it and nothing else — `PathHandle`/`PathRequest` and a
free-function `supervise` loop calling `crate::sink::current()`/`Output`
directly. None of `PathState`, `PathBackend`, `PathEvent`, `LinkState`,
`SinkState` or `Tristate` exist in the tree yet, and `audible()` returns a plain
`bool` rather than the `Tristate` `[SPEC-APS-030]` calls for. They arrive with
steps 2 and 3, not before.

---

## 4. Migration order

**`[SPEC-APS-100]`** Sequenced so each step is separately verifiable, and so the
risky one lands last with a test harness already under it:

1. **Extract the supervisor** with today's subprocess backend behind it. No
   behaviour change; the win is that one component owns the concern and
   `Engine` loses its blocking capability. **Done 2026-08-16**, in
   `player/src/path.rs`: `engine.rs` 1,297 -> 1,140 lines, `SinkWatch` and its
   thread deleted rather than moved, 215 tests passing.
2. **Add the trait and the fake.** Write the failure cases that were only ever
   found in the room.
3. **Port the backend to D-Bus.** Replaces working-if-ugly code, so it goes
   last, with `[SPEC-APS-080]` already covering it.
4. **Retire the shell layer** — `lempi-speaker.sh`, its timer, `lempi-wait-sink`,
   and the `lempi-btctl` verbs the supervisor subsumes.

**`[SPEC-APS-110]` Explicitly not in scope, when this was written (2026-08-16).**
Splitting `db.rs` (1,853 lines) and `web.rs` (1,018). Both were large, both had
a single concern, and neither had produced a defect. Size alone is not a
reason `[GDE-FBD-050]`.

*Superseded 2026-09-01/02.* Both were split anyway, along with `engine.rs`
(never named above, but the same shape of change): `db.rs` into
`db/{mod,library,player_store}.rs` (2026-09-01), `web.rs` into
`web/{mod,browse,review,musicbrainz,media,edit,settings,bluetooth,skins,control}.rs`
and `engine.rs` into `engine/{mod,persist}.rs` (both 2026-09-02). What changed
was not the reasoning above — none of the three had produced a defect either —
but the measured cost `[GDE-FBD-050]` now on record for each: `db.rs` had grown
to 4,149 lines with `impl Library` split into two non-contiguous blocks around
`PlayerStore`, an architecturally load-bearing distinction invisible in the
file layout; `web.rs` (2,287 lines) mixed roughly ten unrelated route
concerns with no boundary between them; `engine.rs` (1,242 lines in its
`impl Engine` block) mixed the tick/mixing methods `[GDE-FBD-090]` forbids
ever blocking with settings/history persistence triggered by events rather
than every sample. Each split was file-boundary-only — same structs, same
threads, same behaviour, verified equivalent by full test suite plus a
runtime smoke test against a real library, not merely "compiles." This
supersedes the "not in scope" verdict; it does not overturn `[GDE-FBD-050]`,
it satisfies it with numbers the original note didn't have.

---

## 5. Risks and open questions

**`[SPEC-APS-120]` The stability question settled while this was written, and
strengthened the case.** The twelve-minute window scored 139/139 connected; the
drop six seconds after it closed was the measurement's own ssh session ending
`[PI3-FOUND-040]`. The cause was never the radio and never the recovery policy,
so that policy stays a backstop rather than becoming load-bearing.

What it does sharpen is `[SPEC-APS-050]`. Both faults — the UPower cycle and the
seat-state gating — announced themselves in `bluetoothd`'s event stream for two
evenings while the design polled `wpctl` and parsed prose. A supervisor
subscribing to endpoint and transport events would have reported "my endpoints
were just withdrawn" the first time it happened, instead of leaving a listener
to describe a noise.

**`[SPEC-APS-130]` D-Bus is a real dependency, not a free win.** It trades
subprocess fragility for a library and an async surface inside a component that
must stay predictable. The trait boundary is what makes the trade reversible,
which is why it precedes the port.

**`[SPEC-APS-140]` Build identity must be visible at runtime.** A route returned
404 during this work because the deployed binary predated it, and the 404 was
read as a routing defect. The supervisor's snapshot is the natural place to
carry a build stamp, so "the Pi is running something older than you think" is
answerable without guessing.
