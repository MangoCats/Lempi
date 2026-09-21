//! The three document-shaped skins `[REQ-VIS-160]` and the catalogue/asset
//! routes that serve them.

use axum::http::StatusCode;
use axum::response::{Html, IntoResponse};

use super::{js, REVALIDATE};

pub(super) struct Skin {
    pub(super) name: &'static str,
    pub(super) label: &'static str,
    pub(super) html: &'static str,
    pub(super) css: &'static str,
    pub(super) js: &'static str,
}

macro_rules! skin {
    ($name:literal, $label:literal) => {
        Skin {
            name: $name,
            label: $label,
            html: include_str!(concat!("skins/", $name, "/skin.html")),
            css: include_str!(concat!("skins/", $name, "/skin.css")),
            js: include_str!(concat!("skins/", $name, "/skin.js")),
        }
    };
}

pub(super) const SKINS: &[Skin] = &[
    skin!("lempi", "Lempi"),
    skin!("mulibplay", "MuLibPlay"),
    skin!("winamp", "WinAmp"),
];

/// What the browser may choose between. The catalogue is served rather than
/// written into each skin, so adding one does not mean editing the others to
/// list it.
pub(super) async fn skin_list() -> impl IntoResponse {
    let names: Vec<_> = SKINS
        .iter()
        .map(|s| serde_json::json!({ "name": s.name, "label": s.label }))
        .collect();
    axum::Json(names)
}

/// A skin is exactly three files. The set is fixed, so an unknown name is a
/// 404 rather than anything that could reach outside the binary.
pub(super) async fn skin_asset(
    axum::extract::Path((name, file)): axum::extract::Path<(String, String)>,
) -> axum::response::Response {
    let Some(skin) = SKINS.iter().find(|s| s.name == name) else {
        return StatusCode::NOT_FOUND.into_response();
    };
    match file.as_str() {
        "skin.html" => ([REVALIDATE], Html(skin.html)).into_response(),
        "skin.css" => (
            [
                (axum::http::header::CONTENT_TYPE, "text/css; charset=utf-8"),
                REVALIDATE,
            ],
            skin.css,
        )
            .into_response(),
        "skin.js" => js(skin.js).into_response(),
        _ => StatusCode::NOT_FOUND.into_response(),
    }
}
