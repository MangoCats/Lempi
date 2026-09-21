//! Lyrics and cover art, served from the library the same read-only way
//! everything else here reads it.

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::IntoResponse;

use super::{Ui, REVALIDATE};

/// The passage's cover art `[REQ-VIS-170]`.
///
/// Read from the audio file, never fetched: playback must not depend on a live
/// external service `[REQ-NEG-100]`, and the Cover Art Archive is precisely the
/// dependency that forbids. Files without a picture are a plain 404, which is
/// what lets a skin ask unconditionally and hide the element on failure.
///
/// Served by passage rather than by album because that is the id a skin has in
/// hand, and it makes the URL stable enough to cache for a day.
/// One passage's words `[SPEC-LYR-040]`.
///
/// **An endpoint rather than a snapshot field.** The snapshot is published on
/// every tick and read by every skin; up to 5.8 KB of text in it would be sent
/// hundreds of times to say what changes once a song. A skin fetches this when
/// the passage changes, which it already notices.
///
/// Plain text, because that is what the words are — a static block, as
/// MuLibPlay showed them `[SPEC-LYR-045]`. 404 means the library has none, which
/// is the ordinary case for 72% of passages and not an error worth dressing up.
pub(super) async fn lyrics(
    State(ui): State<Ui>,
    axum::extract::Path(passage_id): axum::extract::Path<i64>,
) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    // The query blocks, so it belongs off the runtime.
    let found = tokio::task::spawn_blocking(move || {
        crate::db::Library::open_split(&db, &library).ok().and_then(|lib| lib.lyrics(passage_id))
    })
    .await
    .ok()
    .flatten();
    match found {
        Some(text) => (
            [
                (axum::http::header::CONTENT_TYPE, "text/plain; charset=utf-8"),
                REVALIDATE,
            ],
            text,
        )
            .into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

pub(super) async fn cover_art(
    State(ui): State<Ui>,
    axum::extract::Path(passage_id): axum::extract::Path<i64>,
) -> axum::response::Response {
    // Both the query and the file read block, so they belong off the runtime.
    art_response(ui, passage_id, false).await
}

/// The back of the sleeve `[REQ-VIS-170]`, for skins that show it.
pub(super) async fn cover_art_back(
    State(ui): State<Ui>,
    axum::extract::Path(passage_id): axum::extract::Path<i64>,
) -> axum::response::Response {
    art_response(ui, passage_id, true).await
}

/// One passage's cover, from wherever it can be found.
///
/// Three sources, cheapest first `[REQ-VIS-170]`:
///
/// 1. **the audio file's own picture** -- 64% of this library carries one;
/// 2. **a cover file beside it** -- `folder.jpg` and its spellings, which 83%
///    of the remaining files have. It was already on disk and nothing looked;
/// 3. **the fetched archive**, keyed by the release Vipunen chose, which is the
///    only one of the three that can tell two albums in one folder apart.
///
/// A back cover skips step 1: embedded back covers are vanishingly rare, and
/// `artwork()` deliberately falls back to *any* picture, which would return
/// the front and label it the back.
pub(super) async fn art_response(ui: Ui, passage_id: i64, back: bool) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let found = tokio::task::spawn_blocking(move || {
        let lib = crate::db::Library::open_split(&db, &library).ok()?;
        let path = lib.passage_path(passage_id).ok();
        if !back {
            if let Some(a) = path.as_deref().and_then(crate::tags::artwork) {
                if a.data.len() >= crate::tags::MIN_ART_BYTES {
                    return Some(a);
                }
            }
        }
        if let Some(a) = path.as_deref().and_then(|p| crate::tags::sibling_art(p, back)) {
            return Some(a);
        }
        lib.stored_art(passage_id, back)
    })
    .await;

    match found {
        Ok(Some(art)) => (
            [
                (axum::http::header::CONTENT_TYPE, art.media_type),
                // The art of a given passage does not change; let the browser
                // keep it rather than re-reading the file on every passage change.
                (axum::http::header::CACHE_CONTROL, "public, max-age=86400".into()),
            ],
            art.data,
        )
            .into_response(),
        _ => StatusCode::NOT_FOUND.into_response(),
    }
}

