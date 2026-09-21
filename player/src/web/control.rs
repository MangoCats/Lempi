//! Playback control: volume, seek, queue actions, the WebSocket command
//! verb, and the handful of process-level actions (power, restart, backend
//! switch, program override) that do not fit any other topic here.

use std::time::Duration;

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};

use crate::engine::{Command, Placement};
use crate::output::Volume;

use super::Ui;

/// Master level in dB relative to full scale, `-72.0` to `0.0`.
///
/// Fractional now that the control moves a pixel at a time `[REQ-AUD-156]`: at
/// the top of its travel a pixel is worth less than a hundredth of a dB, so
/// whole decibels would quantise away most of the resolution the curve exists
/// to provide. Rust parses a decimal point locale-independently, so the earlier
/// worry about one crossing a URL does not apply to the parser -- only to
/// anything that might have formatted it, and the browser formats with
/// `toFixed`.
///
/// It becomes an amplitude here, at the edge, so that everything inward of this
/// point -- engine, device, saved state -- speaks in amplitude, which is what
/// multiplies samples, and only the listener's control speaks in dB
/// `[REQ-AUD-154]`.
pub(super) async fn set_volume(
    State(ui): State<Ui>,
    axum::extract::Path(db): axum::extract::Path<f32>,
) -> StatusCode {
    ui.handle.send(Command::SetVolume(Volume::amplitude_at_db(db)));
    StatusCode::NO_CONTENT
}

/// Start the underrun count again from now `[REQ-VIS-230]`.
///
/// **The counter itself is not reset.** The engine moves a baseline and
/// reports the difference, so whatever wants the whole-process figure can
/// still have it — and a display that has been restarted does not make the
/// player forget it ever glitched.
pub(super) async fn restart_underruns(State(ui): State<Ui>) -> StatusCode {
    ui.handle.send(Command::RestartUnderruns);
    StatusCode::NO_CONTENT
}

/// Move to a point inside the passage that is sounding `[REQ-VIS-225]`.
///
/// **In milliseconds into the passage, not a fraction of it.** The browser
/// knows where it clicked as a proportion of a bar, and could have sent that
/// — but a fraction means nothing without the duration, and the two sides
/// would then have to agree about which duration. The engine owns the span;
/// the browser converts once, here, against the duration it was shown.
pub(super) async fn seek_to(
    State(ui): State<Ui>,
    axum::extract::Path(ms): axum::extract::Path<u64>,
) -> StatusCode {
    let Ok(mut c) = ui.controls.lock() else { return StatusCode::INTERNAL_SERVER_ERROR };
    c.seek_requested = Some(ms);
    StatusCode::ACCEPTED
}

/// Act on a passage's place in the queue `[REQ-VIS-185]`.
///
/// Six verbs on one route, because they are one idea -- where does this go --
/// and splitting them across six routes would spread that idea thin:
///
/// * `now` — to the front, then skip into it. The only one that interrupts.
/// * `next` — after the current passage.
/// * `last` — to the back, behind everything already waiting.
/// * `remove` — out of the queue.
/// * `sooner` / `later` — one place each way, clamped at the ends.
///
/// The first three take a passage from the library; the last three act on one
/// already queued, and need no library read at all.
pub(super) async fn queue_passage(
    State(ui): State<Ui>,
    axum::extract::Path((passages, action)): axum::extract::Path<(String, String)>,
) -> StatusCode {
    // A comma-separated list, so one selected passage and thirty travel the same
    // path `[REQ-VIS-195]`. They must arrive together: three passages sent as
    // three requests and inserted one at a time at the same place come out
    // backwards, which looks like a UI fault and is not.
    // Two identifier spaces meet on this route, and conflating them was a bug
    // `[REQ-VIS-186]`. The verbs that ADD name passages in the library; the
    // verbs that EDIT name entries already in the queue, which is not the same
    // thing the moment a passage appears twice.
    //
    // Editing the queue is a rearrangement, not a lookup: what is named is
    // already there, so none of these touch the database.
    match action.as_str() {
        "remove" => {
            let qids: Vec<u64> =
                passages.split(',').filter_map(|s| s.trim().parse().ok()).collect();
            if qids.is_empty() {
                return StatusCode::NOT_FOUND;
            }
            for qid in qids {
                ui.handle.send(Command::RemoveQueued(qid));
            }
            return StatusCode::NO_CONTENT;
        }
        "sooner" | "later" => {
            let Ok(qid) = passages.trim().parse::<u64>() else {
                return StatusCode::NOT_FOUND;
            };
            let delta = if action == "sooner" { -1 } else { 1 };
            ui.handle.send(Command::ShiftQueued(qid, delta));
            return StatusCode::NO_CONTENT;
        }
        _ => {}
    }
    let ids: Vec<i64> = passages.split(',').filter_map(|s| s.trim().parse().ok()).collect();
    if ids.is_empty() {
        return StatusCode::NOT_FOUND;
    }
    let place = match action.as_str() {
        "now" => Placement::Now,
        "next" => Placement::Next,
        "last" => Placement::Last,
        _ => return StatusCode::NOT_FOUND,
    };
    let db = ui.db.clone();
    let library = ui.library.clone();
    let entries = tokio::task::spawn_blocking(move || {
        let lib = crate::db::Library::open_split(&db, &library).ok()?;
        // Order is the caller's, and it is the order they were looking at.
        // A passage that cannot be read is dropped rather than failing the
        // batch: nineteen passages queued beats none.
        let mut out = Vec::with_capacity(ids.len());
        for id in ids {
            if let Ok(mut e) = lib.passage(id) {
                lib.describe(&mut e);
                if let Some(t) = lib.stored_tags(id) {
                    e.naming.apply_tags(t);
                }
                // The listener chose this one directly, not the Program
                // Director `[REQ-VIS-300]` -- the play-frequency panel's
                // own "User" row.
                e.selected_by = Some("user".into());
                out.push(e);
            }
        }
        Some(out)
    })
    .await;
    match entries {
        Ok(Some(v)) if !v.is_empty() => {
            ui.handle.send(Command::EnqueueMany(v, place));
            StatusCode::NO_CONTENT
        }
        _ => StatusCode::NOT_FOUND,
    }
}

