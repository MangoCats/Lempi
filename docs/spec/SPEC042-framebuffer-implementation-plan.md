# SPEC042: Framebuffer UI — Implementation Plan

**Specification — the phased build, and what lies beyond it**

Split from [SPEC036](SPEC036-framebuffer-touch-ui.md) on 2026-09-10, which had reached 568 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [SPEC036](SPEC036-framebuffer-touch-ui.md) for the design

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1-3 in [SPEC036](SPEC036-framebuffer-touch-ui.md), §4-7 in [SPEC041](SPEC041-framebuffer-constraints.md), §8-9 in [SPEC042](SPEC042-framebuffer-implementation-plan.md).

## 8. Implementation plan, phased against the open items above

1. ~~Hardware bring-up~~ **Done, 2026-09-07** — `/dev/fb0` (`fb_ili9486`,
   480×320, RGB565) and `/dev/input/event2` (`ADS7846 Touchscreen`) both
   confirmed against real hardware, per `[SPEC-FBUI-025]`.
2. ~~A minimal `DrawTarget` + connection test~~ **Done, 2026-09-07** —
   `player/src/bin/fbui.rs` (feature-gated `fbui`, `bose`/`lempi02w` builds
   never compile it), a `ClientSnapshot` naming only the fields this phase
   draws rather than making the real `Snapshot` `Deserialize` too
   (`[SPEC-FBUI-015]`'s "zero server-side changes" held in practice, not
   just on paper). Deployed and run as a real systemd service on
   `lempiplay3` (`After=lempi.service`), stable, not crash-looping.
   Verified past "it didn't crash" — read `/dev/fb0` back after a render
   and counted actual pixel bytes: 496 pixels matching `LCD_GREEN`'s exact
   RGB565 encoding (real text was drawn, not garbage) against ~153,040
   matching `LCD_BG`'s (the whole-screen fill), out of 153,600 total. The
   `embedded-graphics` → RGB565 → byte-order → real-framebuffer pipeline
   works end to end. **Confirmed by looking at the physical screen**, not
   just by byte-level readback: real green text on black, as designed.
   Getting there also surfaced and resolved `[SPEC-FBUI-027]` (the console
   shares this device) — a real boot-hang risk found and fixed along the
   way, not merely a cosmetic finish.
3. ~~Calibration routine~~ **Done, 2026-09-07** — the standard 3-point
   affine solve (`[SPEC-FBUI-050]`, Vidales' algorithm, the same one
   tslib/X11 evtouch use), raw evdev hand-parsed rather than a new crate
   (`[SPEC-FBUI-045]`). The math is unit-tested against a synthetic
   rotated/scaled transform (recovers a held-out 4th point exactly, the
   check the calibration literature itself recommends) independent of any
   hardware, then run for real: three genuine touches on `lempiplay3`
   produced distinct, internally-consistent raw readings (raw_x fell as
   screen_x rose, raw_y rose sharply with screen_y — the axis remix
   `rotate=90` predicts) and a saved `/var/lempi/touch-calibration.toml`
   with finite, sane coefficients. Confirmed re-triggerable (`fbui
   --calibrate`) and confirmed idempotent the other way too — a plain
   restart finds the file and logs `using existing calibration` rather
   than re-prompting.
4. ~~Transport UI~~ **Done, 2026-09-07, mostly confirmed** — four
   buttons (`VOL-`, play/pause, skip, `VOL+`) plus a tap-to-seek position
   bar, hit-tested against concrete pixel regions now that §8.1's
   orientation is known. Wired to the exact three real command names
   `control.rs` serves — checked against the handler itself rather than
   assumed: `play`, `pause`, `skip` (there is deliberately no `prev` or
   `stop`, `[REQ-AUD-142]`) — plus `/volume/:db` (±3dB per tap, computed
   from the last known `volume_db`) and `/seek/:ms`. Posted over a
   hand-rolled HTTP/1.1 request on a raw `TcpStream` rather than a new
   client crate, since `reqwest` is explicitly appliance-excluded
   (`Cargo.toml`'s own `vipunen-support` reasoning) and `hyper`'s client
   builder would be more code than three fire-and-forget local requests
   need. Touch runs on its own OS thread, joined with the websocket
   stream via `tokio::select!` — physically confirmed working: skip,
   volume, and play/pause all produced their real effect when tapped on
   `lempiplay3`. **Seek is not yet confirmed** — this library is 31
   radio passages, all `duration_ms == 0`, and the code correctly declines
   to send a seek for those (`if duration_ms > 0`), so a tap there is
   correctly inert but untested against real seekable content. Left open
   rather than claimed.
5. ~~Reconnection handling~~ **Done, 2026-09-07** — `render_disconnected`
   replaces `render`'s old "connect before first try" placeholder (which
   only covered the first-ever connection, not a later drop) and now fires
   on every disconnect, with `last` reset to `None` alongside it. That
   reset is the actual fix, not the message alone: without it, a `lempi`
   restart that happened to come back with an identical snapshot would
   fail `[SPEC-FBUI-020]`'s diff check and leave the disconnected screen
   up forever despite real data flowing again. Tested exactly as planned
   — `systemctl restart lempi.service` mid-session on `lempiplay3` — and
   confirmed by framebuffer readback, not just log lines: the green-pixel
   count dropped from 7,186 (full transport UI) to 508 (the two-line
   disconnected message, no buttons) within one push interval of the
   restart, then returned to 7,287 within `fbui`'s own 3-second retry once
   `lempi` was back.
