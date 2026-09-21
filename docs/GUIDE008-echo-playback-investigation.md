# GUIDE008: Echo Playback — Two Instances, One Programme, Their Own Files

**Development Guidance — investigated 2026-09-11, preliminary to [GUIDE009](GUIDE009-echo-playback-plan.md)**

Whether two or more Lempi instances can play the *same passage at the same
moment* from each node's own local copy of the file, and what it would take to
keep them together. Evaluated the way [GUIDE007](GUIDE007-external-backends-investigation.md)
and [SPIN001](../sendspin/SPIN001-protocol-and-integration-analysis.md) evaluated
their subjects: cost against what already exists here, and no ranking asserted
without a measurement behind it `[GOV-SRC-020]`.

> **Related:** [GUIDE010](GUIDE010-echo-node-capabilities.md) — what makes a node eligible · [GUIDE013](GUIDE013-audio-stack-reporting.md) — what its audio stack will actually report · [GUIDE009](GUIDE009-echo-playback-plan.md) — the development plan this concludes in · [SPIN001](../sendspin/SPIN001-protocol-and-integration-analysis.md) — the multi-room protocol this is the cheap alternative to · [BOSE004](../BosePi/BOSE004-operating-health.md) — the drift measurement this rests on · [REQ003](spec/REQ003-audio-playback.md) — the buffer-depth rule that shapes every correction · [SPEC017](spec/SPEC017-what-counts-as-a-play.md) — play logging, unchanged by this · [SPEC035](spec/SPEC035-mesh-library-sync.md) — what makes the files identical in the first place

---

## 1. What is being proposed

**`[GDE-ECHO-010]` Echo, not streaming — and that is the whole reason it is
cheap.** Each node decodes its own file to its own device; only a control plane
crosses the network. No codec, no Noise handshake, no audio transport, no
pairing UI, and nothing that contradicts `[REQ-AUD-150]` or `[REQ-NEG-100]`:
Lempi still does not stream audio to remote devices. This is what separates it
from every mode [SPIN001](../sendspin/SPIN001-protocol-and-integration-analysis.md)
priced — Mode A would have made Lempi a server fanning PCM out to receivers,
which `[GDE-SPIN-110]` identified as "a shape nothing in
`player/src/playback.rs`'s `Playback` trait currently models."

The identity of "the same audio" is already solved and already verifiable:
`md5_encoded` `[REQ-AUD-100]`, with [SPEC035](spec/SPEC035-mesh-library-sync.md)
as the mechanism that puts the same bytes on both nodes.

**`[GDE-ECHO-020]` Echoing is consent, so the master holds no state about its
followers.** A node that elects to echo another thereby accepts that node's
passage selections; the master broadcasts what it is playing and where it is,
and never learns who listened. No leader election, no membership list, no
session negotiation, no back-channel. A master need not know it is one. This
collapses what first looked like a queue-ownership problem into a publish and a
subscribe, and it is the largest single simplification available here.

**`[GDE-ECHO-030]` A play is logged per device, on the existing threshold, with
no exception carved for echoing.** If a passage was audible in a room long
enough to qualify `[SPEC-PLAY-010]` — half the passage or four minutes,
whichever comes first — then it played in that room, whether the node chose it
or echoed it. Two rooms hearing one programme are two plays because they were
two listening events, not one event counted twice. Class D stays strictly local
`[SPEC-MESH-005]`, each node's `[SPEC-FREQ-010]` data remains an honest record
of that room, and `player/src/scrobble.rs` needs no change at all.

---

## 2. What actually drifts

**`[GDE-ECHO-040]` Two problems wear one name, and only the second is hard.**
Agreeing *when to start* is a wall-clock problem, solved far better than it needs
to be by any ordinary time daemon. Staying together afterwards is a
*sample-rate* problem: each DAC's crystal runs at its own true frequency, and
perfect knowledge of the time of day says nothing about whether the other box's
44.1 kHz is really 44.1002 kHz. Conflating the two is what makes time-sync
protocols look more relevant to this than they are.

