# GUIDE013: What a Node's Audio Stack Will Actually Tell You

**Development Guidance — split from [GUIDE010](GUIDE010-echo-node-capabilities.md) on 2026-09-12, which had reached the 300-line limit `[GOV-DOC-010]`**

Everything below was read out of `cpal` 0.15.3's own source rather than
inferred from its documentation, because the failure modes are all in the
fallbacks — and then checked against two running appliances, where one of the
plans it describes turned out not to work at all `[LOG-DRIFT-055]`.

GUIDE010 answers *what a node is*; this answers *what its software will
honestly report about itself*, which turned out to be a good deal less.

> **Related:** [GUIDE010](GUIDE010-echo-node-capabilities.md) — the node model this serves · [LOG006](LOG006-echo-drift-measurement.md) `[LOG-DRIFT-055]`, `[LOG-DRIFT-058]` — where these findings were tested and two of them failed · [GUIDE009](GUIDE009-echo-playback-plan.md) `[GDE-ECHO-280]` — the frame clock built on them

---

## 3. What the audio stack actually provides

Everything below was read out of `cpal` 0.15.3's own source rather than inferred
from its documentation, because the failure modes are all in the fallbacks.

**`[GDE-ECHO-140]` The callback is already handed a timestamp, and Lempi throws
it away.** In `player/src/output.rs`, all three sample-format arms are
`move |out, _| fill(...)` — the discarded `_` is `&cpal::OutputCallbackInfo`.
The hook needed for any of this already exists, at the one place
`[REQ-AUD-164]` says measurements must be taken.

**`[GDE-ECHO-150]` cpal's ALSA backend does enable hardware timestamping — in a
clock domain chrony deliberately does not discipline.** It calls
`set_tstamp_mode(true)` then `set_tstamp_type(TstampType::MonotonicRaw)`, and on
failure silently retries with `Monotonic`. `CLOCK_MONOTONIC` is frequency-slewed
by `adjtimex` and therefore *is* disciplined; `CLOCK_MONOTONIC_RAW` is not. So
cpal's timestamps arrive in one of two domains differing by the system crystal's
own error — tens of ppm, one to two orders of magnitude larger than the DAC
error being measured — and the API offers no way to learn which one was given.

**`[GDE-ECHO-160]` The absolute instants are not comparable across machines; the
difference between them is.** `StreamInstant` is measured from the stream's own
trigger, so two nodes' values share no epoch. But `playback.duration_since(callback)`
is computed as `frames_to_duration(status.get_delay())` — the genuine ALSA
hardware delay, expressed as a duration, and therefore domain-independent and
directly usable. **Take the difference, never the absolutes** is the whole rule,
and it is `[GOV-SRC-050]` in miniature: the two values answer different
questions.

**`[GDE-ECHO-170]` If hardware timestamps are unavailable, cpal substitutes a
software clock permanently and silently.** At stream open it probes
`get_htstamp()` once; on `(0, 0)` it stores an `Instant` and every later
timestamp becomes elapsed time since stream creation — a pure software monotonic
reading carrying **no information about the DAC at all**, in which drift is
definitionally invisible. Consumed unknowingly, that is a textbook
`[GOV-SRC-030]` breach: the weaker source answers in the same shape as the
stronger one, destroying the evidence that would expose it. Any use of these
timestamps must detect and declare the fallback.

**`[GDE-ECHO-180]` On Windows the delay term is an estimate by its own
admission.** cpal's WASAPI backend carries the comment that the returned
`playback` value "is an estimate that assumes audio is delivered immediately
after the callback." The desktop can therefore be a master or a controller, but
must not be assumed measurable as an echo node until someone measures it.

**`[GDE-ECHO-190]` There is a `panic!` on the audio thread in that path.**
cpal's `stream_timestamp` panics outright if `get_htstamp` precedes
`get_trigger_htstamp`, and two neighbouring `.expect()` calls abort on
`StreamInstant` range overflow. Lempi does not currently reach any of them
because it does not read the timestamp; a design that starts reading it inherits
them, and must not add a reason to call into that code more often.
