# SPEC041: Framebuffer UI — Bandwidth, Touch and Deployment

**Specification — the constraints the design has to live inside**

Split from [SPEC036](SPEC036-framebuffer-touch-ui.md) on 2026-09-10, which had reached 568 lines against `[GOV-DOC-010]`'s 300-line limit.

> **Related:** [SPEC036](SPEC036-framebuffer-touch-ui.md) for the design

---

> **Section numbers below are the pre-split document's.** This file was carved out of a larger one on 2026-09-10, and its cross-references still use the original numbering: §1-3 in [SPEC036](SPEC036-framebuffer-touch-ui.md), §4-7 in [SPEC041](SPEC041-framebuffer-constraints.md), §8-9 in [SPEC042](SPEC042-framebuffer-implementation-plan.md).

## 4. The SPI bandwidth budget, stated as a real constraint

**`[SPEC-FBUI-040]` A full-panel redraw is the most expensive thing this
UI can do, and this design avoids doing it more than necessary.** fbtft
over SPI — originally assumed to matter at the bus's rated 32 MHz, but
this design ended up committing to full-panel redraws anyway (below), so
the number that actually governs refresh cost is whatever
`piscreen2r`'s `speed=` is configured to, not a fixed original figure.
**Lowered to 16 MHz on 2026-09-08**, after real vehicle use surfaced
intermittent color corruption (a "white-washed, almost inverted" flicker,
worse on large content changes like a track skip) consistent with SPI
signal-integrity trouble at the higher rate on this board's actual
wiring — confirmed as the fix by direct observation before/after, not
just plausible reasoning. Refresh is visibly slower as a result; nothing
about this UI's own usage pattern (an update every few seconds at most,
never continuous motion) needs the faster rate back.

**Region-limited redraw was the original plan; full-panel redraw is what
was actually built,** once §8 phase 2 measured the real cost of the
alternative: `render()`'s own CPU-side work stayed under 20ms even
redrawing the *entire* 480×320 canvas on every call, an order of
magnitude below anything a person would perceive as lag. Given that,
tracking exactly which sub-regions changed and redrawing only those
would have added real code and real risk (partial-redraw bugs are a
classic source of stale-looking UI) for a saving that never mattered in
practice. What *is* still true from the original plan:

- The position/duration text and progress bar update on a timer — not
  every push (matching the browser's ~500ms cadence is unnecessary here),
  throttled instead to **once per 5 seconds** for a position-only change
  (`differs_ignoring_position`, added once queue/programme/volume state
  gave the diff rule more to ignore); anything else still redraws the
  full panel at once.
- Title/artist/album still only actually *change* on track change — the
  diff rule's job, not a region-redraw's.
- **Album art was the one open question, and is no longer one.** §8
  phase 7 measured it and built it: a full-color bitmap blit adds a
  small, bounded cost to the full-panel redraw this design already pays
  for on every real change, not a new separate expensive operation.

---

## 5. Touch: raw evdev, calibrated by this project, not by X11/libinput

**`[SPEC-FBUI-045]` Reads the XPT2046 driver's raw evdev device directly**
(via the `evdev` crate or hand-rolled `ioctl`/`read` — a well-documented,
simple wire format), rather than depending on X11/libinput's own mature
calibration tooling — because there is no X11 or Wayland compositor in
this design at all; running one just to get its calibration UI would
reintroduce exactly the memory/complexity cost this whole design exists to
avoid.

**`[SPEC-FBUI-050]` A one-time (re-triggerable) calibration routine.**
Displays crosshair targets at known screen coordinates, records the raw
touch event at each, computes a linear affine transform (the standard
resistive-touchscreen calibration algorithm — well-established, not
invented here) from raw ADC space to screen pixels. Stored as a small flat
file, not a database table: this is a single-purpose, host-specific
setting with no relational structure to it, the same reasoning that keeps
`run-local.sh` a flat file rather than a config table.

**`[SPEC-FBUI-055]` Storage location follows the existing A/B/C write-
frequency design, not a new convention.** The calibration file is written
once (at calibration time) and read on every subsequent start — squarely
a C-partition (state) artifact by `[PI-PART-020]`'s own ordering
principle, alongside `listener.db`. Proposed path:
`/var/lempi/touch-calibration.toml`. The binary itself is an A-partition
(system) artifact, deployed and locked down exactly like `lempi` is.

---

## 6. Deployment: mapped onto the existing appliance model, not new

**`[SPEC-FBUI-060]`** A `systemd` unit alongside `lempi.service`,
`Restart=always` matching its pattern, but **not started or enabled until
the hardware bring-up from the parent conversation is independently
confirmed working** — bringing up a service that crash-loops against
hardware nobody has proven responds yet would just be noise obscuring the
actual bring-up signal. Runs as the same `pi` user `lempi` does (needs
`/dev/fb1` and the touch `/dev/input/eventN` device permissions, the usual
`video`/`input` group membership rather than root).

---

## 7. Reviewed for gaps before any code — real ones found

Asked of this design itself, the same discipline `[IMPL002]`'s own review
passes used, before treating this as ready to build:

- ~~**The hardware identity itself is still open**~~ **Resolved
  2026-09-07, per `[SPEC-FBUI-025]`** — was the single largest risk in
  this document when this review was written; struck through rather than
  deleted so this section's own history stays legible.
- **Non-ASCII text is a real gap, not a hypothetical one.** This library
  has real, non-ASCII artist/title names (checked against this project's
  own data, not assumed) — `embedded-graphics`'s bundled fonts are small
  fixed bitmap sets, typically ASCII-only. A v1 that silently drops or
  mangles a non-ASCII title is a real, known-in-advance defect, not an
  edge case to discover later. Needs a decision before `[SPEC-FBUI-030]`
  is implemented: a broader bitmap font (the `u8g2-fonts` crate covers
  much more of Unicode at a similar footprint to `embedded-graphics`'s own
  fonts) is the likely answer, not a real TTF rasterizer, which would
  reintroduce real memory/CPU cost this design is trying to avoid.
- **Reconnection behavior needs its own explicit design, not an
  afterthought.** `lempi` restarting (a crash, an update) must not leave
  this UI showing stale, confidently-wrong state — `[SPEC-FBUI-020]`'s
  diff-based redraw needs an explicit "no connection" state distinct from
  "connected and nothing changed," or a `lempi` restart could leave the
  display showing the last song it played, silently, indefinitely.
- **Touch hit-testing regions are not yet specified concretely** — this
  document describes the visual language and the data flow, not exact
  pixel rectangles for play/pause/skip/seek. That is real remaining design
  work, appropriately deferred until `[SPEC-FBUI-025]`'s hardware
  questions are answered (a hit-test grid designed against the wrong
  orientation or the wrong overlay's rotation convention is wasted work).
- ~~**What happens before calibration has ever run**~~ **Resolved
  2026-09-07** — `fbui` checks for `[SPEC-FBUI-055]`'s file at startup and
  runs `[SPEC-FBUI-050]`'s routine automatically when it's missing; no
  separate manual step exists to forget.

None of these are reasons not to build this — they are exactly the kind
of gap this review step exists to find before code makes them expensive to
fix. Folded into §8's phasing below rather than left as loose ends.

---

