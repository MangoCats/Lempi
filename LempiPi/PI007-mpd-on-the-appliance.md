# PI007: MPD on the Appliance

**Measurement — Tier 1 · measured on lempi02w, 2026-08-23**

MPD as Lempi's guest on the hardware `[SPEC-BK-020]`: what it costs to keep
running, which of its three plausible outputs actually reaches the speaker, and
the three ways the handoff reported success while producing silence.

Split from [PI006](PI006-appliance-characterisation.md) once it outgrew a
section there. PI006 is about what the player costs; this is about the guest.

> **Related:** [PI006](PI006-appliance-characterisation.md) for the player's own
> numbers · [PI004](PI004-speaker-operation.md) for the speaker ·
> [SPEC016](../docs/spec/SPEC016-mpd-protocol-findings.md) for the protocol
> findings these settle

---

## 1. Running it at all

**`[PI-CHR-085]` MPD must run as the user that owns the sound.** *(Installed
2026-08-23, MPD 0.23.12.)* PipeWire lives in `pi`'s session with its socket in
`/run/user/1000`; Debian's packaged unit runs MPD as the `mpd` user, which
cannot reach it. MPD would have started, found no output, and played silently
into nothing while reporting itself healthy — the failure `[IMPL-AUD-010]`
describes for a missing device, arrived at by a different road.

A drop-in gives it `User=pi` and the same two environment lines
`lempi.service` already carries. `mpd.socket` is masked: socket activation would
start MPD outside the drop-in and undo all of it.

**What it costs, which settles `[SPEC-BK-060]`:** 100.8 MB resident beside
Lempi's 43.4, leaving 264 MB of 464 free; 242 s to index 5,758 songs from
nothing, and 2.9–4.0 s to start with the database already built. Resident, on
that evidence — an on-demand MPD would pay the index at every switch.

---

## 2. Which output reaches the speaker

**`[PI-CHR-090]` The native-looking output plugin is the one that stays
silent.** *(Measured 2026-08-23 by recording the speaker, not by asking MPD.)*

| plugin | scheduled? | at the speaker |
| :--- | :--- | ---: |
| `pipewire` | **never** — quantum 0 in `pw-top` | RMS 0 |
| `pulse` | yes, quantum 8192, links active | RMS 0 |
| `alsa`, `device "default"` | yes | **RMS 4,119** |
| *(control)* `pipe` to a file | — | RMS 12,138 |

The `pipewire` plugin linked to the sink and was never scheduled, while MPD
reported a live bitrate. The `pulse` plugin *was* scheduled, with links active,
volumes at 1.00, and pacing at exactly 1.0× real time — and delivered nothing.
The `pipe` control shows the samples existed the whole time; two of the three
audio routes simply dropped them.

`default` is PipeWire's ALSA compatibility device rather than the sound card,
which is the point: it is the path Lempi itself has always used, so both players
still mix into one sink rather than contending for hardware `[SPEC-BK-030]`.

**`[PI-CHR-100]` And it is not a free choice: the outputs that sound are the
outputs that die on a seek.** Found when a listener seeked and the music stopped
without advancing. Seven configurations, each on its own MPD, each judged at the
speaker:

| output | sounds | survives a seek |
| :--- | :--- | :--- |
| `alsa` / `default`, forced format | yes | **no** |
| `alsa` / `default`, explicit buffer and period | yes | **no** |
| `alsa` / `default`, no forced format | yes | **no** |
| `alsa` / `default`, `use_mmap yes` | yes | **no** |
| `alsa` / `pipewire` PCM | yes | **no** |
| `pulse`, with and without an explicit sink | **no** | yes |
| `pipewire`, native plugin | **no** | yes |

Perfectly split, and it holds for `next` as well as `seekid` — any command that
cancels the output mid-playback. `state` stays `play`, `elapsed` freezes, the
speaker goes quiet. `pause 1` then `pause 0`, with no delay between them, brings
it back; that is what `[SPEC-MPD-135]` now does.

`mixer_type "software"` was tested as a possible cure and is not one — the wedge
happens with and without it. It was kept for its own reasons `[SPEC-MPD-140]`.

> **`close_on_pause` is not an MPD option**, whatever the internet says: it logs
> `option 'close_on_pause' on line 35 was not recognized` and is ignored. That
> variant was silently testing the baseline again until the log was read.

