//! The player as a library: start it, command it, read it, stop it
//! `[GDE-HST-040]`.
//!
//! **Until 2026-09-25 this was `lempi`'s `main`** -- about three hundred lines of
//! wiring in a binary, which no test could reach and no other host could call.
//! The MPD naming fault fixed in `e899a67` lived in exactly that code, and
//! nothing tested it, because it sat in the binary's wiring rather than here.
//! Now `lempi` resolves its command line into a [`Config`] and calls
//! [`Player::start`]; a phone's JNI layer would do the same with a settings
//! screen in place of a command line `[GDE-HST-320]`.
//!
//! Decisions it carries, from GUIDE033 §6:
//!
//! * **The library owns its async runtime** `[GDE-HST-300]`, built inside
//!   `start`. A JNI caller has no runtime to lend, and a binary that kept
//!   `#[tokio::main]` while this built another would nest them, which tokio
//!   refuses -- so `lempi` is a plain `fn main` now.
//! * **The web server starts only when configured** `[GDE-HST-310]`: `lempi`
//!   passes a port, a native-UI phone would pass none, a WebView phone
//!   localhost -- so the WebView question `[GDE-AND-060]` does not block this.
//! * **Configuration arrives resolved** `[GDE-HST-320]`. The four option layers
//!   stay in `cli`, with the binaries.
//!
//! **Threads.** The engine is built and ticked on one thread it never leaves,
//! because `cpal`'s stream is not `Send` on every backend; the web server and
//! the echo client run on the runtime and reach playback only through
//! [`EngineHandle`] `[REQ-VIS-140]`. The backup and tag-scan threads are
//! optional, and the backup one ends when the player is shut down rather than
//! sleeping out its hour.
//!
//! **`station` does not use this, deliberately.** It is a finite harness -- a
//! fixed set, a queue listing, a stop when idle, an underrun count -- and a
//! never-idle radio host would change what it tests. It already shares what is
//! shared, through [`Session`], `path` and [`Engine`].
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{channel, sync_channel, Receiver, RecvTimeoutError, Sender, SyncSender};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::engine::{Command, Engine, EngineHandle, PlayerState};
use crate::session::{Controls, Explanations, Session, SharedControls};
use crate::BUFFER_FRAMES;

/// How far ahead of the outgoing side the incoming one is told to start
/// `[SPEC-BK-065]`.
///
/// It begins in a moment rather than now, so handing it the position as of
/// *now* would replay that moment. MPD was measured at 14-27 ms from the
/// command to its first frame `[SPEC-BK-055]`; the rest of this is the fade
/// the outgoing side is still running through. Small enough that being wrong
/// by all of it is a fraction of a second of music, in one direction or the
/// other, once.
const HANDOFF_LEAD_MS: u64 = 250;

/// Everything a player needs, already resolved `[GDE-HST-320]` -- the one
/// list of what a player is started with.
#[derive(Debug, Clone)]
pub struct Config {
    /// The listener-side database: plays, preferences, programmes.
    pub listener: PathBuf,
    /// The catalogue-side database. Equal to `listener` on every installation
    /// that has not split `[IMPL-DBSPLIT-025]`.
    pub library: PathBuf,
    /// Passages kept queued ahead.
    pub depth: usize,
    /// A case-insensitive substring of the output device's name; `None` for
    /// the default device.
    pub device: Option<String>,
    /// This node's calibrated presentation offset, in frames; 0 when unknown.
    pub echo_offset_frames: u64,
    /// The smallest presentation offset in the fleet, in frames; 0 when unknown.
    pub echo_fleet_min_frames: u64,
    /// The device's nominal rate, for the echo client's frame arithmetic.
    pub echo_rate: u32,
    /// The node to follow, seeded into the stored setting `[SPEC-ECHO-010]`.
    /// Ignored in a build without `echo-client`; the caller says so.
    pub follow: Option<String>,
    /// An MPD instance to attach as a guest backend `[SPEC-BK-020]`. Honoured
    /// only in a build with `mpd`.
    pub mpd_addr: Option<String>,
    /// That MPD's `music_directory`.
    pub mpd_root: Option<String>,
    /// The web UI's port; `None` for no server at all `[GDE-HST-310]`.
    pub web_port: Option<u16>,
    /// Also serve on :80, best-effort, where the process may bind it `[SPEC034]`.
    pub also_port_80: bool,
    /// Snapshot the listener database now and hourly `[REQ-LIB-160]`.
    pub backup: bool,
    /// Read the files' own tags in the background, for browsing by album.
    pub tag_scan: bool,
}

