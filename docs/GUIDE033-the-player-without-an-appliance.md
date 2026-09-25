# GUIDE033: The Player Without an Appliance

**Development Guidance — what `lempi-player` assumes about the machine it runs on, and the structural changes that let a phone target be maintained beside the Pi and the desktop without destabilising either**

Written 2026-09-24, after `lempi-core` was split out `[GDE-AND-045]` and before any Android code exists. Every figure below was measured on that date or cites where it was.

> **Related:** [GUIDE004](GUIDE004-phone-port-strategy.md) (which route onto a phone, and why) · [GUIDE006](GUIDE006-director-as-a-guest.md) (the `Playback` seam) · [SPEC011](spec/SPEC011-audio-path-supervisor.md) (the path supervisor) · [CLAUDE.md §10](../CLAUDE.md) (the commit gate)

---

## 1. The dependency a phone actually has

**`[GDE-HST-010]` A phone links `lempi-player` with the appliance parts switched off — not `lempi-core` alone.** GUIDE004 was written assuming the audio path would be rebuilt on Media3 or AVAudioEngine. It need not be: `cpal` 0.18.2 declares `aarch64-linux-android` (AAudio) and `aarch64-apple-ios` among its targets, and `symphonia`, `rubato` and bundled SQLite are portable. So the decoder, mixer, fades and engine — the hardest and best-measured code here — can run on a phone unchanged.

That changes what preparation means. **Core should not grow.** Its value is its boundary — 30 crates, none of them audio or web — and the gate in `build/verify-targets.sh` enforces it. The work is making `lempi-player` indifferent to what kind of machine hosts it, which is the subject of the rest of this document.

## 2. What the player assumes about its host

**`[GDE-HST-020]` Four assumptions, each true on every node today and false on a phone:**

| It assumes | Evidence, measured 2026-09-24 |
| :--- | :--- |
| **The player is a binary** | `player/src/bin/lempi.rs` is `#[tokio::main]` with a ~320-line `engine_thread` that opens the session, builds the engine, attaches MPD and runs the loop. `station.rs` repeats a subset. A JNI or UniFFI host has nothing to call. |
| **stdout reaches a journal** | 591 `println!`/`eprintln!` in the player crate and no logging facade. On Android an app's stdout and stderr are discarded. |
| **Linux tools exist** | `path.rs` runs `wpctl` every `WATCH` interval while playing, on every build; `bluetooth.rs` runs `sudo bluetoothctl`; `web/control.rs` ships `sudo systemctl poweroff` to Windows builds too. |
| **Audio is a path** | `decoder.rs` opens with `File::open(path)`. Android's Storage Access Framework hands out descriptors, not paths. |

**`[GDE-HST-025]` `cfg(target_os = "linux")` is false on Android**, whose `target_os` is `"android"`. Three sites therefore compile *differently* there and nothing checks it: `session.rs`'s I/O-priority code, `lib.rs`'s `peak_rss_bytes`, and the `alsa` dependency. `cfg(unix)` is true on both, which is why `director/program.rs` needs no change. This is the `libc` failure of 2026-09-22 in another form — code that compiles where it is written and differently where it runs (CLAUDE.md §10).

## 3. The changes, in order

Each is worth doing for the existing targets on its own, which is what makes the order safe: if the phone never happens, none of it is wasted. The day counts are judgement, not measurement.

**`[GDE-HST-030]` An Android compile check, in the full run and in CI.** Start with `cargo check -p lempi-core --target aarch64-linux-android`, then widen to the player. It needs an image with the NDK, because `libsqlite3-sys` compiles C in its build script even under `cargo check` — too heavy for the pre-commit gate, right for `verify-targets.sh` and CI. Without it the target decays between work sessions, silently, exactly as `[GDE-HST-025]` describes. *~1–2 days.*

**`[GDE-HST-040]` A library-level player host.** Move `engine_thread`'s wiring into the library behind a handle — start, command, snapshot, persist, shutdown — and make `lempi.rs`, `station.rs` and a future JNI layer all call it. It pays off now: **the MPD naming fault fixed in `e899a67` lived in exactly this code, and nothing tested it, because it sat in the binary's wiring rather than in the library.** `session.rs`'s own header says it exists so the two binaries "cannot drift apart on what 'start playing' means"; this finishes that job. The engine's `Command` enum — `Play`, `Pause`, `SetVolume`, `Persist` — already covers Android audio focus and `onStop`. *~3–5 days.*

