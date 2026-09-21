# GUIDE016: Sonos Direct Play, and Why It Stopped

**Development Guidance — investigation closed 2026-09-20; the work itself ran 2026-08-24 to 2026-08-29**

Lempi once played directly to a Sonos pair: its own MP3 encoder, its own HTTP
stream, its own SOAP control of the coordinator, working against real hardware
in a real house. It is abandoned — not because it failed, but because it met a
ceiling that nothing on this side of the wire can move.

This document is the verdict and the three findings worth more than the code.
The implementation, thirteen development records (`SONOS001`–`SONOS013`), the `sonos.rs` module and
its fixtures live on the **`archive/sonos-direct-play`** branch and are not on `main`. Recording the answer is worth as much as recording
the question `[GDE-DIS-010]`: without this, the next person to wonder whether
Sonos direct play is viable has to build it again to find out.

> **Related:** [GUIDE001 §7](GUIDE001-lineage-and-lessons.md#7-disposal-register) `[GDE-DIS-010]` — the disposal register this is filed against · the archive branch: [Sonos/SONOS013](https://github.com/MangoCats/Lempi/blob/archive/sonos-direct-play/Sonos/SONOS013-icecast-experiment-and-deploy-safety.md), [Sonos/SONOS007](https://github.com/MangoCats/Lempi/blob/archive/sonos-direct-play/Sonos/SONOS007-lgpl-implications.md), [player/src/sonos.rs](https://github.com/MangoCats/Lempi/blob/archive/sonos-direct-play/player/src/sonos.rs)

---

## 1. It worked, and that is the point

**`[GDE-SNS-010]` Ten of eleven implementation gaps were closed, four of them
against live hardware.** This was not a sketch. Exclusivity was enforced by
reusing `path.rs`'s own `set_playing()`; a failed activation no longer left the
engine believing a ring was wanted after its encoder had stopped; volume
reached the speaker through `RenderingControl#SetVolume`, mapped from Lempi's dB
range onto Sonos's `0..=100`; and a periodic `GetMediaInfo`/`CurrentURI` re-check
detected losing the speaker to another controller, guarded by a generation
counter so a superseded session could not act.

Five bugs were found and fixed live against the real pair in a single session,
including an encoder racing ahead of real time and local-device backpressure
starving the Sonos ring. Skip was made to cut `sonos_ring` the same way it
already cut `path.ring`, verified both by a test that fails without the fix and
by a live skip that returned `204` with the stream undisturbed.

**One item of eleven remains unmeasured** — end-to-end encoder latency, which
needs a stopwatch against real hardware and is the listener's own to run.

---

## 2. The ceiling: a reconnect cadence nothing on this side can fix

**`[GDE-SNS-020]` The stream drops and re-requests every 30–39 seconds, and
three independent experiments failed to move it.** Measured against the real
pair: eight consecutive reconnects at 34, 33, 39, 31, 35, 39 and 32 seconds.

Ruled out, cumulatively:

| Hypothesis | How it was eliminated |
| :--- | :--- |
| Chunked-transfer framing | Tested and ruled out earlier in the investigation |
| The claimed content-length (`FakeLength`) | Corrected against a proven reference; cadence unchanged |
| Missing ICY / Shoutcast metadata | **A real Icecast server** was put in front of the identical audio |

The Icecast experiment is the strong one. Sonos's own User-Agent against the
Icecast mount changed to the classic Shoutcast handshake string
(`… Nullsoft Winamp3 version 3.0 (compatible)`), confirming it genuinely
detected and used ICY framing — a different code path from talking to Lempi
directly. **It reconnected on the same schedule anyway.** Music Assistant's own
tracker reports the behaviour as format-independent, affecting `.flac` and
`.aac` alike.

**The conclusion is a Sonos-firmware-side timeout for this class of stream, not
a serving-side defect.** The tractable target was never eliminating the
disconnect but narrowing the gap it leaves — buffer-ahead, fast re-request,
something shaped like Music Assistant's "queue flow" mitigation. That work was
not started, and no further transport-layer experiment is worth running without
new evidence to justify it.

---

## 3. The LGPL finding, which outlives the feature

**`[GDE-SNS-030]` Statically linking LAME does not relicense Lempi, and this was
read against the licence text rather than its reputation.** Two licences are
actually in play: LAME's own C source is **LGPLv2**, while the Rust binding
crates (`mp3lame-sys` / `mp3lame-encoder`) are separately declared **LGPL-3.0**.
The conservative course is to satisfy LGPLv3 §4 for the combined result, which
is at least as strict as LGPLv2 §6 on every point that matters.

The fact that resolves most of the worry is LGPLv3 §0's own definitions. LAME is
*the Library*. Lempi is *an Application* — "any work that makes use of an
interface provided by the Library, but which is not otherwise based on the
Library", which is exactly what calling LAME's public encoding API is. The
linked binary is *a Combined Work*. **The licence reaches the Library and the
Combined Work's §4 obligations, never the Application's own source.** Lempi's MIT
licence on every line outside LAME is untouched, statically linked or not.

Two obligations were identified and **never discharged**, because nothing
shipped:

- **§4(a)** — a prominent notice that the Library is used and is covered by the
  licence. The natural home is the `--version` output and the web UI, which
  already carry the build identity `[REQ-VIS-200]`.
- **§4(b)** — a `THIRD-PARTY-LICENSES` file carrying LAME's own `COPYING`
  (LGPLv2) and the LGPLv3 text alongside the repository's own `LICENSE`.

**Anything that revives MP3 encoding inherits both.** They are one file and one
line of output, not a process change — but they are not optional.

---

## 4. The lesson that applies whether or not Sonos ever returns

**`[GDE-SNS-040]` A correct guard can be expensive when the thing it guards
against was deliberate.** The loss-of-control watcher did exactly what it was
built to do when a standalone script redirected the coordinator out-of-band:
`CurrentURI` no longer matched what Lempi had set, two failed confirmation polls
followed, and it fell back to local output within about twenty seconds.

The cascading cost had not been reasoned through. Falling back tore down Lempi's
own encoder, which killed the relay feeding the experiment, and local fallback
found only a dummy sink — **nothing audible in the house for about a minute**,
noticed through `journalctl` rather than by ear. That is `[GDE-DEP-060]`'s shape
in a different subsystem: the guard announced nothing a listener could hear, and
the silence read exactly like working equipment.

It was fixed the right way — a first-class redirect endpoint that retires the
superseded watcher and starts a fresh one with the new URL as what "confirmed"
means — rather than by weakening the guard. **A deliberate redirect and a real
takeover must stay distinguishable**; the answer is to teach the system about
the deliberate case, never to make the detector less sensitive.

---

## 5. Where it lives now

**`[GDE-SNS-050]` Archived, not deleted, and it does not travel.** The branch was
renamed `Sonos` → `archive/sonos-direct-play` on 2026-09-20, tip `8d23474`,
following
`archive/v1-python-and-go-evaluation`'s precedent: source preserved, verdict
recorded, nothing merged to `main`.

Two consequences worth stating rather than discovering:

- It was **23 commits behind and already conflicting** — eleven conflicts
  against the current tree, including the engine module deleted here and modified
  there. Reviving it is a rewrite against today's engine, not a merge.
- **It did not reach this repository.** This one was seeded from the previous
  repository's final state, and archived branches stayed behind there; that
  repository is private `[GDE-NAM-020]`. Everything in this
  document is here precisely because a pointer to that branch will not be enough
  for long.
