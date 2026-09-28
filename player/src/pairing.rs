//! The mesh pairing window `[SecurityReview3 R3]`.
//!
//! Mesh membership changes -- being invited, confirming an invitation, and
//! leaving -- are the levers a hostile host on the LAN would pull to detach a
//! player from the household mesh or enrol it into its own. The origin guard
//! stops a *browser* page doing it cross-site, but not a script talking to the
//! appliance's open LAN port directly. So those changes are refused unless a
//! **pairing window** is open, and the window can be opened only by a local,
//! physical act -- a `SIGUSR1` to this process (`lempi-btctl mesh-pair`, wired to a
//! button, the framebuffer UI, or an operator on the console). Nothing on the
//! LAN can send that signal, and no route opens the window. Its *state* is
//! reported by `GET /mesh`, to any LAN client, so the page can show the person
//! pairing whether it is open `[SecurityReview4 D1]`. One pairing closes it,
//! and a new length applies only from the next press `[SPEC-NSH-130..150]`.
//!
//! A phone's player serves loopback only `[REQ-AND-160]`, so every request to
//! it is already local; there the caller treats the window as unnecessary (see
//! `crate::web::settings`), and this module is simply never consulted.

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

/// Epoch-millis at which the open window ends, or 0 when closed. The end is
/// fixed when the window opens, so changing the length afterwards never
/// stretches a window already open `[SPEC-NSH-150]`; it applies from the next.
static OPEN_UNTIL_MS: AtomicU64 = AtomicU64::new(0);
/// The configured window length, cached so `open()` needs no database read
/// (and so the signal handler never has to touch one). Loaded at start and on
/// change from `player_settings` `pairing_window_secs`.
static WINDOW_SECS: AtomicU64 = AtomicU64::new(300);

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

/// Cache the window length (seconds). Called at start and when the setting
/// changes; it takes effect at the next press, not on a window already open.
pub fn set_window_secs(secs: u64) {
    WINDOW_SECS.store(secs.max(1), Ordering::Relaxed);
}

pub fn window_secs() -> u64 {
    WINDOW_SECS.load(Ordering::Relaxed)
}

/// Open the window now -- the button press. Async-signal-safe enough for a
/// `SIGUSR1` handler: one clock read, two atomic operations, no allocation.
pub fn press() {
    OPEN_UNTIL_MS.store(now_ms() + WINDOW_SECS.load(Ordering::Relaxed) * 1000, Ordering::Relaxed);
}

/// Close the window at once: one pairing per window `[SPEC-NSH-130]`, called
/// after a successful confirmation or leave.
pub fn close() {
    OPEN_UNTIL_MS.store(0, Ordering::Relaxed);
}

/// Whether the pairing window is currently open.
pub fn open() -> bool {
    now_ms() < OPEN_UNTIL_MS.load(Ordering::Relaxed)
}

/// Which window is open, as a value that differs for every window and is 0
/// when none is -- so an invitation can be tied to the window it began in, and
/// dropped when that window is gone `[SPEC-NSH-140]`.
pub fn window_id() -> u64 {
    if open() { OPEN_UNTIL_MS.load(Ordering::Relaxed) } else { 0 }
}

/// Seconds left in the window, 0 when closed.
pub fn remaining_secs() -> u64 {
    let end = OPEN_UNTIL_MS.load(Ordering::Relaxed);
    let now = now_ms();
    if now >= end { 0 } else { (end - now).div_ceil(1000) }
}

/// Register `SIGUSR1` as the pairing button. Unix only; on other platforms the
/// window can only be opened by a loopback caller, which is the appliance's
/// case that matters here.
#[cfg(unix)]
pub fn install_signal_handler() {
    extern "C" fn on_usr1(_sig: libc::c_int) {
        press();
    }
    // SAFETY: the handler does only an atomic store behind a clock read, both
    // async-signal-safe; registering it once at start races with nothing.
    unsafe {
        libc::signal(libc::SIGUSR1, on_usr1 as libc::sighandler_t);
    }
}

#[cfg(not(unix))]
pub fn install_signal_handler() {}

#[cfg(test)]
mod tests {
    use super::*;

    // One test, not several: `OPEN_UNTIL_MS`/`WINDOW_SECS` are process globals,
    // and separate `#[test]` functions run in parallel threads that would race
    // on them. This runs the scenarios in sequence instead.
    #[test]
    fn the_pairing_window_opens_closes_and_is_configurable() {
        set_window_secs(300);
        // Never pressed: closed.
        close();
        assert!(!open(), "unpressed is closed");
        assert_eq!(remaining_secs(), 0);
        assert_eq!(window_id(), 0, "no window, no id");
        // Pressed now: open, with time remaining, and an id.
        press();
        assert!(open(), "a press opens the window");
        assert!(remaining_secs() > 290 && remaining_secs() <= 300, "remaining near the full window");
        let id = window_id();
        assert_ne!(id, 0, "an open window has an id");
        // [SPEC-NSH-150]: a new length does not stretch or shorten the open window.
        set_window_secs(3600);
        assert!(remaining_secs() <= 300, "a longer length does not stretch the open window");
        set_window_secs(3);
        assert!(open() && remaining_secs() > 290, "a shorter length does not cut it either");
        assert_eq!(window_id(), id, "and it is the same window");
        // [SPEC-NSH-130]: one pairing closes it.
        close();
        assert!(!open(), "close() ends the window at once");
        assert_eq!(window_id(), 0);
        // The next press uses the length set meanwhile.
        press();
        assert!(remaining_secs() <= 3, "the next window has the new length");
        // An end in the past: closed.
        OPEN_UNTIL_MS.store(now_ms().saturating_sub(1000), Ordering::Relaxed);
        assert!(!open(), "the window closes at its end");
        assert_eq!(remaining_secs(), 0);
        set_window_secs(300);
        close();
    }
}
