//! A passage named in a URL, by its uid `[IMPL-UID-072]`.
//!
//! A route names a passage by its uid and never by a number `[SPEC-UID-035]`.
//! A link carried to another node -- bookmarked, pasted, sent -- then opens the
//! same passage there or none. A number would open whatever that node happens
//! to number so, which is the fault `[SPEC-UID-010]` found in echo. A number, or
//! anything else not in a uid's form, is refused with 400. A uid this node does
//! not hold is 404. The handler behind either never runs.

use axum::extract::{FromRequestParts, RawPathParams};
use axum::http::{request::Parts, StatusCode};
use axum::response::{IntoResponse, Response};

use super::Ui;
use crate::db::PassageUid;

/// The passage a route's `:passage_id` names, as this node's own number.
pub(super) struct Passage(pub i64);

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
        match numbers_of(ui, vec![uid]).await?.pop().flatten() {
            Some(n) => Ok(Passage(n)),
            None => Err((StatusCode::NOT_FOUND, format!("this node holds no passage {uid}")).into_response()),
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

/// This node's number for each uid, in order, `None` for one it lacks. One
/// read of the catalogue for all of them, off the runtime.
pub(super) async fn numbers_of(ui: &Ui, uids: Vec<PassageUid>) -> Result<Vec<Option<i64>>, Response> {
    let (db, library) = (ui.db.clone(), ui.library.clone());
    let found = tokio::task::spawn_blocking(move || {
        let lib = crate::db::Library::open_split(&db, &library)?;
        uids.iter().map(|u| lib.number_of(u)).collect::<Result<Vec<_>, _>>()
    })
    .await;
    match found {
        Ok(Ok(numbers)) => Ok(numbers),
        Ok(Err(e)) => Err(super::db_failure(&e)),
        Err(e) => {
            tracing::error!("a passage lookup did not finish ({e})");
            Err(StatusCode::INTERNAL_SERVER_ERROR.into_response())
        }
    }
}
