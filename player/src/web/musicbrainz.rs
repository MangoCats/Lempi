//! Live MusicBrainz search from the review page, rate-limited to one
//! request in flight at a time.

#[cfg(feature = "vipunen-support")]
use axum::http::StatusCode;
#[cfg(feature = "vipunen-support")]
use axum::response::IntoResponse;


/// Serialises MusicBrainz search calls to roughly one per second
/// `[SPEC-SUI-196]` -- so opening two browser tabs and searching from both
/// still respects the API's limit, because the discipline lives in this one
/// process rather than in client behaviour a second tab simply would not
/// share. Same rate `tools/fetch_releases.py` already uses for the same API.
#[cfg(feature = "vipunen-support")]
pub(super) static MB_LAST_REQUEST: std::sync::Mutex<Option<std::time::Instant>> = std::sync::Mutex::new(None);

#[cfg(feature = "vipunen-support")]
pub(super) async fn mb_rate_limit() {
    use std::time::{Duration, Instant};
    // The slot is reserved while holding the lock, and only the wait itself
    // happens after releasing it -- an `await` under a `std::sync::Mutex`
    // guard would hold it across a suspend point, which is the bug this
    // avoids rather than a style preference.
    let target = {
        let mut last = MB_LAST_REQUEST.lock().unwrap();
        let now = Instant::now();
        let earliest = last.map(|t| t + Duration::from_secs(1)).unwrap_or(now);
        let target = earliest.max(now);
        *last = Some(target);
        target
    };
    let now = Instant::now();
    if target > now {
        tokio::time::sleep(target - now).await;
    }
}

/// The same contact-bearing agent string `tools/fetch_releases.py` already
/// sends for the same API -- MusicBrainz asks for one and enforces it.
#[cfg(feature = "vipunen-support")]
pub(super) const MB_USER_AGENT: &str = "Lempi-Vipunen/0.1 ( https://github.com/MangoCats/Lempi )";

#[cfg(feature = "vipunen-support")]
#[derive(serde::Deserialize)]
pub(super) struct MbSearchQuery {
    kind: String,
    q: String,
}

/// Search MusicBrainz directly `[SPEC-SUI-196]`, `[REQ-LIB-180]` -- for the
/// cases the fingerprint queue cannot reach: self-released audio with no
/// AcoustID entry, and a remaster or bootleg it has never indexed. The one
/// route the browser is allowed to reach musicbrainz.org through, so the rate
/// limit above cannot be bypassed by calling the API directly from the page.
///
/// Results come back shaped exactly like a fingerprint [`crate::db::Suggestion`]
/// so the page renders a searched match and a suggested one identically --
/// choosing either is the same action from the reviewer's side of the page.
#[cfg(feature = "vipunen-support")]
pub(super) async fn musicbrainz_search(
    axum::extract::Query(q): axum::extract::Query<MbSearchQuery>,
) -> axum::response::Response {
    if !matches!(q.kind.as_str(), "recording" | "artist" | "release") {
        return (StatusCode::BAD_REQUEST, "kind must be recording, artist or release")
            .into_response();
    }
    if q.q.trim().is_empty() {
        return axum::Json(Vec::<crate::db::Suggestion>::new()).into_response();
    }

    mb_rate_limit().await;

    let client = match reqwest::Client::builder().user_agent(MB_USER_AGENT).build() {
        Ok(c) => c,
        Err(_) => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    };
    let url = format!("https://musicbrainz.org/ws/2/{}", q.kind);
    let resp = client
        .get(&url)
        .query(&[("query", q.q.as_str()), ("fmt", "json"), ("limit", "15")])
        .send()
        .await;
    let body: serde_json::Value = match resp {
        Ok(r) if r.status().is_success() => match r.json().await {
            Ok(v) => v,
            Err(_) => return StatusCode::BAD_GATEWAY.into_response(),
        },
        _ => return StatusCode::BAD_GATEWAY.into_response(),
    };

    axum::Json(parse_mb_results(&q.kind, &body)).into_response()
}

/// One track of a release's own tracklist -- what
/// [`release_tracks`] returns, and what `review.js`'s release-kind search
/// renders as its second step once a release is picked `[SPEC-RIP-074]`.
#[cfg(feature = "vipunen-support")]
#[derive(serde::Serialize)]
pub(super) struct ReleaseTrack {
    pub(super) position: i64,
    pub(super) mbid: String,
    pub(super) title: Option<String>,
}