/// Choose a programme by hand, or `auto` to revert to time of day
/// `[SPEC-DIR-185]`. Applied by the engine on its next refill, so an override
/// changes what is selected NEXT rather than interrupting what is playing.
/// Make imported music selectable without restarting the player
/// `[IMPL-SUI-075]`.
///
/// Browse reads the library live and sees an import at once; the Program
/// Director does not, because it is loaded once at startup and nothing reloaded
/// it. This asks for a rebuild — it does not perform one. The engine picks the
/// intent up on its next refill and starts only when the queue can afford it,
/// which is why the reply is *accepted* rather than *done*.
pub(super) async fn reload_library(State(ui): State<Ui>) -> StatusCode {
    let Ok(mut c) = ui.controls.lock() else { return StatusCode::INTERNAL_SERVER_ERROR };
    c.reload_requested = true;
    c.reload_status = Some("requested".into());
    StatusCode::ACCEPTED
}

/// Ask for the other backend `[SPEC-BK-030]`.
///
/// Asks; it does not perform. The engine takes the intent on its next pass,
/// where the backends actually live — the same reason `reload_library` is a
/// request. The reply is *accepted*, and what the switch managed to carry
/// appears in `switch_status` a moment later `[SPEC-BK-045]`.
pub(super) async fn switch_backend(
    State(ui): State<Ui>,
    axum::extract::Path(which): axum::extract::Path<String>,
) -> StatusCode {
    if which != "lempi" && which != "mpd" {
        return StatusCode::BAD_REQUEST;
    }
    let Ok(mut c) = ui.controls.lock() else { return StatusCode::INTERNAL_SERVER_ERROR };
    if !c.guest_available {
        return StatusCode::CONFLICT; // nothing to switch to
    }
    c.switch_requested = Some(which);
    c.switch_status = Some("requested".into());
    StatusCode::ACCEPTED
}

pub(super) async fn set_program(
    State(ui): State<Ui>,
    axum::extract::Path(id): axum::extract::Path<String>,
) -> StatusCode {
    let new_value = {
        let Ok(mut c) = ui.controls.lock() else { return StatusCode::INTERNAL_SERVER_ERROR };
        if id == "auto" {
            c.manual_program = None;
            Some(None)
        } else {
            match id.parse::<i64>() {
                Ok(n) if c.programs.iter().any(|p| p.0 == n) => {
                    c.manual_program = Some(n);
                    Some(Some(n))
                }
                _ => None,
            }
        }
    };
    let Some(new_value) = new_value else { return StatusCode::NOT_FOUND };
    // Persisted so it survives a restart rather than reverting to
    // time-of-day selection every power cycle `[SPEC-DIR-185]`. Best-effort,
    // off the request's own success: a save that fails here costs the choice
    // not surviving the *next* restart, not this one working.
    let db = ui.db.clone();
    let library = ui.library.clone();
    let _ = tokio::task::spawn_blocking(move || match crate::db::PlayerStore::open_split(&db, &library) {
        Ok(store) => {
            if let Err(e) = store.save_manual_program(new_value) {
                eprintln!("save manual program: {e}");
            }
        }
        Err(e) => eprintln!("save manual program: {e}"),
    })
    .await;
    StatusCode::NO_CONTENT
}

