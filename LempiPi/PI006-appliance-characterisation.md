# PI006: What the Appliance Actually Costs

**Measurement — Tier 1 · measured on lempi02w, 2026-08-23**

The player as it runs on the hardware it is for: what it uses, what it can be
asked for, and the one thing it does badly. Every number here was taken from
the appliance itself rather than inferred from the development machine, which
has eight times the memory and no thermal limit worth the name.

> **Related:** [PI001](PI001-image-and-partitions.md) for the machine ·
> [PI005](PI005-appliance-library.md) for the library on it ·
> [IMPL001](IMPL001-appliance-setup.md) for how it was built

---

## 1. The machine and the build

| | |
| :--- | :--- |
| Hardware | Raspberry Pi, 4 cores, **464 MB RAM**, 511 MB swap |
| OS | Linux 6.12.93 aarch64, Raspberry Pi OS |
| Storage | 235 GB, 22% used; library 1.07 GB |
| Build under test | `lempi 0.1.0 (b8bb2e29b94e)`, cross-compiled aarch64, 7.4 MB |
| Library | 8,330 radio passages, 5,705 files |

**`[PI-CHR-010]` The binary now says which source it is, and it is believed.**
The previous build predated `[REQ-VIS-200]` and answered nothing; `--version`
against it started a *second player* instead, because it read the flag as a
database path. The deployed build answers `lempi 0.1.0 (b8bb2e29b94e)`, matching
the development machine exactly.

**`[PI-CHR-015]` It did not match an hour earlier, and the reason was the
cross-build.** The appliance binary is compiled in a Linux container against a
bind-mounted Windows checkout. The worktree there is CRLF and the container's
git has no `autocrlf` to undo it, so `git status --porcelain` reported **104
phantom modifications** and every appliance build stamped `+dirty` however clean
the tree was.

A stamp that always says `+dirty` says nothing — and it was saying it on the one
machine where nobody can check by looking, which is the whole reason
`[REQ-VIS-200]` exists. `build.rs` now asks `git diff --quiet --ignore-cr-at-eol
HEAD`, which agrees with the host. **Found by deploying**: it is not visible from
the development machine at all.

---

## 2. Starting

Taken from the journal, to the millisecond:

| stage | at | cost |
| :--- | ---: | ---: |
| service started | 0.000 s | |
| binary announces itself | 0.014 s | **14 ms** |
| library open, 8,330 passages counted | 11.730 s | **11.7 s** |
| audio device open | 11.888 s | 158 ms |
| web UI listening | 15.292 s | 3.4 s |

**`[PI-CHR-020]` Fifteen seconds to serving, and eleven of them are the
library.** Opening a 1.07 GB SQLite file and counting its radio passages
dominates everything else; the Director's build is the 3.4 s after it. The
deploy script's 90-second health deadline is therefore about six times the
observed need, which is the right side to err on for a machine that is only
reachable through the thing being started.

---

## 3. Resting and playing

Sampled from `/proc` deltas, not `ps %cpu` — that reports the average since the
process began, so it charges the eleven-second library load against every
reading for the rest of the day, and reported 20% for an idle player.

| state | CPU (median) | RSS | temp |
| :--- | ---: | ---: | ---: |
| paused | **1.0%** | 36 MB | 44 °C |
| playing | **4.2%** | 53 MB | 44 °C |
| passage change | 8.6% peak | 53 MB | 44 °C |
| four seeks in ten seconds | 9% median, **37% peak** | 53 MB | 45 °C |

**`[PI-CHR-030]` One core, loafing.** Four per cent of one of four cores to
decode, resample, mix and serve. Twelve threads throughout.

**`[PI-CHR-035]` A seek costs a spike and nothing after it.** 37% for the
moment of a seek — a file open, a decoder seek and a resampler build, which is
exactly what `[REQ-VIS-225]` says it buys — falling back to 4% immediately.
Nothing accumulates: four seeks in ten seconds left the median at 4%.

**`[PI-CHR-040]` Memory is flat.** 52.9 MB to 53.0 MB across five minutes and a
passage change. The machine has 263 MB available with the player running, and
13 MB of swap in use out of 511. Nothing here is close to the edge — which
matters for `[SPEC-BK-060]`, where the open question is whether MPD can be
resident beside it.

---

## 4. Listening

**`[PI-CHR-050]` Zero underruns in five minutes of continuous playback**,
including one passage change. Lock failures: one at startup, none after.

```
  min   cpu%   rss MB   temp  underruns  passage
  0.3    4.2     52.9   45.1          0     4536
  ...
  3.3    8.6     53.0   44.0          0     8730   <- crossfade
  ...
  5.0    4.2     53.0   43.5          0     8730
```

Temperature held between 43.5 °C and 45.1 °C and `get_throttled` stayed
`0x0` — no throttling of any kind, at any point, including during the browse
work below.

---

## 5. The one thing it does badly

**`[PI-CHR-060]` `/browse/albums` takes 25.7 seconds.** Measured over HTTP on
the appliance, for 54 KB of reply:

| endpoint | time | payload |
| :--- | ---: | ---: |
| `/` | 7 ms | |
| `/skins` | 5 ms | |
| `POST /command/skip` | 4 ms | |
| `POST /seek/60000` | 5 ms | |
| `/browse/artists` | 835 ms | 30 KB |
| `/browse/tracks` | 8.2 s | 266 KB |
| **`/browse/albums`** | **25.7 s** | 54 KB |

Thirty times slower than tracks for a fifth of the data, so it is not the
volume. **It is the sort inside the correlated subquery**, measured directly
against the library:

| the same query | time |
| :--- | ---: |
| as the player runs it | **18.05 s** |
| with the inner `ORDER BY` removed | **0.70 s** |

