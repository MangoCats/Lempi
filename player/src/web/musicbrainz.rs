//! Live MusicBrainz search from the review page, rate-limited to one
//! request in flight at a time.

#[cfg(feature = "vipunen-support")]
use axum::http::StatusCode;
#[cfg(feature = "vipunen-support")]
use axum::response::IntoResponse;


/// The least time between two MusicBrainz requests made on this machine
/// `[SPEC-MBP-020]`: their published one a second, with a fifth to spare --
/// the margin the maintainer set on 2026-11-19. `tools/mb_pace.py`'s
/// `MIN_INTERVAL_S`; the two must agree.
#[cfg(feature = "vipunen-support")]
pub(super) const MB_MIN_INTERVAL_S: f64 = 1.2;

/// A reservation further ahead than this is a clock that was set back, not a
/// queue of callers, and is forgotten -- `mb_pace.STALE_AHEAD_S`.
#[cfg(feature = "vipunen-support")]
const MB_STALE_AHEAD_S: f64 = 60.0;

/// The file every MusicBrainz caller on this machine paces itself by
/// `[SPEC-MBP-030]`: the player here and every tool `tools/mb_pace.py`
/// serves, so a search on the review page while a console job asks
/// MusicBrainz still keeps the pair 1.2 s apart. The same path that module
/// computes: `LEMPI_MB_PACE_FILE`, else `%LOCALAPPDATA%\Lempi\` on Windows,
/// else `$XDG_CACHE_HOME/lempi/` or `~/.cache/lempi/`.
#[cfg(feature = "vipunen-support")]
pub(super) fn mb_pace_file() -> std::path::PathBuf {
    use std::path::PathBuf;
    if let Some(p) = std::env::var_os("LEMPI_MB_PACE_FILE") {
        return PathBuf::from(p);
    }
    #[cfg(windows)]
    if let Some(l) = std::env::var_os("LOCALAPPDATA") {
        return PathBuf::from(l).join("Lempi").join("musicbrainz.pace");
    }
    let base = std::env::var_os("XDG_CACHE_HOME").map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".cache")))
        .unwrap_or_else(std::env::temp_dir);
    base.join("lempi").join("musicbrainz.pace")
}

/// Take the next free moment in the pace file at `path`, at least `interval`
/// seconds after the last one taken, record it, and return it -- seconds
/// since the epoch, as `mb_pace.reserve` does, in the same text. Locked while
/// read and written, so processes sharing the file take turns.
#[cfg(feature = "vipunen-support")]
pub(super) fn mb_reserve_at(path: &std::path::Path, interval: f64, now: f64) -> std::io::Result<f64> {
    use std::io::{Read, Seek, SeekFrom, Write};
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let mut f = std::fs::OpenOptions::new().read(true).write(true).create(true).truncate(false).open(path)?;
    f.lock()?;
    let taken = (|| -> std::io::Result<f64> {
        let mut text = String::new();
        f.seek(SeekFrom::Start(0))?;
        f.read_to_string(&mut text)?;
        let last = text.trim().parse::<f64>().ok().filter(|&t| t <= now + MB_STALE_AHEAD_S);
        let slot = last.map_or(now, |t| (t + interval).max(now));
        f.seek(SeekFrom::Start(0))?;
        f.set_len(0)?;
        write!(f, "{slot:.3}")?;
        Ok(slot)
    })();
    let _ = f.unlock();
    taken
}

/// This process's own pace, for when the shared file cannot be used: a
/// second tab still waits its turn, though a console job no longer does.
#[cfg(feature = "vipunen-support")]
pub(super) static MB_LAST_REQUEST: std::sync::Mutex<Option<std::time::Instant>> = std::sync::Mutex::new(None);

