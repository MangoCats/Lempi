//! A native framebuffer UI for small SPI touchscreens `[SPEC036]`.
//!
//! Phase 2 of that spec's plan: prove the pixel format and orientation
//! assumptions against real hardware before building anything else on top
//! of them. This binary does exactly one thing -- connect to `lempi`'s
//! *existing* `/ws`, the same push every browser skin already reads, and
//! draw the title/artist it carries onto whichever `/dev/fbN` the
//! `fb_ili9486` driver claims (`FbDisplay::find_panel_path`, found by name
//! rather than a fixed index after a later boot-config change moved it
//! from `fb0` to `fb1`) -- 480x320, RGB565 `[SPEC-FBUI-025]`, measured
//! against `lempiplay3` directly, not assumed.
//!
//! Deliberately not the real `Snapshot` from `web::mod` -- that type is
//! `Serialize`-only (the server never deserializes its own push), and
//! giving it `Deserialize` too would be a change to code this design's
//! whole premise is to leave untouched `[SPEC-FBUI-015]`. `ClientSnapshot`
//! below names only the handful of fields this phase actually draws;
//! serde ignores every field it doesn't ask for, so the real `Snapshot`
//! growing a field is not a breaking change here.
//!
//! Phase 3 adds touch calibration `[SPEC-FBUI-050]`: reads the ADS7846
//! touch device's raw evdev stream directly `[SPEC-FBUI-045]` (a
//! hand-rolled 24-byte `struct input_event` parse -- the documented,
//! stable ABI on 64-bit Linux -- rather than pulling in an `evdev` crate
//! for a format this small), walks the person through three on-screen
//! targets, and solves the standard 3-point affine calibration (Vidales'
//! algorithm: the same one tslib/X11 evtouch use) rather than assuming
//! any particular rotation or axis convention. That matters here because
//! `piscreen2r`'s `rotate=90` is applied by the *display* driver; the
//! touch controller has no idea it happened, so an assumed mapping would
//! have to hardcode this panel's specific rotation. An empirical
//! screen-to-raw correspondence absorbs whatever rotation, mirroring, or
//! skew actually exists between them, without needing to know what it is.
//!
//! Phase 4 wires that calibration to an actual transport UI: play/pause,
//! skip, +-3dB volume, and tap-to-seek on the position bar, hit-tested
//! against the concrete regions `render` draws and posted to `lempi`'s
//! existing `/command/:name`, `/volume/:db`, `/seek/:passage/:ms` routes
//! `[SPEC-FBUI-015]` -- the exact three real command names `control.rs`
//! serves (`play`, `pause`, `skip`; there is deliberately no "prev" or
//! "stop" `[REQ-AUD-142]`), not assumed ones. Touch is read on its own OS
//! thread and joined with the websocket stream via `tokio::select!` in
//! `main`, rather than sharing `fbui`'s single async worker thread with a
//! blocking evdev read.
//!
//! Phase 5 fixes the reconnection gap `[SPEC036]` §7 named: every drop
//! (not just the first-ever connect) now shows `render_disconnected` and
//! resets the held snapshot, so an identical post-restart push can't be
//! mistaken by `[SPEC-FBUI-020]`'s diff check for "nothing changed."
//!
//! Phase 6 replaces `embedded-graphics`'s bundled ASCII-only fonts with
//! `u8g2-fonts`' `u8g2_font_9x15_t_symbols` -- chosen by measuring real
//! titles/artists (queried from a production `lempi.db`, not invented):
//! it covers the accented Latin that shows up constantly ("Bj\u{f6}rk",
//! "Mendon\u{e7}a"), confirmed by actually calling `render()` against it
//! rather than trusting the font name (`tests::font_coverage_matches_real_library_data`).
//! It does *not* cover the curly quotes/apostrophes and Unicode hyphens
//! that turn out to be the single most common non-ASCII character in this
//! library ("Guns N\u{2019}Roses", "The Go\u{2010}Go\u{2019}s") -- no
//! bitmap font checked carried both without jumping to `unifont`'s much
//! larger, uglier glyphs for a whole library's worth of tracks that
//! mostly don't need it. `normalize_for_display` maps that specific,
//! measured set to its plain-ASCII look-alike instead: the semantic
//! content of a title is unaffected by a curly apostrophe rendering as a
//! straight one.
//!
//! A post-Phase-7 refinement adds two pages -- Now Playing and Settings,
//! reached by a gear icon in the top-right corner of both -- rather than
//! trying to fit volume control, all 8 programmes, and per-track queue
//! editing onto one 480x320 screen at once. Now Playing gained a 2x5
//! button grid (play/pause, skip, then all 8 programmes, the currently
//! active one highlighted); Settings holds volume +/- and the upcoming
//! queue, each entry with the same sooner/remove/later actions the
//! browser skins already expose at `/queue/:qid/:action`
//! `[SPEC-FBUI-015]`. The programme buttons never send `auto`
//! (time-of-day selection) -- only ever a specific id -- matching this
//! appliance's own requirement (no realtime clock, and a truck's driving
//! hours have no relationship to a schedule tuned for home listening).
//! The position/progress redraw is throttled to once per 5 seconds
//! (`differs_ignoring_position`); every other change still redraws at
//! once.
//!
//! Now Playing's palette was then re-themed to match MuLibPlay's own web
//! skin (`web/skins/mulibplay/skin.css`'s real, measured colors -- black
//! ground, `#ccc` text, filled `#2010a0` buttons, `#d0a000` gold for
//! whatever is active, `#7ab7ff` progress fill) rather than WinAmp green,
//! at the same resolution-driven simplification that skin's own outlined
//! variants get: filled and rounded, no thin outline strokes, which do not
//! read clearly at 480x320. Settings keeps the original WinAmp palette.
//!
//!     The command line is `cli::specs::fbui`; `fbui --help` prints it,
//!     default URL included.
//!
//! Every line it writes goes through `tracing` since 2026-09-25, with its
//! text unchanged: it runs as a service, and its 17 `eprintln!`s -- a failed
//! snapshot parse, a lost connection -- reached the journal as info, where
//! `journalctl -p warning` could not see them `[GDE-HST-180]` step (4).
#![deny(clippy::print_stdout, clippy::print_stderr)]

use embedded_graphics::pixelcolor::Rgb565;
use embedded_graphics::prelude::*;
use embedded_graphics::primitives::{Circle, CornerRadii, Line, PrimitiveStyle, PrimitiveStyleBuilder, Rectangle, RoundedRectangle, Triangle};
use framebuffer::Framebuffer;
use futures_util::StreamExt;
use u8g2_fonts::types::{FontColor, HorizontalAlignment, VerticalPosition};
use u8g2_fonts::FontRenderer;

/// Named in the real `Snapshot` as `ProgramItem` -- only `id`/`name` are
/// drawn here, `start` is not; serde ignores the field it never asked for.
#[derive(serde::Deserialize, Default, Clone, PartialEq)]
struct ProgramItem {
    id: i64,
    name: String,
}

/// Named in the real `Snapshot` as `QueueItem` -- only what a queue row
/// displays and edits. `qid` (not `passage_id`) is what `/queue/:qid/:action`
/// takes `[REQ-VIS-186]`: the same passage queued twice must be editable
/// as two different rows.
#[derive(serde::Deserialize, Default, Clone, PartialEq)]
struct QueueItem {
    qid: u64,
    title: String,
    artist: Option<String>,
    #[serde(default)]
    is_shutdown: bool,
}

/// Only the fields this phase draws. Unknown incoming fields are ignored by
/// serde automatically -- the real `Snapshot` carries dozens more.
#[derive(serde::Deserialize, Default, Clone, PartialEq)]
struct ClientSnapshot {
    playing: bool,
    /// The passage on air, for fetching its cover at `/art/{id}` -- the
    /// exact same field the real `Snapshot` documents itself as carrying
    /// for this exact purpose `[SPEC036]` §8 phase 7.
    passage_id: Option<i64>,
    title: Option<String>,
    artist: Option<String>,
    /// The Release title -- `None` until the release tables are populated
    /// and the file carries no album tag either, matching the real
    /// `Snapshot`'s own field exactly.
    album: Option<String>,
    position_ms: u64,
    duration_ms: u64,
    volume_db: f32,
    /// The programme in force, by name -- the real `Snapshot` carries no
    /// numeric id for it, only `programs[].id` for the *available* list.
    /// Matched against `programs` by name to decide which button to
    /// highlight; names are unique in practice (`listener_programs` has no
    /// uniqueness constraint on `name`, but nothing in this project has
    /// ever given two programmes the same one).
    program: Option<String>,
    programs: Vec<ProgramItem>,
    queue: Vec<QueueItem>,
    /// The supply in one word -- `ok`, `recent`, `low` -- or `None` where
    /// the player watches nothing `[APP-PWR-010]`. Drawn as a glyph by the
    /// gear; a change of it redraws, like any other field.
    power: Option<String>,
    /// What the Audio page may offer `[SPEC-TPL-110]`, `[SPEC-EQ-070]`:
    /// false for a backend that cannot, whose rows then say so instead.
    /// Defaulted, these and the rest below, so a player from before them
    /// still parses: absent is "not offered", never a blank screen.
    #[serde(default)]
    can_tempo_pitch: bool,
    #[serde(default)]
    can_ab_loop: bool,
    #[serde(default)]
    can_eq: bool,
    /// `1000` is `1.00x`; `0` only when absent, read as neutral
    /// (`ClientSnapshot::tempo`).
    #[serde(default)]
    tempo_milli: u32,
    #[serde(default)]
    pitch_cents: i32,
    #[serde(default)]
    ab_loop: AbLoopView,
    /// `off`, a preset's name, or `custom` `[SPEC-EQ-085]` -- the server owns
    /// the curves, so this page never needs one.
    #[serde(default)]
    eq_preset: Option<String>,
}

/// Named in the real `Snapshot` as `AbLoop`; a loop is active when both are
/// set `[SPEC-TPL-020]`.
#[derive(serde::Deserialize, Default, Clone, Copy, PartialEq)]
struct AbLoopView {
    a_ms: Option<u64>,
    b_ms: Option<u64>,
}

impl ClientSnapshot {
    fn tempo(&self) -> u32 {
        if self.tempo_milli == 0 { 1000 } else { self.tempo_milli }
    }

    /// Tempo, pitch or a loop is in use -- what makes playback something
    /// other than the passage as it is `[SPEC-TPL-015]`. The equalizer is not:
    /// it shapes the sound and changes no time.
    fn warped(&self) -> bool {
        self.tempo() != 1000 || self.pitch_cents != 0 || self.ab_loop.a_ms.is_some()
    }

    /// The short line Now Playing shows while anything on the Audio page is
    /// in use: `1.25x -2st A-B EQ`. `None` when nothing is.
    fn audio_summary(&self) -> Option<String> {
        let mut parts = Vec::new();
        if self.tempo() != 1000 {
            parts.push(fmt_tempo(self.tempo()));
        }
        if self.pitch_cents != 0 {
            parts.push(format!("{:+}st", self.pitch_cents / 100));
        }
        match (self.ab_loop.a_ms, self.ab_loop.b_ms) {
            (Some(_), Some(_)) => parts.push("A-B".into()),
            (Some(_), None) => parts.push("A..".into()),
            _ => {}
        }
        if self.eq_preset.as_deref().is_some_and(|p| p != "off") {
            parts.push("EQ".into());
        }
        (!parts.is_empty()).then(|| parts.join(" "))
    }
}

/// Wraps the real Linux framebuffer device so `embedded-graphics` can draw
/// into it directly. Reads the panel's *actual* reported geometry via the
/// standard `FBIOGET_VSCREENINFO`/`FBIOGET_FSCREENINFO` ioctls (what the
/// `framebuffer` crate does internally) rather than hardcoding the
/// 480x320/RGB565 this project already measured -- a future config change
/// to `piscreen2r`'s params should not silently desync from a number typed
/// into this file.
struct FbDisplay {
    fb: Framebuffer,
    width: u32,
    height: u32,
    bytes_per_pixel: u32,
}

impl FbDisplay {
    /// Finds whichever `/dev/fbN` the `fb_ili9486` driver actually claimed,
    /// by name (`/sys/class/graphics/fbN/name`) rather than a fixed index.
    ///
    /// `[SPEC-FBUI-025]` originally confirmed this panel at `/dev/fb0`
    /// because nothing else claimed a framebuffer on that boot. Disabling
    /// `vc4-kms-v3d` for boot speed (audio-first priority) removed the
    /// hand-off that used to keep the legacy firmware framebuffer
    /// (`BCM2708 FB`, 32bpp) from ever appearing at all -- it now claims
    /// `fb0` first, bumping the real panel to `fb1`. A fixed path already
    /// broke once from a config change this file had no part in; scanning
    /// by name survives the next one too.
    fn find_panel_path() -> String {
        for n in 0..4 {
            let name_path = format!("/sys/class/graphics/fb{n}/name");
            if std::fs::read_to_string(&name_path).map(|s| s.trim() == "fb_ili9486").unwrap_or(false) {
                return format!("/dev/fb{n}");
            }
        }
        // Falls through to the historically-confirmed default so the error
        // this produces downstream (wrong bpp, or no such device) still
        // names a real path to go look at, rather than an empty string.
        "/dev/fb0".to_string()
    }

    fn open(path: &str) -> Result<Self, String> {
        let fb = Framebuffer::new(path).map_err(|e| format!("open {path}: {e:?}"))?;
        let width = fb.var_screen_info.xres;
        let height = fb.var_screen_info.yres;
        let bits_per_pixel = fb.var_screen_info.bits_per_pixel;
        if bits_per_pixel != 16 {
            // This design's whole pixel-writing path below assumes RGB565
            // (2 bytes/pixel) because that is what was actually measured
            // against lempiplay3's hardware [SPEC-FBUI-025]. A different
            // bit depth here means a different overlay or a different
            // config than what this was built and tested against -- refuse
            // rather than write pixels in the wrong format silently.
            return Err(format!(
                "expected 16bpp (RGB565), got {bits_per_pixel}bpp -- this binary's pixel \
                 writer was built against the confirmed lempiplay3 config and does not \
                 know how to draw into this format"
            ));
        }
        Ok(Self { fb, width, height, bytes_per_pixel: bits_per_pixel / 8 })
    }

