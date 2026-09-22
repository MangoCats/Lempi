//! Lempi: continuous radio with a web UI.
//!
//! Two threads by necessity, not by preference. `cpal`'s stream is not `Send`
//! on every backend, so the engine must be built and pumped on one thread that
//! owns it; the web server runs on tokio and touches playback only through
//! [`EngineHandle`], which is the whole control surface `[REQ-VIS-140]`.
//!
//! The command line is [`lempi_player::cli::specs::lempi`] and lives there
//! rather than here: one parser serves all seventeen binaries, and each
//! contributes only a table of options `[GDE-CLI-010]`. `lempi --help` prints
//! it. **Until 2026-09-20 it did not** -- nothing recognised `--help`, so the
//! string became the positional database path and the player started against
//! a file of that name `[GDE-CLI-050]`.
//!
//! `--device` takes a case-insensitive substring of the output device name.
//! It matters more than it looks: PipeWire offers a `Dummy Output` whenever no
//! real sink is present, and a Bluetooth speaker that is momentarily absent at
//! startup leaves the player attached to that dummy -- playing perfectly into
//! nothing, and reporting itself healthy `[IMPL-AUD-010]`.

use std::path::PathBuf;
use std::sync::mpsc::sync_channel;
use std::sync::Arc;
use std::time::Duration;

use lempi_player::cli::specs::lempi as opt;
use lempi_player::engine::Engine;
use lempi_player::session::{Explanations, Session, SharedControls};
use lempi_player::web;
use lempi_player::BUFFER_FRAMES;

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


