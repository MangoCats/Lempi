# GUIDE007: External Backends — an Investigation

**Development Guidance — measured on `investigate/external-players`, not estimated**

What it would cost to let the Director drive MPD or an OpenSubsonic server, whether that makes the project unwieldy for the uses it already serves, and how cleanly it can be kept out of the way of people who do not want it.

> **Related:** [GUIDE006](GUIDE006-director-as-a-guest.md) posed the question · [`player/src/playback.rs`](../player/src/playback.rs) is the spike · [SPEC009](spec/SPEC009-program-director.md)

> **Outcome, added 2026-09-02:** this investigation's MPD recommendation was accepted and built — see [IMPL004](IMPL004-mpd-prototype.md)/[IMPL005](IMPL005-mpd-prototype-results.md) for the build order and results, [SPEC015](spec/SPEC015-mpd-director.md)/[SPEC018](spec/SPEC018-switching-backends.md)/[SPEC020](spec/SPEC020-the-handoff.md) for the shipped design. `player/Cargo.toml` carries the `mpd` feature gate exactly as proposed below (§6/§7), and `player/src/mpd_backend.rs`, `switch.rs` are merged to `main`. What follows is kept as the reasoning that produced that decision, not as an open proposal — read §4/§6/§7's "if built" / "would" framing as history, not as a live question. OpenSubsonic was not pursued.

---

## 1. The seam already existed

**`[GDE-BAK-010]` Measured, not argued.** Three counts, taken from this tree:

| | |
| :--- | ---: |
| Methods `Session` calls on `Engine` | **7** |
| More that a binary's own loop calls | 3 |
| Occurrences of `path` anywhere under `director/` | **0** — confined to a handful of comments and a test's DDL, not code |
| Lines that turn a queue entry into audio | **1** — `PassageDecoder::open(&e.path, …)` |

Every other use of `entry.path` is an error message, a filename for display, or `relink`, which is a different subsystem.

**`[GDE-BAK-015]` `Engine` satisfies a `Playback` trait with no change to `engine.rs` at all** (`engine.rs` as it was then; split into `engine/{mod,persist}.rs` 2026-09-02, file-organization only, unrelated to this finding). The spike defines the trait, implements it for `Engine` by forwarding to methods that already existed with matching signatures, and **237 tests pass**. Nothing was refactored to make that true; the separation of selection from playback was already real and merely unnamed.

**So the cost of the seam itself is approximately zero.** It has been paid, by whoever kept the Director from reaching for a file path.

---

## 2. What it would actually cost

**`[GDE-BAK-020]`** The trait is not the work. The work is one adapter per protocol, and the mapping problem behind both.

| Piece | Estimate | Notes |
| :--- | :--- | :--- |
| `Playback` trait + `Capabilities` | **done, ~130 lines** | in the spike, tested |
| MPD adapter | few hundred lines | line protocol over TCP; **no new dependency**, `std::net` suffices |
| OpenSubsonic adapter | more | HTTP + JSON; wants an HTTP client, and that is the dependency question below |
| **Passage → server song mapping** | **the real unknown** | see `[GDE-BAK-025]` |
| Vipunen | **nothing** | it writes `lempi.db` and does not care who plays |

**`[GDE-BAK-025]` The mapping is the part with no obvious answer.** Lempi selects a passage in *its* database; MPD names a file by URI relative to *its* music root; OpenSubsonic names a song by an opaque server id. Nothing guarantees the two libraries even contain the same files. Candidate keys — `recording_mbid` where the server exposes it, path suffix, `(artist, title, duration)` — each fail differently, and this is the same class of problem `[SPEC-RLK-025]` already made expensive once. **It should be prototyped before anything else**, because a clean trait over an unreliable mapping is a well-organised way to play the wrong song.

---

## 3. Would it hurt the uses already served?

**`[GDE-BAK-030]` The fidelity question is much bigger than DAO captures, and the measurement says so.** Taken from the live library, 8,330 radio passages:

| | |
| :--- | ---: |
| passages that are the whole file | **116 — 1.4%** |
| passages that are genuine slices | **8,214 — 98.6%** |
| files holding more than one radio passage (DAO) | 191 of 5,709 |

The trim is not a DAO phenomenon. It is on nearly every track, because the Album/Radio duality `[GDE-BMK-030]` is what radio passages *are*. How much it removes, over the 5,518 single-passage files:

| | omitted |
| :--- | ---: |
| median | **6.7 s** |
| p75 | 14.5 s |
| p90 | **61.3 s** |
| p95 | 106.1 s |
| max | 1,976 s |

Only 12.5% of files are within a second of their whole length. **A whole-file backend would put a median of nearly seven seconds of dead air into most tracks**, a minute or more into one in ten. That is not a corner case; it is the listening experience.

**`[GDE-BAK-035]` MPD escapes this and OpenSubsonic does not.** `rangeid {ID} {START:END}` has specified the portion of a song to play since **MPD 0.19**, in fractional seconds. That is Lempi's passage model almost exactly.

| | spans | gain | ramps |
| :--- | :---: | :---: | :---: |
| built-in engine | ✓ | ✓ | ✓ |
| **MPD** | **✓** | ✗ | ✗ |
| OpenSubsonic | ✗ | ✗ | ✗ |

So the two targets are **not comparable**, and GUIDE006 was wrong to rank OpenSubsonic first on reach alone. MPD preserves what the library is built around; OpenSubsonic reaches more clients while discarding it. Reach is worth less than it looked.

**`[GDE-BAK-040]` The capability declaration is what keeps this honest.** `Capabilities::would_misplay` answers, per passage, whether a backend would play something other than what was chosen — and a whole-file backend is *fine* for a passage that covers its file. This is `[PI3-API-030]`'s rule applied to a network: an output that accepts everything and plays the wrong thing is a player lying about what it is doing. A backend that cannot clip should **refuse the passage and say so**, not play forty minutes and hope.

---

## 4. Keeping it out of the way

**`[GDE-BAK-050]` Cargo features, default off, and the appliance carries nothing.** Nothing in this repository is feature-gated today, and this is the case that earns it:

```toml
[features]
default = []
mpd = []                    # std::net only
subsonic = ["dep:ureq"]     # or whatever HTTP client
```

A build without them contains no adapter code, no adapter dependencies, and no larger binary — which matters, because the aarch64 player is **6.8 MB** on a 512 MB appliance where *"every dependency is a memory decision"* `[REQ-HW-140]`.

**`[GDE-BAK-055]` Three further rules keep the blast radius at zero:**

1. **No schema change.** Any passage-to-server mapping lives in a **sidecar**, not `lempi.db` — the precedent already exists twice, in `library.idchecks.db` and `<library>.console.db` `[IMPL-SUI-055]`. A local-only user never grows a table they will not fill `[SPEC-SC-015]`.
2. **The trait is internal.** `Engine` implements it and behaves identically; no local code path changes, and the spike proves it by leaving `engine.rs` (now `engine/`) untouched.
3. **Static dispatch.** Make `Session` generic over `P: Playback` rather than holding a `dyn`, and the local build monomorphises to what it compiles today.

**`[GDE-BAK-060]` What genuinely does get harder.** Honesty about the cost, not just the containment:

- **Two more ways to be wrong in the same report.** Every diagnostic that says "playing" now has a backend behind it, and the appliance's dummy-sink episode is the standing reminder of how that goes wrong `[PI3-FOUND-050]`.
- **The test matrix widens.** A protocol adapter wants a fake server, or it is untested.
- **`take_dropped` becomes ambiguous.** Locally it means a file would not open. Remotely it may mean the server forgot the song, the network went away, or someone else emptied the queue — and rotation bookkeeping `[REQ-PD-112]` treats those identically today.

---

## 5. Who actually runs MPD

**`[GDE-BAK-065]` There is no telemetry, so every figure below is a proxy or a
biased sample, and is labelled as one.** MPD is a daemon installed from distro
repositories. Nobody counts downloads, and the project asks users nothing.

**Debian, live data taken 2026-08-21** from `popcon.debian.org` — 282,629
opted-in submissions, the whole 10.8 MB table of 107,824 packages:

| package | installed | **regular users** | use / install |
| :--- | ---: | ---: | ---: |
| vlc | 43,661 | 142 | 0.3% |
| rhythmbox | 41,427 | 2,074 | 5.0% |
| quodlibet | 25,314 | 1,636 | 6.5% |
| mpv | 21,841 | 5,875 | 26.9% |
| **mpd** | **1,461** | **1,367** | **93.6%** |