    fn put_pixel(&mut self, x: u32, y: u32, color: Rgb565) {
        if x >= self.width || y >= self.height {
            return;
        }
        let offset = ((y * self.width + x) * self.bytes_per_pixel) as usize;
        // RGB565, little-endian -- the standard fbdev byte order for this
        // depth. Confirmed correct by physically looking at the panel
        // `[SPEC-FBUI-027]`: real green text on black, as designed.
        let raw = color.into_storage();
        let bytes = raw.to_le_bytes();
        self.fb.frame[offset] = bytes[0];
        self.fb.frame[offset + 1] = bytes[1];
    }
}

impl OriginDimensions for FbDisplay {
    fn size(&self) -> Size {
        Size::new(self.width, self.height)
    }
}

impl DrawTarget for FbDisplay {
    type Color = Rgb565;
    type Error = std::convert::Infallible;

    fn draw_iter<I>(&mut self, pixels: I) -> Result<(), Self::Error>
    where
        I: IntoIterator<Item = Pixel<Self::Color>>,
    {
        for Pixel(point, color) in pixels {
            if point.x >= 0 && point.y >= 0 {
                self.put_pixel(point.x as u32, point.y as u32, color);
            }
        }
        Ok(())
    }
}

/// Anything these pages can be drawn on: the panel, or a buffer in memory
/// that a test can save as an image and a person can look at -- the only way
/// to see a page without the hardware.
trait Panel: DrawTarget<Color = Rgb565, Error = std::convert::Infallible> {}
impl<T: DrawTarget<Color = Rgb565, Error = std::convert::Infallible>> Panel for T {}

/// The WinAmp-derived palette `[SPEC-FBUI-035]` -- same values as
/// `web/skins/winamp/skin.css`'s `--lcd-bg`/`--lcd`, so the two UIs read as
/// the same player rather than coincidentally similar ones. Still used for
/// Settings, the gear icon's own outline on both pages, calibration, and
/// the disconnected screen -- only Now Playing's own content re-themes.
const LCD_BG: Rgb565 = Rgb565::new(1, 3, 1); // ~#0b0f0b at 5/6/5 bit depth
const LCD_GREEN: Rgb565 = Rgb565::new(4, 59, 4); // ~#22dd22

/// MuLibPlay's own palette, the same real, measured values
/// `web/skins/mulibplay/skin.css`'s own doc comment states for itself:
/// "black ground, #ccc text, big rounded #2010a0 buttons with a #d0a000
/// gold for whatever is currently on." Filled and rounded rather than
/// outlined -- the same simplification any low-resolution rendering of
/// that skin needs, since a thin outline stroke does not read clearly at
/// 480x320.
const MULIB_BG: Rgb565 = Rgb565::new(0, 0, 0); // #000000, "black ground"
const MULIB_TEXT: Rgb565 = Rgb565::new(25, 52, 25); // #cccccc
const MULIB_DIM: Rgb565 = Rgb565::new(17, 34, 17); // #888888, secondary text
/// Darker than `#2010a0`'s own literal value -- the real web skin has that
/// color next to `#ccc` text on a browser's own accurate display; this
/// panel's real, measured contrast between the two was reported too low
/// to read comfortably, so the button is darkened rather than the text
/// lightened (keeping `MULIB_TEXT` shared and correct for title/artist
/// against pure black elsewhere on the same page).
const MULIB_BUTTON: Rgb565 = Rgb565::new(1, 1, 9); // darker than #2010a0
const MULIB_GOLD: Rgb565 = Rgb565::new(25, 40, 0); // #d0a000, "whatever is currently on"
const MULIB_GOLD_TEXT: Rgb565 = Rgb565::new(2, 4, 2); // #111111, .buttonOn's own text color
/// Darker than `#333333`'s own literal value, for the same reported-low-
/// contrast reason as `MULIB_BUTTON` -- against `MULIB_FILL`, "which part
/// of the bar has played" needs to be legible on this specific panel, not
/// just correct against a desktop reference.
const MULIB_TRACK: Rgb565 = Rgb565::new(2, 4, 2); // darker than #333333
const MULIB_FILL: Rgb565 = Rgb565::new(15, 45, 31); // #7ab7ff
/// `.button`'s own 20px on desktop-sized buttons; scaled down for this
/// panel's much smaller ones, kept generous enough to still read as "big
/// rounded" rather than barely-softened corners.
const MULIB_RADIUS: u32 = 14;

/// The Lempi skin's own palette, for the Audio page -- its dark theme, as
/// `web/skins/lempi/skin.css` defines it: `--bg #141416`, `--fg #eee`,
/// `--dim #99a`, `--line #2c2c30`, and the `#4c9a5a` its equalizer curve is
/// drawn in while on. The skin's buttons are a 1px `--line` outline on the
/// page; here they are filled `--line` with a 2px `--dim` edge, for the reason
/// the other two palettes give: a thin stroke does not read at 480x320. What
/// is on is the skin's own inversion -- ink and paper trading places -- not a
/// colour.
const LEMPI_BG: Rgb565 = Rgb565::new(2, 5, 2); // #141416
const LEMPI_FG: Rgb565 = Rgb565::new(29, 59, 29); // #eeeeee
const LEMPI_DIM: Rgb565 = Rgb565::new(19, 38, 21); // #9999aa
const LEMPI_LINE: Rgb565 = Rgb565::new(5, 11, 6); // #2c2c30
const LEMPI_GREEN: Rgb565 = Rgb565::new(9, 38, 11); // #4c9a5a
/// The skin's `border-radius: 6px`.
const LEMPI_RADIUS: u32 = 6;

/// `[SPEC-FBUI-030]`, revised for Phase 6: `embedded-graphics`'s own
/// bundled fonts are ASCII-only, which real library data (not a synthetic
/// test string) shows is not enough. `u8g2_font_9x15_t_symbols` matches
/// this file's original 9x15 glyph size and covers the accented Latin
/// that appears constantly in real titles/artists -- checked by actually
/// rendering, not by trusting the font's name
/// (`tests::font_coverage_matches_real_library_data`).
const TEXT_FONT: FontRenderer = FontRenderer::new::<u8g2_fonts::fonts::u8g2_font_9x15_t_symbols>();

/// Bold, for button labels specifically -- reported low body/text contrast
/// on the MuLibPlay-themed buttons is helped by weight as well as by
/// `MULIB_BUTTON`'s own darkening. ASCII-only (`_tr`, not `_t_symbols`):
/// no bold+full-coverage variant of this font exists, but every button
/// label this file ever draws is either this project's own fixed English
/// text (PLAY, SKIP, VOL -, ...) or a real production programme name
/// (`Cool`, `Fun`, `Mellow`, `Soft`, `Prog`, `Light`, `Loud`, `Groove` --
/// checked against `lempiplay3`'s own deployed database, not assumed),
/// all plain ASCII.
const BUTTON_FONT: FontRenderer = FontRenderer::new::<u8g2_fonts::fonts::u8g2_font_9x15B_tr>();

/// Maps the specific, measured set of punctuation this library's titles
/// use that `TEXT_FONT` does not carry a glyph for -- curly quotes and
/// apostrophes, and every dash-like character in Unicode's General
/// Punctuation block, plus the Hawaiian ʻokina (a modifier letter, not a
/// quote mark, but visually and phonetically closest to one) -- to their
/// plain-ASCII look-alikes. A title's meaning survives a curly apostrophe
/// rendering as a straight one; a missing-glyph box or a render error
/// would be the actually-wrong outcome here.
fn normalize_for_display(s: &str) -> String {
    s.chars()
        .map(|c| match c {
            '\u{2018}' | '\u{2019}' | '\u{02bb}' | '\u{02bc}' => '\'',
            '\u{201c}' | '\u{201d}' => '"',
            '\u{2010}'..='\u{2015}' => '-',
            other => other,
        })
        .collect()
}

/// Cut to `max_chars`, ellipsis included, using three ASCII dots rather
/// than the single Unicode ellipsis glyph (`…`, U+2026) -- untested against
/// `TEXT_FONT` and not worth adding to `normalize_for_display`'s mapping
/// for the one place this file would ever produce it.
fn truncate_display(s: &str, max_chars: usize) -> String {
    if s.chars().count() <= max_chars {
        return s.to_string();
    }
    let mut t: String = s.chars().take(max_chars.saturating_sub(3)).collect();
    t.push_str("...");
    t
}

/// Renders one line of already-`normalize_for_display`-cleaned text at a
/// given left/baseline anchor. Errors are logged, not propagated --
/// `FbDisplay`'s own `DrawTarget::Error` is `Infallible`, so the only way
/// this can fail is a glyph `TEXT_FONT` genuinely lacks; that should be
/// caught by `normalize_for_display` and the coverage test before it ever
/// reaches here, so surfacing it loudly if it somehow doesn't is more
/// useful than a silent `.unwrap_or(())`.
fn draw_text(display: &mut impl Panel, text: &str, x: i32, y: i32) {
    draw_text_color(display, text, x, y, LCD_GREEN);
}

/// As `draw_text`, in a caller-chosen color -- Now Playing's MuLibPlay
/// theme and Settings/calibration's WinAmp green share this one renderer,
/// not two near-identical copies of it.
fn draw_text_color(display: &mut impl Panel, text: &str, x: i32, y: i32, color: Rgb565) {
    if let Err(e) = TEXT_FONT.render(text, Point::new(x, y), VerticalPosition::Baseline, FontColor::Transparent(color), display) {
        tracing::warn!("fbui: could not render {text:?}: {e:?}");
    }
}

/// Right-aligned at `right_x`, on a baseline.
fn draw_text_right(display: &mut impl Panel, text: &str, right_x: i32, y: i32, color: Rgb565) {
    if let Err(e) = TEXT_FONT.render_aligned(
        text,
        Point::new(right_x, y),
        VerticalPosition::Baseline,
        HorizontalAlignment::Right,
        FontColor::Transparent(color),
        display,
    ) {
        tracing::warn!("fbui: could not render {text:?}: {e:?}");
    }
}

fn draw_text_centered(display: &mut impl Panel, text: &str, center: Point, color: Rgb565) {
    draw_text_centered_font(display, &TEXT_FONT, text, center, color);
}

/// As `draw_text_centered`, in a caller-chosen font -- `draw_button_mulib`
/// uses this with `BUTTON_FONT` instead of `TEXT_FONT`.
fn draw_text_centered_font(display: &mut impl Panel, font: &FontRenderer, text: &str, center: Point, color: Rgb565) {
    if let Err(e) = font.render_aligned(
        text,
        center,
        VerticalPosition::Center,
        HorizontalAlignment::Center,
        FontColor::Transparent(color),
        display,
    ) {
        tracing::warn!("fbui: could not render {text:?}: {e:?}");
    }
}

fn fmt_time(ms: u64) -> String {
    let s = ms / 1000;
    format!("{}:{:02}", s / 60, s % 60)
}

// ---------------------------------------------------------------------
// Pages. Settings is reached by the gear and Audio by the fader icon, both
// present at the same coordinates on every page; each icon toggles its own
// page against Now Playing -- not a stack, since there are only these three.
// ---------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq, Debug)]
enum Page {
    NowPlaying,
    Settings,
    Audio,
}

/// Top-right, both pages. Hit region is deliberately larger than the drawn
/// icon -- a fingertip is wider than a 2px gear outline.
const GEAR_CX: i32 = 458;
const GEAR_CY: i32 = 22;
const GEAR_HIT_X0: i32 = 420;
const GEAR_HIT_Y1: i32 = 44;

/// `backdrop` matches whichever page is currently drawn (`MULIB_BG` on Now
/// Playing, `LCD_BG` on Settings) -- the gear's own outline stays WinAmp
/// green on both, a fixed piece of chrome rather than page content, but
/// its backdrop patch would otherwise stand out as a mismatched square.
/// The supply's state, as a lightning bolt just left of the gear
/// `[APP-PWR-010]`: amber after a dip in the last ten minutes, red while the
/// supply is low now, and nothing at all while it is fine -- a screen that
/// shows a glyph only when there is something to say. Its own backdrop
/// patch, for the same reason the gear has one.
const POWER_X0: i32 = 394;
fn power_color(state: Option<&str>) -> Option<Rgb565> {
    match state {
        Some("low") => Some(Rgb565::new(31, 4, 2)),
        Some("recent") => Some(Rgb565::new(31, 42, 0)),
        _ => None,
    }
}
fn draw_power(display: &mut impl Panel, state: Option<&str>, backdrop: Rgb565) -> Result<(), std::convert::Infallible> {
    let Some(color) = power_color(state) else { return Ok(()) };
    Rectangle::new(Point::new(POWER_X0, 0), Size::new((GEAR_HIT_X0 - POWER_X0) as u32, GEAR_HIT_Y1 as u32))
        .into_styled(PrimitiveStyle::with_fill(backdrop))
        .draw(display)?;
    let fill = PrimitiveStyle::with_fill(color);
    // Two triangles make the bolt: a top half leaning right, a bottom half
    // leaning left, meeting at the waist.
    Triangle::new(Point::new(412, 6), Point::new(400, 24), Point::new(409, 24)).into_styled(fill).draw(display)?;
    Triangle::new(Point::new(405, 20), Point::new(414, 20), Point::new(402, 38)).into_styled(fill).draw(display)?;
    Ok(())
}

fn draw_gear(display: &mut impl Panel, backdrop: Rgb565) -> Result<(), std::convert::Infallible> {
    // A solid backdrop first: this corner is drawn last so it always wins
    // regardless of how long the title text underneath it runs, rather
    // than hoping no title is ever wide enough to reach the corner.
    Rectangle::new(Point::new(GEAR_HIT_X0, 0), Size::new(60, GEAR_HIT_Y1 as u32))
        .into_styled(PrimitiveStyle::with_fill(backdrop))
        .draw(display)?;
    let (r_in, r_out) = (9i32, 16i32);
    Circle::with_center(Point::new(GEAR_CX, GEAR_CY), (r_in * 2) as u32)
        .into_styled(PrimitiveStyle::with_stroke(LCD_GREEN, 2))
        .draw(display)?;
    for i in 0..8 {
        let theta = (i as f64) * std::f64::consts::PI / 4.0;
        let (sin, cos) = theta.sin_cos();
        let p0 = Point::new(GEAR_CX + (cos * r_in as f64) as i32, GEAR_CY + (sin * r_in as f64) as i32);
        let p1 = Point::new(GEAR_CX + (cos * r_out as f64) as i32, GEAR_CY + (sin * r_out as f64) as i32);
        Line::new(p0, p1).into_styled(PrimitiveStyle::with_stroke(LCD_GREEN, 2)).draw(display)?;
    }
    Ok(())
}

