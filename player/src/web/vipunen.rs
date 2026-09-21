//! Launching Vipunen's own console from Lempi's browse page `[REQ-VIS-320]` --
//! the mirror image of `tools/lempi_control.py`'s `ensure_lempi()`: that
//! module finds and starts a co-resident Lempi on demand, from Vipunen's own
//! browser; this finds and starts a co-resident Vipunen the same way, from
//! Lempi's own browse page, for the one link a desktop listener curating
//! their own library might actually want to follow without opening a
//! terminal.
//!
//! Gated behind `vipunen-support` exactly like `/review` and `/edit`
//! (`mod.rs`) -- Vipunen is x86-desktop-only (`README.md`), and a build that
//! never runs Vipunen has nothing to offer here. Not even a route exists on
//! a build without the feature, the same posture the other two take.

use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use axum::extract::State;
use axum::response::{IntoResponse, Response};

use super::Ui;

/// `tools/console.py`'s own default, mirrored rather than re-derived --
/// the two processes must agree on where to look for each other without
/// either configuring the other.
const VIPUNEN_PORT: u16 = 5730;

/// Whether a Vipunen is *serving*, which is not the same question as whether
/// something holds the port.
///
/// This was a `TcpStream::connect_timeout` and that is precisely what made
/// the failure it now prevents invisible. A socket check cannot tell a
/// working console from a wedged one, so a dead-but-bound Vipunen read as
/// *absent*, `vipunen_ensure` started another beside it, and the next click
/// started a third -- each one binding the same address because
/// `console.py` asked for `allow_reuse_address`, which on Windows permits
/// exactly that. Measured on 2026-09-11: three consoles `LISTENING` on
/// `127.0.0.1:5730`, every connection actively refused, and this endpoint
/// answering `did not answer within 20s` forever after.
///
/// `/console.css` is a static asset served by `console.py`'s `do_GET`, so
/// this reads no database -- a liveness question, the same shape and the
/// same reasoning as `lempi_control.py::_lempi_has_vipunen_support`'s
/// `/review.js` probe in reverse.
async fn vipunen_reachable() -> bool {
    let Ok(client) = reqwest::Client::builder()
        .timeout(Duration::from_millis(1500))
        .build()
    else {
        return false;
    };
    client
        .get(format!("http://127.0.0.1:{VIPUNEN_PORT}/console.css"))
        .send()
        .await
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

/// Where `tools/console.py` is, if it can be found at all `[REQ-VIS-320]`.
///
/// Checked against this repository's own build layout, relative to
/// *Lempi's own running binary* -- `player/target/release/lempi[.exe]`,
/// three directories below the repository root -- the same "developed
/// side by side" case `lempi_control.py::_lempi_binary` already checks in
/// reverse. Never guessed beyond that, and never a `PATH` search: unlike a
/// `lempi` binary, a Python script has no reason to be installed anywhere
/// generic, and a wrong script started against the wrong database is
/// worse than admitting there is none.
fn console_script() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let candidate = exe.parent()?.parent()?.parent()?.parent()?.join("tools").join("console.py");
    candidate.is_file().then_some(candidate)
}

/// Whichever Python actually runs on this machine, checked rather than
/// assumed -- `python3` first (the name every non-Windows install uses),
/// falling back to plain `python` (Windows' own convention).
fn python() -> Option<&'static str> {
    ["python3", "python"].into_iter().find(|&candidate| {
        Command::new(candidate)
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    })
}

/// Whether the browse page's Vipunen link is worth showing at all
/// `[REQ-VIS-320]` -- probed once when the page loads, the same shape
/// `lempi_control.py::_lempi_has_vipunen_support` checks in reverse.
/// Already running counts, so a listener who started Vipunen by hand still
/// sees the link; otherwise it takes both a locatable console and a
/// Python to run it with, since offering a link neither `vipunen_ensure`
/// verb could ever satisfy would be worse than no link at all.
pub(super) async fn vipunen_available() -> Response {
    let launchable = tokio::task::spawn_blocking(|| console_script().is_some() && python().is_some())
        .await
        .unwrap_or(false);
    let available = launchable || vipunen_reachable().await;
    axum::Json(serde_json::json!({ "available": available })).into_response()
}