/// A release's own tracklist, by position -- the direction
/// [`musicbrainz_search`]`(kind=release)` cannot answer on its own: search
/// finds the *album*, this finds which *recording* sits at which track
/// number on it, so a person identifying a CD by its printed track listing
/// (`[SPEC028] §3`) can pick the one at the position they're looking at.
/// Closes the release/track-search gap `[SPEC-SUI-197]` already named as
/// designed but not built -- CD ripping's manual-fallback path is a second,
/// independent caller for it, not a redesign.
#[cfg(feature = "vipunen-support")]
pub(super) async fn release_tracks(
    axum::extract::Path(mbid): axum::extract::Path<String>,
) -> axum::response::Response {
    mb_rate_limit().await;

    let client = match reqwest::Client::builder().user_agent(MB_USER_AGENT).build() {
        Ok(c) => c,
        Err(_) => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    };
    let url = format!("https://musicbrainz.org/ws/2/release/{mbid}");
    let resp = client
        .get(&url)
        .query(&[("inc", "recordings"), ("fmt", "json")])
        .send()
        .await;
    let body: serde_json::Value = match resp {
        Ok(r) if r.status().is_success() => match r.json().await {
            Ok(v) => v,
            Err(_) => return StatusCode::BAD_GATEWAY.into_response(),
        },
        Ok(r) if r.status().as_u16() == 404 => {
            return axum::Json(Vec::<ReleaseTrack>::new()).into_response()
        }
        _ => return StatusCode::BAD_GATEWAY.into_response(),
    };

    axum::Json(parse_release_tracks(&body)).into_response()
}

/// The parsing half of [`release_tracks`], kept separate from the HTTP call
/// for the same reason [`parse_mb_results`] is -- checkable against a
/// captured response with no network. A release's `media` array holds one
/// entry per disc; multi-disc releases are read across all of them, not
/// only the first, since position numbering restarts per medium and a
/// caller matching by position alone would otherwise silently prefer disc 1.
#[cfg(feature = "vipunen-support")]
pub(super) fn parse_release_tracks(body: &serde_json::Value) -> Vec<ReleaseTrack> {
    body.get("media")
        .and_then(|v| v.as_array())
        .into_iter()
        .flatten()
        .flat_map(|m| m.get("tracks").and_then(|v| v.as_array()).into_iter().flatten())
        .filter_map(|t| {
            let position = t.get("position").and_then(|v| v.as_i64())?;
            let rec = t.get("recording")?;
            let mbid = rec.get("id")?.as_str()?.to_string();
            let title = rec
                .get("title")
                .and_then(|v| v.as_str())
                .or_else(|| t.get("title").and_then(|v| v.as_str()))
                .map(str::to_string);
            Some(ReleaseTrack { position, mbid, title })
        })
        .collect()
}

/// The parsing half of [`musicbrainz_search`], kept separate from the HTTP
/// call so it can be checked against a captured response without a network
/// -- MusicBrainz's own JSON shape differs per entity (`name` vs. `title`,
/// present or absent `artist-credit`), and that is exactly the part worth a
/// test's attention, not the fact that `reqwest` can fetch a URL.
#[cfg(feature = "vipunen-support")]
pub(super) fn parse_mb_results(kind: &str, body: &serde_json::Value) -> Vec<crate::db::Suggestion> {
    let key = match kind {
        "recording" => "recordings",
        "artist" => "artists",
        _ => "releases",
    };
    let is_artist = kind == "artist";
    body.get(key)
        .and_then(|v| v.as_array())
        .into_iter()
        .flatten()
        .filter_map(|item| {
            let mbid = item.get("id")?.as_str()?.to_string();
            let title = if is_artist { item.get("name") } else { item.get("title") }
                .and_then(|v| v.as_str())
                .map(str::to_string);
            let artist = item
                .get("artist-credit")
                .and_then(|v| v.as_array())
                .and_then(|a| a.first())
                .and_then(|c| c.get("name"))
                .and_then(|v| v.as_str())
                .map(str::to_string);
            let score = item.get("score").and_then(|v| v.as_f64()).unwrap_or(0.0) / 100.0;
            Some(crate::db::Suggestion { mbid, title, artist, score })
        })
        .collect()
}