// ---------------------------------------------------------------------
// Now Playing: album art, the tap-to-seek progress bar, and a 2x5 button
// grid -- play/pause, skip, then all 8 programmes `[SPEC-DIR-140]`, the
// currently active one highlighted.
// ---------------------------------------------------------------------

// Album art `[SPEC036]` §8 phase 7: a thumbnail, top-left, doubled to
// 100x100 after seeing the first size on the physical screen.
const ART_SIZE: u32 = 100;
const ART_X0: u32 = 4;
const ART_Y0: u32 = 4;
const TEXT_X: i32 = 112;
const TITLE_Y: i32 = 40;
const ARTIST_Y: i32 = 64;
const ALBUM_Y: i32 = 88;

const SEEKBAR_Y0: i32 = 110;
const SEEKBAR_Y1: i32 = 128;
const POS_TEXT_Y: i32 = 148;

/// Two rows of five: play/pause, skip, then all 8 programmes. Sized well
/// above a fingertip's real contact area, not just legible text, since
/// this is what a person actually presses -- same reasoning as the
/// original single-row grid, now split to fit ten buttons instead of four.
const GRID_XS: [(i32, i32); 5] = [(0, 88), (96, 184), (192, 280), (288, 376), (384, 472)];
const ROW1_Y0: i32 = 162;
const ROW1_Y1: i32 = 208;
const ROW2_Y0: i32 = 214;
const ROW2_Y1: i32 = 260;

/// `programs[0..8]`'s screen slots, in row-major reading order matching the
/// physical grid: row 1 is transport, row 2 is entirely programmes 4-8.
const PROGRAM_SLOTS: [(i32, i32, i32, i32); 8] = [
    (GRID_XS[2].0, GRID_XS[2].1, ROW1_Y0, ROW1_Y1),
    (GRID_XS[3].0, GRID_XS[3].1, ROW1_Y0, ROW1_Y1),
    (GRID_XS[4].0, GRID_XS[4].1, ROW1_Y0, ROW1_Y1),
    (GRID_XS[0].0, GRID_XS[0].1, ROW2_Y0, ROW2_Y1),
    (GRID_XS[1].0, GRID_XS[1].1, ROW2_Y0, ROW2_Y1),
    (GRID_XS[2].0, GRID_XS[2].1, ROW2_Y0, ROW2_Y1),
    (GRID_XS[3].0, GRID_XS[3].1, ROW2_Y0, ROW2_Y1),
    (GRID_XS[4].0, GRID_XS[4].1, ROW2_Y0, ROW2_Y1),
];

/// WinAmp-styled: an outline, sharp corners, green -- used by Settings,
/// unaffected by Now Playing's MuLibPlay re-theme.
fn draw_button_winamp(display: &mut impl Panel, x0: i32, x1: i32, y0: i32, y1: i32, label: &str, highlighted: bool) -> Result<(), std::convert::Infallible> {
    if highlighted {
        // Inverted: a filled background is unmistakable at a glance, which
        // matters more here than anywhere else in this UI -- "which
        // programme is active" is the one piece of state this screen has
        // no other way to show `[SPEC-DIR-140]`.
        Rectangle::new(Point::new(x0, y0), Size::new((x1 - x0) as u32, (y1 - y0) as u32))
            .into_styled(PrimitiveStyle::with_fill(LCD_GREEN))
            .draw(display)?;
        draw_text_centered(display, label, Point::new((x0 + x1) / 2, (y0 + y1) / 2), LCD_BG);
    } else {
        Rectangle::new(Point::new(x0, y0), Size::new((x1 - x0) as u32, (y1 - y0) as u32))
            .into_styled(PrimitiveStyle::with_stroke(LCD_GREEN, 2))
            .draw(display)?;
        draw_text_centered(display, label, Point::new((x0 + x1) / 2, (y0 + y1) / 2), LCD_GREEN);
    }
    Ok(())
}

/// MuLibPlay-styled: filled, big rounded corners, no outline stroke -- a
/// thin outline does not read clearly at this panel's resolution, the same
/// simplification a low-resolution rendering of that skin needs regardless
/// of this project's own reasons for it. `#2010a0` indigo normally,
/// `#d0a000` gold (with `#111` text, matching `.buttonOn` exactly) for
/// whichever programme is currently active `[SPEC-DIR-140]`.
fn draw_button_mulib(display: &mut impl Panel, x0: i32, x1: i32, y0: i32, y1: i32, label: &str, highlighted: bool) -> Result<(), std::convert::Infallible> {
    let (fill, text) = if highlighted { (MULIB_GOLD, MULIB_GOLD_TEXT) } else { (MULIB_BUTTON, MULIB_TEXT) };
    let rect = Rectangle::new(Point::new(x0, y0), Size::new((x1 - x0) as u32, (y1 - y0) as u32));
    RoundedRectangle::new(rect, CornerRadii::new(Size::new(MULIB_RADIUS, MULIB_RADIUS)))
        .into_styled(PrimitiveStyle::with_fill(fill))
        .draw(display)?;
    draw_text_centered_font(display, &BUTTON_FONT, label, Point::new((x0 + x1) / 2, (y0 + y1) / 2), text);
    Ok(())
}

/// Blits a pre-resized `ART_SIZE`x`ART_SIZE` `Rgb565` buffer at the fixed
/// art position, pixel by pixel via `FbDisplay::put_pixel` directly rather
/// than through `embedded-graphics`' `Drawable` machinery -- the pixels
/// are already exactly what the panel needs (decoded and resized once in
/// `decode_and_resize`, not on every redraw), so there is nothing left for
/// a generic drawing primitive to add here.
fn draw_art(display: &mut impl Panel, pixels: &[Rgb565]) {
    let at = pixels.iter().enumerate().map(|(i, &color)| {
        let i = i as u32;
        Pixel(Point::new((ART_X0 + i % ART_SIZE) as i32, (ART_Y0 + i / ART_SIZE) as i32), color)
    });
    let _ = display.draw_iter(at);
}

/// Now Playing: title/artist, art, the seek bar, position, and the 2x5
/// button grid. `[SPEC-FBUI-020]`'s whole-region redraw on any change is
/// `main`'s job to gate, not this function's.
fn render_now_playing(display: &mut impl Panel, snap: &ClientSnapshot, art: Option<&[Rgb565]>) -> Result<(), std::convert::Infallible> {
    let w = display.bounding_box().size.width as i32;
    Rectangle::new(Point::zero(), display.bounding_box().size)
        .into_styled(PrimitiveStyle::with_fill(MULIB_BG))
        .draw(display)?;

    let title = snap.title.as_deref().map(normalize_for_display);
    let artist = snap.artist.as_deref().map(normalize_for_display);
    let album = snap.album.as_deref().map(normalize_for_display);
    draw_text_color(display, title.as_deref().unwrap_or("(nothing playing)"), TEXT_X, TITLE_Y, MULIB_TEXT);
    // Artist and album both dimmed, matching the web skin's own
    // `.nowplaying i { color: #888 }` -- album left blank (`Snapshot`'s
    // own `None`, not a placeholder string) rather than shown as
    // "(unknown)", the same absent-is-blank treatment artist already gets.
    draw_text_color(display, artist.as_deref().unwrap_or(""), TEXT_X, ARTIST_Y, MULIB_DIM);
    draw_text_color(display, album.as_deref().unwrap_or(""), TEXT_X, ALBUM_Y, MULIB_DIM);
    if let Some(pixels) = art {
        draw_art(display, pixels);
    }

    let (bar_x0, bar_x1) = (8, w - 8);
    let bar_radius = ((SEEKBAR_Y1 - SEEKBAR_Y0) / 2) as u32;
    RoundedRectangle::new(
        Rectangle::new(Point::new(bar_x0, SEEKBAR_Y0), Size::new((bar_x1 - bar_x0) as u32, (SEEKBAR_Y1 - SEEKBAR_Y0) as u32)),
        CornerRadii::new(Size::new(bar_radius, bar_radius)),
    )
    .into_styled(PrimitiveStyle::with_fill(MULIB_TRACK))
    .draw(display)?;
    if snap.duration_ms > 0 {
        let frac = (snap.position_ms as f64 / snap.duration_ms as f64).clamp(0.0, 1.0);
        let fill_w = (((bar_x1 - bar_x0) as f64) * frac).round() as u32;
        if fill_w > 0 {
            RoundedRectangle::new(
                Rectangle::new(Point::new(bar_x0, SEEKBAR_Y0), Size::new(fill_w, (SEEKBAR_Y1 - SEEKBAR_Y0) as u32)),
                CornerRadii::new(Size::new(bar_radius, bar_radius)),
            )
            .into_styled(PrimitiveStyle::with_fill(MULIB_FILL))
            .draw(display)?;
        }
    }

    let pos = format!(
        "{} {} / {}",
        if snap.playing { ">" } else { "||" },
        fmt_time(snap.position_ms),
        fmt_time(snap.duration_ms),
    );
    draw_text_color(display, &pos, 8, POS_TEXT_Y, MULIB_TEXT);
    // What the Audio page has in use, right-aligned on the same line: gold
    // while tempo, pitch or a loop changes what is heard, dim for the
    // equalizer alone `[SPEC-FBUI-110]`.
    if let Some(summary) = snap.audio_summary() {
        draw_text_right(display, &summary, w - 8, POS_TEXT_Y, if snap.warped() { MULIB_GOLD } else { MULIB_DIM });
    }

    draw_button_mulib(display, GRID_XS[0].0, GRID_XS[0].1, ROW1_Y0, ROW1_Y1, if snap.playing { "PAUSE" } else { "PLAY" }, false)?;
    draw_button_mulib(display, GRID_XS[1].0, GRID_XS[1].1, ROW1_Y0, ROW1_Y1, "SKIP", false)?;
    for (i, &(x0, x1, y0, y1)) in PROGRAM_SLOTS.iter().enumerate() {
        let label = snap.programs.get(i).map(|p| truncate_display(&normalize_for_display(&p.name), 8));
        let active = snap
            .programs
            .get(i)
            .is_some_and(|p| snap.program.as_deref() == Some(p.name.as_str()));
        draw_button_mulib(display, x0, x1, y0, y1, label.as_deref().unwrap_or(""), active)?;
    }

    draw_audio_icon(display, snap.warped(), MULIB_BG, GEAR_HIT_Y1)?;
    draw_gear(display, MULIB_BG)?;
    draw_power(display, snap.power.as_deref(), MULIB_BG)?;
    Ok(())
}

// ---------------------------------------------------------------------
// Settings: volume +/-, and the upcoming queue with per-item sooner/
// remove/later controls -- the same three actions `/queue/:qid/:action`
// already serves every browser skin `[SPEC-FBUI-015]`.
// ---------------------------------------------------------------------

const VOL_DOWN_X: (i32, i32) = (8, 150);
const VOL_UP_X: (i32, i32) = (330, 472);
const VOL_ROW_Y: (i32, i32) = (30, 72);

const QUEUE_ROW_HEIGHT: i32 = 44;
const QUEUE_TOP_Y: i32 = 94;
const QUEUE_ROW_GAP: i32 = 2;
/// However many rows actually fit above the pairing row -- not tied to
/// any particular queue depth setting, since `[REQ-VIS-180]`-style depth
/// changes must not silently need a layout change here too. Five until
/// 2026-09-28, when the fifth (y 278..322, two pixels past a 320-high
/// panel) gave its place to the pairing button.
const MAX_QUEUE_ROWS: usize = 4;

/// The pairing button `[SPEC-NSH-170]`: this node's physical way to open its
/// mesh pairing window, in the row below the queue. Its outcome is written
/// to the right of it, at `PAIR_NOTE_X`.
const PAIR_X: (i32, i32) = (8, 150);
const PAIR_ROW_Y: (i32, i32) = (280, 316);
const PAIR_NOTE_X: i32 = 162;
/// How long a press's outcome stays written beside the button.
const PAIR_NOTE_SECS: u64 = 10;
/// The appliance's root helper, the same path the player runs it by
/// (`lempi_player::bluetooth::HELPER`, which this binary's features do not
/// build).
const BTCTL: &str = "/usr/local/bin/lempi-btctl";

/// A queue row's three action buttons, right-aligned: "-"/"+" (sooner/
/// later) grouped together since both reorder, "X" (remove) set apart at
/// the far edge so reordering and deleting are not adjacent under a thumb.
const QUEUE_BTN_MINUS: (i32, i32) = (356, 390);
const QUEUE_BTN_PLUS: (i32, i32) = (394, 428);
const QUEUE_BTN_X: (i32, i32) = (436, 472);

fn queue_row_y(row: usize) -> (i32, i32) {
    let y0 = QUEUE_TOP_Y + row as i32 * (QUEUE_ROW_HEIGHT + QUEUE_ROW_GAP);
    (y0, y0 + QUEUE_ROW_HEIGHT)
}