**`[GDE-ECHO-050]` Measured on `bose`: ≈+14 ppm** — corrected 2026-09-13,
see `[LOG-FIX-030]`. The 0.35 ppm this section carried until then, from
`[BOS-OPS-020]`'s 14,126,435,269 frames against 320,327.218 s of "monotonic
time", was an artefact: that second figure is `tstamp − trigger_time`, and
`tstamp` is derived from the frame counter, so the comparison was the counter
against itself `[LOG-FIX-010]`.

The corrected figure is the HiFiBerry DAC+ Pro's crystal against the Pi's
chrony-disciplined system clock. It is **ordinary** — comfortably inside the
±20–100 ppm a generic consumer crystal is quoted at, rather than fifty to
three hundred times better than it, as this section previously claimed. The
Pro's dedicated 44.1/48 kHz oscillators buy a clean ratio and low jitter, not
absolute accuracy. Nothing here should be read as implying a self-clocked
device is an accurate one `[LOG-FIX-040]`.

What that implies for a pair of nodes, where relative error is roughly the sum
of each node's own:

| relative error | per 4-min passage | per hour |
| :--- | ---: | ---: |
| 0.7 ppm — two DAC+ Pro class | **0.17 ms** | 2.5 ms |
| 2 ppm | 0.48 ms | 7.2 ms |
| 20 ppm — one ordinary node | 4.8 ms | 72 ms |
| 100 ppm — one poor node | 24 ms | 360 ms |

Against audibility, when one listener hears both:

| offset | what it sounds like |
| ---: | :--- |
| < 0.1 ms | one source |
| ~0.5 ms | Sendspin's stated target `[GDE-SPIN-010]` |
| 1–5 ms | comb filtering; hollow, phasey colouration |
| 5–30 ms | fuses, but the image pulls hard to the earlier speaker |
| > 30 ms | a distinct slap |

**`[GDE-ECHO-060]` That number is one device, and it licenses nothing about any
other.** `[GOV-SRC-020]` applies to this document's own headline figure: 0.35
ppm is a measurement of `bose`, over one stable indoor window, and a crystal
moves with temperature. It is evidence that *this class of hardware can be very
good*, not evidence that any given node is. The instrument is cheap and already
proven — `pcm0p/sub0/status` read against monotonic time, exactly as BOSE004 did
it — so the honest move is to measure each node before deciding what correction
it needs. That is why [GUIDE009](GUIDE009-echo-playback-plan.md) measures every
node before designing a correction — behind a timebase phase that has to settle
first `[GDE-ECHO-305]`.

**`[GDE-ECHO-070]` `lempipi` is not in this table, but it is not excluded by
category either — and an earlier revision of this document had that wrong.** It
drives the Middleton over BlueALSA, through a stack-dependent latency of roughly
100–250 ms. That was first written up as disqualifying. It is not, and the
reason is the same property that makes this whole design cheap: an echo node
holds the audio on its own disk and knows the queue ahead of time, so it can
**start early by exactly its own offset**. A streaming receiver cannot, because
it does not yet have the audio to start with. A large fixed latency is a
scheduling constant.

What actually disqualifies a node is offset *variability*, not offset
*magnitude*, and the two were conflated.

> **Measured 2026-09-12, and the blocker turns out to be neither.** `lempipi`
> is *unobservable* by software: no hardware PCM to read, cpal reporting
> `Software`, and a frame clock that measures PipeWire's timer rather than the
> speaker `[LOG-DRIFT-058]`. Variability is not the obstacle — being unable to
> see it at all is. So the deferral stands, on evidence rather than caution,
> and for a different reason than this paragraph first gave. The acoustic
> cross-correlation `[GDE-ECHO-460]` is promoted from useful to the only
> instrument that can reach this node. The model that replaces the
device-class reasoning is in
[GUIDE010](GUIDE010-echo-node-capabilities.md) `[GDE-ECHO-420]`.

**`[GDE-ECHO-075]` `smartboardpc` ("Smart") adds a third output class — USB
audio — and it is the hardest of the three to time.** Surveyed in full in
[SMART001](../SmartPC/SMART001-survey.md); three findings bear on echo playback.

