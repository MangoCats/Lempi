//! The whole of `[SPEC018]` in one program: Lempi's own session, playing MPD.
//!
//! The command line is `cli::specs::mpd_session`; `mpd_session --help`
//! prints it.
//!
//! **Nothing here selects anything.** It opens a `Session` — the same one
//! `lempi` runs — hands it an [`MpdBackend`] instead of an `Engine`, and calls
//! `refill`. If passages appear in MPD's queue, then the Director, its
//! rotation, its flow and its bookkeeping all reached a backend that is not the
//! built-in one, through the seam and without knowing they had `[SPEC-BK-022]`.
//!
//! `mpd_direct` proved the same behaviour by reimplementing the refill loop.
//! This proves it by *not* reimplementing it, which is the stronger claim and
//! the reason this binary exists alongside that one.

use std::path::Path;
use std::time::{Duration, Instant};

use lempi_player::db::PlayerStore;
use lempi_player::engine::Engine;
use lempi_player::mpd_backend::MpdBackend;
use lempi_player::playback::Playback;
use lempi_player::session::Session;
use lempi_player::switch::{Side, Stopped, Switching};

fn main() {
    use lempi_player::cli::specs::mpd_session as opt;
    let mut args = opt::SPEC.parse();
    let db = args.need(&opt::LISTENER).to_string();
    let root = args.need(&opt::ROOT).to_string();
    let write = args.has(&opt::WRITE);
    // **No default duration.** Without `--for` this runs until interrupted,
    // which is what a listening session is: a client such as Cantata is driving
    // playback and the Director is keeping the queue full behind it.
    let run_for: Option<f64> = args.real(&opt::RUN_FOR);
    // The handoff is a demonstration, and demonstrating it ends the session --
    // it stops MPD, which would pull the floor out from under whatever client
    // is playing. Off unless asked for.
    let then_handoff = args.has(&opt::THEN_HANDOFF);
    let addr = args.need(&opt::ADDR).to_string();

    // The listener owns the depth and the sampling rate `[SPEC-MPD-105]`.
    // Neither precedence nor the fallback is written here any more -- the
    // options declare `.setting(..)` and the chain does it `[GDE-CLI-090]`.
    // The struct is still wanted for the two suppression windows below,
    // which are settings and not options.
    let saved = PlayerStore::open(Path::new(&db))
        .ok()
        .and_then(|s| s.load_settings())
        .unwrap_or_default();
    for complaint in args.with_settings(|k| saved.value_of(k)) {
        eprintln!("mpd_session: {complaint}");
    }
    let depth: usize = args.must_size(&opt::DEPTH);
    let interval: u64 = args.must_int(&opt::INTERVAL);

    let loading = Instant::now();
    let mut session = Session::open(Path::new(&db), Path::new(&db), depth).unwrap_or_else(|e| {
        eprintln!("session: {e}");
        std::process::exit(1);
    });
    println!("session opened in {:.1}s", loading.elapsed().as_secs_f64());

    let mut guest = MpdBackend::connect(&addr, &root, depth, interval).unwrap_or_else(|e| {
        eprintln!("{e}");
        std::process::exit(1);
    });
    // Teach it to name what the listener queues themselves `[SPEC-MPD-115]`.
    match rusqlite::Connection::open(&db)
        .map_err(|e| e.to_string())
        .and_then(|c| lempi_player::mpd_backend::nameable_uris(&c, &root))
    {
        Ok(names) => {
            let unambiguous = names.values().filter(|v| v.is_some()).count();
            println!("{unambiguous} of {} URIs name exactly one radio passage", names.len());
            guest.attach_names(names);
        }
        Err(e) => eprintln!("cannot name the library's URIs ({e}); hand-added songs will not count"),
    }
    if write {
        match PlayerStore::open(Path::new(&db)) {
            Ok(s) => guest.attach_store(s),
            Err(e) => {
                eprintln!("cannot open {db} for writing: {e}");
                std::process::exit(1);
            }
        }
    }

    // A *real* engine, with a silent output. This is the local backend the
    // appliance would be using; only the sound card is absent, so a queue that
    // arrives here has arrived somewhere that could play it.
    let (engine, _handle) = Engine::new(lempi_player::path::PathHandle::silent(), depth);
    let mut sw = Switching::new(Box::new(engine));
    sw.attach_guest(Box::new(guest));
    sw.switch_to(Side::Guest).expect("guest attached");

    println!(
        "backend: MPD at {addr}, depth {depth}, sampling {interval}ms; spans {} gain {} ramps {}",
        sw.capabilities().spans,
        sw.capabilities().gain,
        sw.capabilities().ramps
    );
    println!("{}
", if write { "WRITING plays and rejections" } else { "writes nothing" });
    if let Some(c) = session.census() {
        println!("pool: {} eligible, {} suppressed
", c.eligible, c.suppressed);
    }

    let suppress = (saved.skip_suppress_h, saved.dequeue_suppress_h);
    let started = Instant::now();
    let mut last = 0usize;
    while run_for.is_none_or(|s| started.elapsed().as_secs_f64() < s) {
        sw.tick();
        session.refill(&mut sw, suppress);
        let n = sw.queued_ids().len();
        if n != last {
            println!("  MPD queue: {n} passage(s), {:.0}s ahead", sw.queued_ms() as f64 / 1000.0);
            last = n;
        }
        std::thread::sleep(Duration::from_millis(200));
    }

    if !then_handoff {
        println!("
stopping; MPD keeps playing and keeps its queue");
        return;
    }

    // --- the handoff `[SPEC-BK-030]` ---
    println!("
handing MPD -> Lempi");
    let before = sw.queued_ids();

    println!("  MPD was holding {} passage(s): {:?}", before.len(), before);
    // The same call `lempi` makes `[SPEC-BK-065]`. It was the queue-only one
    // until the seamless path shipped, which meant this program demonstrated a
    // handoff nobody performs any more -- and demonstrated it dropping the
    // passage that was playing.
    match session.hand_over_seamless(&mut sw, Side::Local, 600, 250) {
        Ok(h) => {
            println!(
                "  MPD {}",
                match h.stopped {
                    Some(Stopped::Faded) => "faded out over 600ms",
                    Some(Stopped::Cut) => "CUT -- no mixer on its output, so no fade was possible",
                    None => "was already the side that was not playing",
                }
            );
            match (h.resumed, h.took_ms) {
                (Some((id, at)), Some(ms)) => println!(
                    "  passage {id} crossed mid-play at {:.1}s; the local engine was audible after {ms} ms",
                    at as f64 / 1000.0
                ),
                (Some((id, at)), None) => println!(
                    "  passage {id} was handed over at {:.1}s but the local engine never sounded",
                    at as f64 / 1000.0
                ),
                (None, _) => println!("  nothing was playing to carry"),
            }
            println!("  carried {} passage(s) to the local engine: {:?}", h.carried.moved.len(), h.carried.moved);
            if !h.carried.lost.is_empty() {
                println!("  the library could no longer build: {:?}", h.carried.lost);
            }
        }
        Err(e) => println!("  refused: {e}"),
    }
    println!("  now active: {:?}", sw.active());
    println!("  local engine holds {} passage(s): {:?}", sw.queued_ids().len(), sw.queued_ids());
    println!(
        "  capabilities now: gain {} ramps {} (the local side can do what MPD cannot)",
        sw.capabilities().gain,
        sw.capabilities().ramps
    );
}