/// `pair`: the outcome of a recent press of the pairing button, if any --
/// `Some(true)` opened the window, `Some(false)` could not.
fn render_settings(display: &mut impl Panel, snap: &ClientSnapshot, pair: Option<bool>) -> Result<(), std::convert::Infallible> {
    Rectangle::new(Point::zero(), display.bounding_box().size)
        .into_styled(PrimitiveStyle::with_fill(LCD_BG))
        .draw(display)?;

    draw_text(display, "SETTINGS", 8, 20);

    draw_button_winamp(display, VOL_DOWN_X.0, VOL_DOWN_X.1, VOL_ROW_Y.0, VOL_ROW_Y.1, "VOL -", false)?;
    draw_button_winamp(display, VOL_UP_X.0, VOL_UP_X.1, VOL_ROW_Y.0, VOL_ROW_Y.1, "VOL +", false)?;
    draw_text_centered(
        display,
        &format!("{:+.0}dB", snap.volume_db),
        Point::new((VOL_DOWN_X.1 + VOL_UP_X.0) / 2, (VOL_ROW_Y.0 + VOL_ROW_Y.1) / 2),
        LCD_GREEN,
    );

    draw_text(display, "NEXT UP", 8, 88);
    for (row, item) in snap.queue.iter().take(MAX_QUEUE_ROWS).enumerate() {
        let (y0, y1) = queue_row_y(row);
        let label = match &item.artist {
            Some(a) => format!("{} - {}", item.title, a),
            None => item.title.clone(),
        };
        draw_text(display, &truncate_display(&normalize_for_display(&label), 34), 8, y1 - 12);
        if !item.is_shutdown {
            draw_button_winamp(display, QUEUE_BTN_MINUS.0, QUEUE_BTN_MINUS.1, y0, y1, "-", false)?;
            draw_button_winamp(display, QUEUE_BTN_PLUS.0, QUEUE_BTN_PLUS.1, y0, y1, "+", false)?;
        }
        draw_button_winamp(display, QUEUE_BTN_X.0, QUEUE_BTN_X.1, y0, y1, "X", false)?;
    }

    draw_button_winamp(display, PAIR_X.0, PAIR_X.1, PAIR_ROW_Y.0, PAIR_ROW_Y.1, "PAIR", pair == Some(true))?;
    // What the press did, not what it was meant to do: the helper's own
    // answer, so a refusal reads as one `[GOV-SRC-040]`.
    let note = match pair {
        Some(true) => "pairing open",
        Some(false) => "could not open pairing",
        None => "",
    };
    if !note.is_empty() {
        draw_text(display, note, PAIR_NOTE_X, PAIR_ROW_Y.1 - 12);
    }

    draw_audio_icon(display, snap.warped(), LCD_BG, AUDIO_HIT_Y1_SETTINGS)?;
    draw_gear(display, LCD_BG)?;
    draw_power(display, snap.power.as_deref(), LCD_BG)?;
    Ok(())
}

// ---------------------------------------------------------------------
// Audio: tempo, pitch, the A-B loop and the equalizer's presets
// `[SPEC-FBUI-110]` -- the controls of `[SPEC-TPL-120]` and `[SPEC-EQ-085]`,
// abbreviated to what a fingertip can press. Reached by the fader icon left
// of the power glyph, on all three pages; the same icon, gold, is how every
// page shows that tempo, pitch or a loop is in use.
// ---------------------------------------------------------------------

/// The fader icon's hit region: as tall as the gear's, from its left edge to
/// the power glyph's.
const AUDIO_HIT_X0: i32 = 348;
const AUDIO_HIT_X1: i32 = POWER_X0 - 1;
/// On Settings the volume row starts at y 30, under this corner, and keeps
/// what lies below this.
const AUDIO_HIT_Y1_SETTINGS: i32 = 28;

/// Three faders. Green chrome like the gear; gold -- "whatever is currently
/// on" -- while tempo, pitch or a loop is in use, which is the one thing a
/// person must be able to see from Now Playing: a loop holds its passage
/// until it is cleared `[SPEC-TPL-030]`.
///
/// `bottom` is where its corner ends: the gear's height, or on Settings the
/// shorter `AUDIO_HIT_Y1_SETTINGS`, so its backdrop does not cut through the
/// volume row's outline below it.
fn draw_audio_icon(display: &mut impl Panel, warped: bool, backdrop: Rgb565, bottom: i32) -> Result<(), std::convert::Infallible> {
    Rectangle::new(Point::new(AUDIO_HIT_X0, 0), Size::new((POWER_X0 - AUDIO_HIT_X0) as u32, bottom as u32))
        .into_styled(PrimitiveStyle::with_fill(backdrop))
        .draw(display)?;
    let color = if warped { MULIB_GOLD } else { LCD_GREEN };
    let (top, foot) = (bottom / 6, bottom - bottom / 6);
    for (x, knob) in [(359, 0.7), (371, 0.25), (383, 0.5)] {
        Line::new(Point::new(x, top), Point::new(x, foot))
            .into_styled(PrimitiveStyle::with_stroke(color, 2))
            .draw(display)?;
        let at = top + ((foot - top) as f64 * knob) as i32;
        Rectangle::new(Point::new(x - 4, at - 3), Size::new(10, 7))
            .into_styled(PrimitiveStyle::with_fill(color))
            .draw(display)?;
    }
    Ok(())
}

/// A label column, then four columns of buttons, five rows of them.
const AUDIO_LABEL_X: i32 = 8;
const AUDIO_COLS: [(i32, i32); 4] = [(80, 172), (178, 270), (276, 368), (374, 472)];
const AUDIO_ROWS: [(i32, i32); 5] = [(50, 94), (100, 144), (150, 194), (200, 244), (250, 294)];
const AUDIO_NOTE_Y: i32 = 312;

/// Where `-` and `+` step to: the skin's presets, and the range's ends
/// `[SPEC-TPL-040]`. A tempo set elsewhere between two steps moves to the
/// next one in the direction pressed.
const TEMPO_STEPS: [u32; 11] = [250, 500, 750, 900, 1000, 1100, 1250, 1500, 2000, 3000, 4000];
const PITCH_STEP: i32 = 100;
const PITCH_LIMIT: i32 = 1200;

/// The equalizer's buttons, rows 4 and 5: `off`, then the server's presets
/// by name `[SPEC-EQ-085]`. The curves are not here -- only names, and the
/// label a 92-pixel button has room for. A test holds these to
/// `lempi_core::eq::PRESETS`.
const EQ_BUTTONS: [(&str, &str); 6] = [
    ("off", "OFF"),
    ("flat", "FLAT"),
    ("bass", "BASS"),
    ("vocal", "VOCAL"),
    ("treble", "TREBLE"),
    ("loudness", "LOUD"),
];

/// `EQ_BUTTONS[i]`'s place: four on row 4, the rest on row 5.
fn eq_button_at(i: usize) -> ((i32, i32), (i32, i32)) {
    (AUDIO_COLS[i % 4], AUDIO_ROWS[3 + i / 4])
}

fn tempo_step(current: u32, up: bool) -> u32 {
    if up {
        TEMPO_STEPS.iter().copied().find(|&s| s > current).unwrap_or(TEMPO_STEPS[TEMPO_STEPS.len() - 1])
    } else {
        TEMPO_STEPS.iter().rev().copied().find(|&s| s < current).unwrap_or(TEMPO_STEPS[0])
    }
}

fn pitch_step(current: i32, up: bool) -> i32 {
    (current + if up { PITCH_STEP } else { -PITCH_STEP }).clamp(-PITCH_LIMIT, PITCH_LIMIT)
}

fn fmt_tempo(milli: u32) -> String {
    format!("{:.2}x", milli as f64 / 1000.0)
}

/// Semitones; tenths where the skin's slider left a fraction of one.
fn fmt_pitch(cents: i32) -> String {
    if cents == 0 {
        "0 st".into()
    } else if cents % 100 == 0 {
        format!("{:+} st", cents / 100)
    } else {
        format!("{:+.1} st", cents as f64 / 100.0)
    }
}

/// Lempi-styled `[SPEC-FBUI-110]`: filled `--line`, a 2px `--dim` edge, `--fg`
/// text; inverted while `on`.
fn draw_button_lempi(display: &mut impl Panel, (x0, x1): (i32, i32), (y0, y1): (i32, i32), label: &str, on: bool) -> Result<(), std::convert::Infallible> {
    let rect = Rectangle::new(Point::new(x0, y0), Size::new((x1 - x0) as u32, (y1 - y0) as u32));
    let shape = RoundedRectangle::new(rect, CornerRadii::new(Size::new(LEMPI_RADIUS, LEMPI_RADIUS)));
    let (style, text) = if on {
        (PrimitiveStyle::with_fill(LEMPI_FG), LEMPI_BG)
    } else {
        (
            PrimitiveStyleBuilder::new().fill_color(LEMPI_LINE).stroke_color(LEMPI_DIM).stroke_width(2).build(),
            LEMPI_FG,
        )
    };
    shape.into_styled(style).draw(display)?;
    draw_text_centered_font(display, &BUTTON_FONT, label, Point::new((x0 + x1) / 2, (y0 + y1) / 2), text);
    Ok(())
}

fn draw_row_label(display: &mut impl Panel, text: &str, row: usize) {
    let (y0, y1) = AUDIO_ROWS[row];
    draw_text_color(display, text, AUDIO_LABEL_X, (y0 + y1) / 2 + 5, LEMPI_DIM);
}

/// Said in the row, where the buttons would be, so an absent control is not
/// mistaken for a broken one.
fn draw_not_offered(display: &mut impl Panel, row: usize) {
    let (y0, y1) = AUDIO_ROWS[row];
    draw_text_color(display, "not offered by this player", AUDIO_COLS[0].0, (y0 + y1) / 2 + 5, LEMPI_DIM);
}

/// The Audio page. Every control shows its state: the tempo and pitch it is
/// at, inverted when not neutral; where A and B are; which preset is in force,
/// or that the curve is a custom one made in the browser.
fn render_audio(display: &mut impl Panel, snap: &ClientSnapshot) -> Result<(), std::convert::Infallible> {
    Rectangle::new(Point::zero(), display.bounding_box().size)
        .into_styled(PrimitiveStyle::with_fill(LEMPI_BG))
        .draw(display)?;
    draw_text_color(display, "AUDIO", 8, 28, LEMPI_FG);

    draw_row_label(display, "TEMPO", 0);
    draw_row_label(display, "PITCH", 1);
    if snap.can_tempo_pitch {
        let t = snap.tempo();
        draw_button_lempi(display, AUDIO_COLS[0], AUDIO_ROWS[0], "-", false)?;
        draw_button_lempi(display, AUDIO_COLS[1], AUDIO_ROWS[0], &fmt_tempo(t), t != 1000)?;
        draw_button_lempi(display, AUDIO_COLS[2], AUDIO_ROWS[0], "+", false)?;
        let p = snap.pitch_cents;
        draw_button_lempi(display, AUDIO_COLS[0], AUDIO_ROWS[1], "-1 st", false)?;
        draw_button_lempi(display, AUDIO_COLS[1], AUDIO_ROWS[1], &fmt_pitch(p), p != 0)?;
        draw_button_lempi(display, AUDIO_COLS[2], AUDIO_ROWS[1], "+1 st", false)?;
    } else {
        draw_not_offered(display, 0);
        draw_not_offered(display, 1);
    }

    draw_row_label(display, "LOOP", 2);
    if snap.can_ab_loop {
        let AbLoopView { a_ms, b_ms } = snap.ab_loop;
        let a = a_ms.map_or("SET A".to_string(), |ms| format!("A {}", fmt_time(ms)));
        let b = b_ms.map_or("SET B".to_string(), |ms| format!("B {}", fmt_time(ms)));
        draw_button_lempi(display, AUDIO_COLS[0], AUDIO_ROWS[2], &a, a_ms.is_some())?;
        draw_button_lempi(display, AUDIO_COLS[1], AUDIO_ROWS[2], &b, b_ms.is_some())?;
        draw_button_lempi(display, AUDIO_COLS[2], AUDIO_ROWS[2], "CLEAR", false)?;
    } else {
        draw_not_offered(display, 2);
    }

    draw_row_label(display, "EQ", 3);
    if snap.can_eq {
        let current = snap.eq_preset.as_deref().unwrap_or("off");
        for (i, (name, label)) in EQ_BUTTONS.iter().enumerate() {
            let (cols, rows) = eq_button_at(i);
            draw_button_lempi(display, cols, rows, label, current == *name)?;
        }
        if current == "custom" {
            let (y0, y1) = AUDIO_ROWS[4];
            draw_text_color(display, "custom curve", AUDIO_COLS[2].0 + 6, (y0 + y1) / 2 + 5, LEMPI_GREEN);
        }
    } else {
        draw_not_offered(display, 3);
    }

    draw_text_color(display, "tempo, pitch and loop reset at the next track", 8, AUDIO_NOTE_Y, LEMPI_DIM);

    draw_audio_icon(display, snap.warped(), LEMPI_BG, GEAR_HIT_Y1)?;
    draw_gear(display, LEMPI_BG)?;
    draw_power(display, snap.power.as_deref(), LEMPI_BG)?;
    Ok(())
}

/// An explicit, unmistakably different screen for "not connected to
/// `lempi` right now" `[SPEC036]` §7's reconnection gap -- no buttons, no
/// seek bar, none of `render`'s controls that would be silently inert if
/// tapped while there is nothing to send them to. Deliberately not just
/// `render` called with a default/empty `ClientSnapshot`: that would read
/// as "connected, and nothing happens to be playing," which is a real,
/// different state a person could otherwise not tell apart from this one.
/// Page-agnostic on purpose -- there is nothing to page between when there
/// is nothing to show.
fn render_disconnected(display: &mut impl Panel) -> Result<(), std::convert::Infallible> {
    let size = display.bounding_box().size;
    let (w, h) = (size.width as i32, size.height as i32);
    Rectangle::new(Point::zero(), size)
        .into_styled(PrimitiveStyle::with_fill(LCD_BG))
        .draw(display)?;
    let center_x = w / 2;
    for (line, y) in [("-- disconnected --", h / 2 - 12), ("reconnecting...", h / 2 + 12)] {
        if let Err(e) = TEXT_FONT.render_aligned(
            line,
            Point::new(center_x, y),
            VerticalPosition::Baseline,
            HorizontalAlignment::Center,
            FontColor::Transparent(LCD_GREEN),
            display,
        ) {
            tracing::warn!("fbui: could not render {line:?}: {e:?}");
        }
    }
    Ok(())
}