#[tokio::main]
async fn main() {
    // Help, version and every refusal are handled here, identically for all
    // seventeen binaries. Nothing below this line inspects a raw argument.
    let mut args = opt::SPEC.parse();
    // **What the first three layers settled** `[GOV-SRC-040]`, before
    // anything is opened. Printed here rather than only after the database
    // is attached so that it is visible on the query path below too, where
    // there is no layer 2 to attach.
    for line in args.provenance() {
        println!("lempi: {line}");
    }
    // Before the database, the engine or the server: this asks the machine a
    // question and leaves `[IMPL-AUD-010]`. It is what that document has told
    // appliance builders to run since long before the flag existed.
    if args.has(&opt::LIST_DEVICES) {
        for d in lempi_player::output::Output::list_devices() {
            println!("{d}");
        }
        return;
    }
    println!("lempi {}", lempi_player::build_id());
    // **Bootstrap, and then everything else** `[GDE-CLI-095]`. These two are
    // declared `.bootstrap()`, so they resolve from the command line, the
    // environment and their defaults only -- they name the database the
    // stored settings live in, and cannot be answered by it.
    let db = PathBuf::from(args.need(&opt::LISTENER));
    // The catalog-side file. Equal to `db` on every installation that
    // hasn't split `[IMPL-DBSPLIT-025]`, `[IMPL-DBSPLIT-030]` -- lempi02w,
    // once split, is the first to ever pass a genuinely different one.
    let library = args.text(&opt::LIBRARY).map(PathBuf::from).unwrap_or_else(|| db.clone());

    // Layer 2 is attached here and not before. Everything read after this
    // line resolves through all four layers; everything above it, through
    // three. That ordering is the reason the two are written apart.
    if let Some(saved) =
        lempi_player::db::PlayerStore::open_split(&db, &library).ok().and_then(|s| s.load_settings())
    {
        for complaint in args.with_settings(|k| saved.value_of(k)) {
            eprintln!("lempi: {complaint}");
        }
    }
    // And what layer 2 has now changed, if anything. Only the stored ones:
    // the rest were reported above and repeating them would bury these.
    for line in args.provenance() {
        if line.contains("from the stored setting") {
            println!("lempi: {line}");
        }
    }

    let port = args.must_size(&opt::PORT);
    let depth = args.must_size(&opt::DEPTH);
    let device = args.text(&opt::DEVICE).map(str::to_string);
    // This node's calibrated presentation offset, and the smallest in the
    // fleet `[LOG-ECHO-030]`. Both in frames, both calibrated rather than read
    // live `[LOG-P4-100]`. Absent means "fill the ring to capacity", which is
    // correct for a node running alone and for whichever node holds the
    // fleet's minimum `[GOV-SRC-040]`.
    let echo_offset = args.must_int(&opt::ECHO_OFFSET);
    let echo_fleet_min = args.must_int(&opt::ECHO_FLEET_MIN);
    // The master to follow `[GDE-ECHO-330]`. Absent means this node is nobody's
    // echo and plays its own programme, which is every node's default and the
    // behaviour it keeps if its master ever goes away `[GDE-ECHO-500]`.
    let follow = args.text(&opt::FOLLOW).map(str::to_string);
    #[cfg_attr(not(feature = "echo-client"), allow(unused_variables))]
    let echo_rate = args.must_int(&opt::ECHO_RATE) as u32;
    // A guest backend, offered rather than assumed `[SPEC-BK-020]`. Lempi still
    // plays; MPD is attached and idle until a switch asks for it.
    let mpd_addr = args.text(&opt::MPD).map(str::to_string);
    let mpd_root = args.text(&opt::MPD_ROOT).map(str::to_string);

    // The listening -- plays, preferences, programmes -- is the one thing in
    // that file nothing can rebuild `[REQ-LIB-160]`. One snapshot now, so a
    // library that has never been backed up stops being so within a second of
    // starting, then hourly for as long as it runs.
    {
        let backup_db = db.clone();
        std::thread::Builder::new()
            .name("lempi-backup".into())
            .spawn(move || loop {
                match lempi_player::backup::snapshot(&backup_db) {
                    Ok(p) => println!("listener state backed up to {}", p.display()),
                    // Never fatal: a player that stops playing because it could
                    // not write a backup has turned a precaution into the fault.
                    Err(e) => eprintln!("listener backup failed ({e}); playback continues"),
                }
                std::thread::sleep(Duration::from_secs(3600));
            })
            .ok();
    }

    // Album names and the browse index come from the files' own tags, and
    // reading them takes ~18 s for five thousand files. Doing it here, in the
    // background, is the difference between a feature that works and one that
    // waits for someone to remember a command -- which is exactly how the
    // browse pages came up empty in the first place. Incremental, so it is a
    // no-op on every start after the first, and off the audio path entirely.
    {
        // `file_tags` is a B-side table `[PI-DB-010]` -- `library`, not `db`,
        // is where it lives once split; equal to `db` until then.
        let scan_library = library.clone();
        std::thread::Builder::new()
            .name("lempi-tagscan".into())
            .spawn(move || {
                if let Err(e) = lempi_player::tags::backfill(&scan_library, true) {
                    eprintln!("tag scan unavailable ({e}); album names will be missing");
                }
            })
            .ok();
    }

    // The web side needs the library too, to read cover art out of the files
    // [REQ-VIS-170]; the engine thread takes ownership of the path itself.
    let art_db = db.clone();
    let art_library = library.clone();

    // The engine thread builds everything it owns, then reports its handle
    // back. Nothing audio-related crosses a thread boundary afterwards.
    let (tx, rx) = sync_channel(1);
    std::thread::Builder::new()
        .name("lempi-engine".into())
        .spawn(move || engine_thread(db, library, depth, device, mpd_addr, mpd_root, tx))
        .expect("spawn engine thread");

    // Sent from here rather than inside the engine thread: the handle comes
    // back to this scope anyway, and threading two more parameters through
    // `engine_thread` pushed it past what a reader can hold at once.
    let (handle, why, controls) = match rx.recv() {
        Ok((h, why, c)) => (Arc::new(h), why, c),
        Err(_) => {
            eprintln!("engine failed to start");
            std::process::exit(1);
        }
    };
    // Absent is not zero `[GOV-SRC-040]`: with no roster figures this node
    // fills its ring to capacity, which is right for a node playing alone and
    // for whichever node holds the fleet's smallest offset. Only a node told
    // both numbers holds anything back.
    if echo_offset > 0 && echo_fleet_min > 0 {
        eprintln!("echo-depth: offset={echo_offset} fleet-min={echo_fleet_min}, holding {} frames of ring back", echo_offset.saturating_sub(echo_fleet_min));
        handle.send(lempi_player::engine::Command::SetEchoDepth {
            own_offset_frames: echo_offset,
            fleet_min_offset_frames: echo_fleet_min,
        });
    }
    // `--follow` seeds the stored setting rather than replacing it. The
    // control is the settings panel `[SPEC-ECHO-010]`; the flag is for a node
    // being set up before anyone can reach its interface, and a flag that
    // silently overrode what a listener chose would be worse than no flag.
    #[cfg(feature = "echo-client")]
    {
        if let Some(host) = follow {
            handle.send(lempi_player::engine::Command::SetEchoFollow(host));
        }
        // Always spawned, even with nothing to follow: which node to follow is
        // now a setting that can change at any moment, and a task started only
        // at boot would mean the control did nothing until a restart.
        tokio::spawn(lempi_player::echo_client::run(
            lempi_player::echo_client::Following {
                timing: lempi_player::echo::NodeTiming {
                    presentation_offset_frames: echo_offset,
                    rate: echo_rate,
                },
                db: art_db.clone(),
                library: art_library.clone(),
            },
            handle.clone(),
        ));
    }
    #[cfg(not(feature = "echo-client"))]
    if follow.is_some() {
        // Refusing loudly beats ignoring a flag: a node asked to follow and
        // silently playing its own programme is the hardest kind of wrong to
        // notice `[GOV-SRC-040]`.
        eprintln!("{} needs a build with `--features echo-client`; not following", opt::FOLLOW.name);
    }
    let ui = web::Ui { handle, why, controls, db: art_db, library: art_library };
    let app = web::router(ui);

    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], port as u16));
    let listener = match tokio::net::TcpListener::bind(addr).await {
        Ok(l) => l,
        Err(e) => {
            eprintln!("cannot listen on {addr}: {e}");
            std::process::exit(1);
        }
    };
    println!("web UI on http://localhost:{port}/");

    // Also on :80, best-effort `[SPEC034]` -- reachable as plain
    // `http://lempi/` with no port, once `setcap cap_net_bind_service`
    // lets this otherwise-unprivileged process bind it at all (applied by
    // `deploy-player.sh` after every deploy; a fresh binary has no
    // capability of its own, since `setcap` is a file attribute a new
    // inode does not inherit). Expected to fail wherever that has not
    // been done -- a desktop build, an appliance before its first
    // capability-aware deploy -- and that failure is one log line, never
    // fatal: the port above is what everything else depends on, and it
    // must never wait on, or be brought down by, this one being merely a
    // convenience for a phone that would rather not type a port number.
    if port != 80 {
        let addr80 = std::net::SocketAddr::from(([0, 0, 0, 0], 80));
        match tokio::net::TcpListener::bind(addr80).await {
            Ok(listener80) => {
                let app80 = app.clone();
                tokio::spawn(async move {
                    if let Err(e) = axum::serve(listener80, app80).await {
                        eprintln!("port 80 server: {e}");
                    }
                });
                println!("also on http://localhost/ (port 80)");
            }
            Err(e) => {
                eprintln!("not also listening on :80 ({e}) -- port {port} still works");
            }
        }
    }

    if let Err(e) = axum::serve(listener, app).await {
        eprintln!("server: {e}");
    }
}

