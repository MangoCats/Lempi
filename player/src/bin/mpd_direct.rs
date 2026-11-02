//! The Program Director driving MPD from a process of its own, beside the
//! player `[SPEC-DIR-109]`, `[IMPL-MPD-050]`.
//!
//! The command line is `cli::specs::mpd_direct`; `mpd_direct --help`
//! prints it.
//!
//! **Thin, by the maintainer's decision of 2026-11-01.** It was a loop of its
//! own -- its own queue diff, its own bookkeeping of what was queued, played,
//! skipped and removed -- and the copy drifted from the player's: a skip kept
//! its queueing mark here and gave it back there, a hand-added song never
//! reached rotation here, and a mark was never resolved at all, so the
//! Director's open marks grew for as long as it ran. It is now a [`Session`],
//! the player's own, over an [`MpdBackend`], the player's own MPD backend, and
//! the bookkeeping is the one the player runs.
//!
//! What it adds is only what being another process needs: the session is a
//! **follower** ([`Role::Follower`]). It reads what the player changes in the
//! listener file -- a rebuild started, a programme edit, the slot held by
//! hand, the offset, the suppression windows -- and leaves alone what the
//! player keeps there: the remembered queue and the rebuild stamp.
//!
//! **Writing is opt-in.** Without `--write` it records nothing it chooses or
//! hears -- no play, no rejection, no decision, no offset -- and keeps in
//! memory what it heard, for a Director rebuilt while it runs. With it, plays
//! and rejections go to `listener_play_history` and `listener_rejections`, and
//! each pick's record to `selection_decisions` -- and **nowhere else**: no
//! scrobbler, no network, nothing that leaves the machine `[SPEC-MPD-100]`.
//! Either way each pick's reasoning is published as MPD stickers, where MPD's
//! own clients already look `[SPEC-MPD-050]`.
//!
//! [`Role::Follower`]: lempi_player::session::Role::Follower

use std::path::Path;
use std::time::{Duration, Instant};

use lempi_player::db::PlayerStore;
use lempi_player::mpd_backend::MpdBackend;
use lempi_player::playback::Playback;
use lempi_player::session::Session;

fn main() {
    use lempi_player::cli::specs::mpd_direct as opt;
    let mut args = opt::SPEC.parse();
    let listener = args.need(&opt::LISTENER).to_string();
    let library = args.need(&opt::LIBRARY).to_string();
    let root = args.need(&opt::ROOT).to_string();
    // The listener's settings own these `[SPEC-MPD-105]`; the flags are an
    // override for a test run, not the source of truth. Neither precedence
    // nor the fallback is written here -- the options declare `.setting(..)`
    // and the chain does it `[GDE-CLI-090]`.
    //
    // `open_split`, never `open`: `open(p)` is `open_split(p, p)`, which takes
    // the listener for the whole database and creates empty `file_tags` and
    // `cover_art` in it, shadowing the catalogue's own (`host.rs`,
    // `[IMPL-DBSPLIT-025]`).
    let saved = PlayerStore::open_split(Path::new(&listener), Path::new(&library))
        .ok()
        .and_then(|s| s.load_settings());
    if let Some(saved) = &saved {
        for complaint in args.with_settings(|k| saved.value_of(k)) {
            eprintln!("mpd_direct: {complaint}");
        }
    }
    let depth: usize = args.must_size(&opt::DEPTH);
    let interval: f64 = args.must_real(&opt::INTERVAL);
    let run_for: Option<f64> = args.real(&opt::RUN_FOR);
    let write = args.has(&opt::WRITE);
    let addr = args.need(&opt::ADDR).to_string();
    let seed = args.int(&opt::SEED);

    let mut session = Session::open_follower(Path::new(&listener), Path::new(&library), depth, write)
        .unwrap_or_else(|e| {
            eprintln!("session: {e}");
            std::process::exit(1);
        });
    if let Some(s) = seed {
        session.seed_rng(s);
    }
    // Built here rather than on its own thread at the first refill: there is
    // nothing for this program to do until it has one, and a pick made
    // without it would be the no-Director fallback's, with no record.
    let loading = Instant::now();
    if let Err(e) = session.build_director_now() {
        eprintln!("director: {e}");
        std::process::exit(1);
    }
    println!("director loaded in {:.1}s", loading.elapsed().as_secs_f64());

    let interval_ms = (interval * 1000.0).round().max(1.0) as u64;
    let mut mpd = MpdBackend::connect(&addr, &root, depth, interval_ms).unwrap_or_else(|e| {
        eprintln!("{e}");
        std::process::exit(1);
    });
    // To name what a person queues through another client `[SPEC-MPD-115]`:
    // from the catalogue, read-only, where the passages are.
    match rusqlite::Connection::open_with_flags(&library, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| e.to_string())
        .and_then(|c| lempi_player::mpd_backend::nameable_uris(&c, &root))
    {
        Ok(names) => {
            let unambiguous = names.values().filter(|v| v.is_some()).count();
            println!("{unambiguous} of {} URIs name exactly one radio passage", names.len());
            mpd.attach_names(names);
        }
        Err(e) => eprintln!("cannot name the library's URIs ({e}); hand-added songs will not count"),
    }
    if write {
        match PlayerStore::open_split(Path::new(&listener), Path::new(&library)) {
            Ok(s) => mpd.attach_store(s),
            Err(e) => {
                eprintln!("cannot open {listener} for writing: {e}");
                std::process::exit(1);
            }
        }
    }
    if let Some(c) = session.census() {
        println!("pool: {} eligible, {} suppressed by a recent skip or removal", c.eligible, c.suppressed);
    }
    println!(
        "depth {depth}, sampling {interval}s; {}",
        if write { "WRITING plays, rejections and decisions" } else { "writes nothing (pass --write)" }
    );
    println!("nothing leaves this machine: no scrobble, no network `[SPEC-MPD-100]`\n");

    // The windows as they stand, for the first refill; the session reads
    // them again from the listener file as it follows the player.
    let suppress = saved
        .map(|s| (s.skip_suppress_h, s.dequeue_suppress_h))
        .unwrap_or((lempi_player::SKIP_SUPPRESS_H, lempi_player::DEQUEUE_SUPPRESS_H));
    let started = Instant::now();
    let mut last = usize::MAX;
    // The tick may never wait on a write `[GDE-FBD-090]`: from here on this
    // thread's lines are queued, not written `[GDE-HST-140]`.
    lempi_player::logging::this_thread_must_not_block();
    while run_for.is_none_or(|s| started.elapsed().as_secs_f64() < s) {
        mpd.tick();
        if mpd.is_shutdown() {
            tracing::error!("mpd_direct: MPD has gone and not come back; stopping");
            break;
        }
        session.refill(&mut mpd, suppress);
        let n = mpd.queued_ids().len();
        if n != last {
            tracing::info!("MPD queue: {n} passage(s), {:.0}s ahead", mpd.ahead_ms() as f64 / 1000.0);
            last = n;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    // The tick is over, so this thread may wait: let what it queued be
    // written before the program goes on to exit `[GDE-HST-140]`.
    lempi_player::logging::flush(Duration::from_millis(500));
    if let Some(c) = session.census() {
        println!(
            "pool now: {} eligible, {} suppressed, {} recording-blocked, {} artist-blocked",
            c.eligible, c.suppressed, c.recording_blocked, c.artist_blocked
        );
    }
}
