# SPEC036: A native framebuffer/touch UI, for small SPI displays

**Design Specification — Tier 2 · Built, deployed, and running** — tagged
`testedInTruck`: confirmed functional in a real vehicle install (a 2019
Ram Classic Tradesman's center console, AUX-in to the factory head unit),
not just on a bench.

A second, native UI for Lempi, alongside the existing browser one — not a
replacement for it, and not built the same way. Motivated by a concrete
need: `lempiplay3` (a Raspberry Pi 3B, first built `2026-09-07`) has a
480×320 SPI-driven touchscreen too small and too memory-constrained (1 GB
total, `[REQ-HW-100]`'s player budget already spoken for) to run a kiosk
browser against — checked directly rather than assumed: Chromium's
own well-documented ~512 MB baseline and WPE/cog's real, currently-open
memory-leak issues over multi-day uptime both fail this appliance's actual
usage pattern (continuous, unattended, for weeks). See §1 for that review
in full.

> **Related:** [player/src/web/mod.rs](../../player/src/web/mod.rs) (the
> existing `Snapshot`/`/ws`/`/command` surface this reuses, unchanged) ·
> [REQ002 §6](REQ002-functional-requirements.md#6-appliance--hw) ·
> [PI001 §2](../../LempiPi/PI001-image-and-partitions.md#2-partition-a--system-and-application)
> (the A/B/C write-frequency design this maps onto)

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1-3 in [SPEC036](SPEC036-framebuffer-touch-ui.md), §4-7 in [SPEC041](SPEC041-framebuffer-constraints.md), §8-9 in [SPEC042](SPEC042-framebuffer-implementation-plan.md).

## 1. Why not a browser — the review that led here

Asked directly, before any design work: is a lightweight kiosk browser
(`cog`/WPE WebKit, chosen over Chromium for its lower baseline) actually
going to fit this appliance's 1 GB budget. Checked rather than assumed:

- **Chromium**: real forum reports put its baseline over 512 MB before any
  page content, driven by its fixed multi-process overhead (browser +
  renderer + GPU process) regardless of page complexity. Against `lempi`'s
  own measured ~100 MB and a ~80–100 MB OS baseline, that leaves this
  appliance swapping from the moment it starts. Ruled out.
- **cog/WPE WebKit**: lower baseline in principle, but has **open,
  documented memory-leak issues specific to continuous uptime** — one
  reported case reached 1,300 MB over three days and crashed
  (`Igalia/cog#693`); multiple open upstream issues describe the same
  shape (`WebPlatformForEmbedded/WPEWebKit#1569`). This appliance's whole
  point is running unattended for weeks in a vehicle, which is exactly the
  usage pattern that failure mode targets. Mitigable (a periodic restart
  of just the kiosk process, `WPE_POLL_MAX_MEMORY`/`WPE_RAM_SIZE`), but
  that is patching around a known defect in code this project doesn't
  own, for a component that must not fail unattended.

**`[SPEC-FBUI-005]` Conclusion: build a small native client instead**,
inside this project's own Rust, using `lempi`'s *existing* network API
exactly as a browser skin already does — no new server-side surface, no
new coupling, and the one component most exposed to slow-SPI/small-RAM
constraints is code this project controls rather than a third-party
browser engine's.

---

## 2. Architecture: a client of the existing API, not a new one

**`[SPEC-FBUI-010]` A separate process, not a mode of `lempi` itself.**
Matches the existing pattern of narrowly-scoped bin targets (`tagscan`,
`dircheck`, `station`) rather than growing `lempi`'s own responsibilities.
If this crashes, hangs, or a display fault wedges it, playback in the main
`lempi` process is unaffected — the same "must never stop the music"
discipline `[SPEC-DIR-190]`'s decision-recording and `backup.rs`'s
snapshot already model, applied to the newest, least-proven part of the
system.

**`[SPEC-FBUI-015]` Talks to `lempi` exactly the way a browser skin
does, and nothing more:**

- `GET /ws` — the same `Snapshot` struct, the same 500 ms push, already
  serialized and already tested (`the_snapshot_keeps_the_field_names...`).
  No new server-side field, no new endpoint.
- `POST /command/play|pause|skip`, `POST /volume/:db`, `POST /seek/:ms` —
  the exact routes `control.rs` already serves.
- `GET /art/:passage_id` — for the one piece of bitmap content this UI
  might show (§5's open question on whether it's worth the SPI cost).

This means **zero changes to `lempi`'s own server code** for this whole
feature. The new binary is purely a client of an interface that already
exists, is already tested, and already has other consumers (every browser
skin) exercising the exact same contract. `tokio-tungstenite` is already a
dependency of this workspace (axum's `ws` feature pulls it in) and speaks
both server and client roles — reused, not added.

**`[SPEC-FBUI-020]` One held `Snapshot`, redrawn on diff, not on tick.**
The client holds the last received `Snapshot` and compares it to the
previous one on each `/ws` message; only the regions whose *source fields*
changed are redrawn. This is not an optimization applied after the fact —
it is the whole reason this design is viable at all, given §4's SPI
bandwidth reality.

---

## 3. Display: fbtft framebuffer, RGB565 — now confirmed against real hardware

**`[SPEC-FBUI-025]` Confirmed 2026-09-07, against `lempiplay3` itself,
not assumed.** `dtoverlay=piscreen2r,rotate=90,speed=32000000,fps=20` in
`config.txt`, reboot, then read directly from the device rather than
trusted from documentation:

```
$ cat /sys/class/graphics/fb0/name
fb_ili9486
[   12.312] graphics fb0: fb_ili9486 frame buffer, 480x320, 300 KiB video
            memory, 32 KiB buffer memory, fps=20, spi0.0 at 32 MHz
```

**`/dev/fb0`, not `/dev/fb1`** — this design's own earlier guess was
wrong in the specific but right in the shape: there is no HDMI output
active on this headless build, so the SPI panel became the *first*
framebuffer rather than a second one alongside it. 480×320 landscape,
300 KiB = 480×320×2 bytes — confirms RGB565 (16-bit) exactly as assumed.
`piscreen2r` was the right overlay on the first try; the original
hardware identification (ILI9486 controller, XPT2046/ADS7846-protocol
touch) is now fully validated, not just plausible.

Touch is equally confirmed:

```
$ cat /proc/bus/input/devices
N: Name="ADS7846 Touchscreen"
H: Handlers=mouse0 event2
```

A real IRQ-driven evdev device at `/dev/input/event2`, exactly the
contract `[SPEC-FBUI-045]` assumed. One harmless, well-known fbtft quirk
seen in `dmesg` (`start_line=319 is larger than end_line=0 ... will do
full display update`) — cosmetic, not a defect in this configuration, and
confirmed pre-existing rather than caused by any later phase: `dmesg -T`
timestamps put it at 19:38-19:40 on this boot, over an hour before album
art (§8 phase 7) was ever deployed. Only 7 total log lines exist despite
far more redraws than that having happened since boot, so the kernel is
clearly rate-limiting the *message*, not the underlying fallback -- it
likely fires on every `render()` call, given `render()` already rewrites
virtually the whole panel every time (§8 phase 7's own measurement). Its
visible symptom (observed 2026-09-07, once album art existed to make it
obvious): a brief whole-screen white cast on a large content change like
a track skip, resolving to the correct frame immediately after. Not
noticed on smaller changes (a position-bar tick) because old and new
frames look nearly identical against each other, not because the
underlying driver fallback fires less often for them. A kernel-driver-
level cosmetic artifact, not something fixable from this binary's own
code; `piscreen2r`'s untried `drm`/KMS alternative (next paragraph) is
the only real candidate for eliminating it, and has not been pursued.

This resolves §7's largest named risk. `piscreen2r`'s own KMS/`drm`-mode
alternative remains untried (FBTFT already works, no forcing reason to);
byte-order is now confirmed too, by §8 phase 2's pixel-readback test, but
*perceived* correctness on the physical panel — orientation, color, real
refresh behavior — still wants an actual look, not just measured bytes,
**since confirmed directly**: real green text on a black background, seen
on the physical unit.

**`[SPEC-FBUI-027]` The kernel's own framebuffer console shares this
device, and must be tamed rather than removed.** Found the hard way, not
designed for up front: `/dev/fb0` is also Linux's text console (`fbcon`),
because `cmdline.txt` binds `console=tty1` to it. Removing that binding
looked like the obvious fix for a login-prompt cursor stomping on
`fbui`'s output — instead it hung `lempiplay3` at boot, reproducibly,
three times in a row, with no filesystem corruption on either the ext4
`SYSTEM` partition (`e2fsck`-clean) or the f2fs `STATE` partition (full
read-only check passed, checkpoint recorded a proper `unmount`) to explain
it — something in this image's boot sequence depends on a VT console
existing at all, even an unused one. `console=tty1` stays. The two actual
symptoms it caused are instead solved without touching boot config:

- The login prompt itself: `getty@tty1.service` disabled (`systemctl
  disable --now`) — confirmed durable by finding no unit symlink at all
  under `/etc/systemd/system` after a reboot, not just believing the
  command's own output.
- `fbcon`'s VT cursor, which keeps blinking even with no getty attached
  (a property of the console layer itself, not of whatever reads from
  it): turned off live with `TERM=linux setterm -cursor off > /dev/tty1`,
  made durable via `fbui.service`'s own `ExecStartPre` (a `+`-prefixed
  line, since `/dev/tty1` isn't writable by the `pi` user the rest of the
  unit runs as) — tying the fix to the unit that actually owns the
  display, rather than a separate boot-order-dependent script.

One narrow, cosmetic residual: for the few seconds between `fbcon`
claiming the console during boot and `fbui`'s own first paint, boot text
can in principle still flash on screen once. Self-heals immediately
(`fbui` unconditionally repaints the whole panel on start) and has not
needed chasing further.

**`[SPEC-FBUI-030]` Drawing: `embedded-graphics`, not a from-scratch
rasterizer.** A mature, widely-used Rust crate for exactly this class of
target (small framebuffers, primitive shapes, bitmap fonts), with existing
Linux-framebuffer `DrawTarget` glue in its ecosystem. Pulling it in is a
real new dependency — weighed against writing equivalent rectangle/text/
line primitives by hand, which is not obviously cheaper once font
rendering is included, and is more likely to have an undiscovered bug in
exactly the pixel-format-handling code this design most needs to be
right.

**`[SPEC-FBUI-035]` Visual language borrows directly from an existing web
skin, not from scratch — originally WinAmp, since revised into two
themes, one per page.** Found while grounding this spec, not assumed:
`web/skins/winamp/skin.css`'s chassis is a fixed **320px** wide — which is
not a coincidence to route around but a near-exact match to this panel's
own 320-pixel dimension. Same palette (`#3d3d47` chassis, `#0b0f0b` LCD
background, `#22dd22` LCD green), same idiom (a small LCD readout for
title/artist/position, flat transport buttons) — reimplemented as native
primitives, not by parsing or rendering the CSS itself. This is still
Settings' own theme entirely, and remains the gear icon's on both pages.

**Now Playing re-themed to MuLibPlay on 2026-09-08**, on direct request:
`web/skins/mulibplay/skin.css`'s own real, measured palette (`#000000`
"black ground", `#cccccc` text, `#2010a0` filled buttons, `#d0a000` gold
for whichever programme is active, `#7ab7ff` progress fill) rather than
WinAmp green, once the button grid grew from four transport buttons to
ten (two rows of five: play/pause, skip, all 8 programmes — §9). Filled
and rounded rather than outlined, the same simplification a low-resolution
rendering of that skin needs regardless of this project's own reasons for
it (a thin outline stroke does not read clearly at 480×320): confirmed
directly, not assumed, after an outlined first attempt read as illegible
on the physical panel. The literal hex values from the desktop skin
needed further darkening beyond that for this specific panel's own
real, measured low contrast, and button labels moved to a bold ASCII
font — both again confirmed by looking at the physical screen rather than
trusting the numbers on paper. See §9 for the full account.

---

> **Split on 2026-09-10.** The bandwidth budget, touch and deployment are now
> [SPEC041](SPEC041-framebuffer-constraints.md), and the implementation plan
> [SPEC042](SPEC042-framebuffer-implementation-plan.md).