/// Open this node's mesh pairing window `[SPEC-NSH-170]`: the Pair button.
/// Through the root helper, as any other local "button" does, rather than a
/// signal sent from here -- one path to the player, and it names it exactly
/// `[SPEC-NSH-160]`. True only when the helper *says* it signalled the player:
/// its answer, not its exit status, is what is checked.
fn open_pairing_window() -> bool {
    match std::process::Command::new("sudo").args(["-n", BTCTL, "mesh-pair"]).output() {
        Ok(out) if String::from_utf8_lossy(&out.stdout).contains("\"ok\":true") => {
            tracing::info!("fbui: pairing window opened");
            true
        }
        Ok(out) => {
            tracing::warn!("fbui: pairing window not opened: {}", String::from_utf8_lossy(&out.stdout).trim());
            false
        }
        Err(e) => {
            tracing::warn!("fbui: could not run {BTCTL}: {e}");
            false
        }
    }
}

/// A pairing-button outcome still recent enough to show.
fn pair_shown(note: Option<(bool, std::time::Instant)>) -> Option<bool> {
    note.filter(|(_, at)| at.elapsed() < std::time::Duration::from_secs(PAIR_NOTE_SECS)).map(|(ok, _)| ok)
}

/// Fire-and-forget POST to `lempi`'s existing control API `[SPEC-FBUI-015]`
/// -- a hand-rolled HTTP/1.1 request over a raw TCP socket rather than a
/// client crate. `hyper` is already in this workspace's dependency tree
/// (`axum` pulls it in), but wiring its client builder for three
/// fire-and-forget local requests is more code than the lines below, for
/// no behavior this UI needs -- same "every dependency is a memory
/// decision" reasoning `Cargo.toml` already states for `reqwest`.
async fn http_post(addr: String, path: String) {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let mut stream = match tokio::net::TcpStream::connect(&addr).await {
        Ok(s) => s,
        Err(e) => {
            tracing::warn!("fbui: {path} failed to connect to {addr}: {e}");
            return;
        }
    };
    let req = format!("POST {path} HTTP/1.1\r\nHost: {addr}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
    if let Err(e) = stream.write_all(req.as_bytes()).await {
        tracing::warn!("fbui: {path} failed to send: {e}");
        return;
    }
    let mut buf = [0u8; 32];
    let _ = stream.read(&mut buf).await; // drain enough for a clean close; response body unused
}

/// A minimal HTTP/1.1 GET, for the one request this UI needs a real
/// response body from -- `[SPEC-FBUI-015]`'s `/art/:passage_id`. Same
/// "hand-rolled beats a client crate for one route" reasoning as
/// `http_post`. `Connection: close` means the server closes once it's
/// done, so reading to EOF is a correct, complete read of the whole
/// response without parsing `Content-Length` or handling chunked
/// transfer-encoding -- axum/hyper honor the header, and this is a
/// loopback connection to a server this project controls, not an
/// arbitrary one on the open internet.
async fn http_get(addr: &str, path: &str) -> Option<Vec<u8>> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let mut stream = tokio::net::TcpStream::connect(addr).await.ok()?;
    let req = format!("GET {path} HTTP/1.1\r\nHost: {addr}\r\nConnection: close\r\n\r\n");
    stream.write_all(req.as_bytes()).await.ok()?;
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).await.ok()?;
    let header_end = raw.windows(4).position(|w| w == b"\r\n\r\n")? + 4;
    let status_line = std::str::from_utf8(&raw[..header_end]).ok()?;
    if !status_line.starts_with("HTTP/1.1 200") && !status_line.starts_with("HTTP/1.0 200") {
        return None; // 404 (no art for this passage) or anything else -- not an error to log, just "no art"
    }
    Some(raw[header_end..].to_vec())
}

/// Decodes whatever `/art/:passage_id` returned (real production art is
/// JPEG or PNG, never anything else -- `stored_art` in `library.rs`;
/// `image::load_from_memory` sniffs the format from magic bytes so this
/// doesn't need to trust or even look at the `Content-Type` header) and
/// resizes it once to exactly `ART_SIZE`x`ART_SIZE`, so `draw_art` is a
/// flat pixel copy on every redraw rather than a resize on every redraw.
/// Run inside `spawn_blocking` by its caller -- decode and resize are real
/// CPU work, not something to do on the same task that's also servicing
/// the websocket and touch channel.
///
/// `FilterType::Nearest`, not a smoother filter -- `image`'s own resize
/// blends source pixels directly in their stored, gamma-encoded (sRGB)
/// values rather than in linear light, which systematically biases a
/// large downscale *brighter* than correct (averaging gamma-encoded values
/// is not the same as averaging light). Reported as album art looking
/// "washed out, too white" -- real production art is confirmed plain
/// JPEG, and this project's own flat-filled UI colors are never
/// interpolated at all, so a resize-blending bias is the one thing that
/// explains it happening only here. `Nearest` picks one source pixel
/// untouched per output pixel, so there is no blend to be biased --
/// correctness over smoothness for a 100x100 thumbnail on a panel this
/// coarse, where the trade is barely visible either way.
fn decode_and_resize(bytes: &[u8]) -> Option<Vec<Rgb565>> {
    let img = image::load_from_memory(bytes).ok()?;
    let resized = img.resize_exact(ART_SIZE, ART_SIZE, image::imageops::FilterType::Nearest).to_rgb8();
    Some(resized.pixels().map(|p| Rgb565::new(p.0[0] >> 3, p.0[1] >> 2, p.0[2] >> 3)).collect())
}

/// Fetches and decodes one passage's cover art, once per passage change --
/// `[SPEC-FBUI-040]`'s own "once per track, not continuously" reasoning
/// for why a full bitmap blit is affordable here at all. `None` covers
/// both "no art exists for this passage" (a 404, most of this all-radio
/// sample library) and "art existed but failed to decode" identically --
/// this UI has nothing more useful to do with either than show no art.
async fn fetch_art(addr: String, passage_id: i64) -> Option<Vec<Rgb565>> {
    let bytes = http_get(&addr, &format!("/art/{passage_id}")).await?;
    tokio::task::spawn_blocking(move || decode_and_resize(&bytes)).await.ok().flatten()
}

/// `ws://host:port/ws` -> `host:port`, so touch commands go to the same
/// `lempi` this UI's websocket is already talking to, without a second
/// address to keep in sync by hand.
fn http_addr_from_ws_url(ws_url: &str) -> String {
    let after_scheme = ws_url.split_once("://").map(|(_, rest)| rest).unwrap_or(ws_url);
    after_scheme.split('/').next().unwrap_or(after_scheme).to_string()
}

// ---------------------------------------------------------------------
// Hit-testing: maps an already-calibrated screen point, plus the current
// page and snapshot (programme ids and queue qids are dynamic, unlike the
// fixed transport regions), to whichever action it lands on.
// ---------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq)]
enum Zone {
    Gear,
    PlayPause,
    Skip,
    Program(i64),
    VolDown,
    VolUp,
    Seek,
    QueueSooner(u64),
    QueueRemove(u64),
    QueueLater(u64),
    Pair,
    Audio,
    TempoDown,
    TempoNeutral,
    TempoUp,
    PitchDown,
    PitchNeutral,
    PitchUp,
    LoopA,
    LoopB,
    LoopClear,
    EqPreset(&'static str),
}

fn in_rect(x: i32, y: i32, x0: i32, x1: i32, y0: i32, y1: i32) -> bool {
    (x0..=x1).contains(&x) && (y0..=y1).contains(&y)
}

/// Takes screen coordinates, not raw touch ADC values -- calibration
/// `[SPEC-FBUI-050]` is applied by the caller first, so this function
/// never needs to know this panel's rotation or axis convention.
fn hit_test(page: Page, sx: f64, sy: f64, snap: &ClientSnapshot) -> Option<Zone> {
    let (x, y) = (sx as i32, sy as i32);
    if x >= GEAR_HIT_X0 && y <= GEAR_HIT_Y1 {
        return Some(Zone::Gear);
    }
    let audio_y1 = if page == Page::Settings { AUDIO_HIT_Y1_SETTINGS } else { GEAR_HIT_Y1 };
    if (AUDIO_HIT_X0..=AUDIO_HIT_X1).contains(&x) && y <= audio_y1 {
        return Some(Zone::Audio);
    }
    match page {
        Page::NowPlaying => {
            if (SEEKBAR_Y0..=SEEKBAR_Y1).contains(&y) {
                return Some(Zone::Seek);
            }
            if in_rect(x, y, GRID_XS[0].0, GRID_XS[0].1, ROW1_Y0, ROW1_Y1) {
                return Some(Zone::PlayPause);
            }
            if in_rect(x, y, GRID_XS[1].0, GRID_XS[1].1, ROW1_Y0, ROW1_Y1) {
                return Some(Zone::Skip);
            }
            for (i, &(x0, x1, y0, y1)) in PROGRAM_SLOTS.iter().enumerate() {
                if in_rect(x, y, x0, x1, y0, y1) {
                    return snap.programs.get(i).map(|p| Zone::Program(p.id));
                }
            }
            None
        }
        Page::Settings => {
            if in_rect(x, y, VOL_DOWN_X.0, VOL_DOWN_X.1, VOL_ROW_Y.0, VOL_ROW_Y.1) {
                return Some(Zone::VolDown);
            }
            if in_rect(x, y, VOL_UP_X.0, VOL_UP_X.1, VOL_ROW_Y.0, VOL_ROW_Y.1) {
                return Some(Zone::VolUp);
            }
            if in_rect(x, y, PAIR_X.0, PAIR_X.1, PAIR_ROW_Y.0, PAIR_ROW_Y.1) {
                return Some(Zone::Pair);
            }
            for (row, item) in snap.queue.iter().take(MAX_QUEUE_ROWS).enumerate() {
                let (y0, y1) = queue_row_y(row);
                if !item.is_shutdown && in_rect(x, y, QUEUE_BTN_MINUS.0, QUEUE_BTN_MINUS.1, y0, y1) {
                    return Some(Zone::QueueLater(item.qid));
                }
                if !item.is_shutdown && in_rect(x, y, QUEUE_BTN_PLUS.0, QUEUE_BTN_PLUS.1, y0, y1) {
                    return Some(Zone::QueueSooner(item.qid));
                }
                if in_rect(x, y, QUEUE_BTN_X.0, QUEUE_BTN_X.1, y0, y1) {
                    return Some(Zone::QueueRemove(item.qid));
                }
            }
            None
        }
        // Only what the page drew: a row the backend does not offer has no
        // buttons, so it has nothing to press either.
        Page::Audio => {
            let at = |col: usize, row: usize| {
                in_rect(x, y, AUDIO_COLS[col].0, AUDIO_COLS[col].1, AUDIO_ROWS[row].0, AUDIO_ROWS[row].1)
            };
            if snap.can_tempo_pitch {
                let rows = [
                    (0, [Zone::TempoDown, Zone::TempoNeutral, Zone::TempoUp]),
                    (1, [Zone::PitchDown, Zone::PitchNeutral, Zone::PitchUp]),
                ];
                for (row, zones) in rows {
                    for (col, zone) in zones.into_iter().enumerate() {
                        if at(col, row) {
                            return Some(zone);
                        }
                    }
                }
            }
            if snap.can_ab_loop {
                for (col, zone) in [Zone::LoopA, Zone::LoopB, Zone::LoopClear].into_iter().enumerate() {
                    if at(col, 2) {
                        return Some(zone);
                    }
                }
            }
            if snap.can_eq {
                for (i, (name, _)) in EQ_BUTTONS.iter().enumerate() {
                    let ((x0, x1), (y0, y1)) = eq_button_at(i);
                    if in_rect(x, y, x0, x1, y0, y1) {
                        return Some(Zone::EqPreset(name));
                    }
                }
            }
            None
        }
    }
}

/// Whichever page is showing, drawn whole.
fn render_page(display: &mut impl Panel, page: Page, snap: &ClientSnapshot, art: Option<&[Rgb565]>, pair: Option<bool>) -> Result<(), std::convert::Infallible> {
    match page {
        Page::NowPlaying => render_now_playing(display, snap, art),
        Page::Settings => render_settings(display, snap, pair),
        Page::Audio => render_audio(display, snap),
    }
}

// ---------------------------------------------------------------------
// Touch `[SPEC-FBUI-045]`: raw evdev, hand-parsed rather than a new crate.
// ---------------------------------------------------------------------

/// Finds whichever `/dev/input/eventN` the `ADS7846 Touchscreen` device
/// actually enumerated as, by name (`/sys/class/input/eventN/device/name`)
/// rather than a fixed index -- `[SPEC-FBUI-025]` originally confirmed this
/// at `event2`, but disabling `vc4-kms-v3d` for boot speed shifted evdev's
/// own enumeration order and moved it to `event0`, the same class of
/// fragility `FbDisplay::find_panel_path` exists to survive on the
/// framebuffer side.
fn find_touch_path() -> String {
    for n in 0..8 {
        let name_path = format!("/sys/class/input/event{n}/device/name");
        if std::fs::read_to_string(&name_path).map(|s| s.trim() == "ADS7846 Touchscreen").unwrap_or(false) {
            return format!("/dev/input/event{n}");
        }
    }
    "/dev/input/event2".to_string() // historically-confirmed fallback, names a real path to investigate
}

/// Where the calibration this module produces gets stored -- a C-partition
/// (state) artifact by `[SPEC-FBUI-055]`'s own reasoning, alongside
/// `listener.db`.
const CALIBRATION_PATH: &str = "/var/lempi/touch-calibration.toml";

const EV_SYN: u16 = 0x00;
const EV_KEY: u16 = 0x01;
const EV_ABS: u16 = 0x03;
const ABS_X: u16 = 0x00;
const ABS_Y: u16 = 0x01;
const BTN_TOUCH: u16 = 0x14a;

struct RawEvent {
    ev_type: u16,
    code: u16,
    value: i32,
}

/// `struct input_event` on 64-bit Linux is a fixed, documented 24-byte
/// layout: `struct timeval` (two `i64`s here, 16 bytes) followed by
/// `u16 type, u16 code, i32 value` (8 bytes). Parsed field-by-field from
/// raw bytes rather than an `unsafe` struct transmute -- same ABI, no
/// reliance on repr/padding guesses holding on whatever target this
/// cross-compiles for next.
fn read_event(file: &mut std::fs::File) -> std::io::Result<RawEvent> {
    use std::io::Read;
    let mut buf = [0u8; 24];
    file.read_exact(&mut buf)?;
    Ok(RawEvent {
        ev_type: u16::from_ne_bytes([buf[16], buf[17]]),
        code: u16::from_ne_bytes([buf[18], buf[19]]),
        value: i32::from_ne_bytes([buf[20], buf[21], buf[22], buf[23]]),
    })
}

struct TouchDevice {
    file: std::fs::File,
}

impl TouchDevice {
    fn open(path: &str) -> std::io::Result<Self> {
        Ok(Self { file: std::fs::File::open(path)? })
    }