/// What stopped a running player.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Ended {
    /// The engine's tick loop ended.
    Engine,
    /// The web server stopped serving.
    Web,
}

/// Why a player could not be started.
#[derive(Debug)]
pub enum StartError {
    /// The async runtime, or the engine's thread, could not be created.
    Spawn(std::io::Error),
    /// The session could not be opened -- the library or the store.
    Engine(String),
    /// The engine thread went away before reporting either way.
    EngineGone,
    /// The web UI's port could not be bound.
    Listen { addr: SocketAddr, error: std::io::Error },
}

impl std::fmt::Display for StartError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StartError::Spawn(e) => write!(f, "cannot start the player's threads: {e}"),
            StartError::Engine(e) => write!(f, "{e}"),
            StartError::EngineGone => write!(f, "engine failed to start"),
            StartError::Listen { addr, error } => write!(f, "cannot listen on {addr}: {error}"),
        }
    }
}

impl std::error::Error for StartError {}

/// A running player.
pub struct Player {
    handle: Arc<EngineHandle>,
    runtime: Option<tokio::runtime::Runtime>,
    ended: Receiver<Ended>,
    /// Dropped to end the backup thread's hourly wait.
    _stop_background: Sender<()>,
}

/// Sends `Ended` when dropped -- at the end of a thread's work, and during an
/// unwind if it panics, so a waiter always learns the thread is gone.
struct Signal(Sender<Ended>, Ended);

impl Drop for Signal {
    fn drop(&mut self) {
        let _ = self.0.send(self.1);
    }
}

type Started = Result<(EngineHandle, Explanations, SharedControls), String>;

