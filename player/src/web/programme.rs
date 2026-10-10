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
                    SeedView { mbid: m.clone(), title: n.title, artist: n.artist, album: n.album }
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
    let manual = ui.controls.lock().ok().and_then(|c| c.manual_program);
    let saved = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&db, &library).map_err(|e| (true, e.message().to_string()))?;
        let v = store
            .save_programme(edit.name.as_deref(), &edit.slots)
            .map_err(|e| (false, e.message().to_string()))?;
        // A slot held by hand that the edit removed: the hold goes, here and
        // in what a restart reads, and time of day decides again.
        let held_gone = manual.is_some_and(|id| !v.slots.iter().any(|s| s.id == Some(id)));
        if held_gone {
            let _ = store.save_manual_program(None);
        }
        Ok::<_, (bool, String)>((page_view(&store, v), held_gone))
    })
    .await;
    match saved {
        Ok(Ok((v, held_gone))) => {
            if let Ok(mut c) = ui.controls.lock() {
                c.programme_changed = true;
                if held_gone {
                    c.manual_program = None;
                }
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