    /// Blocks until one complete press-then-release is seen and returns the
    /// raw (x, y) reading, averaged over every sample reported while held
    /// down -- a resistive panel's ADC jitters visibly sample to sample,
    /// and calibration accuracy depends on this being stable, not on
    /// whichever single sample happened to arrive first or last.
    fn wait_for_tap(&mut self) -> std::io::Result<(f64, f64)> {
        let (mut cur_x, mut cur_y) = (0i32, 0i32);
        let (mut sum_x, mut sum_y, mut n) = (0i64, 0i64, 0i64);
        let mut touching = false;
        loop {
            let ev = read_event(&mut self.file)?;
            match (ev.ev_type, ev.code) {
                (t, c) if t == EV_ABS && c == ABS_X => cur_x = ev.value,
                (t, c) if t == EV_ABS && c == ABS_Y => cur_y = ev.value,
                (t, c) if t == EV_KEY && c == BTN_TOUCH => {
                    let now = ev.value != 0;
                    if now && !touching {
                        sum_x = 0;
                        sum_y = 0;
                        n = 0;
                    } else if !now && touching {
                        return Ok(if n > 0 {
                            (sum_x as f64 / n as f64, sum_y as f64 / n as f64)
                        } else {
                            // A tap quick enough that no ABS sample landed
                            // between press and release -- fall back to the
                            // last known position rather than blocking
                            // forever waiting for a sample that isn't coming.
                            (cur_x as f64, cur_y as f64)
                        });
                    }
                    touching = now;
                }
                (t, _) if t == EV_SYN && touching => {
                    sum_x += cur_x as i64;
                    sum_y += cur_y as i64;
                    n += 1;
                }
                _ => {}
            }
        }
    }
}

/// The standard 3-point affine touch calibration (screen = raw * matrix),
/// solved exactly from three known correspondences `[SPEC-FBUI-050]`. Not
/// invented here -- the same algorithm behind tslib and X11's evtouch.
/// Handles rotation, mirroring, scale and translation all at once, which
/// is exactly what's needed given the display's `rotate=90` is invisible
/// to the touch controller (see this file's module doc comment).
struct AffineCalibration {
    a: f64,
    b: f64,
    c: f64,
    d: f64,
    e: f64,
    f: f64,
}

impl AffineCalibration {
    fn from_three_points(screen: [(f64, f64); 3], raw: [(f64, f64); 3]) -> Self {
        let [(xs1, ys1), (xs2, ys2), (xs3, ys3)] = screen;
        let [(xr1, yr1), (xr2, yr2), (xr3, yr3)] = raw;

        let delta = (xr1 - xr3) * (yr2 - yr3) - (xr2 - xr3) * (yr1 - yr3);

        let a = ((xs1 - xs3) * (yr2 - yr3) - (xs2 - xs3) * (yr1 - yr3)) / delta;
        let b = ((xr1 - xr3) * (xs2 - xs3) - (xr2 - xr3) * (xs1 - xs3)) / delta;
        let c = (yr1 * (xr3 * xs2 - xr2 * xs3) + yr2 * (xr1 * xs3 - xr3 * xs1)
            + yr3 * (xr2 * xs1 - xr1 * xs2))
            / delta;

        let d = ((ys1 - ys3) * (yr2 - yr3) - (ys2 - ys3) * (yr1 - yr3)) / delta;
        let e = ((xr1 - xr3) * (ys2 - ys3) - (xr2 - xr3) * (ys1 - ys3)) / delta;
        let f = (yr1 * (xr3 * ys2 - xr2 * ys3) + yr2 * (xr1 * ys3 - xr3 * ys1)
            + yr3 * (xr2 * ys1 - xr1 * ys2))
            / delta;

        Self { a, b, c, d, e, f }
    }

    fn apply(&self, raw_x: f64, raw_y: f64) -> (f64, f64) {
        (self.a * raw_x + self.b * raw_y + self.c, self.d * raw_x + self.e * raw_y + self.f)
    }

    /// A human-readable flat file, not a database table `[SPEC-FBUI-055]`
    /// -- six floats have no relational structure worth a schema for.
    /// `.toml`-named and `key = value`-shaped so it reads as valid TOML to
    /// a person or a future tool, without this binary needing the `toml`
    /// crate just to write six numbers `[REQ-HW-140]`'s "every dependency
    /// is a memory decision" reasoning.
    fn save(&self, path: &str) -> std::io::Result<()> {
        let contents = format!(
            "# Lempi touch calibration [SPEC-FBUI-050].\n\
             # Regenerate with `fbui --calibrate`; do not hand-edit.\n\
             a = {}\nb = {}\nc = {}\nd = {}\ne = {}\nf = {}\n",
            self.a, self.b, self.c, self.d, self.e, self.f
        );
        std::fs::write(path, contents)
    }

    fn load(path: &str) -> Option<Self> {
        let text = std::fs::read_to_string(path).ok()?;
        let mut v = std::collections::HashMap::new();
        for line in text.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let (key, val) = line.split_once('=')?;
            v.insert(key.trim().to_string(), val.trim().parse::<f64>().ok()?);
        }
        Some(Self {
            a: *v.get("a")?,
            b: *v.get("b")?,
            c: *v.get("c")?,
            d: *v.get("d")?,
            e: *v.get("e")?,
            f: *v.get("f")?,
        })
    }
}

/// Inset from the physical edges, not at them -- a resistive panel's own
/// accuracy degrades right at the bezel, a well-known property of the
/// technology rather than anything specific to this board. Three points
/// forming a wide triangle (not collinear), as the algorithm requires.
const CAL_POINTS: [(i32, i32); 3] = [(48, 32), (432, 32), (240, 288)];

fn draw_crosshair(display: &mut FbDisplay, x: i32, y: i32, index: usize) -> Result<(), std::convert::Infallible> {
    let (w, h) = (display.width as i32, display.height as i32);
    Rectangle::new(Point::zero(), Size::new(w as u32, h as u32))
        .into_styled(PrimitiveStyle::with_fill(LCD_BG))
        .draw(display)?;
    let stroke = PrimitiveStyle::with_stroke(LCD_GREEN, 2);
    Line::new(Point::new(x - 10, y), Point::new(x + 10, y)).into_styled(stroke).draw(display)?;
    Line::new(Point::new(x, y - 10), Point::new(x, y + 10)).into_styled(stroke).draw(display)?;
    draw_text(display, &format!("calibration {}/{} -- touch the +", index + 1, CAL_POINTS.len()), 8, h - 12);
    Ok(())
}

/// Walks the person through `CAL_POINTS`, one crosshair at a time, and
/// solves the affine transform from what they actually touched.
/// Re-triggerable `[SPEC-FBUI-050]`: nothing here is one-shot besides the
/// file it writes, so running this again with `--calibrate` simply
/// overwrites a stale or wrong calibration.
fn run_calibration(display: &mut FbDisplay, touch: &mut TouchDevice) -> std::io::Result<AffineCalibration> {
    let mut raw_pts = [(0.0, 0.0); 3];
    for (i, &(sx, sy)) in CAL_POINTS.iter().enumerate() {
        let _ = draw_crosshair(display, sx, sy, i);
        let (rx, ry) = touch.wait_for_tap()?;
        tracing::info!("fbui: calibration point {} -- screen ({sx},{sy}) -> raw ({rx},{ry})", i + 1);
        raw_pts[i] = (rx, ry);
    }
    let screen_pts = CAL_POINTS.map(|(x, y)| (x as f64, y as f64));
    Ok(AffineCalibration::from_three_points(screen_pts, raw_pts))
}

/// True if `a` and `b` differ in anything other than `position_ms` --
/// `main`'s redraw gate uses this to throttle the once-a-second position
/// tick to once per 5 seconds without also delaying an actual play/pause,
/// skip, programme, volume, or queue change, which must still redraw at
/// once. Compares via a modified clone rather than a hand-written
/// field list, so a future field addition to `ClientSnapshot` is caught
/// by this comparison automatically instead of silently being ignored.
fn differs_ignoring_position(a: &ClientSnapshot, b: &ClientSnapshot) -> bool {
    let mut a2 = a.clone();
    a2.position_ms = b.position_ms;
    a2 != *b
}

#[cfg(test)]
mod tests {
    use super::AffineCalibration;

    /// fbui's side of the snapshot contract `[GDE-HST-060]`: the fields its
    /// hand-copied structs read are the ones the fixture records for it, so
    /// the server's test is checking what fbui really needs. Linux-only, like
    /// fbui; CI's `--all-features` run is where it runs.
    #[test]
    fn what_fbui_reads_is_what_the_fixture_records() {
        use lempi_player::web::contract;
        let mine = contract::paths(
            contract::fields_of::<super::ClientSnapshot>(),
            &[
                ("queue[]", contract::fields_of::<super::QueueItem>()),
                ("programs[]", contract::fields_of::<super::ProgramItem>()),
                ("ab_loop", contract::fields_of::<super::AbLoopView>()),
            ],
        );
        assert_eq!(mine, contract::required("fbui"));
    }

    /// Synthetic hardware stand-in: a forward map with a 90-degree-style
    /// axis swap plus scale and offset, the same *shape* of transform
    /// `piscreen2r`'s rotate=90 plus an uncalibrated touch controller would
    /// actually produce. Calibrating from three points generated by this
    /// map, then applying the result to a fourth, held-out point, must
    /// recover that fourth point's real screen coordinates -- this is
    /// exactly the "verify at a 4th point" check the calibration
    /// literature recommends, run here without needing real hardware.
    fn forward(sx: f64, sy: f64) -> (f64, f64) {
        (2.0 * sy + 100.0, 3.0 * sx + 50.0)
    }

    #[test]
    fn recovers_a_rotated_scaled_transform() {
        let screen = [(48.0, 32.0), (432.0, 32.0), (240.0, 288.0)];
        let raw = screen.map(|(x, y)| forward(x, y));
        let cal = AffineCalibration::from_three_points(screen, raw);

        let (test_sx, test_sy) = (150.0, 200.0);
        let (test_rx, test_ry) = forward(test_sx, test_sy);
        let (got_sx, got_sy) = cal.apply(test_rx, test_ry);

        assert!((got_sx - test_sx).abs() < 1e-6, "x: got {got_sx}, want {test_sx}");
        assert!((got_sy - test_sy).abs() < 1e-6, "y: got {got_sy}, want {test_sy}");
    }

    /// A no-op sink -- only whether `TEXT_FONT.render` returns `Ok`/`Err`
    /// matters here, per the crate's own documented default behavior:
    /// "unknown chars will return an error" unless
    /// `with_ignore_unknown_chars` is set (it is not, for `TEXT_FONT`).
    struct NullDisplay;
    impl embedded_graphics::prelude::OriginDimensions for NullDisplay {
        fn size(&self) -> embedded_graphics::prelude::Size {
            embedded_graphics::prelude::Size::new(64, 64)
        }
    }
    impl embedded_graphics::prelude::DrawTarget for NullDisplay {
        type Color = embedded_graphics::pixelcolor::Rgb565;
        type Error = std::convert::Infallible;
        fn draw_iter<I>(&mut self, _pixels: I) -> Result<(), Self::Error>
        where
            I: IntoIterator<Item = embedded_graphics::Pixel<Self::Color>>,
        {
            Ok(())
        }
    }

    fn font_renders(ch: char) -> bool {
        text_renders(&super::TEXT_FONT, &ch.to_string())
    }

    fn text_renders(font: &super::FontRenderer, text: &str) -> bool {
        use embedded_graphics::prelude::Point;
        let mut nd = NullDisplay;
        font.render(text, Point::new(0, 20), super::VerticalPosition::Baseline, super::FontColor::Transparent(super::LCD_GREEN), &mut nd).is_ok()
    }

    /// Grounds `[SPEC-FBUI-030]`'s font choice in real data instead of a
    /// synthetic test string, and pins it down as a regression test: these
    /// exact characters were queried from a production `lempi.db`'s real
    /// artist/title text (`Bj\u{f6}rk`, `M\u{f6}tley Cr\u{fc}e`, `Fernando
    /// Mendon\u{e7}a`, `Auli\u{2bb}i Cravalho`, `Guns N\u{2019} Roses`,
    /// `The Go\u{2010}Go\u{2019}s`, a `\u{201c}`-quoted nickname). Split
    /// exactly the way the actual code path splits them: the accented
    /// Latin group must render directly (this is what justified choosing
    /// `u8g2_font_9x15_t_symbols` over plain ASCII), and the punctuation
    /// group must NOT render directly (this is what justifies
    /// `normalize_for_display` existing at all) but must render once
    /// normalized.
    #[test]
    fn font_coverage_matches_real_library_data() {
        for ch in ['\u{f6}', '\u{fc}', '\u{e9}', '\u{e7}'] {
            assert!(font_renders(ch), "expected TEXT_FONT to cover accented Latin {ch:?} directly");
        }
        for ch in ['\u{2018}', '\u{2019}', '\u{02bb}', '\u{201c}', '\u{2010}', '\u{2014}'] {
            assert!(!font_renders(ch), "expected TEXT_FONT to lack {ch:?} -- if this now fails, a font upgrade may let normalize_for_display drop this mapping");
            let normalized = super::normalize_for_display(&ch.to_string());
            assert!(font_renders(normalized.chars().next().unwrap()), "normalize_for_display({ch:?}) = {normalized:?} still doesn't render");
        }
    }