Its speaker is driven by a USB dongle whose **playback endpoint is `ADAPTIVE`**
`[SMT-AUD-040]`, meaning the sink slaves its rate to the host's feed rather than
to a crystal of its own. Smart's drift is therefore a property of the Intel USB
controller and the driver, shares **no physical cause** with `bose`'s
self-clocked I²S DAC, and cannot be inferred from `[BOS-OPS-020]` at any
confidence — which is `[GDE-ECHO-060]`'s argument arriving as a concrete case
rather than a caution.

It offers **no 44.1 kHz rate** `[SMT-AUD-050]`, so every passage is resampled
there regardless. One of `[GDE-ECHO-210]`'s two arguments against correcting
drift by resampling is void on this node — there is no bit-exact pass-through
left to protect — and the second, that the existing resampler is built for exact
ratios, still decides it.

And `snd-usb-audio`'s timestamp granularity is bounded by the USB frame at 1 ms,
the whole of `[GDE-ECHO-260]`'s budget. Smart is the best available test of
`[GDE-ECHO-290]`'s fallback detection and the worst available candidate for a
sub-millisecond pair: a fleet node and a measurement subject, not a first echo
target.

---

## 3. Why not PTP

**`[GDE-ECHO-080]` PTP solves the easy half at a thousand times the necessary
precision, and does not touch the hard half.** Scheduling a joint start needs a
shared timebase good to perhaps ±1 ms; sub-microsecond accuracy buys nothing
beyond that, because the thing being scheduled is a buffer submission, not a
gate pulse. And no wall-clock precision whatsoever corrects a sample-rate
mismatch — a node with a perfect clock and a 20 ppm DAC still walks away at
4.8 ms per passage.

**`[GDE-ECHO-090]` Over Wi-Fi its advantage is not merely wasted but
unavailable.** PTP's accuracy rests on symmetric, deterministic path delay.
Wi-Fi violates that by tens of milliseconds through retries and power-save, and
Pi-class NICs have patchy hardware timestamping. The relevant existence proof
runs the other way: Snapcast holds multi-room sync over ordinary Wi-Fi with
plain offset estimation and continuous correction. Sendspin's own design agrees
— the interesting part of `[GDE-SPIN-010]` is a Kalman filter and a
converged-before-joining rule, which is a control loop, not a clock protocol.

**`[GDE-ECHO-100]` Chrony is the chosen timebase, and what it must deliver is
modest.** It has to give every node a common wall clock stable to ~1 ms for
scheduling starts, and — more importantly — a common *frequency* reference
against which each node's DAC error can be expressed as a number meaning the
same thing on both machines. It does not have to be accurate in absolute terms;
it has to be the same on both. Verifying that on the fleet is Phase 0 of
[GUIDE009](GUIDE009-echo-playback-plan.md), not an assumption of this document.

---

## 4. Is re-synchronising once per passage enough?

> **Superseded 2026-09-12 for any pair including `smartboardpc`.** Measured in
> `[LOG-DRIFT-045]` — Smart **+9.96 ppm**, and `bose` **≈+14** as corrected
> in `[LOG-FIX-030]`; ~4 ppm relative — ~1.0 ms across a four-minute passage,
> inside the comb-filtering band. The conditional below required both nodes to
> be sub-ppm and **neither is**, so it fails for every measured pair, not only
> those including a host-slaved node `[LOG-DRIFT-050]`. Clock ownership does
> not predict rate accuracy `[LOG-FIX-040]`.

**`[GDE-ECHO-110]` At the drift actually measured, yes — comfortably, and for
same-room listening.** 0.17 ms accumulated across a four-minute passage is
inside Sendspin's ±0.2–0.5 ms target, reached with no control loop running at
all. The conclusion is real but *conditional on `[GDE-ECHO-060]`*: it holds for
a pair of nodes measured at sub-ppm, and it fails by a factor of thirty against
a 20 ppm node, where per-passage resync leaves 4.8 ms of audible comb filtering
by the end of every track.