/// Wait for this machine's next MusicBrainz turn `[SPEC-SUI-196]`,
/// `[SPEC-MBP-030]` -- so two browser tabs, or a tab and a console job, still
/// keep the API's limit, because the discipline lives below every caller
/// rather than in behaviour a second caller would not share.
#[cfg(feature = "vipunen-support")]
pub(super) async fn mb_rate_limit() {
    use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
    let epoch_now = || SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64());
    let shared = tokio::task::spawn_blocking(move || {
        mb_reserve_at(&mb_pace_file(), MB_MIN_INTERVAL_S, epoch_now())
    }).await;
    match shared {
        Ok(Ok(slot)) => {
            let wait = slot - epoch_now();
            if wait > 0.0 {
                tokio::time::sleep(Duration::from_secs_f64(wait)).await;
            }
            return;
        }
        // Said, never silent: a pace the console's tools do not share is one
        // that can let the two together past the limit [GDE-DEP-060].
        Ok(Err(e)) => tracing::warn!("the MusicBrainz pace file {} could not be used ({e}); \
                                      pacing this process alone", mb_pace_file().display()),
        Err(e) => tracing::warn!("the MusicBrainz pace could not be taken ({e}); pacing this process alone"),
    }
    // The slot is reserved while holding the lock, and only the wait itself
    // happens after releasing it -- an `await` under a `std::sync::Mutex`
    // guard would hold it across a suspend point, which is the bug this
    // avoids rather than a style preference.
    let target = {
        let mut last = MB_LAST_REQUEST.lock().unwrap();
        let now = Instant::now();
        let earliest = last.map(|t| t + Duration::from_secs_f64(MB_MIN_INTERVAL_S)).unwrap_or(now);
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

/// The shared pace `[SPEC-MBP-020]`, `[SPEC-MBP-030]`, against a file of each
/// test's own: the times are given, not read, so no test waits.
#[cfg(all(test, feature = "vipunen-support"))]
mod pace_tests {
    use super::*;

    fn fresh(name: &str) -> std::path::PathBuf {
        let p = std::env::temp_dir().join(format!("lempi-mb-pace-{}-{name}", std::process::id()));
        let _ = std::fs::remove_file(&p);
        p
    }

    #[test]
    fn the_interval_is_the_limit_with_a_fifth_to_spare() {
        assert!((MB_MIN_INTERVAL_S - 1.2).abs() < 1e-9, "tools/mb_pace.py's MIN_INTERVAL_S is 1.2 s");
    }

    #[test]
    fn a_request_soon_after_another_waits_the_interval_and_a_late_one_does_not() {
        let p = fresh("turns");
        assert_eq!(mb_reserve_at(&p, MB_MIN_INTERVAL_S, 1000.0).unwrap(), 1000.0, "the first asks at once");
        let b = mb_reserve_at(&p, MB_MIN_INTERVAL_S, 1000.1).unwrap();
        assert!((b - 1001.2).abs() < 1e-6, "the second, 0.1 s later, waits for 1001.2: {b}");
        let c = mb_reserve_at(&p, MB_MIN_INTERVAL_S, 1000.2).unwrap();
        assert!((c - 1002.4).abs() < 1e-6, "a third queues behind the second: {c}");
        assert_eq!(mb_reserve_at(&p, MB_MIN_INTERVAL_S, 1010.0).unwrap(), 1010.0, "one long after asks at once");
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn the_file_reads_as_the_tools_write_it_and_they_read_ours() {
        let p = fresh("text");
        // What `mb_pace.reserve` writes: seconds since the epoch, three places.
        std::fs::write(&p, "2000.500").unwrap();
        let slot = mb_reserve_at(&p, MB_MIN_INTERVAL_S, 2000.0).unwrap();
        assert!((slot - 2001.7).abs() < 1e-6, "a tool's request at 2000.5 holds the next until 2001.7: {slot}");
        assert_eq!(std::fs::read_to_string(&p).unwrap(), "2001.700", "and the file says so, as the tools read it");
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn a_clock_set_back_or_a_damaged_file_does_not_hold_requests_back() {
        let p = fresh("odd");
        std::fs::write(&p, "999999.000").unwrap();
        assert_eq!(mb_reserve_at(&p, MB_MIN_INTERVAL_S, 1000.0).unwrap(), 1000.0,
                   "a reservation hours ahead is a clock set back, not a queue");
        std::fs::write(&p, "not a time").unwrap();
        assert_eq!(mb_reserve_at(&p, MB_MIN_INTERVAL_S, 1000.0).unwrap(), 1000.0, "nor is text that is no time");
        let _ = std::fs::remove_file(&p);
    }
}