    /// `BUTTON_FONT` is deliberately ASCII-only (no bold+full-coverage
    /// variant of this font exists) -- this pins down that every label it
    /// is actually asked to draw is covered, using the real production
    /// programme names queried from lempiplay3's own deployed database
    /// (`Cool`, `Fun`, `Mellow`, `Soft`, `Prog`, `Light`, `Loud`,
    /// `Groove`), not an assumption that programme names are always
    /// simple English words.
    #[test]
    fn button_font_covers_every_real_label() {
        let labels = [
            "PLAY", "PAUSE", "SKIP", "VOL -", "VOL +", "-", "+", "X", "PAIR",
            "Cool", "Fun", "Mellow", "Soft", "Prog", "Light", "Loud", "Groove",
            // The Audio page's, every shape a value takes.
            "-1 st", "+1 st", "0 st", "+12 st", "-12 st", "-0.5 st", "0.25x", "1.25x", "4.00x",
            "SET A", "SET B", "A 12:34", "B 1:02:03", "CLEAR",
            "OFF", "FLAT", "BASS", "VOCAL", "TREBLE", "LOUD",
        ];
        for label in labels {
            assert!(text_renders(&super::BUTTON_FONT, label), "BUTTON_FONT could not render {label:?}");
        }
    }

    #[test]
    fn save_and_load_round_trip() {
        let cal = AffineCalibration { a: 1.5, b: -0.25, c: 10.0, d: 0.1, e: 2.0, f: -5.0 };
        let path = std::env::temp_dir().join("fbui-test-calibration.toml");
        let path = path.to_str().unwrap();
        cal.save(path).unwrap();
        let loaded = AffineCalibration::load(path).expect("load");
        std::fs::remove_file(path).ok();
        assert_eq!((cal.a, cal.b, cal.c, cal.d, cal.e, cal.f), (loaded.a, loaded.b, loaded.c, loaded.d, loaded.e, loaded.f));
    }

    #[test]
    fn truncate_display_leaves_short_strings_alone() {
        assert_eq!(super::truncate_display("short", 10), "short");
    }

    #[test]
    fn truncate_display_cuts_long_strings_with_an_ascii_ellipsis() {
        let out = super::truncate_display("a very long title indeed", 10);
        assert_eq!(out.chars().count(), 10);
        assert!(out.ends_with("..."));
    }

    /// The Audio page's EQ buttons are the server's presets by name, every
    /// one of them and nothing else, plus `off` `[SPEC-EQ-085]`.
    #[test]
    fn the_eq_buttons_are_the_servers_presets() {
        let mine: Vec<&str> = super::EQ_BUTTONS.iter().map(|(n, _)| *n).filter(|n| *n != "off").collect();
        let theirs: Vec<&str> = lempi_core::eq::PRESETS.iter().map(|(n, _)| *n).collect();
        assert_eq!(mine, theirs);
        assert_eq!(super::EQ_BUTTONS[0].0, lempi_core::eq::PRESET_OFF);
    }

    #[test]
    fn tempo_and_pitch_step_within_their_ranges() {
        use super::{pitch_step, tempo_step};
        assert_eq!(tempo_step(1000, true), 1100);
        assert_eq!(tempo_step(1000, false), 900);
        assert_eq!(tempo_step(1180, true), 1250, "set elsewhere, between steps: the next one up");
        assert_eq!(tempo_step(1180, false), 1100);
        assert_eq!(tempo_step(4000, true), 4000);
        assert_eq!(tempo_step(250, false), 250);
        assert_eq!(pitch_step(0, true), 100);
        assert_eq!(pitch_step(1200, true), 1200);
        assert_eq!(pitch_step(-1150, false), -1200);
    }

    fn audio_snap() -> super::ClientSnapshot {
        super::ClientSnapshot {
            can_tempo_pitch: true,
            can_ab_loop: true,
            can_eq: true,
            tempo_milli: 1250,
            pitch_cents: -200,
            ab_loop: super::AbLoopView { a_ms: Some(12_000), b_ms: Some(45_000) },
            eq_preset: Some("bass".into()),
            title: Some("Africa".into()),
            artist: Some("Toto".into()),
            album: Some("Toto IV".into()),
            playing: true,
            position_ms: 61_000,
            duration_ms: 295_000,
            ..Default::default()
        }
    }

    /// Every button the Audio page draws is pressed where it is drawn, and a
    /// row the player does not offer has nothing to press.
    #[test]
    fn the_audio_page_is_hit_where_it_is_drawn() {
        use super::{hit_test, Page, Zone, AUDIO_COLS, AUDIO_ROWS};
        let s = audio_snap();
        let mid = |col: usize, row: usize| {
            (((AUDIO_COLS[col].0 + AUDIO_COLS[col].1) / 2) as f64, ((AUDIO_ROWS[row].0 + AUDIO_ROWS[row].1) / 2) as f64)
        };
        let want = [
            ((0, 0), Zone::TempoDown), ((1, 0), Zone::TempoNeutral), ((2, 0), Zone::TempoUp),
            ((0, 1), Zone::PitchDown), ((1, 1), Zone::PitchNeutral), ((2, 1), Zone::PitchUp),
            ((0, 2), Zone::LoopA), ((1, 2), Zone::LoopB), ((2, 2), Zone::LoopClear),
            ((0, 3), Zone::EqPreset("off")), ((1, 3), Zone::EqPreset("flat")),
            ((2, 3), Zone::EqPreset("bass")), ((3, 3), Zone::EqPreset("vocal")),
            ((0, 4), Zone::EqPreset("treble")), ((1, 4), Zone::EqPreset("loudness")),
        ];
        for ((col, row), zone) in want {
            let (x, y) = mid(col, row);
            assert_eq!(hit_test(Page::Audio, x, y, &s), Some(zone), "col {col} row {row}");
        }
        assert_eq!(hit_test(Page::Audio, mid(3, 0).0, mid(3, 0).1, &s), None, "nothing drawn there");

        let none = super::ClientSnapshot::default();
        for row in 0..5 {
            for col in 0..4 {
                let (x, y) = mid(col, row);
                assert_eq!(hit_test(Page::Audio, x, y, &none), None, "not offered, col {col} row {row}");
            }
        }

        // The fader icon from every page; on Settings it yields the volume
        // row beneath it.
        for page in [Page::NowPlaying, Page::Audio, Page::Settings] {
            assert_eq!(hit_test(page, 370.0, 14.0, &s), Some(Zone::Audio), "{page:?}");
        }
        assert_eq!(hit_test(Page::Settings, 370.0, 40.0, &s), Some(Zone::VolUp));
        assert_eq!(hit_test(Page::NowPlaying, 370.0, 40.0, &s), Some(Zone::Audio));
        assert_eq!(hit_test(Page::Audio, 450.0, 20.0, &s), Some(Zone::Gear));
    }

    /// A panel in memory, for drawing a page where there is no hardware.
    struct MemPanel {
        px: Vec<super::Rgb565>,
    }
    const W: u32 = 480;
    const H: u32 = 320;
    impl MemPanel {
        fn new() -> Self {
            Self { px: vec![super::Rgb565::new(31, 0, 31); (W * H) as usize] }
        }
        fn at(&self, x: i32, y: i32) -> super::Rgb565 {
            self.px[(y as u32 * W + x as u32) as usize]
        }
        /// Saved as a PNG where `FBUI_PREVIEW_DIR` names a folder -- for a
        /// person to look at, since no assertion can say a page reads well.
        fn save(&self, name: &str) {
            use embedded_graphics::pixelcolor::RgbColor;
            let Ok(dir) = std::env::var("FBUI_PREVIEW_DIR") else { return };
            let img = image::RgbImage::from_fn(W, H, |x, y| {
                let c = self.px[(y * W + x) as usize];
                image::Rgb([(c.r() << 3) | (c.r() >> 2), (c.g() << 2) | (c.g() >> 4), (c.b() << 3) | (c.b() >> 2)])
            });
            img.save(std::path::Path::new(&dir).join(format!("{name}.png"))).expect("save preview");
        }
    }
    impl embedded_graphics::prelude::OriginDimensions for MemPanel {
        fn size(&self) -> embedded_graphics::prelude::Size {
            embedded_graphics::prelude::Size::new(W, H)
        }
    }
    impl embedded_graphics::prelude::DrawTarget for MemPanel {
        type Color = super::Rgb565;
        type Error = std::convert::Infallible;
        fn draw_iter<I>(&mut self, pixels: I) -> Result<(), Self::Error>
        where
            I: IntoIterator<Item = embedded_graphics::Pixel<Self::Color>>,
        {
            for embedded_graphics::Pixel(p, c) in pixels {
                if (0..W as i32).contains(&p.x) && (0..H as i32).contains(&p.y) {
                    self.px[(p.y as u32 * W + p.x as u32) as usize] = c;
                }
            }
            Ok(())
        }
    }

    /// Every page draws on the whole panel, and the Audio page shows its
    /// state where a person reads it: what is on is inverted, the fader icon
    /// is gold while tempo, pitch or a loop is in use `[SPEC-FBUI-110]`.
    #[test]
    fn the_pages_draw_and_show_what_is_in_use() {
        use super::*;
        let warped = audio_snap();
        let mut neutral = audio_snap();
        neutral.tempo_milli = 1000;
        neutral.pitch_cents = 0;
        neutral.ab_loop = AbLoopView::default();
        neutral.eq_preset = Some("off".into());

        let mut audio = MemPanel::new();
        render_audio(&mut audio, &warped).unwrap();
        audio.save("audio-warped");
        assert!(!audio.px.contains(&Rgb565::new(31, 0, 31)), "the Audio page leaves part of the panel undrawn");
        // A corner inside each button: filled, clear of its label.
        let inside = |p: &MemPanel, col: usize, row: usize| p.at(AUDIO_COLS[col].0 + 8, AUDIO_ROWS[row].0 + 8);
        assert_eq!(inside(&audio, 1, 0), LEMPI_FG, "a tempo off 1.00x is shown inverted");
        assert_eq!(inside(&audio, 0, 0), LEMPI_LINE);
        assert_eq!(inside(&audio, 0, 2), LEMPI_FG, "A, set");
        assert_eq!(inside(&audio, 2, 3), LEMPI_FG, "bass, in force");
        assert_eq!(inside(&audio, 0, 3), LEMPI_LINE, "off, not");
        assert_eq!(audio.at(371, 20), MULIB_GOLD, "the fader icon, gold while warped");

        let mut calm = MemPanel::new();
        render_audio(&mut calm, &neutral).unwrap();
        calm.save("audio-neutral");
        assert_eq!(inside(&calm, 1, 0), LEMPI_LINE, "1.00x is not on");
        assert_eq!(inside(&calm, 0, 3), LEMPI_FG, "off, in force");
        assert_eq!(calm.at(371, 20), LCD_GREEN);

        let mut custom = neutral.clone();
        custom.eq_preset = Some("custom".into());
        let mut panel = MemPanel::new();
        render_audio(&mut panel, &custom).unwrap();
        panel.save("audio-custom");
        assert!(EQ_BUTTONS.iter().enumerate().all(|(i, _)| {
            let ((x0, _), (y0, _)) = eq_button_at(i);
            panel.at(x0 + 8, y0 + 8) == LEMPI_LINE
        }), "a custom curve lights no preset");

        let absent = ClientSnapshot { title: Some("Played by MPD".into()), ..Default::default() };
        let mut panel = MemPanel::new();
        render_audio(&mut panel, &absent).unwrap();
        panel.save("audio-not-offered");

        let mut now = MemPanel::new();
        render_now_playing(&mut now, &warped, None).unwrap();
        now.save("now-playing-warped");
        assert_eq!(now.at(371, 20), MULIB_GOLD, "Now Playing shows a warp by the icon");
        assert!(now.px.contains(&MULIB_GOLD), "and by its summary");
        let mut now = MemPanel::new();
        render_now_playing(&mut now, &neutral, None).unwrap();
        now.save("now-playing-neutral");
        assert_eq!(now.at(371, 20), LCD_GREEN);

        let mut settings = MemPanel::new();
        render_settings(&mut settings, &warped, None).unwrap();
        settings.save("settings");
        assert_eq!(settings.at(371, 20), MULIB_GOLD);
    }

    #[test]
    fn the_summary_says_what_is_in_use() {
        let s = audio_snap();
        assert_eq!(s.audio_summary().as_deref(), Some("1.25x -2st A-B EQ"));
        assert!(super::ClientSnapshot::default().audio_summary().is_none(), "nothing in use, nothing said");
        let a = super::ClientSnapshot {
            ab_loop: super::AbLoopView { a_ms: Some(1), b_ms: None },
            ..Default::default()
        };
        assert_eq!(a.audio_summary().as_deref(), Some("A.."));
        assert!(a.warped(), "a loop half-set is already working with the passage");
    }

    fn snap(position_ms: u64, title: &str) -> super::ClientSnapshot {
        super::ClientSnapshot { position_ms, title: Some(title.to_string()), ..Default::default() }
    }

    #[test]
    fn differs_ignoring_position_ignores_only_that_field() {
        let a = snap(1000, "Same Title");
        let b = snap(6000, "Same Title");
        assert!(!super::differs_ignoring_position(&a, &b), "position alone must not count as a difference");

        let c = snap(1000, "Different Title");
        assert!(super::differs_ignoring_position(&a, &c), "a real change must still count, regardless of position");
    }
}

