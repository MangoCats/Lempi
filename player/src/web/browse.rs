//! Browsing the library and the play-history page `[REQ-VIS-180]`,
//! `[REQ-VIS-250]`, plus the listener-flag toggle the history page's own
//! checkbox posts to.

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::IntoResponse;
use serde::Serialize;

use super::Ui;

/// Browse the library by artist, album or track `[REQ-VIS-180]`.
///
/// Read-only and off the engine entirely: the browse page asks the database
/// directly rather than going through the player, so listing ten thousand
/// tracks cannot get in the way of playing one.
pub(super) async fn browse(
    State(ui): State<Ui>,
    axum::extract::Path(kind): axum::extract::Path<String>,
    axum::extract::Query(q): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let filter = crate::db::BrowseFilter {
        q: q.get("q").filter(|s| !s.is_empty()).cloned(),
        artist: q.get("artist").filter(|s| !s.is_empty()).cloned(),
        album: q.get("album").filter(|s| !s.is_empty()).cloned(),
    };
    let out = tokio::task::spawn_blocking(move || {
        let lib = crate::db::Library::open_split(&db, &library).ok()?;
        match kind.as_str() {
            "artists" => serde_json::to_value(lib.browse_artists(&filter).ok()?).ok(),
            "albums" => serde_json::to_value(lib.browse_albums(&filter).ok()?).ok(),
            "tracks" => serde_json::to_value(lib.browse_tracks(&filter).ok()?).ok(),
            // The row cap, so the page can report it without a second copy.
            "limit" => serde_json::to_value(crate::BROWSE_LIMIT).ok(),
            _ => None,
        }
    })
    .await;
    match out {
        Ok(Some(v)) => axum::Json(v).into_response(),
        _ => StatusCode::NOT_FOUND.into_response(),
    }
}

/// One passage's own facts, for the appliance-side sibling of Vipunen's
/// profile page `[REQ-VIS-270]` -- unconditional, not behind `vipunen-support`:
/// a read against the same database `browse`/`why_for` already query, no
/// decoder, no network, the same boundary `[SPEC-SUI-213]`'s own capability
/// probe already draws between build capability and application data (this
/// is squarely the latter, and it costs nothing an appliance cannot afford).
pub(super) async fn passage_info(
    State(ui): State<Ui>,
    axum::extract::Path(passage_id): axum::extract::Path<i64>,
) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let out = tokio::task::spawn_blocking(move || {
        let lib = crate::db::Library::open_split(&db, &library).ok()?;
        lib.passage_profile(passage_id).ok().flatten()
    })
    .await;
    match out {
        Ok(Some(profile)) => axum::Json(profile).into_response(),
        _ => StatusCode::NOT_FOUND.into_response(),
    }
}

/// The page sizes the history panel offers `[REQ-VIS-250]`. Anything else
/// asked for falls back to the default rather than handing SQLite an
/// unbounded `LIMIT`.
pub(super) const HISTORY_PAGE_SIZES: [i64; 3] = [10, 100, 1000];
pub(super) const HISTORY_DEFAULT_SIZE: i64 = 100;

/// One page of the play-history panel, with enough to draw the pager without
/// a second request `[REQ-VIS-250]`.
#[derive(Serialize)]
pub(super) struct HistoryPage {
    entries: Vec<crate::db::HistoryEntry>,
    total: i64,
    page: i64,
    size: i64,
}

/// What has actually sounded, paged `[REQ-VIS-250]`.
///
/// Off the engine entirely, like `browse`: a history read must never get in
/// the way of playing the next passage.
pub(super) async fn history(
    State(ui): State<Ui>,
    axum::extract::Query(q): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let size = q
        .get("size")
        .and_then(|s| s.parse::<i64>().ok())
        .filter(|n| HISTORY_PAGE_SIZES.contains(n))
        .unwrap_or(HISTORY_DEFAULT_SIZE);
    let page = q.get("page").and_then(|s| s.parse::<i64>().ok()).unwrap_or(1).max(1);
    let offset = (page - 1) * size;
    let out = tokio::task::spawn_blocking(move || {
        let lib = crate::db::Library::open_split(&db, &library).ok()?;
        let entries = lib.play_history(size, offset).ok()?;
        let total = lib.play_history_count().ok()?;
        Some(HistoryPage { entries, total, page, size })
    })
    .await;
    match out {
        Ok(Some(page)) => axum::Json(page).into_response(),
        _ => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

/// "Flag this for review" from the history page, on at any time `[REQ-VIS-265]`.
///
/// `?flagged=` carries the checkbox's own new state rather than the route
/// meaning "set" and needing a second one for "clear": a checkbox already
/// knows what it just became, and sending that is simpler than the caller
/// inferring a verb from it. `kind` is validated here against the same two
/// words the table's own CHECK constraint allows, so a malformed request is
/// refused with a reason before it reaches the database at all.
pub(super) async fn set_flag(
    State(ui): State<Ui>,
    axum::extract::Path((kind, id)): axum::extract::Path<(String, String)>,
    axum::extract::Query(q): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> axum::response::Response {
    if kind != "recording" && kind != "passage" {
        return (StatusCode::BAD_REQUEST, "kind must be recording or passage").into_response();
    }
    let flagged = q.get("flagged").map(|v| v == "true" || v == "1").unwrap_or(false);
    let db = ui.db.clone();
    let library = ui.library.clone();
    let done = tokio::task::spawn_blocking(move || {
        crate::db::PlayerStore::open_split(&db, &library)
            .map_err(|e| e.message().to_string())?
            .set_flag(&kind, &id, flagged)
            .map_err(|e| e.message().to_string())
    })
    .await;
    match done {
        Ok(Ok(())) => StatusCode::NO_CONTENT.into_response(),
        Ok(Err(msg)) => (StatusCode::BAD_REQUEST, msg).into_response(),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

