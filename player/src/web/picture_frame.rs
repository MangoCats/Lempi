//! The picture frame's page, its pictures, and its three settings `[SPEC068]`.
//!
//! The page and the pictures answer only the browser the player started,
//! which alone holds the token in its URL `[SPEC-PIC-300]`; anything else is
//! told there is nothing here. The settings are the Settings panel's, reached
//! as every other setting is `[SPEC-PIC-310]`. All of it lives in
//! `crate::frame`.

use axum::extract::{Path, Query};
use axum::http::{header, StatusCode};
use axum::response::{Html, IntoResponse, Response};

const FRAME_HTML: &str = include_str!("frame.html");

#[derive(serde::Deserialize)]
pub(super) struct Token {
    #[serde(default)]
    t: String,
    last: Option<usize>,
}

pub(super) async fn page(Query(q): Query<Token>) -> Response {
    if !crate::frame::token_ok(&q.t) {
        return StatusCode::NOT_FOUND.into_response();
    }
    ([(header::CACHE_CONTROL, "no-store")], Html(FRAME_HTML)).into_response()
}

pub(super) async fn next(Query(q): Query<Token>) -> Response {
    match tokio::task::spawn_blocking(move || crate::frame::next(&q.t, q.last)).await {
        Ok(Some(v)) => ([(header::CACHE_CONTROL, "no-store")], axum::Json(v)).into_response(),
        _ => StatusCode::NOT_FOUND.into_response(),
    }
}

pub(super) async fn picture(Path(n): Path<usize>, Query(q): Query<Token>) -> Response {
    let got = tokio::task::spawn_blocking(move || {
        crate::frame::picture(&q.t, n).and_then(|path| std::fs::read(&path).ok().map(|bytes| (path, bytes)))
    })
    .await;
    match got {
        Ok(Some((path, bytes))) => (
            [(header::CONTENT_TYPE, crate::frame::content_type(&path)), (header::CACHE_CONTROL, "private, max-age=3600")],
            bytes,
        )
            .into_response(),
        _ => StatusCode::NOT_FOUND.into_response(),
    }
}

/// Where the frame stands `[SPEC-PIC-200]`; 404 on a host with none, so the
/// panel draws no row there.
pub(super) async fn settings() -> Response {
    match tokio::task::spawn_blocking(crate::frame::status).await {
        Ok(Some(v)) => axum::Json(v).into_response(),
        _ => StatusCode::NOT_FOUND.into_response(),
    }
}

fn answered(got: Result<Result<serde_json::Value, String>, tokio::task::JoinError>) -> Response {
    match got {
        Ok(Ok(v)) => axum::Json(v).into_response(),
        Ok(Err(e)) => (StatusCode::CONFLICT, axum::Json(serde_json::json!({"error": e}))).into_response(),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

pub(super) async fn set_on(Path(on): Path<String>) -> Response {
    let want = matches!(on.as_str(), "on" | "true" | "1");
    // Off ends a browser, which may take a few seconds: off the runtime.
    answered(tokio::task::spawn_blocking(move || crate::frame::set_on(want)).await)
}

#[derive(serde::Deserialize)]
pub(super) struct Folder {
    folder: String,
}

pub(super) async fn set_folder(axum::Json(b): axum::Json<Folder>) -> Response {
    answered(tokio::task::spawn_blocking(move || crate::frame::set_folder(&b.folder)).await)
}

pub(super) async fn set_clock(Path(pct): Path<u32>) -> Response {
    answered(tokio::task::spawn_blocking(move || crate::frame::set_clock_pct(pct)).await)
}