6. ~~Non-ASCII font decision~~ **Done, 2026-09-07** — resolved with real
   library data, not a synthetic test string: queried a production
   `lempi.db` for actual non-ASCII artist/title text and found accented
   Latin (`Björk`, `Mötley Crüe`, `Fernando Mendonça`) far outnumbered by
   curly quotes/apostrophes and Unicode dashes (`Guns N'Roses`, `The
   Go‐Go's`) — checked, not assumed. `u8g2_font_9x15_t_symbols`
   (`[SPEC-FBUI-030]`, revised) covers the first group directly, confirmed
   by actually calling `render()` against it rather than trusting the
   font's name; `normalize_for_display` maps the second, measured group to
   plain ASCII. Both paths pinned down as a permanent regression test
   (`tests::font_coverage_matches_real_library_data`) and confirmed live:
   a real production title/artist pair temporarily substituted into
   `lempiplay3`'s currently-playing passage, rendered with no errors in
   `fbui`'s log, and visually confirmed legible on the physical screen —
   not just proven not to crash.
7. ~~Album art~~ **Done, 2026-09-07.** The measurement came first: a raw
   write of a full 307,200-byte frame into `/dev/fb0`'s mmap takes ~0.09ms
   (negligible), `render()`'s own CPU-side cost stayed under 20ms across
   every real redraw during normal playback, and the SPI transfer itself
   is governed by `piscreen2r`'s already-configured `fps=20` (~50ms/frame)
   independent of this binary — since `render()` already touches virtually
   every pixel on every call, a thumbnail adds a small bounded cost to an
   already-paid full-panel redraw, not a new expensive operation. Built on
   that basis: `image` (default features off, `jpeg`+`png` only — real
   production art is never anything else, per `media_type_for` in
   `tags.rs`) decodes whatever `GET /art/:passage_id` returns, resized
   once per passage change to a fixed 100x100 buffer (doubled from an
   initial 50x50 after seeing it on the physical screen), blitted via
   `FbDisplay::put_pixel` directly. `passage_id` added to `ClientSnapshot`
   for exactly the purpose the real `Snapshot` already documents it for.
   Fetching uses a second hand-rolled client, `http_get` (a
   `Connection: close` GET, read to EOF — simpler than parsing
   `Content-Length` for a loopback request to a server this project
   controls), keyed and cached per `passage_id` so a passage confirmed to
   have no art (most of this all-radio sample library) is not re-requested
   on every push. Confirmed against real hardware, not just compiled: a
   real 29KB JPEG fetched, decoded, and resized in 32.6ms on
   `lempiplay3`; 1,373 distinct colors read back from the 100x100 art
   region (real photo content, not a solid fill); and confirmed visually
   on the physical screen twice, once at each size.
   One real, pre-existing cosmetic artifact this made newly *visible*
   without being its cause, run down before deciding not to pursue it
   further: `dmesg -T` shows the well-known
   `fbtft`/`fb_ili9486` `start_line=319 is larger than end_line=0` full-
   display-update fallback (`[SPEC-FBUI-025]`'s own §3 already named it
   "cosmetic") firing at 19:38-19:40 on this boot -- over an hour before
   album art was ever deployed, so it predates and is not caused by this
   phase. It likely fires on every `render()` call (only 7 log lines exist
   total despite far more redraws than that, so the kernel is rate-
   limiting the *message*, not the fallback), but only became visible to
   the eye -- a brief whole-screen white cast on a large content change
   like a skip, resolving immediately after -- once album art gave a track
   change a large, high-contrast frame to visibly differ from. Not pursued
   further per explicit instruction; `piscreen2r`'s untried `drm`/KMS mode
   remains the one real candidate for eliminating it, should it matter later.

Phases 1–7 done and verified against real hardware, per each entry above.
This document's original seven-phase plan is complete; §9 covers real
work that came after it.

---

## 9. Beyond the seven-phase plan

The original plan closed with a single-page, WinAmp-themed transport UI.
Real use — including the vehicle install itself — asked for more, all
done and verified against real `lempiplay3` hardware between
2026-09-07 and 2026-09-08:

