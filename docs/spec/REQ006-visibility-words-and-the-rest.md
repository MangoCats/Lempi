# REQ006: Visibility — Words, and What Else the Surface Owes

**Requirements — lyrics, and the remaining visibility obligations**

Split from [REQ002](REQ002-functional-requirements.md) on 2026-09-10, which had reached 1,036 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [REQ002](REQ002-functional-requirements.md) is the index for the functional requirements

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1 in [REQ003](REQ003-audio-playback.md), §3 in [REQ004](REQ004-visibility-provenance.md), [REQ005](REQ005-visibility-listening-surface.md) and [REQ006](REQ006-visibility-words-and-the-rest.md), §2 and §4-8 in [REQ002](REQ002-functional-requirements.md).

**`[REQ-VIS-213]` The MuLibPlay skin shows the words for whatever is
currently playing, when the library has them.** A plain static text block,
exactly as MuLibPlay itself showed them, fetched once per passage change
rather than pushed on every tick `[SPEC-LYR-040]`, `[SPEC-LYR-045]`. Unlike
the three settings below, this asks nothing of a folder or another client's
cache and needs no opt-in — it is always on, and simply absent for a
passage the library has none for, which is the ordinary case for about
72% of them and not an error worth dressing up. The endpoint is
skin-neutral; the other skins may adopt the same panel `[SPEC-LYR-045]`.

**`[REQ-VIS-220]` Lempi may write lyrics beside the audio, and only if asked.**
A persisted setting, **off by default**, the fourth of this kind and the
companion to the one below.

**1,624 single-passage files; the 702 passages inside captures are skipped on
purpose.** A client tries the sidecar *before* its cache, so one written beside
a capture would overrule the per-song words `[REQ-VIS-215]` puts there and show
all twelve songs at once for every one of them. Skipping captures is what keeps
the two settings complementary rather than one undoing the other
`[SPEC-LYR-080]`.

**This is the route that can reach another machine — on one condition.** A
client builds this path from its own music-folder setting, so it works where
that client can read the music folder and not otherwise `[SPEC-LYR-085]`. A file
already there is never replaced, and a second run writes nothing.

**`[REQ-VIS-215]` Lempi may write per-song lyrics into a local client's cache,
and only if asked.** A persisted setting, **off by default**, the third of this
kind.

A sidecar belongs to a file, and a capture is one file holding a dozen songs —
so `<audiofile>.lyrics` can only ever show all twelve at once. A client's own
cache is keyed by **artist and title**, which a cue track has `[SPEC-MPD-056]`,
so writing there gives every passage its own words. 2,235 songs on this library.

**Two things make this a heavier ask than the other two, and both are said on
the settings page rather than only here.** It writes into another application's
data folder, not the listener's music folder. And **the cache is on the machine
the client runs on**: it works when Lempi and the client share a machine, and
does nothing at all when the client is a phone in another room `[SPEC-LYR-075]`.

**A file already there is never replaced**, whatever it holds — a client may
have fetched and saved it, and that was its choice about its own cache. Written
once, a second run reports every song as already current and touches nothing.

**`[REQ-VIS-210]` Lempi may write cover art into the music folder, and only if
asked.** A persisted setting, **off by default**, beside the cue one.

MPD's art is directory-based: a picture embedded in the file, or `cover.jpg` in
the song's folder. **None of the 191 captures here carry embedded art**, so a
guest falls back to its own artist-level lookup and every album by an artist
wears one cover. Lempi already holds the right pictures `[REQ-VIS-170]`; this
puts one where MPD looks.

**Only where the capture is alone in its folder.** A folder has room for exactly
one cover, and 77 captures share theirs with other captures — six in `Various`,
five in `Eagles`. Writing there would give several albums the same picture,
which is the symptom rather than the cure, so those are skipped **and counted**:
a listener is told how many were left and why, not given a number that looks
like a failure.

**A folder that already has a cover is never touched**, under any of the names
MPD looks for and whoever wrote it. A picture already there was somebody's
choice. That also makes the run idempotent: written once, the folder has a cover
and is skipped thereafter, including over Lempi's own.

**`[REQ-VIS-205]` Lempi may write cue sheets into the music folder, and only if
asked.** A persisted setting, **off by default**, on the settings page.

A whole-side capture holds one set of tags, so a guest names every passage
inside it after the file — 34.1% of this library `[SPEC-MPD-052]`. A `.cue`
sheet beside the capture fixes that for every client, because MPD exposes a cue
track as its own song with its own title `[SPEC-MPD-056]`.

**The setting exists because of where the files go.** Nothing else in Lempi
writes into the listener's music folder, and a player that quietly starts doing
so has taken a decision that was not its to take. So it is asked for, it says
what it will do before doing it, and it is off until then.

