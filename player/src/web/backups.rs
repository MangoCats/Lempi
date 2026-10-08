//! The Backups row of the Lempi skin's Settings `[REQ-LIB-160]`: the
//! snapshots and what each holds, what putting one back would do, and a
//! restore staged for the player's next start -- never one beneath the
//! running player, which writes the same file and keeps its own state in
//! memory. `backup::apply_staged` does the restore, as the player starts.
//!
//! Behind the same cross-site guard as every route `[SecurityReview C2]`. A
//! snapshot is named as `backup::held` names it, and only such a name finds
//! one, so nothing here reaches a path the page chose.

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde_json::json;

use super::Ui;
use crate::backup;

fn report(r: &backup::Report) -> serde_json::Value {
    json!({ "tables": r.tables, "plays": r.plays, "remapped": r.remapped, "orphaned": r.orphaned })
}

fn unknown(name: &str) -> Response {
    (StatusCode::NOT_FOUND, format!("no snapshot called {name}")).into_response()
}

/// What runs here is SQLite and the file system, off the async threads.
async fn blocking(work: impl FnOnce() -> Response + Send + 'static) -> Response {
    tokio::task::spawn_blocking(work)
        .await
        .unwrap_or_else(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response())
}

/// `GET /backups`: every snapshot, newest first, with what it holds; the
/// restore staged, if any; and what the last staged restore did.
pub(super) async fn backups(State(ui): State<Ui>) -> Response {
    blocking(move || {
        let snapshots: Vec<serde_json::Value> = backup::held(&ui.db)
            .into_iter()
            .map(|h| {
                let mut row = json!({
                    "name": h.name, "taken_at": h.taken_at, "before_restore": h.before_restore,
                });
                // One unreadable snapshot is said on its own row, never a
                // failed page: the others are still worth choosing between.
                let more = match backup::inspect(&h.path) {
                    Ok(s) => json!({
                        "plays": s.plays, "first_play": s.first_play, "last_play": s.last_play,
                        "preferences": s.preferences, "likes": s.likes, "programs": s.programs,
                    }),
                    Err(e) => json!({ "error": e.to_string() }),
                };
                if let (Some(row), Some(more)) = (row.as_object_mut(), more.as_object()) {
                    row.extend(more.clone());
                }
                row
            })
            .collect();
        let last = backup::last_outcome(&ui.db)
            .map(|o| json!({ "at": o.at, "restored": o.restored, "said": o.said }));
        axum::Json(json!({
            "snapshots": snapshots,
            "staged": backup::staged(&ui.db),
            "last": last,
        }))
        .into_response()
    })
    .await
}

/// `POST /backups/:name/rehearse`: what putting it back would do. Writes
/// nothing, so it is safe while the player plays.
pub(super) async fn rehearse(State(ui): State<Ui>, Path(name): Path<String>) -> Response {
    blocking(move || {
        let Some(path) = backup::find(&ui.db, &name) else { return unknown(&name) };
        match backup::restore(&path, &ui.db, &ui.library, false) {
            Ok(r) => axum::Json(report(&r)).into_response(),
            Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
        }
    })
    .await
}

/// `POST /backups/:name/stage`: put it back at the next start. Rehearsed
/// first, so a snapshot that cannot be restored is refused here.
pub(super) async fn stage(State(ui): State<Ui>, Path(name): Path<String>) -> Response {
    blocking(move || {
        if backup::find(&ui.db, &name).is_none() {
            return unknown(&name);
        }
        match backup::stage(&ui.db, &ui.library, &name) {
            Ok(r) => axum::Json(json!({ "staged": name, "report": report(&r) })).into_response(),
            Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
        }
    })
    .await
}

/// `DELETE /backups/stage`: change of mind before the restart.
pub(super) async fn unstage(State(ui): State<Ui>) -> Response {
    blocking(move || match backup::unstage(&ui.db) {
        Ok(was) => axum::Json(json!({ "unstaged": was })).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
    })
    .await
}