- **Two pages, not one.** A gear icon (top-right, both pages, drawn last
  so its own backdrop always wins over whatever text runs underneath it)
  toggles between Now Playing and a new Settings page. Splitting was
  necessary, not stylistic: volume control, all 8 programmes, and
  per-track queue editing do not fit one 480×320 screen alongside album
  art and transport controls.
- **A 2×5 button grid**: play/pause, skip, then all 8 of `SPEC009`'s own
  programmes, the currently active one highlighted (a filled gold button
  rather than an outline — "which programme is active" has no other
  indicator on this screen). Never sends `auto`: this appliance has
  no realtime clock and a truck's driving hours have no relationship to a
  schedule tuned for home listening, so only a specific programme id is
  ever posted to `/program/:id`. Persistence for this was a real,
  necessary backend fix, not a UI-only change — `[SPEC-DIR-185]`,
  `SPEC009` §6.
- **Settings**: volume +/− (moved off Now Playing to make room for the
  programme grid) and the upcoming queue, each entry with the same
  sooner/remove/later actions `/queue/:qid/:action` already serves every
  browser skin — found already built and reused, not a new server-side
  route.
- **Album name.** A third line under title/artist, from the real
  `Snapshot`'s own `album` field, blank rather than a placeholder string
  when unknown.
- **MuLibPlay re-theme, contrast, and weight** — `[SPEC-FBUI-035]` above
  has the full account: darker button fill and progress-bar track than
  the desktop skin's own literal values, a bold ASCII font for button
  labels (`Cool`, `Fun`, `Mellow`, `Soft`, `Prog`, `Light`, `Loud`,
  `Groove` — `lempiplay3`'s own real programme names, not synthetic
  ones), both fixes made only after the first attempt was reported too
  low-contrast to read on the physical panel.
- **Album art's resize filter was a real, found bug, not a style
  choice.** `image`'s `FilterType::Triangle` blends source pixels in
  their stored gamma-encoded (sRGB) values rather than in linear light,
  which systematically biases a large downscale brighter than correct —
  reported as art looking "washed out, too white." Real production art
  is confirmed plain JPEG (ruling out a PNG-alpha explanation), and this
  UI's own flat-filled colors are never interpolated at all, so a
  resize-blending bias was the one thing left that explained it
  happening only on album art. Switched to `FilterType::Nearest`, which
  picks one source pixel untouched per output pixel and so cannot be
  biased by a blend that never happens — a real fix, not a full
  correct-and-proper linear-light resize, but the right-sized one for a
  100×100 thumbnail on a panel this coarse. A residual, panel-hardware
  viewing-angle washout remains and is not a software defect.
- **`/dev/fb0` and `/dev/input/event2` are not fixed paths anymore.**
  Disabling `vc4-kms-v3d` for boot speed (`[REQ-HW-100]`'s audio-first
  priority, next bullet) removed the hand-off that used to keep the
  legacy firmware framebuffer (`BCM2708 FB`, 32bpp) from ever claiming a
  device node at all — it now claims `fb0` first on boot, bumping the
  real panel to `fb1`, and evdev's own enumeration order shifted the
  touchscreen from `event2` to `event0` the same way. A fixed path had
  already broken once from a config change this file had no part in;
  `FbDisplay::find_panel_path`/`find_touch_path` now find both by their
  real driver/device name (`fb_ili9486`, `ADS7846 Touchscreen`) instead,
  surviving the next such shift rather than breaking on it again.
- **Boot-to-audio time, halved.** Prompted directly: "is lempiplay3's
  boot sequence optimized to reach audio playout as soon as possible,"
  answered honestly (no) and then fixed, in priority order the appliance
  actually needs (audio, then touch display, then network; HDMI never).
  Real measurement, not estimation: ~28.5s power-on-to-audio before,
  ~12.4s after. Two independent fixes, not one:
  - **OS boot**: `cloud-init` (its one-time job long done, still costing
    5+ seconds every boot for nothing) and Bluetooth (unused on this
    install) disabled; `vc4-kms-v3d` disabled (HDMI never used, and it
    was actively probing for a display every boot regardless).
    `fsck.repair=yes` was deliberately left alone — trading this
    appliance's own power-interruption protection for boot speed is not
    a trade worth making.
  - **`lempi`'s own startup**: `Session::open()` was loading the Program
    Director's full flavor index (578,523 rows) synchronously before
    playback could resume, measured at ~13-15s, even though resuming an
    already-known passage needs no selection at all. Moved onto the same
    background-rebuild machinery `[SPEC-DIR-185]`'s persistence fix
    already reuses for a live `/library/reload` — including its existing
    SD-card-contention-aware queue-depth gate, not a new one invented for
    this. Selection runs on the pre-existing frequency-only fallback
    (the same one a library with no Director tables at all already used)
    for the few seconds until the background build lands.