Three properties the implementation must keep: it is **idempotent** (a sheet
already matching is left alone, so the folder is not rewritten on a whim); it
**never overwrites a sheet Lempi did not write**, since one that was already
there may be why the library is arranged as it is; and **unticking leaves
written sheets alone**, because deleting files from someone's music folder is a
larger act than declining to add more, and is not what unticking a box asked
for.

**`[REQ-VIS-200]` A running server must say which build it is.** The crate
version and the commit it was built from, stamped in at compile time and
published to every skin so any of them can show it.

The question is asked most often when something expected is **missing** — a
control that is not there, a fix that seems not to have landed, an appliance
deployed to twice — which is exactly when "which build am I looking at" is
hardest to answer by any other means, and when asking a person to remember is
least likely to work.

**A tree with uncommitted changes reports `+dirty`.** A hash alone says which
commit the tree was *at*, not what was compiled, and a build from an edited tree
is not that commit. Reporting it as one would be a confident wrong answer of the
kind `[PI3-API-030]` exists to refuse. Absent git is not a failure: the version
stands and the hash reads `unknown`.

**`[REQ-VIS-155]` What the listener sets, the player remembers.** Master volume, skip fade and skip lead survive a restart. They are written the moment a control moves rather than on the resume point's one-second timer: they change when a hand moves them and not otherwise, so saving them on that schedule would be a write per second to record that nothing had happened — and a setting that survives everything except a crash before the next tick is not really saved.

> **Volume already had a column and was never written to it.** The resume row saved position and playing state and quietly left the level behind, so it came back at full scale every start. That had been true since the row existed, and reads as "it persists" from the schema alone.
>
> Values from disk are clamped exactly as values from the network are — a number that has been sitting in a file deserves no more trust than one that just arrived.
>
> Verified across a real restart: −24.5 dB, 6 s fade and 1.2 s lead were set, the player stopped and started, and all three came back unchanged.

**`[REQ-VIS-160]` The listening surface is skinnable, and the skin is the only part that may differ.** A skin is three files — `skin.html`, `skin.css`, `skin.js` — and nothing else. It never opens a socket, never builds a URL, and never carries a copy of a control law.

What makes this possible was already true and merely tangled: **the server's contract is the snapshot and the command endpoints**, and the DOM was only ever one rendering of it. `core.js` holds that contract — the socket and its reconnection, the complete-snapshot dispatch, the command helpers, the shared formatting, and the fader curve `[REQ-AUD-156]`, which is specified rather than decorative and would be three chances to disagree with the engine if each skin carried its own.

| skin | what it is |
|---|---|
| `lempi` | The reference: quiet and typographic. Whatever a new skin needs from `core.js`, this one uses first, so a gap in the contract shows up here. |
| `mulibplay` | MuLibPlay's arrangement, with the colours and metrics taken from the page it actually serves rather than remembered. Stacked station buttons with the live one gold are the programme list; "Autoselect by clock time" is the manual override `[SPEC-DIR-185]`. |
| `winamp` | The awkward case, on purpose: a fixed-width appliance with bevelled chassis, green LCD, a scrolling title and a separate playlist window. It is the proof that the contract survives a skin that is not a document. |

> **The choice is per browser, not per player.** Two people on two phones may want different skins of the same radio, and neither should be able to restyle the other; it lives in `localStorage`, never in the engine. `?skin=` selects and sticks.
>
> **Skins are compiled in** (`include_str!`), so deploying to a Pi stays a copy rather than an install. Adding a skin is a row in `SKINS` and three files; the catalogue is served, so no existing skin needs editing to list a new one. An unknown skin or file is a 404 and nothing can reach outside the binary.
>
> **What the MuLibPlay skin cannot show, it does not invent:** album art, artist and album names, play counts, and the browse-by-artist pages are simply not in the snapshot. The omission is the engine's, not the layout's, and a skin fabricating them would be worse than the gap.
>
> **Verified** by [`build/verify-skins.js`](../../build/verify-skins.js), which also drives the browse page — its alphabet, its narrowing from artist to album to track, album ordering, the verbs refusing while nothing is selected, the selection travelling as one request in listing order, and a failed query reporting rather than rendering as an empty library `[REQ-VIS-180]`, `[REQ-VIS-195]`. For each skin it loads through `core.js`'s own loader into a real DOM and pushes snapshots at it — one with everything in it, one with almost nothing, optionally a live capture. It checks that nothing throws, that the transport is wired, and that dragging the fader to mid-travel posts `−18 dB`, which is the quadratic `[REQ-AUD-156]` confirming itself through the skin. Optional, because the player needs neither node nor jsdom to run: a skip is reported as a skip and never folded into the pass.

**`[REQ-VIS-140]`** Long-running operations report real progress and are interruptible without loss `[REQ-LIB-130]`.

