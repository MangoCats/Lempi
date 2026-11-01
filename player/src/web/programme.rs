//! The Programme page's data `[SPEC-PGM-400]`: the programme this node runs,
//! its time slots and their seeds, and saving an edit of it.
//!
//! Words per SPEC023 `[ENT-PROGRAMME-010]`: the programme is the whole day,
//! a time slot one part of it, a seed a recording a slot names.

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::IntoResponse;

use super::Ui;
use crate::db::{ProgrammeView, Slot};

/// A seed as a person reads it: the recording, its artist and its album.
#[derive(serde::Serialize)]
struct SeedView {
    mbid: String,
    title: Option<String>,
    artist: Option<String>,
    album: Option<String>,
    /// `Some(false)` for a seed with no flavor, which the Director passes
    /// over because it shapes nothing `[SPEC-DIR-142]`; the page flags it.
    /// `None` when the library could not be asked.
    has_flavor: Option<bool>,
}

#[derive(serde::Serialize)]
struct SlotView {
    id: Option<i64>,
    name: String,
    start: Option<String>,
    /// When the next time slot starts, so the page can show a range; for
    /// the last of the day, the first of the next.
    until: Option<String>,
    seeds: Vec<SeedView>,
}

#[derive(serde::Serialize)]
struct PageView {
    name: Option<String>,
    source: String,
    hub_version: Option<i64>,
    fingerprint: String,
    slots: Vec<SlotView>,
}

/// The slots as the pickers offer them, from what was just saved. Set in the
/// same lock as the hold, so a slot renewed by new seeds `[SPEC-PGM-125]` can
/// be held at once: `POST /programme/slot/:id` checks this list, and the
/// engine rebuilds it from its Director only at its next refill -- a window in
/// which the new id would have been refused, and the picker, which reads no
/// answer, would have snapped back without a word.
fn picker_slots(v: &PageView) -> Vec<(i64, String, String)> {
    v.slots.iter().filter_map(|s| Some((s.id?, s.name.clone(), s.start.clone()?))).collect()
}

fn page_view(store: &crate::db::PlayerStore, v: ProgrammeView) -> PageView {
    let starts: Vec<&str> = v.slots.iter().filter_map(|s| s.start.as_deref()).collect();
    let until = |start: Option<&str>| -> Option<String> {
        let s = start?;
        let i = starts.iter().position(|t| *t == s)?;
        (starts.len() > 1).then(|| starts[(i + 1) % starts.len()].to_string())
    };
    let slots = v
        .slots
        .iter()
        .map(|s| SlotView {
            id: s.id,
            name: s.name.clone(),
            start: s.start.clone(),
            until: until(s.start.as_deref()),
            seeds: s
                .seeds
                .iter()
                .map(|m| {
                    // An unknown recording still shows, by its id: a seed the
                    // library does not hold yet is not an error.
                    let n = store.subject_naming("recording", m).unwrap_or_default();
                    SeedView {
                        mbid: m.clone(),
                        title: n.title,
                        artist: n.artist,
                        album: n.album,
                        has_flavor: store.has_flavor(m),
                    }
                })
                .collect(),
        })
        .collect();
    PageView { name: v.name, source: v.source, hub_version: v.hub_version, fingerprint: v.fingerprint, slots }
}

pub(super) async fn get_programme(State(ui): State<Ui>) -> axum::response::Response {
    let (db, library) = (ui.db.clone(), ui.library.clone());
    let got = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&db, &library).map_err(|e| e.message().to_string())?;
        let v = store.programme().map_err(|e| e.message().to_string())?;
        Ok::<_, String>(page_view(&store, v))
    })
    .await;
    match got {
        Ok(Ok(v)) => axum::Json(v).into_response(),
        Ok(Err(e)) => (StatusCode::INTERNAL_SERVER_ERROR, e).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
    }
}