**`[GDE-HST-050]` A platform seam, and an `appliance` feature.** One trait for host facts — is the sink audible, Bluetooth, restart and power-off, I/O priority, peak RSS — with the Linux-appliance implementation behind the feature. The present behaviour is the argument: with no `wpctl`, `sink::current()` returns `known: false`, and `path.rs` checks only `.dummy`, so on Windows, on any node without PipeWire and on a phone the "is it audible" guarantee disappears without a word. That is the silent skip `[GDE-DEP-060]` forbids. *~2–4 days.*

**`[GDE-HST-060]` A versioned `Snapshot`.** It has 57 fields and no version, and `fbui` already keeps its own hand-copied 11-field `ClientSnapshot`. A phone UI would be the third consumer. Add a `snapshot_version` and a fixture checked by Rust and by `fbui`, the way `fixtures/fade/` is checked by Rust and by `fade.js`. *~1 day.*

**`[GDE-HST-070]` A logging facade whose appliance output is byte-identical.** §4 below. *~1 day for the facade, then one module at a time.*

**`[GDE-HST-080]` When Android work starts, not before:** an audio-source resolver in place of the decoder's path (`engine/mod.rs` has the one call site), and a transfer hash in the payload so on-device import stops needing ffmpeg — separating arrival from identity, the two axes `[SPEC-PL-075]` already distinguishes, rather than moving the hash authority early `[SPEC-RLK-150]`.

**`[GDE-HST-090]` Deliberately not yet: a `web` feature gating `axum` and `tokio`.** Whether it is wanted depends on `[GDE-AND-060]`: if the phone runs the existing skins in a WebView, the server stays.

## 4. Logging: `tracing`, and the constraints that decide whether it helps

**`[GDE-HST-100]` `tracing` is the right interface for the Rust code, and a better fit here than `log`.**

- **It is already compiled in.** `axum`, `axum-core` and `tower` depend on it, and nothing installs a subscriber, so their events currently go nowhere. The player would add `tracing-subscriber`; core would gain four crates (`tracing`, `tracing-core`, `pin-project-lite`, `once_cell`) with `default-features = false`, which also avoids the proc-macro dependencies. None trips the boundary gate.
- **It captures what is being lost.** Every `symphonia` crate, and `tungstenite`, reports through `log`, and no `log` logger is installed — so the decoder's own warnings about malformed files are discarded today, in a project that spent a specification on three stray bytes at the end of an MP3 `[SPEC-RLK-085]`. A subscriber with the `log` bridge keeps them.
- **The lines are already records.** `clock: frames=151552 delay=24374 rate=44100 ts=Hardware callbacks=74` is structured data written out as a string, and scripts grep it back apart. Fields are its natural form.
- **Spans suit the concurrency.** Engine thread, path supervisor, web server, echo client and generation jobs run at once, and the echo timing problems cross both threads and nodes.
- **Every target has a subscriber**, verified on crates.io: `tracing-journald`; `tracing-android` or `paranoid-android` for logcat; `tracing-oslog` for iOS; and `tracing_android_trace`, which puts spans into Perfetto.
- **Library discipline comes free.** Core emits and never installs; the host decides. With no subscriber an event is a cached check.

**`[GDE-HST-110]` The journal cannot tell a warning from a heartbeat.** Measured on `lempi02w`: 500 of the last 500 lempi lines at `PRIORITY=6`; `journalctl -p warning` returns nothing; six of the last 2,000 lines say *cannot*, *failed* or *error* — one of them `cannot name URIs for MPD`, the fault fixed in `e899a67`, sitting at the level of a clock tick.

**`[GDE-HST-120]` Constraint 1 — message text is a contract.** `build/verify-playing.sh` reads `journalctl -o cat` and anchors on `^clock: frames=`; at least five scripts read the journal; `build/deploy-local.sh` writes stdout to `lempi-local.log`. The default `fmt` output adds timestamps, levels, targets and fields and would break all of them. **A line a script reads keeps its message byte-identical; fields are added beside it, never substituted.** Pin those prefixes in a test.

