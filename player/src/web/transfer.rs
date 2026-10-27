//! A bulk transfer to a node whose Wi-Fi shares a radio with its Bluetooth
//! speaker pauses the playback, and the end of it resumes the playback
//! `[PI3-AIM-170]`.
//!
//! Decided by the maintainer 2026-10-25
//! [PI031](../../../LempiPi/PI031-settling-the-shared-radio.md): on these Pis
//! every byte over Wi-Fi is radio time the speaker's audio does not get, and a
//! 690 MB export run under Bluetooth playback froze the Wi-Fi chip twice
//! `[PI3-FOUND-830]`. A pause the listener can see is better than stutter
//! they cannot explain, and it is put back when the transfer ends.
//!
//! Whoever starts the transfer asks for a hold first and releases it after:
//! `POST /transfer/hold?seconds=N`, then `POST /transfer/release/<id>`. The
//! player decides whether to pause -- only where the radio is shared, and only
//! if it is playing when the first hold arrives. When the last hold ends, the
//! playback a hold paused is resumed, unless the listener has already pressed
//! play. A hold that is never released ends by itself after its `seconds`, so a
//! transfer that dies cannot leave the music stopped for good. A sender that
//! knows how long it may take says so; one that does not gets `DEFAULT_SECS`.
//!
//! **`[PI3-AIM-175]` A pause alone does not free the radio, so a hold also
//! releases the output.** Paused, the player feeds the device silence so the
//! speaker's link survives `[PI3-OPEN-020]`, and an SBC packet of silence is
//! the size of one of music: the air carries the same stream. So on a shared
//! radio the first hold releases the device too -- whether or not it found the
//! music playing, since a listener who paused first wants the same transfer --
//! and the last one takes it back, paused or not.
//!
//! **Who may ask.** Anyone who can reach the player's web port, exactly as
//! for `/command/pause`: on an appliance, the household network, or the
//! access point's clients. The most a hold can do is that pause, for at most
//! `MAX_SECS`, after which the music plays again by itself.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde_json::json;

use crate::engine::{Command, EngineHandle};

use super::Ui;

/// How long a hold lasts when the asker does not say: fifteen minutes, past
/// any transfer the fleet makes today. The longest it may ask for is an hour.
const DEFAULT_SECS: u64 = 900;
const MAX_SECS: u64 = 3_600;

/// The holds in force, and whether one of them paused the playback or
/// released the output.
#[derive(Debug, Default)]
pub struct Holds {
    next: u64,
    live: Vec<u64>,
    paused: bool,
    quiet: bool,
}

/// What a new hold does.
#[derive(Debug, PartialEq)]
pub struct Begun {
    pub id: u64,
    pub pause: bool,
    /// Release the output `[PI3-AIM-175]`.
    pub quiet: bool,
}

/// What the end of a hold undoes: nothing until the last one ends.
#[derive(Debug, PartialEq)]
pub struct Ended {
    pub resume: bool,
    /// Take the output back.
    pub unquiet: bool,
}

impl Holds {
    /// A new hold, and whether to pause for it: only the first of a set, only
    /// while playing, only on a shared radio. A listener who presses play
    /// during a transfer has said what they want, and a second hold arriving
    /// does not overrule them. The first on a shared radio releases the output
    /// as well, playing or not.
    pub fn hold(&mut self, playing: bool, shared: bool) -> Begun {
        self.next += 1;
        let first = self.live.is_empty();
        let pause = first && playing && shared;
        let quiet = first && shared;
        self.paused |= pause;
        self.quiet |= quiet;
        self.live.push(self.next);
        Begun { id: self.next, pause, quiet }
    }

    /// Ends a hold. `None` for one not in force -- released already, expired,
    /// or never made by this process. Otherwise, if this was the last one,
    /// whether a hold had paused the playback, which is the cue to resume it,
    /// and whether one had released the output.
    pub fn release(&mut self, id: u64) -> Option<Ended> {
        let at = self.live.iter().position(|&i| i == id)?;
        self.live.remove(at);
        if !self.live.is_empty() {
            return Some(Ended { resume: false, unquiet: false });
        }
        Some(Ended { resume: std::mem::take(&mut self.paused), unquiet: std::mem::take(&mut self.quiet) })
    }
}

fn holds() -> &'static Mutex<Holds> {
    static HOLDS: OnceLock<Mutex<Holds>> = OnceLock::new();
    HOLDS.get_or_init(Default::default)
}

/// Is a bulk transfer under way -- any hold in force? The integrity check
/// waits for it on a shared radio `[GDE-INT-138]`.
pub fn in_force() -> bool {
    !holds().lock().unwrap_or_else(|e| e.into_inner()).live.is_empty()
}

/// The rule is `crate::radio`'s, shared with the integrity check.
async fn shared_radio(ui: &Ui) -> bool {
    let (bluetooth, db, library) = (ui.capabilities.bluetooth, ui.db.clone(), ui.library.clone());
    tokio::task::spawn_blocking(move || crate::radio::shares_radio(bluetooth, &db, &library))
        .await
        .unwrap_or(false)
}

