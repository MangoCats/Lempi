# LOG013: The Android Spike

**Experiment Record — 2026-09-26**

The device spike `[GDE-APP-020]`, `[REQ-AND-530]`: does Lempi's own player run
on a phone, audibly, with the screen off, under its skin in a WebView? Run on
the maintainer's **moto g power (2021)**: Android 11 (API 30, the
`[REQ-AND-100]` floor), arm64-v8a, 3.8 GB RAM, security patch 2023-02-01
`[REQ-AND-540]`. The code is in `android/` and `player/android/`, and the
scripts that set the phone up and took these readings are in `android/spike/`.

> **Related:** [REQ007](spec/REQ007-android.md) · [GUIDE034](GUIDE034-android-decisions-before-requirements.md) · [GUIDE033](GUIDE033-the-player-without-an-appliance.md) · `android/README.md`

---

## 1. What ran

**`[LOG-SPK-010]` The player unchanged, in a foreground service.** A `cdylib`
with three hand-written JNI entries (init, start, stop) calls `Player::start`.
It is built without `appliance` or `echo-client`, serves on loopback behind a
launch key, and writes nothing beside the audio. A Java foreground service of
type mediaPlayback holds it and a partial wake lock, and an activity shows the
lempi skin from `127.0.0.1` in a WebView. The catalogue was 47 tracks from four
albums, ingested by Vipunen's own tools, with the audio in shared storage at
`/storage/emulated/0/Music/Lempi/`.

## 2. Results

**`[LOG-SPK-020]` It plays, and shared music opens by path.** The first
passage played was *Lovely Rita*, read by `File::open` from a folder named
*Sgt. Pepper’s Lonely Hearts Club Band*, curly apostrophe included, with the
storage permission granted. That answers `[REQ-AND-920]`: on Android 11, the
audio-source resolver is not needed for audio.

**`[LOG-SPK-030]` 37.8 minutes on battery with the screen off, and no
underruns in it.** From 11:54:12 to 12:32:02 UTC the counter read 24,576
samples at both ends, with 0 output recoveries, and the maintainer confirmed
the music was still audible. The screen was on only at the start, before it
timed out, and for the reconnections at the end.

**`[LOG-SPK-040]` But every start underruns.** Each launch accumulated 8,192
to 24,576 samples before settling, and the log's two echo-anchor lines name
`Underrun` as the cause. It is short and it stops, but it is audible as a
glitch at the start of the first passage, so it is not zero.

**`[LOG-SPK-050]` Drain: about 3.2–4.8% of the battery per hour — an upper
bound.** Battery level went from 100% to 97%, and batterystats reported an
actual drain of 100–151 mAh against 5,018 mAh capacity over the 37.8 minutes.
That includes the screen-on minutes. The phone's charge counter never moved
from 4,767,100, so on this phone it cannot be used; a longer run would narrow
the figure. At the upper bound that is about 20 hours of play, which the
maintainer judged acceptable, even good, for a five-year-old battery
`[REQ-AND-910]`.

**`[LOG-SPK-060]` The player is cheap; the skin is not.** Per-thread CPU,
measured from `/proc` ticks over 20–30 second windows:

| state | player (engine + callback + web) | WebView (renderer, GPU, UI, IO) |
| :--- | ---: | ---: |
| playing, screen on | 2.7% | ~130% |
| paused, screen on | 1.3% | ~30% |
| playing, screen off, WebView alive | 7.3% | **87%** (renderer alone) |
| playing, screen off, WebView destroyed | 6.0–7.1% | 0.1% |

With the screen off the renderer ran *hotter* than with it on: nothing was
painting, yet the script still handled a snapshot every 500 ms. The app now
destroys its WebView when the activity stops. How much the skin's `render()`
does per snapshot is a question for the skin, and not only on phones
`[LOG-SPK-900]`.

**`[LOG-SPK-070]` The rest.**
- Session open took 24–89 ms, and a Director rebuild over 47 passages 7 ms.
- AAudio opened "Default Device @ 44100 Hz, 2 ch", with **hardware**
  timestamps.
- Memory was 118 MB PSS.
- The library links only Android system libraries, and the stripped `.so` is
  6.9 MB, the APK 9.5 MB.

## 3. What the spike found in the tree

**`[LOG-SPK-080]` `sql/schema.sql` had no fade columns.** A library built from
it could not refill its queue, and the refill logged 1,724 identical errors in
about 20 seconds. Both are fixed, with a test that builds a library from the
schema file.

**`[LOG-SPK-090]` Wireless debugging on this phone is fragile.** It switched
itself off during every long transfer (a 350 MB music copy never finished; a
10 MB install worked once and failed once), whenever the screen was off, and
after `adb tcpip`. Each restart moves its port. Bulk transfers want a USB
cable.

## 4. Open

1. **`[LOG-SPK-900]` The skin's cost per snapshot** `[LOG-SPK-060]`: about 130%
   of this phone's core to show a playing passage. Any phone browsing an
   appliance's UI pays the same.
2. **`[LOG-SPK-910]` The start-up underrun** `[LOG-SPK-040]`: whether the ring
   is primed before the stream starts, and whether AAudio's first callbacks
   ask for more than the default period.
3. **`[LOG-SPK-920]` "The skin controlling it"** `[REQ-AND-530]`. *Closed
   2026-09-26:* the maintainer confirmed by hand, in the WebView, that pause,
   play, skip, volume and seek all work. With it the spike passes.

---

**Traceability:** `[LOG-SPK-010..920]` · evidence for `[REQ-AND-530]`, `[REQ-AND-920]`, `[REQ-AND-910]` · the device of `[REQ-AND-540]`