**`[GDE-ECHO-120]` There is, in any case, no seam to put a step correction in.**
Passages crossfade `[REQ-AUD-130]`, and even a skip is a crossfade with a 1.5 s
summed overlap `[REQ-AUD-162]`. "The start of a passage" lands in the middle of
audible material from the previous one, on two streams at once. A design that
assumed a silent boundary would be assuming something this engine does not have.

**`[GDE-ECHO-130]` Which settles the mechanism rather than threatening it.**
Because the required correction is so small and so rare, it never needs to be a
step: a drift budget of 0.17 ms per passage is met by trimming a single frame
every few minutes. The absence of a seam stops mattering the moment the
correction is small enough to hide anywhere. The design target is therefore
**continuous, unconditional, sub-frame-per-second trimming**, not a boundary
event — and per-passage resync survives only as the coarse fallback for a node
measured badly enough to need it.

---

## 5. The mechanism this points to

**`[GDE-ECHO-200]` Do not consume cpal's instants. Keep an own frame clock and
regress it against the shared timebase.** Count frames emitted in the callback,
sample the chrony-disciplined clock in the same breath, and fit rate over
minutes. This sidesteps `[GDE-ECHO-150]`'s domain ambiguity, `[GDE-ECHO-160]`'s
missing epoch and `[GDE-ECHO-170]`'s invisible fallback in one move, while still
using `playback − callback` for the one thing it is good for — the hardware
delay between submission and sound.

**`[GDE-ECHO-210]` Correct by dropping and inserting frames, not by
resampling.** Two reasons, the first being this project's first requirement.
`[REQ-AUD-100]` asks that the decoded stream be **unaltered**, and an always-on
drift resampler would mean audio is never bit-exact — including the 44.1→44.1
case that is a free pass-through today in `player/src/resample.rs`. Drop/insert
preserves exactness everywhere except at the correction points, of which
`[GDE-ECHO-130]` predicts a handful per hour. Second, the existing resampler is
deliberately built wrong for this job: its chunk size is chosen as a multiple of
`from_hz / gcd` precisely to make the ratio *exact*, after an arbitrary size
measured a 1 % error, and fractional drift ratios are what that design exists to
exclude.

**`[GDE-ECHO-220]` This is the fifth instance of the rule `[REQ-AUD-164]`
already generalised.** Pause had to stop the device `[REQ-AUD-142]`, volume had
to move into the callback `[REQ-AUD-152]`, skip had to cut the ring
`[REQ-AUD-158]`, the display had to lag the mixer — and now sync has to be
measured and applied at the device, because a correction computed at the mixer
is heard fifteen seconds later and a position measured there is fifteen seconds
early. The engine already distinguishes the two clocks correctly in
`player/src/engine/mod.rs` (`played_ms` against `audible_ms`), and the ring
already carries the scheduled-surgery primitives `truncate` and `mix_at` that
`[REQ-AUD-158]` needed. The vocabulary exists; only the frame clock is missing.

**`[GDE-ECHO-230]` No Cargo feature, unlike `[GDE-SPIN-180]`.** That precedent
earns its keep on dependencies — `vipunen-support` exists so an appliance never
resolves, fetches or compiles the HTTP client at all. Echo playback plausibly
adds **no crate**: the control plane rides axum's existing `ws`, chrony is an OS
daemon outside the binary, and drop/insert is arithmetic on a buffer already
owned. With no dependency argument, what remains is a default-off code path in
the realtime thread — exactly the disused path `[REQ-AUD-160]` warns about, and
one that cannot be smoke-tested by a single appliance because it is unobservable
without two. Build the frame clock unconditionally; gate the networked behaviour
at runtime on a `player_settings` row `[SPEC-SC-099]`, where it stays compiled.

---

## 6. Conclusion

**`[GDE-ECHO-240]` Worth building, in this order, and not before measuring.**
The concept is sound, materially cheaper than any protocol adoption, and the
only multi-node shape that leaves `[REQ-AUD-150]` intact. Its headline risk is
neither the network nor the clock — it is that the one drift figure in evidence
belongs to one machine on one afternoon. The development plan therefore begins
by measuring every candidate node and only then chooses a correction: see
[GUIDE009](GUIDE009-echo-playback-plan.md).
