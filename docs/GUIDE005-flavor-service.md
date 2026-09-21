# GUIDE005: Flavor Without Vipunen

**Development Guidance — what a hosted flavor service would be, and whether $9 once pays for it**

`[GDE-IOS-050]` named decoupling the phone from Vipunen as the thing that changes adoption by orders of magnitude. This is what that would actually be.

> **Related:** [GUIDE004 §5](GUIDE004-phone-port-strategy.md#5-would-anyone-use-it-an-estimate-not-a-measurement) · [SPEC007](spec/SPEC007-vipunen-architecture.md) · [GUIDE003](GUIDE003-feature-extraction-strategy.md) · [LICENSING.md](../LICENSING.md)

---

## 1. Two things wear the same name, and only one is viable

**`[GDE-CLD-010]` "Vipunen in the cloud" means either uploading audio or looking up
an answer, and the difference decides everything.**

| | **A · Hosted extraction** | **B · Flavor lookup** |
| :--- | :--- | :--- |
| What crosses the wire | **the user's audio files** | a recording MBID |
| Per 5,000-track library | ~44 GB up | **~6.5 MB down** |
| Server does | Essentia, 27 s/track `[SPEC-SA-025]` | a key-value read |
| Compute per library | ~37 CPU-hours | milliseconds |
| Holds copyrighted audio | **yes** | **no** |
| Works for music nobody has analysed | yes | no |

**`[GDE-CLD-015]` A is not primarily a cost problem, it is a liability problem.**
The compute is affordable — 37 CPU-hours at commodity rates is under a dollar
against $7.65 net from a $9 sale. What is not affordable is **receiving and
storing other people's music**. That is a takedown regime, a safe-harbour
posture, an abuse surface, and a permanent invitation to be the defendant. No
$9 fee prices that, and no amount of "we delete it after processing" changes
what the service is while it holds the file.

**B never touches audio at all**, which is why the rest of this document is
about B.

---

## 2. What B looks like

**`[GDE-CLD-020]` The client identifies; the service only answers.**

1. The device fingerprints the file locally with **Chromaprint** — LGPL, and
   `fpcalc` already ships `macos-arm64` and `linux-arm64` builds `[SPEC-SA-060]`,
   so the one binary that blocks Vipunen on ARM does not block this.
2. The fingerprint goes to **AcoustID** and comes back a recording MBID.
3. The MBID goes to the service and comes back a **71-dimension flavor vector**.
4. The device writes it into its own `lempi.db` exactly as Vipunen would, with
   provenance recorded `[SPEC-SC-025]`.

**No audio leaves the device at any step.** The service never learns what music
anyone owns beyond the identifiers they ask about, and holds nothing it could be
asked to take down.

**`[GDE-CLD-025]` The data is already public domain.** Verified 2026-08-21:
AcousticBrainz shut down in February 2022, took the API offline, and published
the entire dataset as a one-time dump — **CC0**, still downloadable, high-level
and low-level, in 30 zstd archives of a million files each. There is no licence
obstacle to hosting it, redistributing it, or shipping a subset inside an app.

**`[GDE-CLD-030]` AGPL is not an obstacle either, and it is worth saying why.**
Vipunen is AGPL because Essentia is `[GDE-ARC-018]`, and AGPL §13 obliges whoever
*operates* a modified version over a network to offer its source — an obligation
falling on the operator, not on clients. Service B runs no Essentia at all: it
serves CC0 data over HTTP. Even service A would only oblige its operator to
publish source that is already public. **An MIT phone app talking HTTP to either
is unaffected**, for the same reason `[SPEC-SA-015]` gives — separate programs,
no linked code.

---

## 3. Does $9 once pay for it?

**`[GDE-CLD-040]` At B's shape, comfortably — because the marginal cost is
almost nothing.**

| | |
| :--- | ---: |
| Net per sale, 15% commission `[GDE-IOS-060]` | **$7.65** |
| Bandwidth per user, whole library once | ~6.5 MB |
| Marginal cost per user, at commodity egress | **well under $0.01** |
| Fixed: Apple Developer Program | $99 / yr |
| Fixed: a VPS and object storage able to serve this | ~$120–500 / yr |
| **Break-even** | **~29–79 sales per year** |

Against GUIDE004's estimate of hundreds to low thousands of *installs* for a
free app — a paid one converting far worse — that break-even is **plausible but
not comfortable**. It is the same order as the outcome, not an order below it.

**`[GDE-CLD-045]` The real hazard is not the arithmetic. It is that revenue is
one-time and obligation is perpetual.** Every sale buys an indefinite dependency
on a server the buyer does not control. Ten years of hosting is sold once, at
$7.65, and the bill arrives monthly for ever.

**So the service must be a convenience, never a dependency.** Concretely:

- the app **works without it**, degrading to tags and no flavor — which is
  already a supported state, since unidentified passages are playable
  `[SPEC-SC-050]` and a partial vector is normal `[SPEC-SC-060]`;
- every answer is **cached permanently** in the device's own database, so a
  library looked up once never asks again;
- the lookup is **batched and one-shot**, not a per-play call.

Do that and a $9 one-time fee is honest: the money buys an app plus a
first-import convenience, and the day the service stops, every existing
installation keeps working with everything it already has. Sell it the other way
— an app that is inert without a server — and $9 once is a promise that cannot
be kept.

---

## 4. What it cannot do

**`[GDE-CLD-050]` The dataset is frozen, and its coverage decays.** The dump
ends **2022-06-23**. It covers 93.7% of *this* library `[LOG-FEX-055]` because
this library is largely back-catalogue. It covers **nothing released since**, and
its share falls every year without anything being done wrong.

**`[GDE-CLD-055]` And it is worst exactly where a user is most excited.** The
newest four tracks inducted here — Gerardo Frisina, 2026 — came back
**`unmatched` from AcoustID, four of four** `[IMPL-SUI-025]`. Not "no flavor
found": no *identification*, so the lookup never even gets a key to ask with.
New and self-published music is the case the service answers worst, and it is
the case a person adding music today most often has.

**`[GDE-CLD-060]` The honest framing is a back-catalogue accelerator.** It
removes the 22-hour first import `[SPEC-SA-025]` for mainstream libraries and
does nothing for new music. That is genuinely valuable and it is not the same
claim as "you no longer need Vipunen".

---

## 5. Open

1. **`[GDE-CLD-070]` Whether contributed vectors should be accepted.** Desktop
   Vipunen users could return `(recording_mbid → flavor)` — data, not audio, and
   CC0-compatible — which is AcousticBrainz's own model in miniature and would
   halt the decay. Against it: poisoning, and `[SPEC-FD-145]`'s measurement that
   **mixed provenance costs ~8 points of retrieval accuracy**, so contributions
   would have to be segregated by extractor version rather than pooled.
2. **`[GDE-CLD-075]` Whether a subset ships inside the app instead.** At fp16 a
   71-dimension vector is ~142 bytes; a few hundred thousand popular recordings
   would be tens of megabytes and need no server, no account, and no perpetual
   obligation at all. **This may retire the whole question**, and it should be
   sized before anything is hosted.
3. **`[GDE-CLD-080]` Whether the AcoustID dependency is acceptable.** It is a
   registration, a rate limit, and a single point of failure `[SPEC-SA-055]` —
   and it is the step that failed on all four of the newest tracks here.

---

**Traceability:** `[GDE-CLD-010..080]` · derived from `[GDE-IOS-050]`, `[LOG-FEX-055]`, `[SPEC-SA-055]`, `[GDE-ARC-018]`
