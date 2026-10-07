//! Bluetooth speaker discovery and pairing, reached from the audio-source
//! panel `[PI3-AIM-020]`.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::time::Duration;

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};

use crate::bluetooth;
use crate::engine::Command;
use crate::output::Volume;

use super::Ui;

/// Every radio and whether it is blocked `[PI3-RF-010]`.
///
/// Built because a blocked radio is indistinguishable, from the settings page,
/// from a broken button: the Middleton was paired, bonded, trusted and
/// advertising, and Connect did nothing at all because `hci0` was soft-blocked
/// `[PI3-FOUND-050]`. One line saying so would have ended that evening.
pub(super) async fn radios() -> Response {
    bt_reply(bluetooth::run(bluetooth::Verb::Radios, None), false)
}

/// Switch one radio on or off `[PI3-RF-020]`.
///
/// The helper refuses to block whichever radio carries the default route, and
/// this deliberately does **not** repeat that rule -- one copy, on the side
/// that holds the privilege, so a second caller cannot be told something
/// different `[PI3-RF-030]`.
pub(super) async fn set_radio(
    axum::extract::Path((kind, state)): axum::extract::Path<(String, String)>,
) -> Response {
    let on = match state.as_str() {
        "on" => true,
        "off" => false,
        _ => return (StatusCode::NOT_FOUND, "state is on or off").into_response(),
    };
    bt_reply(bluetooth::set_radio(&kind, on), false)
}

pub(super) async fn speakers() -> Response {
    bt_reply(bluetooth::run(bluetooth::Verb::List, None), false)
}

/// Verbs that name no device: `scan`.
pub(super) async fn speaker_verb(
    State(ui): State<Ui>,
    axum::extract::Path(verb): axum::extract::Path<String>,
) -> Response {
    let Some(v) = bluetooth::Verb::parse(&verb) else {
        return (StatusCode::NOT_FOUND, "unknown verb").into_response();
    };
    if v.needs_address() {
        return (StatusCode::BAD_REQUEST, "verb needs a device").into_response();
    }
    let _ = &ui;
    bt_reply(bluetooth::run(v, None), false)
}

