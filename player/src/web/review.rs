//! The identification-review queue `[SPEC010]`: what to look at, and the
//! decisions made about it. Gated behind `vipunen-support` along with
//! everything else reached from the review page.

#[cfg(feature = "vipunen-support")]
use axum::extract::State;
#[cfg(feature = "vipunen-support")]
use axum::http::StatusCode;
#[cfg(feature = "vipunen-support")]
use axum::response::IntoResponse;

#[cfg(feature = "vipunen-support")]
use super::Ui;

/// The questionable ids, with the evidence against them `[REQ-LIB-165]`.
///
/// Progress travels with the list so the page can distinguish three states that
/// would otherwise all render as an empty table: the pass has never been run,
/// it ran and found nothing, or everything it found has been dealt with.
#[cfg(feature = "vipunen-support")]
pub(super) async fn review_queue(State(ui): State<Ui>) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let out = tokio::task::spawn_blocking(move || {
        let lib = crate::db::Library::open_split(&db, &library).ok()?;
        let items = lib.review_queue(crate::BROWSE_LIMIT).ok()?;
        serde_json::to_value(serde_json::json!({
            "progress": lib.review_progress(),
            "items": items,
        }))
        .ok()
    })
    .await;
    match out {
        Ok(Some(v)) => axum::Json(v).into_response(),
        _ => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

/// One passage's own review card, on demand `[SPEC-SUI-199]`.
///
/// Reached from a deep link (`/review?passage=X`) for a passage the
/// CONTRADICTED-only queue above would never surface itself -- never
/// fingerprinted, or simply not what someone wants it to say. Same shape as
/// a queue row, so the page renders it with the exact same card.
#[cfg(feature = "vipunen-support")]
pub(super) async fn review_passage(
    State(ui): State<Ui>,
    axum::extract::Path(passage_id): axum::extract::Path<i64>,
) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let item = tokio::task::spawn_blocking(move || {
        let lib = crate::db::Library::open_split(&db, &library).ok()?;
        lib.review_item_for(passage_id)
    })
    .await
    .ok()
    .flatten();
    match item {
        Some(v) => axum::Json(v).into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

/// Record a judgement `[REQ-LIB-165]`.
///
/// `kept`, `reassigned` or `deferred`; a reassignment carries `?mbid=`. The
/// vocabulary is checked in `PlayerStore`, which is the only writer, so an
/// unknown verb is rejected there rather than trusted from the URL.
///
/// Nothing here rewrites `passage_recordings`. A decision is recorded, and
/// `tools/apply_reviews.py` folds accepted ones into the library as a separate,
/// deliberate step -- reassigning an id changes what a passage *is*, and play
/// history is keyed by recording.
#[cfg(feature = "vipunen-support")]
pub(super) async fn record_review(
    State(ui): State<Ui>,
    axum::extract::Path((passage_id, decision)): axum::extract::Path<(i64, String)>,
    axum::extract::Query(q): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let mbid = q.get("mbid").cloned();
    let release = q.get("release").cloned();
    let done = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&db, &library)
            .map_err(|e| e.message().to_string())?;
        // `reopen` is the undo. It is a decision verb like the others from the
        // page's point of view, and a different operation underneath, so it
        // routes here rather than growing a second endpoint shape.
        if decision == "reopen" {
            store.clear_review(passage_id).map_err(|e| e.message().to_string())
        } else {
            store
                .record_review(passage_id, &decision, mbid.as_deref(), release.as_deref())
                .map_err(|e| e.message().to_string())
        }
    })
    .await;
    match done {
        Ok(Ok(())) => StatusCode::NO_CONTENT.into_response(),
        // The reason travels back as text. A refusal a person cannot read is
        // one they will retry, and "already applied to the library" is
        // precisely what they need to be told.
        Ok(Err(why)) => (StatusCode::CONFLICT, why).into_response(),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

/// Correct a recording's credited artist, or withdraw that correction
/// `[SPEC-SUI-197]` -- independent of whatever `record_review` above is doing
/// with the recording itself, the same way `artist_reviews` is independent
/// of `id_reviews` in the schema. `reopen` is the undo, routed here rather
/// than a second endpoint shape, for the same reason `record_review` does it.
#[cfg(feature = "vipunen-support")]
pub(super) async fn artist_review_verb(
    State(ui): State<Ui>,
    axum::extract::Path((passage_id, verb)): axum::extract::Path<(i64, String)>,
    axum::extract::Query(q): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let mbid = q.get("mbid").cloned();
    let name = q.get("name").cloned();
    let done = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&db, &library).map_err(|e| e.message().to_string())?;
        if verb == "reopen" {
            store.clear_artist_review(passage_id).map_err(|e| e.message().to_string())
        } else {
            let (mbid, name) = match (mbid.filter(|m| !m.is_empty()), name.filter(|n| !n.is_empty())) {
                (Some(m), Some(n)) => (m, n),
                _ => return Err("an artist correction needs both an id and a name".into()),
            };
            store
                .record_artist_review(passage_id, &mbid, &name)
                .map_err(|e| e.message().to_string())
        }
    })
    .await;
    match done {
        Ok(Ok(())) => StatusCode::NO_CONTENT.into_response(),
        Ok(Err(why)) => (StatusCode::CONFLICT, why).into_response(),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

/// The releases a recording appears on `[REQ-LIB-165]`, for naming the album.
///
/// Fetched when a candidate is chosen rather than shipped with the queue: a
/// recording can be on dozens of releases, and sending them for every
/// candidate of every card would be most of the payload for something almost
/// none of them will be asked about.
#[cfg(feature = "vipunen-support")]
pub(super) async fn review_releases(
    State(ui): State<Ui>,
    axum::extract::Path(mbid): axum::extract::Path<String>,
) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let out = tokio::task::spawn_blocking(move || {
        let lib = crate::db::Library::open_split(&db, &library).ok()?;
        serde_json::to_value(lib.releases_for(&mbid).ok()?).ok()
    })
    .await;
    match out {
        Ok(Some(v)) => axum::Json(v).into_response(),
        _ => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