> **The meter was checked before it was believed.** An RMS reading of zero is
> also what a broken recorder produces. It was validated against known-good
> audio first — Lempi playing, RMS 891 — and `pw-record --target` was found to
> be **ignored**, reading the default monitor regardless of what it was aimed
> at. A probe sink with nothing feeding it read 728. Measurements taken before
> that was known were re-taken.

---

## 3. Three ways a handoff lied

**`[PI-CHR-095]` A restarted MPD left the guest silently inert.** The backend
held one socket for the life of the process, so restarting `mpd` — a package
upgrade, a config change, a crash — killed it, and every command afterwards
wrote into a closed pipe.

Nothing said so. A handoff announced *"6 passage(s) carried"* into an MPD whose
queue was **empty**, and went on announcing it for as long as the connection
stayed dead, because the count was of what the library could *build* and never
of what the backend *took* `[SPEC-BK-047]`.

Worse, `is_shutdown` returned that same lost flag, and `Switching` forwards it
to `lempi`'s main loop — so `apt upgrade mpd` would have stopped the player.
Both fixed `[SPEC-MPD-130]`: the connection is rebuilt on the next poll, and a
dead socket is reported as a shutdown only after MPD has been unreachable far
longer than any restart takes.

**And a third fault, which only the first two were hiding: a seek is not a
start.** `seekid` begins playback from `stop`, so the handoff worked for as long
as MPD was only ever stopped. Restarted mid-session, `restore_paused "yes"`
brings MPD back **paused** — the seek then landed at exactly the right offset
and nothing was heard, with `status` obligingly reporting the position the
switch had asked for. `resume_at` now follows the seek with `pause 0`.

That setting stays: an appliance whose MPD starts playing by itself on boot is
worse than one that needs a click. It is the handoff's business to start what
it is handing to.

**Verified end to end**, both directions, and confirmed at the speaker rather
than from the report:

| | switch reported | MPD said | speaker |
| :--- | :--- | :--- | ---: |
| Lempi → MPD | *"resumed 10.3 s in after 176 ms"* | `play`, 6 queued | RMS **3,784** |
| MPD → Lempi | *"resumed 124.3 s in after 1,042 ms"* | `stop` | RMS **507** |

> **That `cut` was a symptom, not a setting.** The switch away from MPD reported
> `cut` rather than `faded` because `mixer_type "none"` leaves no `setvol` to
> build a fade from `[SPEC-MPD-099]`, and MPD's log filled with `exception: No
> mixer` — six of them per switch, one per fade step. The same missing mixer is
> why a guest client's volume slider did nothing. Both fixed by giving MPD a
> software mixer `[SPEC-MPD-140]`; the switch now reports `faded`, and `setvol
> 40` → `setvol 100` moved the speaker from RMS 964 to 5,847.

Start latency, once the ALSA output was right: **95–128 ms** over five trials
from `seekid` to audible position, median 103 ms — comfortably inside the
1,500 ms the handoff allows before it gives up waiting `[SPEC-BK-065]`. The
`SOUNDING_WAIT_MS` backstop was suspected and cleared by measurement.

> **Twice in one session a player's self-report was believed over a
> measurement, and twice it was wrong** — first audio that turned out not to be
> Lempi at all `[PI-CHR-080]`, then this. Here the switch report and MPD's own
> `state: play` agreed with each other and with nothing audible `[PI3-API-030]`.

> **Track names inside captures need cue sheets, which are off here.** MPD read
> a handed-over passage as `Flora Purim — (no title)`, because a capture carries
> one set of album tags `[SPEC-MPD-052]`. `[REQ-VIS-205]` is the cure and it
> writes into the music folder, so it stays off until asked for.

---

## 4. What was not measured

- **How long the ALSA output survives a Bluetooth dropout.** The speaker was in
  range throughout.
- **MPD's own memory under a long session.** The 100.8 MB figure is from a
  freshly indexed instance; nothing ran for more than an hour.
- **Whether `.wkmp_temp` files should be indexed.** MPD reports 8,379 songs
  against 5,705 library files, which is a discrepancy that has been noticed and
  not yet explained.

---

**Traceability:** `[PI-CHR-085]`, `[PI-CHR-090]`, `[PI-CHR-095]` · settles
`[SPEC-BK-060]` · confirms `[SPEC-BK-047]` and `[SPEC-MPD-130]` · configuration
in [`mpd.conf`](mpd.conf) and [`mpd-override.conf`](mpd-override.conf)