/// Verbs that name a device. `use` reopens the player output as part of the
/// same request `[PI3-UI-020]`: the stream does not dependably follow a change
/// of default sink, so a selection that stopped at the helper would look like
/// it worked and be silent. Doing it here rather than in the browser means no
/// caller can forget the step that makes the choice audible.
pub(super) async fn speaker_verb_on(
    State(ui): State<Ui>,
    axum::extract::Path((verb, address)): axum::extract::Path<(String, String)>,
) -> Response {
    let Some(v) = bluetooth::Verb::parse(&verb) else {
        return (StatusCode::NOT_FOUND, "unknown verb").into_response();
    };

    if v == bluetooth::Verb::Forget {
        let db = ui.db.clone();
        let library = ui.library.clone();
        let addr = address.clone();
        let forgotten_was_active = tokio::task::spawn_blocking(move || {
            if let Ok(store) = crate::db::PlayerStore::open_split(&db, &library) {
                let active = store.get_active_output();
                let was_active = active == format!("bt:{addr}");
                let _ = store.forget_output(&format!("bt:{addr}"));
                was_active
            } else {
                false
            }
        })
        .await
        .unwrap_or(false);

        let result = bluetooth::run(v, Some(&address));
        if forgotten_was_active {
            let db = ui.db.clone();
            let library = ui.library.clone();
            let current_amp = ui.handle.snapshot().volume;
            let current_db = Volume::db_for(current_amp);
            let dac_amp = tokio::task::spawn_blocking(move || {
                crate::bluetooth::restore_dac_sink();
                let store = crate::db::PlayerStore::open_split(&db, &library).ok()?;
                let dac_db = store.resolve_output_volume("dac", current_db);
                Some(Volume::amplitude_at_db(dac_db))
            })
            .await
            .unwrap_or(None)
            .unwrap_or(current_amp);

            ui.handle.send(Command::ReopenOutput);
            tokio::time::sleep(Duration::from_millis(200)).await;
            ui.handle.send(Command::SetVolume(dac_amp));
            tokio::time::sleep(Duration::from_millis(1_300)).await;
        }
        return bt_reply(result, forgotten_was_active);
    }

    if matches!(v, bluetooth::Verb::Use | bluetooth::Verb::Pair) {
        let db = ui.db.clone();
        let library = ui.library.clone();
        let current_amp = ui.handle.snapshot().volume;
        let current_db = Volume::db_for(current_amp);
        let target_output = format!("bt:{address}");

        let (timeout_s, target_amp, dac_amp) = tokio::task::spawn_blocking({
            let db = db.clone();
            let library = library.clone();
            let target_output = target_output.clone();
            move || {
                let store = crate::db::PlayerStore::open_split(&db, &library).ok()?;
                let outgoing = store.get_active_output();
                if outgoing != target_output {
                    let _ = store.save_output_volume(&outgoing, current_db, false);
                }
                let timeout = store.get_speaker_connect_timeout();
                let target_db = store.resolve_output_volume(&target_output, current_db);
                let target_amp = Volume::amplitude_at_db(target_db);
                let dac_db = store.resolve_output_volume("dac", current_db);
                let dac_amp = Volume::amplitude_at_db(dac_db);
                Some((timeout, target_amp, dac_amp))
            }
        })
        .await
        .unwrap_or(None)
        .unwrap_or((45, current_amp, current_amp));

        let result = bluetooth::run_with_timeout(v, Some(&address), Some(timeout_s));
        let is_connected = result
            .as_ref()
            .map(|val| val.get("ok").and_then(|o| o.as_bool()).unwrap_or(false))
            .unwrap_or(false);

        if is_connected {
            let db = ui.db.clone();
            let library = ui.library.clone();
            let addr2 = address.clone();
            let target_out = target_output.clone();
            let _ = tokio::task::spawn_blocking(move || {
                if let Ok(store) = crate::db::PlayerStore::open_split(&db, &library) {
                    let _ = store.save_speaker_address(&addr2);
                    let _ = store.set_active_output(&target_out);
                }
            })
            .await;
            ui.handle.send(Command::ReopenOutput);
            tokio::time::sleep(Duration::from_millis(200)).await;
            ui.handle.send(Command::SetVolume(target_amp));
            tokio::time::sleep(Duration::from_millis(1_300)).await;
            return bt_reply(result, true);
        } else {
            // Connection failed or timed out: automatic fallback to DAC [IMPL-POV-045]
            let db = ui.db.clone();
            let library = ui.library.clone();
            let _ = tokio::task::spawn_blocking(move || {
                if let Ok(store) = crate::db::PlayerStore::open_split(&db, &library) {
                    let _ = store.set_active_output("dac");
                }
                crate::bluetooth::restore_dac_sink();
            })
            .await;
            ui.handle.send(Command::ReopenOutput);
            tokio::time::sleep(Duration::from_millis(200)).await;
            ui.handle.send(Command::SetVolume(dac_amp));
            tokio::time::sleep(Duration::from_millis(1_300)).await;
            return bt_reply(result, false);
        }
    }

    let result = bluetooth::run(v, Some(&address));
    bt_reply(result, false)
}

/// The appliance's status LED: which of the four modes, and the brightness
/// `on` would use `[PI3-LED-010]`. Fetched once when the settings panel
/// opens, the same way the speaker list is -- there is nothing here that
/// changes on its own, so unlike playback state it has no reason to ride
/// the live snapshot.
pub(super) async fn led_state(State(ui): State<Ui>) -> Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let (mode, brightness) = tokio::task::spawn_blocking(move || {
        crate::db::PlayerStore::open_split(&db, &library)
            .map(|s| (s.load_led_mode(), s.load_led_brightness()))
            .unwrap_or_else(|_| ("on".into(), 100))
    })
    .await
    .unwrap_or_else(|_| ("on".into(), 100));
    axum::Json(serde_json::json!({ "mode": mode, "brightness": brightness })).into_response()
}

