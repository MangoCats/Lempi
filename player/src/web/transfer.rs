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

/// The holds in force, and whether one of them paused the playback.
#[derive(Debug, Default)]
pub struct Holds {
    next: u64,
    live: Vec<u64>,
    paused: bool,
}

impl Holds {
    /// A new hold, and whether to pause for it: only the first of a set, only
    /// while playing, only on a shared radio. A listener who presses play
    /// during a transfer has said what they want, and a second hold arriving
    /// does not overrule them.
    pub fn hold(&mut self, playing: bool, shared: bool) -> (u64, bool) {
        self.next += 1;
        let pause = self.live.is_empty() && playing && shared;
        if pause {
            self.paused = true;
        }
        self.live.push(self.next);
        (self.next, pause)
    }

    /// Ends a hold. `None` for one not in force -- released already, expired,
    /// or never made by this process. Otherwise whether this was the last one
    /// and a hold had paused the playback, which is the cue to resume it.
    pub fn release(&mut self, id: u64) -> Option<bool> {
        let at = self.live.iter().position(|&i| i == id)?;
        self.live.remove(at);
        Some(self.live.is_empty() && std::mem::take(&mut self.paused))
    }
}

fn holds() -> &'static Mutex<Holds> {
    static HOLDS: OnceLock<Mutex<Holds>> = OnceLock::new();
    HOLDS.get_or_init(Default::default)
}

/// Whether this node's Wi-Fi shares a radio with the speaker it is playing
/// to: a node with the Bluetooth helper, unless it is plainly playing to a
/// DAC of its own. A stored `dac` on a node with no DAC is not believed
/// `[PI3-FOUND-820]`.
async fn shared_radio(ui: &Ui) -> bool {
    if !ui.capabilities.bluetooth {
        return false;
    }
    let db = ui.db.clone();
    let library = ui.library.clone();
    tokio::task::spawn_blocking(move || {
        let dac = crate::db::PlayerStore::open_split(&db, &library)
            .map(|s| s.get_active_output() == "dac")
            .unwrap_or(false);
        !(dac && crate::sink::local_output() == crate::sink::Tristate::Yes)
    })
    .await
    .unwrap_or(true)
}

/// End hold `id`, resuming the playback it paused. `None` when it was not in
/// force.
fn end(handle: &EngineHandle, id: u64, how: &str) -> Option<bool> {
    let resume = holds().lock().unwrap_or_else(|e| e.into_inner()).release(id)?;
    if resume && !handle.snapshot().playing {
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
    let (id, paused) = holds().lock().unwrap_or_else(|e| e.into_inner()).hold(playing, shared);
    if paused {
        ui.handle.send(Command::Pause);
        tracing::info!("transfer hold {id}: paused for a bulk transfer on the shared radio [PI3-AIM-170]");
    } else {
        tracing::info!("transfer hold {id} (playing {playing}, shared radio {shared}): nothing paused");
    }
    let handle = ui.handle.clone();
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_secs(secs)).await;
        end(&handle, id, "expired");
    });
    axum::Json(json!({ "id": id, "paused": paused, "shared_radio": shared, "seconds": secs })).into_response()
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
    use super::Holds;

    #[test]
    fn one_hold_pauses_a_shared_radio_and_its_release_resumes() {
        let mut h = Holds::default();
        let (id, pause) = h.hold(true, true);
        assert!(pause);
        assert_eq!(h.release(id), Some(true));
    }

    #[test]
    fn nothing_is_paused_off_a_shared_radio_or_when_not_playing() {
        let mut h = Holds::default();
        let (a, pause) = h.hold(true, false);
        assert!(!pause, "a radio of its own is not paused");
        assert_eq!(h.release(a), Some(false));
        let (b, pause) = h.hold(false, true);
        assert!(!pause, "a paused player is left as it is");
        assert_eq!(h.release(b), Some(false), "and is not started at the end");
    }

    #[test]
    fn overlapping_holds_resume_once_at_the_last() {
        let mut h = Holds::default();
        let (a, pause) = h.hold(true, true);
        assert!(pause);
        // Paused now, so the second sees a player that is not playing.
        let (b, pause) = h.hold(false, true);
        assert!(!pause);
        assert_eq!(h.release(a), Some(false), "one still in force");
        assert_eq!(h.release(b), Some(true), "the last resumes");
    }

    #[test]
    fn a_second_hold_does_not_overrule_a_listener_who_pressed_play() {
        let mut h = Holds::default();
        let (a, _) = h.hold(true, true);
        let (b, pause) = h.hold(true, true);
        assert!(!pause, "playing again mid-transfer is the listener's choice");
        h.release(a);
        assert_eq!(h.release(b), Some(true), "the first hold's pause is still the one to undo");
    }

    #[test]
    fn a_hold_ends_once() {
        let mut h = Holds::default();
        let (a, _) = h.hold(true, true);
        assert_eq!(h.release(a), Some(true));
        assert_eq!(h.release(a), None, "released twice, or expired and then released");
        assert_eq!(h.release(99), None, "never made");
    }
}