#[cfg(test)]
mod tests {
    use super::super::{access, router};
    use super::*;
    use std::sync::Arc;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    /// A split pair with two snapshots of it, served through the real router
    /// behind the cross-site guard.
    async fn served(tag: &str) -> (std::path::PathBuf, std::net::SocketAddr, Vec<String>) {
        let dir = std::env::temp_dir().join(format!("lempi-bk-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let (listener, library) = (dir.join("listener.db"), dir.join("library.db"));
        rusqlite::Connection::open(&listener).unwrap().execute_batch(
            "CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY,
                 played_at INTEGER, passage_id INTEGER, mbid TEXT);
             INSERT INTO listener_play_history VALUES (1, 100, 10, 'rec-a');",
        ).unwrap();
        rusqlite::Connection::open(&library).unwrap().execute_batch(
            "CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL);
             INSERT INTO passage_recordings VALUES (10, 'rec-a', 1.0);",
        ).unwrap();
        let snaps = backup::dir_for(&listener);
        std::fs::create_dir_all(&snaps).unwrap();
        let first = backup::snapshot(&listener).unwrap();
        let older = snaps.join("listener-1000.db");
        std::fs::rename(&first, &older).unwrap();
        let newer = backup::snapshot(&listener).unwrap();
        let names = [newer, older]
            .iter()
            .map(|p| p.file_name().unwrap().to_string_lossy().into_owned())
            .collect();

        let (_e, h) = crate::engine::Engine::new(crate::path::PathHandle::silent(), 1);
        let ui = Ui {
            handle: Arc::new(h),
            db: listener,
            library,
            why: Default::default(),
            controls: Default::default(),
            capabilities: Default::default(),
            web_loopback_only: false,
        };
        let app = access::origin_guard(router(ui));
        let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = l.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(l, app).await });
        (dir, addr, names)
    }

    /// The status line and the body.
    async fn ask(addr: std::net::SocketAddr, method: &str, path: &str, origin: Option<&str>)
        -> (String, String) {
        let mut s = tokio::net::TcpStream::connect(addr).await.unwrap();
        let o = origin.map(|o| format!("Origin: {o}\r\n")).unwrap_or_default();
        let req = format!(
            "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n{o}Content-Length: 0\r\nConnection: close\r\n\r\n");
        s.write_all(req.as_bytes()).await.unwrap();
        let mut buf = Vec::new();
        s.read_to_end(&mut buf).await.unwrap();
        let text = String::from_utf8_lossy(&buf).into_owned();
        let status = text.lines().next().unwrap_or("").to_string();
        let body = text.split_once("\r\n\r\n").map(|(_, b)| b.to_string()).unwrap_or_default();
        (status, body)
    }

    #[tokio::test]
    async fn the_page_lists_rehearses_stages_and_unstages() {
        let (dir, addr, names) = served("flow").await;
        let (status, body) = ask(addr, "GET", "/backups", None).await;
        assert!(status.contains(" 200 "), "{status}");
        let v: serde_json::Value = serde_json::from_str(&body).unwrap();
        let listed: Vec<&str> =
            v["snapshots"].as_array().unwrap().iter().map(|s| s["name"].as_str().unwrap()).collect();
        assert_eq!(listed, names.iter().map(String::as_str).collect::<Vec<_>>(), "newest first");
        assert_eq!(v["snapshots"][0]["plays"], 1);
        assert!(v["staged"].is_null() && v["last"].is_null());

        let older = &names[1];
        let (status, body) = ask(addr, "POST", &format!("/backups/{older}/rehearse"), None).await;
        assert!(status.contains(" 200 "), "{status} {body}");
        let r: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!((r["plays"].as_i64(), r["orphaned"].as_i64()), (Some(1), Some(0)));

        let (status, _) = ask(addr, "POST", &format!("/backups/{older}/stage"), None).await;
        assert!(status.contains(" 200 "), "{status}");
        let (_, body) = ask(addr, "GET", "/backups", None).await;
        let v: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(v["staged"].as_str(), Some(older.as_str()));

        let (status, body) = ask(addr, "DELETE", "/backups/stage", None).await;
        assert!(status.contains(" 200 ") && body.contains("true"), "{status} {body}");
        let (_, body) = ask(addr, "GET", "/backups", None).await;
        let v: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert!(v["staged"].is_null(), "unstaged");
        std::fs::remove_dir_all(&dir).ok();
    }

    /// Only a name the directory lists finds a snapshot, and a page on
    /// another site cannot stage one.
    #[tokio::test]
    async fn unknown_names_and_other_sites_are_refused() {
        let (dir, addr, names) = served("refuse").await;
        for path in [
            "/backups/listener-9.db/rehearse",
            "/backups/listener-9.db/stage",
            "/backups/..%2Flistener.db/stage",
            "/backups/restore-staged/stage",
        ] {
            let (status, _) = ask(addr, "POST", path, None).await;
            assert!(status.contains(" 404 "), "POST {path} answered {status}");
        }
        let path = format!("/backups/{}/stage", names[0]);
        let (status, _) = ask(addr, "POST", &path, Some("http://evil.example")).await;
        assert!(status.contains(" 403 "), "a foreign Origin staged a restore: {status}");
        let (_, body) = ask(addr, "GET", "/backups", None).await;
        assert!(body.contains("\"staged\":null"), "nothing was staged: {body}");
        std::fs::remove_dir_all(&dir).ok();
    }
}
