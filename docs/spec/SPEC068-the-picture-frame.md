# SPEC068: The Picture Frame

**Design Specification — Tier 2 · written 2026-10-29, at the maintainer's request, for `lrhtpc` first · built 2026-10-29 (`17fffb3`, `9099338`)**

A player whose machine has a screen of its own can show a picture frame on it:
the pictures in a folder, full screen and over everything else, one a minute,
with the time in the corner. It is switched on and off from the Lempi skin's
Settings panel, and it is off until someone switches it on. The music plays
through the same output either way; the frame shows pictures and nothing else.

It is not the fourth skin of ROADMAP's open question 2, a page a wall tablet
opens to show the music: that is a page a browser comes to, where this is the
player putting a window on its own machine's screen. The page this one serves
could become the start of that skin; nothing here depends on it.

> **Related:** [ROADMAP](../ROADMAP.md) §3, question 2 (the fourth skin) ·
> [SPEC036](SPEC036-framebuffer-touch-ui.md) (an appliance's own screen, which
> a Pi keeps) · `[SPEC-SC-099]` (settings, one row each) · [LRH001](../../LRHTPC/LRH001-the-htpc.md)
> (the machine it is for)

---

## 1. What it shows

**`[SPEC-PIC-010]` One picture at a time, filling the screen.** Scaled to cover
the whole screen with its proportions kept, and cropped where its shape is not
the screen's: no bars at the sides or the top. Rotated as its EXIF orientation
says.

**`[SPEC-PIC-020]` A new picture each minute, at the minute.** It changes when
the clock does, at :00 seconds, with a one-second crossfade. The next one is
loaded and decoded before it is due, so a change never shows a blank screen.
For now it is chosen at random, and never the one just shown.

**`[SPEC-PIC-030]` The time, in the lower right corner.** Hours and minutes on
a 12-hour clock, `h:mm`: `9:05`, `12:40`, with no leading zero and no AM or PM.
Large and bold: its digits 15% of the screen's height by default, a share a
person sets on the Settings panel beside the switch `[SPEC-PIC-200]`, from 5%
to 40%. Set in from the corner by a third of its own height.

**`[SPEC-PIC-040]` In black or in white, whichever stands out more against
that picture.** For each picture, the page measures the part of it that lies
under the clock, as displayed (after the crop), and takes its mean relative
luminance *L*. White against it has a contrast ratio of 1.05 / (*L* + 0.05),
and black (*L* + 0.05) / 0.05. The clock takes the colour with the higher
ratio, so white wherever *L* < 0.179. One colour per picture, decided before
the picture is shown. No outline or shadow.

**`[SPEC-PIC-050]` The pictures are a folder's**, which a person names on the
Settings panel. The default is the user's own Pictures folder: `~/Pictures`, or
`%USERPROFILE%\Pictures` on Windows. Its subfolders are included. JPEG, PNG,
WebP and GIF are shown; anything else is left out. The folder is read again
each hour and whenever it is changed, so a picture added appears without a
restart. A folder that is missing or holds no pictures shows the clock on
black, with one line saying so, and the panel says it too: an empty frame is
never a silent one.

**`[SPEC-PIC-060]` The screen is kept on while the frame shows.** A slideshow
is not input, so anything that blanks an idle screen would blank the frame.
Read on lrhtpc 2026-10-29: DPMS's timers set at 600 s; at 23:05, DPMS itself
**disabled** and the screen saver's timeout 0, so nothing blanked it that
evening. The first reading, which said "after 600 s", saw the timers and not
whether DPMS was on. A desktop's power manager can turn it on, so the frame
holds the screen anyway. The page asks the browser for a screen wake lock,
and, as built, the player also turns X's screen saver and DPMS off whenever
the frame shows (`xset s off`, `xset -dpms`), rather than waiting to find the
wake lock insufficient. It puts back the values it read beforehand when the
frame is hidden. It keeps them on disk too, so a player that dies first has
them put back by the next one at its start.

## 2. How it reaches the screen

