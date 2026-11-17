//! A passage named in a URL, by its uid `[IMPL-UID-072]`.
//!
//! A route names a passage by its uid and never by a number `[SPEC-UID-035]`.
//! A link carried to another node -- bookmarked, pasted, sent -- then opens the
//! same passage there or none. A number would open whatever that node happens
//! to number so, which is the fault `[SPEC-UID-010]` found in echo. A number, or
//! anything else not in a uid's form, is refused with 400. A uid this node does
//! not hold is 404. The handler behind either never runs.
//!
//! **The pair opened to find the number is the handler's too** `[IMPL-UID-073]`.
//! Until 2026-11-17 the extractor opened it, closed it, and the handler opened it
//! again: two opens per request where a number in the URL had cost one. The
//! routes that pay it most are the ones a page asks most, a cover and the words
//! at every track change, and dozens of covers for an album list. A handler
//! that reads through a [`Library`](crate::db::Library) takes this one
//! ([`Passage::with_library`]); one that writes opens its own store, as before,
//! and closes this first ([`Passage::number`]).

use axum::extract::{FromRequestParts, RawPathParams};
use axum::http::{request::Parts, StatusCode};
use axum::response::{IntoResponse, Response};

use super::Ui;
use crate::db::{Library, PassageUid};

/// The passage a route's `:passage_id` names, as this node's own number, with
/// the read-only pair its number was found through.
pub(super) struct Passage {
    id: i64,
    lib: Library,
}

impl Passage {
    /// The number, and the pair it was found through, for a handler that reads.
    pub(super) fn with_library(self) -> (i64, Library) {
        (self.id, self.lib)
    }

    /// The number alone, the pair closed now: for a handler that reads nothing
    /// of the catalogue, or writes through a store of its own.
    pub(super) fn number(self) -> i64 {
        self.id
    }
}

#[axum::async_trait]
impl FromRequestParts<Ui> for Passage {
    type Rejection = Response;

    async fn from_request_parts(parts: &mut Parts, ui: &Ui) -> Result<Self, Response> {
        let params = RawPathParams::from_request_parts(parts, ui).await.map_err(IntoResponse::into_response)?;
        let Some(text) = params.iter().find(|(k, _)| *k == "passage_id").map(|(_, v)| v.to_owned()) else {
            // A route wired to this extractor without the parameter: the
            // server's fault, and a test's to find.
            tracing::error!("a passage route has no :passage_id ({})", parts.uri.path());
            return Err(StatusCode::INTERNAL_SERVER_ERROR.into_response());
        };
        let uid = uid_or_refuse(&text)?;
        let (db, library) = (ui.db.clone(), ui.library.clone());
        let found = tokio::task::spawn_blocking(move || {
            let lib = Library::open_split(&db, &library)?;
            lib.number_of(&uid).map(|n| n.map(|id| Passage { id, lib }))
        })
        .await;
        match found {
            Ok(Ok(Some(p))) => Ok(p),
            Ok(Ok(None)) => Err((StatusCode::NOT_FOUND, format!("this node holds no passage {uid}")).into_response()),
            Ok(Err(e)) => Err(super::db_failure(&e)),
            Err(e) => {
                tracing::error!("a passage lookup did not finish ({e})");
                Err(StatusCode::INTERNAL_SERVER_ERROR.into_response())
            }
        }
    }
}

/// The uid `text` writes, or the 400 that refuses it.
///
/// The error is the answer itself, as an extractor's rejection is, so its size
/// is axum's `Response`'s; it is built once, for a request already refused.
#[allow(clippy::result_large_err)]
pub(super) fn uid_or_refuse(text: &str) -> Result<PassageUid, Response> {
    PassageUid::parse(text).ok_or_else(|| {
        (
            StatusCode::BAD_REQUEST,
            format!("{text:?} is not a passage uid: a passage is named by its uid, which is the same on \
                     every node, and never by a number, which is not [IMPL-UID-072]"),
        )
            .into_response()
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    async fn answer(r: Response) -> (StatusCode, String) {
        let status = r.status();
        let bytes = axum::body::to_bytes(r.into_body(), usize::MAX).await.unwrap();
        (status, String::from_utf8_lossy(&bytes).into_owned())
    }

    /// `[IMPL-UID-073]`: a handler that reads answers through the pair its
    /// passage was found through. It is handed no `Ui`, so it has no path to open
    /// a second pair from, and it still answers in full from that one connection:
    /// the words, their sources and the passage's profile.
    #[tokio::test]
    async fn a_reading_handler_answers_through_the_pair_its_passage_was_found_through() {
        let d = std::env::temp_dir().join(format!("lempi-passage-ref-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        let db = d.join("listener.db");
        let c = rusqlite::Connection::open(&db).unwrap();
        c.execute_batch(include_str!("../../../sql/schema.sql")).unwrap();
        c.execute_batch(
            "PRAGMA foreign_keys = OFF;
             INSERT INTO files (file_id, audio_md5, path, size_bytes, mtime, format, duration_ms,
                                first_seen, last_seen) VALUES (1, 'aaa', '/m/a.mp3', 1, 0, 'mp3', 200000, 't', 't');
             INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, boundary_src)
                 VALUES (7, 1, 'radio', 0, 200000, 'test');
             UPDATE passages SET passage_uid = printf('%032x', passage_id);",
        )
        .unwrap();
        drop(c);
        let store = crate::db::PlayerStore::open_split(&db, &db).unwrap();
        store.save_lyrics(7, "the words", false, "here").unwrap();
        drop(store);
        let found = || Passage { id: 7, lib: Library::open_split(&db, &db).unwrap() };

        let (status, text) = answer(super::super::media::lyrics(found()).await).await;
        assert_eq!((status, text.as_str()), (StatusCode::OK, "the words"), "GET /lyrics");
        let (status, text) = answer(super::super::lyrics::lyrics_sources(found()).await).await;
        assert!(status == StatusCode::OK && text.contains("the words"), "GET /lyrics/sources: {status} {text}");
        let (status, text) = answer(super::super::browse::passage_info(found()).await).await;
        let uid = crate::test_catalogue::uid(7);
        assert!(status == StatusCode::OK && text.contains(&uid), "GET /passage/info: {status} {text}");
        let _ = std::fs::remove_dir_all(&d);
    }
}
