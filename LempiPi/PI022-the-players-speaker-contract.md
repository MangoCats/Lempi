# PI022: The Player's Contract for a Bluetooth Speaker

**Appliance Record — what the player must provide, and what it may not do**

Split from [PI003](PI003-choosing-a-speaker.md) on 2026-09-10, which had
reached 337 lines against `[GOV-DOC-010]`'s 300-line limit. PI003 says what
a speaker has to survive and what the listener should feel; this says what
the software owes in return.

> **Related:** [PI003](PI003-choosing-a-speaker.md) for choosing the speaker ·
> [PI004](PI004-speaker-operation.md) for operating the link

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1-2 in [PI003](PI003-choosing-a-speaker.md), §3-5 in [PI022](PI022-the-players-speaker-contract.md).

## 3. What the player must provide

**`[PI3-API-010]` Reopen the output on demand. Built and proven on hardware.**
`POST /command/reopen-output` rebuilds the stream against the current default
sink, keeping the same ring. Verified by the output node id changing and by
`pw-top` showing the rebuilt node running at 1102 quantum, 44100, F32LE 2ch,
with playback continuing across the reopen. A reopen that lands on a device not
ready yet -- a speaker still completing its connection is the normal case --
hands itself to the retry loop rather than failing once.

**`[PI3-API-020]` Report the sink actually in use. Built.** `GET /audio/sink`
answers from PipeWire, because ALSA only ever tells the player `default`.
Queried on demand rather than polled: it costs a subprocess, and a player that
shelled out every state tick would spend more effort describing its output than
producing it. `known: false` distinguishes "the query could not run" from "it
ran and found nothing", since the remedies differ. Verified on hardware, both
ways round:

    speaker connected  {"sink":"MIDDLETON","dummy":false,"known":true}
    speaker removed    {"sink":"Dummy Output","dummy":true,"known":true}

**`[PI3-API-030]` Never settle for a dummy, and notice when one arrives.
Built.** Two paths, because the first one shipped covering only half the
problem. The loud failure -- a stream that breaks -- is caught by the error
callback. The quiet one is a speaker switched off during normal playback:
PipeWire moves the stream to the `Dummy Output` and **reports no error at
all**. Nothing in the player is wrong at that moment. The callback runs, the
ring drains, the clock advances, and nobody can hear a thing. A guard that
fires only on reopen therefore almost never fires. So the engine also confirms,
every twenty seconds while playing, that the audio still reaches something
real.

**`[PI3-API-030]` (original) Never settle for a dummy.** `lempi-wait-sink` guards
boot; the engine now guards every reopen and every recovery. Opening
successfully says nothing about whether anyone can hear it -- the dummy accepts
audio perfectly -- so a reopen that lands there is marked failed and the retry
loop keeps looking. The player will not report itself recovered into silence.

**`[PI3-API-050]` Routes fronting the helper. Built.** `GET /audio/speakers`
lists; `POST /audio/speakers/:verb/:address` acts. The verb is an enum rather
than a string passed through, so an unknown one cannot reach the helper, and
the address is validated again in the player -- the helper's check is the one
that must not be bypassed, this one makes a malformed address a 400 with an
explanation rather than a non-zero exit nobody reads.

`use` reopens the output **as part of the same request** `[PI3-UI-020]`. Doing
it server-side rather than in the browser means no caller can forget the step
that makes the choice audible.

Every reply carries `audible`, which is the question a listener actually has.
It is `null` rather than `false` when the stream is not linked yet: "we could
not tell" and "it is not working" want different responses, and collapsing them
is how a fault stays hidden. The reply also waits for the reopen to land before
reporting -- reading immediately returned `sink:null` with `dummy:false`, which
reads as healthy and was merely early.

Choosing a speaker from silence, in one call:

    before  {"sink":"Dummy Output","dummy":true,"known":true}
    POST    /audio/speakers/use/20:64:DE:CF:F3:AD
    reply   {"audible":true,"state":"connected","reopened":true,
             "output":{"sink":"MIDDLETON","dummy":false,"known":true}}

**`[PI3-API-040]` Surface the recovery count `[REQ-VIS-140]`.** A link that
drops and recovers repeatedly is a range or battery problem, and a number
climbing in the diagnostics is how anyone would know. Silent recovery is right
for one dropout and wrong for fifty.

---

## 4. Privilege

**Built: `lempi-btctl`.** A closed set of verbs -- `list`, `scan`, `pair`,
`repair`, `use`, `forget`, `status` -- each taking at most a device address,
checked against an anchored MAC pattern *before* it reaches BlueZ. Nothing is
interpolated into a shell. It emits JSON so the caller parses a shape rather
than prose, and it is reached through a sudoers rule naming that one binary
rather than by granting the player broader rights.

Three verbs encode failures rather than operations. `pair` registers an agent
and paces its steps, because piping them as one block runs them faster than
bluetoothd establishes the connection `[PI3-WHY-060]`. `repair` removes the
device first, because a stale trusted-but-not-paired record makes every attempt
fail with no explanation `[PI3-WHY-050]`. `use` trusts as well as connects,
because trust is what survives a reboot `[PI3-WHY-040]`.

The sudoers file is validated with `visudo -c` and **removed if malformed** --
a bad one can lock the machine out of sudo entirely, and a setup script that
bricks its own escape route is worse than one that does nothing.

Verified on hardware. Injection and malformed addresses are refused
(`{"ok":false,"error":"not a device address"}`), unknown verbs are refused, and
the whole `[PI3-UI-020]` sequence runs from a silent start:

    before   {"sink":"Dummy Output","dummy":true,"known":true}
    use      {"ok":true,"state":"connected","sink_node":"46"}
    reopen   HTTP 204
    after    {"sink":"MIDDLETON","dummy":false,"known":true}

with `pw-top` confirming the node running at 1102 quantum, 44100, F32LE 2ch.

---

## 4a. The reopened stream, and feeding silence while paused

**A reopened stream was found to die where a fresh one does not — investigated
2026-08-16, improved, not proven closed.** What was found, ruled out, and
fixed is history: see
[PI008 §2](PI008-appliance-bringup-history.md#2-a-reopened-stream-dies-a-fresh-one-does-not).
Whether it is now fully closed is a still-open question, tracked in
[ROADMAP §4](../docs/ROADMAP.md#4-the-appliances-still-open-speaker-questions)
rather than here.

**`[PI3-OPEN-020]` Feed silence while paused. Built.** McRhythm did this, and
the same reasoning applies: A2DP tears down when nothing feeds it `[PI3-WHY-030]`, so a
paused Lempi loses its speaker after a few minutes and resuming needs a
reconnect. Pausing should stop the *music*, not the stream. This also likely
reduces how often the fragile reopen path is needed at all, which makes it
worth doing regardless. Pausing now silences the callback rather than stopping
the device: the ring is left untouched so resuming is still instant
`[REQ-AUD-142]`, and the silence is not counted as an underrun, because
inflating the one diagnostic that matters most to hide an intended quiet would
be its own small lie.

## 5. Deliberately not now

**`[PI3-NOT-010]` (superseded) Interference was not being designed around.** The dark arm of
`radio-silence-test.sh` exists to answer that question and has not been run,
because with Wi-Fi up the link now measures 40/40 across two minutes with audio
flowing. Should unexplained connection problems appear, that test is the tool
to reach for. Building mitigations for a problem not yet observed would be
designing against a guess.

**`[PI3-NOT-020]` Multiple simultaneous sinks.** One speaker, chosen. Whole-house
audio is a different product.

---

**What operating it taught** is in [PI004](PI004-speaker-operation.md): this
document says what the link must do, that one says what it did.

