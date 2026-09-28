//! Every plain "set this number/flag and let the engine pick it up"
//! handler `[SPEC-SC-099]`, including the four folder-writing toggles
//! generated together so changing one is changing all four.

use axum::extract::State;
use axum::http::StatusCode;

use crate::engine::Command;

use super::Ui;

/// This node's hand-set delay trim `[SPEC-DLY-010]`.
///
/// Signed, so the path takes it as a string and parses: axum's `i64` path
/// extractor is fine with `-40`, but a browser sending `+40` is not worth a
/// 400 when the intent is unambiguous.
pub(super) async fn set_echo_trim(
    State(ui): State<Ui>,
    axum::extract::Path(ms): axum::extract::Path<String>,
) -> StatusCode {
    match ms.trim().trim_start_matches('+').parse::<i64>() {
        Ok(v) => {
            ui.handle.send(Command::SetEchoDelayTrim(v));
            StatusCode::NO_CONTENT
        }
        Err(_) => StatusCode::BAD_REQUEST,
    }
}

/// Return the trim to its ranked default `[SPEC-DLY-060]`.
///
/// Zero, which on a node with a measured delay means "the measured figure and
/// nothing added". On a node without one it means unset, which is not the same
/// claim and is shown differently `[SPEC-DLY-050]`.
pub(super) async fn reset_echo_trim(State(ui): State<Ui>) -> StatusCode {
    ui.handle.send(Command::SetEchoDelayTrim(0));
    StatusCode::NO_CONTENT
}

/// The node to follow, or nothing `[SPEC-ECHO-010]`.
pub(super) async fn set_echo_follow(
    State(ui): State<Ui>,
    body: String,
) -> StatusCode {
    ui.handle.send(Command::SetEchoFollow(body.trim().to_string()));
    StatusCode::NO_CONTENT
}

/// Join at once, or wait for the followed node's next passage
/// `[SPEC-ECHO-030]`.
pub(super) async fn set_echo_join_now(
    State(ui): State<Ui>,
    axum::extract::Path(now): axum::extract::Path<String>,
) -> StatusCode {
    ui.handle.send(Command::SetEchoJoinNow(matches!(now.as_str(), "1" | "true" | "now")));
    StatusCode::NO_CONTENT
}

/// This node on the network `[SPEC050]`: whether it answers discovery, and
/// the identity it answers with.
pub(super) async fn discovery_status(State(ui): State<Ui>) -> axum::response::Response {
    use axum::response::IntoResponse;
    let (db, lib) = (ui.db.clone(), ui.library.clone());
    let out = tokio::task::spawn_blocking(move || {
        let announce = crate::db::PlayerStore::open_split(&db, &lib).map(|s| s.load_announce()).unwrap_or(true);
        let me = crate::discovery::identity(&db).ok();
        serde_json::json!({
            "answering": crate::discovery::ANSWERING.load(std::sync::atomic::Ordering::Relaxed),
            "announce": announce,
            "name": crate::discovery::host_name(),
            "fingerprint": me.map(|m| m.fingerprint),
        })
    })
    .await
    .unwrap_or_else(|_| serde_json::json!({"error": "could not read"}));
    axum::Json(out).into_response()
}

/// Membership of a mesh `[SPEC-MTR-130]`: the hub's invitation and its
/// steps, this player's own confirmation from its Settings, and leaving.
/// Each answers JSON; a refusal is 409 with the reason.
pub(super) async fn mesh_status(State(ui): State<Ui>) -> axum::response::Response {
    use axum::response::IntoResponse;
    if !ui.web_loopback_only {
        // Never show an invitation whose window has gone `[SPEC-NSH-140]`.
        crate::membership::drop_stale_invite(crate::pairing::window_id());
    }
    let mut s = crate::membership::status(&ui.db);
    // The pairing window `[SecurityReview3 R3]`, so the page can say whether
    // enrolment is open and for how long. On a loopback-only host it is always
    // effectively open (no window needed), which the skin reads as "local".
    s["pairing"] = serde_json::json!({
        "required": !ui.web_loopback_only,
        "open": ui.web_loopback_only || crate::pairing::open(),
        "remaining_secs": crate::pairing::remaining_secs(),
        "window_secs": crate::pairing::window_secs(),
    });
    axum::Json(s).into_response()
}

