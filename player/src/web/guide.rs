//! The in-app listener's guide `[REQ-VIS-310]` -- a full page, not a panel,
//! reached from a "Help" link every skin carries next to its own skin
//! picker. Not gated behind `vipunen-support`: the appliance needs this
//! exactly as much as the desktop does, the same posture `preference.rs`
//! already takes for the same reason.
//!
//! **Multi-tiered.** Six fixed sections -- quickstart, preference tuning,
//! importing/refining music, bringing up an empty system, an advanced-
//! features sweep, and an appendix on how the Program Director actually
//! decides -- fetched together in one request and switched client-side by
//! `guide.js`, the same "one fetch, then interact locally" shape the
//! listener-preference panel already uses.
//!
//! **Multi-language, one shown at a time.** [`GuideLang`]/[`GUIDE_LANGS`]
//! mirror `skins.rs`'s own `Skin`/`SKINS`/`skin!` pattern exactly, because
//! it is the identical problem: a fixed catalogue of named content bundles,
//! chosen client-side, remembered per browser. Adding a second language is
//! six new HTML files under `guide/<code>/` plus one line here -- no route,
//! no JS, and no architecture change.

use axum::response::{Html, IntoResponse, Response};
use axum::http::StatusCode;

use super::{js, REVALIDATE};

pub(super) struct GuideLang {
    /// A short code, `navigator.language`-comparable (`"en"`), never shown
    /// to a listener directly -- `label` is what the picker displays.
    pub(super) code: &'static str,
    pub(super) label: &'static str,
    quickstart: &'static str,
    preferences: &'static str,
    importing: &'static str,
    empty_system: &'static str,
    advanced: &'static str,
    appendix: &'static str,
}

macro_rules! guide_lang {
    ($code:literal, $label:literal) => {
        GuideLang {
            code: $code,
            label: $label,
            quickstart: include_str!(concat!("guide/", $code, "/quickstart.html")),
            preferences: include_str!(concat!("guide/", $code, "/preferences.html")),
            importing: include_str!(concat!("guide/", $code, "/importing.html")),
            empty_system: include_str!(concat!("guide/", $code, "/empty-system.html")),
            advanced: include_str!(concat!("guide/", $code, "/advanced.html")),
            appendix: include_str!(concat!("guide/", $code, "/appendix.html")),
        }
    };
}

pub(super) const GUIDE_LANGS: &[GuideLang] = &[guide_lang!("en", "English")];

const GUIDE_HTML: &str = include_str!("guide.html");
const GUIDE_JS: &str = include_str!("guide.js");

pub(super) async fn guide_page() -> impl IntoResponse {
    ([REVALIDATE], Html(GUIDE_HTML))
}

pub(super) async fn guide_js_route() -> impl IntoResponse {
    js(GUIDE_JS)
}

/// The catalogue a listener may choose between `[REQ-VIS-310]`. Served
/// rather than written into `guide.js`, the same reason `skin_list` is
/// served rather than compiled into `core.js` -- adding a language means
/// adding a row here, not editing the page that reads it.
pub(super) async fn guide_langs() -> impl IntoResponse {
    let langs: Vec<_> = GUIDE_LANGS
        .iter()
        .map(|l| serde_json::json!({ "code": l.code, "label": l.label }))
        .collect();
    axum::Json(langs)
}

/// One language's whole guide, every tier at once. A listener switching
/// tiers never re-fetches; only a language change does.
pub(super) async fn guide_content(
    axum::extract::Path(code): axum::extract::Path<String>,
) -> Response {
    let Some(lang) = GUIDE_LANGS.iter().find(|l| l.code == code) else {
        return StatusCode::NOT_FOUND.into_response();
    };
    axum::Json(serde_json::json!({
        "quickstart": lang.quickstart,
        "preferences": lang.preferences,
        "importing": lang.importing,
        "empty_system": lang.empty_system,
        "advanced": lang.advanced,
        "appendix": lang.appendix,
    }))
    .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every registered language must actually have something in every
    /// tier -- an empty `include_str!` compiles fine and would otherwise
    /// only be noticed by a listener staring at a blank section.
    #[test]
    fn every_registered_language_has_every_tier_filled_in() {
        for lang in GUIDE_LANGS {
            for (tier, text) in [
                ("quickstart", lang.quickstart),
                ("preferences", lang.preferences),
                ("importing", lang.importing),
                ("empty_system", lang.empty_system),
                ("advanced", lang.advanced),
                ("appendix", lang.appendix),
            ] {
                assert!(
                    text.len() > 200,
                    "{}/{tier} is suspiciously short ({} bytes) -- looks unwritten",
                    lang.code,
                    text.len()
                );
            }
        }
    }

    #[test]
    fn english_is_registered() {
        assert!(GUIDE_LANGS.iter().any(|l| l.code == "en"), "there must always be a fallback");
    }
}