**`[GDE-HST-130]` Constraint 2 — choose the destination by detection.** The low-risk path is already switched on: the unit has `SyslogLevelPrefix=yes`, so a message-only formatter writing `<4>`, `<3>` or `<6>` at the start of a stdout line gets a real priority and journald strips the prefix — `-o cat` unchanged, transport unchanged. Write the prefix only when `JOURNAL_STREAM` is set, which systemd does when stdout is the journal; otherwise a literal `<4>` lands in `lempi-local.log`. `tracing-journald`, which stores fields as journal fields, is a later step, because it moves the transport from stdout to the journald socket.

**`[GDE-HST-140]` Constraint 3 — no events in the audio callback.** `fill()` in `output.rs` prints nothing today; the repository already counts in the callback and reports from the supervisor thread (`output: 1 missed ring lock(s)`). `tracing` makes a casual `trace!` there tempting, and under an active subscriber it formats a string and takes a lock inside the audio deadline. Write the rule into `output.rs`.

**`[GDE-HST-150]` Constraint 4 — filter with `Targets`, not `EnvFilter`.** Installing a subscriber surfaces `axum`, `tower`, `tungstenite` and `symphonia` at once. `tracing_subscriber::filter::Targets` filters by crate without a regex engine; `EnvFilter` brings one, which is a memory decision on a 512 MB node `[REQ-HW-140]`.

**`[GDE-HST-160]` Not every `println!` is a log line.** Program output stays as it is: `build/deploy-appliance.sh` confirms a deploy by reading `lempi --version`. What migrates is diagnostics, and each migrated module locks itself with `#![deny(clippy::print_stdout, clippy::print_stderr)]`. Core goes first — three sites — which also guarantees the library can never choose a phone's output for it.

**`[GDE-HST-170]` "Throughout the system" ends at the language boundary.** Vipunen is Python — 952 `print()` calls, no use of `logging` — and shares no code with Lempi by design `[GDE-ARC-018]`. What can be shared across Rust, Python and shell is a **convention**: the level names, the `<N>` prefix under systemd, and the rule that a message prefix a script reads is a contract. Vipunen never reaches the phone, so it is off this path.

**`[GDE-HST-180]` Order.** (1) Core: `tracing` without default features, the print lints, three sites. (2) Binaries: the subscriber — message-only stdout, `<N>` only under `JOURNAL_STREAM`, `Targets`, the `log` bridge — then the `eprintln!`s that are real warnings first, since they are `[GDE-HST-110]`'s problem, and the prefix test. (3) One module per commit thereafter, never one 591-site diff (CLAUDE.md §1, §9). (4) Android: the host installs a logcat layer, and `tracing_android_trace` when measuring. (5) `tracing-journald` once the stdout path has proved itself.

## 5. Open

1. **`[GDE-HST-200]` The Director's rebuild cost is two different numbers, and battery wants the second.** `[IMPL-SUI-075]` measured `Director::load` at **9.86 s** in isolation with `dircheck`. In service on `lempi02w` — rebuilding while it plays, decodes and serves — the journal holds 17 rebuilds over the same 8,330 radio passages: **median 22.8 s, range 14.5–32.9 s** (2026-09-23/24). A phone's budget should be set against the in-service figure. An isolated re-run of `dircheck` on the node needs the player stopped and has not been done.
2. **`[GDE-HST-210]` `lempi02w`'s journal is over its cap and losing entries.** 71.6 MB against `SystemMaxUse=64M`; the whole of 2026-09-22 retains 221 lempi lines and none of the rebuilds observed that day. Levels and target filtering in §4 would reduce the volume, but the retention policy is its own decision — and until it is made, "the journal holds n of something" is a lower bound.
3. **`[GDE-HST-220]`** The NDK image for `[GDE-HST-030]` is unbuilt, so its size and cold-build time are estimates.

---

**Traceability:** `[GDE-HST-010..220]` · derived from `[GDE-AND-045]`, `[GDE-AND-060]`, `[GDE-DEP-060]`, `[IMPL-SUI-075]`, `[SPEC-RLK-150]`