/// Switch the LED, and remember the choice `[PI3-LED-010]`. The write to
/// `player_settings` happens first: a listener who changes this wants it
/// to survive the next reboot at least as much as they want it to change
/// right now, and a hardware failure below must not silently lose that
/// half of the request. Brightness (`?pct=`) is only required, and only
/// stored, for `mode=on` -- the other three modes leave whatever
/// brightness was last set untouched, so switching back to `on` later
/// remembers it.
pub(super) async fn set_led(
    State(ui): State<Ui>,
    axum::extract::Path(mode): axum::extract::Path<String>,
    axum::extract::Query(q): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> Response {
    if !matches!(mode.as_str(), "on" | "wifi" | "off" | "default") {
        return (StatusCode::BAD_REQUEST, "mode is on, wifi, off, or default").into_response();
    }
    let pct: Option<u8> = if mode == "on" {
        match q.get("pct").map(|p| p.parse::<u8>()) {
            Some(Ok(v)) => Some(v.clamp(1, 100)),
            Some(Err(_)) => {
                return (StatusCode::BAD_REQUEST, "pct must be a number 1-100").into_response();
            }
            None => None, // caller left it alone -- keep whatever was stored
        }
    } else {
        None
    };
    let db = ui.db.clone();
    let library = ui.library.clone();
    let mode2 = mode.clone();
    let saved = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&db, &library).map_err(|e| e.message().to_string())?;
        store.save_led_mode(&mode2).map_err(|e| e.message().to_string())?;
        if let Some(pct) = pct {
            store.save_led_brightness(pct).map_err(|e| e.message().to_string())?;
        }
        // The effective brightness to apply below: whatever was just set,
        // or -- a bare `POST /led/on` with no `?pct=` -- whatever was
        // remembered from before. Switching back to `on` must not silently
        // reset a previously-chosen brightness to full `[PI3-LED-010]`.
        Ok::<u8, String>(pct.unwrap_or_else(|| store.load_led_brightness()))
    })
    .await;
    let effective_pct = match saved.unwrap_or_else(|_| Err("could not reach the database".into())) {
        Ok(p) => p,
        Err(why) => return (StatusCode::INTERNAL_SERVER_ERROR, why).into_response(),
    };
    // A deliberate, listener-visible change -- worth its own journal line
    // distinct from `lempi-led-boot`'s own (also now logged), so "who set
    // this" is a fact one `journalctl` away rather than a guess the next
    // time it looks surprising.
    tracing::info!("led set to {mode} ({effective_pct}%) via web request");
    // The choice is already durable at this point -- a hardware failure
    // from here down is real and worth reporting, but it must not read as
    // "your choice was not saved" `[PI3-LED-010]`.
    match bluetooth::set_led(&mode, Some(effective_pct)) {
        Ok(_) => axum::Json(serde_json::json!({ "ok": true, "mode": mode, "brightness": effective_pct }))
            .into_response(),
        Err(e) => (StatusCode::BAD_REQUEST, e).into_response(),
    }
}

/// Output volume status payload `[SPEC-POV-060]`.
#[derive(Debug, Clone, serde::Serialize)]
pub struct OutputVolumeStatus {
    pub output_id: String,
    pub name: String,
    pub current_db: f32,
    pub pinned_db: Option<f32>,
    pub last_used_db: Option<f32>,
    pub has_pinned: bool,
    pub connect_timeout_s: u32,
}