/// Set how long the pairing window stays open, in seconds `[SecurityReview3
/// R3]`. Clamped to 60..=3600. Gated like the membership changes it governs: a
/// LAN host must not be able to pre-stretch the window before a button press,
/// so on a LAN-exposed host this needs the window already open (or loopback).
pub(super) async fn set_pairing_window(
    State(ui): State<Ui>,
    axum::extract::Path(secs): axum::extract::Path<u64>,
) -> StatusCode {
    if !ui.web_loopback_only && !crate::pairing::open() {
        return StatusCode::CONFLICT;
    }
    let secs = secs.clamp(60, 3600);
    let (db, lib) = (ui.db.clone(), ui.library.clone());
    let done = tokio::task::spawn_blocking(move || {
        crate::db::PlayerStore::open_split(&db, &lib)
            .map_err(|e| e.to_string())?
            .save_pairing_window_secs(secs)
            .map_err(|e| e.to_string())
    })
    .await;
    match done {
        Ok(Ok(())) => {
            crate::pairing::set_window_secs(secs);
            StatusCode::NO_CONTENT
        }
        _ => StatusCode::INTERNAL_SERVER_ERROR,
    }
}

/// The membership steps a LAN host may take only inside a pairing window:
/// accepting an invitation, confirming it, rejecting it `[SPEC-NSH-140]`, and
/// leaving.
fn gated(parts: &[&str]) -> bool {
    matches!(parts, ["invite"] | ["invite", _, "confirm"] | ["invite", _, "reject"] | ["leave"])
}

/// The steps that use the window up: after one of these succeeds the window
/// closes, so it yields one pairing, not several `[SPEC-NSH-130]`.
fn consumes_window(parts: &[&str]) -> bool {
    matches!(parts, ["invite", _, "confirm"] | ["leave"])
}

pub(super) async fn mesh_step(
    State(ui): State<Ui>,
    axum::extract::Path(path): axum::extract::Path<String>,
    body: String,
) -> axum::response::Response {
    use axum::response::IntoResponse;
    let db = ui.db.clone();
    let loopback = ui.web_loopback_only;
    let out = tokio::task::spawn_blocking(move || -> Result<serde_json::Value, String> {
        let b: serde_json::Value = if body.trim().is_empty() {
            serde_json::Value::Null
        } else {
            serde_json::from_str(&body).map_err(|e| format!("not JSON: {e}"))?
        };
        let parts: Vec<&str> = path.split('/').collect();
        // Membership changes need an open pairing window on a LAN-exposed host,
        // so a host on the network cannot enrol or detach this player on its
        // own `[SecurityReview3 R3]`. A loopback-only host (a phone) is
        // inherently local and needs no window. The window opens only by the
        // local button (`crate::pairing`), never over HTTP. The hub-driven
        // roster update and the mid-flow reveal are not gated: `roster` is
        // authenticated by the pinned mesh key.
        let window = if loopback { 0 } else { crate::pairing::window_id() };
        if !loopback {
            // An invitation not confirmed within its own window goes with it
            // `[SPEC-NSH-140]`.
            crate::membership::drop_stale_invite(window);
        }
        if !loopback && gated(&parts) && window == 0 {
            return Err(format!(
                "pairing is not open: press the pair button on the device (open for {}s), \
                 then try again `[SecurityReview3 R3]`",
                crate::pairing::window_secs()
            ));
        }
        let out = match parts.as_slice() {
            ["invite"] => crate::membership::invite(&db, &b),
            ["invite", id, "reveal"] => crate::membership::reveal(&db, id, b["nonce"].as_str().unwrap_or("")),
            ["invite", id, "confirm"] => crate::membership::confirm(&db, id, b["code"].as_str().unwrap_or("")),
            ["invite", id, "reject"] => crate::membership::reject(id),
            ["invite", id, "roster"] => crate::membership::take_roster(&db, id, &b),
            ["leave"] => crate::membership::leave(&db, b["mesh_fp"].as_str().unwrap_or("")).map(|()| serde_json::json!({"left": true})),
            ["roster"] => crate::membership::update_roster(&db, &b),
            _ => Err("unknown".into()),
        };
        if !loopback {
            if let Ok(v) = &out {
                if parts.as_slice() == ["invite"] {
                    if let Some(id) = v["invite"].as_str() {
                        crate::membership::stamp_invite_window(id, window);
                    }
                }
                // One pairing per window `[SPEC-NSH-130]`.
                if consumes_window(&parts) {
                    crate::pairing::close();
                }
            }
        }
        out
    })
    .await
    .unwrap_or_else(|_| Err("failed".into()));
    match out {
        Ok(v) => axum::Json(v).into_response(),
        Err(e) => (StatusCode::CONFLICT, axum::Json(serde_json::json!({"error": e}))).into_response(),
    }
}