**`[GDE-BAK-070]` The install count is the wrong number, and the ratio is the
finding.** Across the 26,052 Debian packages with 200+ installs the **median
use/install ratio is 7.5%**; MPD's 93.6% is higher than **99.5% of them**. VLC
and Rhythmbox arrive with desktop meta-packages and are mostly never used as
anyone's music player. Nobody installs MPD by accident.

Read by *regular users* rather than installs, MPD's 1,367 is **66% of
Rhythmbox's 2,074 — on one twenty-eighth the installs.**

**`[GDE-BAK-075]` The distro skew is 19×, and it points at tinkerers.** Arch's
`pkgstats` puts **mpd at 9.81%** of reporting systems against Debian's 0.52%.
Calibration on the same source: `firefox` 69.81%, `mpdecimal` (a transitive
dependency) 98.93% — so the scale is real. MPD is roughly nineteen times more
prevalent among people who assembled their own system.

**`[GDE-BAK-080]` The largest population is invisible to both.** Volumio, moOde
and RuneAudio — the mainstream Raspberry Pi audiophile distributions — are built
on MPD. Their users never install a package and appear in no package survey.
**That segment is precisely LempiPi's own category**: a small board playing a
local library to a DAC.

**`[GDE-BAK-085]` Development is alive; the last release was eight days ago.**
19,899 commits, 2.7k stars, 422 forks, 158 open issues, 8 open pull requests.
Series cadence: 0.19 (2016), 0.20 (2018), 0.21 (2020), 0.22 (2021), 0.23 (2025),
**0.24 — 13 August 2026**. Around sixty actively maintained clients are listed
across console, web, desktop, Android, Wear OS and **iOS**.

**`[GDE-BAK-090]` "Keeping up with the latest version" is answered by distro
policy, not by users.** There are no download counters, and MPD arrives through
package managers. Arch users are current within days by construction; Debian
stable users run whatever the freeze caught, for years. **The actionable
consequence: target the 0.19 protocol surface.** `rangeid` has been there since
2016 `[GDE-BAK-035]`, so it is in every version anyone is plausibly running, and
depending on anything newer would exclude the stable-distro half of the user
base for years.

**`[GDE-BAK-095]` Absolute scale, stated with its uncertainty.** Popcon captures
an unknown and small fraction of Debian installs, so the counts cannot be
multiplied up responsibly. The defensible statement is: **tens of thousands to
low hundreds of thousands of systems worldwide**, dominated by the embedded and
audiophile segment, with engagement in the top half-percent of all packaged
software.

**And that population is unusually well matched to this project.** GUIDE004 §5
put Lempi's ceiling at people who maintain a large tagged local library and will
run a desktop pipeline `[GDE-IOS-045]`. That is close to a description of the
MPD user: self-selected, deliberate, already running a headless daemon against
files they own. **Small, and almost exactly the right people** — which is worth
more here than reach.

---

## 6. Recommendation

**`[GDE-BAK-100]` If any of it is built, build MPD, and build the mapping first.** MPD keeps the Album/Radio duality, needs no new dependency, and is the smaller adapter. OpenSubsonic buys client reach at the price of the thing 98.6% of this library is shaped by — which is a poor trade for *this* library, whatever it is for someone else's.

**`[GDE-BAK-105]` And nothing needs deciding to keep the option.** The seam is already there and costs nothing to leave named: the trait compiles, `Engine` satisfies it, tests pass. That is the whole of what this branch establishes, and it can sit unused indefinitely without weighing anything down.

---

## 7. Open

1. **`[GDE-BAK-110]` The mapping, prototyped against a real Navidrome or MPD instance.** Nothing else should start first `[GDE-BAK-025]`.
2. **`[GDE-BAK-115]` Whether gain can be carried to MPD at all.** ReplayGain is per-file; Lempi's `gain_db` is per-passage `[SPEC-SC-040]`, and two passages in one file may legitimately differ.
3. **`[GDE-BAK-120]` Whether a remote backend can report plays precisely enough for rotation.** Stage A is the part with six years of tuning behind it `[GDE-PD-010]`, and it is worth less if the history it reads is approximate.

---

**Traceability:** `[GDE-BAK-010..120]` · derived from `[GDE-EXT-020]`, `[GDE-BMK-030]`, `[REQ-HW-140]`, `[PI3-API-030]`