impl Player {
    /// Start a player: its background threads, its engine, and -- if asked --
    /// its web UI. Returns once the engine is primed and the port is bound.
    pub fn start(cfg: Config) -> Result<Player, StartError> {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .map_err(StartError::Spawn)?;

        let (stop_background, stopped) = channel::<()>();
        if cfg.backup {
            spawn_backup(cfg.listener.clone(), stopped);
        }
        if cfg.tag_scan {
            spawn_tag_scan(cfg.library.clone());
        }

        let (ended_tx, ended) = channel::<Ended>();
        let (tx, rx) = sync_channel::<Started>(1);
        let setup = EngineSetup {
            db: cfg.listener.clone(),
            library: cfg.library.clone(),
            depth: cfg.depth,
            device: cfg.device.clone(),
            mpd_addr: cfg.mpd_addr.clone(),
            mpd_root: cfg.mpd_root.clone(),
        };
        let signal = Signal(ended_tx.clone(), Ended::Engine);
        std::thread::Builder::new()
            .name("lempi-engine".into())
            .spawn(move || {
                let _signal = signal;
                engine_thread(setup, tx)
            })
            .map_err(StartError::Spawn)?;

        let (handle, why, controls) = match rx.recv() {
            Ok(Ok((h, why, c))) => (Arc::new(h), why, c),
            Ok(Err(e)) => return Err(StartError::Engine(e)),
            Err(_) => return Err(StartError::EngineGone),
        };

        // Absent is not zero `[GOV-SRC-040]`: with no roster figures this node
        // fills its ring to capacity, which is right for a node playing alone
        // and for whichever node holds the fleet's smallest offset. Only a node
        // told both numbers holds anything back.
        if cfg.echo_offset_frames > 0 && cfg.echo_fleet_min_frames > 0 {
            tracing::info!(
                "echo-depth: offset={} fleet-min={}, holding {} frames of ring back",
                cfg.echo_offset_frames,
                cfg.echo_fleet_min_frames,
                cfg.echo_offset_frames.saturating_sub(cfg.echo_fleet_min_frames)
            );
            handle.send(Command::SetEchoDepth {
                own_offset_frames: cfg.echo_offset_frames,
                fleet_min_offset_frames: cfg.echo_fleet_min_frames,
            });
        }
        // The follow setting is seeded rather than replaced: the control is the
        // settings panel `[SPEC-ECHO-010]`, and a start-up value that silently
        // overrode what a listener chose would be worse than none. The client
        // is always spawned, even with nothing to follow, because which node to
        // follow can change at any moment.
        #[cfg(feature = "echo-client")]
        {
            if let Some(host) = cfg.follow.clone() {
                handle.send(Command::SetEchoFollow(host));
            }
            runtime.spawn(crate::echo_client::run(
                crate::echo_client::Following {
                    timing: crate::echo::NodeTiming {
                        presentation_offset_frames: cfg.echo_offset_frames,
                        rate: cfg.echo_rate,
                    },
                    db: cfg.listener.clone(),
                    library: cfg.library.clone(),
                },
                handle.clone(),
            ));
        }

        if let Some(port) = cfg.web_port {
            let ui = crate::web::Ui {
                handle: handle.clone(),
                why,
                controls,
                db: cfg.listener.clone(),
                library: cfg.library.clone(),
            };
            let app = crate::web::router(ui);
            let addr = SocketAddr::from(([0, 0, 0, 0], port));
            let listener = match runtime.block_on(tokio::net::TcpListener::bind(addr)) {
                Ok(l) => l,
                Err(error) => {
                    // Started, and now refused: stop the engine rather than
                    // leave it playing behind an error its caller will exit on.
                    handle.send(Command::Shutdown);
                    let _ = wait_for_engine(&ended, Duration::from_secs(5));
                    return Err(StartError::Listen { addr, error });
                }
            };
            tracing::info!("web UI on http://localhost:{port}/");

            // Also on :80, best-effort `[SPEC034]` -- reachable as plain
            // `http://lempi/` once `setcap cap_net_bind_service` lets this
            // otherwise-unprivileged process bind it. Expected to fail wherever
            // that has not been done, and that failure is one line, never
            // fatal: the port above is what everything else depends on.
            if cfg.also_port_80 && port != 80 {
                let addr80 = SocketAddr::from(([0, 0, 0, 0], 80));
                match runtime.block_on(tokio::net::TcpListener::bind(addr80)) {
                    Ok(listener80) => {
                        let app80 = app.clone();
                        runtime.spawn(async move {
                            if let Err(e) = axum::serve(listener80, app80).await {
                                tracing::error!("port 80 server: {e}");
                            }
                        });
                        tracing::info!("also on http://localhost/ (port 80)");
                    }
                    Err(e) => {
                        tracing::info!("not also listening on :80 ({e}) -- port {port} still works");
                    }
                }
            }

            // The main server ending ends the player's wait, as it ended
            // `lempi`'s `main` before this was a library: a player still
            // sounding behind a UI that has gone is the fault `[PI3-API-030]`
            // names, and a restart is the remedy systemd already applies.
            let web_ended = Signal(ended_tx.clone(), Ended::Web);
            runtime.spawn(async move {
                let _signal = web_ended;
                if let Err(e) = axum::serve(listener, app).await {
                    tracing::error!("server: {e}");
                }
            });
        }
        drop(ended_tx);

        Ok(Player { handle, runtime: Some(runtime), ended, _stop_background: stop_background })
    }

    /// The engine's control surface, for anything that commands it directly.
    pub fn handle(&self) -> Arc<EngineHandle> {
        self.handle.clone()
    }

    /// Send the engine a command: play, pause, skip, a volume, and the rest.
    pub fn command(&self, c: Command) {
        self.handle.send(c);
    }

    /// What the engine last published.
    pub fn state(&self) -> PlayerState {
        self.handle.snapshot()
    }

    /// Write the resume point and settings now -- what a phone's `onStop` asks.
    pub fn persist(&self) {
        self.command(Command::Persist);
    }