/// Where the audio is actually going `[PI3-API-020]`.
///
/// On demand rather than in the state snapshot: it costs a subprocess, and the
/// settings panel is the only thing that needs it.
pub(super) async fn audio_sink() -> axum::Json<crate::sink::SinkStatus> {
    axum::Json(crate::sink::current())
}

/// Restart the player, without restarting the machine `[REQ-VIS-250]`.
///
/// **Several settings only take effect at startup**, because what they change
/// is read once: cue tracks are mapped when the guest is attached
/// `[REQ-VIS-205]`, and the Director builds its pool from the library as it
/// stands. Telling a listener to "restart the player" on an appliance whose
/// only interface is this page previously meant an SSH session or the plug.
///
/// The same first step as a shutdown, and for the same reason: the resume
/// point is otherwise written on an interval `[REQ-VIS-155]`, so a deliberate
/// restart would still lose up to that much position — in exactly the case
/// somebody took care over.
///
/// **202, not 204.** The service is about to be stopped by the thing it is
/// asking, so this reply cannot honestly claim the restart finished
/// `[PI3-API-030]`.
pub(super) async fn restart_player(State(ui): State<Ui>) -> Response {
    ui.handle.send(Command::Persist);
    tokio::time::sleep(Duration::from_millis(400)).await;

    // `restart` rather than `stop` then `start`: systemd owns the ordering,
    // and this process does not survive to run a second command anyway.
    match std::process::Command::new("sudo")
        .arg("-n")
        .arg("systemctl")
        .arg("restart")
        .arg("lempi")
        .spawn()
    {
        Ok(_) => (
            StatusCode::ACCEPTED,
            "restarting; the page will reconnect on its own in a few seconds",
        )
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("could not restart: {e}"),
        )
            .into_response(),
    }
}

/// Shut the appliance down gracefully `[PI5-PWR-010]`.
///
/// An appliance whose only interface is a web page has no other way to be
/// turned off, and pulling its power is how an SD card is corrupted and how a
/// database is left mid-write. This exists so that stops being the only option.
///
/// Three steps, in order, and the first is the one a bare `poweroff` misses:
///
/// 1. **Write the resume point now.** It is otherwise saved on an interval
///    `[REQ-VIS-155]`, so a deliberate shutdown would still lose up to that
///    much position -- in exactly the case someone took care over.
/// 2. **Hand off to systemd**, which stops the services and unmounts, rather
///    than cutting power under a live filesystem.
/// 3. **Answer 202, not 204.** The request is accepted; whether the machine
///    completes it is not something this reply can honestly claim, since the
///    process making it is about to be stopped.
pub(super) async fn power_off(State(ui): State<Ui>) -> Response {
    ui.handle.send(Command::Persist);
    // Long enough for the engine to take the command off the channel and write
    // one row; short enough that a person does not wonder if the click landed.
    tokio::time::sleep(Duration::from_millis(400)).await;

    // Detached, and deliberately not awaited: systemd stops this very service
    // as part of the transition, so waiting for the child to exit would mean
    // waiting to be killed.
    match std::process::Command::new("sudo")
        .arg("-n")
        .arg("systemctl")
        .arg("poweroff")
        .spawn()
    {
        Ok(_) => (
            StatusCode::ACCEPTED,
            "shutting down; wait for the light to go out before pulling power",
        )
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("could not begin shutdown: {e}"),
        )
            .into_response(),
    }
}

/// Controls are named rather than numbered so the wire stays readable and an
/// unknown name is a clean 404 rather than a silently wrong action.
pub(super) async fn command(
    State(ui): State<Ui>,
    axum::extract::Path(name): axum::extract::Path<String>,
) -> StatusCode {
    let h = &ui.handle;
    match name.as_str() {
        "play" => h.send(Command::Play),
        "pause" => h.send(Command::Pause),
        "skip" => h.send(Command::Skip),
        // Named rather than folded into a settings write because it is an
        // action with an audible consequence, not a stored preference: the
        // speaker panel calls it after changing the default sink, without
        // which the change is silent `[PI3-API-010]`.
        "reopen-output" => h.send(Command::ReopenOutput),
        // Deliberately no "stop": there are two states [REQ-AUD-142].
        _ => return StatusCode::NOT_FOUND,
    }
    StatusCode::NO_CONTENT
}