**`[SPEC-PIC-100]` A page the player serves, shown by a browser the player
starts.** A desktop machine is not a Pi: X or Windows owns its screen, so the
framebuffer that fbui draws on is not available. A window of the player's own
would need a windowing library, image decoding and text drawn by hand in the
player. A browser is already on every machine this is for (Chrome on lrhtpc
and smartboardpc, and Edge and Chrome on the desktop), draws pictures and text
well, and can measure a picture for `[SPEC-PIC-040]`.

**`[SPEC-PIC-110]` Its own browser, in kiosk mode, with its own profile.** The
player looks for a browser in a fixed order: `google-chrome`, `chromium`,
`chromium-browser` on Linux, and `msedge`, then `chrome`, on Windows. It starts
it full screen in kiosk mode on the frame's page, with a profile directory
beside the player's state (`--user-data-dir`), no first-run screens and no
restore prompt. A profile of its own makes it a process of its own, which the
player can end, and keeps it out of whatever a person browses on that machine.

**`[SPEC-PIC-120]` Over everything, and gone without trace.** Shown, the frame
is the top window on the screen. Hidden, its browser is ended, and whatever was
underneath (Kodi, a desktop, another program) is as it was. Whether a newly
started kiosk window comes up over a full-screen Kodi on lrhtpc is to be
measured. If it does not, the player raises it with `xdotool windowactivate`,
which lrhtpc has.

**`[SPEC-PIC-130]` A frame closed at the screen stays closed.** If its browser
exits when the player did not end it (a person pressed Alt+F4, or it crashed),
the switch turns itself off, and the panel says the frame was closed on the
screen. The person in the room has the last word. Switching it on again starts
it again.

**`[SPEC-PIC-140]` Offered only where there is a screen and a browser.** On
Linux there must be a display: one named in the player's environment
(`DISPLAY`, which the user unit on lrhtpc and smartboardpc already has), or
else the first X server's socket, for a player started at boot before the
session. On Windows the player must be running in the session of the person
signed in. Where either is missing, or no browser is found, the switch is
shown disabled with the reason beside it, and it is tried again each minute.
It is decided by what the machine has, not by its build: every node is built
with the default features, the appliances' among them. A Pi has no display in
its player's environment and no browser, so it is never offered there, and its
screen stays fbui's `[SPEC036]`. A phone, serving its own loopback alone, has
no frame, and no row on its panel.
X11 only for now (both machines are X11 sessions, read 2026-10-29). Under
Wayland the frame would show full screen but could not be raised over other
windows, and the panel would say so.

## 3. Switching it

**`[SPEC-PIC-200]` A row on the Lempi skin's Settings panel: *Picture frame*.**
- an on/off switch;
- the folder;
- the clock's size, as a share of the screen's height: 15% until changed, 5% to
  40% (the maintainer, 2026-10-29);
- a line saying where it stands: *showing, 102 pictures in ~/Pictures*,
  *off*, *closed on the screen*, or *not available: no display* (and so on).

It takes effect at once, with no restart. It is off on every machine until
someone switches it on, and lrhtpc's is switched on there.

**`[SPEC-PIC-210]` Kept on its own machine.** All three settings are rows in
`player_settings` (`picture_frame`, `picture_frame_folder` and
`picture_frame_clock_pct`). A change of size reaches a frame already showing
at its next minute. The star sync
takes that table from the hub's own state and never sends it to a node
(`star_merge.TABLES`, the `HUB` rule), so switching it on at lrhtpc switches it
on nowhere else. A player that starts with the switch on shows the frame as soon
as it can.

## 4. What it exposes

**`[SPEC-PIC-300]` The pictures are served only to the browser the player
started.** They are a person's own photographs, and the player's port is open
to the LAN. Each time the player starts the browser it makes a random token,
which is in the URL it gives the browser and known to nothing else on the
network. The frame's page and its pictures are refused to any request without
that token. It guards them from the LAN, not from that machine: the URL is in
the browser's command line, which a person signed in there can read, as they
can read the folder itself. A picture
is asked for by its number in the player's own list, never by a path. The list
holds only files whose resolved path lies under the resolved folder, so a link
cannot lead out of it.