    /// Block until the engine or the web server stops, and say which.
    pub fn wait(&self) -> Ended {
        self.ended.recv().unwrap_or(Ended::Engine)
    }

    /// Stop the engine, then the runtime. Returns whether the engine stopped
    /// within five seconds.
    ///
    /// **Not certain to, and said so.** A shutdown reaches the local engine;
    /// if the player has been switched to an MPD guest, the loop runs while the
    /// guest is live `[SPEC-BK-030]`, and this reports `false` rather than
    /// hanging on it.
    pub fn shutdown(mut self) -> bool {
        self.command(Command::Shutdown);
        let stopped = wait_for_engine(&self.ended, Duration::from_secs(5));
        if let Some(rt) = self.runtime.take() {
            rt.shutdown_timeout(Duration::from_secs(1));
        }
        stopped
    }
}

/// Wait, at most `limit`, for the engine thread to say it has gone.
fn wait_for_engine(ended: &Receiver<Ended>, limit: Duration) -> bool {
    let until = std::time::Instant::now() + limit;
    loop {
        let left = until.saturating_duration_since(std::time::Instant::now());
        match ended.recv_timeout(left) {
            Ok(Ended::Engine) | Err(RecvTimeoutError::Disconnected) => return true,
            Ok(Ended::Web) => continue,
            Err(RecvTimeoutError::Timeout) => return false,
        }
    }
}

/// The listening -- plays, preferences, programmes -- is the one thing in
/// that file nothing can rebuild `[REQ-LIB-160]`. One snapshot now, so a
/// library that has never been backed up stops being so within a second of
/// starting, then hourly for as long as it runs. Ends when the player does.
fn spawn_backup(db: PathBuf, stopped: Receiver<()>) {
    let _ = std::thread::Builder::new().name("lempi-backup".into()).spawn(move || loop {
        match crate::backup::snapshot(&db) {
            Ok(p) => tracing::info!("listener state backed up to {}", p.display()),
            // Never fatal: a player that stops playing because it could not
            // write a backup has turned a precaution into the fault.
            Err(e) => tracing::warn!("listener backup failed ({e}); playback continues"),
        }
        match stopped.recv_timeout(Duration::from_secs(3600)) {
            Err(RecvTimeoutError::Timeout) => continue,
            _ => return,
        }
    });
}

/// Album names and the browse index come from the files' own tags, and
/// reading them takes ~18 s for five thousand files. Done in the background,
/// incrementally -- a no-op on every start after the first -- and off the
/// audio path entirely. `file_tags` is a catalogue table `[PI-DB-010]`, so
/// this reads `library`, not the listener database.
fn spawn_tag_scan(library: PathBuf) {
    let _ = std::thread::Builder::new().name("lempi-tagscan".into()).spawn(move || {
        if let Err(e) = crate::tags::backfill(&library, true) {
            tracing::warn!("tag scan unavailable ({e}); album names will be missing");
        }
    });
}

/// What the engine thread is started with.
struct EngineSetup {
    db: PathBuf,
    library: PathBuf,
    depth: usize,
    device: Option<String>,
    mpd_addr: Option<String>,
    mpd_root: Option<String>,
}

/// The published state, so the loop can read the listener's settings after the
/// engine has become an anonymous backend.
fn state_of(h: &EngineHandle) -> Arc<Mutex<PlayerState>> {
    h.state.clone()
}