pub(super) fn get_output_volume_status(ui: &Ui) -> Result<OutputVolumeStatus, String> {
    let store = crate::db::PlayerStore::open_split(&ui.db, &ui.library)
        .map_err(|e| e.message().to_string())?;
    let output_id = store.get_active_output();
    let vol = store.load_output_volume(&output_id).unwrap_or_default();
    let connect_timeout_s = store.get_speaker_connect_timeout();
    let current_amp = ui.handle.snapshot().volume;
    let current_db = ((Volume::db_for(current_amp) * 10.0).round()) / 10.0;
    let name = if output_id == "dac" {
        "DAC / Onboard Audio".to_string()
    } else if let Some(addr) = output_id.strip_prefix("bt:") {
        crate::bluetooth::device_name(addr)
    } else {
        output_id.clone()
    };
    Ok(OutputVolumeStatus {
        has_pinned: vol.pinned_db.is_some(),
        pinned_db: vol.pinned_db,
        last_used_db: vol.last_used_db,
        output_id,
        name,
        current_db,
        connect_timeout_s,
    })
}

pub(super) async fn output_volume(State(ui): State<Ui>) -> Response {
    let ui_clone = ui.clone();
    match tokio::task::spawn_blocking(move || get_output_volume_status(&ui_clone)).await {
        Ok(Ok(st)) => axum::Json(st).into_response(),
        Ok(Err(e)) => (StatusCode::INTERNAL_SERVER_ERROR, e).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
    }
}

pub(super) async fn remember_output_volume(State(ui): State<Ui>) -> Response {
    let ui_clone = ui.clone();
    let res = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&ui_clone.db, &ui_clone.library)
            .map_err(|e| e.message().to_string())?;
        let active = store.get_active_output();
        let current_amp = ui_clone.handle.snapshot().volume;
        let current_db = Volume::db_for(current_amp);
        store
            .save_output_volume(&active, current_db, true)
            .map_err(|e| e.message().to_string())?;
        get_output_volume_status(&ui_clone)
    })
    .await;
    match res {
        Ok(Ok(st)) => axum::Json(st).into_response(),
        Ok(Err(e)) => (StatusCode::INTERNAL_SERVER_ERROR, e).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
    }
}

pub(super) async fn clear_output_volume(State(ui): State<Ui>) -> Response {
    let ui_clone = ui.clone();
    let res = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&ui_clone.db, &ui_clone.library)
            .map_err(|e| e.message().to_string())?;
        let active = store.get_active_output();
        store
            .clear_output_pinned_volume(&active)
            .map_err(|e| e.message().to_string())?;
        get_output_volume_status(&ui_clone)
    })
    .await;
    match res {
        Ok(Ok(st)) => axum::Json(st).into_response(),
        Ok(Err(e)) => (StatusCode::INTERNAL_SERVER_ERROR, e).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
    }
}

pub(super) async fn set_output_timeout(
    State(ui): State<Ui>,
    axum::extract::Path(secs): axum::extract::Path<u32>,
) -> Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let res = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&db, &library)
            .map_err(|e| e.message().to_string())?;
        store
            .set_speaker_connect_timeout(secs)
            .map_err(|e| e.message().to_string())?;
        Ok::<u32, String>(store.get_speaker_connect_timeout())
    })
    .await;
    match res {
        Ok(Ok(val)) => {
            axum::Json(serde_json::json!({ "ok": true, "connect_timeout_s": val })).into_response()
        }
        Ok(Err(e)) => (StatusCode::BAD_REQUEST, e).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
    }
}