/// Answer discovery queries, or not `[SPEC-MTR-020]`.
pub(super) async fn set_discovery_announce(
    State(ui): State<Ui>,
    axum::extract::Path(on): axum::extract::Path<String>,
) -> StatusCode {
    let on = matches!(on.as_str(), "1" | "true" | "on");
    let (db, lib) = (ui.db.clone(), ui.library.clone());
    let done = tokio::task::spawn_blocking(move || {
        crate::db::PlayerStore::open_split(&db, &lib).map_err(|e| e.to_string())?.save_announce(on).map_err(|e| e.to_string())
    })
    .await;
    match done {
        Ok(Ok(())) => StatusCode::NO_CONTENT,
        _ => StatusCode::INTERNAL_SERVER_ERROR,
    }
}

/// How often the resume point is written `[REQ-VIS-155]`.
pub(super) async fn set_resume_save(
    State(ui): State<Ui>,
    axum::extract::Path(ms): axum::extract::Path<u64>,
) -> StatusCode {
    ui.handle.send(Command::SetResumeSave(ms));
    StatusCode::NO_CONTENT
}

/// How long a skipped passage stays out of selection `[SPEC-PLAY-050]`.
pub(super) async fn set_skip_suppress(
    State(ui): State<Ui>,
    axum::extract::Path(hours): axum::extract::Path<u64>,
) -> StatusCode {
    ui.handle.send(Command::SetSkipSuppress(hours));
    StatusCode::NO_CONTENT
}

/// How long a passage removed from the queue unheard stays out
/// `[SPEC-PLAY-055]`.
pub(super) async fn set_dequeue_suppress(
    State(ui): State<Ui>,
    axum::extract::Path(hours): axum::extract::Path<u64>,
) -> StatusCode {
    ui.handle.send(Command::SetDequeueSuppress(hours));
    StatusCode::NO_CONTENT
}

/// How many passages the Director keeps ahead `[SPEC-MPD-105]`.
pub(super) async fn set_queue_depth(
    State(ui): State<Ui>,
    axum::extract::Path(n): axum::extract::Path<usize>,
) -> StatusCode {
    ui.handle.send(Command::SetQueueDepth(n));
    StatusCode::NO_CONTENT
}

/// How often a guest backend samples `status` `[SPEC-MPD-105]`.
pub(super) async fn set_sample_interval(
    State(ui): State<Ui>,
    axum::extract::Path(ms): axum::extract::Path<u64>,
) -> StatusCode {
    ui.handle.send(Command::SetSampleInterval(ms));
    StatusCode::NO_CONTENT
}

/// How long a skip fades the outgoing passage out, in ms. Clamped by the
/// engine, which owns the limits `[REQ-AUD-162]`.
pub(super) async fn set_skip_fade(
    State(ui): State<Ui>,
    axum::extract::Path(ms): axum::extract::Path<u64>,
) -> StatusCode {
    ui.handle.send(Command::SetSkipFade(ms));
    StatusCode::NO_CONTENT
}

/// How long after a skip the next passage starts, in ms `[REQ-AUD-162]`.
pub(super) async fn set_skip_lead(
    State(ui): State<Ui>,
    axum::extract::Path(ms): axum::extract::Path<u64>,
) -> StatusCode {
    ui.handle.send(Command::SetSkipLead(ms));
    StatusCode::NO_CONTENT
}