fn engine_thread(setup: EngineSetup, tx: SyncSender<Started>) {
    let EngineSetup { db, library, depth, device, mpd_addr, mpd_root } = setup;
    let mut session = match Session::open(&db, &library, depth) {
        Ok(s) => s,
        Err(e) => {
            let _ = tx.send(Err(e.to_string()));
            return;
        }
    };
    tracing::info!("library: {} radio passages", session.lib.count_radio().unwrap_or(0));

    // A missing device must not stop the process: the UI still needs to come
    // up and say so, which is more use than exiting silently.
    // The supervisor opens the device on its own thread and owns it from
    // there `[SPEC-APS-060]`; this reports only what happened.
    let (path, why) = crate::path::start(device, BUFFER_FRAMES * 2);
    tracing::info!("{why}");

    let (mut engine, handle) = Engine::new(path, session.depth());
    // Taken before the handle is sent away: the loop below reads the listener's
    // settings from here once the engine has become an anonymous backend.
    let published = state_of(&handle);
    // `prime` is where the resumed passage is looked up and the queue is first
    // filled, so it is the last thing between a started process and a sounding
    // one `[PI3-FOUND-210]`. Timed separately from `Session::open` because the
    // two have entirely different remedies: one is a database open, the other
    // is selection.
    let primed = std::time::Instant::now();
    session.prime(&mut engine);
    tracing::info!("session prime: {}ms", primed.elapsed().as_millis());
    if tx.send(Ok((handle, session.explanations(), session.controls()))).is_err() {
        return; // nobody left to control it
    }

    // Resume the play STATE, not just the position `[PI5-PWR-030]`.
    //
    // `player_state` has always recorded whether it was playing, and the
    // session read that column and threw it away -- so an appliance that lost
    // power, or was shut down deliberately from the settings page, came back
    // holding its place and silent. Restoring the position but not the
    // intention is half a resume.
    //
    // Safe against a missing speaker without waiting for one. Playing marks
    // the supervisor's interest, it checks for a dummy sink immediately rather
    // than up to WATCH later, and a dummy is treated as a failure -- which
    // makes `path.audible()` false, and the engine advances nothing while
    // nobody can hear it. So this resumes into silence only for as long as it
    // takes to notice, and the position is not spent.
    if session.resume_playing() {
        tracing::info!("resuming playback: it was playing when it last stopped");
        engine.play_on_resume();
    }

    // From here the engine is a **backend** rather than the engine
    // `[SPEC-BK-020]`. Everything above is setup, which is the process's
    // business and not a backend's -- priming, the store, the resume.
    //
    // The suppression windows are read from the published state rather than
    // from the engine, because the engine is about to stop being reachable by
    // name. They are the listener's settings and the same whoever plays.
    // `open_split`, not `open`. `PlayerStore::open(p)` is `open_split(p, p)`,
    // which makes the listener half look like the whole database: the alias
    // is then `main`, `ensure_library_tables_if_owned` decides this
    // connection owns the catalogue, and it creates empty `file_tags` and
    // `cover_art` in the LISTENER half -- shadows that mask the real ones
    // `[IMPL-DBSPLIT-025]`. Reproduced here 2026-09-11: dropped both, started
    // the player, and both came back. This is where lempi02w's own pair came
    // from `[PI-OWE-040]`, on every start, not from some older build.
    let saved_cue = crate::db::PlayerStore::open_split(&db, &library)
        .ok()
        .and_then(|s| s.load_settings())
        .map(|s| s.cue_sheets)
        .unwrap_or(false);
    // Read regardless of the feature so the configuration is the same either
    // way; only the guest that consumes it is gated.
    #[cfg(not(feature = "mpd"))]
    let _ = (&mpd_addr, &mpd_root, &saved_cue);
    let controls_for_switch = session.controls();
    if let Ok(mut c) = controls_for_switch.lock() {
        c.backend = Some("lempi".into());
    }
    let mut backend = crate::switch::Switching::new(Box::new(engine));

    #[cfg(feature = "mpd")]
    if let Some(addr) = mpd_addr {
        let root = mpd_root.unwrap_or_default();
        match crate::mpd_backend::MpdBackend::connect(
            &addr,
            &root,
            session.depth(),
            crate::SAMPLE_INTERVAL_MS,
        ) {
            Ok(mut guest) => {
                // `&library`, not `&db`. Both `nameable_uris` and `cue_uris`
                // read `passages`, `files` and `passage_recordings`, which are
                // library tables; `db` is the *listener* database
                // `[IMPL-DBSPLIT-025]`. On a node that gives no `--library`
                // the two are the same path, so this worked by accident
                // everywhere it had been tried -- and failed on `lempi02w`, the
                // one node that splits them, with `cannot name URIs for MPD (no
                // such table: passages)` on every start. Found 2026-09-22 by
                // turning `--features mpd` on there for the first time; the flag
                // had been passed to a binary that ignored it since the node
                // was built.
                if let Ok(c) = rusqlite::Connection::open(&library) {
                    match crate::mpd_backend::nameable_uris(&c, &root) {
                        Ok(n) => guest.attach_names(n),
                        Err(e) => tracing::error!("cannot name URIs for MPD ({e})"),
                    }
                    // Cue tracks, only when the listener has asked for them
                    // `[REQ-VIS-205]`. Read at startup: sheets written now are
                    // used from the next run, which the settings page says.
                    if saved_cue {
                        match crate::mpd_backend::cue_uris(&c, &root) {
                            Ok(m) => {
                                tracing::info!("{} passage(s) have a cue track to be named by", m.len());
                                guest.attach_cues(m);
                            }
                            Err(e) => tracing::error!("cannot map cue tracks ({e})"),
                        }
                    }
                }
                // `open_split` for the same reason as `saved_cue` above.
                if let Ok(st) = crate::db::PlayerStore::open_split(&db, &library) {
                    guest.attach_store(st);
                }
                backend.attach_guest(Box::new(guest));
                tracing::info!("MPD guest attached at {addr}; Lempi is still the one playing");
                if let Ok(mut c) = controls_for_switch.lock() {
                    c.guest_available = true;
                    c.guest_name = Some(format!("MPD at {addr}"));
                    // Something true before the first switch, so the control is
                    // evidently working rather than evidently blank.
                    c.switch_status = Some("Lempi is playing; the guest is attached and idle".into());
                }
            }
            Err(e) => tracing::warn!("MPD guest unavailable ({e}); continuing on the local engine"),
        }
    }

    // Otherwise paused until told otherwise. The producers fill regardless, so
    // pressing Play in the browser starts on a primed pipeline rather than an
    // underrun [REQ-AUD-142].
    // The tick may never wait on a write `[GDE-FBD-090]`: from here on this
    // thread's lines are queued, not written `[GDE-HST-140]`.
    crate::logging::this_thread_must_not_block();
    while !crate::playback::Playback::is_shutdown(&backend) {
        let submitted = crate::playback::Playback::tick(&mut backend);
        // Continuous radio: the queue never runs dry, so playback never ends.
        // The backend plays; the settings belong to the process `[SPEC-BK-020]`.
        let (suppress, queue_settings) = published
            .lock()
            .map(|s| {
                (
                    (s.skip_suppress_h, s.dequeue_suppress_h),
                    (s.queue_depth, s.sample_interval_ms),
                )
            })
            .unwrap_or((
                (crate::SKIP_SUPPRESS_H, crate::DEQUEUE_SUPPRESS_H),
                (crate::QUEUE_DEPTH, crate::SAMPLE_INTERVAL_MS),
            ));
        // Reaches whichever backend is live, and the one that is not
        // `[SPEC-MPD-105]`: the local engine already sees a change through its
        // own `Command` channel regardless, but a guest has no such channel,
        // so this is the only path that ever reaches it.
        crate::playback::Playback::apply_queue_settings(
            &mut backend,
            queue_settings.0,
            queue_settings.1,
        );
        // The four folder-writing settings, run here rather than in a request
        // handler: each walks the library and writes into a folder Lempi does
        // not own `[REQ-VIS-205]`, which is not work to do while a browser
        // waits.
        //
        // **These four and the `writes_files!` arms in `web.rs` are one list in
        // two places.** A setting with a route and no entry here is a checkbox
        // that persists and does nothing; one with an entry and no route cannot
        // be asked for. Change either and change both.
        run_generation(&library, &controls_for_switch, "cue sheets", "sheets",
            |c| c.cue_requested.take(), |c, s| c.cue_status = Some(s),
            |conn| crate::cue::generate(conn, false).map(|r| (r, "cue sheet")));
        run_generation(&library, &controls_for_switch, "cover art", "covers",
            |c| c.covers_requested.take(), |c, s| c.covers_status = Some(s),
            |conn| crate::covers::generate(conn, false).map(|r| (r, "cover")));
        run_generation(&library, &controls_for_switch, "lyrics sidecar", "files",
            |c| c.sidecar_requested.take(), |c, s| c.sidecar_status = Some(s),
            |conn| crate::lyrics_sidecar::generate(conn, false).map(|r| (r, "file")));
        // The odd one out: it writes into a client's cache rather than the
        // music folder, so it has somewhere to fail to find.
        run_generation(&library, &controls_for_switch, "lyrics cache", "files",
            |c| c.lyrics_requested.take(), |c, s| c.lyrics_status = Some(s),
            |conn| match crate::lyrics_cache::cache_dir() {
                // Not a failure: a machine the client has never run on has
                // nothing useful to write there `[SPEC-LYR-075]`.
                None => Err("no client cache on this machine; nothing written".to_string()),
                Some(dir) => crate::lyrics_cache::generate(conn, &dir, false)
                    .map(|r| (r, "song")),
            });
        // A switch asked for by the browser happens here, where the backends
        // are `[SPEC-BK-030]`. Taken before the refill so the incoming side is
        // topped up rather than the outgoing one.
        let asked = controls_for_switch.lock().ok().and_then(|mut c| c.switch_requested.take());
        if let Some(which) = asked {
            let target = if which == "mpd" {
                crate::switch::Side::Guest
            } else {
                crate::switch::Side::Local
            };
            // Seamless: the passage that is playing crosses at the position it
            // has reached, and the outgoing side is not silenced until the
            // incoming one is audible `[SPEC-BK-065]`.
            let said = match session.hand_over_seamless(&mut backend, target, 600, HANDOFF_LEAD_MS)
            {
                Ok(h) => {
                    let how = match h.stopped {
                        Some(crate::switch::Stopped::Faded) => "faded",
                        Some(crate::switch::Stopped::Cut) => "cut",
                        None => "already there",
                    };
                    let lost = if h.carried.lost.is_empty() {
                        String::new()
                    } else {
                        format!(", {} could not be carried", h.carried.lost.len())
                    };
                    // Said rather than assumed: a handoff that found nothing
                    // playing, or one the other side never answered, is a
                    // different event from a seamless one `[PI3-API-030]`.
                    let seam = match (h.resumed, h.took_ms) {
                        (Some((_, at)), Some(ms)) => {
                            format!(", resumed {:.1}s in after {ms} ms", at as f64 / 1000.0)
                        }
                        (Some((_, at)), None) => format!(
                            ", resumed {:.1}s in but the other side never sounded",
                            at as f64 / 1000.0
                        ),
                        (None, _) => ", nothing was playing to carry".to_string(),
                    };
                    format!(
                        "now on {which} ({how}, {} passage(s) carried{lost}{seam})",
                        h.carried.moved.len()
                    )
                }
                Err(e) => format!("refused: {e}"),
            };
            tracing::info!("switch: {said}");
            if let Ok(mut c) = controls_for_switch.lock() {
                c.switch_status = Some(said);
                c.backend = Some(which);
            }
        }

        // A seek asked for by the browser, applied to the side that is
        // sounding `[REQ-VIS-225]`. Taken here for the same reason a switch is:
        // the backends live on this thread and nowhere else.
        let seek_ask = controls_for_switch.lock().ok().and_then(|mut c| c.seek_requested.take());
        if let Some(ms) = seek_ask {
            crate::playback::Playback::seek_to(&mut backend, ms);
        }

        // What the side now sounding can do, published where the browser can
        // read it `[SPEC-BK-040]`. Taken every pass rather than at a switch,
        // so it is right even for the side the player started on.
        {
            let seekable = crate::playback::Playback::capabilities(&backend).seek;
            if let Ok(mut c) = controls_for_switch.lock() {
                if c.can_seek != seekable {
                    c.can_seek = seekable;
                }
            }
        }
        session.refill(&mut backend, suppress);
        // Nothing submitted means the ring is comfortably full -- the engine
        // declines to mix less than a threshold's worth -- so there is time to
        // spare. Sleeping through it is the difference between a loop that
        // wakes hundreds of times a second to move a handful of samples and one
        // that wakes when there is work. The ring holds ~14 s, so this pause is
        // three orders of magnitude inside the margin.
        if submitted == 0 {
            std::thread::sleep(Duration::from_millis(10));
        }
    }
    // The tick is over, so this thread may wait: let what it queued be
    // written before the program goes on to exit `[GDE-HST-140]`.
    crate::logging::flush(Duration::from_millis(500));
}

