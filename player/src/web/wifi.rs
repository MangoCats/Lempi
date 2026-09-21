//! Moving the appliance into a new Wi-Fi network, or serving its own,
//! from the browser `[SPEC034]`.
//!
//! Every route here is a thin, unauthenticated-by-design relay onto
//! `crate::bluetooth`'s own wifi/ap functions, which is where the actual
//! privilege boundary and validation live (`lempi-btctl`, checked twice
//! the same way a Bluetooth device address already is). Nothing here
//! touches `PlayerStore` at all -- NetworkManager's own connection
//! profiles are the durable state; there is no second copy of it to keep
//! in sync.
//!
//! Not gated behind `vipunen-support`: an appliance needs this exactly as
//! much with or without Vipunen installed, the same posture
//! `preference.rs`/`bluetooth.rs`'s existing routes already take.

use std::collections::HashMap;

use axum::extract::{Path, Query};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};

use crate::bluetooth;

fn reply(result: Result<Result<serde_json::Value, String>, tokio::task::JoinError>) -> Response {
    match result {
        Ok(Ok(v)) => axum::Json(v).into_response(),
        Ok(Err(e)) => (StatusCode::BAD_REQUEST, e).into_response(),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

fn reply_rows(result: Result<Result<Vec<serde_json::Value>, String>, tokio::task::JoinError>) -> Response {
    match result {
        Ok(Ok(v)) => axum::Json(v).into_response(),
        Ok(Err(e)) => (StatusCode::BAD_REQUEST, e).into_response(),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

/// Networks currently in range, freshly scanned `[SPEC034]`.
pub(super) async fn scan() -> Response {
    reply_rows(tokio::task::spawn_blocking(bluetooth::wifi_scan).await)
}

/// Every known network -- NetworkManager's own connection profiles,
/// filtered to Wi-Fi `[SPEC034]`.
pub(super) async fn known() -> Response {
    reply_rows(tokio::task::spawn_blocking(bluetooth::wifi_known).await)
}

/// Switch the client connection `[SPEC034]`. `?ssid=&password=` -- an
/// absent or empty `password` means an open network, which is a real
/// choice a listener's own target network may actually be, not a mistake
/// to guess at. Returns `{change_id, minutes}` on an apparent success;
/// the browser must still call `confirm` once it can prove it can reach
/// this device on whatever the new network turns out to be.
pub(super) async fn connect(Query(q): Query<HashMap<String, String>>) -> Response {
    let Some(ssid) = q.get("ssid").filter(|s| !s.is_empty()).cloned() else {
        return (StatusCode::BAD_REQUEST, "ssid is required").into_response();
    };
    let password = q.get("password").cloned().unwrap_or_default();
    reply(tokio::task::spawn_blocking(move || bluetooth::wifi_connect(&ssid, &password)).await)
}

/// Cancel the pending hard revert `[SPEC034]` -- the one call in this
/// module that means "yes, I can still reach you."
pub(super) async fn confirm(Path(change_id): Path<String>) -> Response {
    reply(tokio::task::spawn_blocking(move || bluetooth::wifi_confirm(&change_id)).await)
}

/// Delete a known-network profile; refused for whichever one is active
/// `[SPEC034]`.
pub(super) async fn forget(Path(name): Path<String>) -> Response {
    reply(tokio::task::spawn_blocking(move || bluetooth::wifi_forget(&name)).await)
}

/// Whether a known network is offered automatically at boot `[SPEC034]`.
pub(super) async fn autoconnect(Path((name, state)): Path<(String, String)>) -> Response {
    let on = match state.as_str() {
        "on" => true,
        "off" => false,
        _ => return (StatusCode::BAD_REQUEST, "state is on or off").into_response(),
    };
    reply(tokio::task::spawn_blocking(move || bluetooth::wifi_autoconnect(&name, on)).await)
}

/// Start the appliance's own access point `[SPEC034]`. `?ssid=&password=`
/// both optional -- omitted, the helper uses the same published default
/// `[PI-SET-030]` already named (`Lempi`/`Lempi321`), so there is exactly
/// one thing to remember and it is already written down. Same
/// `{change_id, minutes}` shape and the same confirm requirement as
/// `connect`, since bringing the appliance's own AP up is just as likely
/// to cut whatever connection asked for it.
pub(super) async fn ap_start(Query(q): Query<HashMap<String, String>>) -> Response {
    let ssid = q.get("ssid").filter(|s| !s.is_empty()).cloned();
    let password = q.get("password").filter(|s| !s.is_empty()).cloned();
    reply(tokio::task::spawn_blocking(move || bluetooth::ap_start(ssid.as_deref(), password.as_deref())).await)
}

/// Leave access-point mode, back to whichever known network is set to
/// connect automatically `[SPEC034]`.
pub(super) async fn ap_stop() -> Response {
    reply(tokio::task::spawn_blocking(bluetooth::ap_stop).await)
}