/// End hold `id`, resuming the playback it paused. `None` when it was not in
/// force.
fn end(handle: &EngineHandle, id: u64, how: &str) -> Option<bool> {
    let ended = holds().lock().unwrap_or_else(|e| e.into_inner()).release(id)?;
    // The device first, then the music: a play that arrives first finds the
    // output released and reopens it by recovery anyway, but this way round
    // the line says what happened.
    if ended.unquiet {
        handle.send(Command::QuietOutput(false));
        tracing::info!("transfer hold {id} {how}: output taken back [PI3-AIM-175]");
    }
    if ended.resume && !handle.snapshot().playing {
        handle.send(Command::Play);
        tracing::info!("transfer hold {id} {how}: playback resumed [PI3-AIM-170]");
        return Some(true);
    }
    tracing::info!("transfer hold {id} {how}");
    Some(false)
}

/// `POST /transfer/hold?seconds=N`.
pub(super) async fn hold(State(ui): State<Ui>, Query(q): Query<HashMap<String, String>>) -> Response {
    let secs = q
        .get("seconds")
        .and_then(|s| s.parse::<u64>().ok())
        .unwrap_or(DEFAULT_SECS)
        .clamp(1, MAX_SECS);
    let shared = shared_radio(&ui).await;
    let playing = ui.handle.snapshot().playing;
    let Begun { id, pause: paused, quiet } = holds().lock().unwrap_or_else(|e| e.into_inner()).hold(playing, shared);
    if paused {
        ui.handle.send(Command::Pause);
        tracing::info!("transfer hold {id}: paused for a bulk transfer on the shared radio [PI3-AIM-170]");
    } else if quiet {
        tracing::info!("transfer hold {id} (not playing, shared radio true): nothing to pause");
    } else {
        tracing::info!("transfer hold {id} (playing {playing}, shared radio {shared}): nothing paused");
    }
    // After the pause, so the engine has stopped mixing before the device goes.
    if quiet {
        ui.handle.send(Command::QuietOutput(true));
    }
    let handle = ui.handle.clone();
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_secs(secs)).await;
        end(&handle, id, "expired");
    });
    axum::Json(json!({ "id": id, "paused": paused, "quiet": quiet, "shared_radio": shared, "seconds": secs }))
        .into_response()
}

/// `GET /transfer`: whether a hold is in force, and whether one holds the
/// shared radio -- what the speaker keeper stands down for `[PI3-AIM-176]`.
/// Off a shared radio a hold leaves the radio alone, and so does the keeper.
pub(super) async fn state() -> Response {
    let h = holds().lock().unwrap_or_else(|e| e.into_inner());
    axum::Json(json!({ "in_force": !h.live.is_empty(), "shared_radio_held": h.quiet })).into_response()
}

/// `POST /transfer/release/<id>`.
pub(super) async fn release(State(ui): State<Ui>, Path(id): Path<u64>) -> Response {
    match end(&ui.handle, id, "released") {
        Some(resumed) => axum::Json(json!({ "released": true, "resumed": resumed })).into_response(),
        None => (StatusCode::NOT_FOUND, axum::Json(json!({ "released": false }))).into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::{Ended, Holds};

    const NOTHING: Ended = Ended { resume: false, unquiet: false };

    #[test]
    fn one_hold_pauses_a_shared_radio_and_its_release_resumes() {
        let mut h = Holds::default();
        let b = h.hold(true, true);
        assert!(b.pause && b.quiet);
        assert_eq!(h.release(b.id), Some(Ended { resume: true, unquiet: true }));
    }

    #[test]
    fn nothing_is_touched_off_a_shared_radio() {
        let mut h = Holds::default();
        let b = h.hold(true, false);
        assert!(!b.pause && !b.quiet, "a radio of its own is neither paused nor released");
        assert_eq!(h.release(b.id), Some(NOTHING));
    }

    /// `[PI3-AIM-175]`: a listener who paused first wants the radio freed too.
    #[test]
    fn a_paused_player_on_a_shared_radio_is_released_but_not_started() {
        let mut h = Holds::default();
        let b = h.hold(false, true);
        assert!(!b.pause, "a paused player is left paused");
        assert!(b.quiet, "but its silence still costs the radio, so the output goes");
        assert_eq!(h.release(b.id), Some(Ended { resume: false, unquiet: true }),
                   "taken back at the end, and not started");
    }

    #[test]
    fn overlapping_holds_undo_once_at_the_last() {
        let mut h = Holds::default();
        let a = h.hold(true, true);
        assert!(a.pause && a.quiet);
        // Paused now, so the second sees a player that is not playing.
        let b = h.hold(false, true);
        assert!(!b.pause && !b.quiet, "the first already did both");
        assert_eq!(h.release(a.id), Some(NOTHING), "one still in force");
        assert_eq!(h.release(b.id), Some(Ended { resume: true, unquiet: true }), "the last undoes both");
    }

    #[test]
    fn a_second_hold_does_not_overrule_a_listener_who_pressed_play() {
        let mut h = Holds::default();
        let a = h.hold(true, true);
        let b = h.hold(true, true);
        assert!(!b.pause && !b.quiet, "playing again mid-transfer is the listener's choice");
        h.release(a.id);
        assert_eq!(h.release(b.id), Some(Ended { resume: true, unquiet: true }),
                   "the first hold's pause is still the one to undo");
    }

    #[test]
    fn a_hold_ends_once() {
        let mut h = Holds::default();
        let a = h.hold(true, true);
        assert_eq!(h.release(a.id), Some(Ended { resume: true, unquiet: true }));
        assert_eq!(h.release(a.id), None, "released twice, or expired and then released");
        assert_eq!(h.release(99), None, "never made");
        let b = h.hold(true, true);
        assert!(b.pause && b.quiet, "a set that has ended leaves nothing behind for the next");
    }
}