/// Run one folder-writing setting when the browser has asked for it.
///
/// The four of them `[REQ-VIS-205]`, `[REQ-VIS-210]`, `[REQ-VIS-215]`,
/// `[REQ-VIS-220]` differ only in which intent cell they read, which module
/// they call and what they call the files. Written four times they drifted --
/// one of them was pasted with the wrong status field and reported into another
/// setting's line -- so they are written once.
///
/// **Turning one off leaves what was written.** Deleting files from someone's
/// music folder is a larger act than declining to add more, and is not what
/// unticking a box asked for; `off_noun` names what stays.
///
/// **`library`, not the listener database.** Every one of the four reads
/// catalogue tables and nothing else -- `files`, `passages`,
/// `passage_recordings`, `recordings`, `artists`, `recording_artists`,
/// `file_tags`, `cover_art`, `release_recordings`, `lyrics` -- all of them
/// on the catalogue side of a split `[IMPL-DBSPLIT-025]`. This opened the
/// path the player was *started* with, which on a split installation is the
/// listener half, so every one of them failed on the first statement with
/// "no such table: passages". Found on `lempi02w` 2026-09-11, where `covers`
/// and `cue_sheets` were both switched on and had been failing since that
/// appliance split; it would have started failing here the moment the local
/// database split too.
///
/// Unsplit, the two paths are the same file and nothing about this changes.
fn run_generation(
    db: &Path,
    controls: &SharedControls,
    label: &str,
    off_noun: &str,
    take: impl Fn(&mut Controls) -> Option<bool>,
    status: impl Fn(&mut Controls, String),
    run: impl Fn(&rusqlite::Connection) -> Result<(crate::report::Written, &'static str), String>,
) {
    let Some(asked) = controls.lock().ok().and_then(|mut c| take(&mut c)) else { return };
    let said = if !asked {
        format!("off; {off_noun} already written are left alone")
    } else {
        match rusqlite::Connection::open(db).map_err(|e| e.to_string()).and_then(|c| run(&c)) {
            Ok((rep, noun)) => {
                for f in &rep.failed {
                    tracing::warn!("{label}: {f}");
                }
                rep.summary(noun)
            }
            Err(e) => e,
        }
    };
    tracing::info!("{label}: {said}");
    if let Ok(mut c) = controls.lock() {
        status(&mut c, said);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A session that cannot open is returned to the caller, not printed and
    /// lost inside a thread -- and its text is the session's own, so `lempi`
    /// writes exactly the two lines it wrote before this was a library.
    #[test]
    fn a_session_that_cannot_open_is_an_error_the_caller_receives() {
        let dir = std::env::temp_dir().join(format!("lempi-host-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let missing = dir.join("no-such.db");
        let cfg = Config {
            listener: missing.clone(),
            library: missing,
            depth: 3,
            device: None,
            echo_offset_frames: 0,
            echo_fleet_min_frames: 0,
            echo_rate: 44_100,
            follow: None,
            mpd_addr: None,
            mpd_root: None,
            web_port: None,
            also_port_80: false,
            backup: false,
            tag_scan: false,
        };
        match Player::start(cfg) {
            Err(StartError::Engine(e)) => assert!(!e.is_empty()),
            Err(other) => panic!("expected the session's own error, got {other}"),
            Ok(_) => panic!("a missing database must not start"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn start_errors_say_what_lempi_said_before() {
        assert_eq!(StartError::EngineGone.to_string(), "engine failed to start");
        let addr = SocketAddr::from(([0, 0, 0, 0], 5720));
        let e = StartError::Listen {
            addr,
            error: std::io::Error::new(std::io::ErrorKind::AddrInUse, "in use"),
        };
        assert_eq!(e.to_string(), "cannot listen on 0.0.0.0:5720: in use");
    }
}