/// Recordings that could become a seed, for the page's "Add a seed…" search
/// `[SPEC-PGM-420]`: by title or artist, at most 40, named as Browse names
/// them.
pub(super) async fn search_seeds(
    State(ui): State<Ui>,
    axum::extract::Query(q): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> axum::response::Response {
    let query = q.get("q").cloned().unwrap_or_default();
    let (db, library) = (ui.db.clone(), ui.library.clone());
    let found = tokio::task::spawn_blocking(move || {
        let lib = crate::db::Library::open_split(&db, &library).map_err(|e| e.message().to_string())?;
        lib.seed_candidates(&query, 40).map_err(|e| e.message().to_string())
    })
    .await;
    match found {
        Ok(Ok(rows)) => axum::Json(rows).into_response(),
        Ok(Err(e)) => (StatusCode::INTERNAL_SERVER_ERROR, e).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
    }
}

/// One seed in or out of one time slot `[SPEC-PGM-420]`:
/// `?slot=<id>&mbid=<recording>&on=1|0`. What the preference panel and
/// Browse send, so that ticking one box does not send the programme back.
pub(super) async fn set_seed(
    State(ui): State<Ui>,
    axum::extract::Query(q): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> axum::response::Response {
    let Some(slot) = q.get("slot").and_then(|s| s.parse::<i64>().ok()) else {
        return (StatusCode::BAD_REQUEST, "Which time slot? slot=<id> is needed.").into_response();
    };
    let on = match q.get("on").map(String::as_str) {
        Some("1") => true,
        Some("0") => false,
        _ => return (StatusCode::BAD_REQUEST, "on=1 adds the seed, on=0 removes it.").into_response(),
    };
    let mbid = q.get("mbid").cloned().unwrap_or_default();
    let (db, library) = (ui.db.clone(), ui.library.clone());
    let saved = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&db, &library).map_err(|e| (true, e.message().to_string()))?;
        let v = store.set_seed(slot, &mbid, on).map_err(|e| (false, e.message().to_string()))?;
        // A changed slot takes a new id, and a hold on it moves with it
        // `[SPEC-PGM-125]`: what the store now holds is the hold.
        let held = store.load_manual_slot();
        Ok::<_, (bool, String)>((page_view(&store, v), held))
    })
    .await;
    match saved {
        Ok(Ok((v, held))) => {
            if let Ok(mut c) = ui.controls.lock() {
                c.programme_changed = true;
                c.manual_slot = held;
                c.slots = picker_slots(&v);
            }
            axum::Json(v).into_response()
        }
        Ok(Err((false, why))) => (StatusCode::BAD_REQUEST, why).into_response(),
        Ok(Err((true, why))) => (StatusCode::INTERNAL_SERVER_ERROR, why).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
    }
}

/// What the page sends: the whole programme as edited. A new name is how a
/// person keeps the programme they started from unchanged and saves this as
/// another `[SPEC-PGM-405]`; on the node the two are the same act, since a
/// node runs exactly one.
#[derive(serde::Deserialize)]
pub(super) struct Edit {
    name: Option<String>,
    slots: Vec<Slot>,
}

pub(super) async fn save_programme(
    State(ui): State<Ui>,
    axum::Json(edit): axum::Json<Edit>,
) -> axum::response::Response {
    let (db, library) = (ui.db.clone(), ui.library.clone());
    let saved = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&db, &library).map_err(|e| (true, e.message().to_string()))?;
        let v = store
            .save_programme(edit.name.as_deref(), &edit.slots)
            .map_err(|e| (false, e.message().to_string()))?;
        // The save moves a hold to a slot renewed by new seeds, and drops one
        // whose slot it removed `[SPEC-PGM-125]`: what the store now holds is
        // the hold, here and in what a restart reads.
        let held = store.load_manual_slot();
        Ok::<_, (bool, String)>((page_view(&store, v), held))
    })
    .await;
    match saved {
        Ok(Ok((v, held))) => {
            if let Ok(mut c) = ui.controls.lock() {
                c.programme_changed = true;
                c.manual_slot = held;
                c.slots = picker_slots(&v);
            }
            tracing::info!(
                "programme: saved {} ({} time slot(s)) from the page",
                v.name.as_deref().unwrap_or("unnamed"),
                v.slots.len()
            );
            axum::Json(v).into_response()
        }
        // A refusal is a sentence for the page; a store that would not open
        // is the player's fault, not the person's.
        Ok(Err((false, why))) => (StatusCode::BAD_REQUEST, why).into_response(),
        Ok(Err((true, why))) => (StatusCode::INTERNAL_SERVER_ERROR, why).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
    }
}