pub(super) async fn use_dac(State(ui): State<Ui>) -> Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let current_amp = ui.handle.snapshot().volume;
    let current_db = Volume::db_for(current_amp);

    let target_amp = match tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&db, &library)
            .map_err(|e| e.message().to_string())?;
        let outgoing = store.get_active_output();
        if outgoing != "dac" {
            let _ = store.save_output_volume(&outgoing, current_db, false);
        }
        store
            .set_active_output("dac")
            .map_err(|e| e.message().to_string())?;
        let target_db = store.resolve_output_volume("dac", current_db);
        crate::bluetooth::restore_dac_sink();
        Ok::<f32, String>(Volume::amplitude_at_db(target_db))
    })
    .await {
        Ok(Ok(amp)) => amp,
        Ok(Err(e)) => return (StatusCode::INTERNAL_SERVER_ERROR, e).into_response(),
        Err(e) => return (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
    };

    ui.handle.send(Command::ReopenOutput);
    tokio::time::sleep(Duration::from_millis(200)).await;
    ui.handle.send(Command::SetVolume(target_amp));
    tokio::time::sleep(Duration::from_millis(1_300)).await;

    let ui_clone = ui.clone();
    let status = tokio::task::spawn_blocking(move || get_output_volume_status(&ui_clone)).await;
    let mut resp = serde_json::json!({
        "ok": true,
        "active_output": "dac",
        "reopened": true,
    });
    if let Ok(Ok(st)) = status {
        if let Ok(st_val) = serde_json::to_value(st) {
            resp["volume_status"] = st_val;
        }
    }
    let where_to = crate::sink::current();
    resp["audible"] = audible_json(where_to.audible);
    if let Ok(s) = serde_json::to_value(where_to) {
        resp["output"] = s;
    }
    axum::Json(resp).into_response()
}

/// The reply's `audible`: `true`, `false`, or `null` for "could not tell".
///
/// The skin tests `=== false`, so `null` never reads as silence -- and, which
/// is the point, never as sound either. This used to answer `!dummy` whenever
/// the stream was not seen unlinked, so a host where `wpctl` could not run at
/// all replied `true` `[SPEC-APS-030]`.
fn audible_json(a: crate::sink::Tristate) -> serde_json::Value {
    use crate::sink::Tristate;
    match a {
        Tristate::Yes => true.into(),
        Tristate::No => false.into(),
        Tristate::Unknown => serde_json::Value::Null,
    }
}

/// One shape for every speaker reply, so the panel has one thing to read.
pub(super) fn bt_reply(result: Result<serde_json::Value, String>, reopened: bool) -> Response {
    match result {
        Ok(mut v) => {
            if let Some(obj) = v.as_object_mut() {
                obj.insert("reopened".into(), reopened.into());
                // The answer the listener actually cares about, and the one
                // the helper cannot give: where the audio ended up
                // `[PI3-API-020]`.
                let where_to = crate::sink::current();
                obj.insert("audible".into(), audible_json(where_to.audible));
                if let Ok(s) = serde_json::to_value(where_to) {
                    obj.insert("output".into(), s);
                }
            }
            axum::Json(v).into_response()
        }
        Err(e) => (StatusCode::BAD_REQUEST, e).into_response(),
    }
}


#[cfg(test)]
mod tests {
    use super::audible_json;
    use crate::sink::Tristate;

    /// "Could not tell" must reach the panel as `null`, never as `true`
    /// `[SPEC-APS-030]`.
    #[test]
    fn an_unobserved_output_is_null_not_true() {
        assert_eq!(audible_json(Tristate::Yes), serde_json::json!(true));
        assert_eq!(audible_json(Tristate::No), serde_json::json!(false));
        assert_eq!(audible_json(Tristate::Unknown), serde_json::Value::Null);
    }

    #[test]
    fn output_volume_status_serialization() {
        let st = super::OutputVolumeStatus {
            output_id: "dac".into(),
            name: "DAC / Onboard Audio".into(),
            current_db: -12.0,
            pinned_db: Some(-15.0),
            last_used_db: Some(-12.0),
            has_pinned: true,
            connect_timeout_s: 45,
        };
        let val = serde_json::to_value(&st).unwrap();
        assert_eq!(val["output_id"], "dac");
        assert_eq!(val["name"], "DAC / Onboard Audio");
        assert_eq!(val["has_pinned"], true);
        assert_eq!(val["pinned_db"], -15.0);
        assert_eq!(val["connect_timeout_s"], 45);
    }
}