/// The published state, so the loop can read the listener's settings after the
/// engine has become an anonymous backend.
fn state_of(
    h: &lempi_player::engine::EngineHandle,
) -> std::sync::Arc<std::sync::Mutex<lempi_player::engine::PlayerState>> {
    h.state.clone()
}

fn engine_thread(
    db: PathBuf,
    library: PathBuf,
    depth: usize,
    device: Option<String>,
    mpd_addr: Option<String>,
    mpd_root: Option<String>,
    tx: std::sync::mpsc::SyncSender<(
        lempi_player::engine::EngineHandle,
        Explanations,
        SharedControls,
    )>,
) {
    let mut session = match Session::open(&db, &library, depth) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("{e}");
            return;
        }
    };
    println!("library: {} radio passages", session.lib.count_radio().unwrap_or(0));

    // A missing device must not stop the process: the UI still needs to come
    // up and say so, which is more use than exiting silently.
    // The supervisor opens the device on its own thread and owns it from
    // there `[SPEC-APS-060]`; this reports only what happened.
    let (path, why) = lempi_player::path::start(device, BUFFER_FRAMES * 2);
    println!("{why}");

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
    eprintln!("session prime: {}ms", primed.elapsed().as_millis());
    if tx.send((handle, session.explanations(), session.controls())).is_err() {
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
        println!("resuming playback: it was playing when it last stopped");
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
    let saved_cue = lempi_player::db::PlayerStore::open_split(&db, &library)
        .ok()
        .and_then(|s| s.load_settings())
        .map(|s| s.cue_sheets)
        .unwrap_or(false);
    // Read regardless of the feature so the flags parse and report the same
    // either way; only the guest that consumes them is gated.
    #[cfg(not(feature = "mpd"))]
    let _ = (&mpd_addr, &mpd_root, &saved_cue);
    let controls_for_switch = session.controls();
    if let Ok(mut c) = controls_for_switch.lock() {
        c.backend = Some("lempi".into());
    }
    let mut backend = lempi_player::switch::Switching::new(Box::new(engine));

    #[cfg(feature = "mpd")]
    if let Some(addr) = mpd_addr {
        let root = mpd_root.unwrap_or_default();
        match lempi_player::mpd_backend::MpdBackend::connect(
            &addr,
            &root,
            session.depth(),
            lempi_player::SAMPLE_INTERVAL_MS,
        ) {
            Ok(mut guest) => {
                if let Ok(c) = rusqlite::Connection::open(&db) {
                    match lempi_player::mpd_backend::nameable_uris(&c, &root) {
                        Ok(n) => guest.attach_names(n),
                        Err(e) => eprintln!("cannot name URIs for MPD ({e})"),
                    }
                    // Cue tracks, only when the listener has asked for them
                    // `[REQ-VIS-205]`. Read at startup: sheets written now are
                    // used from the next run, which the settings page says.
                    if saved_cue {
                        match lempi_player::mpd_backend::cue_uris(&c, &root) {
                            Ok(m) => {
                                println!("{} passage(s) have a cue track to be named by", m.len());
                                guest.attach_cues(m);
                            }
                            Err(e) => eprintln!("cannot map cue tracks ({e})"),
                        }
                    }
                }
                // `open_split` for the same reason as `saved_cue` above.
                if let Ok(st) = lempi_player::db::PlayerStore::open_split(&db, &library) {
                    guest.attach_store(st);
                }
                backend.attach_guest(Box::new(guest));
                println!("MPD guest attached at {addr}; Lempi is still the one playing");
                if let Ok(mut c) = controls_for_switch.lock() {
                    c.guest_available = true;
                    c.guest_name = Some(format!("MPD at {addr}"));
                    // Something true before the first switch, so the control is
                    // evidently working rather than evidently blank.
                    c.switch_status = Some("Lempi is playing; the guest is attached and idle".into());
                }
            }
            Err(e) => eprintln!("MPD guest unavailable ({e}); continuing on the local engine"),
        }
    }

    // Otherwise paused until told otherwise. The producers fill regardless, so
    // pressing Play in the browser starts on a primed pipeline rather than an
    // underrun [REQ-AUD-142].
    while !lempi_player::playback::Playback::is_shutdown(&backend) {
        let submitted = lempi_player::playback::Playback::tick(&mut backend);
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
                (lempi_player::SKIP_SUPPRESS_H, lempi_player::DEQUEUE_SUPPRESS_H),
                (lempi_player::QUEUE_DEPTH, lempi_player::SAMPLE_INTERVAL_MS),
            ));
        // Reaches whichever backend is live, and the one that is not
        // `[SPEC-MPD-105]`: the local engine already sees a change through its
        // own `Command` channel regardless, but a guest has no such channel,
        // so this is the only path that ever reaches it.
        lempi_player::playback::Playback::apply_queue_settings(
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
            |conn| lempi_player::cue::generate(conn, false).map(|r| (r, "cue sheet")));
        run_generation(&library, &controls_for_switch, "cover art", "covers",
            |c| c.covers_requested.take(), |c, s| c.covers_status = Some(s),
            |conn| lempi_player::covers::generate(conn, false).map(|r| (r, "cover")));
        run_generation(&library, &controls_for_switch, "lyrics sidecar", "files",
            |c| c.sidecar_requested.take(), |c, s| c.sidecar_status = Some(s),
            |conn| lempi_player::lyrics_sidecar::generate(conn, false).map(|r| (r, "file")));
        // The odd one out: it writes into a client's cache rather than the
        // music folder, so it has somewhere to fail to find.
        run_generation(&library, &controls_for_switch, "lyrics cache", "files",
            |c| c.lyrics_requested.take(), |c, s| c.lyrics_status = Some(s),
            |conn| match lempi_player::lyrics_cache::cache_dir() {
                // Not a failure: a machine the client has never run on has
                // nothing useful to write there `[SPEC-LYR-075]`.
                None => Err("no client cache on this machine; nothing written".to_string()),
                Some(dir) => lempi_player::lyrics_cache::generate(conn, &dir, false)
                    .map(|r| (r, "song")),
            });
        // A switch asked for by the browser happens here, where the backends
        // are `[SPEC-BK-030]`. Taken before the refill so the incoming side is
        // topped up rather than the outgoing one.
        let asked = controls_for_switch.lock().ok().and_then(|mut c| c.switch_requested.take());
        if let Some(which) = asked {
            let target = if which == "mpd" {
                lempi_player::switch::Side::Guest
            } else {
                lempi_player::switch::Side::Local
            };
            // Seamless: the passage that is playing crosses at the position it
            // has reached, and the outgoing side is not silenced until the
            // incoming one is audible `[SPEC-BK-065]`.
            let said = match session.hand_over_seamless(&mut backend, target, 600, HANDOFF_LEAD_MS)
            {
                Ok(h) => {
                    let how = match h.stopped {
                        Some(lempi_player::switch::Stopped::Faded) => "faded",
                        Some(lempi_player::switch::Stopped::Cut) => "cut",
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
            println!("switch: {said}");
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
            lempi_player::playback::Playback::seek_to(&mut backend, ms);
        }

        // What the side now sounding can do, published where the browser can
        // read it `[SPEC-BK-040]`. Taken every pass rather than at a switch,
        // so it is right even for the side the player started on.
        {
            let seekable = lempi_player::playback::Playback::capabilities(&backend).seek;
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
/// The four folder-writing generators `[REQ-VIS-205]`, `[REQ-VIS-210]`,
/// `[REQ-VIS-215]`, `[REQ-VIS-220]`.
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
    db: &std::path::Path,
    controls: &SharedControls,
    label: &str,
    off_noun: &str,
    take: impl Fn(&mut lempi_player::session::Controls) -> Option<bool>,
    status: impl Fn(&mut lempi_player::session::Controls, String),
    run: impl Fn(
        &rusqlite::Connection,
    ) -> Result<(lempi_player::report::Written, &'static str), String>,
) {
    let Some(asked) = controls.lock().ok().and_then(|mut c| take(&mut c)) else { return };
    let said = if !asked {
        format!("off; {off_noun} already written are left alone")
    } else {
        match rusqlite::Connection::open(db).map_err(|e| e.to_string()).and_then(|c| run(&c)) {
            Ok((rep, noun)) => {
                for f in &rep.failed {
                    eprintln!("{label}: {f}");
                }
                rep.summary(noun)
            }
            Err(e) => e,
        }
    };
    println!("{label}: {said}");
    if let Ok(mut c) = controls.lock() {
        status(&mut c, said);
    }
}