/// A programme the hub sent `[SPEC-PGM-300]`, handed in by whatever reached
/// the members' channel -- the phone's app. Held, never applied: the answer
/// is a person's `[SPEC-PGM-050]`.
///
/// Unsigned, so only on a loopback-only host, where the caller is the app on
/// the same device. A node on the network takes the hub's programme signed,
/// at `/mesh/programme` (`crate::programme_node`); served here too, any host
/// on the LAN could put a question on its screen.
pub(super) async fn arrived(
    State(ui): State<Ui>,
    axum::Json(a): axum::Json<crate::db::Arrival>,
) -> axum::response::Response {
    if !ui.web_loopback_only {
        return (StatusCode::NOT_FOUND, "a node on the network takes the hub's programme signed, at /mesh/programme")
            .into_response();
    }
    let (db, library) = (ui.db.clone(), ui.library.clone());
    let (name, version) = (a.name.clone(), a.version);
    let got = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&db, &library).map_err(|e| (true, e.message().to_string()))?;
        store.programme_arrived(&a).map_err(|e| (false, e.message().to_string()))
    })
    .await;
    match got {
        Ok(Ok(outcome)) => {
            tracing::info!("programme: {name} version {version} arrived from the hub: {outcome:?}");
            axum::Json(serde_json::json!({ "outcome": outcome })).into_response()
        }
        Ok(Err((false, why))) => (StatusCode::BAD_REQUEST, why).into_response(),
        Ok(Err((true, why))) => (StatusCode::INTERNAL_SERVER_ERROR, why).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
    }
}

/// What waits for an answer, with the programme it would replace, for the
/// question every skin asks and the page's slot-by-slot difference
/// `[SPEC-PGM-410]`. `{"pending": null}` when nothing has arrived.
pub(super) async fn pending(State(ui): State<Ui>) -> axum::response::Response {
    let (db, library) = (ui.db.clone(), ui.library.clone());
    let got = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&db, &library).map_err(|e| e.message().to_string())?;
        let p = store.programme_pending().map_err(|e| e.message().to_string())?;
        let current = store.programme().map_err(|e| e.message().to_string())?;
        Ok::<_, String>(serde_json::json!({
            "pending": p,
            "current": { "name": current.name, "source": current.source,
                         "hub_version": current.hub_version, "fingerprint": current.fingerprint,
                         "slots": current.slots },
        }))
    })
    .await;
    match got {
        Ok(Ok(v)) => axum::Json(v).into_response(),
        Ok(Err(e)) => (StatusCode::INTERNAL_SERVER_ERROR, e).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
    }
}

#[derive(serde::Deserialize)]
pub(super) struct Answer {
    fingerprint: String,
    replace: bool,
}

/// **Replace** or **Keep this node's** `[SPEC-PGM-050]`. A replacement reaches
/// the running Director as a save from the page does, and a slot held by hand
/// that the arrival does not have is let go.
pub(super) async fn answer(
    State(ui): State<Ui>,
    axum::Json(a): axum::Json<Answer>,
) -> axum::response::Response {
    let (db, library) = (ui.db.clone(), ui.library.clone());
    let replace = a.replace;
    let got = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&db, &library).map_err(|e| (true, e.message().to_string()))?;
        let v = store.answer_programme(&a.fingerprint, a.replace).map_err(|e| (false, e.message().to_string()))?;
        // As a save from the page: the hold follows a renewed slot and goes
        // with a removed one `[SPEC-PGM-125]`.
        let held = store.load_manual_slot();
        Ok::<_, (bool, String)>((page_view(&store, v), held))
    })
    .await;
    match got {
        Ok(Ok((v, held))) => {
            if replace {
                if let Ok(mut c) = ui.controls.lock() {
                    c.programme_changed = true;
                    c.manual_slot = held;
                    c.slots = picker_slots(&v);
                }
            }
            tracing::info!(
                "programme: {} {} (version {:?})",
                if replace { "replaced with" } else { "kept over" },
                v.name.as_deref().unwrap_or("unnamed"),
                v.hub_version
            );
            axum::Json(v).into_response()
        }
        Ok(Err((false, why))) => (StatusCode::CONFLICT, why).into_response(),
        Ok(Err((true, why))) => (StatusCode::INTERNAL_SERVER_ERROR, why).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
    }
}