The `ORDER BY` is not removable — without it the answer is wrong, 1,698 albums
instead of 694, because it picks an arbitrary release rather than the chosen
one. The cost is structural: `ALBUM_EXPR` runs once per passage, and
`release_recordings` holds **304,334 rows** for 8,067 recordings — some
thirty-six releases per recording, every reissue and compilation among them —
each needing a join into 61,563 `releases` and a three-column sort.

**`[PI-CHR-065]` A covering index removes nineteen twentieths of it.**

```sql
CREATE INDEX ON release_recordings(mbid, chosen DESC, release_mbid);
```

| | time |
| :--- | ---: |
| without | 18.05 s |
| with | **0.93 s** |
| index build, once | 4.4 s |

Same 694 albums from the probe query, with the index and without it. *(That
694 is the releases-backed count; the endpoint answers 708, because it falls
back to the file's own tag where a passage has no release. Both are right for
what they count.)*

> **This is `[REQ-LIB-165]`'s shape again** — a query that was fine against a
> test library and is not fine against a real one. The development machine
> hides it: the same page there is quick enough that nobody would look.

**`[PI-CHR-067]` Shipped, and measured again afterwards.** The index is created
in the player's open path, where `chosen` is guaranteed to exist, and the
single-column index it supersedes is dropped after it rather than before, so
there is never a start with neither.

| | before | after, cold | after, warm |
| :--- | ---: | ---: | ---: |
| `/browse/albums` | 25.7 s | 4.9 s | **1.4 s** |
| `/browse/tracks` | 8.2 s | 2.1 s | |
| `/browse/artists` | 835 ms | 825 ms | |

Artists is unchanged because it never touched that table, which is the check
that the improvement is the one intended rather than a warmer cache.

**The build costs one start and no more.** The first start after deploying took
**23.0 s** instead of 15.3, the extra being the index; the next was **13.6 s**,
which is quicker than either. Steady state is a page eighteen times faster for a
cost paid once.

---

## 6. Over Bluetooth

Middleton (`20:64:DE:CF:F3:AD`) attached as a bonded, trusted device — a
reconnect rather than a pairing — and PipeWire routed the player's stream to it:
`PipeWire ALSA [lempi] → MIDDLETON:playback_FL/FR`, both active.

**`[PI-CHR-070]` The wireless hop costs nothing measurable.** Two minutes of
undisturbed playback including a crossfade: **zero underruns**, CPU 4.3% median
and 6.5% at the crossfade, 42.9 °C — indistinguishable from the wired figures in
§3. A2DP encoding and the radio link are free at this scale.

**`[PI-CHR-075]` A skip loses about a second and a quarter of audio, roughly one
time in seven.** The finding the undisturbed soaks could not have made, because
neither skipped anything.

| what a listener does | lost audio |
| :--- | ---: |
| seek | **0 of 7** |
| skip | **1 of 7**, and then 1,297 ms |

Two independent runs agree on the size: 110,320 interleaved samples in one
(1,251 ms at 44.1 kHz stereo) and 1,297 ms in the other. It is intermittent, not
progressive — once it happens, the count stays where it is for the rest of the
run. **Seeks never did it**, which is the useful half of the result: the path
that rebuilds a decoder on demand `[REQ-VIS-225]` is not the path that drops
audio, so the cause is somewhere in what `skip` does differently — most likely
whether the passage being skipped *to* was the one already prepared.

Not chased further here. It is a real audible defect and it deserves its own
sitting.

---

## 7. An operational lesson that cost an hour

**`[PI-CHR-080]` A Bluetooth speaker can be taken by another device, and the
appliance cannot see it happen.** Mid-measurement the listener heard content
that was not in the library at all — *Hey Jude*, which this library has **zero**
recordings of, and talk programming, which the captures do not contain (their
passages claim 243 of 244 minutes).

Everything on the Pi looked correct throughout: the player was playing Genesis,
advancing steadily, and its stream was the only one on the sink. It was correct.
Middleton supports multipoint, another paired device had taken it, and
`bluetoothctl` on the Pi only ever sees **its own** link — so no command run
here could have shown it.

> Worth knowing before diagnosing an appliance that "plays the wrong thing":
> confirm the speaker is listening to *this* machine before suspecting the
> library. Pausing the player for twenty seconds settles it in one step.

---

## 8. MPD, installed

Measured, and then split out: MPD grew its own subject once the output
plugin and the handoff both turned out to be wrong in ways worth recording.
See **[PI007](PI007-mpd-on-the-appliance.md)** for what it costs
`[PI-CHR-085]`, which of its three outputs actually reaches the speaker
`[PI-CHR-090]`, and the two ways a handoff reported success while producing
silence `[PI-CHR-095]`.

---

## 9. What was not measured

- **Nothing ran for longer than eight minutes.** Memory is flat over that
  window; a leak with a slower period would not have shown.
- **The skip dropout's cause** `[PI-CHR-075]`, which is measured but not
  diagnosed.

> **A soak that presses skip is not a read-only measurement.** The disturbance
> runs above wrote **ten skip rejections** into `listener_rejections`, each
> suppressing its recording for 156 hours `[SPEC-PLAY-050]` — a script on a
> fixed timer standing in for a listener declining a song, which none of them
> were. Left in the record this time, by the listener's decision. A future run
> should either work against a copy of the library or undo its own suppressions,
> because the alternative is remembering, and this one was not noticed until
> somebody asked whether the appliance was in a normal state.

---

**Traceability:** `[PI-CHR-010..080]` · confirms `[REQ-VIS-200]`'s stamp and
`[REQ-VIS-225]`'s cost · re-finds `[REQ-LIB-165]` · MPD split to
[PI007](PI007-mpd-on-the-appliance.md), which settles `[SPEC-BK-060]`