**`[SPEC-PIC-310]` The switch and the folder are settings like the others.**
The Settings panel is reachable from the LAN, as every other setting there is,
so a host on the LAN can turn the frame on: the same reach as Play and Pause.
What it can do with the folder is choose which pictures appear on that screen,
not get them: they are not served to it `[SPEC-PIC-300]`. The folder must
exist and be readable, or the setting is refused with the reason.

## 5. Where it applies

| machine | its screen, read 2026-10-29 | browser | the switch |
| :--- | :--- | :--- | :--- |
| `lrhtpc` | X11, XFCE under LightDM; HDMI, 3840×2160 at 60 Hz; DPMS disabled, its timers 600 s; 7.7 GB of memory | Chrome 148 | off; tested on and off, left off |
| `smartboardpc` | X11, GNOME on Xorg (Ubuntu 24.04) | Chrome, Firefox | off |
| the desktop (`localhost`) | Windows 11 | Edge, Chrome | off |
| `teacherslounge` | not read: not on the network that evening | not read | off |
| the Pis | fbui's, or none | — | not offered |

lrhtpc's folder held 102 pictures that evening (101 JPEG, 1 PNG, 355 MB),
in the folder itself and one subfolder, `Wallpapers`.

## 6. Chosen by default, and kept

Proposed as defaults, and kept as they are by the maintainer, 2026-10-29:
1. Closing the frame at the screen switches it off, rather than having it come back `[SPEC-PIC-130]`.
2. Subfolders are included `[SPEC-PIC-050]`.
3. No AM or PM on the clock `[SPEC-PIC-030]`.
4. Pictures change on the minute with the clock, with a one-second crossfade `[SPEC-PIC-020]`.
5. The clock's colour comes from the mean luminance under it. A picture busy in that corner may read poorly either way; an outline was left out because it was not asked for `[SPEC-PIC-040]`.
6. A picture is cropped to fill the screen, never letterboxed `[SPEC-PIC-010]`.

## 7. To build, and to measure on lrhtpc

**`[SPEC-PIC-900]` Built in this order:**
1. the player: finding the browser and starting and ending it, the token, the picture list, and the routes for the page and its pictures;
2. the page: cover, preload, the minute, the measured colour, the wake lock;
3. the Settings row, the two settings, and the capability that offers it;
4. tests: the token refused and accepted, a path outside the folder never listed, the colour rule at its threshold, and the switch's states.

**`[SPEC-PIC-910]` Measured on lrhtpc, 2026-10-29 23:05 to 23:20,** on
`17fffb3` and then `9099338`, over a YouTube live camera in an ordinary Chrome
window, by switching it on and off through the Settings routes:
- **Over the screen, and gone without trace:** started on `:0`, raised
  (xdotool), and the active window, full screen at 3840×2160. Switched off,
  no process of its profile was left. The YouTube window was active again and
  `xset q` read as before.
- **The clock:** its digits 15% of the screen's height and set in by a third
  of that, measured from screenshots. White on a dim corner, then black at
  11:08 on bright surf and sand, each changing at the minute with a new picture.
- **A bubble found:** on the first run, Chrome's "Can't update Chrome" bubble,
  from the frame's own browser, sat in a corner of the pictures. Suppressed
  in `9099338`, and absent on the next run.
- **A player restarted under it:** the new player put back the blanking the
  old one had turned off, and showed the frame again a second after starting.
- **Its pictures:** 4.7 MB served in 5 ms over loopback, and 404 without the
  token, from the machine and from the LAN.
- **Memory:** 1,111 MB, then 1,081 MB four minutes later, summed over its 10
  processes' resident sets (shared pages counted more than once).

**Not yet measured:** over a full-screen Kodi, which was not running; the
screen held past a DPMS that is on, since it was off; memory after an hour,
rather than four minutes; and the colour rule across all 102 pictures, rather
than the few that came up.

---

**Traceability:** `[SPEC-PIC-010..060]`, `[SPEC-PIC-100..140]`, `[SPEC-PIC-200..210]`, `[SPEC-PIC-300..310]`, `[SPEC-PIC-900..910]` · at the maintainer's request, 2026-10-29 · beside ROADMAP §3 question 2
