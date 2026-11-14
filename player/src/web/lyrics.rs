//! The lyric editor's two routes `[SPEC-LYR-172]`, `[SPEC-LYR-174]`: what the
//! page shows for one passage, and saving that passage's words.
//!
//! `GET /lyrics/:passage_id` itself, the text a skin shows, is in `media.rs`
//! beside the cover art, where it has always been.
//!
//! **Not gated behind `vipunen-support`**, for the reason the preference panel
//! is not: words are typed where they are heard, and that is an appliance as
//! often as the desktop.

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::IntoResponse;

use super::Ui;

/// Everything the editor shows for a passage: its naming, its own entry, and
/// every other source of words in the order of `[SPEC-LYR-100]`, read-only.
///
/// A failure is answered as the save answers one ([`super::db_failure`]):
/// 503 while the file is busy, 500 otherwise. Until 2026-11-14 this was
/// always 500, so the page read *could not be read* where the save, a moment
/// later, said *busy, try again*.
pub(super) async fn lyrics_sources(
    State(ui): State<Ui>,
    axum::extract::Path(passage_id): axum::extract::Path<i64>,
) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let got = tokio::task::spawn_blocking(move || {
        crate::db::Library::open_split(&db, &library).and_then(|lib| lib.lyrics_view(passage_id))
    })
    .await;
    match got {
        Ok(Ok(Some(view))) => axum::Json(view).into_response(),
        Ok(Ok(None)) => StatusCode::NOT_FOUND.into_response(),
        Ok(Err(e)) => super::db_failure(&e),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

/// What a save carries. A body, not a query string: the longest text held on
/// 2026-11-12 was 5,820 characters.
#[derive(serde::Deserialize)]
pub(super) struct LyricsEdit {
    #[serde(default)]
    text: String,
    #[serde(default)]
    no_words: bool,
}

/// Save the words of one passage, and of no other `[SPEC-LYR-174]`.
///
/// An empty text clears them; `no_words` marks the passage as having none
/// `[SPEC-LYR-188]`. Answers the entry as stored, `null` once cleared. Nothing
/// is asked of the engine: words change what a page shows, not what plays.
///
/// **The one refusal that is the request's is a text over 64 KiB, and it is
/// 413**, answered before the file is opened. Any other failure is the
/// server's: 503 while the file is busy, 500 otherwise ([`super::db_failure`]).
/// Until 2026-11-13 every failure was a 400, so a listener held by a long read
/// told the editor its request was wrong.
pub(super) async fn save_lyrics(
    State(ui): State<Ui>,
    axum::extract::Path(passage_id): axum::extract::Path<i64>,
    axum::Json(edit): axum::Json<LyricsEdit>,
) -> axum::response::Response {
    if let Some(why) = crate::db::lyrics_refusal(&edit.text) {
        return (StatusCode::PAYLOAD_TOO_LARGE, why).into_response();
    }
    let db = ui.db.clone();
    let library = ui.library.clone();
    let done = tokio::task::spawn_blocking(move || {
        let origin = crate::discovery::host_name();
        crate::db::PlayerStore::open_split(&db, &library)
            .and_then(|store| store.save_lyrics(passage_id, &edit.text, edit.no_words, &origin))
    })
    .await;
    match done {
        Ok(Ok(Some(own))) => axum::Json(serde_json::json!({ "own": own })).into_response(),
        Ok(Ok(None)) => StatusCode::NOT_FOUND.into_response(),
        Ok(Err(e)) => super::db_failure(&e),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}