/// Start the co-resident Vipunen console if one is not already there
/// `[REQ-VIS-320]`, on *this* Lempi's own catalogue path -- the same reason
/// `ensure_lempi` starts a fresh Lempi on Vipunen's own database path in
/// reverse: the link should open the same library this browse page is
/// already showing, not a different one. The catalogue half specifically,
/// which is what Vipunen is a browser for; see the call site for what handing
/// it the listener half cost. Launched without `--root`
/// (Lempi has no single "music folder" to offer -- only individual
/// passage paths); Vipunen's own console already treats that as a normal,
/// supported case, with only its Folder view left empty (`HOWTO.md §5`).
pub(super) async fn vipunen_ensure(State(ui): State<Ui>) -> Response {
    if vipunen_reachable().await {
        return axum::Json(serde_json::json!({ "ok": true, "port": VIPUNEN_PORT })).into_response();
    }
    // `ui.library`, not `ui.db` `[PI-OWE-010]`. This passed the path *this*
    // player was started with, which on a split installation is the listener
    // half -- the same defect `lempi.rs`'s folder generators had, in the same
    // shape, found the same way. Vipunen survives being handed it (`lempi_db`
    // finds the catalogue as a sibling) so nothing crashed; what changed was
    // everything Vipunen names after its database. Its job sidecar became
    // `listener.console.db` instead of `library.console.db`, so a console
    // launched from this page came up with no job history and, worse, an
    // empty `remote_config` -- the configured peer simply absent, with no
    // error to say so. Equal to `ui.db` on every unsplit installation, so
    // this changes nothing there.
    let db = ui.library.clone();
    let started = tokio::task::spawn_blocking(move || -> Result<(), String> {
        let script = console_script()
            .ok_or_else(|| "no local Vipunen console found (tools/console.py)".to_string())?;
        let py = python().ok_or_else(|| "no python3/python found on PATH".to_string())?;
        // Vipunen's own output, kept rather than discarded. Both streams went
        // to `Stdio::null()`, which is why a console that refused to start
        // said nothing at all and this endpoint's 20s timeout was the only
        // evidence a person ever got. Beside the library, named like the
        // other sidecars, truncated per launch -- the interesting run is
        // always the one that just failed. `vipunen-support` is desktop-only,
        // so the directory is writable; if it somehow is not, launching
        // still matters more than logging and this falls back to discarding.
        // One handle, cloned -- two `File::create`s would each truncate and
        // then write at their own offset, so the two streams would overwrite
        // each other instead of interleaving.
        let log = db
            .parent()
            .map(|d| d.join("vipunen-launch.log"))
            .and_then(|p| std::fs::File::create(p).ok())
            .and_then(|f| f.try_clone().ok().map(|g| (f, g)));
        let (out, err) = match log {
            Some((f, g)) => (Stdio::from(f), Stdio::from(g)),
            None => (Stdio::null(), Stdio::null()),
        };
        Command::new(py)
            // Unbuffered, or the log above is empty exactly when it matters.
            // Python block-buffers stdout when it is a file rather than a
            // terminal, so a console that starts and then *wedges* flushes
            // nothing -- measured 2026-09-11, a 0-byte `vipunen-launch.log`
            // beside a Vipunen that had been hung for twenty minutes. A log
            // that only survives a clean exit is not a log.
            .arg("-u")
            .arg(&script)
            .arg(&db)
            .arg("--port")
            .arg(VIPUNEN_PORT.to_string())
            .stdout(out)
            .stderr(err)
            .spawn()
            .map(|_| ())
            .map_err(|e| format!("could not start vipunen: {e}"))
    })
    .await;
    if let Err(why) = started.unwrap_or_else(|_| Err("could not start vipunen".into())) {
        return axum::Json(serde_json::json!({ "ok": false, "error": why })).into_response();
    }
    // Polled, not a fixed wait -- the same reasoning `ensure_lempi` gives
    // for not guessing a sleep duration against a console whose own
    // startup time scales with library size.
    let deadline = Instant::now() + Duration::from_secs(20);
    while Instant::now() < deadline {
        if vipunen_reachable().await {
            return axum::Json(serde_json::json!({ "ok": true, "port": VIPUNEN_PORT })).into_response();
        }
        tokio::time::sleep(Duration::from_millis(250)).await;
    }
    // Say where the reason is. The console now writes down why it refused to
    // start (an occupied port, most often), and a message that does not point
    // at that log sends a person back to guessing.
    axum::Json(serde_json::json!({
        "ok": false,
        "error": "vipunen did not answer within 20s of starting -- \
                  see vipunen-launch.log beside the library for what it said"
    }))
    .into_response()
}
