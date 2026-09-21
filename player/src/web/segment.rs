//! The segmentation-cascade review queue `[SPEC024 §7]`: passages the DAO
//! cascade drew boundaries for, worklisted the same way the identification
//! queue is (`review.rs`), plus a lighter "accept as-is" verb than opening
//! the full waveform editor `[SPEC-SA-125]`. Gated behind `vipunen-support`
//! along with everything else reached from the review page `[SPEC-SUI-190]`.

#[cfg(feature = "vipunen-support")]
use axum::extract::State;
#[cfg(feature = "vipunen-support")]
use axum::http::StatusCode;
#[cfg(feature = "vipunen-support")]
use axum::response::IntoResponse;

#[cfg(feature = "vipunen-support")]
use super::Ui;

/// The passages still waiting on a look `[SPEC-SA-124]`.
///
/// Progress travels with the list, the same reason `review_queue`
/// (`review.rs`) sends it: an empty list alone cannot say whether the
/// cascade has never been run, ran and produced nothing new, or everything
/// it produced has already been confirmed.
#[cfg(feature = "vipunen-support")]
pub(super) async fn segment_queue(State(ui): State<Ui>) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let out = tokio::task::spawn_blocking(move || {
        let lib = crate::db::Library::open_split(&db, &library).ok()?;
        let items = lib.segment_queue(crate::BROWSE_LIMIT).ok()?;
        serde_json::to_value(serde_json::json!({
            "progress": lib.segment_progress(),
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

/// Accept a cascade-drawn span as-is `[SPEC-SA-125]` -- the fourth verb
/// alongside a correction (`edit_review`, `edit.rs`), parallel to
/// `record_review`'s `kept` for an identification: nothing to change, only
/// a decision to record.
#[cfg(feature = "vipunen-support")]
pub(super) async fn accept_segment(
    State(ui): State<Ui>,
    axum::extract::Path(passage_id): axum::extract::Path<i64>,
) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let done = tokio::task::spawn_blocking(move || {
        crate::db::PlayerStore::open_split(&db, &library)
            .map_err(|e| e.message().to_string())?
            .accept_segment(passage_id)
            .map_err(|e| e.message().to_string())
    })
    .await;
    match done {
        Ok(Ok(())) => StatusCode::NO_CONTENT.into_response(),
        // The reason travels back as text, same as `record_review`'s and
        // `edit_review`'s own refusals -- "not a cascade passage" or
        // "already applied" is exactly what the operator needs to read, not
        // a bare status code.
        Ok(Err(why)) => (StatusCode::CONFLICT, why).into_response(),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}