#[tokio::main]
async fn main() {
    let args = lempi_player::cli::specs::fbui::SPEC.parse();
    let force_calibrate = args.has(&lempi_player::cli::specs::fbui::CALIBRATE);
    let url = args.need(&lempi_player::cli::specs::fbui::URL).to_string();

    let mut display = match FbDisplay::open(&FbDisplay::find_panel_path()) {
        Ok(d) => d,
        Err(e) => {
            tracing::error!("fbui: {e}");
            std::process::exit(1);
        }
    };
    // Read out first: inside tracing's macros `display` names its own
    // formatting helper, which shadows this variable.
    let (width, height) = (display.width, display.height);
    tracing::info!("fbui: {width}x{height} framebuffer opened");

    // Calibrate once, re-triggerable, before anything else touches the
    // screen `[SPEC-FBUI-050]` -- resolves the "what happens before
    // calibration has ever run" gap `[SPEC036]` §7 named: first boot has
    // no file at CALIBRATION_PATH, so this always runs then, with no
    // separate manual step for anyone to forget. Run synchronously here,
    // before any async work starts (no websocket connection exists yet,
    // nothing else is scheduled) -- blocking the sole worker thread on a
    // blocking file read is exactly correct at this specific point, not
    // a shortcut around doing it properly.
    let calibration: Option<AffineCalibration> = if force_calibrate
        || AffineCalibration::load(CALIBRATION_PATH).is_none()
    {
        let touch_path = find_touch_path();
        match TouchDevice::open(&touch_path) {
            Ok(mut touch) => match run_calibration(&mut display, &mut touch) {
                Ok(cal) => {
                    match cal.save(CALIBRATION_PATH) {
                        Ok(()) => tracing::info!("fbui: calibration saved to {CALIBRATION_PATH}"),
                        Err(e) => tracing::error!("fbui: could not save calibration: {e}"),
                    }
                    Some(cal)
                }
                Err(e) => {
                    tracing::warn!("fbui: calibration aborted: {e}");
                    None
                }
            },
            Err(e) => {
                tracing::warn!("fbui: could not open {touch_path} for calibration: {e}");
                None
            }
        }
    } else {
        tracing::info!("fbui: using existing calibration at {CALIBRATION_PATH}");
        AffineCalibration::load(CALIBRATION_PATH)
    };

    // Touch is read on its own OS thread, blocking on the same evdev
    // protocol calibration above already used, and forwarded to `main`'s
    // async loop over a channel -- a blocking file read has no place
    // sharing this binary's one async worker thread once that loop also
    // has a websocket to service concurrently, unlike calibration's
    // one-time, nothing-else-running use of the same call above.
    let (touch_tx, mut touch_rx) = tokio::sync::mpsc::unbounded_channel::<(f64, f64)>();
    std::thread::spawn(move || loop {
        // Re-resolved on every retry, not just once -- cheap (a handful of
        // sysfs reads), and robust to a device that enumerates late rather
        // than one that moved.
        let touch_path = find_touch_path();
        match TouchDevice::open(&touch_path) {
            Ok(mut td) => loop {
                match td.wait_for_tap() {
                    Ok(pos) => {
                        if touch_tx.send(pos).is_err() {
                            return; // main has exited; nothing left to report to
                        }
                    }
                    Err(e) => {
                        tracing::warn!("fbui: touch read error: {e}");
                        break;
                    }
                }
            },
            Err(e) => tracing::error!("fbui: could not open {touch_path}: {e}"),
        }
        std::thread::sleep(std::time::Duration::from_secs(2));
    });

    // An explicit "disconnected" render before ever trying to connect, so
    // a `lempi` that is not up yet shows an honest state rather than
    // whatever garbage was in video memory at boot -- the same state
    // `render_disconnected` also shows on every later drop, resolving
    // `[SPEC036]` §7's reconnection-state gap for both cases now, not
    // just this first one.
    let _ = render_disconnected(&mut display);

    let http_addr = http_addr_from_ws_url(&url);
    let mut page = Page::NowPlaying;
    let mut last: Option<ClientSnapshot> = None;
    // Set in the past so the 5-second position-only throttle below is already
    // expired when the first snapshot arrives. Belt-and-braces: `last` is
    // `None` for that snapshot and the match below redraws unconditionally on
    // it anyway.
    //
    // `checked_sub`, not `-`: `Instant` counts from boot, and `Instant -
    // Duration` panics on underflow rather than saturating, so the plain
    // subtraction is a panic in any process that starts within five seconds of
    // boot. `fbui.service` starts at boot, on a node built around audio at
    // power-on, which makes that window narrow but real -- and the same
    // construct did fire in the engine's own tests on 2026-09-21. Saturating
    // to now costs at most one throttled position redraw.
    let mut last_position_redraw = std::time::Instant::now()
        .checked_sub(std::time::Duration::from_secs(5))
        .unwrap_or_else(std::time::Instant::now);
    // Keyed by passage_id, not refetched on every push -- `None` inside
    // the tuple is cached too, so a passage confirmed to have no art (most
    // of this all-radio sample library) is not re-requested on every
    // ~1s snapshot push for as long as it keeps playing.
    let mut art_cache: Option<(i64, Option<Vec<Rgb565>>)> = None;
    // The last press of the pairing button and what it did, shown beside the
    // button for `PAIR_NOTE_SECS` `[SPEC-NSH-170]`.
    let mut pair_note: Option<(bool, std::time::Instant)> = None;
    loop {
        tracing::info!("fbui: connecting to {url}");
        let ws = match tokio_tungstenite::connect_async(&url).await {
            Ok((ws, _)) => ws,
            Err(e) => {
                tracing::warn!("fbui: connect failed: {e}; retrying in 3s");
                tokio::time::sleep(std::time::Duration::from_secs(3)).await;
                continue;
            }
        };
        tracing::info!("fbui: connected");
        let (_, mut read) = ws.split();

        // Any taps that queued up while disconnected are stale by the time
        // a connection succeeds -- a button press firing a command the
        // instant reconnection completes would be surprising, not useful.
        while touch_rx.try_recv().is_ok() {}

        loop {
            tokio::select! {
                msg = read.next() => {
                    let msg = match msg {
                        Some(Ok(m)) => m,
                        Some(Err(e)) => {
                            tracing::warn!("fbui: ws error: {e}");
                            break;
                        }
                        None => break, // stream ended
                    };
                    let text = match msg.into_text() {
                        Ok(t) => t,
                        Err(_) => continue, // not a text frame; nothing this phase reads
                    };
                    let snap: ClientSnapshot = match serde_json::from_str(&text) {
                        Ok(s) => s,
                        Err(e) => {
                            tracing::warn!("fbui: snapshot parse failed: {e}");
                            continue;
                        }
                    };
                    // Throttled to once per 5s for a position-only change
                    // (the once-a-second push while a track plays), full
                    // speed for anything else -- a play/pause, skip,
                    // programme, volume, or queue change always redraws
                    // at once.
                    let due = last_position_redraw.elapsed() >= std::time::Duration::from_secs(5);
                    let should_redraw = match &last {
                        None => true,
                        Some(l) => differs_ignoring_position(l, &snap) || due,
                    };
                    if should_redraw {
                        // Refetched only on an actual passage change, not on
                        // every push -- position_ms alone changing must not
                        // trigger this `[SPEC-FBUI-040]`'s "once per track"
                        // reasoning is what makes a full bitmap blit
                        // affordable at all. Awaited inline rather than
                        // spawned+channeled: this only blocks the loop on
                        // the one push where the track actually changed, not
                        // on the far more frequent position-only updates.
                        if art_cache.as_ref().map(|(id, _)| *id) != snap.passage_id {
                            art_cache = match snap.passage_id {
                                Some(pid) => {
                                    let started = std::time::Instant::now();
                                    let art = fetch_art(http_addr.clone(), pid).await;
                                    tracing::info!("fbui: art fetch+decode for passage {pid} took {:?} ({})", started.elapsed(), if art.is_some() { "found" } else { "none" });
                                    Some((pid, art))
                                }
                                None => None,
                            };
                        }
                        let art = art_cache.as_ref().and_then(|(_, px)| px.as_deref());

                        // Timed, not just called `[SPEC036]` §8 phase 7: this
                        // is already a full-panel write every time (`render`
                        // fills the whole 480x320 canvas unconditionally, not
                        // a true per-region diff at the framebuffer level),
                        // so its own latency is the real answer to "how long
                        // does one full bitmap blit take" that phase 7 needs
                        // before deciding whether album art fits -- measured
                        // against real hardware, not estimated from the SPI
                        // clock rate on paper.
                        let started = std::time::Instant::now();
                        let result = render_page(&mut display, page, &snap, art, pair_shown(pair_note));
                        let elapsed = started.elapsed();
                        if elapsed > std::time::Duration::from_millis(20) {
                            tracing::info!("fbui: render took {elapsed:?}");
                        }
                        if let Err(e) = result {
                            // Infallible today, per DrawTarget::Error above --
                            // kept as a real match rather than `.unwrap()` so
                            // a future fallible backend (the `drm` path
                            // `[SPEC-FBUI-025]` leaves open) fails loudly
                            // here instead of panicking.
                            tracing::warn!("fbui: render failed: {e:?}");
                        }
                        last_position_redraw = std::time::Instant::now();
                    }
                    last = Some(snap);
                }
                Some((rx, ry)) = touch_rx.recv() => {
                    let Some(cal) = &calibration else { continue };
                    let (sx, sy) = cal.apply(rx, ry);
                    let snap = last.clone().unwrap_or_default();
                    match hit_test(page, sx, sy, &snap) {
                        Some(zone @ (Zone::Gear | Zone::Audio)) => {
                            // Each icon toggles its own page against Now
                            // Playing, from wherever it is pressed.
                            let own = if zone == Zone::Gear { Page::Settings } else { Page::Audio };
                            page = if page == own { Page::NowPlaying } else { own };
                            let art = art_cache.as_ref().and_then(|(_, px)| px.as_deref());
                            if let Err(e) = render_page(&mut display, page, &snap, art, pair_shown(pair_note)) {
                                tracing::warn!("fbui: render failed: {e:?}");
                            }
                        }
                        // The Audio page's controls `[SPEC-FBUI-110]`. Each
                        // sends and does not redraw: the snapshot that follows
                        // is what the page shows, so it shows what the player
                        // did rather than what was asked.
                        Some(zone @ (Zone::TempoDown | Zone::TempoNeutral | Zone::TempoUp)) => {
                            let t = match zone {
                                Zone::TempoDown => tempo_step(snap.tempo(), false),
                                Zone::TempoUp => tempo_step(snap.tempo(), true),
                                _ => 1000,
                            };
                            tokio::spawn(http_post(http_addr.clone(), format!("/tempo/{t}")));
                        }
                        Some(zone @ (Zone::PitchDown | Zone::PitchNeutral | Zone::PitchUp)) => {
                            let c = match zone {
                                Zone::PitchDown => pitch_step(snap.pitch_cents, false),
                                Zone::PitchUp => pitch_step(snap.pitch_cents, true),
                                _ => 0,
                            };
                            tokio::spawn(http_post(http_addr.clone(), format!("/pitch/{c}")));
                        }
                        // Where the listener is now, from the latest snapshot:
                        // at most a push old, as the browser's own marks are.
                        Some(Zone::LoopA) => {
                            tokio::spawn(http_post(http_addr.clone(), format!("/loop/a/{}", snap.position_ms)));
                        }
                        Some(Zone::LoopB) => {
                            tokio::spawn(http_post(http_addr.clone(), format!("/loop/b/{}", snap.position_ms)));
                        }
                        Some(Zone::LoopClear) => {
                            tokio::spawn(http_post(http_addr.clone(), "/loop/clear".to_string()));
                        }
                        Some(Zone::EqPreset(name)) => {
                            tokio::spawn(http_post(http_addr.clone(), format!("/audio/eq/preset/{name}")));
                        }
                        Some(Zone::PlayPause) => {
                            let playing = snap.playing;
                            let name = if playing { "pause" } else { "play" };
                            tokio::spawn(http_post(http_addr.clone(), format!("/command/{name}")));
                        }
                        Some(Zone::Skip) => {
                            tokio::spawn(http_post(http_addr.clone(), "/command/skip".to_string()));
                        }
                        Some(Zone::Program(id)) => {
                            // Never "auto" -- this appliance has no realtime
                            // clock and time-of-day selection has no
                            // relationship to a truck's driving hours. Only
                            // ever a specific id, persisted server-side by
                            // `set_program` so it survives a restart
                            // `[SPEC-DIR-185]`.
                            tokio::spawn(http_post(http_addr.clone(), format!("/program/{id}")));
                        }
                        Some(Zone::VolUp) => {
                            let db = snap.volume_db + 3.0;
                            tokio::spawn(http_post(http_addr.clone(), format!("/volume/{db}")));
                        }
                        Some(Zone::VolDown) => {
                            let db = snap.volume_db - 3.0;
                            tokio::spawn(http_post(http_addr.clone(), format!("/volume/{db}")));
                        }
                        Some(Zone::Seek) => {
                            if snap.duration_ms > 0 {
                                let frac = ((sx - 8.0) / (display.width as f64 - 16.0)).clamp(0.0, 1.0);
                                let ms = (frac * snap.duration_ms as f64).round() as u64;
                                // Named, so a touch that races a track change
                                // is refused rather than moving the next one.
                                let to = match snap.passage_id {
                                    Some(id) => format!("/seek/{id}/{ms}"),
                                    None => format!("/seek/{ms}"),
                                };
                                tokio::spawn(http_post(http_addr.clone(), to));
                            }
                        }
                        Some(Zone::QueueSooner(qid)) => {
                            tokio::spawn(http_post(http_addr.clone(), format!("/queue/{qid}/sooner")));
                        }
                        Some(Zone::QueueLater(qid)) => {
                            tokio::spawn(http_post(http_addr.clone(), format!("/queue/{qid}/later")));
                        }
                        Some(Zone::QueueRemove(qid)) => {
                            tokio::spawn(http_post(http_addr.clone(), format!("/queue/{qid}/remove")));
                        }
                        Some(Zone::Pair) => {
                            // Awaited, not fired and forgotten: the person at
                            // the screen is told what the press did. It is a
                            // sudo and a pkill, well under a second.
                            let ok = tokio::task::spawn_blocking(open_pairing_window).await.unwrap_or(false);
                            pair_note = Some((ok, std::time::Instant::now()));
                            if let Err(e) = render_settings(&mut display, &snap, pair_shown(pair_note)) {
                                tracing::warn!("fbui: render failed: {e:?}");
                            }
                        }
                        None => {}
                    }
                }
            }
        }
        tracing::info!("fbui: disconnected; reconnecting in 3s");
        let _ = render_disconnected(&mut display);
        // Cleared, not carried over: the next successful snapshot must
        // render even if it happens to be identical to the last one shown
        // before this drop -- otherwise the diff check in the loop above
        // would see no change and leave this disconnected screen up
        // indefinitely despite `lempi` being back and pushing real data
        // `[SPEC036]` §7's reconnection-state gap, the actual bug this
        // phase exists to close, not just the missing message on its own.
        last = None;
        tokio::time::sleep(std::time::Duration::from_secs(3)).await;
    }
}