/// Allow or forbid Lempi writing cue sheets into the music folder
/// `[REQ-VIS-205]`.
///
/// The four settings that let Lempi write files outside its own storage.
///
/// **Written as one macro so that changing one is changing all four.** They are
/// the same handler with a different flag: take `on`/`off`, tell the engine so
/// the choice persists, and leave an intent for the loop to act on — because
/// acting means walking the library and writing into a folder Lempi does not
/// own, which is not work for a request handler to do while a browser waits.
///
/// The generation each one triggers is the matching table in `lempi.rs`; the two
/// lists are the same four in the same order, and neither is complete without
/// the other. Adding a fifth means an arm here, an entry there, a column of
/// none — settings are rows now `[SPEC-SC-099]` — and a checkbox in the skin.
///
/// `beside` marks a setting that writes **beside the audio**, which a host may
/// forbid -- a phone, where the music is shared and Lempi's own files are not
/// `[REQ-AND-200]`. Where it is forbidden the route refuses rather than
/// persisting a choice that would do nothing, and the snapshot's
/// `writes_beside_audio` capability lets the skin hide it.
macro_rules! writes_files {
    ($($fn_name:ident => $cmd:ident, $asked:ident, $status:ident, $what:literal, $req:literal, beside: $beside:literal;)+) => {
        $(
            #[doc = concat!("Allow or forbid Lempi writing ", $what, " `", $req, "`.")]
            ///
            /// One of four; see [`writes_files`].
            pub(super) async fn $fn_name(
                State(ui): State<Ui>,
                axum::extract::Path(on): axum::extract::Path<String>,
            ) -> StatusCode {
                if $beside && !ui.capabilities.writes_beside_audio {
                    return StatusCode::FORBIDDEN;
                }
                let want = on == "on" || on == "true" || on == "1";
                ui.handle.send(Command::$cmd(want));
                let Ok(mut c) = ui.controls.lock() else {
                    return StatusCode::INTERNAL_SERVER_ERROR;
                };
                c.$asked = Some(want);
                c.$status = Some(if want { "writing…".into() } else { "off".into() });
                StatusCode::ACCEPTED
            }
        )+
    };
}

// Only the lyrics sidecar is marked `beside` so far: [REQ-AND-200] decided it.
// Cue sheets and covers also write into the music folder, and whether a phone
// forbids them too is [REQ-AND-950], the maintainer's.
writes_files! {    set_cue_sheets => SetCueSheets, cue_requested, cue_status,
        "cue sheets into the music folder", "[REQ-VIS-205]", beside: false;
    set_covers => SetCovers, covers_requested, covers_status,
        "cover art into the music folder", "[REQ-VIS-210]", beside: false;
    set_lyrics_cache => SetLyricsCache, lyrics_requested, lyrics_status,
        "per-song lyrics into a local client's cache", "[REQ-VIS-215]", beside: false;
    set_lyrics_sidecar => SetLyricsSidecar, sidecar_requested, sidecar_status,
        "lyrics beside the audio", "[REQ-VIS-220]", beside: true;
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Which membership steps a LAN host needs the pairing window for, and
    /// which of them use it up `[SPEC-NSH-130]`, `[SPEC-NSH-140]`. Pure, so it
    /// is tested here without the process-global window.
    #[test]
    fn the_window_gates_and_is_consumed_by_the_right_steps() {
        for p in [vec!["invite"], vec!["invite", "x", "confirm"], vec!["invite", "x", "reject"], vec!["leave"]] {
            assert!(gated(&p), "{p:?} needs the window");
        }
        for p in [vec!["invite", "x", "reveal"], vec!["invite", "x", "roster"], vec!["roster"]] {
            assert!(!gated(&p), "{p:?} is authenticated otherwise, not by the window");
        }
        assert!(consumes_window(&["invite", "x", "confirm"]), "a confirmation uses the window up");
        assert!(consumes_window(&["leave"]), "so does leaving");
        assert!(!consumes_window(&["invite"]), "accepting an invitation does not");
        assert!(!consumes_window(&["invite", "x", "reject"]), "nor does rejecting one");
    }
}
