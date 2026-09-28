//! The mesh pairing window `[SecurityReview3 R3]`.
//!
//! Mesh membership changes -- being invited, confirming an invitation, and
//! leaving -- are the levers a hostile host on the LAN would pull to detach a
//! player from the household mesh or enrol it into its own. The origin guard
//! stops a *browser* page doing it cross-site, but not a script talking to the
//! appliance's open LAN port directly. So those changes are refused unless a
//! **pairing window** is open, and the window can be opened only by a local,
//! physical act -- a `SIGUSR1` to this process (`lempi-btctl pair`, wired to a
//! button, the framebuffer UI, or an operator on the console). A page on the
//! LAN can neither send that signal nor read that it happened.
//!
//! A phone's player serves loopback only `[REQ-AND-160]`, so every request to
//! it is already local; there the caller treats the window as unnecessary (see
//! `crate::web::settings`), and this module is simply never consulted.

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

/// Epoch-millis of the last button press, or 0 for "never pressed".
static PRESSED_AT_MS: AtomicU64 = AtomicU64::new(0);
/// The configured window length, cached so `open()` needs no database read
/// (and so the signal handler never has to touch one). Loaded at start and on
/// change from `player_settings` `pairing_window_secs`.
static WINDOW_SECS: AtomicU64 = AtomicU64::new(300);

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

/// Cache the window length (seconds). Called at start and when the setting changes.
pub fn set_window_secs(secs: u64) {
    WINDOW_SECS.store(secs.max(1), Ordering::Relaxed);
}

pub fn window_secs() -> u64 {
    WINDOW_SECS.load(Ordering::Relaxed)
}

/// Open the window now -- the button press. Async-signal-safe enough for a
/// `SIGUSR1` handler: one clock read and one atomic store, no allocation.
pub fn press() {
    PRESSED_AT_MS.store(now_ms(), Ordering::Relaxed);
}

/// Whether the pairing window is currently open.
pub fn open() -> bool {
    let at = PRESSED_AT_MS.load(Ordering::Relaxed);
    at != 0 && now_ms() < at + WINDOW_SECS.load(Ordering::Relaxed) * 1000
}

/// Seconds left in the window, 0 when closed.
pub fn remaining_secs() -> u64 {
    let at = PRESSED_AT_MS.load(Ordering::Relaxed);
    if at == 0 {
        return 0;
    }
    let end = at + WINDOW_SECS.load(Ordering::Relaxed) * 1000;
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

    // One test, not several: `PRESSED_AT_MS`/`WINDOW_SECS` are process globals,
    // and separate `#[test]` functions run in parallel threads that would race
    // on them. This runs the scenarios in sequence instead.
    #[test]
    fn the_pairing_window_opens_closes_and_is_configurable() {
        set_window_secs(300);
        // Never pressed: closed.
        PRESSED_AT_MS.store(0, Ordering::Relaxed);
        assert!(!open(), "unpressed is closed");
        assert_eq!(remaining_secs(), 0);
        // Pressed now: open, with time remaining.
        press();
        assert!(open(), "a press opens the window");
        assert!(remaining_secs() > 290 && remaining_secs() <= 300, "remaining near the full window");
        // A press long ago: closed.
        PRESSED_AT_MS.store(now_ms() - 301 * 1000, Ordering::Relaxed);
        assert!(!open(), "the window closes after its length");
        assert_eq!(remaining_secs(), 0);
        // The length is configurable: the same press is open under 10s, closed under 3s.
        set_window_secs(10);
        PRESSED_AT_MS.store(now_ms() - 5 * 1000, Ordering::Relaxed);
        assert!(open(), "5s into a 10s window is open");
        set_window_secs(3);
        assert!(!open(), "5s into a 3s window is closed");
    }
}
